"""Explicit observation profiles; no runtime entity discovery or control."""
from dataclasses import dataclass
import re


ENTITY_ID = re.compile(r"[a-z][a-z0-9_]*\.[a-z0-9_]+")


@dataclass(frozen=True)
class Room:
    name: str
    primary_light: str
    pir: str
    mmwave: str
    derived: str
    lux: str
    extra_light: str
    media: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not 1 <= len(self.name) <= 64 or any(ord(c) < 32 for c in self.name):
            raise ValueError("Room name must be 1–64 printable characters")
        fields = {"primary_light":"light", "extra_light":"light", "pir":"binary_sensor",
                  "mmwave":"binary_sensor", "derived":"binary_sensor", "lux":"sensor", "media":"media_player"}
        values = []
        for key, domain in fields.items():
            value = getattr(self, key)
            if key == "media" and value is None:
                continue
            if not isinstance(value, str) or not ENTITY_ID.fullmatch(value) or value.split('.')[0] != domain:
                raise ValueError("Entity role has an invalid ID or domain")
            values.append(value)
        if len(set(values)) != len(values):
            raise ValueError("Each room role must use a distinct entity")

    @property
    def entities(self):
        light_fields = ["state", "brightness", "color_temp_kelvin", "color_temp", "color_mode_str"]
        result = {self.primary_light: light_fields, self.extra_light: light_fields,
                  self.pir: ["state", "value"], self.mmwave: ["state", "value"],
                  self.derived: ["state", "value"], self.lux: ["value"]}
        if self.media:
            result[self.media] = ["state", "state_str"]
        return result

    @property
    def mappings(self):
        return {entity: {"measurement": "lx" if entity == self.lux else "state", "fields": fields}
                for entity, fields in self.entities.items()}


OFFICE = Room("Office", "light.office_ceiling", "binary_sensor.office_motion_occupancy",
              "binary_sensor.office_presence_occupancy", "binary_sensor.office_occupancy",
              "sensor.office_ambient_illuminance_lux", "light.office_accent")
LIVING_ROOM = Room("Living Room", "light.living_room_ceiling", "binary_sensor.living_room_motion_occupancy",
                   "binary_sensor.living_room_presence_occupancy", "binary_sensor.living_room_occupancy",
                   "sensor.living_room_ambient_illuminance_lux", "light.living_room_lamp", "media_player.living_room_tv")
PROFILES = {"office": OFFICE, "living_room": LIVING_ROOM}


def configured_profiles(overrides=None):
    """Validate public room-role overrides; shipped IDs are examples only."""
    if overrides is None:
        return dict(PROFILES)
    if not isinstance(overrides, dict) or overrides.keys() - PROFILES.keys():
        raise ValueError("Only office and living_room overrides are supported")
    result = dict(PROFILES)
    allowed = {"name", "primary_light", "pir", "mmwave", "derived", "lux", "extra_light", "media"}
    for key, changes in overrides.items():
        if not isinstance(changes, dict) or changes.keys() - allowed:
            raise ValueError("Unsupported room role")
        values = {field:getattr(PROFILES[key],field) for field in allowed}
        result[key] = Room(**{**values, **changes})
    selected = [entity for room in result.values() for entity in room.entities]
    if len(selected) != len(set(selected)):
        raise ValueError("An entity cannot belong to multiple configured rooms")
    return result
