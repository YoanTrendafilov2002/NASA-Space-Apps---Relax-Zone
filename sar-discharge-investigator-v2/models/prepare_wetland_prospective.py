"""Prepare the Hasik open-water holdout with the workspace's audited optical crop tools."""

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from rasterio.transform import from_origin
from scipy.ndimage import distance_transform_edt


GRANULE = "NISAR_L2_PR_GCOV_027_150_D_080_2005_DHDH_A_20260811T144946_20260811T145009_P05023_N_P_J_001"
STUDY = {
    "scope": "Prospective coastal open-water comparison on a site and date untouched by this wetland model.",
    "site": "Hasik coast, Dhofar, Oman",
    "radar_asset": GRANULE,
    "radar_item": "https://search.asf.alaska.edu/#/?granule=" + GRANULE,
    "reference_item": "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/S2C_40QCE_20260812_0_L2A",
    "epsg": 32640,
    "native_bounds": (314340, 1927160, 320340, 1933160),
    "notes": "Fixed 6 km square around Hasik from published geography and the parent radar footprint. The nearest full-coverage Sentinel-2 opportunity is on the following morning. A coast can change between observations.",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radar", type=Path, required=True)
    parser.add_argument("--workspace-tools", type=Path, required=True,
                        help="Path to work/new-recognition-wetland containing the audited crop adapters")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.workspace_tools.resolve()))
    import prepare_uae_shore_holdout as extract
    from evaluate_external_burgas import optical_labels
    from fetch_burgas_reference import fetch

    extract.GRANULE = GRANULE
    extract.STUDY = STUDY
    inputs, source = extract.radar(args.radar)
    if not (args.output / "source.json").is_file():
        fetch(STUDY, args.output)
    reference, _, optical = optical_labels(args.output, STUDY)
    x, _, _, y = STUDY["native_bounds"]
    transform = from_origin(x, y, 60, 60)
    water, other, unknown = reference == 1, reference == 2, reference == 0
    coast = ((water & (distance_transform_edt(~other, sampling=60) <= 120))
             | (other & (distance_transform_edt(~water, sampling=60) <= 120)))
    core = (reference != 0) & ~coast & (distance_transform_edt(~unknown, sampling=60) > 120)
    archive = args.output / "inputs.npz"
    np.savez_compressed(archive, **inputs, reference=reference, reference_core=core, coast_transition=coast)
    report = {
        **STUDY, "radar": source, "optical": optical["acquired_at"],
        "optical_reference_sha256": extract.digest(args.output / "source.json"),
        "analysis_affine": list(transform)[:6], "analysis_shape": list(reference.shape),
        "npz_sha256": extract.digest(archive),
        "counts": {"water": int(water.sum()), "nonwater": int(other.sum()), "shore": int(coast.sum()),
                   "core_water": int((water & core).sum()), "core_other": int((other & core).sum())},
    }
    (args.output / "prepared.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
