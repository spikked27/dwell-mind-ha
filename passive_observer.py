"""Transport-independent HA event projection. No HA credentials or control API.

Consumes already-authorized event envelopes; this module does not subscribe to
HA itself. Context identities stay in bounded, expiring memory, never output.
"""
import argparse
from collections import OrderedDict
import json
import math
import re
import sys

from policy import SafeError, instant, strict_json
from rooms import PROFILES

MAX_EVENT = 16384
MAX_CONTEXTS = 512
CONTEXT_TTL = 120 * 10**9
ATTRIBUTES = {"brightness": (0, 255), "color_temp": (1, 1000), "color_temp_kelvin": (1000, 20000)}
EXTRA_ATTRIBUTES = {'climate':{'temperature':(-40,140),'current_temperature':(-100,200)},
                    'cover':{'current_position':(0,100)},'fan':{'percentage':(0,100)}}
MEDIA_STATES = {"on", "off", "playing", "paused", "idle", "standby", "buffering", "unknown", "unavailable"}


def context_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else None


class Observer:
    def __init__(self, profiles, engine_entities=()):
        self.entities = {}
        for profile in profiles:
            for entity in profile.entities:
                self.entities[entity] = profile.name
        if not 1 <= len(self.entities) <= 40:
            raise SafeError("Invalid observation allowlist.")
        self.engine_entities = frozenset(engine_entities)
        self.contexts = OrderedDict()
        self.last_time = None
        self.counters = {"ignored": 0, "out_of_order": 0, "projected": 0, "contexts": 0, "gaps": 0}

    def reset(self, timestamp, reason="connection_lost"):
        """Future transport must call this on gaps; never carry state across them."""
        if reason not in {"connection_lost", "reconnected", "dropped_events"}:
            raise SafeError("Invalid telemetry-gap reason.")
        t = instant(timestamp)
        if self.last_time is not None and t < self.last_time:
            raise SafeError("Telemetry-gap timestamp is out of order.")
        self.contexts.clear()
        self.last_time = t
        self.counters["gaps"] += 1
        return [{"time": timestamp, "room": room, "entity_id": entity, "state": "unknown", "value": None,
                 "attributes": {}, "availability": "unknown", "actor": "unattributed", "source_entity": None,
                 "gap_reason": reason, "eligible_preference_label": False,
                 "label_reason": "Telemetry gap invalidates prior state and attribution; not an absence or correction label."}
                for entity, room in self.entities.items()]

    def process(self, envelope):
        if not isinstance(envelope, dict):
            raise SafeError("Invalid event envelope.")
        event = envelope.get("event", envelope)
        if not isinstance(event, dict):
            raise SafeError("Invalid event envelope.")
        kind = event.get("event_type")
        if kind not in {"state_changed", "automation_triggered", "script_started"}:
            self.counters["ignored"] += 1
            return None
        timestamp = event.get("time_fired")
        t = instant(timestamp)
        if self.last_time is not None and t < self.last_time:
            self.counters["out_of_order"] += 1
            return None
        self.last_time = t
        # Sort-free eviction is safe because accepted event time is monotonic.
        while self.contexts:
            key = next(iter(self.contexts))
            if t - self.contexts[key]["time_ns"] <= CONTEXT_TTL:
                break
            self.contexts.popitem(last=False)
        data = event.get("data")
        if not isinstance(data, dict):
            raise SafeError("Invalid event data.")
        context = event.get("context", {})
        if not isinstance(context, dict):
            context = {}
        if kind != "state_changed":
            source = data.get("entity_id")
            prefix = "automation" if kind == "automation_triggered" else "script"
            if not isinstance(source, str) or not re.fullmatch(prefix + r"\.[a-z0-9_]+", source):
                self.counters["ignored"] += 1
                return None
            key = context_id(context.get("id"))
            if key:
                parent = self.contexts.get(context_id(context.get("parent_id")))
                inherited_engine = parent is not None and parent["actor"] == "engine"
                actor = "engine" if source in self.engine_entities or inherited_engine else prefix
                self.contexts.pop(key, None)
                self.contexts[key] = {"time_ns": t, "actor": actor, "source_entity": source}
                while len(self.contexts) > MAX_CONTEXTS:
                    self.contexts.popitem(last=False)
                self.counters["contexts"] += 1
            return None
        entity = data.get("entity_id")
        if not isinstance(entity, str) or entity not in self.entities:
            self.counters["ignored"] += 1
            return None
        new = data.get("new_state")
        if new is None:
            new = {"state": "unavailable", "attributes": {}}
        if not isinstance(new, dict):
            raise SafeError("Invalid entity state.")
        ctx = new.get("context", context)
        if not isinstance(ctx, dict):
            ctx = {}
        keys = [context_id(ctx.get("id")), context_id(ctx.get("parent_id"))]
        source = next((self.contexts[k] for k in keys if k in self.contexts), None)
        actor = source["actor"] if source else "user_associated" if ctx.get("user_id") else "unattributed"
        raw_state = new.get("state")
        domain = entity.split(".", 1)[0]
        value = None
        if domain == "sensor":
            try:
                number = float(raw_state)
                low = -100 if new.get('device_class') == 'temperature' else -10**7 if new.get('device_class')=='power' else 0
                value = number if math.isfinite(number) and low <= number <= 10**7 else None
            except (ValueError, TypeError, OverflowError):
                pass
            projected_state = "numeric" if value is not None else raw_state if raw_state in ("unknown", "unavailable") else "other"
        else:
            allowed = MEDIA_STATES if domain == 'media_player' else {'open','closed','opening','closing','unknown','unavailable'} if domain in {'cover','valve'} else {'heat','cool','auto','dry','fan_only','heat_cool','off','unknown','unavailable'} if domain == 'climate' else {'on','off','unknown','unavailable'}
            projected_state = raw_state if isinstance(raw_state, str) and raw_state in allowed else "other"
        attrs = new.get("attributes", {})
        if not isinstance(attrs, dict):
            attrs = {}
        selected = {}
        if domain == 'light' or domain in EXTRA_ATTRIBUTES:
            for key, (low, high) in (ATTRIBUTES if domain=='light' else EXTRA_ATTRIBUTES[domain]).items():
                value_attr = attrs.get(key)
                if type(value_attr) in {int, float} and low <= value_attr <= high and math.isfinite(value_attr):
                    selected[key] = value_attr
        availability = projected_state if projected_state in {"unknown", "unavailable", "other"} else "reported"
        if domain=='climate' and attrs.get('hvac_action') in {'heating','cooling','idle','off','fan','drying'}:
            selected['hvac_action']=attrs['hvac_action']
        self.counters["projected"] += 1
        return {"time": timestamp, "room": self.entities[entity], "entity_id": entity,
                "state": projected_state, "value": value, "attributes": selected, "availability": availability,
                "actor": actor, "source_entity": source["source_entity"] if source else None,
                "eligible_preference_label": False,
                "label_reason": "Action attribution alone does not establish a manual correction or desired preference."}


def main():
    parser = argparse.ArgumentParser(description="Project authorized HA event JSONL; no network or device control")
    parser.add_argument("--room", choices=[*PROFILES, "both"], default="both")
    parser.add_argument("--engine-entity", action="append", default=[])
    args = parser.parse_args()
    profiles = list(PROFILES.values()) if args.room == "both" else [PROFILES[args.room]]
    observer = Observer(profiles, args.engine_entity)
    for _ in range(10000):
        raw = sys.stdin.buffer.readline(MAX_EVENT + 1)
        if not raw:
            break
        if len(raw) > MAX_EVENT:
            raise SystemExit("Event exceeds input byte budget; stopped without logging payload.")
        try:
            projected = observer.process(strict_json(raw))
        except (SafeError, ValueError, TypeError):
            raise SystemExit("Malformed event; stopped without logging payload.") from None
        if projected:
            print(json.dumps(projected, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    print(json.dumps({"observer_counters": observer.counters}), file=sys.stderr)


if __name__ == "__main__":
    main()
