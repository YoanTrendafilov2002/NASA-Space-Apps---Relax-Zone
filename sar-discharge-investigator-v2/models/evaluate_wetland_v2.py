"""Compare frozen NISAR water screens on an independently labelled grid."""

import argparse
from hashlib import file_digest
import json
from pathlib import Path

import numpy as np
from scipy.ndimage import binary_dilation

try:
    from . import wetland_v2
except ImportError:
    import wetland_v2


def _digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def _ratio(num, den):
    return num / den if den else None


def _sum(mask):
    return int(np.count_nonzero(mask))


def score(reference, support, flags):
    """Score radar flags against 0 withheld, 1 water, 2 other labels."""
    if reference.shape != support.shape or not np.isin(reference, (0, 1, 2)).all():
        raise ValueError("Reference must align to the evaluated grid and use classes 0, 1 and 2.")
    known = reference > 0
    eligible = known & support
    water = reference == 1
    tp, fp, fn, tn = (_sum(eligible & condition) for condition in
                      (water & flags, ~water & flags, water & ~flags, ~water & ~flags))
    return {
        "total_cells": int(support.size), "radar_assessed_cells": _sum(support), "radar_flags": _sum(support & flags),
        "reference_cells": _sum(known), "paired_cells": _sum(eligible), "reference_coverage": _ratio(_sum(eligible), _sum(known)),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn),
        "iou": _ratio(tp, tp + fp + fn), "false_positive_rate": _ratio(fp, fp + tn),
        "withheld_reference_water": _sum(known & water & ~support), "withheld_reference_other": _sum(known & ~water & ~support),
        "positive_and_negative_support": bool(tp + fn and fp + tn),
    }


def _edge_score(reference, support, shore):
    water, other = reference == 1, reference == 2
    reference_edge = support & ((water & binary_dilation(other)) | (other & binary_dilation(water)))
    near_valid_edge = binary_dilation(reference_edge, iterations=1)
    near_screen_transition = binary_dilation(shore, iterations=1)
    screened_near_edge = shore & near_valid_edge
    reference_near_flag = reference_edge & near_screen_transition
    assessable_transition = shore & binary_dilation(water | other)
    return {
        "reference_boundary_cells": _sum(reference_edge), "transition_cells": _sum(shore),
        "transition_cells_near_label": _sum(assessable_transition),
        "transition_cells_within_one_cell_of_label_boundary": _sum(screened_near_edge),
        "reference_boundary_cells_within_one_cell_of_transition": _sum(reference_near_flag),
        "scope": "A one-grid-cell adjacency diagnostic where both reference classes are known. Mixed edges are not labelled as confirmed shorelines.",
    }


def compare_open_water(reference, results, *, shoreline=False):
    """Compare methods on identical evaluable pixels and report coverage separately."""
    shared = results["support"] & results["r6_support"]
    return {
        "r6_all_supported": score(reference, results["r6_support"], results["r6_water"]),
        "v2_all_supported": score(reference, results["support"], results["open_water"]),
        "r6_common_support": score(reference, shared, results["r6_water"]),
        "v2_common_support": score(reference, shared, results["open_water"]),
        "uncertain_transition_cells": _sum(results["shore"]),
        "boundary_diagnostic": _edge_score(reference, shared, results["shore"]) if shoreline else None,
    }


def _aligned_curuai_reference(path, shape, affine, epsg):
    """Rebuild the existing 60 m labels from nine aligned 20 m optical postings."""
    import rasterio
    from rasterio.transform import Affine
    from rasterio.warp import reproject, Resampling

    transform = Affine(*affine)
    if (transform.a, transform.b, transform.d, transform.e) != (60, 0, 0, -60):
        raise ValueError("The Curuai reference requires a north-up 60 m target.")
    fine = np.zeros((shape[0] * 3, shape[1] * 3), dtype=np.uint8)
    with rasterio.open(path) as source:
        reproject(rasterio.band(source, 1), fine, dst_transform=transform * Affine.scale(1 / 3),
                  dst_crs=f"EPSG:{epsg}", resampling=Resampling.nearest)
    grid = np.zeros(shape, dtype=np.uint8)
    for older_code, reference_code in ((2, 1), (1, 2)):
        count = (fine == older_code).reshape(shape[0], 3, shape[1], 3).sum(axis=(1, 3))
        grid[count >= 8] = reference_code
    return grid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radar", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--reference-tiff", type=Path, help="Optional Curuai 20 m optical proxy, regridded to radar")
    parser.add_argument("--case", required=True)
    parser.add_argument("--scope", choices=("retrospective", "prospective", "proxy-only"), required=True)
    parser.add_argument("--shoreline", action="store_true", help="Report the nearby reference-edge diagnostic")
    parser.add_argument("--reference-core", action="store_true", help="Exclude pre-marked coastal and unclear edges from the scored reference")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.reference_core and args.shoreline:
        parser.error("Score reference edges or restrict the score to the clear core.")
    metadata = json.loads(args.provenance.read_text(encoding="utf-8-sig"))
    source = metadata.get("rows", {}).get(args.case, metadata)
    with np.load(args.radar, allow_pickle=False) as arrays:
        inputs = {field: arrays[field] for field in wetland_v2.RADAR_FIELDS}
        shape = inputs["hh_power"].shape
        reference = (arrays["reference"] if args.reference_tiff is None else
                     _aligned_curuai_reference(args.reference_tiff, shape, source["affine"], source["epsg"]))
        scope = arrays["reference_core"] if args.reference_core else np.ones(shape, dtype=bool)
        habitat = arrays["habitat"] if "habitat" in arrays else None
    if scope.dtype != np.bool_ or scope.shape != shape:
        raise ValueError("Reference scope must be a Boolean array aligned to the radar grid.")
    analysis = wetland_v2.classify_open_water(inputs)
    report = {
        "case": args.case, "assessment_scope": args.scope,
        "scope_definition": {
            "retrospective": "Previously examined reference and candidate development context.",
            "prospective": "Candidate rule frozen before these independent comparison labels were examined.",
            "proxy-only": "Independent reference supports only a restricted proxy comparison."
        }[args.scope],
        "data": source, "input_sha256": _digest(args.radar),
        "reference_sha256": _digest(args.reference_tiff) if args.reference_tiff else _digest(args.radar),
        "evaluation_code_sha256": _digest(Path(__file__)),
        "screen_code_sha256": _digest(Path(wetland_v2.__file__)),
        "open_water_parameters": wetland_v2.OPEN_WATER_PARAMETERS,
        "paired_flooded_vegetation_parameters": wetland_v2.FLOODED_VEGETATION_PARAMETERS,
        "reference_mask": "registered clear reference core" if args.reference_core else "all classified reference cells",
        "open_water": compare_open_water(np.where(scope, reference, 0), analysis, shoreline=args.shoreline),
        "flooded_vegetation": {
            "status": "unassessed",
            "reason": "This comparison uses exposed-water labels. Evaluating submerged vegetation requires an independent inundation reference."
        }
    }
    if habitat is not None:
        if habitat.dtype != np.bool_ or habitat.shape != shape:
            raise ValueError("Habitat inventory must be a Boolean array aligned to the radar grid.")
        report["habitat_context"] = {
            "inventory_cells": _sum(habitat),
            "optical_water_in_inventory": _sum((reference == 1) & habitat),
            "r6_flags_in_inventory": _sum(analysis["r6_water"] & habitat),
            "v2_flags_in_inventory": _sum(analysis["open_water"] & habitat),
            "scope": "Inventory overlap is descriptive context. The inventory gives habitat extent and does not measure flooding underneath vegetation."
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
