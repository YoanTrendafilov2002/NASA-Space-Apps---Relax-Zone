"""Train an observed-return screen on Oman and test it on withheld NOAA sites.

Harmony's calibrated HH output measures the product's recorded backscatter.
The downloaded subsets lack per-pixel NESZ and the exception mask. This model
compares observed echoes. It does not infer net signal or classify oil.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform_geom
from scipy.ndimage import distance_transform_edt, gaussian_filter, label
from scipy.signal import fftconvolve
from shapely.geometry import box, shape

from oil import CELL_M, MIN_CONNECTED_CELLS, OMAN, sha256
from oil_recognition import CLIP_DB, FIT_QUANTILE, MIN_WEIGHTED_SUPPORT, SIGMA_M, THRESHOLD_STEP_DB, TRUNCATE


CONTEXT_M = 4000
COAST_BUFFER_M = 500
MIN_CONTROL_AREA_M2 = 500_000
NEAR_TIME_LIMIT_MINUTES = 60
NOAA = 'https://www.ospo.noaa.gov/data/ocean/marinepollution/2026/'


@dataclass(frozen=True)
class Trial:
    site: str
    stamp: str
    reference_ids: tuple[str, ...]
    offset_minutes: float
    map_asset: str


TRIALS = (
    Trial('gulf-eugene-nisar', '20260827T112649', ('20260827_1210_RADARSAT2_OIL',), 43 + 11 / 60,
          'nisar-eugene-20260827-hh.tif'),
    Trial('eloi-bay-nisar', '20261012T111043', ('20261012_1626_LANDSAT8_OIL', '20261012_1626b_LANDSAT8_OIL'),
          315 + 17 / 60, 'nisar-eloi-20261012-hh.tif'),
)


def contrast(power, water):
    """Screen observed HH power against two independently populated local flanks."""
    measured = water & np.isfinite(power) & (power > 0)
    controls = measured & (power < 10 ** (-7 / 10))
    half = math.ceil(CONTEXT_M / CELL_M)
    xx, yy = np.meshgrid(np.arange(-half, half + 1) * CELL_M, np.arange(-half, half + 1) * CELL_M)
    radius = np.hypot(xx, yy)
    ring = (radius > CONTEXT_M / 4) & (radius <= CONTEXT_M)
    populated = measured.copy()
    means = []
    for direction in (-1, 1):
        kernel = ring & (direction * xx >= CELL_M)
        count = np.rint(fftconvolve(controls, kernel, mode='same'))
        populated &= count * CELL_M**2 >= MIN_CONTROL_AREA_M2
        means.append(fftconvolve(np.where(controls, power, 0), kernel, mode='same') / np.maximum(count, 1))
    left, right = means
    positive = populated & (left > 0) & (right > 0)
    enough = np.zeros(positive.shape, dtype=bool)
    enough[positive] = np.abs(10 * np.log10(left[positive] / right[positive])) <= 2
    local = np.zeros(positive.shape, dtype='float32')
    local[enough] = np.clip(10 * np.log10(np.minimum(left, right)[enough] / power[enough]), -CLIP_DB, CLIP_DB)
    parameters = {'sigma': SIGMA_M / CELL_M, 'mode': 'constant', 'cval': 0.0, 'truncate': TRUNCATE}
    support = gaussian_filter(enough.astype('float32'), **parameters)
    retained = enough & (support >= MIN_WEIGHTED_SUPPORT)
    score = np.divide(gaussian_filter(local, **parameters), support,
                      out=np.full(power.shape, np.nan, dtype='float32'), where=retained)
    return measured, enough, score


def frozen_fit(data_root):
    path = data_root / OMAN['directory'] / 'screen_layers.npz'
    with np.load(path, allow_pickle=False) as source:
        _, _, score = contrast(source['power'], source['open_water'] & source['quality'])
    values = score[np.isfinite(score)]
    cutoff = float(np.quantile(values, FIT_QUANTILE))
    frozen = max(THRESHOLD_STEP_DB, round(math.ceil(cutoff / THRESHOLD_STEP_DB) * THRESHOLD_STEP_DB, 2))
    return {'threshold_db': frozen, 'unrounded_calibration_db': cutoff, 'calibration_cells': int(values.size),
            'quantile': FIT_QUANTILE, 'calibration_cache_sha256': sha256(path)}


def source_raster(downloads, stamp, term, required=True):
    paths = list(downloads.glob(f'NISAR_L2_PR_GCOV_*{stamp}*_frequencyA_{term}_subsetted.tif'))
    if not paths and not required:
        return None
    if len(paths) != 1:
        raise ValueError(f'Expected one Harmony {term} raster for {stamp}.')
    return paths[0]


def prepare(downloads, stamp, land):
    paths = {term: source_raster(downloads, stamp, term, required=term != 'numberOfLooks')
             for term in ('HHHH', 'mask', 'numberOfLooks')}
    with rasterio.open(paths['HHHH']) as data:
        native = data.read(1, masked=True).filled(np.nan)
        transform, epsg, crs = data.transform, data.crs.to_epsg(), data.crs
        spacing = data.res[0]
        factor = round(CELL_M / spacing)
        if data.res != (spacing, spacing) or factor * spacing != CELL_M or not crs.is_projected:
            raise ValueError('Expected north-up native NISAR postings divisible into 60 m cells.')
    valid = np.isfinite(native) & (native >= 0)
    for term, path in paths.items():
        if term == 'HHHH' or path is None:
            continue
        with rasterio.open(path) as source:
            if (source.crs, source.transform, source.shape) != (crs, transform, native.shape):
                raise ValueError(f'{term} is not on the original scientific grid.')
            array = source.read(1, masked=True)
            valid &= (~np.ma.getmaskarray(array)) & ((array == 1) if term == 'mask' else (array > 0)).filled(False)
    h, w = (count // factor for count in native.shape)

    def block(array):
        return array[:h * factor, :w * factor].reshape(h, factor, w, factor)

    supported = block(valid).all(axis=(1, 3))
    averaged = np.where(supported, block(native).mean(axis=(1, 3)), np.nan)
    target = transform * Affine.scale(factor)
    footprint = box(*array_bounds(h, w, target))
    extent = shape(transform_geom(crs, 'EPSG:4326', footprint.__geo_interface__)).buffer(.03)
    shore = [feature.intersection(extent) for feature in land if feature.intersects(extent)]
    if shore:
        ground = rasterize([transform_geom('EPSG:4326', crs, geom.__geo_interface__) for geom in shore if not geom.is_empty],
                           out_shape=supported.shape, transform=target, fill=0, dtype='uint8').astype(bool)
        water = distance_transform_edt(~ground) * CELL_M >= COAST_BUFFER_M if ground.any() else ~ground
    else:
        water = np.ones(supported.shape, dtype=bool)
    return averaged, supported, water, target, epsg, paths


def run_trial(trial, downloads, references, data_dir, land, threshold, out):
    power, available, water, transform, epsg, paths = prepare(downloads, trial.stamp, land)
    measured, flanks, score = contrast(power, water)
    hits = np.isfinite(score) & (score >= threshold)
    groups, _ = label(hits)
    flags = hits & (np.bincount(groups.ravel())[groups] >= MIN_CONNECTED_CELLS)
    reference_paths = [references / (identifier + '.geojson') for identifier in trial.reference_ids]
    polygons = [feature['geometry'] for path in reference_paths
                for feature in json.loads(path.read_text(encoding='utf-8'))['features']
                if feature['geometry']['type'] in ('Polygon', 'MultiPolygon')]
    if not polygons:
        raise ValueError('Missing independent NOAA polygon geometry.')
    reference = rasterize([transform_geom('EPSG:4326', f'EPSG:{epsg}', polygon) for polygon in polygons],
                          out_shape=power.shape, transform=transform, fill=0, dtype='uint8').astype(bool)
    if not reference.any():
        raise ValueError('Independent NOAA polygons miss the actual science grid.')
    agreement = flags & reference
    accepted = trial.offset_minutes <= NEAR_TIME_LIMIT_MINUTES
    tally = {
        'reference_cells': int(reference.sum()),
        'reference_valid_coverage': int((reference & available).sum()),
        'reference_measured_ocean_cells': int((reference & measured).sum()),
        'reference_bilateral_control_cells': int((reference & flanks).sum()),
        'reference_assessed_cells': int((reference & np.isfinite(score)).sum()),
        'reference_flagged_cells': int(agreement.sum()),
        'outside_reference_assessed_cells': int((~reference & np.isfinite(score)).sum()),
        'outside_reference_flags_unlabelled': int((~reference & flags).sum()),
    }
    asset = data_dir / 'science' / trial.map_asset
    map_cells = None
    if asset.is_file():
        with rasterio.open(asset) as viewer:
            if viewer.crs.to_epsg() != epsg:
                raise ValueError('The displayed holdout is in a different native CRS.')
            # Only whole science cells inside the selected map crop can be drawn.
            x = transform.c + (np.arange(power.shape[1]) + .5) * CELL_M
            y = transform.f - (np.arange(power.shape[0]) + .5) * CELL_M
            margin = CELL_M / 2
            b = viewer.bounds
            map_cells = ((x >= b.left + margin) & (x <= b.right - margin))[None, :] & (
                (y >= b.bottom + margin) & (y <= b.top - margin))[:, None]
    shown = agreement & map_cells if accepted and map_cells is not None else np.zeros(agreement.shape, dtype=bool)
    report = {'offset_minutes': trial.offset_minutes, 'near_time_agreement_eligible': accepted,
              'map_whole_cell_agreement': int(shown.sum()), 'counts': tally, 'epsg': epsg,
              'grid_transform': list(transform)[:6], 'shape': list(power.shape),
              'source_files': {term: {'filename': path.name, 'sha256': sha256(path)} for term, path in paths.items() if path},
              'reference_files': {key: {'url': NOAA + path.name, 'sha256': sha256(path)}
                                  for key, path in zip(trial.reference_ids, reference_paths)},
              'scope': 'Held-out measured return. NOAA polygons locate possible oil; the material and outside-polygon negatives are unverified.'}
    native = out / f'{trial.site}-observed-overlap.tif'
    with rasterio.open(native, 'w', driver='GTiff', width=power.shape[1], height=power.shape[0], count=1,
                       dtype='uint8', crs=f'EPSG:{epsg}', transform=transform, nodata=0, compress='deflate') as dst:
        dst.write(shown.astype(np.uint8), 1)
        dst.set_band_description(1, 'Observed HH contrast agreeing with the timed NOAA possible-oil reference')
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('downloads', 'data-root', 'references', 'data', 'out'):
        cli.add_argument('--' + name, required=True, type=Path)
    args = cli.parse_args()
    args.out.mkdir(exist_ok=True, parents=True)
    with (args.data / 'basemap-land.geojson').open(encoding='utf-8') as source:
        land = [shape(feature['geometry']) for feature in json.load(source)['features']]
    fit = frozen_fit(args.data_root)
    print('Frozen observed-only HH threshold', fit['threshold_db'], flush=True)
    trials = {trial.site: run_trial(trial, args.downloads, args.references, args.data, land, fit['threshold_db'], args.out)
              for trial in TRIALS}
    summary = {'schema_version': 1, 'calibrated': fit, 'held_out_sites': trials,
               'near_time_agreement_minutes': NEAR_TIME_LIMIT_MINUTES,
               'algorithm_sha256': sha256(Path(__file__)),
               'interpretation': 'Only measured NISAR HH power enters the prediction. This observed-return screen lacks pixel-level NESZ and cannot test oil material or estimate precision. Timing gates apply to published geographic agreement.'}
    (args.out / 'observed-return-results.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    for site, outcome in trials.items():
        print(site, outcome['counts'], 'map confirmed spatial agreement', outcome['map_whole_cell_agreement'])


if __name__ == '__main__':
    main()
