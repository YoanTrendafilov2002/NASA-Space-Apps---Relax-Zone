"""Apply the frozen NISAR screens to registered scientific inputs."""

from __future__ import annotations

from datetime import datetime
from hashlib import file_digest
from io import BytesIO
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image, ImageColor
from rasterio import open as open_raster
from rasterio.features import shapes
from rasterio.io import MemoryFile
from rasterio.transform import Affine, array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds, transform_geom
from scipy.ndimage import binary_erosion, gaussian_filter, label
from scipy.signal import fftconvolve

from models.wetland_v2 import (
    FLOODED_VEGETATION_PARAMETERS, OPEN_WATER_PARAMETERS, classify_flooded_vegetation, classify_open_water,
)

CELL_M = 60
MAX_INPUT_CELLS = 500_000
MAX_OUTLINE_FEATURES = 650
MAX_OUTLINE_POINTS = 20_000
INPUTS = {
    'wetland': {'hh_power', 'hv_power', 'hh_noise', 'hv_noise', 'hh_quality', 'hv_quality'},
    'oil': {'hh_power', 'hh_quality', 'open_water'},
}
MODEL_FILES = {
    'wetland': 'results/recognition/wetlands/baseline/frozen-model.json',
    'oil': 'results/recognition/oil/observed-return-holdouts.json',
}
WETLAND_CLASSES = (
    {"id": "open-water-core", "label": "Open-water candidate", "color": "#6dacc0", "code": 1, "outline_style": "dashed"},
    {"id": "flooded-vegetation", "label": "Flooded-vegetation candidate", "color": "#f38228", "code": 3,
     "outline_style": "dashed"},
    {"id": "unresolved-water-edge", "label": "Possible water edge · uncertain", "color": "#d8b97d", "code": 4,
     "outline_style": "dotted"},
)
WETLAND_CODES = {"0": "Unassessed", "1": "Open-water candidate",
                 "2": "Assessed without a class", "3": "Flooded-vegetation candidate",
                 "4": "Possible water edge · uncertain"}
PAIR_FIELDS = ("site_id", "product", "sensor", "frequency", "orbit", "frame", "direction",
               "look_direction", "noise_correction_applied", "processing_version")


def _digest(path):
    with path.open('rb') as stream:
        return file_digest(stream, 'sha256').hexdigest()


def _dark_water_model(inputs, model):
    """Apply the observed-power rule from models/oil_observed_holdouts.py."""
    power = inputs['hh_power']
    measured = inputs['open_water'] & inputs['hh_quality'] & np.isfinite(power) & (power > 0)
    control = measured & (power < 10 ** (-7 / 10))
    context_m = 4000
    half = (context_m + CELL_M - 1) // CELL_M
    offsets = np.arange(-half, half + 1) * CELL_M
    x, y = np.meshgrid(offsets, offsets)
    distance = np.hypot(x, y)
    ring = (distance > context_m / 4) & (distance <= context_m)
    control_power = np.where(control, power, 0)
    populated = measured.copy()
    means = []
    for direction in (-1, 1):
        kernel = ring & (direction * x >= CELL_M)
        count = np.rint(fftconvolve(control, kernel, mode='same'))
        populated &= count * CELL_M**2 >= 500_000
        means.append(fftconvolve(control_power, kernel, mode='same') / np.maximum(count, 1))
    left, right = means
    positive = populated & (left > 0) & (right > 0)
    enough = np.zeros(power.shape, dtype=bool)
    enough[positive] = np.abs(10 * np.log10(left[positive] / right[positive])) <= 2
    local = np.zeros(power.shape, dtype=np.float32)
    local[enough] = np.clip(10 * np.log10(np.minimum(left, right)[enough] / power[enough]), -4, 4)
    options = {'sigma': 180 / CELL_M, 'mode': 'constant', 'cval': 0.0, 'truncate': 3}
    weight = gaussian_filter(enough.astype('float32'), **options)
    support = enough & (weight >= 0.75)
    score = np.divide(gaussian_filter(local, **options), weight,
                      out=np.full(power.shape, np.nan, dtype=np.float32), where=support)
    hits = support & (score >= model['calibrated']['threshold_db'])
    groups, _ = label(hits)
    flags = hits & (np.bincount(groups.ravel())[groups] >= 3)
    return support, flags


def _science_tiff(codes, transform, source, mode, legend):
    with MemoryFile() as archive:
        with archive.open(driver='GTiff', width=codes.shape[1], height=codes.shape[0], count=1,
                          dtype='uint8', crs=source['crs'], transform=transform, nodata=0,
                          compress='deflate') as output:
            output.write(codes, 1)
            output.set_band_description(1, "SARtainty categorical research screen")
            output.update_tags(product_id=source['product_id'], source_npz_sha256=source['input_sha256'],
                               mode=mode, method_file_sha256=source['model_sha256'],
                               class_codes=json.dumps(legend, ensure_ascii=True))
        return archive.read()


def _outlines(flags, transform, crs, class_id):
    features = []
    points = 0
    for geom, _ in shapes(flags.astype('uint8'), mask=flags, transform=transform, connectivity=4):
        points += sum(len(ring) for ring in geom['coordinates'])
        if len(features) >= MAX_OUTLINE_FEATURES or points > MAX_OUTLINE_POINTS:
            return None
        warped = transform_geom(crs, 'EPSG:4326', geom, precision=7)
        features.append({'type': 'Feature', 'geometry': warped, 'properties': {'class_id': class_id}})
    return {'type': 'FeatureCollection', 'features': features}


def _image(codes, transform, crs, classes):
    extent = array_bounds(*codes.shape, transform)
    target, width, height = calculate_default_transform(crs, 'EPSG:3857', codes.shape[1], codes.shape[0], *extent)
    mask = np.zeros((height, width), dtype=np.uint8)
    reproject(codes, mask, src_transform=transform, src_crs=crs, src_nodata=0,
              dst_transform=target, dst_crs='EPSG:3857', dst_nodata=0, resampling=Resampling.nearest)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    for item in classes:
        areas = mask == item["code"]
        boundaries = areas & ~binary_erosion(areas)
        rgba[boundaries] = (*ImageColor.getrgb(item["color"]), 230)
    buffer = BytesIO()
    Image.fromarray(rgba).save(buffer, 'PNG')
    west, south, east, north = transform_bounds('EPSG:3857', 'EPSG:4326', *array_bounds(height, width, target))
    return buffer.getvalue(), [[south, west], [north, east]]


class RuntimeRecognition:
    """Evaluate allowlisted local NISAR selections without querying a provider."""

    def __init__(self, app_dir, observations=None):
        self.app_dir = Path(app_dir)
        self.observations = observations or {}
        self.input_dir = self.app_dir / 'data/model-inputs'
        manifest = self.input_dir / 'runtime-recognition.json'
        self.manifest = json.loads(manifest.read_text(encoding='utf-8'))
        if self.manifest.get('schema_version') != 1 or not isinstance(self.manifest.get('rows'), dict):
            raise ValueError('Invalid runtime recognition input manifest.')
        self.models = {}
        for mode, relative in MODEL_FILES.items():
            registered = self.manifest['models'][mode]
            path = self.app_dir / relative
            if registered['path'] != relative or _digest(path) != registered['sha256']:
                raise ValueError(f'Frozen {mode} model failed its checksum.')
            self.models[mode] = json.loads(path.read_text(encoding='utf-8'))
        method_path = "models/wetland_v2.py"
        method_receipt = self.manifest["wetland_screen"]
        self.wetland_method_sha256 = _digest(self.app_dir / method_path)
        if method_receipt["path"] != method_path or method_receipt["sha256"] != self.wetland_method_sha256:
            raise ValueError("Wetland screen method failed its checksum.")

    def _inputs(self, observation_id, row):
        name = row['file']
        if not re.fullmatch(r'[a-z0-9-]+', observation_id) or name != f'{observation_id}.npz':
            raise ValueError('Invalid runtime input filename.')
        map_relative = row['map_science_path']
        if not re.fullmatch(r'science/[a-z0-9-]+\.tif', map_relative):
            raise ValueError('Invalid registered map science filename.')
        path = self.input_dir / name
        if (path.stat().st_size != row['size_bytes'] or _digest(path) != row['sha256']
                or _digest(self.app_dir / 'data' / map_relative) != row['map_science_sha256']):
            raise ValueError('Recognition inputs failed their checksums.')
        expected_shape = tuple(row['shape'])
        if len(expected_shape) != 2 or min(expected_shape) <= 0 or np.prod(expected_shape) > MAX_INPUT_CELLS:
            raise ValueError('Recognition input exceeds the supported grid size.')
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != INPUTS[row['mode']]:
                raise ValueError('Unexpected recognition input fields.')
            inputs = {key: archive[key] for key in archive.files}
        for key, array in inputs.items():
            expected_type = np.bool_ if key.endswith('_quality') or key == 'open_water' else np.float32
            if array.shape != expected_shape or array.dtype != expected_type:
                raise ValueError('Recognition inputs disagree on the expected scientific grid or dtype.')
        return inputs

    def _paired_vegetation(self, observation_id, row, inputs, water):
        context = row.get("vegetation_context")
        if not context:
            return classify_flooded_vegetation(inputs), None
        dry_id = context["drier_observation_id"]
        dry_row = self.manifest["rows"].get(dry_id, {})
        observation = self.observations.get(observation_id, {})
        dry_observation = self.observations.get(dry_id, {})
        if (dry_row.get("mode") != "wetland" or dry_row.get("site_id") != row["site_id"]
                or any(dry_row.get(key) != row[key] for key in ("shape", "affine", "epsg"))
                or any(observation.get(key) is None or observation[key] != dry_observation.get(key) for key in PAIR_FIELDS)
                or datetime.fromisoformat(dry_observation["acquired_at"]) >= datetime.fromisoformat(observation["acquired_at"])):
            return classify_flooded_vegetation(inputs), None
        filename = context["mask_file"]
        if not re.fullmatch(r"[a-z0-9-]+-vegetation\.tif", filename):
            raise ValueError("Unexpected vegetation reference mask filename.")
        path = (self.input_dir / filename).resolve()
        if (path.parent != self.input_dir.resolve() or path.stat().st_size != context["mask_size_bytes"]
                or path.stat().st_size > 4_000_000 or _digest(path) != context["mask_sha256"]):
            raise ValueError("Vegetation reference mask failed its receipt.")
        with open_raster(path) as raster:
            if (raster.count != 1 or raster.dtypes != ("uint8",) or raster.shape != tuple(row["shape"])
                    or not raster.crs or raster.crs.to_epsg() != row["epsg"] or list(raster.transform)[:6] != row["affine"]):
                raise ValueError("Vegetation reference mask does not match the analysis grid.")
            mask = raster.read(1)
        if not np.isin(mask, (0, 1)).all():
            raise ValueError("Vegetation reference mask must contain 0 or 1.")
        summary = classify_flooded_vegetation(inputs, self._inputs(dry_id, dry_row), mask.astype(bool), open_water=water)
        return summary, {"baseline_observation_id": dry_id, "baseline_input_sha256": dry_row["sha256"],
                         "vegetation_mask_sha256": context["mask_sha256"], "evidence_ids": context["evidence_ids"]}

    def run(self, site_id, observation_id, mode, polarization='HH'):
        if mode not in MODEL_FILES:
            return self._unsupported('unknown_mode', 'Choose the exposed-water or observed dark-water screen.')
        if polarization != 'HH':
            return self._unsupported('polarization', 'Switch to HH for this screen. The wetland comparison also reads the registered HV data.')
        row = self.manifest['rows'].get(observation_id)
        if row is None or row['site_id'] != site_id or row['mode'] != mode:
            reason = ('This selection lacks the registered HH/HV inputs and spatial noise checks for the frozen exposed-water screen.'
                      if mode == 'wetland' else
                      'This selection lacks the registered HH power and buffered open-water context required by the dark-water screen.')
            return self._unsupported('missing_eligible_inputs', reason)

        inputs = self._inputs(observation_id, row)
        fit = self.models[mode]
        top, bottom, left, right = row['crop']
        if not (0 <= top < bottom <= row['shape'][0] and 0 <= left < right <= row['shape'][1]):
            raise ValueError('Recognition crop is outside the registered analysis grid.')
        transform = Affine(*row['affine'])
        if (transform.a, transform.b, transform.d, transform.e) != (CELL_M, 0, 0, -CELL_M):
            raise ValueError('Recognition requires its original north-up 60 m grid.')
        crop_transform = Affine(CELL_M, 0, transform.c + left * CELL_M,
                                0, -CELL_M, transform.f - top * CELL_M)
        view = np.s_[top:bottom, left:right]
        total = (bottom - top) * (right - left)
        context = None
        if mode == 'wetland':
            open_water = classify_open_water(inputs)
            vegetation, context = self._paired_vegetation(observation_id, row, inputs, open_water["open_water"])
            water_support, flags = open_water["support"][view], open_water["open_water"][view]
            vegetation_support = vegetation["support"][view]
            inundation = vegetation["flooded_vegetation"][view]
            shore = open_water["shore"][view] & ~inundation
            support = water_support | vegetation_support
            measured, flagged = int(support.sum()), int(flags.sum())
            classes, legend = list(WETLAND_CLASSES), dict(WETLAND_CODES)
            title = "Open water and flooded vegetation"
            model_sha = self.wetland_method_sha256
            model = {"name": "Conservative NISAR wetland screen", "sha256": model_sha,
                     "open_water_rule": OPEN_WATER_PARAMETERS, "vegetation_rule": FLOODED_VEGETATION_PARAMETERS,
                     "vegetation_status": "withheld" if vegetation["reason"] else "screened",
                     "vegetation_reason": vegetation["reason"]}
            counts = {"open_water_cells": flagged, "r6_flagged_cells": int(open_water["r6_water"][view].sum()),
                      "uncertain_shore_cells": int(shore.sum()), "flooded_vegetation_cells": int(inundation.sum()),
                      "vegetation_evaluated_cells": int(vegetation_support.sum()),
                      "open_water_unassessed_cells": total - int(water_support.sum())}
            codes = np.where(support, np.where(flags, 1, 2), 0).astype("uint8")
            codes[shore] = 4
            codes[inundation] = 3
            summary = ((f"Both radar channels support {flagged:,} open-water candidates. "
                        f"{counts['uncertain_shore_cells']:,} nearby cells have an uncertain water edge. ")
                       if water_support.any() else
                       "The radar data failed the dual-channel noise or quality checks for open water. ")
            summary += ("Flooded vegetation was withheld because a matched drier date and vegetation reference are needed."
                        if vegetation["reason"] else
                        f"The paired radar screen flags {counts['flooded_vegetation_cells']:,} flooded-vegetation candidates.")
        else:
            support, flags = (mask[view] for mask in _dark_water_model(inputs, fit))
            measured, flagged = int(support.sum()), int(flags.sum())
            model_sha = self.manifest["models"][mode]["sha256"]
            model = {'name': 'Oman frozen observed HH dark-water screen', 'threshold_db': fit['calibrated']['threshold_db'],
                     'calibration_cells': fit['calibrated']['calibration_cells'], 'sha256': model_sha,
                     'context_radius_m': 4000, 'minimum_connected_cells': 3,
                     'algorithm_sha256': fit['algorithm_sha256']}
            title = 'Observed dark-water candidate'
            classes = [{'id': 'dark-water-candidate', 'label': title, 'color': '#e7330d', 'code': 1,
                        'outline_style': 'dashed'}]
            legend = {'0': 'Unassessed', '1': title, '2': 'Assessed without flag'}
            counts = {}
            codes = np.where(support, np.where(flags, 1, 2), 0).astype('uint8')
            summary = (f'{flagged:,} of {measured:,} assessed 60 m cells flagged in this map crop. '
                       f'{total - measured:,} cells withheld. The screen compares observed HH returns against water on both sides. '
                       'Low wind can also produce dark patches. Oil material is unconfirmed and spatial noise calibration is unavailable.')
        counts.update(total_cells=total, assessed_cells=measured, flagged_cells=flagged,
                      unassessed_cells=total - measured, assessed_without_flag_cells=int((codes == 2).sum()))
        evidence = list(dict.fromkeys([*row["evidence_ids"], *(context["evidence_ids"] if context else [])]))
        if mode == "wetland":
            evidence.append("wetland-v2-validation")
        source = {'product_id': row['product_id'], 'input_sha256': row['sha256'], 'model_sha256': model_sha,
                  'map_science_sha256': row['map_science_sha256'], 'crs': f"EPSG:{row['epsg']}",
                  'affine': list(crop_transform)[:6], 'shape': list(codes.shape), 'cell_size_m': CELL_M,
                  'noise_correction_applied': row['noise_correction_applied'],
                  'quality_rule': row['qa'], 'aggregation': row['aggregation'],
                  'evidence_ids': evidence}
        if context:
            source["paired_vegetation"] = context
        if not measured:
            return {**self._unsupported('no_assessable_cells', summary),
                    'runtime_computed': True, 'counts': counts, 'model': model, 'source': source}
        layer = {
            'site_id': site_id, 'observation_id': observation_id, 'polarization': polarization,
            'title': title, 'summary': summary, 'origin': 'runtime_frozen_model',
            'classes': classes, 'science_codes': legend, 'evidence_ids': evidence,
        }
        layer["mapped_class_ids"] = [item["id"] for item in classes if (codes == item["code"]).any()]
        artifacts = {'science': _science_tiff(codes, crop_transform, source, mode, legend)}
        per_class = [_outlines(codes == item["code"], crop_transform, source["crs"], item["id"]) for item in classes]
        features = [feature for record in per_class if record for feature in record["features"]]
        if any(record is None for record in per_class) or not features:
            artifacts['image'], layer['image_bounds'] = _image(codes, crop_transform, source['crs'], classes)
        else:
            layer['outlines'] = {"type": "FeatureCollection", "features": features}
        return {'status': 'measured', 'runtime_computed': True, 'mode': mode, 'summary': summary,
                'counts': counts, 'model': model, 'source': source, 'recognition_layer': layer, '_artifacts': artifacts}

    @staticmethod
    def _unsupported(reason, summary):
        return {'status': 'unsupported', 'reason': reason, 'summary': summary, 'runtime_computed': False}
