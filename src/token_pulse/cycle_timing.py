"""Observed response-cycle boundaries; never output-stream timing."""

from collections import OrderedDict

from token_pulse.tailer import AmbiguousObject


class CycleBoundaries:
    """Keep only scoped observed boundaries; gaps and ownership loss break them."""

    def __init__(self):
        self.starts = OrderedDict()
        self.previous = OrderedDict()
        self.seen = OrderedDict()

    def clear(self):
        self.starts.clear()
        self.previous.clear()
        self.seen.clear()

    def feed(self, record, parser, at):
        payload = record.get("payload")
        if (
            parser.inherited
            or parser.ordinal_inherited
            or parser.history_ordinal_missing
            or parser.damaged
            or isinstance(record, AmbiguousObject)
            or isinstance(payload, AmbiguousObject)
        ):
            self.clear()
            return
        if (
            type(payload) is dict
            and record.get("type") == "event_msg"
            and payload.get("type") == "task_started"
            and isinstance(payload.get("turn_id"), str)
            and payload["turn_id"]
            and at is not None
        ):
            turn = payload["turn_id"]
            # Replayed/conflicting starts do not silently replace an anchor.
            if turn in self.starts and self.starts[turn] != at:
                self.starts[turn] = None
                self.previous.pop(turn, None)
            else:
                self.starts[turn] = at
            self.starts.move_to_end(turn)
            while len(self.starts) > 128:
                self.starts.popitem(last=False)

    def response(self, response):
        key = response.turn_id, response.id
        if key in self.seen:
            return None
        self.seen[key] = None
        while len(self.seen) > 2000:
            self.seen.popitem(last=False)
        if response.usage_issue:
            self.previous.pop(response.turn_id, None)
            return None
        if response.issue not in {None, "compaction_usage"}:
            self.previous.pop(response.turn_id, None)
            return None
        start = self.previous.get(response.turn_id)
        anchor = "previous_usage"
        if start is None:
            start = self.starts.get(response.turn_id)
            anchor = "task_started"
        self.previous[response.turn_id] = response.at
        self.previous.move_to_end(response.turn_id)
        while len(self.previous) > 128:
            self.previous.popitem(last=False)
        # No future anchor or boundary crossing already observed output is used.
        observed = [at for _, at in response.items]
        observed.extend(t.start for t in response.timings if t is not None and not t.ambiguous)
        first = min(observed, default=response.at)
        if start is None or not start < first <= response.at:
            return None
        return {"elapsed_start": start, "usage_at": response.at, "anchor": anchor}
