import unittest

from provenance import annotate_candidates, project_logbook


class ProvenanceTests(unittest.TestCase):
    def entry(self, **update):
        data = {"when": "2026-10-04T16:14:28.075126Z", "entity_id": "light.office_ceiling",
                "state": "off", "context_domain": "automation", "context_entity_id": "automation.lux_light_control_office"}
        data.update(update)
        return data

    def test_actor_classes_never_become_preferences(self):
        entries = [self.entry(), self.entry(context_domain="script", context_entity_id="script.something"),
                   self.entry(context_domain=None, context_entity_id=None),
                   self.entry(context_entity_id="automation.future_engine")]
        evidence = project_logbook(entries, engine_entities={"automation.future_engine"})
        self.assertEqual([e["actor"] for e in evidence], ["automation", "script", "unattributed", "engine"])
        self.assertTrue(all(not e["eligible_preference_label"] for e in evidence))

    def test_private_context_not_retained(self):
        evidence = project_logbook([self.entry(context_user_id="dummy-private-id", context_id="dummy-context", token="dummy-secret")])
        self.assertNotIn("dummy-private-id", str(evidence))
        self.assertNotIn("dummy-secret", str(evidence))

    def test_voice_user_context_is_not_a_correction_label(self):
        evidence = project_logbook([self.entry(context_domain="google_assistant", context_entity_id=None,
                                                context_user_id="dummy-private-id", context_event_type="google_assistant_command")])
        self.assertEqual(evidence[0]["actor"], "user_associated")
        self.assertFalse(evidence[0]["eligible_preference_label"])
        self.assertNotIn("dummy-private-id", str(evidence))

    def test_exact_join_only_and_ambiguity(self):
        candidate = {"start": "2026-10-04T12:14:28.075126-04:00", "minutes": 2.64}
        evidence = project_logbook([self.entry()])
        self.assertEqual(annotate_candidates([candidate], evidence)[0]["actor"], "automation")
        self.assertEqual(annotate_candidates([candidate], evidence * 2)[0]["actor"], "unattributed")
        evidence = project_logbook([self.entry(when="2026-10-04T16:14:28.075127Z")])
        self.assertEqual(annotate_candidates([candidate], evidence)[0]["actor"], "unattributed")
