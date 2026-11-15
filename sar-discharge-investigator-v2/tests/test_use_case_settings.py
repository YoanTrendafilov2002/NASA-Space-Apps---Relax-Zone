"""Regression checks for source-linked settings and independent chat scope."""
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from explorer_assistant import ExplorerAssistant
from explorer_catalogue import ExplorerCatalogue
from explorer_settings import MissionReferences, UseCaseSettings


class UseCaseTests(unittest.TestCase):
    def setUp(self):
        self.guide = UseCaseSettings(APP / "data/nisar-use-case-settings.json")
        self.mission = MissionReferences(APP / "data/nisar-mission-atbd.json")
        self.catalogue = ExplorerCatalogue(None, APP / "data/non-sentinel-nisar.json")
        self.assistant = ExplorerAssistant(self.catalogue, without_ollama=True, settings_guide=self.guide,
                                           mission_references=self.mission)

    def ask(self, question, **state):
        return self.assistant.respond({"message": question, "state": state}, "test")

    def test_eight_profiles_with_abstention_about_local_capabilities(self):
        profiles = {profile["id"]: profile for profile in self.guide.public_profiles()}
        self.assertEqual(set(profiles), {"wetlands", "wetland_flood", "wetland_drought", "oil_spill",
                                         "coseismic", "transient", "secular", "sea_ice_motion"})
        for key in ("coseismic", "transient", "secular", "sea_ice_motion"):
            self.assertEqual(profiles[key]["availability"], "reference_only")
        self.assertEqual(profiles["wetlands"]["availability"], "in_app")

    def test_settings_followup_uses_selected_profile_without_changing_the_map_topic(self):
        reply = self.ask("How far apart should the acquisitions be?", settings_case_id="sea_ice_motion", topic="oil_spill")
        self.assertEqual(reply["action"], {"type": "answer", "topic": "oil_spill"})
        self.assertEqual(reply["answer_kind"], "timing")
        self.assertEqual(reply["settings_case_id"], "sea_ice_motion")
        self.assertIn("3–4 days", reply["message"])
        self.assertIn("1–2 days", reply["message"])

    def test_explicit_use_case_overrides_prior_settings_profile(self):
        reply = self.ask("Which product should I use for earthquake displacement?", settings_case_id="sea_ice_motion")
        self.assertEqual(reply["settings_case_id"], "coseismic")
        self.assertIn("GUNW", reply["message"])
        self.assertIn("asf-gunw-product", reply["evidence_ids"])

    def test_unknown_context_cannot_select_a_case(self):
        reply = self.ask("What settings should I use?", settings_case_id="../../not-a-case")
        self.assertEqual(reply["answer_kind"], "choose_use_case")
        self.assertNotIn("settings_case_id", reply)

    def test_reference_only_map_command_stays_in_chat(self):
        info = self.ask("Show me sea ice")
        self.assertEqual(info["action"]["type"], "answer")
        self.assertEqual(info["settings_case_id"], "sea_ice_motion")
        existing = self.ask("Show me wetlands")
        self.assertNotIn("settings_case_id", existing)
        self.assertNotEqual(existing.get("answer_model"), "use_case_settings")

    def test_atbd_conceptual_lookups_use_the_correct_document(self):
        for question, kind in (
            ("How does the Solid Earth ATBD handle coherence and atmospheric phase corrections?", "earth-quality"),
            ("How does the NISAR sea ice ATBD check velocities with drift buoys?", "ice-validation"),
            ("How does the sea ice ATBD use cross-correlation for feature tracking?", "ice-tracking"),
        ):
            with self.subTest(question=question):
                reply = self.ask(question)
                self.assertEqual(reply["answer_model"], "nisar_atbd")
                self.assertEqual(reply["answer_kind"], kind)
                self.assertEqual(reply["evidence_ids"], [f"nisar-mission-atbd-{kind}"])
                self.assertFalse(reply["model_used"])

    def test_qwen_can_select_only_the_retrieved_source(self):
        def choose(message, choices):
            self.assertIn("buoy", message)
            self.assertTrue(all(choice["id"].startswith("ice-") for choice in choices))
            return "unregistered"

        assistant = ExplorerAssistant(self.catalogue, settings_guide=self.guide, mission_references=self.mission,
                                      reference_inference=choose)
        reply = assistant.respond({"message": "How do GPS drift buoys validate sea ice velocity?"}, "test")
        self.assertEqual(reply["answer_kind"], "ice-validation")
        self.assertFalse(reply["model_used"])


if __name__ == "__main__":
    unittest.main()
