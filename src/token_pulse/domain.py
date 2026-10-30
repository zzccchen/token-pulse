"""UI-independent observations. Unknown evidence remains None."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Context:
    """Independent observed settings; None is unknown, never an inferred default."""

    model: str | None = None
    effort: str | None = None
    provider: str | None = None
    auth_mode: str | None = None
    configured_tier: str | None = None
    requested_tier: str | None = None
    actual_tier: str | None = None
    submitted_tier: str | None = None
    submitted_tier_state: str | None = None
    config_tag_tier: str | None = None
    config_tag_state: str | None = None


@dataclass(frozen=True)
class Sample:
    """One output range, with Unix-second timestamps and an explicit token scope.

    Identifiers are source identities during parsing and HMAC pseudonyms in history.
    Incomplete records retain tokens/evidence; exclusion determines TPS eligibility.
    """

    task_id: str
    turn_id: str
    response_id: str
    ended_at: float
    output_tokens: int | None
    started_at: float | None = None
    context: Context = Context()
    source: str = "codex-local-log-v1"
    token_scope: str = "total_output"
    issue: str | None = None
    parser_revision: int = 1
    timing_source: str | None = None
    response_aliases: tuple[str, ...] = ()
    evidence_sources: tuple[str, ...] = ()

    @property
    def exclusion(self) -> str | None:
        if self.issue:
            return self.issue
        if self.token_scope != "total_output":
            return "unsupported_token_scope"
        if not math.isfinite(self.ended_at):
            return "invalid_timestamp"
        if self.started_at is None:
            return "missing_start"
        if not math.isfinite(self.started_at) or self.ended_at <= self.started_at:
            return "invalid_duration"
        if type(self.output_tokens) is not int or not 0 <= self.output_tokens <= 2**63 - 1:
            return "invalid_tokens"
        return None

    @property
    def seconds(self) -> float | None:
        if self.exclusion is not None:
            return None
        assert self.started_at is not None
        return self.ended_at - self.started_at

    @property
    def tps(self) -> float | None:
        seconds = self.seconds
        return self.output_tokens / seconds if seconds is not None else None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.task_id, self.turn_id, self.response_id

    @property
    def group(self) -> tuple:
        # The product summarizes each model across modes and channels. Raw context
        # remains evidence only; incompatible token scopes cannot produce a TPS.
        return (Context(model=self.context.model),)

    def is_stale(self, now: float, max_age: float = 60) -> bool:
        return now - self.ended_at > max_age or now < self.ended_at


def reconcile(previous: Sample, incoming: Sample) -> Sample:
    """Preserve complete measurements while independently refreshing tier evidence."""
    if previous.key != incoming.key:
        raise ValueError("Cannot reconcile different output samples")
    sources = tuple(
        sorted(
            set(
                (
                    previous.source,
                    incoming.source,
                    *previous.evidence_sources,
                    *incoming.evidence_sources,
                )
            )
        )
    )
    aliases = tuple(sorted(set((*previous.response_aliases, *incoming.response_aliases))))
    transport = "codex-transport-log-v1" in sources
    conflicts = {
        "transport_conflict",
        "cross_source_conflict",
        "ambiguous_response_identity",
        "interrupted_response",
    }
    reliable_usage_issues = {
        None,
        "missing_start",
        "missing_stream_start",
        "missing_output_items",
        "unconfirmed_tool_start",
        "unconfirmed_initial_span",
    }
    token_conflict = (
        transport
        and previous.parser_revision == incoming.parser_revision
        and previous.issue in reliable_usage_issues
        and incoming.issue in reliable_usage_issues
        and type(previous.output_tokens) is int
        and type(incoming.output_tokens) is int
        and previous.output_tokens != incoming.output_tokens
    )
    preferred_transport = (
        incoming.timing_source == "transport-log"
        and incoming.exclusion is None
        and previous.timing_source != "transport-log"
        and incoming.parser_revision >= previous.parser_revision
    )
    revised_measurement = (
        incoming.parser_revision > previous.parser_revision and incoming.exclusion is None
    )
    withdrawals = {
        "unconfirmed_tool_start",
        "unconfirmed_initial_span",
        "unsupported_output_item",
        "missing_usage_baseline",
        "usage_counter_reset",
        "usage_scope_mismatch",
        "missing_item_id",
        "missing_history_ordinal",
        "interrupted_response",
    }
    revised_boundary = (
        incoming.parser_revision > previous.parser_revision and incoming.issue in withdrawals
    )
    extended_boundary = (
        incoming.parser_revision == previous.parser_revision
        and incoming.exclusion is None
        and previous.exclusion is None
        and incoming.source == previous.source
        and incoming.token_scope == previous.token_scope
        and incoming.output_tokens == previous.output_tokens
        and incoming.ended_at == previous.ended_at
        and incoming.started_at < previous.started_at
        and incoming.timing_source == "stream-log"
        and previous.timing_source == "item-event"
    )
    if previous.issue in withdrawals and previous.parser_revision > incoming.parser_revision:
        base = previous
    else:
        base = (
            incoming
            if previous.exclusion is not None
            or revised_measurement
            or revised_boundary
            or extended_boundary
            or preferred_transport
            else previous
        )
    if token_conflict:
        base = replace(base, issue="cross_source_conflict")
    for observation in (previous, incoming):
        if (
            observation.issue == "interrupted_response"
            and base.timing_source == "transport-log"
            and base.exclusion is None
            and base.parser_revision >= observation.parser_revision
        ):
            continue  # A complete exact response sequence independently proves its interval.
        if observation.issue in conflicts and observation.parser_revision >= base.parser_revision:
            base = replace(base, issue=observation.issue)
    evidence = (
        incoming.context if incoming.context.submitted_tier_state is not None else previous.context
    )
    config_tag = (
        incoming.context if incoming.context.config_tag_state is not None else previous.context
    )
    return replace(
        base,
        response_aliases=aliases,
        evidence_sources=sources
        if transport or previous.evidence_sources or incoming.evidence_sources
        else (),
        context=replace(
            base.context,
            model=base.context.model or previous.context.model or incoming.context.model,
            effort=base.context.effort or previous.context.effort or incoming.context.effort,
            provider=base.context.provider
            or previous.context.provider
            or incoming.context.provider,
            auth_mode=base.context.auth_mode
            or previous.context.auth_mode
            or incoming.context.auth_mode,
            requested_tier=base.context.requested_tier
            or previous.context.requested_tier
            or incoming.context.requested_tier,
            actual_tier=base.context.actual_tier
            or previous.context.actual_tier
            or incoming.context.actual_tier,
            submitted_tier=evidence.submitted_tier,
            submitted_tier_state=evidence.submitted_tier_state,
            config_tag_tier=config_tag.config_tag_tier,
            config_tag_state=config_tag.config_tag_state,
        ),
    )


def response_alias_index(samples: list[Sample]) -> dict[tuple, set[str]]:
    owners: dict[tuple, set[str]] = {}
    for sample in samples:
        if not sample.turn_id:
            continue  # An alias cannot infer a turn from timestamp proximity.
        for alias in sample.response_aliases:
            if alias != sample.response_id:
                owners.setdefault((sample.task_id, sample.turn_id, alias), set()).add(
                    sample.response_id
                )
    return owners


def resolve_response_alias(sample: Sample, owners: dict[tuple, set[str]]) -> Sample:
    keys = [
        sample.key,
        *((sample.task_id, sample.turn_id, alias) for alias in sample.response_aliases),
    ]
    if any(len(owners.get(key, ())) > 1 for key in keys):
        return replace(sample, issue="ambiguous_response_identity")
    canonical = owners.get(sample.key, set())
    if len(canonical) == 1:
        return replace(
            sample,
            response_id=next(iter(canonical)),
            response_aliases=tuple(sorted(set((*sample.response_aliases, sample.response_id)))),
        )
    return sample


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    state: str
    updated_at: float
    context: Context = Context()
    latest: Sample | None = None
    turn_seconds: float | None = None
    wait_seconds: float | None = None
    notice: str | None = None

    def has_recent_state(self, now: float, max_age: float = 120) -> bool:
        return math.isfinite(self.updated_at) and 0 <= now - self.updated_at <= max_age

    def display_state(self, now: float) -> str:
        if self.state != "unknown" and not self.has_recent_state(now):
            return "stale"
        return self.state
