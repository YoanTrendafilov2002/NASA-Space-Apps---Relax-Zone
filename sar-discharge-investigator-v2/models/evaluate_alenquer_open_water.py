"""Reproduce the Alenquer open-water comparison from retained evaluation inputs."""
from __future__ import annotations

import argparse
from hashlib import file_digest
import json
from pathlib import Path

import numpy as np

try:
    from .evaluate_wetland_v2 import compare_open_water
    from . import wetland_v2
except ImportError:
    from evaluate_wetland_v2 import compare_open_water
    import wetland_v2


DEFAULT_REPORT = Path(__file__).resolve().parents[1] / "results/recognition/wetlands/v2-alenquer.json"


def sha256(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def evaluate(report_path, inputs):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if sha256(inputs) != report["npz_sha256"]:
        raise ValueError("Evaluation assets do not match the archived receipt.")
    with np.load(inputs, allow_pickle=False) as source:
        arrays = {name: source[name] for name in wetland_v2.RADAR_FIELDS}
        reference, shore, core = (source[key] for key in ("reference", "coast_transition", "reference_core"))
    if (reference.shape != tuple(report["shape"]) or reference.dtype != np.uint8
            or not all(mask.shape == reference.shape and mask.dtype == np.bool_ for mask in (shore, core))
            or (shore & core).any() or ((shore | core) & (reference == 0)).any()):
        raise ValueError("The evaluation masks do not match the documented 60 m grid.")
    computed = wetland_v2.classify_open_water(arrays)
    return report | {
        "frozen_rule": wetland_v2.OPEN_WATER_PARAMETERS,
        "frozen_rule_sha256": sha256(Path(wetland_v2.__file__)),
        "all_labels": compare_open_water(reference, computed, shoreline=True),
        "cores": compare_open_water(np.where(core, reference, 0), computed),
        "near_shore": compare_open_water(np.where(shore, reference, 0), computed),
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    cli.add_argument("--inputs", type=Path)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--check", action="store_true", help="Require exact agreement with the archived evaluation.")
    args = cli.parse_args()
    inputs = args.inputs or args.report.with_name("v2-alenquer-inputs.npz")
    actual = evaluate(args.report, inputs)
    if args.check:
        archived = json.loads(args.report.read_text(encoding="utf-8"))
        fields = ("all_labels", "cores", "near_shore")
        if any(archived[key] != actual[key] for key in fields):
            raise ValueError("Recomputed results differ from the archived Alenquer comparison.")
    if args.output:
        args.output.write_text(json.dumps(actual, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    else:
        print(json.dumps({key: actual["all_labels"][key] for key in ("r6_common_support", "v2_common_support")}, indent=2))


if __name__ == "__main__":
    main()
