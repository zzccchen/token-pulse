"""Bounded, content-free normalization of Responses transport event sequences."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import secrets
from collections import OrderedDict
from dataclasses import dataclass, field, replace

from token_pulse.domain import Context, Sample
from token_pulse.logs import diagnostic_scope, label
from token_pulse.submissions import _TRACE_SPAN, _trace_fields, _unique_json

SOURCE = "codex-transport-log-v1"
TARGETS = ("codex_api::sse::responses", "codex_api::endpoint::responses_websocket")
REVISION = 8
GENERATED = {"message", "reasoning", "function_call", "custom_tool_call"}
TERMINALS = {"response.completed", "response.failed", "response.incomplete", "error"}
CHANNELS = {
    "output_text": ("message", "content", "output_text"),
    "refusal": ("message", "content", "refusal"),
    "reasoning_text": ("reasoning", "content", "reasoning_text"),
    "reasoning_summary_text": ("reasoning", "summary", "summary_text"),
    "function_call_arguments": ("function_call", None, None),
    "custom_tool_call_input": ("custom_tool_call", None, None),
}


def identifier(value: object) -> str | None:
    # Protocol/process IDs are opaque, not necessarily UUIDs. Reject separators
    # that would make the legacy ordered-item digest ambiguous, and controls.
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[^\s\x00-\x1f\x7f|]{1,256}", value)
        else None
    )


def token_count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def decode_event(raw: str) -> dict:
    """Accept JSON, JSON-quoted JSON, or one complete SSE data frame."""

    def decode(value):
        return json.loads(value, object_pairs_hook=_unique_json)

    raw = raw.strip()
    event = decode(raw) if raw.startswith(("{", '"')) else raw
    if isinstance(event, str):
        if event.lstrip().startswith("{"):
            event = decode(event)
        else:
            data, names = [], []
            for line in event.strip().splitlines():
                if line.startswith(":"):
                    continue
                name, separator, value = line.partition(":")
                if not separator or name not in {"event", "data", "id", "retry"}:
                    raise ValueError("Unsupported SSE frame")
                if name == "data":
                    data.append(value.lstrip(" "))
                elif name == "event":
                    names.append(value.strip())
            event = decode("\n".join(data))
            if len(names) > 1 or names and names[0] != event.get("type"):
                raise ValueError("Conflicting SSE event type")
    if not isinstance(event, dict):
        raise ValueError("Expected event object")
    return event


@dataclass
class Item:
    kind: str
    index: int
    start: float
    end: float | None = None
    parts: dict[tuple[str, int], tuple[str, bool]] = field(default_factory=dict)
    channels: dict[tuple[str, int | None], bool] = field(default_factory=dict)


@dataclass
class Stream:
    task: str
    turn: str
    response: str
    at: float
    context: Context = Context()
    sequence: int = -1
    digests: dict[int, bytes] = field(default_factory=dict)
    items: dict[str, Item] = field(default_factory=dict)
    issue: str | None = None

    def damage(self, issue: str) -> None:
        if self.issue is None or issue == "transport_conflict":
            self.issue = issue


class Transport:
    """No network access; complete log sequences supply client-observed timing.

    Unknown events, ambiguous routing and missing boundaries fail closed. The
    per-instance secret hashes replay content without retaining text/arguments.
    Only normalized samples leave this object.
    """

    def __init__(self, *, max_active=32, max_events=20000, max_items=1000, capacity=1000):
        if min(max_active, max_events, max_items, capacity) <= 0:
            raise ValueError("Transport limits must be positive")
        self.max_active, self.max_events, self.max_items = max_active, max_events, max_items
        self.capacity = capacity
        self.secret = secrets.token_bytes(32)
        self.active: OrderedDict[tuple, Stream] = OrderedDict()
        self.finished: OrderedDict[tuple, tuple[Sample, bytes | None, bytes | None]] = OrderedDict()
        self.updates: OrderedDict[tuple, Sample] = OrderedDict()

    def gap(self, task: str | None = None, turn: str | None = None) -> None:
        for stream in self.active.values():
            if (task is None or stream.task == task) and (turn is None or stream.turn == turn):
                stream.damage("transport_record_gap")

    def reset(self) -> None:
        for stream in self.active.values():
            self._unfinished(stream, "transport_incomplete_stream")
        self.active.clear()

    def snapshot_pending(self) -> None:
        # EOF/producer exit is not proof of completion. Keep an excluded record;
        # later events may still finish this same in-memory sequence.
        for stream in self.active.values():
            self._unfinished(stream, stream.issue or "transport_incomplete_stream")

    def samples(self) -> list[Sample]:
        return [sample for sample, _, _ in self.finished.values()]

    def drain(self) -> list[Sample]:
        values = list(self.updates.values())
        self.updates.clear()
        return values

    def accept(
        self, task: str, process: str | None, at: float, target: str, body: str | None
    ) -> None:
        if target not in TARGETS:
            return
        if (
            not isinstance(body, str)
            or not body
            or len(body.encode("utf-8")) > 2 * 1024 * 1024
            or not math.isfinite(at)
        ):
            self.gap(task)
            return
        prefix, separator, tail = body.partition(": ")
        turn = diagnostic_scope(task, prefix) if separator else None
        # Redundant Debug renderings and transport telemetry are not wire events.
        marker = re.match(r"(?:SSE event|sse event|websocket event|event):\s*", tail)
        if marker is None:
            return
        if turn is None:
            self.gap(task)
            return
        routing: dict[str, str] = {}
        try:
            for span in _TRACE_SPAN.finditer(prefix):
                fields = _trace_fields(
                    span[2],
                    display_cwd=span[1] in {"run_sampling_request", "try_run_sampling_request"},
                )
                for name in ("request_id", "connection_id", "websocket_id"):
                    if name in fields:
                        value = identifier(fields[name])
                        if value is None or name in routing and routing[name] != value:
                            raise ValueError("Conflicting transport route")
                        routing[name] = value
            event = decode_event(tail[marker.end() :])
        except (ValueError, RecursionError, AttributeError):
            self.gap(task, turn)
            return
        route = task, turn, process, target, tuple(sorted(routing.items()))
        self.feed(route, at, event)

    def feed(self, route: tuple, at: float, event: dict) -> None:
        """Consume an already scoped event; useful for deterministic format fixtures."""
        task, turn, process = route[:3]
        kind, sequence = event.get("type"), event.get("sequence_number")
        if not isinstance(kind, str) or not math.isfinite(at):
            self.gap(task, turn)
            return
        response = event.get("response")
        response = response if isinstance(response, dict) else {}
        rid = identifier(response.get("id")) or identifier(event.get("response_id"))
        digest = hmac.digest(self.secret, json.dumps(event, sort_keys=True).encode(), "sha256")
        key = (*route, rid)
        if kind == "response.created" and rid:
            stream = self.active.get(key)
            if stream is None:
                stream = Stream(task, turn, rid, at, self._context(response))
                self.active[key] = stream
                if type(sequence) is not int or sequence != 0:
                    stream.damage("transport_missing_start")
                if not identifier(process):
                    stream.damage("transport_missing_process")
                while len(self.active) > self.max_active:
                    _, evicted = self.active.popitem(last=False)
                    self._unfinished(evicted, "transport_capacity")
        else:
            stream = self.active.get(key) if rid else None
            matches = [s for k, s in self.active.items() if k[:-1] == route]
            if stream is None and not rid and len(matches) == 1:
                stream = matches[0]
                key = (*route, stream.response)
            if stream is None:
                for candidate in matches:
                    if not rid:
                        candidate.damage("transport_ambiguous_route")
                    elif identifier(event.get("item_id")) in candidate.items:
                        candidate.damage("transport_conflict")
                if kind in TERMINALS and rid:
                    stream = Stream(
                        task,
                        turn,
                        rid,
                        at,
                        self._context(response),
                        issue="transport_missing_start",
                    )
                    self._complete(stream, event, at, digest)
                return
        if type(sequence) is not int or sequence < 0:
            stream.damage("transport_invalid_sequence")
        elif sequence in stream.digests:
            if stream.digests[sequence] == digest:
                return
            stream.damage("transport_conflict")
        elif sequence != stream.sequence + 1:
            stream.damage("transport_sequence_gap")
        if at < stream.at:
            stream.damage("transport_timestamp_order")
        if type(sequence) is int:
            stream.sequence = sequence
            if len(stream.digests) < self.max_events:
                stream.digests[sequence] = digest
            else:
                stream.damage("transport_capacity")
        while sum(len(s.digests) for s in self.active.values()) > self.max_events:
            _, evicted = self.active.popitem(last=False)
            evicted.damage("transport_capacity")
            self._unfinished(evicted, "transport_capacity")
        if key not in self.active and kind not in TERMINALS:
            return
        stream.at = at
        if (
            event.get("response_id") is not None
            and event.get("response_id") != stream.response
            or response.get("id") is not None
            and response.get("id") != stream.response
        ):
            stream.damage("transport_conflict")
        if kind in TERMINALS:
            self._complete(stream, event, at, digest)
            self.active.pop(key, None)
        elif kind in {"response.created", "response.in_progress", "response.metadata"}:
            if kind == "response.created" and sequence != 0 and stream.issue is None:
                stream.damage("transport_conflict")
        elif kind in {"response.output_item.added", "response.output_item.done"}:
            self._item(stream, event, at)
        elif isinstance(kind, str):
            self._content(stream, event)
        else:
            stream.damage("transport_unknown_event")

    def _item(self, stream: Stream, event: dict, at: float) -> None:
        raw = event.get("item")
        raw = raw if isinstance(raw, dict) else {}
        status = raw.get("status")
        expected = "in_progress" if event["type"].endswith(".added") else "completed"
        if status is not None and status != expected:
            stream.damage("transport_invalid_item_status")
        iid, kind, index = identifier(raw.get("id")), raw.get("type"), event.get("output_index")
        if (
            iid is None
            or type(index) is not int
            or index < 0
            or not isinstance(kind, str)
            or kind not in GENERATED
        ):
            stream.damage("transport_unsupported_item")
            return
        if (
            event.get("item_id", iid) != iid
            or kind == "message"
            and raw.get("role", "assistant") != "assistant"
        ):
            stream.damage("transport_conflict")
        if event["type"].endswith(".added"):
            if iid in stream.items:
                stream.damage("transport_conflict")
            elif index != len(stream.items):
                # An unsupported intervening item already explains this gap.
                # Do not relabel that known scope problem as an ID conflict.
                stream.damage(
                    "transport_conflict" if stream.issue is None else "transport_incomplete_items"
                )
            elif len(stream.items) >= self.max_items:
                stream.damage("transport_capacity")
            else:
                stream.items[iid] = Item(kind, index, at)
        else:
            item = stream.items.get(iid)
            if item is None:
                stream.damage("transport_incomplete_items")
                return
            if (item.kind, item.index) != (kind, index) or item.end is not None:
                stream.damage("transport_conflict")
                return
            if any(not done for _, done in item.parts.values()) or any(
                not done for done in item.channels.values()
            ):
                stream.damage("transport_incomplete_content")
            if at < item.start:
                stream.damage("transport_timestamp_order")
            item.end = at

    def _content(self, stream: Stream, event: dict) -> None:
        kind = event["type"]
        part_match = re.fullmatch(
            r"response\.(content_part|reasoning_summary_part)\.(added|done)", kind
        )
        channel_match = re.fullmatch(r"response\.([a-z_]+)\.(delta|done)", kind)
        channel = CHANNELS.get(channel_match[1]) if channel_match else None
        if not part_match and channel is None:
            stream.damage("transport_unknown_event")
            return
        item = stream.items.get(identifier(event.get("item_id")))
        if (
            item is None
            or type(event.get("output_index")) is not int
            or item.index != event["output_index"]
            or item.end is not None
        ):
            stream.damage("transport_unknown_item")
            return
        group = (
            ("content" if part_match[1] == "content_part" else "summary")
            if part_match
            else channel[1]
        )
        index = (
            event.get("content_index" if group == "content" else "summary_index") if group else None
        )
        if group and (type(index) is not int or not 0 <= index < self.max_items):
            stream.damage("transport_invalid_content_index")
            return
        part_key = group, index
        if part_match:
            raw = event.get("part")
            part_type = raw.get("type") if isinstance(raw, dict) else None
            allowed = (
                {"output_text", "refusal"}
                if item.kind == "message"
                else {"reasoning_text", "summary_text"}
            )
            if (
                not isinstance(part_type, str)
                or part_type not in allowed
                or item.kind not in {"message", "reasoning"}
                or group == "summary"
                and item.kind != "reasoning"
            ):
                stream.damage("transport_unsupported_content")
                return
            if part_match[2] == "added":
                if part_key in item.parts or index != sum(k[0] == group for k in item.parts):
                    stream.damage("transport_conflict")
                else:
                    item.parts[part_key] = part_type, False
            elif part_key not in item.parts:
                stream.damage("transport_incomplete_content")
            elif item.parts.get(part_key) != (part_type, False):
                stream.damage("transport_conflict")
            else:
                item.parts[part_key] = part_type, True
        else:
            if channel_match[2] == "delta" and not isinstance(event.get("delta"), str):
                stream.damage("transport_invalid_delta")
                return
            if item.kind != channel[0] or group and item.parts.get(part_key) != (channel[2], False):
                stream.damage("transport_unknown_content")
                return
            channel_key = channel_match[1], index
            if item.channels.get(channel_key) is True:
                stream.damage("transport_conflict")
            item.channels[channel_key] = channel_match[2] == "done"

    @staticmethod
    def _context(response: dict) -> Context:
        reasoning = response.get("reasoning")
        return Context(
            model=label(response.get("model")),
            effort=label(reasoning.get("effort")) if isinstance(reasoning, dict) else None,
            actual_tier=label(response.get("service_tier")),
        )

    def _complete(self, stream: Stream, event: dict, at: float, digest: bytes) -> None:
        response = event.get("response")
        response = response if isinstance(response, dict) else {}
        usage = response.get("usage")
        tokens = token_count(usage.get("output_tokens")) if isinstance(usage, dict) else None
        if (
            event["type"] != "response.completed"
            or response.get("status") != "completed"
            or response.get("error") is not None
            or response.get("incomplete_details") is not None
        ):
            stream.damage("transport_failed")
        if tokens is None:
            stream.damage("transport_missing_usage")
        items = list(stream.items.items())
        if not items or any(item.end is None for _, item in items):
            stream.damage("transport_incomplete_items")
        final = response.get("output")
        if final is not None:
            if (
                not isinstance(final, list)
                or final
                and [(r.get("id"), r.get("type")) if isinstance(r, dict) else None for r in final]
                != [(iid, item.kind) for iid, item in items]
            ):
                stream.damage(
                    "transport_conflict" if stream.issue is None else "transport_incomplete_items"
                )
        aliases = ()
        start, end = None, at
        issue = stream.issue
        if not issue:
            aliases = (hashlib.sha256("|".join(iid for iid, _ in items).encode()).hexdigest(),)
            first = items[0][1]
            start, end = first.start, max(item.end for _, item in items)
            if first.kind in {"function_call", "custom_tool_call"}:
                issue, start = "unconfirmed_tool_start", None
            elif first.end == first.start:
                issue, start = "unconfirmed_initial_span", None
        context = self._context(response)
        context = replace(
            context,
            model=context.model or stream.context.model,
            effort=context.effort or stream.context.effort,
        )
        sample = Sample(
            stream.task,
            stream.turn,
            stream.response,
            end,
            tokens,
            start,
            context,
            source=SOURCE,
            issue=issue,
            parser_revision=REVISION,
            timing_source="transport-log",
            response_aliases=aliases,
        )
        proof = (
            hmac.digest(self.secret, b"".join(stream.digests.values()), "sha256")
            if stream.issue is None
            else None
        )
        self._store(sample, digest, proof)

    def _unfinished(self, stream: Stream, issue: str) -> None:
        self._store(
            Sample(
                stream.task,
                stream.turn,
                stream.response,
                stream.at,
                None,
                context=stream.context,
                source=SOURCE,
                issue=issue,
                parser_revision=REVISION,
                timing_source="transport-log",
            ),
            None,
        )

    def _store(self, sample: Sample, digest: bytes | None, proof: bytes | None = None) -> None:
        previous = self.finished.get(sample.key)
        if previous:
            old, old_digest, old_proof = previous
            if (
                digest is not None
                and old_digest is not None
                and old_digest != digest
                or proof is not None
                and old_proof is not None
                and proof != old_proof
            ):
                sample = replace(sample, issue="transport_conflict")
            elif old.exclusion is None and sample.issue != "transport_conflict":
                return  # Replay/rotation does not replace the original receive times.
            elif old.issue == "transport_conflict":
                return
            if sample.output_tokens is None and old.output_tokens is not None:
                # A contradictory partial replay can revoke timing, but cannot
                # erase usage already observed in the original completion.
                sample = replace(old, issue=sample.issue)
            if digest is None:
                digest, proof = old_digest, old_proof
            sample = replace(
                sample,
                response_aliases=tuple(
                    sorted(set((*old.response_aliases, *sample.response_aliases)))
                ),
            )
        self.finished[sample.key] = sample, digest, proof
        self.updates[sample.key] = sample
        self.finished.move_to_end(sample.key)
        while len(self.finished) > self.capacity:
            self.finished.popitem(last=False)
