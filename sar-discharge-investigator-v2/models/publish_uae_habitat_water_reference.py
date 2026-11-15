"""Publish optical exposed water within the registered UAE mangrove inventory."""
from __future__ import annotations

import argparse
from datetime import datetime
from hashlib import file_digest
import json
from pathlib import Path

import numpy as np
from rasterio.features import rasterize, shapes
from rasterio.transform import Affine
from rasterio.warp import transform_geom


REVIEW_ID = "nisar-uae-oil-wetland-review"
CLASS_ID = "optical-water-in-mangrove-inventory"
EVIDENCE_ID = "uae-mangrove-optical-water-20261019"
OPTICAL_ITEM = "https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/S2B_40RCP_20261019_0_L2A"
INVENTORY = "https://stgnsdi.fgic.gov.ae/hosting/rest/services/Environment/ENV_SL4_Mangroves_AR/MapServer/1"
PARENT = "NISAR_L2_PR_GCOV_033_128_A_015_2005_DHDH_M_20261021T013516_20261021T013551_P05023_N_F_J_001"


def sha256(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def publish(manifest, inputs, receipt):
    """Build and verify class features from two independent reference masks."""
    record = json.loads(receipt.read_text(encoding="utf-8"))
    if (record["reference_item"] != OPTICAL_ITEM or record["site_url"] != INVENTORY
            or record["radar_asset"] != PARENT or record["epsg"] != 32640
            or record["npz_sha256"] != sha256(inputs)):
        raise ValueError("Input provenance differs from the registered UAE reference")
    transform = Affine(*record["analysis_affine"])
    with np.load(inputs, allow_pickle=False) as source:
        optical, habitat = source["reference"], source["habitat"]
    if (optical.shape != tuple(record["analysis_shape"]) or habitat.shape != optical.shape
            or optical.dtype != np.uint8 or habitat.dtype != np.bool_
            or not np.isin(optical, (0, 1, 2)).all()):
        raise ValueError("Expected aligned optical and habitat masks at the registered grid")
    overlap = habitat & (optical == 1)
    count = int(overlap.sum())
    if count != record["counts"]["reference_water_in_mangrove_inventory"] or count == 0:
        raise ValueError("Independent overlay count differs from its receipt")

    projected_crs = "EPSG:32640"
    features = [
        {"type": "Feature", "geometry": transform_geom(projected_crs, "EPSG:4326", geometry, precision=9),
         "properties": {"class_id": CLASS_ID}}
        for geometry, _ in shapes(overlap.astype(np.uint8), mask=overlap, transform=transform, connectivity=4)
    ]
    rebuilt = rasterize(
        (transform_geom("EPSG:4326", projected_crs, row["geometry"]) for row in features),
        out_shape=overlap.shape, transform=transform, dtype=np.uint8,
    )
    if not np.array_equal(rebuilt.astype(bool), overlap):
        raise ValueError("Published outline does not recreate the verified source pixels")

    notice = (
        f"Dotted blue traces {count} exposed-water cells inside mapped mangrove habitat in the independent "
        "optical image taken 42 hours 33 minutes before radar. Coastal tides can change between observations."
    )
    entry = {
        "id": EVIDENCE_ID, "title": "Khor Al Beidah · exposed water inside mapped mangrove habitat",
        "kind": "Independent optical water and static habitat overlap", "url": OPTICAL_ITEM,
        "inventory_url": INVENTORY, "optical_acquired_at": record["optical"],
        "radar_acquired_at": record["radar"]["start_utc"],
        "optical_before_radar_seconds": (
            datetime.fromisoformat(record["radar"]["start_utc"])
            - datetime.fromisoformat(record["optical"])
        ).total_seconds(), "overlap_cells": count, "posting_m": 60,
        "optical_receipt_sha256": record["optical_reference_sha256"],
        "inventory_geojson_sha256": record["inventory_sha256"],
        "notes": (
            notice + " The optical proxy requires Sentinel-2 scene classification and the spectral water index "
            "to agree in at least eight of nine native 20 m samples per 60 m cell. The inventory depicts habitat."
        ),
    }
    document = json.loads(manifest.read_text(encoding="utf-8-sig"))
    layer, = (row for row in document["layers"] if row["id"] == REVIEW_ID)
    layer["summary"] = (
        "Red traces the independent slick interpretation about 39 minutes later. Orange traces inventoried "
        f"mangroves. {notice} The NISAR oil screen assessed 172 of 46,942 reference cells and flagged zero."
    )
    layer["classes"] = [
        row for row in layer["classes"] if row["id"] != CLASS_ID
    ] + [{"id": CLASS_ID, "label": "Optical exposed water inside mapped mangrove habitat",
          "color": "#2b5b6c", "code": 3, "outline_style": "dotted"}]
    layer["outlines"]["features"] = [
        row for row in layer["outlines"]["features"] if row["properties"]["class_id"] != CLASS_ID
    ] + features
    layer["evidence_ids"] = list(dict.fromkeys([*layer["evidence_ids"], EVIDENCE_ID]))
    document["evidence"] = [row for row in document["evidence"] if row["id"] != EVIDENCE_ID] + [entry]
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    staged = manifest.with_suffix(".new.json")
    staged.write_text(encoded, encoding="utf-8")
    staged.replace(manifest)
    print(f"Published {len(features)} outlines for {count} independently labelled cells.")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--inputs", required=True, type=Path)
    cli.add_argument("--receipt", required=True, type=Path)
    cli.add_argument("--manifest", type=Path,
                     default=Path(__file__).resolve().parents[1] / "data/nisar-detections.json")
    args = cli.parse_args()
    publish(args.manifest, args.inputs, args.receipt)


if __name__ == "__main__":
    main()
