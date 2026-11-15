"""Calibrate a NISAR HH dark-water screening threshold against a positive-only proxy."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor, ImageDraw, ImageFont
from scipy.ndimage import binary_erosion, label


THRESHOLDS_DB = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0)
UNLABELLED_REVIEW_LIMIT = 0.01
SNR_MARGIN_DB = 3.0
MIN_CONNECTED_CELLS = 3
CELL_M = 60
AREA_PER_CELL_KM2 = CELL_M**2 / 1e6
BASELINE_DB = 3.0

OMAN = {
    "name": "Oman, Caroline Bezengi case",
    "stem": "oman",
    "directory": "outputs/nisar_oman/aug11_viirs_60m",
    "reference_path": "outputs/nisar_oman/Oman_Aug11_VIIRS_slick_cores.geojson",
    "reference_label": "Suomi NPP VIIRS bright-core optical interpretation",
    "reference_status": "independent_sensor_non_exhaustive_positive_proxy",
    "reference_url": "https://nasa-gibs.github.io/gibs-api-docs/",
    "time_separation_minutes": [319, 326],
    "reference_limit": "Non-exhaustive bright visible cores from an earlier VIIRS pass. Motion and changes in appearance during the several-hour gap are unresolved.",
}
UAE = {
    "name": "Umm Al Quwain, GEM NO. 2 case",
    "stem": "uae",
    "directory": "outputs/nisar_uae/gem_no_2_60m",
    "reference_path": "outputs/nisar_uae/UAE_Gem_2_Falcon_Eye_reference.geojson",
    "reference_label": "Falcon Eye outline digitized on independent Sentinel-1D VV",
    "reference_status": "independent_sensor_reported_perimeter_positive_proxy",
    "reference_url": "https://www.ess-uae.me/post/satellite-oil-spill-monitoring-uae-umm-al-quwain",
    "time_separation_minutes": [38, 40],
    "reference_limit": "Published analyst perimeter from a later radar pass. It was georeferenced against Sentinel-1D VV. Outline registration and surface displacement remain uncertain.",
}


@dataclass(frozen=True)
class Case:
    x: np.ndarray
    y: np.ndarray
    epsg: int
    delta: np.ndarray
    snr: np.ndarray
    open_water: np.ndarray
    quality: np.ndarray
    local_support: np.ndarray
    eligible: np.ndarray
    scored: np.ndarray
    proxy: np.ndarray
    source: dict


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_case(root: Path, config: dict) -> Case:
    directory = root / config["directory"]
    cache_path = directory / "screen_layers.npz"
    provenance_path = directory / "provenance.json"
    reference_path = root / config["reference_path"]
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    reference = json.loads(reference_path.read_text(encoding="utf-8"))
    if (provenance["frequency"], provenance["polarization"], provenance["cell_m"]) != ("A", "HH", CELL_M):
        raise ValueError(f"{config['name']} must contain the 60 m NISAR frequency-A HH screen.")
    if provenance["noise_margin_db"] != SNR_MARGIN_DB or provenance["minimum_connected_cells"] != MIN_CONNECTED_CELLS:
        raise ValueError("Saved feature preparation differs from the calibration contract.")
    granules = provenance["granules"]
    if not granules or any(not g["id"].startswith("NISAR_L2_PR_GCOV_") for g in granules):
        raise ValueError("Missing NISAR GCOV product provenance.")
    features = reference["features"]
    if len(features) != 1 or features[0]["geometry"]["type"] not in {"Polygon", "MultiPolygon"}:
        raise ValueError("Expected a single independently prepared reference outline.")
    with np.load(cache_path, allow_pickle=False) as cache:
        x, y = cache["x"], cache["y"]
        epsg = int(cache["output_epsg"])
        if x.ndim != 1 or y.ndim != 1 or min(x.size, y.size) < 2:
            raise ValueError("Missing projected grid axes.")
        if epsg != provenance["projection"] or not np.allclose(np.diff(x), CELL_M) or not np.allclose(np.diff(y), -CELL_M):
            raise ValueError("Grid projection or spacing differs from the saved provenance.")
        delta, snr, ceiling = (cache[k] for k in ("delta", "snr", "ceiling"))
        masks = {k: cache[k] for k in ("quality", "open_water", "local_support", "reference", "scored", "eligible")}
    shape = (y.size, x.size)
    if any(a.shape != shape for a in (delta, snr, ceiling, *masks.values())):
        raise ValueError("Saved layers do not share the same projected grid.")
    if any(a.dtype != bool for a in masks.values()):
        raise ValueError("Saved quality and label layers must be Boolean.")
    eligible = masks["quality"] & masks["open_water"] & masks["local_support"] & (snr >= SNR_MARGIN_DB)
    if not np.array_equal(masks["eligible"], eligible):
        raise ValueError("Could not reproduce the saved SNR and local reference support gate.")
    if not eligible.any() or not np.allclose(ceiling[eligible], (delta[eligible] + snr[eligible] - SNR_MARGIN_DB), atol=1e-5):
        raise ValueError("Saved contrast, SNR and reference ceiling are inconsistent.")
    if np.any(masks["reference"] & ~masks["scored"]):
        raise ValueError("The reference outline extends outside its review domain.")
    source = {
        **{key: config[key] for key in ("name", "reference_label", "reference_status", "reference_url", "time_separation_minutes", "reference_limit")},
        "acquisition": provenance["description"],
        "nisar_gcov_granules": granules,
        "epsg": epsg,
        "native_posting_m": provenance["native_posting_m"],
        "screen_grid_m": CELL_M,
        "reference_ring_radius_km": provenance["reference_outer_radius_km"],
        "region": provenance["score_region"],
        "grid_shape": list(shape),
        "input_sha256": {
            "feature_cache": sha256(cache_path),
            "feature_provenance": sha256(provenance_path),
            "reference_geometry": sha256(reference_path),
        },
        "reference_metadata": features[0]["properties"],
    }
    return Case(x, y, epsg, delta, snr, masks["open_water"], masks["quality"], masks["local_support"],
                eligible, masks["scored"], masks["reference"], source)


def candidates(case: Case, threshold_db: float) -> np.ndarray:
    """Apply the original four-neighbour connected-component filter to NISAR contrast."""
    hits = case.eligible & np.isfinite(case.delta) & (case.delta >= threshold_db)
    components, _ = label(hits)
    return hits & (np.bincount(components.ravel())[components] >= MIN_CONNECTED_CELLS)


def metrics(case: Case, flags: np.ndarray) -> dict:
    proxy = case.proxy & case.scored
    eligible_proxy = proxy & case.eligible
    unlabelled = case.scored & case.eligible & ~case.proxy
    proxy_cells = int(proxy.sum())
    eligible_cells = int(eligible_proxy.sum())
    matched = int((eligible_proxy & flags).sum())
    outside = int((unlabelled & flags).sum())
    unlabelled_cells = int(unlabelled.sum())
    return {
        "proxy_cells": proxy_cells,
        "proxy_km2": proxy_cells * AREA_PER_CELL_KM2,
        "proxy_outside_open_water_cells": int((proxy & ~case.open_water).sum()),
        "proxy_without_valid_radar_cells": int((proxy & case.open_water & ~case.quality).sum()),
        "proxy_without_snr_or_local_support_cells": int((proxy & case.open_water & case.quality & ~case.eligible).sum()),
        "eligible_proxy_cells": eligible_cells,
        "eligible_proxy_km2": eligible_cells * AREA_PER_CELL_KM2,
        "proxy_eligible_fraction": eligible_cells / proxy_cells if proxy_cells else None,
        "overlap_cells": matched,
        "overlap_km2": matched * AREA_PER_CELL_KM2,
        "eligible_proxy_overlap_fraction": matched / eligible_cells if eligible_cells else None,
        "eligible_unlabelled_cells": unlabelled_cells,
        "candidate_unlabelled_cells": outside,
        "candidate_unlabelled_km2": outside * AREA_PER_CELL_KM2,
        "eligible_unlabelled_candidate_fraction": outside / unlabelled_cells if unlabelled_cells else None,
        "known_negative_cells": 0,
        "false_positive_rate": None,
        "precision": None,
    }


def calibrate(case: Case) -> tuple[float, list[dict]]:
    sweep = []
    for threshold in THRESHOLDS_DB:
        row = metrics(case, candidates(case, threshold))
        burden = row["eligible_unlabelled_candidate_fraction"]
        row.update(threshold_db=threshold, within_review_limit=burden is not None and burden <= UNLABELLED_REVIEW_LIMIT)
        sweep.append(row)
    viable = [row for row in sweep if row["within_review_limit"] and row["eligible_proxy_cells"]]
    if not viable:
        raise ValueError("No calibration choice met the review limit with adequate proxy coverage.")
    selected = max(viable, key=lambda row: (row["overlap_cells"], -row["candidate_unlabelled_cells"], row["threshold_db"]))
    return selected["threshold_db"], sweep


def make_preview(path: Path, case: Case, flags: np.ndarray, threshold: float, result: dict) -> None:
    """Draw a compact spatial review map from the projected 60 m assessment cells."""
    states = np.zeros(case.eligible.shape, dtype=np.uint8)
    states[~case.open_water] = 1
    states[case.scored & case.open_water & ~case.eligible] = 2
    states[case.scored & case.eligible] = 3
    states[case.proxy & ~case.eligible] = 4
    states[case.scored & ~case.proxy & flags] = 5
    states[case.proxy & flags] = 6
    states[case.proxy & ~binary_erosion(case.proxy)] = 7
    palette = ("#2b5b6c", "#dad6ca", "#4a6168", "#417989", "#665779", "#ffc865", "#76e7b3", "#ff9dca")
    colours = np.asarray([ImageColor.getrgb(code) for code in palette], dtype=np.uint8)
    yy, xx = np.nonzero(case.scored)
    ys = slice(max(0, yy.min()-12), min(case.y.size, yy.max()+13))
    xs = slice(max(0, xx.min()-12), min(case.x.size, xx.max()+13))
    canvas = Image.new("RGB", (1000, 680), "#15252b")
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), case.source["name"], fill="white", font=ImageFont.load_default(size=23))
    draw.text((24, 51), f"NISAR L-band HH  |  60 m screen  |  contrast >= {threshold:g} dB", fill="#d4e3e6")
    raster = Image.fromarray(colours[states[ys, xs]])
    raster.thumbnail((610, 578), Image.Resampling.NEAREST)
    canvas.paste(raster, (24+(610-raster.width)//2, 80))
    legend = [
        (7, "Independent proxy boundary"), (6, "Candidate within proxy"),
        (5, "Candidate in unlabelled water"), (4, "Proxy pixels unassessed"),
        (3, "Eligible assessment water"), (2, "Water below coverage or signal gate"),
        (0, "Water outside assessment domain"), (1, "Land / coastal exclusion"),
    ]
    draw.text((658, 97), "Assessment states", fill="white", font=ImageFont.load_default(size=18))
    for i, (colour, title) in enumerate(legend):
        top = 125+i*25
        draw.rectangle((658, top, 674, top+16), fill=palette[colour])
        draw.text((684, top+3), title, fill="#e4eef0")
    draw.text((658, 355), f"North up | EPSG:{case.epsg}", fill="#d4e3e6")
    draw.text((658, 379), "Review area surrounds a positive-only proxy.", fill="#d4e3e6")
    draw.text((658, 401), "Unlabelled water has unknown material.", fill="#d4e3e6")
    draw.text((658, 456), "Quantified overlap", fill="white", font=ImageFont.load_default(size=18))
    draw.text((658, 491), f"{result['overlap_cells']:,} of {result['eligible_proxy_cells']:,} eligible proxy cells", fill="#e4eef0")
    draw.text((658, 514), f"{result['proxy_eligible_fraction']:.1%} of the proxy was eligible", fill="#e4eef0")
    canvas.save(path, optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True, help="Local dataset root containing the saved NISAR screens.")
    parser.add_argument("--output-dir", type=Path, required=True, help="Local review directory.")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    oman = load_case(args.data_root, OMAN)
    threshold, sweep = calibrate(oman)
    with (args.output_dir / "oman-calibration.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(sweep[0]))
        writer.writeheader()
        writer.writerows(sweep)
    print(f"Threshold fixed at {threshold:g} dB using Oman only.", flush=True)

    uae = load_case(args.data_root, UAE)
    case_results = {}
    for role, case, config in (("calibration", oman, OMAN), ("held_out", uae, UAE)):
        flags = candidates(case, threshold)
        preview_name = config["stem"] + "-screen.png"
        selected_result = metrics(case, flags)
        make_preview(args.output_dir / preview_name, case, flags, threshold, selected_result)
        case_results[role] = {
            "source": case.source,
            "preview": preview_name,
            "selected_rule": selected_result,
            "baseline_3_db_rule": metrics(case, candidates(case, BASELINE_DB)),
        }

    summary = {
        "schema_version": 1,
        "status": "completed_positive_proxy_calibration_and_independent_holdout",
        "interpretation": "Experimental NISAR dark-water screening. Proxy overlap is descriptive. Material classification and classification error rates require validated labels.",
        "model": {
            "predictor": "NISAR L-SAR GCOV frequency-A HH local contrast in gamma0 dB after noise correction where needed",
            "cell_m": CELL_M,
            "candidate_threshold_db": threshold,
            "signal_to_modelled_noise_margin_db": SNR_MARGIN_DB,
            "minimum_four_connected_cells": MIN_CONNECTED_CELLS,
            "eligibility": "Source quality, open water, bilateral local control and SNR gates from nisar_slick_validation.py",
            "fit_objective": "Maximise eligible Oman proxy overlap while flagging at most 1% of eligible unlabelled cells in the review buffer. Prefer fewer unlabelled flags on ties.",
            "threshold_grid_db": THRESHOLDS_DB,
            "max_unlabelled_review_fraction": UNLABELLED_REVIEW_LIMIT,
            "heldout_access": "Held-out UAE arrays loaded after threshold selection. Baseline 3 dB fixed from the original screen.",
            "baseline_threshold_db": BASELINE_DB,
        },
        "metrics_scope": "60 m grid cells in a bounded buffer around each independent reference. Proxy cells outside the quality, water or noise gates remain unassessed. Unlabelled cells include unknown positives and negatives.",
        "cases": case_results,
        "runtime": {"numpy": np.__version__},
        "review_state": "local_review_required",
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for role, result in case_results.items():
        row = result["selected_rule"]
        print(f"{role}: {row['overlap_cells']}/{row['eligible_proxy_cells']} eligible proxy cells overlap; "
              f"{row['eligible_proxy_cells']}/{row['proxy_cells']} proxy cells eligible.")


if __name__ == "__main__":
    main()
