"""Build time series only from aligned, measured NISAR samples on common support."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import numpy as np

from analyze_nisar_matrix import CHANNELS, MANIFESTS, read_channel, same_geometry


SERIES_SITES = {'curuai-nisar', 'maranon-nisar', 'qeshm-nisar'}
STATISTIC = 'Median gamma0 backscatter over the same pixels on every date'


def build_series(data, manifest_paths):
    observations = defaultdict(list)
    for path in manifest_paths:
        manifest = json.loads(path.read_text(encoding='utf-8-sig'))
        for record in manifest['observations']:
            if record['site_id'] in SERIES_SITES:
                observations[record['site_id']].append(record)
    series = []
    for site, records in sorted(observations.items()):
        for pol in CHANNELS:
            eligible = sorted((record for record in records
                               if record['channels'].get(pol, {}).get('status') == 'validated'),
                              key=lambda record: record['acquired_at'])
            if len(eligible) < 2:
                continue
            first = eligible[0]
            if not all(same_geometry(first, record) for record in eligible[1:]):
                raise ValueError(f'Acquisitions in {site} {pol} have incompatible viewing or processing geometry')
            channels = [read_channel(data, record, record['channels'][pol]) for record in eligible]
            if not all(channels[0].grid == layer.grid for layer in channels[1:]):
                raise ValueError(f'Acquisitions in {site} {pol} do not share a native grid')
            common = np.logical_and.reduce([np.isfinite(layer.db) for layer in channels])
            n = int(common.sum())
            if not n:
                raise ValueError(f'Acquisitions in {site} {pol} lack common calibrated pixels')
            points = []
            for record, layer in zip(eligible, channels):
                value = float(np.median(layer.db[common]))
                low, high = np.quantile(layer.db[common], (.1, .9))
                points.append({'observation_id': record['id'], 'acquired_at': record['acquired_at'],
                               'value': value, 'p10': float(low), 'p90': float(high),
                               'valid_pixels': n, 'source_sha256': layer.sha256})
            summary = 'Each point is an observed date. The median uses exactly the same finite positive cells at every date. Radar brightness can change for several reasons.'
            if any(record.get('noise_correction_applied') is None for record in eligible):
                summary += ' These are published product values. Per-date noise correction settings could not be checked in the subsets.'
            series.append({'id': f'{site}-{pol.lower()}-backscatter', 'site_id': site,
                           'polarization': pol, 'title': f'{pol} radar return over time',
                           'statistic': STATISTIC, 'units': 'dB', 'points': points,
                           'summary': summary,
                           'evidence_ids': list(dict.fromkeys(layer.evidence_id for layer in channels))})
    return series


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    parser.add_argument('--manifest', type=Path, action='append', default=[])
    args = parser.parse_args()
    paths = list(dict.fromkeys([*(args.data / name for name in MANIFESTS), *args.manifest]))
    series = build_series(args.data, paths)
    output = args.data / 'nisar-time-series.json'
    scratch = output.with_suffix('.tmp')
    scratch.write_text(json.dumps({'schema_version': 1, 'series': series, 'evidence': []},
                                  ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    scratch.replace(output)
    for group in series:
        print(group['id'], [(point['acquired_at'], round(point['value'], 3), point['valid_pixels'])
                           for point in group['points']])


if __name__ == '__main__':
    main()
