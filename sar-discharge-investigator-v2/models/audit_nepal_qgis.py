"""Audit the supplied Nepal QGIS pair without creating app imagery or labels."""

from __future__ import annotations

import argparse
from hashlib import file_digest
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.transform import Affine, from_bounds
from rasterio.warp import transform_bounds
from shapely.geometry import box, shape

from build_nisar_change_layers import display_grid


DATES = ('Aug19', 'Aug31')
POLS = ('HH', 'HV')
THRESHOLD_DB = 3.0
ASF = 'https://api.daac.asf.alaska.edu/services/search/param'
CMR = 'https://cmr.earthdata.nasa.gov/search/granules.umm_json'
GCOV = 'https://nisar-docs.asf.alaska.edu/gcov/'


def read_raster(path):
    with rasterio.open(path) as src, path.open('rb') as stream:
        if src.count != 1 or (src.scales, src.offsets) != ((1.0,), (0.0,)) or not src.crs.is_projected:
            raise ValueError(f'Unexpected scientific raster structure: {path.name}')
        band = src.tags(1)
        data = src.read(1)
        return data, {
            'file': path.name, 'sha256': file_digest(stream, 'sha256').hexdigest(), 'dtype': str(data.dtype),
            'shape': list(data.shape), 'epsg': src.crs.to_epsg(), 'affine': list(src.transform)[:6],
            'bounds_wgs84': list(transform_bounds(src.crs, 'EPSG:4326', *src.bounds)),
            'native_bounds': list(src.bounds), 'nodata': 'NaN' if np.isnan(src.nodata) else int(src.nodata),
            'mission_tag': src.tags().get('mission_name'), 'long_name': band.get('long_name'), 'units_tag': band.get('units'),
            'source_layer_statistics_as_tags': {key: band[key] for key in ('min_value', 'max_value', 'mean_value', 'sample_stddev') if key in band},
        }


def map_contract(data):
    height, width = data['shape']
    west, south, east, north = bounds = transform_bounds(data['epsg'], 3857, *data['native_bounds'])
    resolution = max((east - west) / width, (north - south) / height)
    wm_width = int(np.ceil((east - west) / resolution))
    wm_height = int(np.ceil((north - south) / resolution))
    affine = from_bounds(*bounds, wm_width, wm_height)
    w, s, e, n = transform_bounds(3857, 4326, *bounds)
    return {'image_epsg': 3857, 'image_shape': [wm_height, wm_width], 'image_transform': list(affine)[:6],
            'image_bounds': [[s, w], [n, e]]}


def audit_change(earlier, later, original, shared_mask, dimensions, web):
    paired = np.isfinite(earlier) & (earlier > 0) & np.isfinite(later) & (later > 0)
    valid = paired & shared_mask
    supplied = np.isfinite(original)
    if not valid.any() or not np.array_equal(valid, supplied) or not np.array_equal(paired, shared_mask):
        raise ValueError('Supplied change validity, common positive power and subswath mask should coincide')
    change = np.full(earlier.shape, np.nan, dtype=np.float64)
    change[valid] = 10 * np.log10(later[valid].astype(np.float64) / earlier[valid].astype(np.float64))
    error = float(np.max(np.abs(change[valid] - original[valid])))
    if error > 1e-5:
        raise ValueError('Supplied difference does not match the stated sign and Float32 precision')
    classes = np.select([change >= THRESHOLD_DB, change <= -THRESHOLD_DB], [1, 2], 0).astype(np.uint8)
    src_grid = dimensions['epsg'], dimensions['shape'], dimensions['affine']
    rendered, rendered_support = display_grid(classes, valid, src_grid, web)
    rows, cols = np.meshgrid(np.linspace(0, rendered.shape[0] - 1, 19, dtype=int),
                             np.linspace(0, rendered.shape[1] - 1, 23, dtype=int), indexing='ij')
    gx, gy = Affine(*web['image_transform']) * (cols.ravel() + .5, rows.ravel() + .5)
    x, y = Transformer.from_crs(3857, dimensions['epsg'], always_xy=True).transform(gx, gy)
    sx, sy = (~Affine(*dimensions['affine'])) * (x, y)
    nr, nc = np.floor(sy).astype(int), np.floor(sx).astype(int)
    inside = (nr >= 0) & (nc >= 0) & (nr < earlier.shape[0]) & (nc < earlier.shape[1])
    expected = np.zeros(len(nr), dtype=np.uint8)
    expected[inside] = classes[nr[inside], nc[inside]]
    if not np.array_equal(expected, rendered[rows, cols].ravel()):
        raise ValueError('Projected class samples disagree with independent transformed native centres')
    common = int(valid.sum())
    bright, dark = int((classes == 1).sum()), int((classes == 2).sum())
    return {'paired_pixels': common, 'paired_fraction': common / earlier.size,
            'max_abs_delta_error_db': error, 'reverse_sign_error_db': float(np.max(np.abs(change[valid] + original[valid]))),
            'change_quantiles_db_p10_p50_p90': np.quantile(change[valid], [.10, .50, .90]).tolist(),
            'brightened_ge_3db_cells': bright, 'darkened_le_minus_3db_cells': dark,
            'brightened_ge_3db_fraction': bright / common, 'darkened_le_minus_3db_fraction': dark / common,
            'map_grid_class_counts_brightened_darkened': [int((rendered == 1).sum()), int((rendered == 2).sum())],
            'map_grid_paired_pixels': int(rendered_support.sum()), 'independent_map_coordinate_checks': len(expected)}


def bounded_json(url):
    with urlopen(url, timeout=12) as response:
        body = response.read(200_001)
    if len(body) > 200_000:
        raise ValueError('Provider metadata response exceeded 200 KB')
    return json.loads(body)


def catalogue(bounds):
    w, s, e, n = bounds
    geometry = f'POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))'
    result = []
    for year in (2025, 2026):
        for day in (19, 31):
            date = f'{year}-08-{day}'
            url = ASF + '?' + urlencode({'platform': 'NISAR', 'processingLevel': 'GCOV', 'start': date + 'T00:00:00Z',
                                        'end': date + 'T23:59:59Z', 'intersectsWith': geometry, 'maxResults': 10, 'output': 'geojson'})
            versions = []
            for row in bounded_json(url)['features']:
                p = row['properties']
                ident = p['fileID']
                cmr_url = CMR + '?' + urlencode({'collection_concept_id': p['conceptID'], 'granule_ur': ident, 'page_size': 4})
                CMR_version = bounded_json(cmr_url)['items']
                versions.append({'granule_id_candidate': ident, 'acquired_at': p['startTime'], 'track': p['pathNumber'],
                                 'frame': p['frameNumber'], 'direction': p['flightDirection'], 'sensor': p['sensor'],
                                 'frequency_a_polarizations': p['mainBandPolarization'], 'footprint_contains_all_crop_bounds': shape(row['geometry']).covers(box(*bounds)),
                                 'collection_concept_id': p['conceptID'], 'source_hdf5_size_bytes': p['bytes'][ident + '.h5']['bytes'],
                                 'source_hdf5_url': p['url'], 'cmr_url': cmr_url,
                                 'cmr_granule_concept_ids': [entry['meta']['concept-id'] for entry in CMR_version if entry['umm']['GranuleUR'] == ident]})
            result.append({'date': date, 'asf_query': url, 'candidates': versions})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--downloads', required=True, type=Path)
    parser.add_argument('--out', type=Path, default=Path(__file__).resolve().parents[1] / 'results/recognition/method-qa/nepal.json')
    parser.add_argument('--catalogue', action='store_true', help='check public ASF and CMR metadata for product candidates')
    args = parser.parse_args()
    names = [f'{pol}_{epoch}' for pol in POLS for epoch in (*DATES, 'Change_dB')] + [f'Mask_{epoch}' for epoch in DATES]
    rasters = {name: read_raster(args.downloads / f'Nepal_{name}.tif') for name in names}
    first = rasters['HH_Aug19'][1]
    if any((row['epsg'], row['shape'], row['affine']) != (first['epsg'], first['shape'], first['affine']) for _, row in rasters.values()):
        raise ValueError('The QGIS comparison requires identical native grids')
    mask = [rasters[f'Mask_{date}'][0] for date in DATES]
    for item in mask:
        if item.dtype != np.uint8 or not np.isin(item, (0, 1, 255)).all():
            raise ValueError('Unexpected subswath mask values or storage')
    for pol in POLS:
        for epoch in DATES:
            array, row = rasters[f'{pol}_{epoch}']
            if array.dtype != np.float32 or row['units_tag'] != '1' or row['mission_tag'] != 'NISAR' or not row['long_name'].endswith(pol * 2):
                raise ValueError('Expected a tagged scientific NISAR GCOV linear covariance diagonal')
    shared = (mask[0] == 1) & (mask[1] == 1)
    web = map_contract(first)
    results = {pol: audit_change(*(rasters[f'{pol}_{epoch}'][0] for epoch in (*DATES, 'Change_dB')), shared, first, web) for pol in POLS}
    output = {'status': 'method_qa_only', 'app_ingest_ready': False, 'pair_labels': list(DATES),
              'method': 'Common finite positive power and valid subswath masks, 10 log10(Aug31/Aug19), then nearest native class to Web Mercator as in Curuai.',
              'native_grid': {key: first[key] for key in ('shape', 'epsg', 'affine', 'bounds_wgs84')},
              'sample_reprojection': web, 'input_evidence': {key: row for key, (_, row) in rasters.items()},
              'mask_code_counts': {date: {str(code): int((data == code).sum()) for code in (0, 1, 255)} for date, data in zip(DATES, mask)},
              'masked_common_pixels': int(shared.sum()), 'results': results, 'catalogue_references': catalogue(first['bounds_wgs84']) if args.catalogue else [],
              'primary_product_reference': GCOV,
              'limits': ['Supplied GeoTIFFs retain NISAR unitless covariance and mask layer tags. Original product IDs, acquisition years, and RTC/noise metadata are absent.',
                         'Matching official date and footprint candidates have multiple processing releases. The native input-exception and looks layers are unavailable in this crop set.',
                         'The date labels and ratio are adequate for a local arithmetic QA example. Independent event timing and extent evidence are unavailable.']}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    print(f'Verified {int(shared.sum()):,} native matched samples and 437 independently checked map positions per polarization.', flush=True)


if __name__ == '__main__':
    main()
