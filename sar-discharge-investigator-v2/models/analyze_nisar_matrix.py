"""Measure the locally validated NISAR matrix without using display pixels."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from hashlib import file_digest
from itertools import combinations
import json
from pathlib import Path
import re

import numpy as np
import rasterio


MANIFESTS = ('non-sentinel-nisar.json', 'nisar-burgas.json', 'nisar-uae-coastal-review.json', 'nisar-everglades.json', 'nisar-maranon.json', 'nisar-holdouts.json', 'nisar-wetland-holdout.json', 'nisar-hormuz-sep10-reference.json')
CHANNELS = ('HH', 'HV')
CHANGE_DB = 3.0
MAX_SAMPLES = 6_000_000
LOCAL_ASSET = re.compile(r'/science/(nisar-[a-z0-9-]+\.tif)\Z')


@dataclass(frozen=True)
class Channel:
    grid: tuple
    power: np.ndarray
    db: np.ndarray
    evidence_id: str
    sha256: str
    url: str


def digest(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def read_channel(data, record, asset):
    match = LOCAL_ASSET.fullmatch(asset['science_url'])
    if match is None:
        raise ValueError('Only registered local NISAR science GeoTIFFs may be analysed')
    directory = (data/'science').resolve()
    path = (directory/match.group(1)).resolve()
    if path.parent!=directory:
        raise ValueError('Science layer escapes the registered data directory')
    checksum = digest(path)
    if checksum!=asset['science_sha256']:
        raise ValueError(f'Science byte digest mismatch for {path.name}')
    shape = asset.get('science_shape') or asset['source_shape']
    epsg = asset.get('science_epsg') or asset['source_epsg']
    expected_valid = asset.get('valid_pixels',asset.get('source_valid_pixels'))
    posting = asset.get('science_posting_m', 20)
    with rasterio.open(path) as src:
        if (src.count!=1 or src.dtypes!=('float32',) or src.width*src.height>MAX_SAMPLES
                or list(src.shape)!=shape or src.crs.to_epsg()!=epsg or not src.crs.is_projected
                or posting not in {10,20} or src.res!=(posting,posting) or src.transform.a!=posting or src.transform.e!=-posting
                or src.transform.b!=0 or src.transform.d!=0 or src.nodata is None or not np.isnan(src.nodata)
                or src.units not in {('1',),(None,)} or 'gamma0' not in asset['science_units']):
            raise ValueError(f'Unexpected NISAR scientific grid, nodata or units for {path.name}')
        if asset.get('science_transform') is not None and list(src.transform)[:6]!=asset['science_transform']:
            raise ValueError(f'Native affine differs from the manifest for {path.name}')
        power = src.read(1,masked=True).filled(np.nan)
        grid = (epsg,src.shape,tuple(src.transform)[:6])
    valid = np.isfinite(power)&(power>=0)
    if np.any(np.isfinite(power)&~valid) or int(valid.sum())!=expected_valid:
        raise ValueError(f'Native scientific validity differs from the manifest for {path.name}')
    positive = valid&(power>0)
    db = np.full(power.shape,np.nan,dtype=np.float64)
    db[positive] = 10*np.log10(power[positive].astype(np.float64))
    if not np.any(positive):
        raise ValueError(f'Scene has no measurable positive gamma0 samples in {path.name}')
    return Channel(grid,power,db,asset.get('evidence_id') or record['evidence_id'],checksum,asset['science_url'])


def quantiles(values):
    low,median,high = np.quantile(values,[.10,.5,.90])
    return float(low),float(median),float(high)


def summarize(channel):
    measured = channel.db[np.isfinite(channel.db)]
    low,median,high = quantiles(measured)
    valid = int(np.isfinite(channel.power).sum())
    total = channel.power.size
    epsg,shape,affine = channel.grid
    return {'valid_pixels':valid,'total_pixels':total,'valid_fraction':valid/total,
        'positive_pixels':len(measured),'zero_power_pixels':valid-len(measured),
        'median_db':median,'p10_db':low,'p90_db':high,'mean_db':float(measured.mean()),
        'native_crs_epsg':epsg,'native_posting_m':affine[0],'shape':shape,'affine':affine,
        'science_sha256':channel.sha256,'science_url':channel.url,'evidence_id':channel.evidence_id}


def dual_pol(layers):
    hh,hv = layers.get('HH'),layers.get('HV')
    if hh is None or hv is None or hh.grid!=hv.grid:
        return None
    support = np.isfinite(hh.db)&np.isfinite(hv.db)
    if not support.any():
        return None
    low,median,high = quantiles(hh.db[support]-hv.db[support])
    return {'paired_pixels':int(support.sum()),'median_hh_minus_hv_db':median,
        'p10_hh_minus_hv_db':low,'p90_hh_minus_hv_db':high}


def same_geometry(earlier,later):
    keys = ('site_id','product','sensor','frequency','orbit','frame','direction','look_direction',
            'processing_version','noise_correction_applied')
    return all(key in earlier and key in later and earlier[key]==later[key] for key in keys)


def compare_channel(earlier,later):
    # Use finite positive power at both dates on the exact same native grid.
    if earlier.grid!=later.grid:
        return None
    support = np.isfinite(earlier.db)&np.isfinite(later.db)
    if not support.any():
        return None
    initial,final = earlier.db[support],later.db[support]
    change = final-initial
    low,median,high = quantiles(change)
    brighter = int(np.count_nonzero(change>=CHANGE_DB))
    darker = int(np.count_nonzero(change<=-CHANGE_DB))
    return {'paired_pixels':len(change),'grid_pixels':earlier.db.size,'paired_fraction':len(change)/earlier.db.size,
        'median_change_db':median,'p10_change_db':low,'p90_change_db':high,
        'earlier_median_db':float(np.median(initial)),'later_median_db':float(np.median(final)),
        'brighter_at_least_3db_pixels':brighter,'brighter_at_least_3db_pct':100*brighter/len(change),
        'darker_at_least_3db_pixels':darker,'darker_at_least_3db_pct':100*darker/len(change)}


def analyze(data):
    measurements,by_site,comparisons = {},defaultdict(list),[]
    inputs = {}
    for filename in MANIFESTS:
        path = data/filename
        inputs[filename] = digest(path)
        manifest = json.loads(path.read_text(encoding='utf-8'))
        for record in manifest['observations']:
            identifier = record['id']
            if record['product']!='NISAR GCOV' or record['sensor']!='L-SAR' or identifier in measurements:
                raise ValueError('Unexpected or duplicated NISAR scientific observation')
            layers = {pol:read_channel(data,record,asset) for pol,asset in record['channels'].items()
                      if pol in CHANNELS and asset['status']=='validated' and asset.get('science_url')}
            if not layers:
                continue
            evidence = sorted({record['evidence_id'],*[layer.evidence_id for layer in layers.values()]})
            measurements[identifier] = {'site_id':record['site_id'],'product_id':record['product_id'],
                'acquired_at':record['acquired_at'],'pol':{pol:summarize(layer) for pol,layer in layers.items()},
                'dual_pol':dual_pol(layers),'evidence_ids':evidence}
            by_site[record['site_id']].append((record,layers))
    readiness = {}
    for site_id,available in by_site.items():
        available.sort(key=lambda pair:pair[0]['acquired_at'])
        ready = 0
        for (earlier,before),(later,after) in combinations(available,2):
            if earlier['acquired_at'][:10]==later['acquired_at'][:10] or not same_geometry(earlier,later):
                continue
            changes = {}
            for pol in CHANNELS:
                if pol in before and pol in after:
                    change = compare_channel(before[pol],after[pol])
                    if change:
                        changes[pol] = change
            if changes:
                ready += 1
                early_id,late_id = earlier['id'],later['id']
                comparisons.append({'site_id':site_id,'earlier_id':early_id,'later_id':late_id,
                    'earlier_date':earlier['acquired_at'][:10],'later_date':later['acquired_at'][:10],
                    'track':earlier['orbit'],'frame':earlier['frame'],'frequency':earlier['frequency'],
                    'channels':changes,'evidence_ids':sorted(set(measurements[early_id]['evidence_ids']+measurements[late_id]['evidence_ids']))})
        unique_dates = sorted({row['acquired_at'][:10] for row,_ in available})
        readiness[site_id] = {'dates':unique_dates,'observation_ids':[row['id'] for row,_ in available],
            'channels':sorted({pol for _,layers in available for pol in layers}),
            'matched_comparison_count':ready,'event_evidence':'requires_independent_reference'}
    return {'schema_version':1,'source_manifests_sha256':inputs,
        'method':{'quantity':'10 log10 of positive native measured gamma0 power','unit':'dB',
            'coverage':'Finite nonnegative science pixels are valid. Only positive values enter dB summaries.',
            'paired_change':'Later minus earlier at common positive-power cells, on identical native grids and matching acquisition geometry and processing.',
            'change_threshold_db':CHANGE_DB,'threshold_meaning':'Descriptive fraction of paired cells reaching a backscatter change. It does not classify surface water or oil.',
            'scope':'Measured radar response in the registered windows. Cross-site differences and single-date darkness do not establish event detection.'},
        'observations':measurements,'comparisons':comparisons,'site_readiness':readiness}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[1]/'data')
    cli.add_argument('--output',type=Path)
    args = cli.parse_args()
    results = analyze(args.data)
    path = args.output or args.data/'nisar-measurements.json'
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(results,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    tmp.replace(path)
    print(f'Measured {len(results["observations"])} observations and {len(results["comparisons"])} co-registered date pairs.')


if __name__=='__main__':
    main()
