"""Conservative NISAR open-water and paired flooded-vegetation screens."""

from collections.abc import Mapping

import numpy as np
from scipy.ndimage import convolve, label


RADAR_FIELDS = ("hh_power", "hv_power", "hh_noise", "hv_noise", "hh_quality", "hv_quality")
OPEN_WATER_PARAMETERS = {
    "hh_db_max": -17.0,
    "hv_db_max": -23.0,
    "hh_noise_margin_db": 3.0,
    "hv_noise_margin_db": 1.0,
    "edge_hh_db_max": -15.0,
    "edge_hv_db_max": -21.0,
    "minimum_weak_neighbors": 4,
    "minimum_component_cells": 4,
}
FLOODED_VEGETATION_PARAMETERS = {
    "hh_increase_db_min": 3.0,
    "hh_minus_hv_increase_db_min": 2.0,
    "hv_absolute_change_db_max": 2.0,
    "observed_above_noise_db_min": 3.0,
    "minimum_component_cells": 4,
}
NEIGHBORHOOD = np.ones((3, 3), dtype=np.uint8)
CROSS = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=np.uint8)


def _db(power):
    result = np.full(power.shape, np.nan, dtype=np.float32)
    positive = np.isfinite(power) & (power > 0)
    result[positive] = 10 * np.log10(power[positive])
    return result


def _features(inputs):
    if not isinstance(inputs, Mapping) or any(field not in inputs for field in RADAR_FIELDS):
        raise ValueError("Wetland screening requires dual-polarization power, noise and quality arrays.")
    power = tuple(np.asarray(inputs[field]) for field in RADAR_FIELDS[:4])
    quality = tuple(np.asarray(inputs[field]) for field in RADAR_FIELDS[4:])
    shape = power[0].shape
    if len(shape) != 2 or any(array.shape != shape for array in (*power, *quality)):
        raise ValueError("Wetland input arrays must share one two-dimensional native grid.")
    if any(array.dtype != np.bool_ for array in quality):
        raise ValueError("Wetland quality arrays must contain Boolean values.")
    hh, hv, hh_noise, hv_noise = (_db(array) for array in power)
    support = quality[0] & quality[1] & np.isfinite(hh) & np.isfinite(hv) & np.isfinite(hh_noise) & np.isfinite(hv_noise)
    return hh, hv, hh_noise, hv_noise, support


def _large_components(mask, minimum):
    groups, _ = label(mask, structure=CROSS)
    count = np.bincount(groups.ravel())
    count[0] = 0
    return mask & (count[groups] >= minimum)


def classify_open_water(inputs, parameters=None):
    """Return data support, a conservative water core, and unresolved nearby edges.

    The input grids contain spatially averaged linear NISAR gamma-zero power,
    noise power and dual-polarization quality flags. Parameters are specified
    in observed-power dB for a 60 m analysis grid. The adjacent weaker class
    avoids assigning mixed shoreline pixels a water or land classification.
    """
    rule = OPEN_WATER_PARAMETERS if parameters is None else parameters
    hh, hv, hh_noise, hv_noise, quality = _features(inputs)
    support = (quality & (hh_noise <= rule["hh_db_max"] - rule["hh_noise_margin_db"])
               & (hv_noise <= rule["hv_db_max"] - rule["hv_noise_margin_db"]))
    weak = support & (hh <= rule["edge_hh_db_max"]) & (hv <= rule["edge_hv_db_max"])
    weak_count = convolve(weak.astype(np.uint8), NEIGHBORHOOD, mode="constant")
    dark = support & (hh <= rule["hh_db_max"]) & (hv <= rule["hv_db_max"])
    core = _large_components(dark & (weak_count >= rule["minimum_weak_neighbors"]), rule["minimum_component_cells"])
    touching_core = convolve(core.astype(np.uint8), CROSS, mode="constant") > 0
    shore = weak & touching_core & ~core
    return {"support": support, "open_water": core, "shore": shore,
            "r6_support": quality & (hh_noise <= -18.0), "r6_water": quality & (hh_noise <= -18.0) & (hh <= -15.0)}


def classify_flooded_vegetation(inputs, baseline=None, vegetation=None, parameters=None, *, open_water=None):
    """Screen for a preferential HH increase within mapped vegetation.

    The caller must supply a verified aligned drier acquisition acquired in
    comparable geometry and a vetted Boolean vegetation scope. An HH increase
    relative to the HV increase is consistent with a water–stem return, among
    other mechanisms. The result remains a radar candidate.
    """
    shape = np.asarray(inputs["hh_power"]).shape
    if baseline is None or vegetation is None:
        return {"support": np.zeros(shape, dtype=bool), "flooded_vegetation": np.zeros(shape, dtype=bool),
                "reason": "A comparable drier radar acquisition and a registered vegetation mask are required."}
    rule = FLOODED_VEGETATION_PARAMETERS if parameters is None else parameters
    hh, hv, hh_noise, hv_noise, quality = _features(inputs)
    dry_hh, dry_hv, dry_hh_noise, dry_hv_noise, dry_quality = _features(baseline)
    vegetation = np.asarray(vegetation)
    if not (vegetation.shape == dry_hh.shape == hh.shape) or vegetation.dtype != np.bool_:
        raise ValueError("Both observations and the vegetation scope must share the same native grid.")
    minimum = rule["observed_above_noise_db_min"]
    support = (quality & dry_quality & vegetation & (hh - hh_noise >= minimum) & (hv - hv_noise >= minimum)
               & (dry_hh - dry_hh_noise >= minimum) & (dry_hv - dry_hv_noise >= minimum))
    if open_water is not None:
        core = np.asarray(open_water)
        if core.shape != shape or core.dtype != np.bool_:
            raise ValueError("Open-water candidates must share the vegetation grid.")
        support &= ~core
    delta_hh = hh - dry_hh
    delta_hv = hv - dry_hv
    hits = (support & (delta_hh >= rule["hh_increase_db_min"])
            & (delta_hh - delta_hv >= rule["hh_minus_hv_increase_db_min"])
            & (np.abs(delta_hv) <= rule["hv_absolute_change_db_max"]))
    return {"support": support, "flooded_vegetation": _large_components(hits, rule["minimum_component_cells"]),
            "reason": None}
