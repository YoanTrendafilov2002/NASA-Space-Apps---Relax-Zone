"""Exercise preview timing and image handling without provider credentials."""
from io import BytesIO
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from explorer_catalogue import ExplorerCatalogue, nisar_observations
from explorer_preview_analysis import ExplorerPreviewAnalysis
from explorer_server import raster_routes
from nisar_tool.errors import ApiError


def provider_scene(day, *, track=128, has_hh=True):
    product = f"NISAR_L2_PR_GCOV_028_{track:03}_A_015_2005_DHDH_A_{day}T013517_{day}T013552_P05023_N_F_J_001"
    stamp = f"{day[:4]}-{day[4:6]}-{day[6:]}T01:35:17Z"
    return {
        "id": product, "product_type": "GCOV", "acquisition_start": stamp,
        "geometry": {"type": "Polygon", "coordinates": [[[55, 26], [56, 26], [56, 27], [55, 27], [55, 26]]]},
        "provider_metadata": {"sensor": "L-SAR", "pathNumber": track, "frameNumber": 15,
                              "flightDirection": "ASCENDING", "mainBandPolarization": ["HH", "HV"]},
        "previews": [{"label": f"{product}_NATIVE_A_{term}.png", "url": f"/api/products/{product}/previews/{index}"}
                     for index, term in enumerate(("HHHH", "HVHV") if has_hh else ("HVHV",))],
    }


class PreviewTimelineTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        data = ROOT / "data"
        self.catalogue = ExplorerCatalogue(self.client, data / "non-sentinel-nisar.json",
                                          (data / "nisar-holdouts.json",))
        self.analysis = ExplorerPreviewAnalysis(self.catalogue, raster_routes(self.catalogue))

    def test_saved_previews_survive_catalogue_failure(self):
        self.client.search.side_effect = ApiError("provider_unavailable", "Offline.", 502)
        report = self.analysis.run({"observation_id": "nisar-qeshm-20260810", "polarization": "HH", "days": 24})
        self.assertTrue(report["partial"])
        self.assertEqual(report["scope"], "visual_only")
        self.assertEqual([f["offset_days"] for f in report["frames"]], [-24, 0, 12])
        self.assertTrue(all(f["preview_kind"] == "prepared_crop" for f in report["frames"]))
        self.assertEqual(report["frames"][0]["appearance"]["status"], "similar")
        self.assertIn("similar", report["summary"])
        self.client.preview.assert_not_called()

    def test_live_images_use_pixels_and_skip_nonmatching_track_or_channel_preview(self):
        anchor = provider_scene("20260810")
        earlier = provider_scene("20260729")
        later = provider_scene("20260822", has_hh=False)
        wrong_track = provider_scene("20260811", track=127)
        self.catalogue._remember(list(nisar_observations(anchor)))
        self.client.search.return_value = {"count": 4, "items": [anchor, earlier, later, wrong_track]}

        def image_content(product_id, *, index):
            self.assertEqual(index, 0)
            pixels = 120 if product_id == anchor["id"] else 60
            with BytesIO() as buffer:
                Image.new("L", (64, 64), pixels).save(buffer, format="PNG")
                return {"content": buffer.getvalue()}

        self.client.preview.side_effect = image_content
        report = self.analysis.run({"observation_id": anchor["id"] + "__A", "polarization": "HH", "days": 12})
        self.assertEqual([f["offset_days"] for f in report["frames"]], [-12, 0, 12])
        self.assertIn("darker", report["frames"][0]["appearance"]["summary"])
        self.assertIsNone(report["frames"][-1]["preview_url"])
        self.assertIn("looks different", report["summary"])
        self.assertEqual(self.client.preview.call_count, 2)
        self.assertEqual(self.client.search.call_args.kwargs["track"], 128)
        self.assertEqual(self.client.search.call_args.kwargs["frame"], 15)

    def test_validation_rejects_invalid_windows_and_unknown_acquisitions(self):
        for invalid in (0, 91, True, 1.5):
            with self.subTest(days=invalid), self.assertRaisesRegex(ApiError, "whole number"):
                self.analysis.run({"observation_id": "nisar-qeshm-20260810", "polarization": "HH", "days": invalid})
        with self.assertRaisesRegex(ApiError, "Select an acquisition again"):
            self.analysis.run({"observation_id": "unknown", "polarization": "HH"})
        self.client.search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
