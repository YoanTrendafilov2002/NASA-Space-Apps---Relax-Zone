"""Verify measured and withheld states against the packaged NISAR inputs."""

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
from rasterio.features import rasterize
from rasterio.io import MemoryFile
from rasterio.warp import transform_geom

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from runtime_recognition import RuntimeRecognition
from models.wetland_v2 import classify_flooded_vegetation


class RuntimeRecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recognition = RuntimeRecognition(APP)

    def test_curuai_open_water_regression_and_unresolved_edges(self):
        expected = (
            ('nisar-curuai-20260813', 47_798, 44_081, 2_914),
            ('nisar-curuai-20261012', 45_490, 34_010, 6_318),
        )
        for observation, previous, water, edge in expected:
            with self.subTest(observation=observation):
                result = self.recognition.run('curuai-nisar', observation, 'wetland')
                self.assertEqual(result['status'], 'measured')
                self.assertTrue(result['runtime_computed'])
                self.assertEqual(result['counts']['assessed_cells'], 62_250)
                self.assertEqual(result['counts']['open_water_cells'], water)
                self.assertEqual(result['counts']['r6_flagged_cells'], previous)
                self.assertEqual(result['counts']['uncertain_shore_cells'], edge)
                self.assertEqual(result['model']['vegetation_status'], 'withheld')
                self.assertEqual(result['model']['open_water_rule']['hh_db_max'], -17)
                self.assertFalse(result['source']['noise_correction_applied'])

    def test_prospective_hasik_raster_codes_and_uae_abstention(self):
        result = self.recognition.run('hasik-nisar', 'nisar-hasik-20260811', 'wetland')
        with MemoryFile(result['_artifacts']['science']) as archive, archive.open() as dataset:
            values = dataset.read(1)
            self.assertEqual(int((values == 1).sum()), 7_388)
            self.assertEqual(int((values == 4).sum()), 103)
            self.assertEqual(int((values == 3).sum()), 0)
        failed = self.recognition.run('uae-coastal-review', 'nisar-uae-coast-20261021', 'wetland')
        self.assertEqual(failed['reason'], 'no_assessable_cells')
        self.assertEqual(failed['counts']['open_water_unassessed_cells'], 22_500)
        self.assertNotIn('recognition_layer', failed)

    def test_vegetation_uses_preferential_gain_and_masks_open_water(self):
        shape = (8, 8)
        baseline = {name: np.full(shape, 0.0001 if 'noise' in name else 0.01, dtype=np.float32)
                    for name in ('hh_power', 'hv_power', 'hh_noise', 'hv_noise')}
        baseline.update(hh_quality=np.ones(shape, dtype=bool), hv_quality=np.ones(shape, dtype=bool))
        target = {name: array.copy() for name, array in baseline.items()}
        target['hh_power'][1:4, 1:4] *= 3
        target['hh_power'][5:8, 5:8] *= 3
        target['hv_power'][5:8, 5:8] *= 3
        open_water = np.zeros(shape, dtype=bool)
        open_water[2, 2] = True
        result = classify_flooded_vegetation(target, baseline, np.ones(shape, dtype=bool), open_water=open_water)
        self.assertEqual(int(result['flooded_vegetation'].sum()), 8)
        self.assertFalse(result['flooded_vegetation'][2, 2])
        self.assertFalse(result['flooded_vegetation'][5:, 5:].any())

    def test_observed_hh_geojson_preserves_candidate_tiff_cells(self):
        result = self.recognition.run('gulf-eugene-nisar', 'nisar-eugene-20260827', 'oil')
        self.assertEqual(result['counts']['flagged_cells'], 1_168)
        self.assertEqual(result['counts']['assessed_cells'], 54_264)
        self.assertIsNone(result['source']['noise_correction_applied'])
        self.assertEqual(result['model']['threshold_db'], 0.65)
        self.assertIn('Oil material is unconfirmed', result['summary'])
        layer = result['recognition_layer']
        self.assertEqual(layer['origin'], 'runtime_frozen_model')
        self.assertEqual(layer['classes'][0]['id'], 'dark-water-candidate')
        self.assertEqual(layer['classes'][0]['outline_style'], 'dashed')
        with MemoryFile(result['_artifacts']['science']) as archive, archive.open() as dataset:
            self.assertEqual(dataset.crs.to_epsg(), 32615)
            self.assertEqual(dataset.res, (60, 60))
            self.assertEqual(dataset.nodata, 0)
            values = dataset.read(1)
            geometry = [transform_geom('EPSG:4326', dataset.crs, feature['geometry'])
                        for feature in layer['outlines']['features']]
            recovered = rasterize(geometry, out_shape=dataset.shape, transform=dataset.transform, dtype='uint8')
            self.assertTrue(np.array_equal(recovered, values == 1))
            self.assertEqual(int((values > 0).sum()), result['counts']['assessed_cells'])

    def test_outlines_can_fall_back_to_georeferenced_png(self):
        with patch('runtime_recognition.MAX_OUTLINE_FEATURES', 1):
            result = self.recognition.run('curuai-nisar', 'nisar-curuai-20260813', 'wetland')
        layer = result['recognition_layer']
        self.assertNotIn('outlines', layer)
        (south, west), (north, east) = layer['image_bounds']
        self.assertLess(south, north)
        self.assertLess(west, east)
        self.assertTrue(result['_artifacts']['image'].startswith(b'\x89PNG\r\n\x1a\n'))

    def test_missing_noise_or_mismatched_selection_abstains(self):
        failed = self.recognition.run('gulf-eugene-nisar', 'nisar-eugene-20260827', 'wetland')
        self.assertEqual(failed['status'], 'unsupported')
        self.assertIn('spatial noise', failed['summary'])
        self.assertFalse(failed['runtime_computed'])
        self.assertNotIn('_artifacts', failed)
        failed = self.recognition.run('curuai-nisar', 'nisar-curuai-20261012', 'wetland', 'HV')
        self.assertEqual(failed['reason'], 'polarization')
        failed = self.recognition.run('another-site', 'nisar-curuai-20261012', 'wetland')
        self.assertEqual(failed['reason'], 'missing_eligible_inputs')


if __name__ == '__main__':
    unittest.main()
