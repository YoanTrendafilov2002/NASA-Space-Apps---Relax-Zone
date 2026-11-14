#!/usr/bin/env python3
"""
analysis.py — data acquisition and calibrated SAR processing for the
SAR Discharge Investigator (NASA Space Apps 2026, "Dancing with the SARs").

Sub-commands
------------
  search   List NISAR L2 GCOV (ASF) and Sentinel-1 RTC (Planetary Computer) acquisitions
           over a hotspot and save a provenance catalogue.
  nisar    Stream a NISAR GCOV granule over HTTPS (Earthdata login), read the HH (or VV)
           gamma0 layer for the analysis window, detect dark surface anomalies.
  s1       Same pipeline on a Sentinel-1 RTC scene (no login needed).
  selftest Run the detector on a synthetic speckle scene (unit test only — never an observation).

Every result file is JSON that the web app loads with "Load analysis.py result".

NISAR access needs a free NASA Earthdata account: either ~/.netrc with
`machine urs.earthdata.nasa.gov login <user> password <pass>` or the
EARTHDATA_USERNAME / EARTHDATA_PASSWORD environment variables.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import math
import os
import sys

import numpy as np
from scipy import ndimage as ndi

# --------------------------------------------------------------------------
# Hotspots (same definitions as app.js). bbox = W, S, E, N in degrees.
# --------------------------------------------------------------------------
HOTSPOTS = {
    "maracaibo": {"name": "Lake Maracaibo, Venezuela", "search": [-71.85, 9.60, -71.10, 10.65], "demo": [-71.64, 10.02, -71.34, 10.32]},
    "galveston": {"name": "Houston Ship Channel - Galveston Bay, USA", "search": [-95.30, 29.30, -94.70, 29.80], "demo": [-95.12, 29.45, -94.82, 29.72]},
    "bonny": {"name": "Bonny River estuary, Niger Delta, Nigeria", "search": [6.95, 4.35, 7.30, 4.80], "demo": [6.98, 4.38, 7.22, 4.62]},
}
S1_NESZ_DB = -21.0   # Sentinel-1 IW NESZ -22 dB sigma0 (ESA spec) expressed as gamma0 at ~39 deg incidence (+/-1 dB across swath)
S1_ENL = 4.4         # IW GRDH nominal equivalent number of looks
GCOV_ROOT = "/science/LSAR/GCOV/grids"


def log(*a):
    print("[analysis]", *a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Detector (mirrors the in-browser algorithm in app.js)
# --------------------------------------------------------------------------
def masked_box_mean(x, mask, r):
    """Mean of x over mask pixels within a (2r+1)^2 window."""
    size = 2 * r + 1
    m = mask.astype(np.float64)
    s = ndi.uniform_filter(np.where(mask, x, 0.0).astype(np.float64), size, mode="constant")
    c = ndi.uniform_filter(m, size, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        return s / c


def lee_filter(lin, mask, enl, r=2):
    m = masked_box_mean(lin, mask, r)
    m2 = masked_box_mean(lin * lin, mask, r)
    var = np.maximum(m2 - m * m, 0)
    cu2 = 1.0 / enl
    with np.errstate(invalid="ignore", divide="ignore"):
        ci2 = var / (m * m + 1e-12)
        w = np.clip((1 - cu2 / ci2) / (1 + cu2), 0, 1)
    w = np.nan_to_num(w)
    return m + w * (lin - m)


def _chamfer_km(src_mask, px_x, px_y):
    """Euclidean distance (km) to the nearest True pixel of src_mask, honouring anisotropic pixels."""
    if not src_mask.any():
        return np.full(src_mask.shape, np.inf)
    return ndi.distance_transform_edt(~src_mask, sampling=(px_y, px_x)) / 1000.0


def detect(db, valid, occ, px_m, T=3.0, min_km2=0.05, nesz=None, enl=S1_ENL, max_out=40,
           px_xy=None, shore_buf_m=100.0, hole_km2=0.1, bg_win_m=3000.0, pt_db=10.0):
    """Dark-feature detection on calibrated gamma0 (dB) — detector v2 (mirrors app.js).

    db    : 2-D gamma0 in dB          valid : bool, pixel has SAR data
    occ   : JRC water occurrence 0-100 (NaN unknown)
    px_xy : (pixel_x_m, pixel_y_m) true pixel size; defaults to px_m
    """
    px_x, px_y = px_xy or (px_m, px_m)
    px_l = math.sqrt(px_x * px_y)
    px_km2 = px_x * px_y / 1e6
    H, W = db.shape
    known = np.isfinite(occ)
    occ0 = np.where(known, occ, 0.0)
    water = known & (occ0 >= 80)
    perm = known & (occ0 >= 90)
    land_raw = known & ~water
    # land = non-water components >= hole_km2 or touching the border; smaller enclosed ones = in-water structures
    llab, nl = ndi.label(land_raw)
    sizes = ndi.sum(land_raw, llab, range(1, nl + 1)) if nl else np.array([])
    border = set(np.unique(np.concatenate([llab[0], llab[-1], llab[:, 0], llab[:, -1]]))) - {0}
    small = np.array([(s < hole_km2 / px_km2) and (k + 1 not in border) for k, s in enumerate(sizes)], bool)
    struct = np.isin(llab, np.nonzero(small)[0] + 1) if nl else np.zeros_like(water)
    land = land_raw & ~struct
    d_land_km = _chamfer_km(land, px_x, px_y)
    near_struct = ndi.binary_dilation(struct, structure=ndi.generate_binary_structure(2, 1))
    wm = water & valid & (d_land_km * 1000 > shore_buf_m) & ~near_struct
    if wm.sum() < 500:
        raise RuntimeError("Too little open water in the window (JRC occurrence >= 80 %, outside the shoreline buffer).")
    lin = np.where(valid, 10 ** (db / 10.0), np.nan)
    linw = np.where(wm, lin, 0.0)
    filt = lee_filter(linw, wm, enl)
    R = max(10, int(round(bg_win_m / 2 / px_l)))
    R2 = 2 * R
    bg1 = masked_box_mean(filt, wm, R)
    with np.errstate(divide="ignore", invalid="ignore"):
        fdb = 10 * np.log10(np.maximum(filt, 1e-12))
        cand = wm & ((10 * np.log10(np.maximum(bg1, 1e-12)) - fdb) >= T / 2)
        keep = wm & ~cand
        size2 = 2 * R2 + 1
        s2 = ndi.uniform_filter(np.where(keep, filt, 0.0), size2, mode="constant")
        c2 = ndi.uniform_filter(keep.astype(float), size2, mode="constant")
        cw = ndi.uniform_filter(wm.astype(float), size2, mode="constant")
        ok_bg = wm & (c2 >= 0.25 * cw) & (c2 * size2 * size2 >= 20)
        bg_lin = np.where(ok_bg, s2 / np.maximum(c2, 1e-12), np.nan)
        bdb = 10 * np.log10(bg_lin)
    con = np.where(ok_bg, bdb - fdb, np.nan)
    bg_min = nesz + T + 1 if nesz is not None else -np.inf
    meas = ok_bg & (bdb >= bg_min)
    # bright point targets (unidentified platforms/vessels)
    ref = 10 * np.log10(np.maximum(bg1, 1e-12))
    if nesz is not None:
        ref = np.maximum(ref, nesz)
    pt_mask = valid & (water | struct) & np.isfinite(bg1) & (db >= ref + pt_db)
    plab, npt = ndi.label(pt_mask, structure=np.ones((3, 3)))
    pts = np.array(ndi.center_of_mass(pt_mask, plab, range(1, npt + 1))) if npt else np.zeros((0, 2))
    # hysteresis restricted to measurable pixels
    grow = meas & (con >= T / 2)
    seeds = meas & (con >= T)
    lab, n = ndi.label(grow, structure=np.ones((3, 3)))
    keep_ids = np.unique(lab[seeds & (lab > 0)])
    min_px = max(6, int(math.ceil(min_km2 / px_km2)))
    regional_km2 = math.pi * (R2 * px_l / 1000) ** 2
    objs = ndi.find_objects(lab)
    se4 = ndi.generate_binary_structure(2, 1)
    anoms = []
    for cid in keep_ids:
        sl = objs[cid - 1]
        comp = lab == cid
        npx = int(comp.sum())
        if npx < min_px:
            continue
        yy, xx = np.nonzero(comp)
        xm, ym = xx * px_x, yy * px_y
        cx, cy = xm.mean(), ym.mean()
        cxx, cyy, cxy = ((xm - cx) ** 2).mean(), ((ym - cy) ** 2).mean(), ((xm - cx) * (ym - cy)).mean()
        ev, evec = np.linalg.eigh(np.array([[cxx, cxy], [cxy, cyy]]))
        fl = px_l * px_l / 12
        l1, l2 = float(ev[1]) + fl, max(float(ev[0]), 0.0) + fl
        u = evec[:, 1]
        proj = (xm - cx) * u[0] + (ym - cy) * u[1]
        extent = proj.max() - proj.min() + abs(u[0]) * px_x + abs(u[1]) * px_y
        a_, b_ = 2 * math.sqrt(l1), 2 * math.sqrt(l2)
        meandb = 10 * math.log10(np.nanmean(lin[comp]))
        bgdb = 10 * math.log10(np.nanmean(bg_lin[comp]))
        cvals = con[comp]
        ring = ndi.binary_dilation(comp, structure=se4) & ~comp & wm
        inner = comp & ~ndi.binary_erosion(comp, structure=se4)
        edge = float(10 * np.log10(filt[ring].mean()) - 10 * np.log10(filt[inner].mean())) if ring.any() and inner.any() else None
        area = npx * px_km2
        noise_frac = float((fdb[comp] < nesz + 1).mean()) if nesz is not None else None
        regional = area > regional_km2
        se = 4.343 * math.sqrt(1.0 / (enl * npx))
        unc = math.sqrt(se ** 2 + 2 * ((30 / 255) / math.sqrt(12)) ** 2)
        shore_km = float(d_land_km[comp].min())
        shore_km = None if not math.isfinite(shore_km) else shore_km   # None = no land in the window
        # point targets inside / within 2 km of the boundary
        inside = near = 0
        nearest = None
        if len(pts):
            bnd = np.argwhere(inner)
            for (py, pxx) in pts:
                if lab[int(round(py)), int(round(pxx))] == cid:
                    inside += 1; nearest = 0.0; continue
                d = np.min(np.hypot((bnd[:, 1] - pxx) * px_x, (bnd[:, 0] - py) * px_y)) / 1000
                if d <= 2: near += 1
                nearest = d if nearest is None else min(nearest, d)
        anoms.append({
            "n": npx, "cx": float(cx / px_x), "cy": float(cy / px_y), "area_km2": area,
            "mean_db": meandb, "bg_db": bgdb, "contrast_db": bgdb - meandb, "contrast_unc_db": unc,
            "p95_contrast_db": float(np.percentile(cvals, 95)), "max_contrast_db": float(np.nanmax(cvals)),
            "elongation": math.sqrt(l1 / l2), "length_km": extent / 1000, "ellipse_major_km": 2 * a_ / 1000,
            "fill_ratio": npx * px_x * px_y / (math.pi * a_ * b_), "edge_step_db": edge,
            "shore_km": shore_km, "touches_shore_buffer": bool(shore_km is not None and shore_km * 1000 <= shore_buf_m + 1.5 * px_l),
            "perm_water_frac": float(perm[comp].mean()), "noise_frac": noise_frac, "regional": bool(regional),
            "class": "noise" if (noise_frac is not None and noise_frac >= 0.5) else ("regional" if regional else "measurable"),
            "point_targets_inside": inside, "point_targets_2km": near, "nearest_point_target_km": nearest, "_mask": comp,
        })
    anoms.sort(key=lambda a: -a["area_km2"])
    low = float((bdb[ok_bg] < nesz + 3).mean()) if nesz is not None and ok_bg.any() else None
    return {"anoms": anoms[:max_out], "total": len(anoms), "n_points": int(npt), "n_struct": int(small.sum()) if nl else 0,
            "con": con, "meas": meas, "lin": lin, "bg_lin": bg_lin, "wm": wm, "fdb": np.where(valid, db, np.nan),  # display layer (unfiltered gamma0 dB)
            "low_frac": low, "meas_frac": float(meas.sum() / wm.sum()), "water_mean_db": float(10 * np.log10(np.nanmean(lin[wm]))),
            "bg_radius_px": R, "regional_km2": regional_km2}


def persistence(anoms, comp_res, T):
    """Re-measure each anomaly's pixels in an independently processed second scene."""
    for a in anoms:
        m = a["_mask"] & comp_res["meas"]
        cover = float(m.sum() / a["n"])
        a["persist_cover"] = cover
        if cover < 0.5:
            a["persist_db"] = a["persist_frac"] = None
            a["persist_status"] = "untestable"
            continue
        a["persist_db"] = float(10 * np.log10(np.nanmean(comp_res["bg_lin"][m])) - 10 * np.log10(np.nanmean(comp_res["lin"][m])))
        a["persist_frac"] = float((comp_res["con"][m] >= T / 2).mean())
        a["persist_status"] = ("recurs" if a["persist_db"] >= T / 2 and a["persist_frac"] >= 0.5
                               else "absent" if a["persist_db"] < T / 2 and a["persist_frac"] < 0.25 else "partial")


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def quicklook(fdb, anoms, valid):
    from PIL import Image
    g = np.clip((np.nan_to_num(fdb, nan=-30) + 30) / 30 * 255, 0, 255).astype(np.uint8)
    rgba = np.dstack([g, g, g, np.where(valid, 255, 0).astype(np.uint8)])
    col = {"measurable": [255, 45, 122, 230], "regional": [245, 158, 11, 230], "noise": [139, 92, 246, 230]}
    for a in anoms:
        rgba[a["_mask"]] = col.get(a.get("class"), col["measurable"])
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, "PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def write_result(path, source, bbox, px_m, params, nesz, res, valid, extra=None):
    W, S, E, N = bbox
    H, Wd = res["fdb"].shape
    out_anoms = []
    for a in res["anoms"]:
        lat = N - (a["cy"] + 0.5) / H * (N - S)
        lon = W + (a["cx"] + 0.5) / Wd * (E - W)
        rec = {k: v for k, v in a.items() if not k.startswith("_") and k not in ("cx", "cy")}
        rec["centroid"] = [round(lat, 5), round(lon, 5)]
        out_anoms.append(rec)
    doc = {
        "generated": dt.datetime.utcnow().isoformat() + "Z",
        "source": source, "bbox": bbox, "pixel_m": px_m, "nesz_db": nesz, "params": params,
        "detector_version": "v2",
        "scene": {"total_dark_features": res["total"], "bright_point_targets": res["n_points"], "in_water_structures": res["n_struct"],
                  "water_mean_db": res["water_mean_db"], "measurable_water_fraction": res["meas_frac"],
                  "low_signal_water_fraction": res["low_frac"], "background_window_px": 2 * res["bg_radius_px"] + 1,
                  "regional_km2": res["regional_km2"]},
        "evidence_tier": "1 - SAR-observed surface anomalies (not possible-discharge or verified-source claims)",
        "anomalies": out_anoms,
        "quicklook_png": quicklook(res["fdb"], res["anoms"], valid),
        "notes": "Dark features are observations of reduced backscatter. They are not identified as oil, "
                 "and no source or legality is implied.",
    }
    if extra:
        doc.update(extra)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, indent=1, allow_nan=False, default=lambda o: None)
    log(f"wrote {path}: {len(out_anoms)} anomalies (of {res['total']})")


# --------------------------------------------------------------------------
# Ancillary: JRC Global Surface Water on the analysis grid
# --------------------------------------------------------------------------
def gsw_on_grid(bbox, shape):
    """JRC GSW occurrence (0-100) resampled to an EPSG:4326 grid covering bbox."""
    import planetary_computer as pc
    import pystac_client
    import rioxarray  # noqa: F401
    from rasterio.enums import Resampling
    import xarray as xr
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    items = list(cat.search(collections=["jrc-gsw"], bbox=bbox).items())
    if not items:
        raise RuntimeError("No JRC GSW tile for this window")
    H, W = shape
    out = np.full(shape, np.nan)
    tmpl = xr.DataArray(np.zeros(shape), dims=("y", "x"),
                        coords={"y": np.linspace(bbox[3], bbox[1], H, endpoint=False) - (bbox[3] - bbox[1]) / H / 2,
                                "x": np.linspace(bbox[0], bbox[2], W, endpoint=False) + (bbox[2] - bbox[0]) / W / 2})
    tmpl = tmpl.rio.write_crs("EPSG:4326")
    for it in items:
        da = rioxarray.open_rasterio(it.assets["occurrence"].href, masked=True).squeeze()
        da = da.rio.clip_box(*bbox, crs="EPSG:4326")
        r = da.rio.reproject_match(tmpl, resampling=Resampling.average).values.astype(float)
        r[r > 100] = np.nan
        out = np.where(np.isnan(out), r, np.fmax(out, r))
    return out


def grid_shape(bbox, target_px):
    lat = (bbox[1] + bbox[3]) / 2
    wm = (bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(lat))
    hm = (bbox[3] - bbox[1]) * 110540
    return int(round(hm / target_px)), int(round(wm / target_px))


def grid_pixel_m(bbox, shape):
    """True (x, y) pixel size in metres of the lat/lon analysis grid."""
    lat = (bbox[1] + bbox[3]) / 2
    H, W = shape
    return ((bbox[2] - bbox[0]) * 111320 * math.cos(math.radians(lat)) / W, (bbox[3] - bbox[1]) * 110540 / H)


def to_latlon_grid(arr, x, y, crs_epsg, bbox, shape):
    """Resample a projected linear-power array (with x/y pixel-centre coords) to the 4326 analysis grid
    by block averaging in linear power (true multilooking, not nearest neighbour)."""
    import xarray as xr
    import rioxarray  # noqa: F401
    from rasterio.enums import Resampling
    da = xr.DataArray(arr, dims=("y", "x"), coords={"y": y, "x": x}).rio.write_crs(f"EPSG:{crs_epsg}")
    da = da.rio.write_nodata(np.nan)
    H, W = shape
    tmpl = xr.DataArray(np.zeros(shape), dims=("y", "x"),
                        coords={"y": np.linspace(bbox[3], bbox[1], H, endpoint=False) - (bbox[3] - bbox[1]) / H / 2,
                                "x": np.linspace(bbox[0], bbox[2], W, endpoint=False) + (bbox[2] - bbox[0]) / W / 2}).rio.write_crs("EPSG:4326")
    return da.rio.reproject_match(tmpl, resampling=Resampling.average).values


# --------------------------------------------------------------------------
# Search (provenance catalogue)
# --------------------------------------------------------------------------
def cmd_search(a):
    import asf_search as asf
    import planetary_computer as pc
    import pystac_client
    hs = HOTSPOTS[a.hotspot]
    W, S, E, N = hs["search"]
    wkt = f"POLYGON(({W} {S},{E} {S},{E} {N},{W} {N},{W} {S}))"
    res = asf.search(platform="NISAR", processingLevel="GCOV", intersectsWith=wkt, start=a.start, end=a.end, maxResults=500)
    nisar = []
    for r in res:
        p = r.properties
        nisar.append({k: p.get(k) for k in ("fileID", "startTime", "flightDirection", "pathNumber", "frameNumber",
                                            "mainBandPolarization", "rangeBandwidth", "url", "crid")})
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    s1 = [{"id": it.id, "datetime": it.properties["datetime"], "platform": it.properties.get("platform"),
           "orbit_state": it.properties.get("sat:orbit_state")}
          for it in cat.search(collections=["sentinel-1-rtc"], bbox=hs["search"], datetime=f"{a.start}/{a.end}").items()]
    out = {"hotspot": a.hotspot, "retrieved": dt.datetime.utcnow().isoformat() + "Z", "nisar_gcov": nisar, "sentinel1_rtc": s1}
    path = a.out or f"results/catalog_{a.hotspot}.json"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    json.dump(out, open(path, "w"), indent=1)
    print(f"NISAR GCOV: {len(nisar)} granules; Sentinel-1 RTC: {len(s1)} scenes -> {path}")
    for g in nisar[:10]:
        print("  NISAR", g["startTime"], g["fileID"])


# --------------------------------------------------------------------------
# Sentinel-1 RTC
# --------------------------------------------------------------------------
def read_s1(item_id, bbox, shape):
    import planetary_computer as pc
    import pystac_client
    import rioxarray
    cat = pystac_client.Client.open("https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace)
    item = next(cat.search(collections=["sentinel-1-rtc"], ids=[item_id]).items(), None)
    if item is None:
        raise RuntimeError(f"Sentinel-1 RTC item not found: {item_id}")
    da = rioxarray.open_rasterio(item.assets["vv"].href, masked=True).squeeze()
    da = da.rio.clip_box(*bbox, crs="EPSG:4326")
    epsg = da.rio.crs.to_epsg()
    lin = to_latlon_grid(da.values.astype(float), da.x.values, da.y.values, epsg, bbox, shape)
    return item, lin


def cmd_s1(a):
    bbox = a.bbox or HOTSPOTS[a.hotspot]["demo"]
    shape = grid_shape(bbox, a.pixel)
    item, lin = read_s1(a.item, bbox, shape)
    valid = np.isfinite(lin) & (lin > 0)
    db = np.where(valid, 10 * np.log10(np.where(valid, lin, 1)), np.nan)
    occ = gsw_on_grid(bbox, shape)
    # block averaging from 10 m to a.pixel increases the ENL by ~ (a.pixel/10)^2
    enl = S1_ENL * max(1.0, (a.pixel / 10.0) ** 2)
    pxy = grid_pixel_m(bbox, shape)
    res = detect(np.nan_to_num(db, nan=-30), valid, occ, a.pixel, a.threshold, a.min_area, S1_NESZ_DB, enl=min(enl, 50), px_xy=pxy)
    extra = {}
    if a.compare:
        item2, lin2 = read_s1(a.compare, bbox, shape)
        v2 = np.isfinite(lin2) & (lin2 > 0)
        r2 = detect(np.nan_to_num(10 * np.log10(np.where(v2, lin2, 1)), nan=-30), v2, occ, a.pixel, a.threshold, a.min_area, S1_NESZ_DB, enl=min(enl, 50), px_xy=pxy)
        persistence(res["anoms"], r2, a.threshold)
        p1, p2 = item.properties, item2.properties
        extra["comparison"] = {"scene": a.compare, "platforms": [p1.get("platform"), p2.get("platform")],
                               "relative_orbits": [p1.get("sat:relative_orbit"), p2.get("sat:relative_orbit")],
                               "orbit_states": [p1.get("sat:orbit_state"), p2.get("sat:orbit_state")],
                               "same_geometry": p1.get("sat:relative_orbit") == p2.get("sat:relative_orbit") and p1.get("sat:orbit_state") == p2.get("sat:orbit_state"),
                               "water_mean_db": [res["water_mean_db"], r2["water_mean_db"]]}
    src = {"platform": item.properties.get("platform", "Sentinel-1"), "product": "Sentinel-1 IW GRD RTC gamma0 (Planetary Computer sentinel-1-rtc)",
           "granule": item.id, "start": item.properties["datetime"], "polarization": "VV"}
    write_result(a.out or f"results/s1_{a.hotspot}_{item.id[:40]}.json", src, bbox, a.pixel,
                 {"contrast_db": a.threshold, "min_area_km2": a.min_area, "enl": min(enl, 50)}, S1_NESZ_DB, res, valid, extra)


# --------------------------------------------------------------------------
# NISAR GCOV
# --------------------------------------------------------------------------
def _find(h5, name):
    hit = []
    h5.visit(lambda n: hit.append(n) if n.endswith(name) else None)
    return hit


def read_gcov(h5, bbox, pol="HHHH", freq="A"):
    """Read a gamma0 diagonal term over bbox from an open h5py.File. Returns (lin, x, y, epsg, meta)."""
    from pyproj import Transformer
    g = f"{GCOV_ROOT}/frequency{freq}"
    if g not in h5:
        raise RuntimeError(f"{g} not in product")
    avail = [k for k in h5[g].keys() if len(k) == 4 and k.isupper()]
    if pol not in h5[g]:
        alt = next((p for p in ("HHHH", "VVVV", "HVHV") if p in h5[g]), None)
        if alt is None:
            raise RuntimeError(f"No diagonal gamma0 term in {g}; available: {avail}")
        log(f"{pol} not present, using {alt}")
        pol = alt
    x = h5[f"{g}/xCoordinates"][()]
    y = h5[f"{g}/yCoordinates"][()]
    proj = h5[f"{g}/projection"]
    epsg = int(proj.attrs.get("epsg_code", proj[()] if proj.shape == () else 0))
    if not epsg:
        raise RuntimeError("Could not determine EPSG code of GCOV grid")
    tr = Transformer.from_crs(4326, epsg, always_xy=True)
    xs, ys = tr.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    ix = np.where((x >= x0) & (x <= x1))[0]
    iy = np.where((y >= y0) & (y <= y1))[0]
    if ix.size == 0 or iy.size == 0:
        raise RuntimeError("Analysis window does not overlap this granule")
    sl = (slice(iy.min(), iy.max() + 1), slice(ix.min(), ix.max() + 1))
    lin = h5[f"{g}/{pol}"][sl].astype(float)
    lin[~np.isfinite(lin) | (lin <= 0)] = np.nan
    # Note: the GCOV "mask" layer describes which radar samples contributed to adaptive multilooking;
    # it is not used here as a validity mask. Invalid/fill pixels are NaN or <= 0 in the gamma0 layer.
    nlooks = None
    if f"{g}/numberOfLooks" in h5:
        nl = h5[f"{g}/numberOfLooks"][sl]
        nlooks = float(np.nanmedian(nl[nl > 0])) if np.any(nl > 0) else None
    meta = {"polarization": pol, "available_terms": avail, "epsg": epsg, "median_looks": nlooks,
            "native_spacing_m": float(abs(x[1] - x[0])) if x.size > 1 else None}
    return lin, x[sl[1]], y[sl[0]], epsg, meta


def cmd_nisar(a):
    import asf_search as asf
    import earthaccess
    import h5py
    bbox = a.bbox or HOTSPOTS[a.hotspot]["demo"]
    shape = grid_shape(bbox, a.pixel)
    prod = asf.granule_search([a.granule])
    url = None
    props = {}
    for r in prod:
        if r.properties.get("fileID") == a.granule or a.granule in r.properties.get("url", ""):
            url, props = r.properties["url"], r.properties
    if url is None:
        raise SystemExit(f"Granule not found in ASF catalogue: {a.granule}")
    log("logging in to NASA Earthdata …")
    earthaccess.login(strategy="environment" if os.getenv("EARTHDATA_USERNAME") else "netrc")
    if a.download:
        files = earthaccess.download([url], local_path=a.download)
        h5 = h5py.File(files[0], "r")
    else:
        log("streaming HDF5 over HTTPS (range requests) …")
        fobj = earthaccess.open([url])[0]
        h5 = h5py.File(fobj, "r")
    with h5:
        lin, x, y, epsg, meta = read_gcov(h5, bbox, pol=a.pol)
    log(f"read {lin.shape} pixels of {meta['polarization']} (EPSG:{epsg}, native {meta['native_spacing_m']} m)")
    lin_g = to_latlon_grid(lin, x, y, epsg, bbox, shape)
    valid = np.isfinite(lin_g) & (lin_g > 0)
    db = np.where(valid, 10 * np.log10(np.where(valid, lin_g, 1)), -30)
    occ = gsw_on_grid(bbox, shape)
    native = meta["native_spacing_m"] or 10.0
    enl = (meta["median_looks"] or 1.0) * max(1.0, (a.pixel / native) ** 2)
    res = detect(db, valid, occ, a.pixel, a.threshold, a.min_area, a.nesz, enl=min(enl, 50), px_xy=grid_pixel_m(bbox, shape))
    src = {"platform": "NISAR", "product": props.get("collectionName", "NISAR_L2_GCOV"), "granule": a.granule,
           "start": props.get("startTime"), "polarization": meta["polarization"], "pass": props.get("flightDirection"),
           "track": props.get("pathNumber"), "frame": props.get("frameNumber"), "url": url}
    write_result(a.out or f"results/nisar_{a.hotspot}_{a.granule[:60]}.json", src, bbox, a.pixel,
                 {"contrast_db": a.threshold, "min_area_km2": a.min_area, "enl": min(enl, 50)}, a.nesz, res, valid,
                 {"gcov_meta": meta})


# --------------------------------------------------------------------------
# Self-test (synthetic, for verifying the detector only)
# --------------------------------------------------------------------------
def cmd_selftest(a):
    rng = np.random.default_rng(1)
    H, W, px = 300, 400, 30.0
    yy, xx = np.mgrid[0:H, 0:W]
    slick = ((xx - 220) / 60) ** 2 + ((yy - 150) / 12) ** 2 <= 1
    mu = np.where(slick, 10 ** (-2.0), 10 ** (-1.2))
    lin = mu * rng.gamma(S1_ENL, 1 / S1_ENL, size=(H, W))
    db = 10 * np.log10(lin)
    occ = np.where(xx < 30, 0.0, 100.0)
    valid = np.ones((H, W), bool)
    r = detect(db, valid, occ, px, 3.0, 0.05, S1_NESZ_DB)
    a0 = r["anoms"][0]
    truth = slick.sum() * px * px / 1e6
    print(f"SYNTHETIC TEST: found {r['total']} feature(s); largest {a0['area_km2']:.3f} km2 (truth {truth:.3f}), "
          f"contrast {a0['contrast_db']:.2f} dB (truth 8.0), axis ratio {a0['elongation']:.2f} (truth 5), class {a0['class']}")
    ok = r["total"] == 1 and abs(a0["area_km2"] - truth) / truth < 0.15 and abs(a0["contrast_db"] - 8) < 1.0
    lin2 = 10 ** (-1.2) * rng.gamma(S1_ENL, 1 / S1_ENL, size=(H, W))
    r2 = detect(10 * np.log10(lin2), valid, occ, px, 3.0, 0.05, S1_NESZ_DB)
    print(f"SYNTHETIC TEST: speckle-only scene -> {r2['total']} false feature(s)")
    ok = ok and r2["total"] == 0
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = p.add_subparsers(dest="cmd", required=True)

    def common(q):
        q.add_argument("--hotspot", choices=HOTSPOTS, default="maracaibo")
        q.add_argument("--bbox", type=float, nargs=4, metavar=("W", "S", "E", "N"), help="analysis window (default: hotspot demo window)")
        q.add_argument("--pixel", type=float, default=30.0, help="analysis pixel size in metres (block-averaged), default 30")
        q.add_argument("--threshold", type=float, default=3.0, help="seed contrast threshold in dB (grow at half), default 3")
        q.add_argument("--min-area", type=float, default=0.05, help="minimum anomaly area km2, default 0.05")
        q.add_argument("--out", help="output JSON path")

    s = sp.add_parser("search", help="list acquisitions and save a provenance catalogue")
    s.add_argument("--hotspot", choices=HOTSPOTS, default="maracaibo")
    s.add_argument("--start", default=(dt.date.today() - dt.timedelta(days=120)).isoformat())
    s.add_argument("--end", default=dt.date.today().isoformat())
    s.add_argument("--out")
    s.set_defaults(fn=cmd_search)

    n = sp.add_parser("nisar", help="analyse a NISAR L2 GCOV granule")
    common(n)
    n.add_argument("--granule", required=True, help="NISAR GCOV fileID, e.g. NISAR_L2_PR_GCOV_034_155_D_083_...")
    n.add_argument("--pol", default="HHHH", help="GCOV diagonal term (HHHH, VVVV, HVHV)")
    n.add_argument("--nesz", type=float, default=None, help="noise-equivalent sigma0 in dB, if known for this product")
    n.add_argument("--download", help="download the full HDF5 to this folder instead of streaming")
    n.set_defaults(fn=cmd_nisar)

    o = sp.add_parser("s1", help="analyse a Sentinel-1 RTC scene from Planetary Computer")
    common(o)
    o.add_argument("--item", required=True, help="sentinel-1-rtc STAC item id")
    o.add_argument("--compare", help="second item id for persistence (before/after)")
    o.set_defaults(fn=cmd_s1)

    t = sp.add_parser("selftest", help="synthetic unit test of the detector")
    t.set_defaults(fn=cmd_selftest)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
