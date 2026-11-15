"""Selection regressions observed with the live Qwen model and catalogue."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from explorer_assistant import ExplorerAssistant
from explorer_catalogue import ExplorerCatalogue, SITE_MANIFESTS, nisar_observations
from explorer_server import raster_routes
from matrix_answers import MatrixAnswers
from matrix_intent import MatrixIntent
from nisar_tool.errors import ApiError


ROOT = Path(__file__).resolve().parents[1]


def command(topic, **values):
    return dict(topic=topic, site_id="", polarizations=[], mission="", start="", end="", period="all") | values


class EphemeralCatalogue(ExplorerCatalogue):
    def search(self, values):
        raise AssertionError(f"Recorded preset caused an unnecessary live search: {values}")


class ExplorerRegression(unittest.TestCase):
    def setUp(self):
        self.catalogue = EphemeralCatalogue(None, ROOT / "data" / "non-sentinel-nisar.json")

    def response(self, message, request, state=None):
        engine = ExplorerAssistant(self.catalogue, inference=lambda *args: request)
        return engine.respond({"message": message, "state": state or {}, "observations": []}, "test")

    def test_generic_wetlands_opens_picker_even_if_qwen_guesses_a_site(self):
        result = self.response("Show me wetlands", command("wetlands", site_id="curuai-nisar"))
        self.assertEqual(result["action"]["type"], "pick_site")

    def test_model_invented_dates_cannot_override_recorded_nisar_site(self):
        result = self.response("Show Curuai HH", command(
            "wetlands", site_id="curuai-nisar", polarizations=["HH"],
            start="2024-10-01", end="2024-10-31"), state={"site_id": "curuai-nisar"})
        action = result["action"]
        self.assertEqual(action["start"], "2026-08-13")
        self.assertEqual(action["polarizations"], ["HH"])
        self.assertEqual(set(action["selected_ids"]), {"nisar-curuai-20260813", "nisar-curuai-20261012"})

    def test_opening_observation_has_both_map_and_science_assets(self):
        selection = self.catalogue.data["default_selection"]
        obs = self.catalogue.seed[selection["observation_id"]]
        channel = obs["channels"][selection["polarization"]]
        routes = raster_routes(self.catalogue)
        self.assertEqual(channel["status"], "validated")
        self.assertEqual(routes[channel["image_url"]][1], "image/png")
        self.assertEqual(routes[channel["science_url"]][1], "image/tiff")

    def test_unavailable_channels_and_preview_context_remain_distinct(self):
        item = {"id": "NISAR_L2_TEST", "product_type": "GCOV", "geometry": None, "acquisition_start": "2026-10-21T01:35:16Z",
                "provider_metadata": {"sensor": "L-SAR", "mainBandPolarization": ["HH", "HV"]},
                "previews": [{"label": "scene_NATIVE_A_HHHH.png", "url": "/preview/example.png"}]}
        row = next(nisar_observations(item))
        self.assertEqual(row["channels"]["HH"]["status"], "preview")
        self.assertEqual(row["channels"]["HV"]["status"], "advertised")
        self.assertEqual(row["channels"]["VV"]["status"], "unavailable")
        self.assertNotIn("image_bounds", row["channels"]["HH"])

    def test_other_missions_are_rejected_before_any_provider_request(self):
        with self.assertRaisesRegex(ApiError, "NISAR"):
            ExplorerCatalogue(None, ROOT / "data" / "non-sentinel-nisar.json").search({"mission": "sentinel-1"})

    def test_show_wetlands_selects_rasters_and_explains_the_comparison(self):
        engine = self.matrix_engine(command("wetlands"))
        result = engine.respond({"message": "Show me wetlands"}, "test")
        self.assertEqual(result["action"]["site_id"], "curuai-nisar")
        self.assertEqual(result["action"]["type"], "select")
        self.assertIn("Solid orange outlines", result["message"])
        self.assertLess(len(result["message"].split()), 45)
        self.assertIn("nisar-curuai-20260813-source", result["evidence_ids"])

    def test_wetlands_prompt_overrides_the_opening_uae_scope_and_loads_the_review(self):
        engine = self.matrix_engine(None, without_ollama=True)
        engine.catalogue.data['recognition_layers'] = json.loads((ROOT / 'data' / 'nisar-detections.json').read_text(encoding='utf-8'))['layers']
        opening = engine.catalogue.data['default_selection']
        row = engine.catalogue.seed[opening['observation_id']]
        state = {'topic': 'wetlands', 'site_id': row['site_id'], 'channel': 'HH', 'polarizations': ['HH'],
                 'start': row['acquired_at'][:10], 'end': row['acquired_at'][:10]}
        result = engine.respond({'message': 'Highlight wetlands', 'state': state, 'observations': [row['id']]}, 'test')
        action = result['action']
        self.assertEqual(action['site_id'], 'curuai-nisar')
        self.assertEqual(action['selected_ids'], ['nisar-curuai-20261012', 'nisar-curuai-20260813'])
        self.assertEqual(action['recognition_layer_id'], 'nisar-curuai-water-agreement-20261012-hh')
        local = engine.respond({'message': 'Show wetlands here', 'state': state, 'observations': [row['id']]}, 'test')
        self.assertEqual(local['action']['site_id'], row['site_id'])

    def test_measured_comparison_answers_without_a_map_change(self):
        engine = self.matrix_engine(command("unknown", question="compare"))
        result = engine.respond({"message": "What can I compare at Curuai?"}, "test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertEqual(result["answer_model"], "nisar_matrix")
        self.assertIn("562,500", result["message"])

    def test_oil_opening_explains_the_limits_of_one_scene(self):
        engine = self.matrix_engine(command("oil_spill", polarizations=["VH"]))
        result = engine.respond({"message": "Show me oil"}, "test")
        self.assertEqual(result["action"]["site_id"], "uae-coastal-review")
        self.assertEqual(result["action"]["polarizations"], ["HH"])
        self.assertIn("Dashed red", result["message"])
        self.assertIn("mangroves", result["message"])
        self.assertIn("uae-falcon-eye-reference", result["evidence_ids"])

    def test_standalone_show_change_can_activate_a_verified_layer(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "Could you show what changed at Curuai in HV?"}, "test")
        self.assertEqual(result["action"]["change_layer_id"], "nisar-curuai-change-hv")
        self.assertEqual(result["action"]["selected_ids"], ["nisar-curuai-20261012", "nisar-curuai-20260813"])
        self.assertIn("Olive marks weaker return", result["message"])
        self.assertFalse(result["model_used"])

    def test_matched_station_context_is_scoped_to_the_regional_gauge(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "How did water levels change near Curuai?"}, "test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertIn("4.65 m", result["message"])
        self.assertIn("Curuai has its own lake level", result["message"])
        self.assertIn("ana-obidos-2026-10-12", result["evidence_ids"])

    def test_color_answer_tracks_the_active_change_layer(self):
        engine = self.matrix_engine(None, without_ollama=True)
        active = "nisar-curuai-20261012"
        request = {"message": "What do these map colors mean?", "observations": [active],
                   "state": {"site_id": "curuai-nisar", "active_id": active, "channel": "HV",
                             "change_layer_id": "nisar-curuai-change-hv"}}
        result = engine.respond(request, "test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertIn("Olive marks", result["message"])
        self.assertIn("nisar-curuai-20260813-source", result["evidence_ids"])

    def test_coastal_legend_distinguishes_review_points_from_measurements(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "Show me the map legend", "state": {"site_id": "uae-coastal-review"}}, "test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertIn("red diamond", result["message"])
        self.assertIn("orange circle", result["message"])
        self.assertIn("nisar-uae-coast-20261021-offshore", result["evidence_ids"])

    def test_maranon_change_scope_uses_measured_pair_and_inspection_context(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "Show what changed at Marañón in HH"}, "test")
        self.assertEqual(result["action"]["site_id"], "maranon-nisar")
        self.assertEqual(result["action"]["change_layer_id"], "nisar-maranon-change-hh")
        self.assertIn("same ground on two dates", result["message"])
        self.assertIn("maranon-oefa-pontoon-4", result["evidence_ids"])

    def test_unknown_place_never_selects_a_different_wetland(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "Show me wetlands near Greenland"}, "test")
        self.assertEqual(result["action"]["type"], "clarify")

    def test_cached_coastal_crop_cannot_replace_the_wetland_site_crop(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "Find HH", "state": {"site_id": "umm-al-quwain-nisar", "topic": "wetlands"},
                                 "observations": ["nisar-uae-coast-20261021"]}, "test")
        self.assertEqual(result["action"]["selected_ids"], ["nisar-uae-20261021"])

    def test_explicit_site_controls_topic_when_model_repeats_the_previous_topic(self):
        engine = self.matrix_engine(command("wetlands"))
        result = engine.respond({"message": "Show what changed at Marañón in HV",
                                 "state": {"site_id": "umm-al-quwain-nisar", "topic": "wetlands"}}, "test")
        self.assertEqual(result["action"]["site_id"], "maranon-nisar")
        self.assertEqual(result["action"]["topic"], "oil_spill")
        self.assertEqual(result["action"]["change_layer_id"], "nisar-maranon-change-hv")

    def test_terrain_shadow_is_explained_as_viewing_geometry(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "Show me why there is a dark band behind the ridge"}, "test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertEqual(result["answer_kind"], "terrain_shadow")
        self.assertIn("asf-radar-geometry", result["evidence_ids"])

    def test_oil_question_explains_the_ripple_mechanism(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "Show me how oil looks dark in NISAR radar",
                                 "state": {"site_id": "uae-coastal-review"}}, "test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertTrue(result["message"].startswith("Oil can flatten tiny ripples"))
        self.assertIn("currents", result["message"])
        self.assertIn("separate observation", result["message"])
        self.assertIn("emsa-slick-context", result["evidence_ids"])

    def test_flooding_under_stems_explains_the_return(self):
        engine = self.matrix_engine(None, without_ollama=True)
        result = engine.respond({"message": "Why can flooded wetlands look bright in HH?"}, "test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertIn("bounces between the water and the stems", result["message"])
        self.assertIn("field observation", result["message"])
        self.assertIn("wetland-field-study", result["evidence_ids"])

    def test_named_series_question_uses_measured_values_without_changing_map(self):
        engine = self.matrix_engine(None, without_ollama=True)
        engine.answers.time_series[("curuai-nisar", "HH")] = {
            "statistic": "Median backscatter", "evidence_ids": ["nisar-curuai-20260813-source", "nisar-curuai-20261012-source"],
            "points": [{"observation_id": "nisar-curuai-20260813", "acquired_at": "2026-08-13T09:21:54Z", "value": -18.5273},
                       {"observation_id": "nisar-curuai-20261012", "acquired_at": "2026-10-12T09:21:54Z", "value": -17.7226}],
        }
        result = engine.respond({"message": "How do I read the HH time series at Curuai?"}, "test")
        self.assertEqual(result["action"]["type"], "answer")
        self.assertEqual(result["answer_kind"], "time_series")
        self.assertIn("same usable ground pixels", result["message"])
        self.assertIn("-18.53 dB", result["message"])
        self.assertIn("-17.72 dB", result["message"])

    def test_generic_wetlands_starts_with_supported_water_agreement(self):
        engine = self.matrix_engine(None, without_ollama=True)
        engine.catalogue.data["recognition_layers"] = [{
            "id": "nisar-curuai-water-agreement-20261012-hh", "site_id": "curuai-nisar",
            "polarization": "HH", "observation_id": "nisar-curuai-20261012",
            "classes": [{"id": "water-agreement"}], "outlines": {"features": [{"properties": {"class_id": "water-agreement"}}]},
            "evidence_ids": ["curuai-october-optical-labels"],
        }]
        result = engine.respond({"message": "Show me wetlands"}, "test")
        self.assertEqual(result["action"]["selected_ids"], ["nisar-curuai-20261012", "nisar-curuai-20260813"])
        self.assertIn("image taken with light", result["message"])
        self.assertIn("curuai-october-optical-labels", result["evidence_ids"])

    def matrix_engine(self, prediction, without_ollama=False):
        data = ROOT / "data"
        main, *additional = (data / name for name in SITE_MANIFESTS)
        catalogue = EphemeralCatalogue(None, main, additional)
        catalogue.data["recognition_layers"] = json.loads((data / "nisar-detections.json").read_text(encoding="utf-8"))["layers"]
        layers = json.loads((data / "nisar-change-layers.json").read_text(encoding="utf-8"))["layers"]
        reference = json.loads((data / "nisar-reference-context.json").read_text(encoding="utf-8"))
        experiments = json.loads((data / "nisar-experiments.json").read_text(encoding="utf-8"))
        return ExplorerAssistant(catalogue, inference=lambda *args: prediction,
                                 answers=MatrixAnswers(catalogue, data / "nisar-measurements.json", change_layers=layers,
                                                       water_levels=reference["water_levels"], experiments=experiments["experiments"]),
                                 intent_model=MatrixIntent(data / "matrix-assistant-model.json"), without_ollama=without_ollama)


if __name__ == "__main__":
    unittest.main()
