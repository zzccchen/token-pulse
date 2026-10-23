"""Extract scoped client tier settings from bounded diagnostic records."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SubmittedTier:
    at: float
    tier: str | None
    state: str  # set / cleared / unspecified / ambiguous


@dataclass(frozen=True)
class ConfigurationTag:
    at: float
    tier: str | None
    state: str  # set / unspecified / ambiguous; not a submission or request


_TRACE_SPAN = re.compile(r'([\w.]+)\{((?:[^{}"]|"(?:[^"\\]|\\.)*")*)\}')
_TRACE_FIELD = re.compile(r'([\w.]+)=("(?:[^"\\]|\\.)*"|[^\s{}"]+)(?:\s+|$)')
_TAGS_MARKER = re.compile(r'"(?:[^"\\]|\\.)*"|(?P<tags> tags_json=)')


def _trace_fields(raw: str) -> dict[str, str]:
    fields = {}
    position = 0
    while position < len(raw):
        match = _TRACE_FIELD.match(raw, position)
        if not match or match[1] in fields:
            raise ValueError("Unsupported tracing fields")
        value = json.loads(match[2]) if match[2].startswith('"') else match[2]
        fields[match[1]] = value
        position = match.end()
    return fields


def _unique_json(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def feedback_tier(task: str, at: float, body: str) -> tuple[str, ConfigurationTag] | None:
    """Read turn configuration tags, never submission/request/confirmation values."""
    if len(body) > 16000:
        return None
    markers = [match for match in _TAGS_MARKER.finditer(body) if match.lastgroup == "tags"]
    if len(markers) != 1:
        return None
    marker = markers[0]
    header, raw = body[: marker.start()], body[marker.end() :]
    prefix, separator, tail = header.rpartition(": ")
    if not separator:
        return None
    try:
        _trace_fields(tail)
        spans = {}
        position = 0
        for match in _TRACE_SPAN.finditer(prefix):
            gap = prefix[position : match.start()]
            if not re.fullmatch(r"(?::?[\w.]+:)*:?", gap) or match[1] in spans:
                return None
            spans[match[1]] = _trace_fields(match[2])
            position = match.end()
        if position != len(prefix) or not prefix.startswith("session_loop{"):
            return None
        if spans.get("session_loop", {}).get("thread_id") != task:
            return None
        turn = spans.get("turn", spans.get("codex.turn_input", {}))
        turn_id = turn.get("turn.id", "")
        if not re.fullmatch(r"[\w-]{1,100}", turn_id):
            return None
        sampling = spans.get("try_run_sampling_request", spans.get("run_sampling_request", {}))
        if sampling.get("turn_id") != turn_id:
            return None
        for fields in spans.values():
            if any(fields[key] != turn_id for key in ("turn.id", "turn_id") if key in fields):
                return None
            if any(
                fields[key] != task
                for key in ("thread_id", "thread.id", "conversation.id")
                if key in fields
            ):
                return None
        tags = json.loads(raw, object_pairs_hook=_unique_json)
        tier = tags.get("service_tier") if isinstance(tags, dict) else None
        if not isinstance(tier, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,39}", tier):
            return None
        if tier == "unset":
            return turn_id, ConfigurationTag(at, None, "unspecified")
        return turn_id, ConfigurationTag(at, tier, "set")
    except (ValueError, RecursionError):
        return None


def _fields(value: str, name: str) -> dict[str, str]:
    prefix = name + " {"
    if not value.startswith(prefix) or not value.endswith("}"):
        raise ValueError("Unexpected Debug structure")
    content = value[len(prefix) : -1]
    result = {}
    stack = []
    quoted = escaped = False
    start = 0
    for i, char in enumerate(content + ","):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "{[(":
            stack.append(char)
            if len(stack) > 64:
                raise ValueError("Debug nesting too deep")
        elif char in "}])":
            if not stack or stack.pop() != {"}": "{", "]": "[", ")": "("}[char]:
                raise ValueError("Unbalanced Debug structure")
        elif char == "," and not stack:
            part = content[start:i].strip()
            start = i + 1
            if not part:
                continue
            key, colon, raw = part.partition(":")
            if not colon or not re.fullmatch(r"[a-z_]+", key) or key in result:
                raise ValueError("Invalid or duplicate field")
            result[key] = raw.strip()
    if stack or quoted:
        raise ValueError("Truncated Debug structure")
    return result


def submitted_tier(task: str, at: float, body: str) -> tuple[str, SubmittedTier] | None:
    if len(body) > 16000:
        return None
    header = re.fullmatch(
        r'session_loop\{thread_id=(?:"([\w-]{1,100})"|([\w-]{1,100}))\}: '
        r"Submission sub=(.*)",
        body,
        re.DOTALL,
    )
    if not header or (header[1] or header[2]) != task:
        return None
    try:
        submission = _fields(header[3], "Submission")
        identifier = re.fullmatch(r'"([\w-]{1,100})"', submission.get("id", ""))
        op = _fields(submission.get("op", ""), "TurnInput")
        if not identifier or op.get("mode") not in {"Start", "StartOrSteer"}:
            return None
        request = _fields(op.get("request", ""), "TurnInputRequest")
        settings = _fields(request.get("thread_settings", ""), "ThreadSettingsOverrides")
        start = _fields(request.get("start", ""), "TurnStartOptions")
        raw = settings.get("service_tier")
        match = re.fullmatch(r'Some\(Some\("([a-z][a-z0-9_-]{0,39})"\)\)', raw or "")
        if match:
            tier, state = match[1], "set"
        elif raw in {"Some(None)", "None"}:
            tier, state = None, "cleared" if raw == "Some(None)" else "unspecified"
        else:
            return None
        # A second override position has not been verified for this adapter. Do
        # not guess its precedence, even if the thread settings contain a value.
        if start.get("service_tier", "None") != "None":
            tier, state = None, "ambiguous"
        return identifier[1], SubmittedTier(at, tier, state)
    except ValueError:
        return None
