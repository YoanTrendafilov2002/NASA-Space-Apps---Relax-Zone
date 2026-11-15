"""Check that synopsis answers describe the selected NISAR observation."""
from pathlib import Path
from unittest.mock import Mock
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from explorer_assistant import asks_for_dataset_synopsis, is_map_command
from explorer_catalogue import nisar_observations
from explorer_server import ExplorerServer


class DatasetSynopsisChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ExplorerServer(0, client=Mock(), without_ollama=True)
        cls.catalogue = cls.server.catalogue
        cls.addClassCleanup(cls.server.server_close)

    def respond(self, prompt, observation_id, site_id, *, other=()):
        result = self.server.assistant.respond({
            "message": prompt,
            "observations": [observation_id, *other],
            "state": {"active_id": observation_id, "site_id": site_id,
                      "channel": "HH", "topic": "oil_spill"},
        }, "synopsis-test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertEqual(result["answer_model"], "dataset_synopsis")
        self.assertFalse(result["model_used"])
        return result

    def test_answers_for_active_wetland_even_with_oil_topic_and_other_rows(self):
        response = self.respond("Is this oil or wetlands or both?", "nisar-curuai-20261012", "curuai-nisar",
                                other=("nisar-eugene-20260827",))
        self.assertIn("Curu", response["message"])
        self.assertIn("wetland", response["message"].lower())
        self.assertIn("HH and HV", response["message"])
        self.assertNotIn("Eugene", response["message"])
        self.assertIn("nisar-curuai-20261012-source", response["evidence_ids"])

    def test_oil_dataset_synopsis_identifies_site_and_measurement(self):
        response = self.respond("Summarize the current dataset", "nisar-eugene-20260827", "gulf-eugene-nisar")
        self.assertIn("Eugene", response["message"])
        self.assertIn("oil", response["message"].lower())
        self.assertIn("HH", response["message"])

    def test_exact_uae_crops_have_different_screening_scope(self):
        opening = self.respond("Give me a synopsis", "nisar-uae-20261021", "umm-al-quwain-nisar")["message"]
        review = self.respond("Give me a synopsis", "nisar-uae-coast-20261021", "uae-coastal-review")["message"]
        self.assertIn("Radar measurements", opening)
        self.assertIn("additional prepared inputs", opening)
        self.assertIn("no usable radar cells", review)
        self.assertIn("Separate sources provide the slick and mangrove outlines", review)

    def test_honors_preview_only_and_does_not_borrow_a_site_result(self):
        item = {"id": "NISAR_L2_TEST_SYNOPSIS", "product_type": "GCOV", "geometry": None,
                "acquisition_start": "2026-10-20T01:35:16Z",
                "provider_metadata": {"sensor": "L-SAR", "mainBandPolarization": ["HH", "HV"]},
                "previews": [{"label": "scene_NATIVE_A_HHHH.png", "url": "/preview/example.png"}]}
        observation = next(nisar_observations(item))
        self.catalogue._remember([observation])
        response = self.respond("What can this dataset check?", observation["id"], "curuai-nisar")
        self.assertIn("preview", response["message"].lower())
        self.assertNotIn("nisar-curuai-20261012-source", response["evidence_ids"])

    def test_stale_or_other_site_selection_requires_a_dataset(self):
        for active in ("unregistered-selection", "nisar-curuai-20261012"):
            with self.subTest(active=active):
                response = self.respond("Give me a synopsis", active, "gulf-eugene-nisar")
                self.assertIn("Select a dataset", response["message"])
                self.assertEqual(response["evidence_ids"], [])

    def test_keeps_map_commands_and_general_questions_in_existing_router(self):
        for text in ("show me wetlands", "highlight oil here", "how does radar detect oil",
                     "summarize the solid earth algorithm"):
            with self.subTest(text=text):
                self.assertFalse(asks_for_dataset_synopsis(text, is_map_command(text)))


if __name__ == "__main__":
    unittest.main()
