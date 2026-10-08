"""Bounded seven-day collector. Credentials are supplied in memory by the caller."""
from datetime import datetime, timedelta, timezone
import time
from zoneinfo import ZoneInfo

from policy import SafeError, instant
from rooms import OFFICE

ENTITIES = {
    "light.office_ceiling": ["state", "brightness"],
    "light.office_accent": ["state", "brightness", "color_temp_kelvin", "color_temp", "color_mode_str"],
    "binary_sensor.office_motion_occupancy": ["state", "value"],
    "binary_sensor.office_presence_occupancy": ["state", "value"],
    "binary_sensor.office_occupancy": ["state", "value"],
    "sensor.office_ambient_illuminance_lux": ["value"],
}
MAX_QUERIES = 100
MAX_ROWS = 20000


def rfc3339_ns(value):
    seconds, fraction = divmod(value, 10**9)
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction:09d}Z"


def collect(policy, end_date, days=7, zone="America/New_York", sleep=time.sleep, profile=None):
    """Calendar days, no carry-in, sequential queries with at least 5.1s spacing."""
    if type(days) is not int or not 1 <= days <= 7:
        raise SafeError("One to seven days required.")
    entities = ENTITIES if profile is None else profile.entities
    tz = ZoneInfo(zone)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=tz)
    windows = []
    for index in range(days):
        start = end - timedelta(days=days - index)
        finish = start + timedelta(days=1)
        if instant(finish.isoformat()) - instant(start.isoformat()) > 86400 * 10**9:
            raise SafeError("25-hour DST days must be collected as separate UTC windows.")
        windows.append((start.isoformat(), finish.isoformat()))
    result = {"version": 1, "timezone": zone, "created_at": datetime.now(timezone.utc).isoformat(),
              "room": (profile or OFFICE).name,
              "start": windows[0][0], "end": windows[-1][1], "queries": 0,
              "scope": "Selected entity fields only; no context IDs, manual labels, or carry-in.", "windows": []}
    last_query = None
    total_rows = 0
    for start, finish in windows:
        day = {"start": start, "end": finish, "entities": {}}
        for entity, fields in entities.items():
            entry = {"rows": [], "complete": False, "error": None, "pages": 0}
            day["entities"][entity] = entry
            cursor = start
            while True:
                if result["queries"] >= MAX_QUERIES or total_rows >= MAX_ROWS:
                    entry["error"] = "Collection budget reached."
                    break
                if last_query is not None:
                    sleep(max(0, 5.1 - (time.monotonic() - last_query)))
                last_query = time.monotonic()
                result["queries"] += 1
                try:
                    page = policy.call("sample", {"entity_id": entity, "retention_policy": "autogen",
                                                   "fields": fields, "start": cursor, "end": finish,
                                                   "limit": min(500, policy.config.max_rows), "order": "oldest"})
                    records = page["rows"]
                    stamps = [instant(r["time"]) for r in records]
                    if any(a >= b for a, b in zip(stamps, stamps[1:])):
                        raise SafeError("Duplicate or unordered timestamps prevent safe pagination.")
                    if total_rows + len(records) > MAX_ROWS:
                        raise SafeError("Collection row budget reached.")
                    entry["rows"].extend(records)
                    entry["pages"] += 1
                    total_rows += len(records)
                    if not page["truncated"]:
                        entry["complete"] = True
                        break
                    if not records:
                        raise SafeError("Empty truncated page.")
                    cursor = rfc3339_ns(stamps[-1] + 1)
                    if instant(cursor) >= instant(finish):
                        raise SafeError("Unexpected truncated endpoint.")
                except SafeError as error:
                    entry["error"] = str(error)
                    break
        result["windows"].append(day)
    result["returned_rows"] = total_rows
    return result
