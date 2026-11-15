"""Build matched NISAR HH/HV views from the saved Saramurillo acquisitions."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import rasterio
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform, transform_bounds, transform_geom
from shapely.geometry import Point, box, shape

from build_nisar_map_assets import RANGES, WGS84, digest, make_assets, read_window


SITE = 'maranon-nisar'
EPSG = 32718
CRS = f'EPSG:{EPSG}'
POINT = (-74.9198, -4.7267)
RADIUS_M = 4500
COLLECTION = 'NISAR_L2_GCOV_PROVISIONAL_V1'
COLLECTION_ID = 'C2854338529-ASF'
OEFA = 'https://www3.congreso.gob.pe/Docs/comisiones2025/transportes/files/oefa_ppt_22_05_2026.pdf#page=10'
INCIDENT = 'maranon-oefa-pontoon-4'
PRODUCTS = (
    {
        'id': 'nisar-maranon-20260224',
        'product': 'NISAR_L2_PR_GCOV_013_147_A_175_2005_DHDH_A_20260224T104407_20260224T104442_P05023_N_F_J_001',
        'concept': 'G4275155676-ASF',
        'acquired_at': '2026-02-24T10:44:07Z',
        'bytes': 1908408320,
        'sha256': '213d494b56d6902de0c46cc42bd0a483886567a70edc8b7c5f954495aa530277',
        'footprint': [[-73.74224, -5.96611], [-74.20418, -3.81847], [-76.39507, -4.28631],
                      [-75.9446, -6.42782], [-73.74224, -5.96611]],
    },
    {
        'id': 'nisar-maranon-20260308',
        'product': 'NISAR_L2_PR_GCOV_014_147_A_175_2005_DHDH_A_20260308T104408_20260308T104442_P05023_N_F_J_001',
        'concept': 'G4275159850-ASF',
        'acquired_at': '2026-03-08T10:44:08Z',
        'bytes': 1862270976,
        'sha256': 'f9ba8313e52c645be74019d631e4956714c50cfa8f01c62dbf8537f03a6cf769',
        'footprint': [[-73.75062, -5.92076], [-74.20025, -3.83289], [-76.39128, -4.30072],
                      [-75.95298, -6.38267], [-73.75062, -5.92076]],
    },
)


def archived_review(directory):
    summary = directory / 'saramurillo_calibrated_3db' / 'screen_summary.json'
    inspection = directory / 'oefa_saramurillo_inspection_area.geojson'
    sensitivity = directory / 'NISAR_Maranon_sensitivity.csv'
    coverage = directory / 'NISAR_Maranon_inspection_coverage.csv'
    record = json.loads(summary.read_text(encoding='utf-8'))
    if ([record.get('before'), record.get('after')] != [p['product'] for p in PRODUCTS]
            or record['point'] != list(POINT) or record['epsg'] != EPSG
            or record['posting_m'] != [20.0, 20.0]
            or record['noise_correction_in_source_hdf5'] != ['false', 'false']):
        raise ValueError('Archived QGIS screen does not identify the expected pair and point')
    feature = json.loads(inspection.read_text(encoding='utf-8'))['features'][0]
    if (feature['properties']['report_url'] != OEFA
            or feature['properties']['event_date_local'] != '2026-03-07'
            or not shape(feature['geometry']).covers(Point(*POINT))):
        raise ValueError('The QGIS field marker falls outside the archived inspection outline')
    with sensitivity.open(newline='', encoding='utf-8') as stream:
        screen = next(row for row in csv.DictReader(stream)
                      if tuple(float(row[k]) for k in ('analysis_cell_m', 'bank_buffer_m', 'radius_m', 'cutoff_db'))
                      == (60, 40, 1000, 3))
    with coverage.open(newline='', encoding='utf-8') as stream:
        areas = next(row for row in csv.DictReader(stream)
                     if tuple(int(row[k]) for k in ('analysis_cell_m', 'bank_buffer_m', 'inspection_area_buffer_m'))
                     == (60, 40, 0))
    return {
        'analysis_cell_m': 60, 'bank_buffer_m': 40, 'review_radius_m': 1000, 'darkening_threshold_db': 3,
        'assessable_river_cells_within_radius': int(screen['assessable_cells']),
        'connected_candidate_patches_within_radius': int(screen['connected_patches']),
        'assessable_area_in_digitized_inspection_footprint_m2': float(areas['assessable_overlap_m2']),
        'source_sha256': {path.name: digest(path) for path in (summary, inspection, sensitivity, coverage)},
        'scope': 'Archived noise-gated QGIS screening context only. The later inspection outline is a manually georeferenced proxy. It has no SAR-overpass material labels.',
    }


def crop_source(downloads, product, center):
    source = downloads / f"{product['product']}.h5"
    if source.stat().st_size != product['bytes'] or digest(source) != product['sha256']:
        raise ValueError(f"Original ASF HDF5 size or pinned digest differs for {product['id']}")
    arrays, affine, meta = read_window(source, product['product'], EPSG, center)
    signature = ('acquired_at', 'orbit', 'frame', 'direction', 'look_direction', 'processing_version',
                 'product_specification_version', 'noise_correction_applied')
    if tuple(meta[k] for k in signature) != (product['acquired_at'], 147, 175, 'ASCENDING', 'Left', '1.0.11', '1.5.0', False):
        raise ValueError('Embedded NISAR acquisition and processing metadata differ from the pinned pair')
    width, height = meta['source_window'][2:]
    x = affine.c + affine.a * (np.arange(width) + 0.5)
    y = affine.f + affine.e * (np.arange(height) + 0.5)
    cols = np.flatnonzero(np.abs(x - center[0]) <= RADIUS_M)
    rows = np.flatnonzero(np.abs(y - center[1]) <= RADIUS_M)
    if cols.size != 450 or rows.size != 450:
        raise ValueError('Expected the archived 9 km QGIS review window on the native 20 m grid')
    crop = np.s_[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    arrays = {pol: layer[crop] for pol, layer in arrays.items()}
    affine *= Affine.translation(int(cols[0]), int(rows[0]))
    col, row, _, _ = meta['source_window']
    meta['source_window'] = [col + int(cols[0]), row + int(rows[0]), 450, 450]
    site_col, site_row = (~affine) * center
    for pol, values in arrays.items():
        if not np.isfinite(values[int(site_row), int(site_col)]) or not np.any(np.isfinite(values)):
            raise ValueError(f'No measured {pol} power at the inspection marker')
    return source, arrays, affine, meta


def prepare(data, downloads, review_dir):
    review = archived_review(review_dir)
    center = tuple(values[0] for values in transform(WGS84, CRS, [POINT[0]], [POINT[1]]))
    extracted = [crop_source(downloads, product, center) for product in PRODUCTS]
    if extracted[0][2] != extracted[1][2] or extracted[0][3]['source_window'] != extracted[1][3]['source_window']:
        raise ValueError('The two Marañón crops do not have identical native grids')
    bounds = array_bounds(450, 450, extracted[0][2])
    geometry = transform_geom(CRS, WGS84, box(*bounds).__geo_interface__)
    support = {
        pol: int((np.isfinite(extracted[0][1][pol]) & (extracted[0][1][pol] > 0)
                  & np.isfinite(extracted[1][1][pol]) & (extracted[1][1][pol] > 0)).sum())
        for pol in RANGES
    }
    if not all(support.values()):
        raise ValueError('No common positive measured samples across the pair')
    review_points = [{
        'id': 'maranon-pontoon-4', 'topic': 'oil_spill', 'label': 'Station 1 · Pontoon 4 inspection area',
        'lon': POINT[0], 'lat': POINT[1], 'evidence_id': INCIDENT, 'kind': 'approximate_inspection_location',
        'notes': 'Approximate QGIS marker within the digitized OEFA inspection illustration. The field record independently establishes the incident. Radar records the backscatter around it.',
    }]
    site = {
        'id': SITE, 'name': 'Marañón River at Saramurillo, Peru', 'topic': 'oil_spill',
        'aliases': ['Marañón', 'Marañón River', 'Maranon River', 'Saramurillo', 'Pontoon 4'],
        'bbox': list(transform_bounds(CRS, WGS84, *bounds, densify_pts=21)),
        'start': '2026-02-24', 'end': '2026-03-08',
        'geometry': geometry, 'geometry_kind': 'study_area', 'review_points': review_points,
        'geometry_note': 'The 9 km QGIS review window is centered on the approximate Pontoon 4 inspection marker.',
        'evidence_id': INCIDENT,
    }
    evidence = [{
        'id': INCIDENT, 'title': 'OEFA field investigation · Saramurillo, March 2026',
        'kind': 'regulatory_field_inspection', 'url': OEFA, 'incident_date_local': '2026-03-07',
        'notes': 'OEFA reported a spill near Station 1 Pontoon 4 on March 7 and field supervision March 8–12. The report describes roughly 2,757 m² of impacted water. The archived 60 m, 40 m bank-buffer HH screen had zero assessable area within the digitized inspection footprint. A same-grid radar pair cannot determine the presence, absence or extent of oil at overpass.',
        'archived_qgis_review': review,
        'corroborating_url': 'https://www.gob.pe/institucion/mpfn/noticias/1363623-fema-dirige-inspeccion-en-loreto-tras-derrame-de-petroleo-en-el-rio-maranon',
    }]
    observations = []
    data.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=data) as staging:
        staged = Path(staging)
        for folder in ('sar', 'science'):
            (staged / folder).mkdir()
        for product, (source, arrays, affine, meta) in zip(PRODUCTS, extracted):
            product_id = product['product']
            source_geometry = {'type': 'Polygon', 'coordinates': [product['footprint']]}
            if not shape(source_geometry).covers(shape(geometry)):
                raise ValueError('The review window extends outside an official ASF footprint')
            source_id = product['id'] + '-source'
            url = f"https://cmr.earthdata.nasa.gov/search/granules.umm_json?concept_id={product['concept']}"
            channels = {}
            for pol, values in arrays.items():
                channel = make_assets(staged, product['id'], product_id, EPSG, affine, pol, values, meta)
                with rasterio.open(staged / channel['science_url'].lstrip('/')) as tif:
                    if not np.array_equal(tif.read_masks(1) != 0, np.isfinite(values)):
                        raise ValueError('Published GeoTIFF validity differs from the retained product mask')
                channels[pol] = dict(channel, scientific=True, evidence_id=source_id)
            observations.append({
                'id': product['id'], 'site_id': SITE, 'product_id': product_id, 'acquisition_id': product_id,
                'mission': 'nisar', 'provider': 'NASA ASF DAAC', 'product': 'NISAR GCOV', 'product_type': 'GCOV',
                'sensor': 'L-SAR', 'wavelength': 'L', 'frequency': 'A', 'mode': 'Frequency A · dual polarization',
                'collection': COLLECTION, 'collection_concept_id': COLLECTION_ID, 'granule_concept_id': product['concept'],
                'maturity': 'provisional', 'geometry': geometry, 'source_geometry': source_geometry,
                'geometry_kind': 'local_coverage_window', 'channels': channels, 'evidence_id': source_id,
                'source_url': url, 'source_requires_earthdata': True, 'science_requires_earthdata': False,
                **meta,
            })
            evidence.append({
                'id': source_id, 'title': f"NASA ASF NISAR HH/HV · {product['acquired_at'][:10]}",
                'kind': 'downloaded_scientific_hdf5', 'url': url, 'source_id': product_id,
                'source_filename': source.name, 'source_size_bytes': product['bytes'], 'provider_size_bytes': product['bytes'],
                'source_sha256': product['sha256'],
                'provider_asset_url': f'https://nisar.asf.earthdatacloud.nasa.gov/NISAR/{COLLECTION}/{product_id}/{source.name}',
                'documentation_url': 'https://nisar-docs.asf.alaska.edu/gcov/',
                'notes': 'The same native grid, acquisition geometry and product processing are shared across both observations. The hosted crop preserves measured HH and HV gamma0, including the source noise convention.',
            })
        manifest = {
            'schema_version': 1, 'sites': [site], 'observations': observations, 'evidence': evidence,
            'pair_audit': {
                'native_epsg': EPSG, 'native_posting_m': 20, 'same_grid': True, 'common_positive_samples': support,
                'scope': 'Co-registered NISAR radar backscatter before and after the reported incident.',
            },
        }
        paths = sorted(p for p in staged.rglob('*') if p.is_file())
        if len(paths) != 8 or sum(p.stat().st_size for p in paths) > 20_000_000:
            raise ValueError('Prepared assets differ from the four bounded scientific and map pairs')
        output = 'nisar-maranon.json'
        (staged / output).write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
        for folder in ('sar', 'science'):
            (data / folder).mkdir(exist_ok=True)
        for path in (*paths, staged / output):
            path.replace(data / path.relative_to(staged))
    print(json.dumps({'site': SITE, 'observations': [row['id'] for row in observations],
                      'native_bounds': bounds, 'common_positive_samples': support,
                      'archived_screen': review}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--downloads', type=Path, required=True, help='Directory containing both original HDF5 files')
    parser.add_argument('--qgis-review', type=Path, required=True, help='Set up QGIS outputs/nisar_amazon archive')
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    args = parser.parse_args()
    prepare(args.data, args.downloads, args.qgis_review)


if __name__ == '__main__':
    main()
