"""Publish the verified Hasik NISAR test crop and unlabelled runtime inputs."""

import argparse
import json
from pathlib import Path

import h5py
import numpy as np
from rasterio.transform import Affine, array_bounds, from_origin
from rasterio.warp import transform_bounds, transform_geom
from shapely.geometry import box, mapping, shape

from build_nisar_holdout_assets import export_channel
from build_nisar_map_assets import GRID, IDENT, ROOT, WGS84, digest, provider_metadata
from prepare_wetland_prospective import GRANULE, STUDY
from wetland_v2 import RADAR_FIELDS, classify_open_water


OBSERVATION = "nisar-hasik-20260811"
SITE = "hasik-nisar"
SOURCE = OBSERVATION + "-source"
REFERENCE = "nisar-hasik-optical-20260812"
DAY = "2026-08-11"


def _save_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def _native(source, receipt):
    col, row, width, height = receipt["radar"]["source_window"]
    window = np.s_[row:row + height, col:col + width]
    transform = from_origin(STUDY["native_bounds"][0], STUDY["native_bounds"][3], 20, 20)
    with h5py.File(source) as product:
        ident, grid = product[IDENT], product[GRID]
        params = product[f"{ROOT}/metadata/processingInformation/parameters"]
        if (ident["granuleId"][()].decode() != GRANULE or ident["radarBand"][()] != b"L"
                or int(grid["projection"][()]) != STUDY["epsg"]
                or params["rtc/outputBackscatterNormalizationConvention"][()] != b"gamma0"
                or params["noiseCorrectionApplied"][()].decode().lower() != "false"
                or not np.array_equal(grid["xCoordinates"][window[1]], transform.c + 20 * (np.arange(width) + .5))
                or not np.array_equal(grid["yCoordinates"][window[0]], transform.f - 20 * (np.arange(height) + .5))):
            raise ValueError("Hasik native HDF5 does not match the registered holdout.")
        mask, exceptions, looks = (grid[key][window] for key in ("mask", "inputDataExceptionMask", "numberOfLooks"))
        if exceptions.dtype not in (np.dtype("uint8"), np.dtype("uint16")):
            raise ValueError("Unsupported Hasik exception flags.")
        description = grid["inputDataExceptionMask"].attrs["description"].decode()
        if exceptions.dtype.itemsize == 2 and not all(term in description for term in ("256:HH", "512:HV")):
            raise ValueError("Hasik quality metadata does not define the expected focus bits.")
        base = ((exceptions & 255) == 0) & (mask >= 1) & (mask <= grid["numberOfSubSwaths"][()])
        base &= np.isfinite(looks) & (looks > 0)
        channels = {}
        for pol, bit in (("HH", 256), ("HV", 512)):
            dataset = grid[pol * 2]
            if dataset.dtype != np.float32 or dataset.attrs["units"] != b"1":
                raise ValueError("Expected native Float32 unitless gamma-zero power.")
            values = dataset[window]
            valid = base & np.isfinite(values) & (values >= 0)
            if exceptions.dtype.itemsize == 2:
                valid &= (exceptions & bit) != 0
            channels[pol] = np.where(valid, values, np.float32(np.nan))
        metadata = {"orbit": int(ident["trackNumber"][()]), "frame": int(ident["frameNumber"][()]),
                    "direction": ident["orbitPassDirection"][()].decode().upper(),
                    "look_direction": ident["lookDirection"][()].decode(),
                    "acquired_at": ident["zeroDopplerStartTime"][()].decode().split(".")[0] + "Z",
                    "acquisition_end": ident["zeroDopplerEndTime"][()].decode() + "Z",
                    "processing_version": ident["productVersion"][()].decode()}
    return channels, transform, metadata


def build(source, holdout, destination, output_row):
    receipt = json.loads((holdout / "prepared.json").read_text(encoding="utf-8"))
    trial = holdout / "inputs.npz"
    if digest(source) != receipt["radar"]["source_sha256"] or digest(trial) != receipt["npz_sha256"]:
        raise ValueError("Source HDF5 or holdout arrays no longer match their preparation receipts.")
    source_query, footprint, properties = provider_metadata(GRANULE, source)
    powers, transform, meta = _native(source, receipt)
    epsg = STUDY["epsg"]
    with np.load(trial, allow_pickle=False) as dataset:
        inputs = {key: dataset[key] for key in RADAR_FIELDS}
    analysis_affine = Affine(*receipt["analysis_affine"])
    if transform * Affine.scale(3) != analysis_affine:
        raise ValueError("Hasik 20 and 60 m cells do not share a grid.")
    for pol, raster in powers.items():
        key = pol.lower()
        height, width = inputs[key + "_power"].shape
        aggregated = raster.reshape(height, 3, width, 3).mean(axis=(1, 3))
        qualified = inputs[key + "_quality"]
        if not np.array_equal(aggregated[qualified], inputs[key + "_power"][qualified]):
            raise ValueError("Published native power does not reproduce the held-out study crop.")
    bounds = array_bounds(*powers["HH"].shape, transform)
    wgs_bounds = list(transform_bounds(f"EPSG:{epsg}", WGS84, *bounds))
    window = shape(transform_geom(f"EPSG:{epsg}", WGS84, mapping(box(*bounds))))
    geometry = shape(footprint).intersection(window)
    if geometry.is_empty or geometry.area / window.area < .99:
        raise ValueError("Published crop differs from the ASF granule footprint.")
    qa = {"quality_rule": "Valid source subswath, positive looks, clean exception flags and finite nonnegative power.",
          "noise_correction_applied": False, "nesz_available": True}
    for folder in ("sar", "science", "model-inputs"):
        (destination / folder).mkdir(parents=True, exist_ok=True)
    channels = {pol: export_channel(destination, OBSERVATION, GRANULE, epsg, 20, transform, pol, array, qa, SOURCE)
                for pol, array in powers.items()}
    asset = destination / "model-inputs" / f"{OBSERVATION}.npz"
    np.savez_compressed(asset, **inputs)
    classified = classify_open_water(inputs)
    height, width = map(int, inputs["hh_power"].shape)
    runtime = {
        "map_science_path": channels["HH"]["science_url"].lstrip("/"),
        "map_science_sha256": channels["HH"]["science_sha256"], "product_id": GRANULE, "site_id": SITE,
        "evidence_ids": ["asf-nisar-gcov", SOURCE, REFERENCE], "mode": "wetland",
        "epsg": epsg, "affine": receipt["analysis_affine"], "shape": [height, width], "crop": [0, height, 0, width],
        "counts_expected": {"assessed_cells": int(classified["support"].sum()),
                            "flagged_cells": int(classified["open_water"].sum()),
                            "shore_uncertain_cells": int(classified["shore"].sum())},
        "hdf5_source_filename": source.name, "original_source_window": receipt["radar"]["source_window"],
        "noise_correction_applied": False, "qa": qa["quality_rule"] + " Spatial noise used on the 60 m grid.",
        "aggregation": "Arithmetic mean of 3 by 3 calibrated native linear-power samples.",
        "file": asset.name, "sha256": digest(asset), "size_bytes": asset.stat().st_size,
    }
    name = "Hasik coast wetland test, Oman"
    observation = {
        "id": OBSERVATION, "site_id": SITE, "product_id": GRANULE, "acquisition_id": GRANULE,
        "mission": "nisar", "provider": "NASA ASF DAAC", "product": "NISAR GCOV", "product_type": "GCOV",
        "sensor": "L-SAR", "wavelength": "L", "frequency": "A",
        "collection": "NISAR_L2_GCOV_PROVISIONAL_V1", "collection_concept_id": "C2854338529-ASF",
        "mode": "Frequency A · dual polarization", "maturity": "provisional", "geometry": mapping(geometry),
        "source_geometry": footprint, "geometry_kind": "local_coverage_window", **meta, "noise_correction_applied": False,
        "evidence_id": SOURCE, "reference_evidence_id": REFERENCE, "channels": channels, "source_url": source_query,
        "source_requires_earthdata": True, "science_requires_earthdata": False,
    }
    prepared = {
        "schema_version": 1,
        "sites": [{"id": SITE, "name": name, "topic": "wetlands", "aliases": ["Hasik", "Hasik coast", "Dhofar"],
                   "bbox": wgs_bounds, "start": DAY, "end": DAY, "evidence_id": REFERENCE,
                   "geometry": mapping(geometry), "geometry_kind": "study_area"}],
        "observations": [observation],
        "evidence": [
            {"id": SOURCE, "title": "NASA ASF DAAC · Hasik coast · 11 August 2026",
             "kind": "Native GCOV HDF5", "url": properties["url"], "source_id": GRANULE,
             "source_filename": source.name, "source_sha256": receipt["radar"]["source_sha256"],
             "provider_size_bytes": source.stat().st_size, "source_window": receipt["radar"]["source_window"],
             "catalogue_url": source_query, "documentation_url": "https://nisar-docs.asf.alaska.edu/gcov/",
             "notes": "This 6 km crop preserves native 20 m HH and HV power. Relative to the earlier exposed-water rule, the new screen uses stricter signal and support criteria.", **qa},
            {"id": REFERENCE, "title": "Copernicus Sentinel-2 · Hasik open-water reference · 12 August 2026",
             "kind": "Independent optical proxy", "url": receipt["reference_item"], "acquired_at": receipt["optical"],
             "reference_sha256": receipt["optical_reference_sha256"],
             "notes": "The optical reference was acquired 16 h 5 min after NISAR. On 8,718 clear cells away from transitional edges, the new screen had 0 extra flags and missed 64 optical water labels. The earlier screen had 28 extra flags and missed no optical water labels. This test does not establish flooding under vegetation."},
        ],
    }
    _save_json(destination / "nisar-wetland-holdout.json", prepared)
    _save_json(output_row, runtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--holdout", type=Path, required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--runtime-row", type=Path, required=True)
    args = parser.parse_args()
    build(args.source, args.holdout, args.data, args.runtime_row)
    print(args.runtime_row)


if __name__ == "__main__":
    main()
