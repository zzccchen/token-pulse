"""Normalize Codex rollout records without retaining message content."""

from __future__ import annotations

import hashlib
import math
from collections import OrderedDict, deque
from dataclasses import dataclass, replace
from datetime import datetime

from token_pulse.domain import Context, Sample, Task, bind_thread_config
from token_pulse.logs import LogEvidence, label
from token_pulse.tailer import AmbiguousObject
from token_pulse.thread_settings import ThreadSettings

REJECTIONS = {"inherited_history", "stale_usage_snapshot", "superseded_usage"}
PARSER_REVISION = 9
# This shadow only withdraws revision-1 split-usage records. An unrelated parser
# upgrade must not make the same legacy observation override newer saved proof.
LEGACY_WITHDRAWAL_REVISION = 2
GENERATED = {"reasoning", "function_call", "custom_tool_call"}
TOOL_RESULTS = {"function_call_output", "custom_tool_call_output", "tool_search_output"}


def timestamp(value: object) -> float | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            return None
        seconds = result.timestamp()
        return seconds if math.isfinite(seconds) else None
    except (ValueError, OverflowError, OSError):
        return None


def duration(value: object) -> float | None:
    if type(value) in (int, float) and 0 <= value <= 2**63 - 1 and math.isfinite(value):
        return value / 1000
    return None


@dataclass(frozen=True)
class ItemTiming:
    turn_id: str
    start: float
    end: float
    ambiguous: bool = False


@dataclass(frozen=True)
class Response:
    turn_id: str
    id: str
    at: float
    tokens: int | None
    items: tuple[tuple[str, float], ...]
    context: Context
    issue: str | None
    timings: tuple[ItemTiming | None, ...] = ()
    first_kind: str | None = None
    usage_issue: str | None = None


class Rollout:
    """Accumulate one task's events without retaining conversation content.

    Usage may arrive after tools finish. Match it to pending model output while
    keeping copied history and repeated snapshots out of newly measured samples.
    """

    def __init__(self, task_id: str, title: str = "", context: Context | None = None):
        self.task_id = task_id
        self.title = title or f"任务 {task_id[:8]}"
        self.context = context or Context()
        self.own_context = self.context
        self.turn_id = ""
        self.turn_started_at = 0.0
        self.state = "unknown"
        self.updated_at = 0.0
        self.turn_seconds: float | None = None
        self.wait_seconds: float | None = None
        self.notice: str | None = None
        self.items: dict[str, float] = {}
        self.first_output_kind: str | None = None
        self.item_timings: OrderedDict[str, ItemTiming] = OrderedDict()
        self.responses: deque[Response] = deque(maxlen=300)
        self.corrections: deque[Response] = deque(maxlen=300)
        self.seen: set[str] = set()
        self.boundary = False
        self.damaged = False
        self.damage_reason: str | None = None
        self.inherited = False
        self.inherited_items = False
        self.last_output_total: int | None = None
        self.calls: set[str] = set()
        # Only identities/usage are retained for withdrawing v1's prematurely
        # closed samples; this tracker never supplies a speed measurement.
        self.legacy_items: dict[str, float] = {}
        self.legacy_turn = ""
        self.legacy_seen: deque[str] = deque(maxlen=300)
        self.legacy_candidate: Response | None = None
        self.has_output = False
        self.history_start: int | None = None
        self.ordinal_inherited = False
        self.history_ordinal_missing = False
        self.thread_settings = ThreadSettings(task_id)
        self.communication_input: tuple[float, int | None] | None = None

    def damage(self, reason: str) -> None:
        self.communication_input = None
        if reason in {"invalid_record", "out_of_order", "record_gap", "incomplete_boundary"}:
            self.thread_settings.gap()
        if not self.damaged:
            self.damage_reason = reason
        self.damaged = True

    def reset(self, *, legacy: bool = True) -> None:
        self.communication_input = None
        self.thread_settings.reset()
        self.items.clear()
        self.has_output = False
        self.first_output_kind = None
        self.legacy_candidate = None
        self.item_timings.clear()
        self.boundary = False
        self.damaged = True
        self.damage_reason = "incomplete_boundary"
        self.inherited_items = False
        self.last_output_total = None
        self.calls.clear()
        self.state = "unknown"
        self.updated_at = 0.0
        self.turn_id = ""
        self.turn_started_at = 0.0
        self.turn_seconds = self.wait_seconds = None
        self.notice = None
        if legacy:
            self.legacy_items.clear()
            self.legacy_turn = ""
            self.history_start = None
            self.ordinal_inherited = False
            self.history_ordinal_missing = False

    def feed(self, record: dict) -> None:
        if record.get("type") != "token_usage_record":
            self.legacy_candidate = None
        payload = record.get("payload")
        if record.get("type") == "session_meta" and isinstance(payload, dict):
            boundary = payload.get("subagent_history_start_ordinal")
            if payload.get("id") == self.task_id and type(boundary) is int and boundary >= 0:
                self.history_start = boundary
        ordinal = record.get("ordinal")
        if self.history_start is not None and record.get("type") != "session_meta":
            if type(ordinal) is not int:
                self.history_ordinal_missing = True
            else:
                inherited = ordinal < self.history_start
                if self.ordinal_inherited and not inherited:
                    self.reset(legacy=False)
                    self.inherited = False
                    self.context = self.own_context
                self.ordinal_inherited = inherited
        previous_boundary = self._legacy_boundary(record)
        before = self.responses[-1] if self.responses else None
        self._feed(record)
        if record.get("type") == "token_usage_record":
            self.legacy_candidate = None
        if previous_boundary is not None:
            after = self.responses[-1] if self.responses else None
            if after is before or (
                previous_boundary.turn_id,
                previous_boundary.id,
                previous_boundary.items,
                previous_boundary.tokens,
            ) != (after.turn_id, after.id, after.items, after.tokens):
                self.corrections.append(previous_boundary)

    def _legacy_boundary(self, record: dict) -> Response | None:
        value = record.get("payload")
        at = timestamp(record.get("timestamp"))
        if not isinstance(value, dict) or at is None:
            return None
        kind, event = record.get("type"), value.get("type")
        if not isinstance(kind, str) or (event is not None and not isinstance(event, str)):
            return None
        if kind == "turn_context" or (kind == "event_msg" and event == "task_started"):
            turn = value.get("turn_id")
            if isinstance(turn, str) and turn != self.legacy_turn:
                self.legacy_turn = turn
                self.legacy_items.clear()
        if kind == "response_item" and (
            event in {"reasoning", "function_call", "custom_tool_call"}
            or event == "message"
            and value.get("role") == "assistant"
        ):
            item_id = value.get("id")
            if isinstance(item_id, str) and item_id:
                self.legacy_items.setdefault(item_id, at)
            if len(self.legacy_items) > 1000:
                self.legacy_items.clear()
        if not self.legacy_items:
            return None
        response_id = None
        if kind == "event_msg" and event == "token_count":
            info = value.get("info")
            usage = info.get("last_token_usage") if isinstance(info, dict) else None
        elif kind == "token_usage_record" and value.get("thread_id", self.task_id) == self.task_id:
            usage, response_id = value.get("usage"), value.get("response_id")
        else:
            return None
        if not isinstance(response_id, str) or not response_id:
            response_id = hashlib.sha256("|".join(self.legacy_items).encode()).hexdigest()
        items = tuple(self.legacy_items.items())
        self.legacy_items.clear()
        if response_id in self.legacy_seen:
            return None
        self.legacy_seen.append(response_id)
        return Response(
            self.legacy_turn,
            response_id,
            at,
            usage.get("output_tokens") if isinstance(usage, dict) else None,
            items,
            self.context,
            "inherited_history"
            if self.inherited or self.inherited_items or self.ordinal_inherited
            else "stale_usage_snapshot",
        )

    def _feed(self, record: dict) -> None:
        communication_input = self.communication_input
        self.communication_input = None
        value = record.get("payload")
        at = timestamp(record.get("timestamp"))
        if not isinstance(value, dict) or at is None:
            self.damage("invalid_record")
            return
        kind = record.get("type")
        event = value.get("type")
        if not isinstance(kind, str) or (event is not None and not isinstance(event, str)):
            self.damage("invalid_record")
            return
        metadata_ambiguous = isinstance(record, AmbiguousObject) or at < self.updated_at
        if at < self.updated_at - 0.001:
            self.damage("out_of_order")
        if kind == "inter_agent_communication_metadata":
            ordinal = record.get("ordinal")
            if (
                not metadata_ambiguous
                and not isinstance(value, AmbiguousObject)
                and type(value.get("trigger_turn")) is bool
                and (ordinal is None or type(ordinal) is int and ordinal >= 0)
            ):
                self.communication_input = at, ordinal
            return
        if kind == "session_meta":
            owner = value.get("id")
            if isinstance(owner, str):
                inherited = owner != self.task_id
                if self.inherited and not inherited:
                    self.reset(legacy=False)
                    self.context = self.own_context
                self.inherited = inherited
                if inherited:
                    self.thread_settings.reset()
            if not self.inherited:
                self.context = replace(self.context, provider=label(value.get("model_provider")))
                self.own_context = self.context
        elif kind == "turn_context":
            new_turn = value.get("turn_id")
            if isinstance(new_turn, str) and new_turn != self.turn_id:
                self._turn(new_turn, at)
            self.context = replace(
                self.context, model=label(value.get("model")), effort=label(value.get("effort"))
            )
            if not self.inherited and not self.ordinal_inherited:
                self.own_context = self.context
        elif kind == "event_msg":
            if event == "item_completed":
                self._item_timing(value)
            elif event == "thread_settings_applied":
                # Modern forks append a child-owned settings boundary after copied
                # ancestor records. Older files resume with their own SessionMeta.
                if self.inherited and value.get("thread_id") == self.task_id:
                    self.reset(legacy=False)
                    self.inherited = False
                    self.context = self.own_context
                self.thread_settings.feed(
                    value,
                    at,
                    inherited=self.inherited
                    or self.ordinal_inherited
                    or self.history_ordinal_missing,
                    ambiguous=metadata_ambiguous,
                )
            elif event == "task_started":
                self._turn(str(value.get("turn_id", "")), at)
                self.thread_settings.feed(
                    value,
                    at,
                    inherited=self.inherited
                    or self.ordinal_inherited
                    or self.history_ordinal_missing,
                    ambiguous=metadata_ambiguous,
                )
            elif event in {"task_complete", "turn_aborted", "task_failed"}:
                self.state = "idle" if event == "task_complete" else "error"
                self.notice = None if event == "task_complete" else "request_failed"
                self.turn_seconds = duration(value.get("duration_ms"))
                self.wait_seconds = duration(value.get("time_to_first_token_ms"))
                self.calls.clear()
                self.updated_at = at
            elif event in {"error", "stream_error"}:
                self.notice = "request_failed"
                self.state = "error"
                self.damage("request_error")
                self.updated_at = at
            elif event == "token_count":
                info = value.get("info")
                info = info if isinstance(info, dict) else {}
                usage = info.get("last_token_usage")
                total = info.get("total_token_usage")
                total = total.get("output_tokens") if isinstance(total, dict) else None
                previous = self.last_output_total
                usage_issue = None
                if type(total) is int and 0 <= total <= 2**63 - 1:
                    self.last_output_total = total
                    if previous is not None and total == previous:
                        return  # Rate-limit/resume notifications can repeat old usage.
                    if previous is not None and self.has_output:
                        if total < previous:
                            usage_issue = "usage_counter_reset"
                        elif isinstance(usage, dict) and total - previous != usage.get(
                            "output_tokens"
                        ):
                            usage_issue = "usage_scope_mismatch"
                    elif (
                        self.has_output
                        and isinstance(usage, dict)
                        and total != usage.get("output_tokens")
                    ):
                        usage_issue = "missing_usage_baseline"
                else:
                    usage_issue = "missing_usage_baseline"
                    self.last_output_total = None
                if self.has_output:
                    self._usage(usage, at, None, legacy=True, usage_issue=usage_issue)
        elif kind == "response_item":
            output = event in GENERATED
            output |= event == "message" and value.get("role") == "assistant"
            known_input = event == "message" and value.get("role") in {
                "user",
                "system",
                "developer",
            }
            known_input |= self._input_item(record, at, communication_input)
            if not output and not known_input and event not in TOOL_RESULTS:
                # Unknown generated items may include tool execution or otherwise
                # unobserved output. Never measure only the recognized suffix.
                self.damage("unsupported_output_item")
                output = True
            if output:
                self.legacy_candidate = None
                if not self.has_output:
                    self.first_output_kind = event
                self.has_output = True
                item_id = value.get("id")
                if not isinstance(item_id, str) or not item_id:
                    self.damage("missing_item_id")
                elif item_id in self.items:
                    if self.items[item_id] != at:
                        self.damage("conflicting_item")
                else:
                    self.items[item_id] = at
                self.state = "generating"
                if event in {"function_call", "custom_tool_call"}:
                    self.calls.add(str(value.get("call_id", item_id)))
                    self.state = "tool"
                self.updated_at = at
            elif event in TOOL_RESULTS:
                self.calls.discard(str(value.get("call_id", "")))
                self.state = "tool" if self.calls else "waiting"
                # Codex emits legacy token_count only after pending tools resolve.
                # Keep the model items; tool result timestamps never extend their
                # output interval, even when usage arrives much later.
                self.updated_at = at
        elif kind == "token_usage_record":
            if value.get("thread_id", self.task_id) != self.task_id:
                self.legacy_candidate = None
                self.inherited_items = bool(self.items)
                return
            explicit_turn = value.get("turn_id")
            if not isinstance(explicit_turn, str):
                explicit_turn = self.turn_id
            if explicit_turn != self.turn_id:
                self.legacy_candidate = None
                self.damage("turn_mismatch")
            if value.get("thread_id") != self.task_id or value.get("turn_id") != self.turn_id:
                self.legacy_candidate = None
            self._usage(value.get("usage"), at, value.get("response_id"), turn_id=explicit_turn)
        if len(self.items) > 1000:
            self.items.clear()
            self.item_timings.clear()
            self.damage("too_many_items")

    @staticmethod
    def _input_item(record: dict, at: float, communication: tuple | None) -> bool:
        """Recognize outer input metadata without interpreting message/tool content."""
        value = record["payload"]
        if isinstance(record, AmbiguousObject) or isinstance(value, AmbiguousObject):
            return False
        if value.get("type") == "additional_tools":
            return value.get("role") in {"system", "developer"} and isinstance(
                value.get("tools"), list
            )
        if value.get("type") != "agent_message" or communication is None:
            return False
        previous_at, previous_ordinal = communication
        ordinal = record.get("ordinal")
        adjacent = (
            ordinal is None
            and previous_ordinal is None
            or type(ordinal) is int
            and type(previous_ordinal) is int
            and ordinal == previous_ordinal + 1
        )
        content = value.get("content")
        return (
            at >= previous_at
            and adjacent
            and all(
                isinstance(value.get(key), str) and 0 < len(value[key]) <= 160
                for key in ("author", "recipient")
            )
            and isinstance(content, list)
            and bool(content)
            and all(
                isinstance(part, dict)
                and not isinstance(part, AmbiguousObject)
                and (
                    part.get("type") == "input_text"
                    and isinstance(part.get("text"), str)
                    or part.get("type") == "encrypted_content"
                    and isinstance(part.get("encrypted_content"), str)
                )
                for part in content
            )
        )

    def _turn(self, turn_id: str, at: float) -> None:
        self.communication_input = None
        if turn_id != self.turn_id:
            self.items.clear()
            self.has_output = False
            self.first_output_kind = None
            self.legacy_candidate = None
            self.item_timings.clear()
            self.calls.clear()
            self.turn_id = turn_id
            self.turn_started_at = at
            self.boundary = True
            self.damaged = False
            self.damage_reason = None
            self.inherited_items = False
            self.turn_seconds = self.wait_seconds = None
            self.notice = None
        self.state = "waiting"
        self.updated_at = at

    def _usage(
        self,
        usage: object,
        at: float,
        response_id: object,
        *,
        legacy: bool = False,
        usage_issue: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
        explicit_id = isinstance(response_id, str) and bool(response_id)
        if explicit_id and response_id in self.seen:
            return  # A replay must not consume the next response's pending items.
        candidate = self.legacy_candidate
        if (
            not legacy
            and explicit_id
            and not self.has_output
            and candidate is not None
            and candidate.turn_id == self.turn_id
            and candidate.tokens == tokens
            and at >= candidate.at
            and type(tokens) is int
            and tokens >= 0
        ):
            # A scoped explicit usage following the legacy usage closes the same
            # pending output. Replace its provisional identity, including on disk.
            self.responses = deque((r for r in self.responses if r is not candidate), maxlen=300)
            self.corrections.append(replace(candidate, issue="superseded_usage"))
            response = replace(candidate, id=response_id, usage_issue=None)
            self.responses.append(response)
            self.seen.add(response_id)
            self.legacy_candidate = None
            return
        if not self.has_output and not explicit_id:
            return
        if not isinstance(response_id, str) or not response_id:
            identity = "|".join(self.items) or f"{self.turn_id}:{at}:unidentified-output"
            response_id = hashlib.sha256(identity.encode()).hexdigest()
        if response_id in self.seen:
            self.items.clear()
            return
        issue = "incomplete_boundary" if not self.boundary else None
        if self.damaged:
            issue = self.damage_reason or "record_gap"
        if self.history_ordinal_missing:
            issue = "missing_history_ordinal"
        if self.inherited or self.inherited_items or self.ordinal_inherited:
            issue = "inherited_history"
        context = self.context
        if issue != "inherited_history":
            first = next(iter(self.items), None)
            timing = self.item_timings.get(first)
            before = timing.start if timing and not timing.ambiguous else self.items.get(first, at)
            context = self.thread_settings.capture(
                context, self.turn_id if turn_id is None else turn_id, before
            )
        self.responses.append(
            Response(
                self.turn_id if turn_id is None else turn_id,
                response_id,
                at,
                tokens,
                tuple(self.items.items()),
                context,
                issue,
                tuple(self.item_timings.get(item) for item in self.items),
                self.first_output_kind,
                usage_issue,
            )
        )
        self.legacy_candidate = self.responses[-1] if legacy else None
        self.seen = {r.id for r in self.responses}
        for item in self.items:
            self.item_timings.pop(item, None)
        self.items.clear()
        self.has_output = False
        self.first_output_kind = None
        self.boundary = True
        self.damaged = False
        self.damage_reason = None
        self.inherited_items = False

    def _item_timing(self, value: dict) -> None:
        if value.get("thread_id") != self.task_id:
            return
        item = value.get("item")
        # Tool ItemCompleted timestamps describe execution, not generated arguments.
        if not isinstance(item, dict) or item.get("type") not in {"Reasoning", "AgentMessage"}:
            return
        item_id, turn = item.get("id"), value.get("turn_id")
        start, end = value.get("started_at_ms"), value.get("completed_at_ms")
        if (
            not isinstance(item_id, str)
            or not item_id
            or len(item_id) > 160
            or not isinstance(turn, str)
            or not turn
            or type(start) is not int
            or type(end) is not int
            or not 0 < start <= end <= 2**53
        ):
            return
        timing = ItemTiming(turn, start / 1000, end / 1000)
        previous = self.item_timings.get(item_id)
        if previous and previous != timing:
            timing = replace(previous, ambiguous=True)
        self.item_timings[item_id] = timing
        while len(self.item_timings) > 2000:
            self.item_timings.popitem(last=False)

    def samples(self, evidence: LogEvidence) -> list[Sample]:
        samples = []
        for response in (*self.responses, *self.corrections):
            revision = (
                LEGACY_WITHDRAWAL_REVISION
                if response.issue == "stale_usage_snapshot"
                else PARSER_REVISION
            )
            starts = [
                evidence.start(self.task_id, item, response.turn_id) for item, _ in response.items
            ]
            issue = response.issue or response.usage_issue
            tool_first = response.first_kind in {"function_call", "custom_tool_call"}
            if tool_first:
                # The added event already identifies the selected tool. Its timestamp
                # cannot establish when all tokens in the response usage began, even
                # if argument streaming subsequently lasts a long time.
                issue = issue or "unconfirmed_tool_start"
            context = self.thread_settings.validate(response.context, response.turn_id)
            start_at = None
            timing_source = None
            timings = response.timings or (None,) * len(response.items)
            if not response.items:
                samples.append(
                    Sample(
                        self.task_id,
                        response.turn_id,
                        response.id,
                        response.at,
                        response.tokens,
                        context=self._submitted(context, response.turn_id, response.at, evidence),
                        issue=issue or "missing_output_items",
                        parser_revision=revision,
                    )
                )
                continue
            first_timing = timings[0]
            first_start = starts[0]
            coincident_start = (
                first_timing is not None
                and not first_timing.ambiguous
                and first_timing.turn_id == response.turn_id
                and first_timing.start == first_timing.end
                and first_start is not None
                and not first_start.ambiguous
                and first_start.turn_id == response.turn_id
                and abs(first_start.at - first_timing.end) <= 0.001
            )
            if coincident_start:
                # Millisecond lifecycle bounds collapse to one instant; a log
                # notification at that same instant does not restore generation.
                issue = issue or "unconfirmed_initial_span"
            # Response items are persisted in streamed output order. Its first
            # item is the response's output boundary; later tool call starts need
            # not survive log rotation. Validate every additional boundary that
            # is available, and fail closed if it contradicts that ordering.
            if first_start is None and first_timing is None:
                issue = issue or "missing_start"
            elif any(
                start is not None and (start.ambiguous or start.turn_id != response.turn_id)
                for start in starts
            ) or any(
                timing is not None and (timing.ambiguous or timing.turn_id != response.turn_id)
                for timing in timings
            ):
                issue = issue or "ambiguous_response"
            elif first_start is None:
                # Core may synthesize both lifecycle events while handling an
                # already completed item. Positive elapsed time includes local
                # processing and cannot prove a streaming origin at any speed.
                issue = issue or (
                    "missing_stream_start"
                    if first_timing.start == first_timing.end
                    else "unverified_lifecycle_start"
                )
                if first_timing.start != first_timing.end:
                    start_at = first_timing.start  # Retain the unverified observation for audit.
                timing_source = "item-event"
            else:
                start_at = first_start.at
                timing_source = first_start.origin
                if any(
                    start is not None and (start.at < start_at - 0.001 or start.at > end + 0.001)
                    for start, (_, end) in zip(starts, response.items, strict=True)
                ) or any(
                    timing is not None
                    and (timing.start < start_at - 0.001 or timing.end > end + 0.001)
                    for timing, (_, end) in zip(timings, response.items, strict=True)
                ):
                    issue = issue or "invalid_duration"
                if first_start:
                    context = replace(
                        context,
                        auth_mode=first_start.auth_mode,
                        requested_tier=first_start.requested_tier,
                    )
            end = max(
                timing.end if timing is not None and not timing.ambiguous else end
                for timing, (_, end) in zip(timings, response.items, strict=True)
            )
            if evidence.interrupted(
                self.task_id,
                response.turn_id,
                start_at if start_at is not None else response.items[0][1],
                end,
            ):
                issue = issue or "interrupted_response"
            context = self._submitted(context, response.turn_id, end, evidence)
            context = bind_thread_config(context, output_start=start_at)
            if tool_first:
                start_at = None
                timing_source = "tool-item-notification"
            elif coincident_start:
                start_at = None
                timing_source = "coincident-item-boundaries"
            samples.append(
                Sample(
                    self.task_id,
                    response.turn_id,
                    response.id,
                    end,
                    response.tokens,
                    start_at,
                    context,
                    issue=issue,
                    parser_revision=revision,
                    timing_source=timing_source,
                    response_aliases=(
                        hashlib.sha256(
                            "|".join(item for item, _ in response.items).encode()
                        ).hexdigest(),
                    )
                    if response.issue is None
                    and response.usage_issue is None
                    and issue != "interrupted_response"
                    else (),
                )
            )
        return samples

    def _submitted(self, context: Context, turn: str, at: float, evidence: LogEvidence) -> Context:
        submitted = evidence.submissions.get((self.task_id, turn))
        if submitted is not None and submitted.at <= at + 0.001:
            context = replace(
                context,
                submitted_tier=submitted.tier,
                submitted_tier_state=submitted.state,
            )
            if submitted.override_at is not None and submitted.override_at <= at + 0.001:
                context = replace(
                    context,
                    turn_override_tier=submitted.override_tier,
                    turn_override_state=submitted.override_state,
                    turn_override_at=submitted.override_at,
                )
        tag = evidence.config_tags.get((self.task_id, turn))
        if tag is not None and tag.at <= at + 0.001:
            context = replace(context, config_tag_tier=tag.tier, config_tag_state=tag.state)
        return bind_thread_config(context)

    def task(self, evidence: LogEvidence) -> Task:
        samples = [sample for sample in self.samples(evidence) if sample.issue not in REJECTIONS]
        latest = samples[-1] if samples else None
        inherited = self.inherited or self.ordinal_inherited
        context = self._submitted(
            self.own_context if inherited else self.context,
            self.turn_id,
            self.updated_at,
            evidence,
        )
        state, updated = ("unknown", 0.0) if inherited else (self.state, self.updated_at)
        for (task_id, _), start in reversed(evidence.starts.items()):
            if not inherited and task_id == self.task_id and start.turn_id == self.turn_id:
                context = replace(
                    context, auth_mode=start.auth_mode, requested_tier=start.requested_tier
                )
                if start.at > updated:
                    state, updated = "generating", start.at
                break
        notice = None if inherited else self.notice
        if (
            not inherited
            and self.task_id in evidence.notices
            and evidence.notices[self.task_id][0] >= self.turn_started_at
        ):
            notice = evidence.notices[self.task_id][1]
        return Task(
            self.task_id,
            self.title,
            state,
            updated,
            context,
            latest,
            None if inherited else self.turn_seconds,
            None if inherited else self.wait_seconds,
            notice,
        )
