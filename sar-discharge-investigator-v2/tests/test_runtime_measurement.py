"""Check common positive support, native outputs and blocked comparisons."""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest

try:
    import numpy as np
    from PIL import Image
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.transform import from_origin
except ImportError:
    np = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime_measurement import RuntimeMeasurement


@unittest.skipIf(np is None, 'NumPy, Rasterio and Pillow are required for measured raster tests')
class RuntimeMeasurementTest(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        (self.root/'science').mkdir()
        self.first = self.create_scene('2026-07-12', [[1, 1, 0], [np.nan, 100, 10]])
        self.second = self.create_scene('2026-07-24', [[10, .1, 5], [3, 1, 10]])
        self.third = self.create_scene('2026-08-05', [[3, np.nan, 2], [3, 1, 0]])

    def create_scene(self, date, powers, west=537000):
        name = 'nisar-test-'+date
        raster = np.asarray(powers, dtype=np.float32)
        transform = from_origin(west, 4717800, 20, 20)
        path = self.root/'science'/f'{name}.tif'
        with rasterio.open(path, 'w', driver='GTiff', count=1, width=3, height=2,
                           dtype='float32', crs='EPSG:32635', transform=transform, nodata=np.nan) as dataset:
            dataset.write(raster, 1)
        return {'id': name, 'evidence_id': name+'-evidence', 'site_id': 'test-wetland',
                'product': 'NISAR GCOV', 'sensor': 'L-SAR', 'product_id': 'NISAR_L2_'+name,
                'acquired_at': date+'T10:00:00Z', 'frequency': 'A', 'orbit': 65, 'frame': 67,
                'direction': 'DESCENDING', 'look_direction': 'Left', 'processing_version': '1.0.11',
                'noise_correction_applied': False,
                'channels': {'HH': {'status': 'validated', 'science_url': f'/science/{path.name}',
                                   'science_sha256': sha256(path.read_bytes()).hexdigest(),
                                   'science_units': 'unitless gamma0 power', 'science_shape': [2, 3],
                                   'science_transform': list(transform)[:6], 'science_epsg': 32635,
                                   'science_size_bytes': path.stat().st_size, 'valid_pixels': int(np.isfinite(raster).sum())}}}

    def test_signed_change_and_artifacts_use_common_positive_cells(self):
        measurements = RuntimeMeasurement(self.root, [self.first, self.second])
        result = measurements.change(self.first['id'], self.second['id'], 'HH')
        self.assertEqual(result['status'], 'measured')
        self.assertEqual(result['comparability']['status'], 'matched')
        comparison = result['comparison']
        self.assertEqual(comparison['paired_pixels'], 4)
        self.assertEqual(comparison['brighter_at_least_3db_pixels'], 1)
        self.assertEqual(comparison['darker_at_least_3db_pixels'], 2)
        self.assertAlmostEqual(comparison['median_change_db'], -5)
        with MemoryFile(result['_artifacts']['science']) as memory, memory.open() as dataset:
            self.assertEqual(dataset.crs.to_epsg(), 32635)
            self.assertEqual(dataset.units, ('dB',))
            expected = [[10, -10, np.nan], [np.nan, -20, 0]]
            np.testing.assert_allclose(dataset.read(1), expected, atol=1e-6, rtol=0)
        with Image.open(BytesIO(result['_artifacts']['image'])) as image:
            self.assertEqual(image.mode, 'RGBA')
            self.assertEqual(tuple(reversed(image.size)), tuple(result['change_layer']['image_shape']))
            self.assertEqual(
                set(map(tuple, np.asarray(image).reshape(-1, 4))),
                {(0, 0, 0, 0), (243, 130, 40, 255), (108, 104, 83, 255)},
            )
        self.assertEqual(measurements.change(self.second['id'], self.first['id'], 'HH')['reason'], 'invalid_date_order')

    def test_time_series_preserves_missing_channels_and_unknown_metadata(self):
        third = deepcopy(self.third)
        third['look_direction'] = None
        missing = deepcopy(self.first)
        missing['id'] = 'catalogue-only'
        missing['channels'] = {'HH': {'status': 'advertised'}}
        measurements = RuntimeMeasurement(self.root, [self.second, missing, third, self.first])
        result = measurements.time_series('test-wetland', 'HH')
        self.assertEqual(result['status'], 'measured')
        self.assertEqual(result['comparability']['unknown_fields'], ['look_direction'])
        self.assertEqual(result['common_pixels'], 2)
        self.assertEqual(result['excluded_observations'],
                         [{'observation_id': 'catalogue-only', 'reason': 'channel_unavailable'}])
        points = result['time_series']['points']
        self.assertEqual([p['observation_id'] for p in points], [r['id'] for r in (self.first, self.second, third)])
        np.testing.assert_allclose([p['value'] for p in points], [10, 5, 5*np.log10(3)], atol=1e-6)

    def test_measured_pair_rejects_wrong_geometry_and_unsafe_asset(self):
        wrong_grid = self.create_scene('2026-08-17', [[1]*3, [1]*3], west=537020)
        bad_asset = deepcopy(self.second)
        bad_asset['channels']['HH']['science_url'] = '/science/../nisar-test.tif'
        different_pass = deepcopy(self.third)
        different_pass['orbit'] += 1
        measurements = RuntimeMeasurement(self.root, [self.first, wrong_grid, bad_asset, different_pass])
        for scene, reason in ((wrong_grid, 'incompatible_grid'), (bad_asset, 'scientific_asset_unavailable'),
                              (different_pass, 'incompatible_geometry')):
            with self.subTest(reason=reason):
                result = measurements.change(self.first['id'], scene['id'], 'HH')
                self.assertEqual(result['status'], 'unavailable')
                self.assertEqual(result['reason'], reason)
                self.assertNotIn('_artifacts', result)


if __name__ == '__main__':
    unittest.main()
