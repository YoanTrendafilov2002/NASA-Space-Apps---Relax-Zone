"""Build the portable, explicitly allowlisted SARtainty archive."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


APP = Path(__file__).resolve().parents[1]
ZIP_ROOT = "SARtainty"
FIXED_TIMESTAMP = (2026, 1, 1, 0, 0, 0)
CATALOGUES = (
    "data/non-sentinel-nisar.json",
    "data/nisar-burgas.json",
    "data/nisar-uae-coastal-review.json",
    "data/nisar-everglades.json",
    "data/nisar-maranon.json",
    "data/nisar-holdouts.json",
    "data/nisar-wetland-holdout.json",
    "data/nisar-hormuz-sep10-reference.json",
)
CHANGE_MANIFEST = "data/nisar-change-layers.json"
RECOGNITION_MANIFEST = "data/nisar-detections.json"
RUNTIME_INPUTS = "data/model-inputs/runtime-recognition.json"
RUNTIME_ARRAYS = (
    "nisar-curuai-20260813.npz",
    "nisar-curuai-20261012.npz",
    "nisar-eloi-20261012.npz",
    "nisar-eugene-20260827.npz",
    "nisar-hasik-20260811.npz",
    "nisar-uae-coast-20261021.npz",
)
VENDOR_MANIFEST = "vendor/sources.json"
VENDOR_FILES = (
    "Leaflet-LICENSE.txt",
    "fonts.css",
    "leaflet.css",
    "leaflet.js",
    "montagu-slab.woff2",
    "montaguslab-OFL.txt",
    "source-sans-3.woff2",
    "sourcesans3-OFL.txt",
    "images/layers.png",
    "images/layers-2x.png",
    "images/marker-icon.png",
    "images/marker-icon-2x.png",
    "images/marker-shadow.png",
)
FILE_ALLOWLIST = (
    "QUICKSTART.md",
    "NISAR_DATA_REVIEW.md",
    "launch_relax_zone.py",
    "requirements-explorer.txt",
    "index.html",
    "evidence.html",
    "evidence.css",
    "explorer.css",
    "explorer.js",
    "explorer-manual.js",
    "explorer_server.py",
    "explorer_analysis.py",
    "explorer_atbd.py",
    "explorer_catalogue.py",
    "explorer_glossary.py",
    "explorer_preview_analysis.py",
    "explorer_settings.py",
    "explorer_synopsis.py",
    "explorer_assistant.py",
    "matrix_answers.py",
    "matrix_intent.py",
    "preview_image_analysis.py",
    "runtime_measurement.py",
    "runtime_recognition.py",
    *CATALOGUES,
    CHANGE_MANIFEST,
    RECOGNITION_MANIFEST,
    "data/basemap-land.geojson",
    "data/basemap-lakes.geojson",
    "data/glossary.json",
    "data/matrix-assistant-model.json",
    "data/matrix-assistant-training.json",
    "data/nisar-experiments.json",
    "data/nisar-ecosystems-atbd.json",
    "data/nisar-mission-atbd.json",
    "data/nisar-use-case-settings.json",
    "data/nisar-measurements.json",
    "data/nisar-reference-context.json",
    "data/nisar-time-series.json",
    "data/nisar-wetland-context.json",
    RUNTIME_INPUTS,
    *(f"data/model-inputs/{name}" for name in RUNTIME_ARRAYS),
    VENDOR_MANIFEST,
    *(f"vendor/{name}" for name in VENDOR_FILES),
    "models/analyze_nisar_matrix.py",
    "models/audit_nepal_qgis.py",
    "models/build_nisar_change_layers.py",
    "models/build_nisar_holdout_assets.py",
    "models/build_nisar_recognition_layers.py",
    "models/build_nisar_time_series.py",
    "models/build_hasik_wetland_assets.py",
    "models/build_wetland_habitat_reference.py",
    "models/evaluate_wetland_v2.py",
    "models/evaluate_alenquer_open_water.py",
    "models/export_relax_zone.py",
    "models/train_matrix_assistant.py",
    "models/oil.py",
    "models/oil_recognition.py",
    "models/oil_observed_holdouts.py",
    "models/publish_recognition_review.py",
    "models/publish_uae_habitat_water_reference.py",
    "models/wetland.py",
    "models/wetland_recognition.py",
    "models/wetland_v2.py",
    "results/recognition/method-qa/nepal.json",
    "results/recognition/method-qa/novice-standalone-r3.json",
    "results/recognition/oil/summary.json",
    "results/recognition/oil/external/README.md",
    "results/recognition/oil/maranon-qgis-review.json",
    "results/recognition/oil/observed-return-holdouts.json",
    "results/recognition/oil/strict-gate-audit.json",
    "results/recognition/wetlands/frozen-models.json",
    "results/recognition/wetlands/baseline/frozen-model.json",
    "results/recognition/wetlands/protocol.json",
    "results/recognition/wetlands/research-report.json",
    "results/recognition/wetlands/summary.json",
    "results/recognition/wetlands/transfer-error-audit.json",
    "results/recognition/wetlands/everglades/research-report.json",
    "results/recognition/wetlands/curuai-october-agreement.json",
    "results/recognition/wetlands/site-transfer.json",
    "results/recognition/wetlands/v2-validation.json",
    "results/recognition/wetlands/v2-alenquer.json",
    "results/recognition/wetlands/v2-alenquer-inputs.npz",
    "nisar_tool/__init__.py",
    "nisar_tool/__main__.py",
    "nisar_tool/client.py",
    "nisar_tool/errors.py",
    "nisar_tool/harmony.py",
    "nisar_tool/runtime.py",
    "nisar_tool/server.py",
    "nisar_tool/settings.py",
    "nisar_tool/transport.py",
    "tests/check_hosted_explorer.py",
    "tests/test_explorer.py",
    "tests/test_ecosystems_atbd.py",
    "tests/test_use_case_settings.py",
    "tests/test_glossary.py",
    "tests/test_reviews.py",
    "tests/test_runtime_measurement.py",
    "tests/test_runtime_recognition.py",
    "tests/test_preview_image_analysis.py",
    "tests/test_explorer_preview_analysis.py",
    "tests/test_dataset_synopsis_chat.py",
)
RASTER_NAMES = (
    "nisar-burgas-20260712-a-hh",
    "nisar-curuai-20260813-hh",
    "nisar-curuai-20260813-hv",
    "nisar-curuai-20261012-hh",
    "nisar-curuai-20261012-hv",
    "nisar-uae-20261021-hh",
    "nisar-uae-20261021-hv",
    "nisar-uae-coast-20261009-hh",
    "nisar-uae-coast-20261009-hv",
    "nisar-uae-coast-20261021-hh",
    "nisar-uae-coast-20261102-hh",
    "nisar-uae-coast-20261102-hv",
    "nisar-everglades-20261027-hh",
    "nisar-everglades-20261027-hv",
    "nisar-maranon-20260224-hh",
    "nisar-maranon-20260224-hv",
    "nisar-maranon-20260308-hh",
    "nisar-maranon-20260308-hv",
    "nisar-qeshm-20260717-hh",
    "nisar-qeshm-20260810-hh",
    "nisar-qeshm-20260810-hv",
    "nisar-qeshm-20260822-hh",
    "nisar-eugene-20260827-hh",
    "nisar-eloi-20261012-hh",
    "nisar-eloi-20261012-hv",
    "nisar-atanasovsko-20260712-hh",
    "nisar-atanasovsko-20260712-hv",
    "nisar-hasik-20260811-hh",
    "nisar-hasik-20260811-hv",
)
CHANGE_ALLOWLIST = frozenset(
    f"data/{folder}/nisar-{site}-change-{channel}.{extension}"
    for site in ("curuai", "maranon")
    for channel in ("hh", "hv")
    for folder, extension in (("sar", "png"), ("science", "tif"))
)
RASTER_ALLOWLIST = frozenset(
    f"data/{folder}/{name}.{extension}"
    for name in RASTER_NAMES
    for folder, extension in (("sar", "png"), ("science", "tif"))
)
RECOGNITION_ALLOWLIST = frozenset({
    "data/science/nisar-curuai-water-agreement-20261012-hh.tif",
    "data/science/nisar-eugene-observed-review-20260827-hh.tif",
})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_allowed(path: str) -> bytes:
    candidate = APP / path
    if not candidate.is_file() or candidate.is_symlink() or not candidate.resolve().is_relative_to(APP):
        raise ValueError(f"Missing or unsafe package file: {path}")
    if not 0 < candidate.stat().st_size <= 50_000_000:
        raise ValueError(f"Unexpected package file size: {path}")
    return candidate.read_bytes()


def validate_artifacts(files: dict[str, bytes]) -> None:
    raster_refs = set()
    for name in CATALOGUES:
        manifest = json.loads(files[name].decode("utf-8-sig"))
        for row in manifest["observations"]:
            if not row["product_id"].startswith("NISAR_"):
                raise ValueError(f"Non-NISAR observation in {name}: {row['id']}")
            for channel in row["channels"].values():
                if channel.get("status") != "validated":
                    if channel.get("image_url") or channel.get("science_url"):
                        raise ValueError(f"Unvalidated prepared asset for {row['id']}")
                    continue
                for kind in ("image", "science"):
                    target = "data" + channel[f"{kind}_url"]
                    if target not in RASTER_ALLOWLIST or target in raster_refs:
                        raise ValueError(f"Unexpected or duplicate prepared asset: {target}")
                    legacy_size = "image_bytes" if kind == "image" else "source_bytes"
                    size = channel.get(f"{kind}_size_bytes", channel.get(legacy_size))
                    if len(files[target]) != size or digest(files[target]) != channel[f"{kind}_sha256"]:
                        raise ValueError(f"Prepared asset integrity mismatch: {target}")
                    raster_refs.add(target)
    if raster_refs != RASTER_ALLOWLIST:
        raise ValueError("The catalogue and allowlisted scene assets differ.")

    change_refs = set()
    for layer in json.loads(files[CHANGE_MANIFEST])["layers"]:
        for kind in ("image", "science"):
            target = "data" + layer[f"{kind}_url"]
            if target not in CHANGE_ALLOWLIST or target in change_refs:
                raise ValueError(f"Unexpected or duplicate change asset: {target}")
            if len(files[target]) != layer[f"{kind}_size_bytes"] or digest(files[target]) != layer[f"{kind}_sha256"]:
                raise ValueError(f"Change asset integrity mismatch: {target}")
            change_refs.add(target)
    if change_refs != CHANGE_ALLOWLIST:
        raise ValueError("The change catalogue and allowlisted overlays differ.")

    recognition_refs = set()
    for layer in json.loads(files[RECOGNITION_MANIFEST])["layers"]:
        for kind in ("image", "science"):
            if kind + "_url" not in layer:
                continue
            target = "data" + layer[f"{kind}_url"]
            if target not in RECOGNITION_ALLOWLIST or target in recognition_refs:
                raise ValueError(f"Unexpected or duplicate recognition asset: {target}")
            if len(files[target]) != layer[f"{kind}_size_bytes"] or digest(files[target]) != layer[f"{kind}_sha256"]:
                raise ValueError(f"Recognition asset integrity mismatch: {target}")
            recognition_refs.add(target)
    if recognition_refs != RECOGNITION_ALLOWLIST:
        raise ValueError("The recognition catalogue and allowlisted downloads differ.")

    runtime_rows = json.loads(files[RUNTIME_INPUTS])["rows"]
    if len(runtime_rows) != len(RUNTIME_ARRAYS) or {row["file"] for row in runtime_rows.values()} != set(RUNTIME_ARRAYS):
        raise ValueError("The runtime model inputs and allowlisted crops differ.")
    for row in runtime_rows.values():
        source = files[f"data/model-inputs/{row['file']}"]
        if len(source) != row["size_bytes"] or digest(source) != row["sha256"]:
            raise ValueError("A runtime model crop does not match its receipt.")
        if digest(files[f"data/{row['map_science_path']}"]) != row["map_science_sha256"]:
            raise ValueError("A runtime model crop does not match its parent scene receipt.")
    for row in json.loads(files[RUNTIME_INPUTS])["models"].values():
        if digest(files[row["path"]]) != row["sha256"]:
            raise ValueError("A frozen runtime model does not match its receipt.")
    screen = json.loads(files[RUNTIME_INPUTS])["wetland_screen"]
    if digest(files[screen["path"]]) != screen["sha256"] or digest(files[screen["validation_path"]]) != screen["validation_sha256"]:
        raise ValueError("The wetland screen or its validation report does not match its receipt.")

    vendor = json.loads(files[VENDOR_MANIFEST])
    if set(vendor) != set(VENDOR_FILES):
        raise ValueError("Vendor source metadata and allowlisted dependencies differ.")
    for name in VENDOR_FILES:
        if digest(files[f"vendor/{name}"]) != vendor[name]["sha256"]:
            raise ValueError(f"Vendor asset checksum mismatch: {name}")

    measurement_sources = json.loads(files["data/nisar-measurements.json"])["source_manifests_sha256"]
    if set(measurement_sources) != {Path(name).name for name in CATALOGUES}:
        raise ValueError("Rerun matrix measurements for all included catalogue manifests.")
    for name in CATALOGUES:
        if measurement_sources[Path(name).name] != digest(files[name]):
            raise ValueError(f"Rerun matrix measurements before packaging the updated catalogue: {name}")
    model = json.loads(files["data/matrix-assistant-model.json"])
    if model["training_sha256"] != digest(files["data/matrix-assistant-training.json"]):
        raise ValueError("Rerun the matrix router trainer before packaging the changed training data.")
    if not model["scores"]["test"]["n"]:
        raise ValueError("The fitted router must carry its held-out evaluation.")


def write_member(archive: ZipFile, path: str, data: bytes) -> None:
    info = ZipInfo(f"{ZIP_ROOT}/{path}", date_time=FIXED_TIMESTAMP)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data, compress_type=ZIP_DEFLATED, compresslevel=6)


def build(output: Path) -> None:
    checksum_path = output.with_suffix(output.suffix + ".sha256")
    if output.exists() or checksum_path.exists():
        raise FileExistsError(f"Choose a new archive name: {output}")
    files = {name: read_allowed(name) for name in sorted((*FILE_ALLOWLIST, *RASTER_ALLOWLIST, *CHANGE_ALLOWLIST, *RECOGNITION_ALLOWLIST))}
    validate_artifacts(files)
    manifest = {name: {"bytes": len(data), "sha256": digest(data)} for name, data in files.items()}
    manifest_bytes = (json.dumps({"schema_version": 1, "files": manifest}, indent=2) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".relax-zone-", suffix=".zip", delete=False) as handle:
        staging = Path(handle.name)
    try:
        with ZipFile(staging, "w") as archive:
            for name, data in files.items():
                write_member(archive, name, data)
            write_member(archive, "portable-manifest.json", manifest_bytes)
        with ZipFile(staging) as archive:
            expected = {f"{ZIP_ROOT}/{name}" for name in (*files, "portable-manifest.json")}
            if set(archive.namelist()) != expected or archive.testzip() is not None:
                raise ValueError("Archive membership or integrity verification failed.")
        staging.replace(output)
    finally:
        staging.unlink(missing_ok=True)
    checksum = digest(output.read_bytes())
    checksum_path.write_text(f"{checksum} *{output.name}\n", encoding="ascii", newline="\n")
    print(f"{output} ({output.stat().st_size:,} bytes, {len(files) + 1} files)")
    print(f"SHA-256 {checksum}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=APP.parent.parent / "sartainty-agent-r9.zip")
    args = parser.parse_args()
    build(args.output.resolve())


if __name__ == "__main__":
    main()
