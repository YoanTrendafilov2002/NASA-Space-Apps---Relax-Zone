"""Export a NISAR view inside the archived Everglades EDEN benchmark."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.request import urlopen

import h5py
import numpy as np
import rasterio
from rasterio.transform import array_bounds, from_origin
from rasterio.warp import transform, transform_bounds, transform_geom
from shapely.geometry import box, shape

from build_nisar_map_assets import GRID, IDENT, POSTING, RANGES, ROOT, SIDE, WGS84, digest, make_assets, provider_metadata


OBSERVATION = 'nisar-everglades-20261027'
SITE = 'everglades-nisar'
GRANULE = 'NISAR_L2_PR_GCOV_034_047_A_014_2005_DHDH_M_20261027T104408_20261027T104439_P05036_N_F_J_001'
CONCEPT = 'G4406681165-ASF'
COLLECTION = 'C3622241360-ASF'
CMR = f'https://cmr.earthdata.nasa.gov/search/granules.umm_json?concept_id={CONCEPT}'
EPSG = 32617
CENTRE = (524000, 2826000)
DATE = '2026-10-27'
STUDY_BOUNDS = [-80.88, 25.43, -80.60, 25.71]
FOCUS = {'HH': 256, 'HV': 512}


def read_inputs(source):
    with h5py.File(source) as hdf:
        identification = hdf[IDENT]
        grid = hdf[GRID]
        parameters = hdf[f'{ROOT}/metadata/processingInformation/parameters']
        normalization = parameters['rtc/outputBackscatterNormalizationConvention'][()].decode().lower()
        noise_corrected = parameters['noiseCorrectionApplied'][()].decode().lower()
        if (identification['granuleId'][()].decode() != GRANULE or identification['radarBand'][()] != b'L'
                or int(grid['projection'][()]) != EPSG or normalization != 'gamma0' or noise_corrected != 'false'):
            raise ValueError('The HDF5 does not match the pinned L-band scientific product')
        x, y = grid['xCoordinates'][:], grid['yCoordinates'][:]
        col = int(np.argmin(np.abs(x - CENTRE[0]))) - SIDE // 2
        row = int(np.argmin(np.abs(y - CENTRE[1]))) - SIDE // 2
        cols, rows = slice(col, col + SIDE), slice(row, row + SIDE)
        if (row < 0 or col < 0 or row + SIDE > len(y) or col + SIDE > len(x)
                or not np.all(np.diff(x[cols]) == POSTING) or not np.all(np.diff(y[rows]) == -POSTING)):
            raise ValueError('The science crop must fit on the native 20 m grid')
        affine = from_origin(x[col] - POSTING / 2, y[row] + POSTING / 2, POSTING, POSTING)
        mask, exception, looks = (grid[key][rows, cols] for key in ('mask', 'inputDataExceptionMask', 'numberOfLooks'))
        description = grid['inputDataExceptionMask'].attrs['description'].decode()
        if exception.dtype != np.uint16 or any(f'{bit}:{pol}' not in description for pol, bit in FOCUS.items()):
            raise ValueError('Expected the product-documented per-polarization focused-data flags')
        common = ((mask >= 1) & (mask <= grid['numberOfSubSwaths'][()]) & ((exception & 255) == 0)
                  & np.isfinite(looks) & (looks > 0))
        inputs = {}
        for pol in FOCUS:
            source_data = grid[pol * 2]
            if source_data.dtype != np.dtype('float32') or source_data.attrs['units'] != b'1':
                raise ValueError('Expected unitless linear gamma0 covariance power')
            power = source_data[rows, cols]
            usable = common & ((exception & FOCUS[pol]) != 0) & np.isfinite(power) & (power >= 0)
            if not usable.all():
                raise ValueError(f'The pinned {pol} source crop no longer has full measured coverage')
            inputs[pol] = power
        meta = {'orbit': int(identification['trackNumber'][()]), 'absolute_orbit': int(identification['absoluteOrbitNumber'][()]),
                'frame': int(identification['frameNumber'][()]), 'direction': identification['orbitPassDirection'][()].decode().upper(),
                'look_direction': identification['lookDirection'][()].decode(),
                'acquired_at': identification['zeroDopplerStartTime'][()].decode().split('.')[0] + 'Z',
                'acquisition_end': identification['zeroDopplerEndTime'][()].decode() + 'Z',
                'processing_version': identification['productVersion'][()].decode(),
                'product_specification_version': identification['productSpecificationVersion'][()].decode(),
                'noise_correction_applied': False, 'source_window': [col, row, SIDE, SIDE]}
    return inputs, affine, meta


def study_overlap(reference_directory, native_bounds):
    legacy = json.loads((reference_directory / 'provenance.json').read_text(encoding='utf-8'))
    if (legacy['granule'] != GRANULE or legacy['granule_concept_id'] != CONCEPT or legacy['date'] != DATE
            or legacy['bbox_wgs84'] != STUDY_BOUNDS or legacy['cell_m'] != 400):
        raise ValueError('The study provenance does not match the pinned source and sampling grid')
    filename = reference_directory / 'inundation_comparison.tif'
    with rasterio.open(filename) as study:
        if study.crs.to_epsg() != 26917 or study.res != (400, 400):
            raise ValueError('Expected the original EDEN 400 m benchmark grid')
        rows, cols = np.indices(study.shape)
        xx, yy = study.transform * (cols.ravel() + .5, rows.ravel() + .5)
        east, north = transform(study.crs, f'EPSG:{EPSG}', xx.tolist(), yy.tolist())
        labels = study.read(1).ravel()
    x, y = np.asarray(east), np.asarray(north)
    west, south, east, north = native_bounds
    inside = (x >= west) & (x < east) & (y >= south) & (y < north)
    assessed = inside & np.isin(labels, (3, 4, 5, 6))
    if not assessed.any():
        raise ValueError('The image crop does not cover any evaluated EDEN reference cells')
    return {'evaluation_grid_m': 400, 'crop_covers_study_grid_centres': int(inside.sum()),
            'crop_covers_assessed_grid_centres': int(assessed.sum()), 'reference_grid_sha256': digest(filename),
            'benchmark_study_bbox_wgs84': STUDY_BOUNDS, 'original_benchmark_assessed_cells': legacy['counts']['scored']}


def verify_cmr():
    with urlopen(CMR, timeout=12) as response:
        payload = response.read(100_001)
    if len(payload) > 100_000:
        raise ValueError('CMR exceeded its bounded metadata response size')
    results = json.loads(payload)['items']
    if len(results) != 1 or results[0]['meta']['concept-id'] != CONCEPT or results[0]['umm']['GranuleUR'] != GRANULE:
        raise ValueError('The original granule and concept do not match provider CMR')


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--h5', required=True, type=Path)
    cli.add_argument('--reference-directory', required=True, type=Path)
    cli.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    args = cli.parse_args()
    url, footprint, props = provider_metadata(GRANULE, args.h5)
    verify_cmr()
    if props['conceptID'] != COLLECTION or props['mainBandPolarization'] != ['HH', 'HV']:
        raise ValueError('Unexpected provider product version or polarization list')
    inputs, affine, metadata = read_inputs(args.h5)
    if (metadata['acquired_at'], metadata['orbit'], metadata['frame'], metadata['direction']) != (
            props['startTime'], props['pathNumber'], props['frameNumber'], props['flightDirection']):
        raise ValueError('Provider and embedded acquisition metadata disagree')
    native_bounds = array_bounds(SIDE, SIDE, affine)
    crs = f'EPSG:{EPSG}'
    crop = transform_geom(crs, WGS84, box(*native_bounds).__geo_interface__)
    bbox = list(transform_bounds(crs, WGS84, *native_bounds))
    if not shape(footprint).covers(shape(crop)) or not box(*STUDY_BOUNDS).covers(box(*bbox)):
        raise ValueError('The crop must sit inside the official SAR footprint and the EDEN evaluation area')
    overlap = study_overlap(args.reference_directory, native_bounds)
    for folder in ('sar', 'science'):
        (args.data / folder).mkdir(parents=True, exist_ok=True)
    evidence = 'nisar-everglades-20261027-source'
    audit_ids = ['everglades-eden-depth', 'everglades-nwi-emergent']
    channels = {}
    for pol in RANGES:
        channel = make_assets(args.data, OBSERVATION, GRANULE, EPSG, affine, pol, inputs[pol], metadata)
        channel['evidence_id'] = evidence
        channel['quality_mask'] = f'Valid subswath, no low-byte input anomaly, {pol} focus bit {FOCUS[pol]}, positive looks, finite nonnegative power.'
        channels[pol] = channel
    site = {'id': SITE, 'name': 'Shark River Slough, Everglades', 'topic': 'wetlands', 'bbox': bbox,
            'start': DATE, 'end': DATE, 'aliases': ['Everglades', 'Florida Everglades', 'Shark River Slough'],
            'evidence_id': evidence, 'benchmark_evidence_ids': audit_ids}
    row = {'id': OBSERVATION, 'site_id': SITE, 'product_id': GRANULE, 'acquisition_id': GRANULE,
           'mission': 'nisar', 'provider': 'NASA ASF DAAC', 'product': 'NISAR GCOV', 'sensor': 'L-SAR', 'wavelength': 'L',
           'product_type': 'GCOV', 'frequency': 'A', 'mode': 'Frequency A · dual polarization',
           'collection': 'NISAR_L2_GCOV_V1', 'collection_concept_id': COLLECTION, 'granule_concept_id': CONCEPT,
           'geometry': crop, 'source_geometry': footprint, 'geometry_kind': 'local_coverage_window',
           'channels': channels, 'evidence_id': evidence, 'source_url': url, 'science_requires_earthdata': False,
           'source_requires_earthdata': True, 'benchmark_evidence_ids': audit_ids, **metadata}
    entries = [{'id': evidence, 'title': 'ASF NISAR GCOV source image · Everglades', 'kind': 'downloaded_scientific_hdf5',
                'url': url, 'source_id': GRANULE, 'source_filename': args.h5.name, 'source_size_bytes': args.h5.stat().st_size,
                'source_sha256': digest(args.h5), 'provider_size_bytes': props['bytes'][args.h5.name]['bytes'],
                'provider_asset_url': props['url'], 'cmr_url': CMR, 'source_authentication': 'NASA Earthdata Login required for original HDF5',
                'accessed_at': datetime.now(timezone.utc).date().isoformat(), 'benchmark_overlap_audit': overlap,
                'documentation_url': 'https://nisar-docs.asf.alaska.edu/gcov/',
                'notes': 'Measured 20 m HH and HV gamma0 power from the original acquisition used in the archived EDEN benchmark. The map shows a 15 km window of this source scene.'},
               {'id': audit_ids[0], 'title': 'USGS EDEN · modeled water-depth reference', 'kind': 'benchmark_reference',
                'url': 'https://pubs.usgs.gov/publication/sir20205083',
                'notes': 'Audit-only reference. USGS EDEN estimates daily water depth from gauge observations and terrain elevation for the separate 400 m wetland benchmark.'},
               {'id': audit_ids[1], 'title': 'USFWS National Wetlands Inventory · Emergent wetland scope', 'kind': 'benchmark_reference',
                'url': 'https://www.fws.gov/program/national-wetlands-inventory/data-download',
                'notes': 'Audit-only reference. Freshwater emergent polygons define the eligibility domain in the separate Shark River Slough benchmark.'}]
    target = args.data / 'nisar-everglades.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps({'schema_version': 1, 'sites': [site], 'observations': [row], 'evidence': entries},
                               ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(target)
    print(json.dumps({'id': OBSERVATION, 'channels': {pol: layer['valid_pixels'] for pol, layer in channels.items()},
                      'image_bounds': channels['HH']['image_bounds'], 'benchmark_overlap': overlap}), flush=True)


if __name__ == '__main__':
    main()
