import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from office_collect import collect
from office_report import analyze, markdown
from passive_observer import MAX_CONTEXTS, Observer
from policy import Config, SafeError
from rooms import LIVING_ROOM, OFFICE


def event(kind="state_changed", entity="light.living_room_lamp", stamp="2026-10-01T00:00:00Z", context=None, state="on", attrs=None):
    ctx = context or {}
    data = {"entity_id": entity}
    if kind == "state_changed":
        data["new_state"] = {"state": state, "attributes": attrs or {}, "context": ctx}
    return {"event_type": kind, "time_fired": stamp, "context": ctx, "data": data}


class RoomTests(unittest.TestCase):
    def test_profile_allowlist_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        config = Config(json.loads((root / "config.living-room.example.json").read_text()))
        self.assertEqual(config.entities, LIVING_ROOM.mappings)
        self.assertNotIn(OFFICE.pir, config.entities)

    def test_living_room_collection_and_analysis(self):
        policy = Mock(config=SimpleNamespace(max_rows=500))
        policy.call.return_value = {"rows": [], "truncated": False}
        data = collect(policy, "2026-10-02", days=1, profile=LIVING_ROOM, sleep=lambda _: None)
        report = analyze(data, profile=LIVING_ROOM)
        self.assertEqual(report["room"], "Living Room")
        self.assertEqual(report["media"]["entity_id"], "media_player.living_room_tv")
        self.assertIn("Living Room observation report", markdown(report))
        self.assertNotIn(OFFICE.pir, report["collection"]["entities"])
        with self.assertRaises(ValueError):
            analyze(data)

    def test_media_not_an_occupancy_or_preference_label(self):
        policy = Mock(config=SimpleNamespace(max_rows=500))
        policy.call.return_value = {"rows": [], "truncated": False}
        data = collect(policy, "2026-10-02", days=1, profile=LIVING_ROOM, sleep=lambda _: None)
        data["windows"][0]["entities"][LIVING_ROOM.media]["rows"] = [{"time": "2026-10-01T04:00:00Z", "state": "playing"}]
        report = analyze(data, profile=LIVING_ROOM)
        self.assertEqual(report["routine_baseline"]["training_bins"], 0)
        self.assertEqual(report["media"]["state_record_counts"], {"playing": 1})

    def test_other_light_media_and_derived_disagreement_are_context(self):
        policy = Mock(config=SimpleNamespace(max_rows=500))
        policy.call.return_value = {"rows": [], "truncated": False}
        data = collect(policy, "2026-10-02", days=1, profile=LIVING_ROOM, sleep=lambda _: None)
        day = data["windows"][0]
        for entity, value in [(LIVING_ROOM.pir, "on"), (LIVING_ROOM.mmwave, "off"),
                              (LIVING_ROOM.primary_light, "off"), (LIVING_ROOM.extra_light, "on"),
                              (LIVING_ROOM.derived, "off"), (LIVING_ROOM.media, "playing")]:
            day["entities"][entity]["rows"] = [{"time": "2026-10-01T04:00:00Z", "state": value}]
        report = analyze(data, profile=LIVING_ROOM)
        self.assertEqual(report["totals"]["derived_off_raw_active_minutes"], 120)
        self.assertEqual(report["totals"]["primary_off_extra_light_on_minutes"], 120)
        self.assertEqual(report["totals"]["sensor_active_media_active_minutes"], 120)
        self.assertIn("other light reports on", report["review_candidates"][0]["context_flags"])
        self.assertIn("media reports active", report["review_candidates"][0]["context_flags"])
        self.assertEqual(report["routine_baseline"]["preference_labels"], 0)


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.observer = Observer([OFFICE, LIVING_ROOM], engine_entities={"automation.future_learning_engine"})

    def test_automation_parent_link_and_private_projection(self):
        self.observer.process(event(kind="automation_triggered", entity="automation.lamp_control", context={"id": "ctx1"}))
        row = self.observer.process(event(context={"id": "ctx2", "parent_id": "ctx1", "user_id": "private-user"},
                                          attrs={"brightness": 200, "password": "dummy-secret", "media_content_id": "private-url"}))
        self.assertEqual(row["actor"], "automation")
        self.assertEqual(row["attributes"], {"brightness": 200})
        self.assertFalse(row["eligible_preference_label"])
        self.assertNotIn("private-user", json.dumps(row))
        self.assertNotIn("dummy-secret", json.dumps(row))
        self.assertNotIn("ctx1", json.dumps(row))

    def test_engine_script_and_unattributed_never_preferences(self):
        for kind, source, actor in [("automation_triggered", "automation.future_learning_engine", "engine"),
                                    ("script_started", "script.movie_time", "script")]:
            self.observer.process(event(kind=kind, entity=source, context={"id": "known"}))
            row = self.observer.process(event(context={"id": "known"}))
            self.assertEqual(row["actor"], actor)
            self.assertFalse(row["eligible_preference_label"])
        row = self.observer.process(event(context={"user_id": "private"}))
        self.assertEqual(row["actor"], "user_associated")
        self.assertFalse(row["eligible_preference_label"])
        self.assertEqual(self.observer.process(event())["actor"], "unattributed")

    def test_context_expiry_and_out_of_order(self):
        self.observer.process(event(kind="automation_triggered", entity="automation.lamp_control", context={"id": "ctx"}))
        row = self.observer.process(event(stamp="2026-10-01T00:03:00Z", context={"id": "ctx"}))
        self.assertEqual(row["actor"], "unattributed")
        self.assertIsNone(self.observer.process(event(stamp="2026-10-01T00:02:00Z")))
        self.assertEqual(self.observer.counters["out_of_order"], 1)

    def test_disconnect_invalidates_all_rooms_and_context(self):
        self.observer.process(event(kind="automation_triggered", entity="automation.lamp_control", context={"id": "ctx"}))
        rows = self.observer.reset("2026-10-01T00:00:01Z")
        self.assertEqual(len(rows), len(OFFICE.entities) + len(LIVING_ROOM.entities))
        self.assertTrue(all(r["state"] == "unknown" and not r["eligible_preference_label"] for r in rows))
        self.assertEqual(len(self.observer.contexts), 0)
        row = self.observer.process(event(stamp="2026-10-01T00:00:02Z", context={"id": "ctx"}))
        self.assertEqual(row["actor"], "unattributed")
        with self.assertRaises(SafeError):
            self.observer.reset("2026-09-30T00:00:00Z")

    def test_engine_descendant_script_is_not_relabelled(self):
        self.observer.process(event(kind="automation_triggered", entity="automation.future_learning_engine", context={"id": "engine"}))
        self.observer.process(event(kind="script_started", entity="script.delegated", context={"id": "child", "parent_id": "engine"}))
        row = self.observer.process(event(context={"id": "action", "parent_id": "child"}))
        self.assertEqual(row["actor"], "engine")
        self.assertFalse(row["eligible_preference_label"])

    def test_bounded_context_cache(self):
        for index in range(MAX_CONTEXTS + 10):
            self.observer.process(event(kind="automation_triggered", entity="automation.lamp_control", context={"id": str(index)}))
        self.assertEqual(len(self.observer.contexts), MAX_CONTEXTS)

    def test_availability_and_removal(self):
        for value in ["unknown", "unavailable"]:
            self.assertEqual(self.observer.process(event(state=value))["availability"], value)
        removed = event()
        removed["data"]["new_state"] = None
        self.assertEqual(self.observer.process(removed)["availability"], "unavailable")
        self.assertEqual(self.observer.process(event())["availability"], "reported")

    def test_numeric_and_attribute_bounds(self):
        for value in ["NaN", "inf", "-1", "100000001", "secret-content"]:
            row = self.observer.process(event(entity=LIVING_ROOM.lux, state=value))
            self.assertIsNone(row["value"])
            self.assertEqual(row["state"], "other")
        self.assertEqual(self.observer.process(event(entity=LIVING_ROOM.lux, state="120"))["value"], 120)
        row = self.observer.process(event(attrs={"brightness": True, "color_temp_kelvin": 10**1000, "color_temp": 250}))
        self.assertEqual(row["attributes"], {"color_temp": 250})

    def test_nonallowlisted_entities_and_media_titles_ignored(self):
        self.assertIsNone(self.observer.process(event(entity="person.somebody")))
        row = self.observer.process(event(entity=LIVING_ROOM.media, state="playing", attrs={"media_title": "private-title"}))
        self.assertEqual(row["state"], "playing")
        self.assertNotIn("private-title", json.dumps(row))
