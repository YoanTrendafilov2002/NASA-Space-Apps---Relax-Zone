"""Check optional measured-series and recognition asset publication."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from explorer_server import load_recognition_reviews, load_time_series, raster_routes


class ReviewManifestTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "manifest.json"
        self.catalogue = SimpleNamespace(data={"evidence": [{"id": "parent"}]}, seed={})
        self.dates = ["2026-01-01T03:00:00Z", "2026-01-13T03:00:00Z"]
        for name, date in zip(("scene-1", "scene-2"), self.dates):
            self.catalogue.seed[name] = {"id": name, "site_id": "site", "acquired_at": date,
                                         "channels": {"HH": {"image_url": "/sar/source.png", "science_url": "/science/source.tif"}}}

    def write(self, key, record):
        self.path.write_text(json.dumps({"schema_version": 1, key: [record], "evidence": [{"id": "review-source"}]}), encoding="utf-8")

    def test_review_manifest_matches_exact_recorded_channel(self):
        load_recognition_reviews(self.catalogue, self.path)
        self.assertNotIn("recognition_layers", self.catalogue.data)
        layer = {"id": "review", "site_id": "site", "observation_id": "scene-1", "polarization": "HH",
                 "title": "Research review", "summary": "Limit", "evidence_ids": ["parent", "review-source"],
                 "image_url": "/sar/review.png"}
        self.write("layers", layer | {"polarization": "HV"})
        with self.assertRaisesRegex(ValueError, "Unmatched recognition review"):
            load_recognition_reviews(self.catalogue, self.path)
        self.write("layers", layer)
        load_recognition_reviews(self.catalogue, self.path)
        self.assertEqual(self.catalogue.data["recognition_layers"], [layer])
        self.assertEqual([item["id"] for item in self.catalogue.data["evidence"]], ["parent", "review-source"])

    def test_recognition_route_checks_location_and_receipt(self):
        file = self.root / "data" / "science" / "review.tif"
        file.parent.mkdir(parents=True)
        content = b"review asset receipt"
        file.write_bytes(content)
        layer = {"id": "review", "science_url": "/science/review.tif",
                 "science_size_bytes": len(content), "science_sha256": sha256(content).hexdigest()}
        table = SimpleNamespace(data={"recognition_layers": [layer]}, seed={})
        with patch("explorer_server.APP", self.root):
            self.assertEqual(raster_routes(table)[layer["science_url"]], (file, "image/tiff", "review.tif"))
            layer["science_url"] = "/science/review.tif?other=1"
            with self.assertRaisesRegex(ValueError, "Invalid raster URL"):
                raster_routes(table)
            layer["science_url"] = "/science/review.tif"
            file.write_bytes(content.upper())
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                raster_routes(table)

    def test_series_uses_exact_dates_and_common_sample_count(self):
        load_time_series(self.catalogue, self.path)
        self.assertNotIn("time_series", self.catalogue.data)
        series = {"id": "m", "site_id": "site", "polarization": "HH", "title": "Measured series",
                  "summary": "Common grid", "statistic": "Median backscatter", "units": "dB", "evidence_ids": ["review-source"],
                  "points": [{"observation_id": key, "acquired_at": date, "value": value, "valid_pixels": 42}
                             for key, date, value in zip(self.catalogue.seed, self.dates, (-18.0, -17.4))]}
        series["points"][1]["valid_pixels"] = 40
        self.write("series", series)
        with self.assertRaisesRegex(ValueError, "different support sizes"):
            load_time_series(self.catalogue, self.path)
        series["points"][1]["valid_pixels"] = 42
        series["points"][1]["acquired_at"] = self.dates[0]
        self.write("series", series)
        with self.assertRaisesRegex(ValueError, "Mismatched time series sample"):
            load_time_series(self.catalogue, self.path)
        series["points"][1]["acquired_at"] = self.dates[1]
        self.write("series", series)
        load_time_series(self.catalogue, self.path)
        self.assertEqual(self.catalogue.data["time_series"], [series])
