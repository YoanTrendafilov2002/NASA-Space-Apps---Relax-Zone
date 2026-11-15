"""Re-run the original QGIS Everglades experiment with local hydrologic labels."""

from __future__ import annotations

import argparse
import csv
from hashlib import file_digest
import json
from pathlib import Path
import runpy
import sys

import h5py  # Register before GDAL on Windows.
import numpy as np
from osgeo import gdal, ogr


EXPECTED = 'NISAR_L2_PR_GCOV_034_047_A_014_2005_DHDH_M_20261027T104408_20261027T104439_P05036_N_F_J_001'


def source(path):
    with path.open('rb') as handle:
        sha256 = file_digest(handle, 'sha256').hexdigest()
    return {'filename': path.name, 'bytes': path.stat().st_size, 'sha256': sha256}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--h5', type=Path, required=True)
    cli.add_argument('--reference-directory', type=Path, required=True)
    cli.add_argument('--qgis-work', type=Path, required=True)
    cli.add_argument('--out', type=Path, required=True)
    args = cli.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    args.qgis_work = args.qgis_work.resolve()
    legacy_path = args.qgis_work / 'everglades_wetland_validation.py'
    reference = args.reference_directory
    depth_source = reference / 'EDEN_2026_Q4_depth.nc'
    inventory = reference / 'NWI_emergent_wetlands.geojson'
    if not depth_source.is_file() or not inventory.is_file():
        raise FileNotFoundError('Use existing local EDEN depth and NWI polygons for this offline replication')
    with h5py.File(args.h5) as hdf:
        if hdf['science/LSAR/identification/granuleId'][()].decode() != EXPECTED:
            raise ValueError('The local HDF5 is outside the pinned Everglades experiment')
    sys.path.insert(0, str(args.qgis_work))
    original = runpy.run_path(str(legacy_path))
    gdal.UseExceptions()
    ogr.UseExceptions()
    x, y, depth = original['read_eden'](reference)
    cover = original['read_emergent'](reference, x, y)
    hh, snr_hh, coverage_hh, audit, posting = original['native_to_eden'](args.h5, x, y, 'HH')
    hv, snr_hv, coverage_hv, _, _ = original['native_to_eden'](args.h5, x, y, 'HV')
    domain = np.isfinite(depth) & (cover >= original['MIN_COVER'])
    truth = depth >= original['DEPTH_CM']
    eligible = domain & (truth | (depth == 0))
    good_radar = ((coverage_hh >= original['MIN_RADAR']) & (coverage_hv >= original['MIN_RADAR'])
                  & (snr_hh >= original['MIN_SNR_DB']) & (snr_hv >= original['MIN_SNR_DB']))
    assessed = eligible & good_radar
    rows, _ = original['evaluate'](x, y, depth, hh, hv, assessed)
    summary = [row for row in rows if row['fold'] == 'all held-out']
    old = json.loads((reference / 'provenance.json').read_text(encoding='utf-8'))
    if summary != old['metrics']:
        raise RuntimeError('Fresh EDEN metrics differ from the archived held-out benchmark')
    with (args.out / 'scores.csv').open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    report = {'site': 'Shark River Slough, Everglades', 'date': str(original['DATE']), 'status': 'completed',
              'scope': 'Separate one-date spatially held-out inundation screen over emergent vegetation.',
              'targets': 'USGS EDEN daily modeled depth 0 cm versus at least 10 cm in NWI freshwater emergent cells.',
              'reference_limit': 'Provisional EDEN v3 modeled depths use interpolation of water-level gauges and elevation.',
              'comparison_limit': 'This target, scale and fit differ from the Curuai exposed-water model. No cross-site or temporal transfer was tested here.',
              'radar_source': {**source(args.h5), **audit[0], 'posting_m': posting},
              'depth_source': {**source(depth_source), 'url': original['EDEN_URL'], 'documentation': 'https://pubs.usgs.gov/publication/sir20205083'},
              'inventory_source': {**source(inventory), 'url': original['NWI_URL'], 'documentation': 'https://www.fws.gov/program/national-wetlands-inventory/data-download'},
              'source_algorithm': source(legacy_path), 'grid_m': original['CELL_M'], 'spatial_block_m': original['BLOCK_M'],
              'folds': original['N_FOLDS'], 'eligible_cells': int(eligible.sum()), 'assessed_cells': int(assessed.sum()),
              'radar_abstentions': int((eligible & ~assessed).sum()), 'reproduced_archived_scores': True, 'metrics': summary}
    (args.out / 'research-report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
    print(f"Replicated Everglades on {int(assessed.sum()):,} of {int(eligible.sum()):,} eligible cells.", flush=True)
    for row in summary:
        print(row['model'], f"IoU {row['IoU']:.6f}", flush=True)


if __name__ == '__main__':
    main()
