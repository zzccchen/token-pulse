"""Read-only adapter for Codex's internal SQLite diagnostic log schema."""

from __future__ import annotations

import re
import sqlite3
import time
from bisect import bisect_left, bisect_right, insort
from collections import OrderedDict
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from token_pulse.submissions import (
    _TRACE_SPAN,
    ConfigurationTag,
    SubmittedTier,
    _fields,
    _trace_fields,
    feedback_tier,
    submitted_tier,
)

_START = re.compile(
    r'^Output item item_type="(reasoning|message|function_call|custom_tool_call)" '
    r'item_id="([\w-]{1,160})"$'
)
_TURN = re.compile(r'(?:turn\.id|turn_id)="?([\w-]{1,100})')


def database_candidates(root: Path, stem: str) -> list[Path]:
    """Known local layouts only; prefer the current root over retained copies."""
    paths = []
    for directory in (root, root / "sqlite"):
        versions = []
        for path in directory.glob(f"{stem}_*.sqlite"):
            match = re.fullmatch(rf"{re.escape(stem)}_(\d+)\.sqlite", path.name)
            if match and path.resolve().is_relative_to(root.resolve()):
                versions.append((int(match[1]), path))
        paths.extend(path for _, path in sorted(versions, reverse=True))
    return paths


def compatible_database(root: Path, stem: str, table: str, required: set[str]) -> Path | None:
    for path in database_candidates(root, stem):
        try:
            with closing(read_only(path)) as db:
                columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                if required <= columns:
                    return path
        except (OSError, sqlite3.Error):
            continue
    return None


def diagnostic_scope(task: str, prefix: str) -> str | None:
    """Read only tracing-span fields; strings and message bodies cannot supply IDs."""
    if len(prefix) > 4000:
        return None
    turns = set()
    position = 0
    try:
        for match in _TRACE_SPAN.finditer(prefix):
            if not re.fullmatch(r"(?::?[\w.]+:)*:?", prefix[position : match.start()]):
                return None
            fields = _trace_fields(
                match[2],
                display_cwd=match[1] in {"run_sampling_request", "try_run_sampling_request"},
            )
            for name in ("thread_id", "thread.id", "conversation.id"):
                if name in fields and fields[name] != task:
                    return None
            turns.update(fields[name] for name in ("turn_id", "turn.id") if name in fields)
            position = match.end()
        if not re.fullmatch(r"(?::?[\w.]+)*:?", prefix[position:]):
            return None
    except ValueError:
        return None
    if len(turns) != 1:
        return None
    turn = next(iter(turns))
    return turn if re.fullmatch(r"[\w-]{1,100}", turn) else None


def output_start(tail: str) -> str | None:
    match = _START.fullmatch(tail)
    if match:
        return match[2]
    # Older diagnostics render Rust Debug objects. Only the outer id field
    # supplies identity; content/arguments are structurally skipped and discarded.
    header = re.match(r"Output item item=(Reasoning|Message|FunctionCall|CustomToolCall) \{", tail)
    if not header:
        return None
    try:
        fields = _fields(tail[len("Output item item=") :], header[1])
    except ValueError:
        return None
    raw = fields.get("id", "")
    match = re.fullmatch(r'(?:"([\w-]{1,160})"|Some\("([\w-]{1,160})"\))', raw)
    return (match[1] or match[2]) if match else None


def label(value: object) -> str | None:
    """Keep identifiers, never URLs, headers or arbitrary log text."""
    if isinstance(value, str) and re.fullmatch(r"[\w. -]{1,100}", value):
        return value
    return None


def read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.2)
    connection.execute("PRAGMA query_only=ON")
    return connection


@dataclass(frozen=True)
class Start:
    at: float
    turn_id: str
    auth_mode: str | None = None
    requested_tier: str | None = None
    ambiguous: bool = False
    origin: str = "stream-log"


def merge_start(previous: Start, incoming: Start) -> Start:
    conflict = previous.ambiguous or incoming.ambiguous
    conflict |= (previous.at, previous.turn_id) != (incoming.at, incoming.turn_id)
    conflict |= any(
        old is not None and new is not None and old != new
        for old, new in (
            (previous.auth_mode, incoming.auth_mode),
            (previous.requested_tier, incoming.requested_tier),
        )
    )
    return Start(
        previous.at,
        previous.turn_id,
        previous.auth_mode or incoming.auth_mode,
        previous.requested_tier or incoming.requested_tier,
        ambiguous=conflict,
        origin="stream-log"
        if "stream-log" in {previous.origin, incoming.origin}
        else previous.origin,
    )


class LogEvidence:
    def __init__(self, capacity: int = 20000):
        self.starts: OrderedDict[tuple[str, str], Start] = OrderedDict()
        self.requests: dict[tuple[str, str], tuple[float, str | None, str | None]] = {}
        self.notices: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self.submissions: OrderedDict[tuple[str, str], SubmittedTier] = OrderedDict()
        self.config_tags: OrderedDict[tuple[str, str], ConfigurationTag] = OrderedDict()
        self.capacity = capacity
        self.interruptions: dict[tuple[str, str], list[float]] = {}
        self.interruption_order: OrderedDict[tuple[str, str, float], None] = OrderedDict()
        # Loaded by the history owner. Only HMAC identifiers enter this archive.
        self.archive_identity: Callable[[str, str], str] | None = None
        self.archived_starts: dict[str, dict[str, Start]] = {}
        self.archived_interruptions: dict[tuple[str, str], list[float]] = {}
        self.archive_floor: float | None = None

    def start(self, task: str, item: str, turn: str) -> Start | None:
        live = self.starts.get((task, item))
        if live and self.archive_floor is not None and live.at <= self.archive_floor:
            live = replace(live, ambiguous=True)
        if self.archive_identity is None:
            return live
        anon = self.archive_identity
        saved = self.archived_starts.get(anon("task", task), {}).get(anon("item", item))
        if saved is None:
            return live
        # Compare pseudonyms; never assign an unknown saved turn to the current one.
        saved = replace(saved, turn_id=turn if saved.turn_id == anon("turn", turn) else "")
        return merge_start(saved, live) if live is not None else saved

    def start_fingerprint(self, task: str) -> list[tuple[str, Start]]:
        anon = self.archive_identity
        values = dict(self.archived_starts.get(anon("task", task), {})) if anon else {}
        for (owner, item), start in self.starts.items():
            if owner != task:
                continue
            if anon:
                item = anon("item", item)
                start = replace(start, turn_id=anon("turn", start.turn_id))
            start = replace(start, at=float(start.at))
            if self.archive_floor is not None and start.at <= self.archive_floor:
                start = replace(start, ambiguous=True)
            values[item] = merge_start(values[item], start) if item in values else start
        return sorted(values.items())

    def interruption_fingerprint(self, task: str) -> list[tuple[str, list[float]]]:
        anon = self.archive_identity
        values = {
            turn: set(times)
            for (owner, turn), times in self.archived_interruptions.items()
            if anon and owner == anon("task", task)
        }
        for (owner, turn), times in self.interruptions.items():
            if owner == task:
                values.setdefault(anon("turn", turn) if anon else turn, set()).update(times)
        return sorted((turn, sorted(float(at) for at in times)) for turn, times in values.items())

    def record_interruption(self, task: str, turn: str, at: float) -> None:
        key = task, turn, at
        if key in self.interruption_order:
            return
        self.interruption_order[key] = None
        insort(self.interruptions.setdefault((task, turn), []), at)
        while len(self.interruption_order) > self.capacity:
            (old_task, old_turn, old_at), _ = self.interruption_order.popitem(last=False)
            times = self.interruptions[old_task, old_turn]
            times.pop(bisect_left(times, old_at))
            if not times:
                del self.interruptions[old_task, old_turn]

    def interrupted(self, task: str, turn: str, start: float, end: float) -> bool:
        times = self.interruptions.get((task, turn), [])
        index = bisect_right(times, start)
        if index < len(times) and times[index] < end:
            return True
        if self.archive_identity is not None:
            anon = self.archive_identity
            times = self.archived_interruptions.get((anon("task", task), anon("turn", turn)), [])
            index = bisect_right(times, start)
            return index < len(times) and times[index] < end
        return False

    def accept(
        self, task: str, at: float, target: str, body: str, *, origin: str = "stream-log"
    ) -> None:
        if not task or not body or len(body) > 16000:
            return
        if target in {"codex_core::session::handlers", "feedback_tags"}:
            parse_tier = submitted_tier if target.endswith("::handlers") else feedback_tier
            settings = self.submissions if target.endswith("::handlers") else self.config_tags
            result = parse_tier(task, at, body)
            if result is not None:
                turn_id, observation = result
                key = task, turn_id
                previous = settings.get(key)
                if previous is not None:
                    original = observation
                    if (previous.tier, previous.state) != (observation.tier, observation.state):
                        observation = replace(
                            observation, at=min(previous.at, at), tier=None, state="ambiguous"
                        )
                    else:
                        observation = replace(observation, at=min(previous.at, at))
                    if isinstance(observation, SubmittedTier):
                        known = [v for v in (previous, original) if v.override_state is not None]
                        if known:
                            values = {(v.override_tier, v.override_state) for v in known}
                            tier, state = (
                                next(iter(values)) if len(values) == 1 else (None, "ambiguous")
                            )
                            observation = replace(
                                observation,
                                override_tier=tier,
                                override_state=state,
                                override_at=min(v.override_at for v in known),
                            )
                settings[key] = observation
                settings.move_to_end(key)
                while len(settings) > self.capacity:
                    settings.popitem(last=False)
                if target == "codex_core::session::handlers":
                    return
        # Limit pattern work; never inspect arbitrary tool bodies for markers.
        prefix, _, tail = body.partition(": ")
        if not prefix:
            tail = body
        turn_id = diagnostic_scope(task, prefix) or ""
        if target == "codex_core::responses_retry" and turn_id:
            # Read a known outer warning and Sampling scope only. Error strings,
            # remote compaction and arbitrary messages cannot supply evidence.
            message = re.match(
                r"(?:stream connection failed; waiting to retry|"
                r"stream disconnected - retrying sampling request "
                r"\(\d+/\d+ in [0-9.]+(?:ns|µs|ms|s)\)\.\.\.) "
                r'turn_id=(?:"([^"\s]{1,256})"|([^\s]{1,256}))(?: |$)',
                tail,
            )
            if message and (message[1] or message[2]) == turn_id:
                try:
                    requests = [
                        _trace_fields(span[2]).get("request")
                        for span in _TRACE_SPAN.finditer(prefix)
                        if span[1] == "handle_response_stream_error"
                    ]
                except ValueError:
                    requests = []
                if requests and all(request == "Sampling" for request in requests):
                    self.record_interruption(task, turn_id, at)
        if target == "feedback_tags" and len(body) < 16000 and "stream_request{" in prefix:
            auth = re.search(r'\bauth_mode="?(Chatgpt|ApiKey|ChatgptAuthTokens)\b', tail)
            request = re.search(r"stream_request\{([^{}]*)\}", prefix)
            tier = re.search(r'\bservice_tier="?([\w-]+)', request[1]) if request else None
            self.requests[task, turn_id] = (
                at,
                auth[1] if auth else None,
                tier[1] if tier else None,
            )
            if len(self.requests) > 1024:
                self.requests.pop(next(iter(self.requests)))
        if target == "codex_core::stream_events_utils" and "handle_output_item_done" not in prefix:
            item_id = output_start(tail)
            if item_id and turn_id:
                key = task, item_id
                request = self.requests.get((task, turn_id))
                auth, tier = request[1:] if request and request[0] <= at else (None, None)
                start = Start(at, turn_id, auth, tier, origin=origin)
                previous = self.starts.get(key)
                if previous:
                    start = merge_start(previous, start)
                self.starts[key] = start
                self.starts.move_to_end(key)
                while len(self.starts) > self.capacity:
                    self.starts.popitem(last=False)
        if target.startswith("codex_core::") and len(tail) < 1000:
            if re.fullmatch(
                r"Configured service tier `[^`]+` is not advertised as supported "
                r"for model `[^`]+` and will be omitted from requests\.?",
                tail,
            ):
                self.notices[task] = (at, "tier_ignored")
                while len(self.notices) > 128:
                    self.notices.popitem(last=False)


class SqliteLogs:
    def __init__(
        self, path: Path, evidence: LogEvidence, *, backfill: int = 50000, batch_size: int = 5000
    ):
        self.path, self.evidence = path, evidence
        self.backfill, self.batch_size = backfill, batch_size
        self.cursor: int | None = None
        self.identity: tuple[int, int] | None = None

    def poll(self) -> str | None:
        try:
            stat = self.path.stat()
            identity = stat.st_dev, stat.st_ino
            with closing(read_only(self.path)) as connection:
                maximum = connection.execute("SELECT MAX(id) FROM logs").fetchone()[0] or 0
                if self.cursor is None or identity != self.identity or maximum < self.cursor:
                    self.cursor = max(0, maximum - self.backfill)
                    self.evidence.starts.clear()
                    self.evidence.requests.clear()
                    self.evidence.submissions.clear()
                    self.evidence.config_tags.clear()
                    self.evidence.interruptions.clear()
                    self.evidence.interruption_order.clear()
                    # Intact orphan cells can outlive SQL-level retention. This is
                    # bounded and read only, once per reset, before live rows replay.
                    from token_pulse.sqlite_recovery import recover_origins

                    recover_origins(connection, self.evidence, now=time.time())
                    # Submission rows are sparse. The normal ID window can miss
                    # them even while they remain in the database. Backfill this
                    # one target separately, once per database reset, with a cap.
                    submissions = connection.execute(
                        "SELECT ts, ts_nanos, thread_id, target, "
                        "CASE WHEN length(feedback_log_body) <= 16000 THEN feedback_log_body END "
                        "FROM logs WHERE target=? ORDER BY id DESC LIMIT 2000",
                        ("codex_core::session::handlers",),
                    ).fetchall()
                    for row in reversed(submissions):
                        self._accept_row(*row)
                    # Old output starts can remain in SQLite long after they leave
                    # the generic ID window. Replay bounded relevant events in ID
                    # order, with feedback preceding each start. Stream the rows
                    # so full log bodies are never accumulated in memory.
                    events = connection.execute(
                        "SELECT ts, ts_nanos, thread_id, target, "
                        "CASE WHEN length(feedback_log_body) <= 16000 THEN feedback_log_body END "
                        "FROM logs WHERE id IN (SELECT id FROM logs WHERE id <= ? "
                        "AND target IN (?,?,?) ORDER BY id DESC LIMIT 40000) ORDER BY id",
                        (
                            maximum,
                            "feedback_tags",
                            "codex_core::stream_events_utils",
                            "codex_core::responses_retry",
                        ),
                    )
                    for row in events:
                        self._accept_row(*row)
                self.identity = identity
                rows = connection.execute(
                    "SELECT id, ts, ts_nanos, thread_id, target, "
                    "CASE WHEN length(feedback_log_body) <= 16000 THEN feedback_log_body END "
                    "FROM logs WHERE id > ? ORDER BY id LIMIT ?",
                    (self.cursor, self.batch_size),
                ).fetchall()
                for row_id, seconds, nanos, task, target, body in rows:
                    self._accept_row(seconds, nanos, task, target, body)
                    self.cursor = row_id
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return "logs_unavailable"
        return None

    def _accept_row(self, seconds, nanos, task, target, body):
        if (
            isinstance(body, str)
            and isinstance(task, str)
            and isinstance(target, str)
            and type(seconds) in (int, float)
            and type(nanos) in (int, float)
            and 0 <= seconds <= 2**53
            and 0 <= nanos < 1e9
        ):
            self.evidence.accept(task, seconds + nanos / 1e9, target, body)
