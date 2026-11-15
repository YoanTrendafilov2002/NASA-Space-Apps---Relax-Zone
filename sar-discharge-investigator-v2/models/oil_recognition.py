"""Fit one NISAR-only darkening score and evaluate it on an independent case."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import math
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter

from oil import CELL_M, OMAN, SNR_MARGIN_DB, UAE, Case, candidates, load_case, make_preview, metrics, sha256


SIGMA_M = 180
TRUNCATE = 3
MIN_WEIGHTED_SUPPORT = 0.75
CLIP_DB = 4.0
FIT_QUANTILE = 0.99
THRESHOLD_STEP_DB = 0.05
FROZEN_UNSMOOTHED_THRESHOLD_DB = 1.5
ORIGINAL_THRESHOLD_DB = 3.0


def coherent(case: Case) -> Case:
    """Weight neighbourhood contrast by the existing per-pixel science gate."""
    support = case.eligible & np.isfinite(case.delta)
    inputs = np.where(support, np.clip(case.delta, -CLIP_DB, CLIP_DB), 0).astype(np.float32)
    params = {"sigma": SIGMA_M/CELL_M, "mode": "constant", "cval": 0.0, "truncate": TRUNCATE}
    weight = gaussian_filter(support.astype(np.float32), **params)
    contrast = gaussian_filter(inputs, **params)
    eligible = support & (weight >= MIN_WEIGHTED_SUPPORT)
    score = np.divide(contrast, weight, out=np.full_like(contrast, np.nan), where=eligible)
    return replace(case, delta=score, eligible=eligible)


def fit_threshold(contrast: np.ndarray) -> dict:
    measured = contrast[np.isfinite(contrast)]
    if not measured.size:
        raise ValueError("No eligible NISAR calibration cells after the neighbourhood support gate.")
    quantile = float(np.quantile(measured, FIT_QUANTILE))
    rounded = round(math.ceil(quantile / THRESHOLD_STEP_DB) * THRESHOLD_STEP_DB, 2)
    threshold = max(THRESHOLD_STEP_DB, rounded)
    return {"eligible_calibration_cells": int(measured.size), "quantile_score_db": quantile,
            "threshold_db": threshold, "cells_at_or_above_threshold": int((measured >= threshold).sum())}


def compare(case: Case, filtered: Case, threshold: float) -> tuple[dict, np.ndarray]:
    new_flags = candidates(filtered, threshold)
    reference = case.proxy & case.scored
    usable = reference & case.open_water & case.quality
    adequate_snr = case.snr >= SNR_MARGIN_DB
    gates = {
        'outside_buffered_water': int((reference & ~case.open_water).sum()),
        'invalid_radar_in_water': int((reference & case.open_water & ~case.quality).sum()),
        'failed_snr_and_local_reference': int((usable & ~adequate_snr & ~case.local_support).sum()),
        'failed_snr_only': int((usable & ~adequate_snr & case.local_support).sum()),
        'failed_local_reference_only': int((usable & adequate_snr & ~case.local_support).sum()),
        'failed_coherence_support_only': int((reference & case.eligible & ~filtered.eligible).sum()),
        'assessed_without_flag': int((reference & filtered.eligible & ~new_flags).sum()),
        'assessed_with_flag': int((reference & new_flags).sum()),
    }
    if sum(gates.values()) != int(reference.sum()):
        raise ValueError('Exclusion stages do not reconcile to the independent reference extent.')
    recognition = metrics(filtered, new_flags)
    recognition['proxy_without_snr_or_local_support_cells'] = sum(gates[key] for key in (
        'failed_snr_and_local_reference', 'failed_snr_only', 'failed_local_reference_only'))
    recognition['proxy_without_coherence_support_cells'] = gates['failed_coherence_support_only']
    baseline_flags = candidates(case, FROZEN_UNSMOOTHED_THRESHOLD_DB)
    shared = case.scored & filtered.eligible
    return {
        "source": case.source,
        "recognition": recognition,
        "reference_gate_breakdown": gates,
        "comparison_on_common_support": {
            "eligible_reference_cells": int((shared & reference).sum()),
            "eligible_unlabelled_cells": int((shared & ~reference).sum()),
            "coherent_overlap_cells": int((shared & reference & new_flags).sum()),
            "unsmoothed_overlap_cells": int((shared & reference & baseline_flags).sum()),
            "coherent_unlabelled_flags": int((shared & ~reference & new_flags).sum()),
            "unsmoothed_unlabelled_flags": int((shared & ~reference & baseline_flags).sum()),
        },
        "frozen_1_5_db_baseline": metrics(case, baseline_flags),
        "original_3_db_baseline": metrics(case, candidates(case, ORIGINAL_THRESHOLD_DB)),
        "additional_proxy_cells_abstained_for_neighbourhood_support": int(
            (case.proxy & case.scored & case.eligible & ~filtered.eligible).sum()
        ),
    }, new_flags


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    oman = load_case(args.data_root, OMAN)
    calibration = coherent(oman)
    # The threshold fitter accepts only NISAR-derived scores. It sees no masks
    # of observations from the independent reference instruments.
    fit = fit_threshold(calibration.delta)
    threshold = fit["threshold_db"]
    print(f"Frozen {threshold:g} dB before loading the UAE case.", flush=True)

    uae = load_case(args.data_root, UAE)
    results = {}
    for role, src, filtered, stem in (
        ("calibration", oman, calibration, "oman"),
        ("held_out", uae, coherent(uae), "uae"),
    ):
        outcome, flags = compare(src, filtered, threshold)
        preview = f"{stem}-coherent-screen.png"
        title = dict(filtered.source, name=filtered.source["name"] + " | coherent screen")
        make_preview(args.output_dir / preview, replace(filtered, source=title), flags, threshold, outcome["recognition"])
        outcome["preview"] = preview
        primary = outcome["recognition"]
        if not primary["eligible_proxy_cells"]:
            outcome["assessment"] = "no_eligible_reference_cells"
        elif not primary["overlap_cells"]:
            outcome["assessment"] = "no_overlap_among_eligible_reference_cells"
        else:
            outcome["assessment"] = "descriptive_reference_overlap"
        results[role] = outcome
        print(f"{role}: matched {primary['overlap_cells']}/{primary['eligible_proxy_cells']} eligible proxy cells; "
              f"{primary['eligible_proxy_cells']}/{primary['proxy_cells']} reference cells assessable; "
              f"{primary['candidate_unlabelled_cells']} eligible unlabelled candidate cells.")

    holdout = results["held_out"]["recognition"]
    summary = {
        "schema_version": 1,
        "study": "NISAR HH coherent dark-water imagery recognition experiment",
        "status": "local_research_only",
        "algorithm_sha256": sha256(Path(__file__)),
        "case_split": {"calibration": OMAN["name"], "held_out": UAE["name"]},
        "predictors": "Only NISAR L-SAR GCOV frequency-A HH contrast and masks derived from NISAR quality and signal.",
        "method": {
            "calibration_quantile": FIT_QUANTILE,
            "threshold_rounding_step_db": THRESHOLD_STEP_DB,
            "gaussian_sigma_m": SIGMA_M,
            "gaussian_truncation_sigmas": TRUNCATE,
            "min_weighted_support": MIN_WEIGHTED_SUPPORT,
            "input_contrast_clamp_db": [-CLIP_DB, CLIP_DB],
            "cell_m": CELL_M,
            "parent_model": "models/oil.py",
            "fit_inputs": "All finite eligible Oman scores in the radar analysis crop. The cached crop extent was originally chosen around an independent proxy. Reference labels are used after the fit for descriptive evaluation.",
        },
        "fit": fit,
        "cases": results,
        "holdout_conclusion": (
            "Observed overlap is descriptive and does not validate oil material recognition."
            if holdout["overlap_cells"] else
            "No positive evidence of transfer to the held-out proxy under this frozen recognition rule."
        ),
        "reference_limit": "A recorded incident and a later or earlier remote-sensing outline have different evidentiary roles. The outlines are time-offset, non-exhaustive proxies. Outside-reference flags are unlabelled. Area and error rates for confirmed oil remain unestimated.",
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
