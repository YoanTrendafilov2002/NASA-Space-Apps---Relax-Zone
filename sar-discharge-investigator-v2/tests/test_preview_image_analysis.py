"""Exercise image-only comparisons, including real browse PNG transparency encoding."""

from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import sys
import unittest

try:
    from PIL import Image
except ImportError:
    Image = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from preview_image_analysis import compare_previews, describe_preview


@unittest.skipIf(Image is None, "Pillow is required for preview image tests")
class PreviewImageAnalysisTest(unittest.TestCase):
    def png(self, image, **options):
        with BytesIO() as buffer:
            image.save(buffer, format="PNG", **options)
            return buffer.getvalue()

    def test_ignores_png_transparency_while_preserving_opaque_black(self):
        image = Image.new("L", (96, 64), 255)
        image.paste(0, (0, 0, 32, 64))
        image.paste(145, (32, 0, 64, 64))
        features = describe_preview(self.png(image, transparency=255))
        self.assertAlmostEqual(features["opaque_fraction"], 2 / 3)
        self.assertEqual(features["tone"], (0, 0, 145))

    def test_describes_only_whole_frame_appearance(self):
        first = describe_preview(self.png(Image.new("L", (64, 64), 80)))
        darker = describe_preview(self.png(Image.new("L", (64, 64), 40)))
        self.assertEqual(compare_previews(first, first)["status"], "similar")
        appearance = compare_previews(first, darker)
        self.assertEqual(appearance["status"], "different")
        self.assertIn("preview looks darker", appearance["summary"])
        self.assertNotIn("dB", appearance["summary"])
        textured = Image.new("L", (64, 64), 80)
        textured.paste(140, (0, 0, 32, 64))
        appearance = compare_previews(first, describe_preview(self.png(textured)))
        self.assertEqual(appearance["status"], "different")
        self.assertIn("more contrast", appearance["summary"])

    def test_different_shape_or_visible_coverage_blocks_comparison(self):
        first = describe_preview(self.png(Image.new("L", (64, 64), 80)))
        wide = describe_preview(self.png(Image.new("L", (128, 64), 80)))
        half_frame = Image.new("RGBA", (64, 64), (80, 80, 80, 0))
        half_frame.paste((80, 80, 80, 255), (0, 0, 32, 64))
        masked = describe_preview(self.png(half_frame))
        for features in (wide, masked):
            with self.subTest(features=features):
                self.assertEqual(compare_previews(first, features)["status"], "not_comparable")

    def test_rejects_unsupported_malformed_and_oversized_data(self):
        data = self.png(Image.new("L", (64, 64), 80))
        self.assertIsNone(describe_preview(b"not an image"))
        self.assertIsNone(describe_preview(data[:-35]))
        with patch("preview_image_analysis.MAX_PREVIEW_BYTES", 12):
            self.assertIsNone(describe_preview(data))
        with patch("preview_image_analysis.MAX_PREVIEW_PIXELS", 1000):
            self.assertIsNone(describe_preview(data))


if __name__ == "__main__":
    unittest.main()
