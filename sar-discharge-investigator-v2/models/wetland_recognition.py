"""Test a single radar-texture feature on held-out Curuai water labels."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import h5py  # Load h5py before GDAL's Windows HDF5 driver.
import numpy as np
from osgeo import gdal
from scipy.ndimage import minimum_filter, uniform_filter

from wetland import (CELL, PENALTY, SPATIAL_BLOCK, dual_support, extract, logit_fit, predictors,
                     save_json, score, spatial_blocks, threshold_fit)


NEIGHBORHOOD = 3
BOOTSTRAP_REPEATS = 1000
BOOTSTRAP_SEED = 4217
HH = 'QGIS HH threshold'
BASE = 'HH and HH−HV logistic'
TEXTURE = 'HH and HH−HV logistic with local HH texture'
AUGUST = 'August spatial holdout'
OCTOBER = 'October temporal holdout on August-withheld blocks'
LOSS = 'August–October paired loss on withheld blocks'
FEATURE_LABELS = ['HH gamma0 dB', 'HH−HV observed gamma0 dB contrast']
TEXTURE_LABEL = 'Population standard deviation of HH gamma0 dB in a 3×3 window of 60 m cells'
PROTOCOL = {
    'target': 'Optically exposed water at Curuai, Brazil',
    'hypothesis': 'Spatial heterogeneity in HH can help identify open water with the August fit held fixed.',
    'candidate_predictors': [*FEATURE_LABELS, TEXTURE_LABEL],
    'control_predictors': FEATURE_LABELS,
    'landmark_comparator': 'Original QGIS HH cutoff fitted using its established August split.',
    'training_date': '2026-08-13',
    'evaluation_date': '2026-10-12',
    'spatial_partition': 'Fixed 3 km checkerboard. A cell and all eight neighbors must lie on its own side of the split.',
    'analysis_grid_m': CELL,
    'predictor_window_cells': NEIGHBORHOOD,
    'l2_weight': PENALTY,
    'logistic_decision_logit': 0,
    'support': 'All nine neighborhood cells pass the original dual-channel quality support. Features use measured NISAR power only.',
    'success_criterion': 'Candidate IoU must exceed both HH threshold and the two-feature control for both October exposed water and paired exposed-water loss.',
    'uncertainty': f'Paired resampling of populated spatial blocks within this site, {BOOTSTRAP_REPEATS} draws and seed {BOOTSTRAP_SEED}.',
    'reuse': 'October was previously scored for the published baseline. It remains held out of fitting and method selection in this fixed follow-up run.',
}


def blocks(epoch):
    x = np.floor(epoch.x / SPATIAL_BLOCK).astype(int)
    y = np.floor(epoch.y / SPATIAL_BLOCK).astype(int)
    first = (x[None, :] + y[:, None]) % 2 == 0
    ids = (y[:, None] - y.min()) * (x.max() - x.min() + 1) + (x[None, :] - x.min())
    train = minimum_filter(first, size=NEIGHBORHOOD, mode='constant', cval=0)
    holdout = minimum_filter(~first, size=NEIGHBORHOOD, mode='constant', cval=0)
    if np.any(train & holdout):
        raise RuntimeError('Fit and held-out centers overlap')
    return train, holdout, ids


def context_features(epoch, cutoff):
    observed = dual_support(epoch, cutoff)
    support = minimum_filter(observed, size=NEIGHBORHOOD, mode='constant', cval=0)
    hh = np.where(observed, epoch.hh, 0).astype(np.float64)
    mean = uniform_filter(hh, size=NEIGHBORHOOD, mode='constant', cval=0)
    second_moment = uniform_filter(hh**2, size=NEIGHBORHOOD, mode='constant', cval=0)
    texture = np.sqrt(np.maximum(second_moment - mean**2, 0))
    return np.concatenate((predictors(epoch), texture[:, :, None]), axis=2), support


def classify(fit, features, support):
    result = np.zeros(support.shape, dtype=bool)
    mean = np.asarray(fit['centering'])
    scale = np.asarray(fit['scaling'])
    coeff = np.asarray(fit['coefficients'])
    values = (features[support, :len(mean)] - mean) / scale
    result[support] = values @ coeff[1:] + coeff[0] >= fit['decision_logit']
    return result


def score_block_counts(truth, prediction, valid, geographic_block):
    block_id = geographic_block[valid]
    label = truth[valid]
    found = prediction[valid]
    size = int(geographic_block.max()) + 1
    masks = (label & found, label & ~found, ~label & found, ~label & ~found)
    return np.stack([np.bincount(block_id[mask], minlength=size) for mask in masks], axis=1)


def spatial_interval(candidate, control):
    populated = candidate.sum(axis=1) > 0
    count = int(populated.sum())
    draws = np.random.default_rng(BOOTSTRAP_SEED).integers(0, count, size=(BOOTSTRAP_REPEATS, count))
    numerator = candidate[populated][draws].sum(axis=1)
    comparator = control[populated][draws].sum(axis=1)
    positive = (numerator[:, :3].sum(axis=1) > 0) & (comparator[:, :3].sum(axis=1) > 0)
    diff = numerator[positive, 0] / numerator[positive, :3].sum(axis=1) - comparator[positive, 0] / comparator[positive, :3].sum(axis=1)
    if not len(diff):
        raise ValueError('Holdout cannot support a block-resampled IoU difference')
    return {'interval_95': np.quantile(diff, [.025, .975]).tolist(), 'populated_blocks': count, 'valid_draws': len(diff)}


def source_epoch(directory, name, date, path):
    tag = f'curuai_{date}_optical'
    return extract(name, path, directory / f'{tag}_reference.tif', directory / f'{tag}_provenance.json')


def run(args):
    args.out.mkdir(parents=True, exist_ok=True)
    save_json(args.out / 'protocol.json', PROTOCOL)
    print('Reading August and fitting the locked radar-texture comparison.', flush=True)
    august = source_epoch(args.optical_directory, 'august', '20260813', args.august_h5)
    threshold, hh_fit = threshold_fit(august, spatial_blocks(august)[0])
    train_region, holdout, block_ids = blocks(august)
    features, support = context_features(august, threshold)
    fit_cells = train_region & support & (august.reference > 0)
    labels = august.reference[fit_cells] == 1
    simple_fit = logit_fit(features[fit_cells, :2], labels)
    texture_fit = logit_fit(features[fit_cells], labels)
    texture_fit['features'] = [*FEATURE_LABELS, TEXTURE_LABEL]
    frozen = {'training_input': august.source, 'training_cells_on_common_support': int(fit_cells.sum()),
              'models': {HH: hh_fit, BASE: simple_fit, TEXTURE: texture_fit}}
    save_json(args.out / 'frozen-models.json', frozen)

    print(f'Fit locked on {fit_cells.sum():,} August training cells. Opening October for score only.', flush=True)
    october = source_epoch(args.optical_directory, 'october', '20261012', args.october_h5)
    if not np.array_equal(august.x, october.x) or not np.array_equal(august.y, october.y):
        raise ValueError('Date grids must align exactly')
    if any(august.source[field] != october.source[field] for field in ('track_number', 'frame_number', 'pass_direction', 'look_direction')):
        raise ValueError('October and August do not share the pinned acquisition geometry')
    later_features, later_support = context_features(october, threshold)
    start = {HH: august.hh <= threshold, BASE: classify(simple_fit, features, support),
             TEXTURE: classify(texture_fit, features, support)}
    end = {HH: october.hh <= threshold, BASE: classify(simple_fit, later_features, later_support),
           TEXTURE: classify(texture_fit, later_features, later_support)}
    targets = {
        AUGUST: ('exposed water', august.reference == 1, holdout & (august.reference > 0), support, start),
        OCTOBER: ('exposed water', october.reference == 1, holdout & (october.reference > 0), later_support, end),
        LOSS: ('exposed-water loss', (august.reference == 1) & (october.reference == 2),
               holdout & (august.reference > 0) & (october.reference > 0), support & later_support,
               {model: start[model] & ~end[model] for model in start}),
    }
    metric_rows = []
    comparisons = []
    by_target = {}
    for test, (outcome, truth, ref_cells, assessed, prediction) in targets.items():
        rows = {name: score(test, name, outcome, truth, pred, ref_cells, assessed) for name, pred in prediction.items()}
        metric_rows.extend(rows.values())
        by_target[test] = rows
        common = ref_cells & assessed
        candidate = score_block_counts(truth, prediction[TEXTURE], common, block_ids)
        for comparator in (HH, BASE):
            old_counts = score_block_counts(truth, prediction[comparator], common, block_ids)
            diff = rows[TEXTURE]['iou'] - rows[comparator]['iou']
            comparisons.append({'test': test, 'control': comparator, 'candidate_minus_control_iou': diff,
                                **spatial_interval(candidate, old_counts)})
    success = all(row['candidate_minus_control_iou'] > 0 for row in comparisons if row['test'] in (OCTOBER, LOSS))
    with (args.out / 'metrics.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(metric_rows[0]))
        writer.writeheader()
        writer.writerows(metric_rows)
    save_json(args.out / 'research-report.json', {'protocol': PROTOCOL, 'training': frozen, 'evaluation_input': october.source,
              'candidate_met_predeclared_criterion': success, 'metrics': metric_rows, 'comparisons': comparisons,
              'assessment_limits': ['Separate optical reference acquired about 4 hours and 52 minutes later than radar.',
                                    'One site and two dates. Spatial resampling measures within-site variability.',
                                    'Labels cover optically exposed water and exclude uncertain cells. Field confirmation, event attribution, and vegetation-covered water remain untested by this experiment.']})
    summary = {'id': 'curuai-exposed-water-texture-validation', 'status': 'completed',
        'site': 'Lago Grande de Curuai, Brazil', 'target': 'Optically exposed water', 'predictor_source': 'NISAR L-band frequency A GCOV only',
        'training_date': '2026-08-13', 'evaluation_date': '2026-10-12', 'training_cells': int(fit_cells.sum()),
        'new_feature': TEXTURE_LABEL, 'candidate_met_predeclared_criterion': success,
        'validation_status': 'single_site_research_screen', 'date_transfer_validated_for_use': False,
        'evidence_status': {'exposed_water': 'evaluated_against_optical_proxy', 'exposed_water_loss': 'evaluated_against_optical_proxy',
                            'flood_event_extent': 'unsupported', 'inundation_under_vegetation': 'unsupported', 'drought': 'unsupported'},
        'reference_source': 'Same-day Sentinel-2A clear optical agreement labels used offline. The predictor features are radar-only.',
        'metrics': [{'test': test, 'common_assessed_cells': next(iter(rows.values()))['assessed_cells'],
                     'models': {model: {key: row[key] for key in ('iou', 'recall', 'precision')} for model, row in rows.items()}}
                    for test, rows in by_target.items()]}
    save_json(args.out / 'summary.json', summary)
    print(f'Locked candidate met the joint improvement criterion: {success}. Saved {len(metric_rows)} score rows.', flush=True)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--august-h5', required=True, type=Path)
    cli.add_argument('--october-h5', required=True, type=Path)
    cli.add_argument('--optical-directory', required=True, type=Path)
    cli.add_argument('--out', required=True, type=Path)
    args = cli.parse_args()
    gdal.UseExceptions()
    run(args)


if __name__ == '__main__':
    main()
