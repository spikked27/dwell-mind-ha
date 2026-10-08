import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from office_collect import ENTITIES, collect, rfc3339_ns
from office_report import LIGHT, PIR, MMWAVE, LUX, analyze, markdown, routine_model, state
from policy import SafeError, instant


def fixture(days=1):
    start = datetime(2026, 9, 28, 4, tzinfo=timezone.utc)
    windows = []
    for day in range(days):
        begin = start + timedelta(days=day)
        end = begin + timedelta(days=1)
        entities = {e: {"rows": [], "complete": True, "error": None} for e in ENTITIES}
        windows.append({"start": begin.isoformat(), "end": end.isoformat(), "entities": entities})
    return {"timezone": "America/New_York", "start": windows[0]["start"], "end": windows[-1]["end"],
            "queries": len(ENTITIES) * days, "returned_rows": 0, "windows": windows}


def add(data, entity, minute, **fields):
    day = data["windows"][0]
    stamp = rfc3339_ns(instant(day["start"]) + minute * 60 * 10**9)
    day["entities"][entity]["rows"].append({"time": stamp, **fields})
    data["returned_rows"] += 1


class ReportTests(unittest.TestCase):
    def test_empty_is_unknown_not_inactive(self):
        report = analyze(fixture())
        self.assertEqual(report["totals"].get("presence_evidence_known_minutes", 0), 0)
        self.assertEqual(report["routine_baseline"]["training_bins"], 0)
        self.assertEqual(report["review_candidates"], [])
        self.assertEqual(report["routine_baseline"]["preference_labels"], 0)

    def test_hold_limit_no_carry_in(self):
        data = fixture()
        add(data, PIR, 60, state="off")
        add(data, MMWAVE, 60, state="off")
        report = analyze(data)
        self.assertEqual(report["totals"]["presence_evidence_known_minutes"], 120)
        self.assertEqual(report["routine_baseline"]["training_bins"], 8)

    def test_sensitivity_hold_is_explicit(self):
        data = fixture()
        add(data, PIR, 0, state="on")
        self.assertEqual(analyze(data, state_hold_minutes=30)["totals"]["sensor_active_minutes"], 30)
        with self.assertRaises(ValueError):
            analyze(data, state_hold_minutes=0)

    def test_positive_sensor_with_other_unknown(self):
        data = fixture()
        add(data, PIR, 0, state="on")
        report = analyze(data)
        self.assertEqual(report["totals"]["sensor_active_minutes"], 120)
        self.assertEqual(report["totals"].get("both_sensors_known_minutes", 0), 0)

    def test_unknown_invalidates_instead_of_using_value(self):
        self.assertIsNone(state({"state": "unavailable", "value": 1}))
        self.assertEqual(state({"value": 0}), "off")
        data = fixture()
        add(data, PIR, 0, state="on")
        add(data, PIR, 30, state="unavailable", value=1)
        report = analyze(data)
        self.assertEqual(report["totals"]["sensor_active_minutes"], 30)

    def test_review_candidates_not_preferences(self):
        data = fixture()
        for entity in (PIR, MMWAVE):
            add(data, entity, 0, state="off")
        add(data, LIGHT, 0, state="on", brightness=255)
        report = analyze(data)
        self.assertEqual(report["totals"]["light_on_sensors_off_minutes"], 120)
        self.assertEqual(report["review_candidates"][0]["minutes"], 120)
        self.assertEqual(report["routine_baseline"]["preference_labels"], 0)
        self.assertFalse(report["routine_baseline"]["device_control"])

    def test_disagreement(self):
        data = fixture()
        add(data, PIR, 0, state="on")
        add(data, MMWAVE, 0, state="off")
        report = analyze(data)
        self.assertEqual(report["totals"]["sensor_disagreement_minutes"], 120)

    def test_incomplete_window_not_extrapolated(self):
        data = fixture()
        add(data, PIR, 0, state="on")
        data["windows"][0]["entities"][PIR]["complete"] = False
        report = analyze(data)
        self.assertEqual(report["totals"].get("sensor_active_minutes", 0), 0)
        self.assertEqual(report["collection"]["entities"][PIR]["incomplete_days"], 1)

    def test_no_future_lux_or_stale_lux(self):
        for minute in [11, 30]:
            data = fixture()
            add(data, LIGHT, 0, state="off")
            add(data, LIGHT, 20, state="on", brightness=200)
            add(data, LUX, minute, value=5)
            report = analyze(data)
            # At minute 11 this reading is fresh; minute 30 is in the future.
            expected = 5 if minute == 11 else None
            self.assertEqual(report["on_transitions"][0]["preceding_lux"], expected)
        data = fixture()
        add(data, LIGHT, 0, state="off")
        add(data, LIGHT, 20, state="on")
        add(data, LUX, 0, value=5)
        self.assertIsNone(analyze(data)["on_transitions"][0]["preceding_lux"])

    def test_light_records_do_not_train_routine(self):
        data = fixture()
        add(data, PIR, 0, state="on")
        first = analyze(data)["routine_baseline"]
        add(data, LIGHT, 15, state="on", brightness=255)
        add(data, LIGHT, 30, state="off")
        self.assertEqual(first, analyze(data)["routine_baseline"])

    def test_chronological_test_excludes_future(self):
        bins = [{"date": f"2026-09-{day}", "hour": 9, "label": 0} for day in (28, 29, 30)]
        model = routine_model(bins)
        self.assertEqual(model["evaluation"]["test_bins"], 1)
        # Two earlier negatives -> prior 1/4; held-out negative -> squared error 1/16.
        self.assertEqual(model["evaluation"]["constant_brier"], 0.0625)
        bins[-1]["label"] = 1
        self.assertEqual(routine_model(bins)["evaluation"]["constant_brier"], 0.5625)

    def test_markdown_and_units(self):
        report = analyze(fixture())
        self.assertIn("1440", markdown(report))
        self.assertIn("duration_minutes", report["days"][0])
        self.assertNotIn("duration_seconds", report["days"][0])


class CollectorTests(unittest.TestCase):
    def test_pagination_advances_one_nanosecond(self):
        policy = Mock(config=SimpleNamespace(max_rows=2))
        starts = []
        count = 0
        def call(name, args):
            nonlocal count
            if args["entity_id"] != LIGHT:
                return {"rows": [], "truncated": False}
            starts.append(args["start"])
            count += 1
            if count == 1:
                return {"rows": [{"time": "2026-09-28T04:00:00.000000001Z"},
                                 {"time": "2026-09-28T04:00:00.000000002Z"}], "truncated": True}
            return {"rows": [{"time": "2026-09-28T04:00:00.000000003Z"}], "truncated": False}
        policy.call.side_effect = call
        sleeps = Mock()
        data = collect(policy, "2026-09-29", days=1, sleep=sleeps)
        self.assertEqual(starts[1], "2026-09-28T04:00:00.000000003Z")
        self.assertEqual(data["returned_rows"], 3)
        self.assertTrue(data["windows"][0]["entities"][LIGHT]["complete"])
        self.assertTrue(all(0 <= args.args[0] <= 5.1 for args in sleeps.call_args_list))

    def test_error_and_duplicate_timestamps_mark_incomplete(self):
        policy = Mock(config=SimpleNamespace(max_rows=2))
        policy.call.side_effect = SafeError("Upstream connection failed.")
        data = collect(policy, "2026-09-29", days=1, sleep=lambda _: None)
        self.assertTrue(all(not e["complete"] for e in data["windows"][0]["entities"].values()))
        policy.call.side_effect = None
        policy.call.return_value = {"rows": [{"time": "2026-09-28T04:00:00Z"}] * 2, "truncated": True}
        data = collect(policy, "2026-09-29", days=1, sleep=lambda _: None)
        self.assertTrue(all(not e["complete"] for e in data["windows"][0]["entities"].values()))

    def test_window_limits_and_dst(self):
        policy = Mock(config=SimpleNamespace(max_rows=2))
        with self.assertRaises(SafeError):
            collect(policy, "2026-09-29", days=8)
        with self.assertRaises(SafeError):
            collect(policy, "2026-11-02", days=1)
        policy.call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
