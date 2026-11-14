# Scientific and technical validation — SAR Discharge Investigator v2

**Date:** 2026‑11‑14

**Inputs:**

- v1 source code: identical to the delivered ZIP. The ZIP itself was not re-uploaded, so the session copy was used.
- `sar_investigation_maracaibo.json`, exported from v1.

**Method:**

1. Code audit.
2. Exact reproduction of the v1 result on live data in a real browser (Chromium on the user's desktop, live Microsoft Planetary Computer, JRC and Open‑Meteo APIs).
3. Diagnosis on the same pixels.
4. Fixes.
5. Synthetic regression tests.
6. Re-run of the corrected detector on the identical inputs.

This validation contains no ground truth: no in-situ samples, no analyst-reviewed slick maps and no optical cross-check were available. Nothing below identifies a pollutant or a source.

---

## 1. Reproduction of the supplied investigation (v1)

**Inputs, read from the JSON and re-queried live:**

| Item | Value |
|---|---|
| Scene | `S1C_IW_GRDH_1SDV_20260921T225819_…_013027_rtc` (Sentinel‑1C, 1 slice) |
| Comparison | `S1D_IW_GRDH_1SDV_20260903T2258{12,41}_…_0082F3_rtc` (Sentinel‑1D, slices 1/11 and 2/11, same datatake, mosaicked) |
| Window | −71.6898, 9.9702, −71.2898, 10.3702 |
| Grid | 974 × 983 px at 45 m |
| Parameters | T = 3 dB, min area 0.05 km², NESZ −22 dB (v1 setting) |

The v1 code was run unchanged on the live data. **Every value of anomaly S1‑1 matched the JSON to all printed digits:**

- n = 138,223 px; area 279.901575 km²
- mean −22.7566 dB; background −19.6781 dB; contrast 3.0785 dB
- elongation 1.2801; length 29.65 km; edge step 1.6936 dB
- shore 0.06 km; permanent water 0.99998
- point targets within 2 km: 511 (nearest 0 km)
- persistence 0.5186 dB

The scene contained 61 features in total.

## 2. Diagnosis of the supplied anomaly (v1 S1‑1, same pixels)

| Check | Result | Implication |
|---|---|---|
| Pixels whose background was below the v1 seed limit (NESZ + T + 1 = −18 dB) | **81.5 %** | Only 13.3 % (18,452 px) could seed. The rest joined by growth, which v1 did not test against the noise floor (defect D1) |
| Pixels below −22 dB (v1 NESZ) | 75.8 % | The "dark" signal is mostly instrument noise |
| Pixels within 1 dB of / below the γ⁰ NESZ (−21 dB) | **92.9 %** | Same conclusion with the corrected NESZ |
| Equivalent diameter vs. background window | 18.9 km vs 3.0 km | The contrast was taken against a background inside the feature (D3, D4) |
| Mean as linear power vs. mean of dB | −22.39 vs −22.76 dB | Averaging in dB biased the value (D9) |
| Contact with land (4-neighbours) | 0 pixels | The 0.06 km "shore distance" was to a diagonal non-water JRC pixel inside the lake. v2 finds 32 such in-water structures in this window (D6) |
| Point targets "within 2 km" | 511, nearest 0 km | Centroid-minus-radius distance does not work for a 30 km irregular region; most of the points lie inside it (D10) |

Visually, the region coincides with the dense platform field off the eastern shore. There the water is near the noise floor while hundreds of bright point targets are visible. This fits calm or sheltered water. It does not show that a film is present.

## 3. Corrected result (v2) on identical inputs

**Scene level, S1C 2026‑09‑21:**

- Linear-mean water γ⁰: −14.65 dB. (v1 reported −16.79 dB as a mean of dB values.)
- Measurable water (background ≥ NESZ + T + 1): 70.5 %. Low-signal water (background < NESZ + 3): 11.0 %.
- 764 bright point targets; 32 in-water structures masked.
- Regional-scale threshold: 27.7 km².
- 49 features ≥ 0.05 km² (v1: 61). Of the 40 listed: **26 measurable, 1 regional, 13 at the noise floor.**

**Where the v1 S1‑1 footprint went.** Its pixels are now split across 10 features:

- v2 S1‑1 (regional): 20,891 px
- S1‑2 (noise floor): 16,551 px
- S1‑3 (noise floor): 8,961 px
- 7 smaller noise-floor features

60 % of the old footprint is not measurable and belongs to no feature.

| v2 feature | Class | Area | γ⁰ / background | Contrast | Noise fraction | Persistence (09‑03) | Verdict (ERA5 2.14 m/s, 0 mm) |
|---|---|---|---|---|---|---|---|
| S1‑1 (10.149 N, 71.605 W) | Regional | 193.9 km² | −18.45 / −13.89 dB | 4.57 ± 0.05 dB | 0.41 | absent (0.38 dB, 21 % dark) | Inconclusive. Consistent: oil, biogenic, low wind, noise. Untested: current, rain. Contradicted: shore |
| S1‑2 (10.203 N, 71.494 W) — largest noise-floor overlap with v1 S1‑1 | Noise floor | 46.97 km² | −21.14 / −15.49 dB | 5.66 dB | 0.78 | absent (0.32 dB) | **Not interpretable — signal at the noise floor.** Noise supported; all surface hypotheses untested; shore contradicted |
| S1‑4 (9.994 N, 71.336 W) — largest measurable | Measurable | 18.55 km² | −19.11 / −15.10 dB | 4.00 ± 0.05 dB | 0.36 | absent (0.13 dB) | Inconclusive. Same statuses as S1‑1 |

**Comparison with the supplied JSON.** The JSON reported the anomaly as "Inconclusive — 7 explanations remain consistent", including shore artefact and noise floor, based on one 280 km² region. With the corrections:

- The dominant part of that region is **not interpretable**: its signal is at the noise floor.
- The shore-artefact explanation is **contradicted** (≥ 6.7 km from land).
- No feature in the window gives positive evidence for an oil film.
- Every measurable feature was **absent** in the 2026‑09‑03 scene. That is consistent with transient wind or film patterns, and it is not evidence of a release.

ERA5 cannot discriminate between features here: Open‑Meteo returned the same coastal grid cell (10.25 N, 71.25 W) for all of them.

## 4. Sentinel‑1C vs Sentinel‑1D comparability

Values read from the STAC metadata for this pair:

| Property | Value |
|---|---|
| Relative orbit | 4 for both (same ground track and nominal incidence angle) |
| Pass direction / time | Ascending, ≈22:58 UTC |
| Instrument configuration ID | 5 |
| Timeliness | Fast‑24h |
| Orbit source | RESORB |
| ENL | 4.4 |
| Pixel spacing | 10 m |
| Time gap | 18 days |

**Geometry:** comparable.

**Radiometry:**

- Absolute inter-unit calibration offsets were not quantified, because no calibration data were accessed.
- Persistence compares *contrast against a background in the same scene*, so a constant offset cancels to first order.
- Wind dominates the difference between scenes: water −14.65 vs −13.95 dB; measurable water 70.5 % vs 81.7 %.
- The local incidence angle is not in the STAC properties, so the γ⁰ NESZ conversion uses a nominal 39° (±1 dB).

The app now computes and displays these checks for any scene pair and flags pairs from different relative orbits or pass directions.

## 5. Definitions verified

All checks below are exact, on synthetic test scenes. They are in `tests/test_detector.js`, mirrored by `tests/test_analysis.py`.

| Quantity | v2 definition | Test |
|---|---|---|
| Area | n × pxX × pxY | 40×4 px rectangle, 30 × 31 m: 0.148800 km² exact (v1: 0.144000) |
| Extent ("length") | Range of pixel positions projected on the principal axis, plus the pixel footprint | 1.2000 km exact (v1: 1.3852) |
| Axis ratio | √(λ₁/λ₂) of the second moments in metres, plus pixel variance px²/12 | 9.687 vs analytic 9.677 (v1: 10.325) |
| Fill ratio | area / (π·2√λ₁·2√λ₂) | 0.956 vs 3/π |
| Contrast | 10·log₁₀(background / anomaly), linear means | 7.99 dB noiseless; 7.54 dB with speckle, truth 8 |
| Persistence | Same pixels in an independently processed second scene; ≥ 50 % measurable | recurs / absent / untestable correctly identified |

## 6. Regression results (executed)

| Suite | v2 | v1 on the same suite |
|---|---|---|
| `tests/test_detector.js` (27 checks: detection, false alarms, noise-floor leakage, regional class, shoreline, geometry, persistence, reasoning semantics) | **27 / 27 pass** | 5 pass, 19 fail or crash. The crashes are partly due to the new output fields. The substantive failures are noise-floor leakage, shoreline rim detection, distance to structures, area, extent, and missing evidence counted as consistent |
| `tests/test_analysis.py` (Python parity) | 4 / 4 pass | — |
| `analysis.py selftest` | Pass: area 2.121 vs 2.017 km²; contrast 7.57 vs 8.0 dB; 0 false alarms | — |
| GCOV reader (mock HDF5 layout) | Pass | — |
| Transcription check: browser vs node on the deterministic rectangle | 6 metrics identical to 9 decimals | — |
| UI end-to-end, headless Chromium, mocked network | All 5 screens, filters, tabs, layer toggles, import, CSV/MD/JSON export; no console errors; no CARTO requests; OSM requested; layer switcher present | — |

**Basemap:** CARTO `light_all` and `light_only_labels` tiles were fetched live and are a 2,049-byte "API KEY REQUIRED" image (visually confirmed). OpenStreetMap and both Esri services returned valid tiles.

## 7. Not validated / limitations

- **No ground truth.** No detected feature has been matched to in-situ data, NOAA or EMSA analyst products, or Sentinel‑2.
- **Thresholds are design choices, not calibrated.** This includes ±2 m/s on ERA5, the 0.1 km² structure size, the 100 m shoreline buffer, the 50 % noise fraction and the persistence thresholds. Each is documented in the code and shown in the app.
- **Speckle uncertainty only.** The contrast ± covers speckle and quantisation. It excludes NESZ and calibration uncertainty and assumes independent pixels (nearest-neighbour sampling from 10 m data).
- **Python pipeline not run end-to-end.** `analysis.py s1` and `analysis.py nisar` were not run against real downloads here (no data access from the build environment). The Python detector matches the JavaScript one on the synthetic suite.
- **UI tested in parts.** The full UI was not exercised against live services in one session. The live data path and the detector were run in a real browser; the UI flow was run with mocked responses.
