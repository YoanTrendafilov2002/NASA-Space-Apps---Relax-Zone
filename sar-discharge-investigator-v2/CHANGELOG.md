# Changelog

## v2.0 — 2026-11-14: validation release

This release keeps all five screens, every data integration, the industry matrix, the hotspots and the full reference list. The scientific evidence for each change is in `VALIDATION.md`.

### Detector (app.js §8 and analysis.py `detect`, kept identical)

| # | Problem in v1 | Cause | Fix |
|---|---|---|---|
| D1 | Dark regions grew into noise-floor water. The supplied S1‑1 covered 280 km², and 87 % of its pixels were within 1 dB of NESZ | Only *seed* pixels had to clear the noise-floor test. Hysteresis *growth* was unrestricted | Both seeds and growth must now have background ≥ NESZ + T + 1 dB ("measurable" pixels) |
| D2 | NESZ compared against the wrong quantity | −22 dB is the Sentinel‑1 σ⁰ specification, but the product is γ⁰ | γ⁰-equivalent NESZ = −21 dB (σ⁰ ÷ cos 39°), with ±1 dB stated |
| D3 | Background was biased low inside large features | The background window included the feature's own dark pixels | Two-pass background: 3 km first pass, then 6 km with dark candidates excluded. Pixels whose window has < 25 % usable water get no background |
| D4 | Features larger than the background window were reported as if they were local anomalies | No scale check | New class **Regional** for features with area > π·(6 km half-window)² |
| D5 | Noise-floor features were reported as anomalies | No per-feature noise check | New class **Noise floor** for features with ≥ 50 % of pixels within 1 dB of NESZ |
| D6 | Shore distance was measured to in-water structures (the supplied 0.06 km came from a platform-sized JRC hole); dark shoreline rims were detected | Distance was taken to *any* non-water pixel; only 1 px of erosion | Land = non-water components ≥ 0.1 km² or touching the window edge. Smaller enclosed components are masked as in-water structures. A 100 m shoreline buffer is applied. Pixels without SAR coverage are not treated as land |
| D7 | Area used the nominal pixel size | The rounded grid has a slightly different true pixel size | Area = n × true pixel area (pxX × pxY) |
| D8 | "Length" was an ellipse estimate in isotropic pixels | 4√λ₁ formula with pixel units | `lengthKm` = extent along the principal axis in metres, including the pixel footprint. Added `ellipseMajorKm`, `fillRatio`, and an axis ratio that includes the pixel-variance term |
| D9 | Mean, background and contrast were averages of dB values | Averaging logarithms biases the result | Linear-power means converted to dB. Contrast = 10·log₁₀(background / anomaly). Added 95th-percentile contrast and ± speckle/quantisation uncertainty |
| D10 | Point targets were counted from the centroid minus an equivalent radius (511 inside a 30 km region in the supplied file); speckle in calm water could pass | Approximate distance formula; no noise reference | Exact distance to the feature boundary, reported as *inside* and *within 2 km* separately. Threshold = max(background, NESZ) + 10 dB |
| D11 | Persistence averaged comparison contrast even over unmeasurable pixels | No check on the comparison scene's signal | Uses only measurable comparison pixels and needs ≥ 50 % coverage. Output is a status (`recurs` / `partial` / `absent` / `untestable`) plus contrast and dark-pixel fraction |

### Comparing acquisitions

- New `comparability()` function. It reports platform, relative orbit, pass direction, time gap, instrument configuration, water background and measurable fraction for the scene pair. The result appears in Investigate, the report and the JSON export.
- `analysis.py s1 --compare` records the same metadata.

### Reasoning (app.js §9)

| # | Problem in v1 | Fix |
|---|---|---|
| R1 | Missing evidence was shown as ✓ "consistent" (rain with no data, currents, "no mapped facilities", noise-floor rows) | Four evidence levels: **supports / consistent / untested / contradicts**, plus **context** (never changes a status). A hypothesis with no usable measurement is **Untested** |
| R2 | Noise-floor anomalies came out as "Inconclusive, 7 consistent" | If the noise floor is *supported*, every surface hypothesis becomes *untested* and the verdict is **"Not interpretable — signal at the noise floor"** |
| R3 | No hypothesis could ever be favoured | *Supported* is used only when a measurement favours one explanation over another (noise floor, ERA5 calm, ERA5 rain, shoreline-buffer contact). Oil is never "supported" by single-band SAR |
| R4 | Wind rule allowed "oil consistent" even at < 2 m/s | If the wind upper bound is < 2 m/s, oil is untested: there are no Bragg waves for a film to damp |

### Presentation

- A three-tier evidence banner (Reasoning screen, report, JSON `evidence_tier`): **1. SAR-observed anomaly → 2. possible industrial discharge (hypothesis) → 3. verified pollution source (never established here)**.
- Class badges: Measurable / Regional / Noise floor. The anomaly overlay uses a matching colour per class, and water that cannot be measured is shaded.
- Contrast is shown with uncertainty, NESZ margin, noise fraction, extent, axis ratio, fill ratio and persistence status, each with a tooltip giving its definition.
- The evidence matrix now has 9 observation columns and a legend for the four evidence levels. Explanations are grouped by status.

### Basemap

- CARTO `light_all` and `light_only_labels` now return an "API KEY REQUIRED" image. They are replaced by OpenStreetMap standard tiles (default), with Esri World Imagery and Esri Light Gray in a layer switcher.
- If OSM tiles fail repeatedly, the map switches to Esri Light Gray automatically.

### Pipeline output (analysis.py)

- New fields: `contrast_unc_db`, `p95_contrast_db`, `ellipse_major_km`, `fill_ratio`, `noise_frac`, `regional`, `class`, `touches_shore_buffer`, `point_targets_inside`, `persist_cover`, `persist_frac`, `persist_status`, `detector_version`, `evidence_tier`, `measurable_water_fraction`, `in_water_structures`.
- JSON is written strictly: no `Infinity` or `NaN`.

### Tests

- Added `tests/test_detector.js` (27 checks) and `tests/test_analysis.py` (4 checks).
- `analysis.py selftest` is stricter: area within 15 % and contrast within 1 dB of truth.
