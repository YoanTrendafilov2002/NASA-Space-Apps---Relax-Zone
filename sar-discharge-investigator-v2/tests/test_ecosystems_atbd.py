"""Check bounded ATBD lookup without requiring a local Ollama process."""
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from explorer_assistant import ExplorerAssistant
from explorer_atbd import EcosystemsKnowledge
from explorer_catalogue import ExplorerCatalogue


class EcosystemsTests(unittest.TestCase):
    def setUp(self):
        self.knowledge = EcosystemsKnowledge(APP / "data/nisar-ecosystems-atbd.json")
        self.catalogue = ExplorerCatalogue(None, APP / "data/non-sentinel-nisar.json")

    def test_grounded_offline_answer(self):
        assistant = ExplorerAssistant(self.catalogue, ecosystems=self.knowledge, without_ollama=True)
        results = (
            ("How does the NISAR Ecosystems ATBD distinguish open water and flooded vegetation?", "water-vegetation"),
            ("What does the ATBD say about tidal shorelines and coherence?", "water-confounds"),
            ("What does the ATBD say about incidence-angle thresholds for open water?", "water-thresholds"),
            ("What does the ATBD say about estimating forest biomass?", "biomass"),
            ("How does the ATBD use cumulative sums for forest disturbance?", "disturbance"),
            ("What does the ATBD say about agriculture and coefficient of variation?", "agriculture"),
        )
        for question, kind in results:
            with self.subTest(question=question):
                reply = assistant.respond({"message": question}, "offline")
                self.assertEqual(reply["action"]["type"], "answer")
                self.assertEqual(reply["answer_kind"], kind)
                self.assertFalse(reply["model_used"])
                self.assertEqual(reply["message"], self.knowledge.entries[kind]["answer"])
                self.assertEqual(reply["evidence_ids"], [self.knowledge.source_id(kind)])

    def test_qwen_receives_page_summaries_and_cannot_write_the_result(self):
        calls = []

        def pick(question, choices):
            calls.append((question, choices))
            return choices[0]["id"]

        assistant = ExplorerAssistant(self.catalogue, ecosystems=self.knowledge, reference_inference=pick)
        reply = assistant.respond({"message": "ATBD method for open water, flooded vegetation and time series?"}, "qwen")
        self.assertTrue(reply["model_used"])
        self.assertGreater(len(calls[0][1][0]["summary"]), 30)
        self.assertIn("§", calls[0][1][0]["section"])
        self.assertEqual(reply["message"], self.knowledge.entries[reply["answer_kind"]]["answer"])

    def test_unknown_selection_falls_back_to_the_best_verified_passage(self):
        assistant = ExplorerAssistant(self.catalogue, ecosystems=self.knowledge,
                                      reference_inference=lambda *_: "unregistered")
        reply = assistant.respond({"message": "What is the NISAR ATBD flood baseline?"}, "fallback")
        self.assertEqual(reply["answer_model"], "ecosystems_atbd")
        self.assertFalse(reply["model_used"])
        self.assertEqual(reply["answer_kind"], "water-time")

    def test_source_summaries_skip_map_commands_and_app_algorithms(self):
        assistant = ExplorerAssistant(self.catalogue, ecosystems=self.knowledge, without_ollama=True)
        command = assistant.respond({"message": "Show me NISAR wetlands"}, "map")
        self.assertNotEqual(command.get("answer_model"), "ecosystems_atbd")
        self.assertEqual(self.knowledge.retrieve("how does this app show nisar wetlands"), [])


if __name__ == "__main__":
    unittest.main()
