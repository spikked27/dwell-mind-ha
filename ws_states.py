"""Decode HA 2026.9 compressed entity subscriptions into private projections.

Raw attributes are discarded immediately; only bounded light attributes survive
in memory. Subscription snapshots are observations, not action transitions.
"""
import math

from office_collect import rfc3339_ns
from passive_observer import ATTRIBUTES, context_id
from policy import SafeError


def epoch_time(value):
    if type(value) not in {int, float} or not 946684800 <= value <= 7258118400 or not math.isfinite(value):
        raise SafeError("Invalid upstream state timestamp.")
    return rfc3339_ns(round(value * 10**9))


def safe_attributes(value):
    if not isinstance(value, dict):
        raise SafeError("Invalid upstream attributes.")
    return {key: v for key, v in value.items() if key in ATTRIBUTES and type(v) in {int, float}
            and ATTRIBUTES[key][0] <= v <= ATTRIBUTES[key][1] and math.isfinite(v)}


def safe_context(value, previous=None):
    result = dict(previous or {})
    if isinstance(value, str):
        value = {"id": value}
    if not isinstance(value, dict):
        raise SafeError("Invalid upstream state context.")
    for key in ("id", "parent_id"):
        if key in value:
            result[key] = context_id(value[key])
    if "user_id" in value:
        result["user_id"] = bool(value["user_id"])  # Only presence, never identity.
    return result


class StateDecoder:
    def __init__(self, entities):
        self.entities = frozenset(entities)
        if not 1 <= len(self.entities) <= 40:
            raise SafeError("Invalid entity subscription.")
        self.states = {}

    def reset(self):
        self.states.clear()

    def _state(self, entity, value):
        if not isinstance(value, str) or len(value) > 64:
            raise SafeError("Invalid upstream entity state.")
        domain = entity.split(".", 1)[0]
        if domain == "sensor":
            try:
                number = float(value)
                if math.isfinite(number) and 0 <= number <= 10**7:
                    return value
            except (ValueError, OverflowError):
                pass
        elif value in {"on", "off", "playing", "paused", "idle", "standby", "buffering"}:
            return value
        return value if value in {"unknown", "unavailable"} else "other"

    def decode(self, event, received_time):
        if not isinstance(event, dict) or event.keys() - {"a", "c", "r"}:
            raise SafeError("Unsupported entity-diff format.")
        additions, changes, removals = event.get("a", {}), event.get("c", {}), event.get("r", [])
        if not isinstance(additions, dict) or not isinstance(changes, dict) or not isinstance(removals, list):
            raise SafeError("Invalid entity-diff sections.")
        if len(additions) + len(changes) + len(removals) > 40:
            raise SafeError("Entity-diff row budget exceeded.")
        output = []
        for entity, compressed in additions.items():
            if entity not in self.entities or not isinstance(compressed, dict):
                raise SafeError("Unexpected entity in subscription.")
            source_time = epoch_time(compressed.get("lu", compressed.get("lc")))
            self.states[entity] = {"state": self._state(entity, compressed.get("s")),
                                   "attributes": safe_attributes(compressed.get("a", {})),
                                   "context": safe_context(compressed.get("c", {})), "source_time": source_time}
            output.append(self._envelope(entity, received_time, "snapshot"))
        for entity, diff in changes.items():
            if entity not in self.entities or entity not in self.states or not isinstance(diff, dict) or diff.keys() - {"+", "-"}:
                raise SafeError("Change arrived without an initial state.")
            plus, minus = diff.get("+", {}), diff.get("-", {})
            if not isinstance(plus, dict) or not isinstance(minus, dict) or plus.keys() - {"s", "a", "c", "lc", "lu"} or minus.keys() - {"a"}:
                raise SafeError("Invalid entity change.")
            current = self.states[entity]
            if "s" in plus:
                current["state"] = self._state(entity, plus["s"])
            if "a" in plus:
                selected = safe_attributes(plus["a"])
                for key in ATTRIBUTES:
                    if key in plus["a"]:
                        current["attributes"].pop(key, None)
                current["attributes"].update(selected)
            removed = minus.get("a", [])
            if not isinstance(removed, list) or len(removed) > 1000 or any(not isinstance(k, str) for k in removed):
                raise SafeError("Invalid removed attributes.")
            for key in removed:
                current["attributes"].pop(key, None)
            if "c" in plus:
                current["context"] = safe_context(plus["c"], current["context"])
            if "lu" in plus or "lc" in plus:
                current["source_time"] = epoch_time(plus.get("lu", plus.get("lc")))
            output.append(self._envelope(entity, received_time, "state_update"))
        for entity in removals:
            if not isinstance(entity, str) or entity not in self.entities:
                raise SafeError("Unexpected removed entity.")
            self.states.pop(entity, None)
            output.append({"event_type": "state_changed", "time_fired": received_time,
                           "data": {"entity_id": entity, "new_state": None}, "record_kind": "removed"})
        return output

    def _envelope(self, entity, received_time, kind):
        current = self.states[entity]
        return {"event_type": "state_changed", "time_fired": received_time, "source_time": current["source_time"],
                "record_kind": kind, "data": {"entity_id": entity,
                 "new_state": {"state": current["state"], "attributes": dict(current["attributes"]),
                               "context": dict(current["context"])}}}
