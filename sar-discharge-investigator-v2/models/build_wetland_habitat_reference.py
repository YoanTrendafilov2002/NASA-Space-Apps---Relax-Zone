"""Publish the independent 400 m mapped-marsh water-depth reference."""
from argparse import ArgumentParser
from hashlib import file_digest
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask, shapes
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform_geom


APP = Path(__file__).resolve().parents[1]
CATALOGUE = APP / "data" / "nisar-everglades.json"
DESTINATION = APP / "data" / "nisar-wetland-context.json"
CLASSES = (
    {"id": "modeled-water-mapped-marsh", "label": "Modeled water in mapped marsh", "color": "#6dacc0", "code": 1,
     "outline_style": "solid"},
    {"id": "shallow-depth-reference", "label": "Shallow modeled depth · edge uncertain", "color": "#d8b97d", "code": 2,
     "outline_style": "dotted"},
    {"id": "reference-radar-gap", "label": "Separate radar benchmark withheld", "color": "#a9a69a", "code": 3,
     "outline_style": "dashed"},
    {"id": "marsh-radar-agreement", "label": "Radar and modeled water agree in mapped marsh", "color": "#f38228", "code": 4,
     "outline_style": "dashed"},
)
EXPECTED = "0 Outside scoring domain / 1 Shallow reference / 2 Radar unassessable / 3 Inundation matched / 4 Extra inundation / 5 Inundation missed / 6 Dry matched"


def layer(source_path):
    observation = json.loads(CATALOGUE.read_text(encoding="utf-8"))["observations"][0]
    channel = observation["channels"]["HH"]
    dimensions = channel["science_shape"]
    cropped = Affine(*channel["science_transform"])
    west, south, east, north = array_bounds(*dimensions, cropped)
    footprint = {"type": "Polygon", "coordinates": [[(west, south), (west, north), (east, north),
                                                       (east, south), (west, south)]]}
    with source_path.open("rb") as stream:
        source_hash = file_digest(stream, "sha256").hexdigest()
    with rasterio.open(source_path) as source:
        if source.res != (400, 400) or source.descriptions != (EXPECTED,):
            raise ValueError("Expected the archived EDEN and NWI comparison TIFF and code legend.")
        comparison = source.read(1)
        if not np.isin(comparison, np.arange(7)).all():
            raise ValueError("Unexpected source comparison class.")
        region = transform_geom(f"EPSG:{channel['science_epsg']}", source.crs, footprint)
        mask = geometry_mask([region], out_shape=source.shape, transform=source.transform, invert=True)
        # Codes 3 and 5 both meet the independent EDEN depth reference.
        # A missed radar prediction still belongs in the reference, and
        # withheld code 2 does not identify depth within the archived TIFF.
        grids = (mask & np.isin(comparison, (3, 5)), mask & (comparison == 1), mask & (comparison == 2),
                 mask & (comparison == 3))
        features = [
            {"type": "Feature", "geometry": transform_geom(source.crs, "EPSG:4326", geometry, precision=7),
             "properties": {"class_id": record["id"]}}
            for record, raster in zip(CLASSES, grids)
            for geometry, _ in shapes(raster.astype(np.uint8), mask=raster, transform=source.transform, connectivity=4)
        ]
    wet, shallow, withheld, matched = (int(grid.sum()) for grid in grids)
    return {
        "id": "nisar-everglades-eden-habitat-reference",
        "site_id": observation["site_id"], "observation_id": observation["id"], "polarization": "HH",
        "title": "Water depth in mapped Everglades marsh",
        "summary": (
            f"The daily USGS depth model gives at least 10 cm of water in {wet:,} evaluated 400 m cells "
            f"inside National Wetlands Inventory emergent marsh. {shallow:,} shallow-depth cells have an uncertain edge. "
            f"The separate one-date radar benchmark agreed on {matched:,} of the water cells and withheld {withheld:,} cells overall. "
            "The model and radar were compared within one scene at 400 m spacing. Fine shorelines are unresolved."
        ),
        "origin": "habitat_reference",
        "classes": list(CLASSES),
        "outlines": {"type": "FeatureCollection", "features": features},
        "evidence_ids": ["nisar-everglades-20261027-source", "everglades-eden-depth", "everglades-nwi-emergent"],
        "reference": {"day_utc": observation["acquired_at"][:10], "grid_m": 400,
                      "depth_minimum_cm": 10, "emergent_fraction_minimum": 0.7, "source_sha256": source_hash,
                      "water_cells": wet, "shallow_cells": shallow, "radar_withheld_cells": withheld,
                      "matched_water_cells": matched},
    }


if __name__ == "__main__":
    arguments = ArgumentParser(description=__doc__)
    arguments.add_argument("comparison_tif", type=Path)
    args = arguments.parse_args()
    collection = {"schema_version": 1, "layers": [layer(args.comparison_tif)], "evidence": []}
    DESTINATION.write_text(json.dumps(collection, separators=(",", ":")) + "\n", encoding="utf-8")
