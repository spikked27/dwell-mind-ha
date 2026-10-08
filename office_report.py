"""Offline Office diagnostics and chronological sensor-routine baseline.

Never actuates, labels preferences, or treats missing rows as absence.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

from office_collect import ENTITIES
from policy import instant, strict_json
from rooms import OFFICE, PROFILES

LIGHT = "light.office_ceiling"
PIR = "binary_sensor.office_motion_occupancy"
MMWAVE = "binary_sensor.office_presence_occupancy"
LUX = "sensor.office_ambient_illuminance_lux"
NS = 10**9
BIN = 15 * 60 * NS
STATE_HOLD = 120 * 60 * NS
LUX_HOLD = 10 * 60 * NS


def local_time(ns, zone):
    return datetime.fromtimestamp(ns / NS, ZoneInfo(zone)).isoformat()


def state(row):
    value = row.get("state")
    if value in {"on", "off"}:
        return value
    # Explicit unavailable/unknown must not be reinterpreted via stale numeric values.
    if value is not None:
        return None
    value = row.get("value")
    if type(value) in {int, float} and value in (0, 1):
        return "on" if value == 1 else "off"
    return None


def routine_model(bins):
    """Beta-smoothed hourly sensor evidence. Never trains on light states."""
    days = sorted({b["date"] for b in bins})
    scored = []
    for index, date in enumerate(days):
        if index < 2:
            continue
        training = [b for b in bins if b["date"] < date]
        if not training:
            continue
        prior = (sum(b["label"] for b in training) + 1) / (len(training) + 2)
        for b in (b for b in bins if b["date"] == date):
            relevant = [t for t in training if t["hour"] == b["hour"]]
            p = (sum(t["label"] for t in relevant) + 1) / (len(relevant) + 2) if len(relevant) >= 3 else prior
            scored.append((b["label"], p, prior))
    hours = []
    for hour in range(24):
        relevant = [b for b in bins if b["hour"] == hour]
        hours.append({"hour": hour, "training_bins": len(relevant),
                      "sensor_active_probability": round((sum(b["label"] for b in relevant) + 1)
                                                         / (len(relevant) + 2), 3) if len(relevant) >= 3 else None,
                      "abstain": len(relevant) < 3})
    return {"kind": "Beta-smoothed hour-of-day sensor-evidence baseline", "training_bins": len(bins),
            "observed_days": len(days), "hours": hours,
            "evaluation": {"method": "Chronological expanding window; first two observed days are training only",
                           "test_bins": len(scored),
                           "hourly_brier": round(sum((y-p)**2 for y,p,q in scored)/len(scored), 4) if scored else None,
                           "constant_brier": round(sum((y-q)**2 for y,p,q in scored)/len(scored), 4) if scored else None},
            "use": "Exploratory shadow output only. Sensor activity is not verified human occupancy.",
            "preference_labels": 0, "device_control": False}


def analyze(data, state_hold_minutes=120, profile=OFFICE):
    if type(state_hold_minutes) is not int or not 1 <= state_hold_minutes <= 360:
        raise ValueError("State hold must be 1–360 minutes")
    state_hold = state_hold_minutes * 60 * NS
    if data.get("room", profile.name) != profile.name:
        raise ValueError("Dataset room does not match selected profile")
    LIGHT, PIR, MMWAVE, LUX = profile.primary_light, profile.pir, profile.mmwave, profile.lux
    entities = ENTITIES if profile == OFFICE else profile.entities
    zone = data["timezone"]
    summaries, bins, candidates = [], [], []
    total = Counter()
    entity_summary = {e: {"rows": 0, "empty_days": 0, "incomplete_days": 0} for e in entities}
    invalid_state_rows = Counter()
    extra_light_summary = Counter()
    media_states = Counter()
    brightness = []
    light_ons = []
    for window in data["windows"]:
        start, end = instant(window["start"]), instant(window["end"])
        events = defaultdict(list)
        timeline = {start, end}
        for entity in entities:
            entry = window["entities"].get(entity, {"rows": [], "complete": False})
            entity_summary[entity]["rows"] += len(entry["rows"])
            entity_summary[entity]["empty_days"] += not entry["rows"]
            entity_summary[entity]["incomplete_days"] += not entry["complete"]
            if not entry["complete"]:
                continue  # Never extrapolate a partial/error window.
            for row in entry["rows"]:
                if entity in {LIGHT, PIR, MMWAVE, profile.derived, profile.extra_light}:
                    if row.get("state") in {"unknown", "unavailable"}:
                        invalid_state_rows[entity] += 1
                if entity == profile.extra_light:
                    for field in ["brightness", "color_temp_kelvin", "color_temp"]:
                        if row.get(field) is not None:
                            extra_light_summary[field + "_non_null"] += 1
                if profile.media and entity == profile.media:
                    media_state = row.get("state") or row.get("state_str")
                    if isinstance(media_state, str):
                        media_states[media_state] += 1
                t = instant(row["time"])
                if not start <= t < end:
                    raise ValueError("Offline row outside collection window")
                events[t].append((entity, row))
                timeline.add(t)
                timeline.add(min(end, t + (LUX_HOLD if entity == LUX else state_hold)))
                if entity == LIGHT and row.get("state") == "on" and type(row.get("brightness")) in {float, int}:
                    brightness.append(row["brightness"])
        # Reset state at every day boundary: deliberately no carry-in.
        current = {}
        latest = {}
        day = Counter()
        daybins = defaultdict(Counter)
        boundaries = sorted(timeline | set(range(start, end, BIN)))
        episodes = []
        previous_brightness = None
        previous_light = None
        for left, right in zip(boundaries, boundaries[1:]):
            for entity, row in events.get(left, []):
                if entity == LIGHT and (entity not in latest or left - latest[entity] >= state_hold):
                    previous_light = None
                    previous_brightness = None
                if entity == LUX:
                    current[entity] = row.get("value") if type(row.get("value")) in {int, float} else None
                elif profile.media and entity == profile.media:
                    current[entity] = row.get("state") or row.get("state_str")
                else:
                    current[entity] = state(row)
                latest[entity] = left
                if entity == LIGHT:
                    now = state(row)
                    if now == "on" and previous_light == "off":
                        day["observed_light_on_transitions"] += 1
                        value = current.get(LUX) if left - latest.get(LUX, -STATE_HOLD) <= LUX_HOLD else None
                        light_ons.append({"time": local_time(left, zone), "brightness": row.get("brightness"),
                                          "preceding_lux": value, "actor": "unknown; not a preference label"})
                    if (now == "on" and previous_light == "on" and row.get("brightness") is not None
                            and previous_brightness is not None and row["brightness"] != previous_brightness):
                        day["brightness_change_records"] += 1
                    previous_light = now
                    previous_brightness = row.get("brightness") if now == "on" else None
            def held(entity):
                if entity not in latest or left - latest[entity] >= state_hold:
                    return None
                return current.get(entity)
            pir, mmwave, light = held(PIR), held(MMWAVE), held(LIGHT)
            derived, extra_light = held(profile.derived), held(profile.extra_light)
            media = held(profile.media) if profile.media else None
            presence = ("on" if "on" in (pir, mmwave) else "off" if pir == mmwave == "off" else None)
            duration = (right - left) / NS
            day["duration_seconds"] += duration
            if presence is not None:
                day["presence_evidence_known_seconds"] += duration
                daybins[(left - start) // BIN]["known"] += duration
                if presence == "on":
                    day["sensor_active_seconds"] += duration
                    daybins[(left - start) // BIN]["active"] += duration
            if pir is not None and mmwave is not None:
                day["both_sensors_known_seconds"] += duration
                if pir != mmwave:
                    day["sensor_disagreement_seconds"] += duration
            if light == "on":
                day["inferred_light_on_seconds"] += duration
            if presence is not None and light is not None:
                day["light_presence_comparable_seconds"] += duration
            if presence is not None and derived is not None:
                day["derived_raw_comparable_seconds"] += duration
                if presence == "on" and derived == "off":
                    day["derived_off_raw_active_seconds"] += duration
            if light == "off" and extra_light == "on":
                day["primary_off_extra_light_on_seconds"] += duration
            if presence == "on" and media in {"playing", "paused", "on", "buffering"}:
                day["sensor_active_media_active_seconds"] += duration
            category = None
            if light == "on" and presence == "off":
                day["light_on_sensors_off_seconds"] += duration
                category = "light on / both sensors report off"
            elif light == "off" and presence == "on":
                day["sensors_active_light_off_seconds"] += duration
                category = "sensor active / main light off"
            if category:
                flags = []
                if extra_light == "on":
                    flags.append("other light reports on")
                if media in {"playing", "paused", "on", "buffering"}:
                    flags.append("media reports active")
                if extra_light is None:
                    flags.append("other light state unknown")
                if profile.media and media not in {"playing", "paused", "on", "buffering", "off", "idle", "standby"}:
                    flags.append("media context unknown")
                if (episodes and episodes[-1]["category"] == category and episodes[-1]["end_ns"] == left
                        and episodes[-1]["context_flags"] == flags):
                    episodes[-1]["end_ns"] = right
                else:
                    episodes.append({"category": category, "start_ns": left, "end_ns": right, "context_flags": flags})
        for episode in episodes:
            seconds = (episode["end_ns"] - episode["start_ns"]) / NS
            if seconds >= 120:
                candidates.append({"category": episode["category"], "start": local_time(episode["start_ns"], zone),
                                   "end": local_time(episode["end_ns"], zone), "minutes": round(seconds / 60, 2),
                                   "context_flags": episode["context_flags"]})
        for index, stats in daybins.items():
            if stats["known"] >= 12 * 60:
                date = datetime.fromtimestamp((start + index * BIN) / NS, ZoneInfo(zone))
                bins.append({"date": date.date().isoformat(), "hour": date.hour,
                             "label": int(stats["active"] / stats["known"] >= 0.2)})
        total.update(day)
        summaries.append({"date": local_time(start, zone)[:10],
                          **{k: round(v / 60, 2) if k.endswith("seconds") else v for k,v in day.items()}})
    counters = {k.replace("_seconds", "_minutes"): round(v / 60, 2) if k.endswith("seconds") else v
                for k,v in total.items()}
    # Daily duration fields also need unambiguous minute units.
    summaries = [{k.replace("_seconds", "_minutes"): v for k,v in d.items()} for d in summaries]
    return {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
            "room": profile.name, "primary_light": profile.primary_light,
            "start": data["start"], "end": data["end"], "timezone": zone,
            "collection": {"queries": data["queries"], "rows": data["returned_rows"], "entities": entity_summary},
            "assumptions": ["Day starts unknown; no carry-in before first event.",
                            f"State persists for at most {state_hold_minutes} minutes, then becomes unknown; sensitivity check required.",
                            "Intervals between events are reconstructed, not independently verified continuous telemetry.",
                            "A positive sensor means sensor evidence, not a verified person; two cats may contribute.",
                            "Primary-light-off candidates do not imply the entire room is dark; other lighting, media, daylight and intent matter.",
                            "No manual/automation attribution exists in this dataset; zero preference labels.",
                            "Preceding illuminance must be at most ten minutes old; future readings never used."],
            "totals": counters, "days": summaries,
            "availability": {"explicit_invalid_state_rows": dict(invalid_state_rows),
                             "scope": "Only invalid states actually archived; absence does not prove continuous availability."},
            "extra_light": {"entity_id": profile.extra_light, "attribute_readings": dict(extra_light_summary)},
            "media": {"entity_id": profile.media, "state_record_counts": dict(media_states),
                      "scope": "Context only, never a preference or verified-occupancy label."},
            "brightness": {"on_state_readings": len(brightness), "minimum": min(brightness) if brightness else None,
                           "maximum": max(brightness) if brightness else None,
                           "scale": "HA raw 1–255; describes recorded actions, not desired settings"},
            "on_transitions": light_ons, "review_candidates": sorted(candidates, key=lambda e:e["minutes"], reverse=True),
            "routine_baseline": routine_model(bins)}


def markdown(report):
    totals = report["totals"]
    model = report["routine_baseline"]
    lines = [f"# {report.get('room', 'Office')} observation report", "", f"**Window:** {report['start']} to {report['end']} ({report['timezone']})", "",
             "Observation only: no device control and no preference training.", "",
             f"Collected {report['collection']['rows']} rows in {report['collection']['queries']} bounded queries.", "",
             "## Reconstructed evidence", "", "| Metric | Minutes |", "| --- | ---: |"]
    for key in ["duration_minutes", "presence_evidence_known_minutes", "sensor_active_minutes",
                "sensor_disagreement_minutes", "light_presence_comparable_minutes", "light_on_sensors_off_minutes",
                "sensors_active_light_off_minutes", "derived_raw_comparable_minutes", "derived_off_raw_active_minutes",
                "primary_off_extra_light_on_minutes", "sensor_active_media_active_minutes"]:
        lines.append(f"| {key.replace('_', ' ')} | {totals.get(key, 0)} |")
    lines += ["", "These durations depend on the hold-time assumption; they are not verified occupancy durations.", "",
              "## Data coverage", "", "| Entity | Rows | Empty days | Incomplete days |", "| --- | ---: | ---: | ---: |"]
    for entity, stats in report["collection"]["entities"].items():
        lines.append(f"| `{entity}` | {stats['rows']} | {stats['empty_days']} | {stats['incomplete_days']} |")
    lines += ["", "## Context and availability", "",
              f"Primary light: `{report.get('primary_light', LIGHT)}`.",
              f"Extra-light attribute readings: {report.get('extra_light', {}).get('attribute_readings', {})}.",
              f"Media state record counts: {report.get('media', {}).get('state_record_counts', {})}.",
              f"Explicit archived unknown/unavailable rows: {report.get('availability', {}).get('explicit_invalid_state_rows', {})}.",
              "Missing invalid-state rows do not prove uptime. Record counts are not durations or preference labels."]
    lines += ["", "## Longest review candidates (not errors)", ""]
    for candidate in report["review_candidates"][:10]:
        context = "; ".join(candidate.get("context_flags", [])) or "no additional recent context"
        lines.append(f"- {candidate['start']} — {candidate['minutes']} min: {candidate['category']} ({context}).")
    if not report["review_candidates"]:
        lines.append("No >=2-minute candidates under the stated assumptions.")
    lines += ["", "## Observation-only routine baseline", "",
              f"Training: {model['training_bins']} qualifying 15-minute bins across {model['observed_days']} days.",
              f"Chronological test bins: {model['evaluation']['test_bins']}.",
              f"Brier score (lower is better): hourly {model['evaluation']['hourly_brier']}; constant {model['evaluation']['constant_brier']}.",
              "The comparison is exploratory, not sufficient validation for automation.", "", "## Assumptions and limitations", ""]
    lines += ["- " + note for note in report["assumptions"]]
    lines += ["", "## Next engineering milestone", "",
              "Collect observation-only action provenance and sensor availability before learning manual preferences.",
              "Extend the shadow evaluation over multiple weeks before proposing any control policy.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Offline Office report; no archive credentials needed")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-prefix", required=True)
    parser.add_argument("--state-hold-minutes", type=int, default=120)
    parser.add_argument("--room", choices=list(PROFILES), default="office")
    args = parser.parse_args()
    with Path(args.input).open("rb") as handle:
        raw = handle.read(8388609)
    if len(raw) > 8388608:
        raise SystemExit("Input artifact exceeds byte budget.")
    report = analyze(strict_json(raw), args.state_hold_minutes, PROFILES[args.room])
    prefix = Path(args.output_prefix)
    os.umask(0o077)
    for suffix, text in [(".report.json", json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"),
                         (".report.md", markdown(report))]:
        # Never overwrite a prior report or user file.
        with Path(str(prefix) + suffix).open("x") as handle:
            handle.write(text)
    print(json.dumps({"saved_prefix": str(prefix), "training_bins": report["routine_baseline"]["training_bins"],
                      "test_bins": report["routine_baseline"]["evaluation"]["test_bins"]}))


if __name__ == "__main__":
    main()
