# SARtainty

NASA Space Apps Challenge 2026: **Dancing with the SARs**.

## Open the NISAR map

Run the launcher from this folder and open [the local explorer](http://127.0.0.1:4174/). The launcher sets up its Python environment. See the [portable hosting guide](QUICKSTART.md) for another computer or a public link.

```powershell
py -3 launch_relax_zone.py --without-ollama
```

The dark map opens on a georeferenced NISAR GCOV image. The Assistant input is visible when the map opens. Enter **show me wetlands** or choose a study area in **Manual**. The timeline and polarization matrix offer the channels and dates recorded for that site. Select **Download GeoTIFF** in the source panel to save the selected scientific crop.

The assistant uses a trained question router, measured data from the matrix and source-backed interpretations. Try **Show me wetlands**, **Show what changed at Curuai in HV**, or **Show me oil**. The [scientific review](NISAR_DATA_REVIEW.md) describes the answer checks and links to the recognition methodology.

The standalone assistant handles supported commands and evidence questions. If Ollama runs locally with `qwen3.5:4b`, launch without `--without-ollama` to add Qwen parsing for flexible phrasing. The server validates selections against the recorded sites and bounded NISAR catalogue results.

The [Curuai and Everglades checks](models/WETLAND.md) and [oil recognition report](results/recognition/oil/README.md) document the new model runs. Each uses measured radar predictors and separate references. The app shows their results in **Source audit → Recognition tests** on the applicable site.

For a temporary HTTPS demo, run the existing Cloudflare connector against `http://127.0.0.1:4174` with `--http-host-header 127.0.0.1:4174`. Start the app with `--public-origin` set to the exact HTTPS URL printed by Cloudflare. Guests use their own browser sessions for NASA access.

The following sections document the original investigation workflow and its historical scientific validation. Refer to the science review for the qualifications and adjustments in this release.

This web app helps you investigate dark surface anomalies in rivers, estuaries and lakes near industrial activity. It uses real NISAR and Sentinel‑1 SAR data. It does not label anything as oil or as illegal. Instead, it reports what the radar measured and which physical explanations those measurements still allow.

Workflow: **Choose hotspot → Find SAR data → Detect anomaly → Investigate explanations → Review findings**

| File | Purpose |
|---|---|
| `index.html` | Interface and styles |
| `app.js` | Live data access, in-browser SAR analysis, set-membership reasoning, report export |
| `analysis.py` | Calibrated NISAR GCOV and Sentinel‑1 RTC pipeline (needs Earthdata login for NISAR) |
| `requirements.txt` | Python dependencies |
| `tests/` | Regression tests: `node tests/test_detector.js`, `python tests/test_analysis.py` |
| `CHANGELOG.md`, `VALIDATION.md` | Change log and scientific validation report |

## 1. Quick start

```bash
# Web app (no install needed; it needs an internet connection)
python -m http.server 8000        # run in this folder
# then open http://localhost:8000

# Optional calibrated pipeline
pip install -r requirements.txt
python analysis.py selftest                                   # detector unit test (synthetic)
python analysis.py search --hotspot maracaibo                 # list NISAR + Sentinel-1 acquisitions
python analysis.py nisar  --hotspot maracaibo --granule <NISAR GCOV fileID>   # needs ~/.netrc for urs.earthdata.nasa.gov
python analysis.py s1     --hotspot maracaibo --item <sentinel-1-rtc item id> [--compare <item id>]
```

To view a result in the app, open **Investigate → NISAR GCOV → Load analysis.py result** and choose the JSON file the pipeline wrote. That file includes a quick-look image and the anomaly list.

## 2. Data sources (all live; nothing is simulated)

| Data | Source | Access | How the app uses it |
|---|---|---|---|
| NISAR L2 GCOV catalogue + public browse PNGs | ASF Search API `api.daac.asf.alaska.edu` | open, CORS | Acquisition list, footprints, quick-look overlay |
| NISAR GCOV HDF5 (`/science/LSAR/GCOV/grids/frequencyA/HHHH`) | `nisar.asf.earthdatacloud.nasa.gov` | Earthdata login | Calibrated analysis in `analysis.py` (streamed with range reads) |
| Sentinel‑1 IW GRD RTC γ⁰ (`sentinel-1-rtc`) | Microsoft Planetary Computer STAC + data API | open | Clearly labelled fallback / cross-frequency data, analysed in the browser |
| JRC Global Surface Water occurrence | Planetary Computer `jrc-gsw` | open | Water mask and shoreline distance |
| ERA5 10 m wind and precipitation | Open‑Meteo historical API (`models=era5`) | open | Wind and rain consistency checks |
| Mapped infrastructure | OpenStreetMap Overpass API | open | Context only. Unverified; proximity is not causation |
| AIS vessel tracks | **not connected** | no open, key-free global API | Bright point targets in the SAR image are reported instead (unidentified platforms or vessels). Global Fishing Watch (API token) and US MarineCadastre AIS (bulk download) are the realistic next integrations |

**Verified on 2026‑11‑14 through the live APIs:**

- NISAR GCOV granules intersecting each hotspot: Lake Maracaibo 21 (tracks 155 descending and 018 ascending, Jun–Nov 2026); Galveston Bay 81+; Bonny River 54.
- All are dual-pol HH/HV with a 40+5 MHz bandwidth, giving 10 m GCOV spacing for frequency A according to the ASF guide.
- Example product: `NISAR_L2_PR_GCOV_034_155_D_083_4005_DHDH_A_20261103T231052_20261103T231126_P05036_N_F_J_001` (5.85 GB HDF5).
- ASF browse PNGs are publicly readable. The HDF5 product needs a login.
- Sentinel‑1 RTC: 112 acquisitions over the Maracaibo demo window between Nov 2025 and Nov 2026, from S1C and S1D.

## 3. Research findings (Q1–Q4) — the full matrix is in the app (Industry Profile, CSV export)

Industries are ranked by SAR evidence, not by how striking they look:

| Rank | Industry | SAR detectability | Why |
|---|---|---|---|
| 1 | Crude production and ageing pipelines | **Supported** | Floating crude damps Bragg waves (Alpers & Hühnerfuss 1989). L‑band detection demonstrated (Minchew et al. 2012; ALOS PALSAR on Lake Maracaibo, Squier et al. 2015) |
| 2 | Ships and shipyards: illegal bilge discharge | **Supported** | Most frequent SAR-detected illicit discharge; linear slicks along shipping lanes (Brekke & Solberg 2005; EMSA CleanSeaNet) |
| 3 | Artisanal (illegal) refining | Conditional | Crude and residues are detectable, but creeks are narrow and light products evaporate (UNEP 2011; Onuh et al. 2021) |
| 4 | Licensed refineries, petrochemicals, terminals | Conditional | Only free-floating product releases (e.g. ITC Deer Park 2019) are visible. Treated effluent and dissolved chemicals are not |
| 5 | Used / waste-oil dumping | Conditional | Usually too small for 10–20 m pixels |
| 6 | Palm and vegetable-oil mills | Conditional | Fatty films damp waves like natural films, so they cannot be told apart at a single frequency |
| 7 | Food processing (FOG) | Unknown | Emulsified fats; no SAR evidence found |
| 8–11 | Metalworking, pulp & paper, textiles, tanneries | **Unlikely** | Emulsified, dissolved or suspended waste does not change surface roughness |

**Q2 operating patterns.** The app separates documented patterns from hypotheses:

- *Documented:* chronic recurring leaks at fixed infrastructure ("multiple and continuous", Lake Maracaibo, NASA EO 2021); linear bilge slicks; distributed creek-side illegal refineries (UNEP 2011).
- *Hypotheses, not established:* night-time operation, production-cycle discharges. The app suggests how to test them (dawn/dusk passes, permit discharge-monitoring data).

## 4. Hotspots (Q5)

| Hotspot | Industries | Water body vs. resolution | Role |
|---|---|---|---|
| **Lake Maracaibo, Venezuela** | Oil production, pipelines, shipping | ~100 km of open water | **Primary demo**: more than 10,000 installations and ~50-year-old pipelines leaking (NASA EO 2021); earlier L-band oil observations; dense NISAR and S1 coverage |
| Houston Ship Channel – Galveston Bay, USA | Refining, petrochemicals, shipping | Bay resolvable; channel 150–300 m (marginal) | Documented 2019 ITC release; NOAA SAR pollution reports exist for validation |
| Bonny River estuary, Niger Delta | Artisanal refining, oil production | Main channel only; most creeks too narrow | Strongest "illicit activity" evidence, weakest SAR feasibility |

## 5. Processing pipeline (detector v2 — see CHANGELOG.md and VALIDATION.md)

1. **Product choice.** NISAR GCOV and Sentinel‑1 RTC are already radiometrically terrain-corrected γ⁰ and geocoded, so no recalibration is done.
2. **Grid.** The analysis grid is 20–45 m. True pixel sizes (pxX, pxY) are used for every metric.
3. **Water, land and structures.** These come from JRC occurrence:
   - water ≥ 80 %;
   - land = non-water components ≥ 0.1 km² or touching the window edge;
   - smaller enclosed non-water blobs are masked as in-water structures (platforms);
   - a 100 m shoreline buffer is excluded.
4. **Speckle.** 5×5 Lee filter (ENL 4.4).
5. **Two-pass background.** First a 3 km mean. Then a 6 km mean with dark candidates excluded. A pixel gets no background if fewer than 25 % of the water pixels in its window are usable.
6. **Measurability.** Only pixels with background ≥ NESZ + T + 1 dB may seed or grow a region. NESZ is −21 dB γ⁰ for Sentinel‑1 (−22 dB σ⁰ ÷ cos 39°, ±1 dB). Water that cannot be measured is shaded on the map.
7. **Segmentation.** Hysteresis: seed ≥ T dB (default 3), grow ≥ T/2, 8-connected, minimum area 0.05 km².
8. **Descriptors:**
   - Area = n·pxX·pxY.
   - Extent along the principal axis; axis ratio √(λ₁/λ₂); fill ratio.
   - Contrast = 10·log₁₀(mean background / mean anomaly), using linear means, with ± speckle/quantisation uncertainty and the 95th percentile.
   - Margin above NESZ and the fraction of pixels at the noise floor.
   - Distance to land and permanent-water share.
   - Bright point targets inside and within 2 km of the boundary.
9. **Classes:**
   - **Measurable** — a local feature with a valid background.
   - **Regional** — larger than the background window (π·6 km²), so its contrast is ill-defined.
   - **Noise floor** — ≥ 50 % of pixels within 1 dB of NESZ; not interpretable.
10. **Persistence.** The same pixels are measured in an independently processed second scene (needs ≥ 50 % measurable). Status is recurs / partial / absent / untestable. The scene-pair comparability (relative orbit, pass, platform, water background) is reported with it.
11. **Surface-condition screening.** A scene is usable only if its median water γ⁰ in the demo window is ≥ NESZ + 4 dB (−17 dB).

**Reasoning (set membership, four evidence levels).** Seven explanations are tested: mineral-oil film, biogenic film, low wind, current front, rain cell, shoreline artefact, noise floor. Each observation is recorded per explanation as:

- **supports** — a measurement favours this explanation over an alternative;
- **consistent** — measured and compatible;
- **untested** — no usable measurement (never counted as support);
- **contradicts** — a measured interval lies outside what the explanation can produce;
- **context** — infrastructure and point targets; these never change a status.

How explanations and verdicts are decided:

- An explanation with only untested evidence is reported as *Untested*.
- If the noise floor is *supported*, every surface explanation becomes untested, and the verdict is "Not interpretable".
- No probabilities are reported.
- An oil film is never "supported" by single-band SAR.

**Evidence tiers** are shown throughout: (1) SAR-observed surface anomaly → (2) possible industrial discharge, a hypothesis → (3) verified pollution source, which needs in-situ evidence and is never established by this tool.

**Basemap:** OpenStreetMap (default), with Esri World Imagery and Esri Light Gray in the layer switcher; all are key-free. CARTO was removed because it now requires an API key.

## 6. Validation

See **VALIDATION.md**. In summary:

- **The supplied Lake Maracaibo result was reproduced exactly with v1.** The S1‑1 values (279.90 km², contrast 3.08 dB, persistence 0.52 dB) match the exported JSON.
- **The v1 diagnosis.** 81.5 % of that region's pixels had a background below the v1 seed limit, and 92.9 % sit at or below the γ⁰ noise floor.
- **The corrected run (v2).** It splits the footprint into one regional and nine noise-floor features. 60 % of the footprint is not measurable. The largest noise-floor part is classed "Not interpretable".
- **Regression tests.**
  - `node tests/test_detector.js`: 27/27 pass. v1 run on the same suite: 5 pass, 19 fail or crash.
  - `python tests/test_analysis.py`: 4/4 pass.
  - `python analysis.py selftest`: passes.
- **Not done:** no ground truth or reference slick maps were available.

## 7. Scientific limitations

- A dark SAR feature is **never** identified as oil. Biogenic films, low wind, rain cells and fronts produce the same signature.
- Lake Maracaibo is usually calm. ERA5 (~25 km) often returns a single coastal grid cell for the whole window.
- L‑band slick contrast is generally weaker than C‑band (Gade et al. 1998). The NISAR noise floor must be supplied with `--nesz`.
- The browser analysis uses 8‑bit γ⁰ (0.12 dB steps). Use `analysis.py` for full precision.
- These thresholds are documented design choices, not calibrated values: shoreline buffer, structure size, noise fraction, persistence.
- OpenStreetMap infrastructure coverage is incomplete.
- Nothing in this tool establishes the source of a feature or any legal wrongdoing.

## References

The full list is in the app and in every exported report. Key sources:

- Alpers & Hühnerfuss 1989 (JGR)
- Gade et al. 1998 (JGR)
- Brekke & Solberg 2005 (RSE)
- Fingas & Brown 2014 (MPB)
- Minchew et al. 2012 (IEEE TGRS)
- Topouzelis 2008 (Sensors)
- NASA Earth Observatory 2003, 2021
- Squier et al. 2015
- UNEP 2011
- Onuh et al. 2021
- NOAA DARRP (ITC Deer Park)
- ASF NISAR Data User Guide
- Pekel et al. 2016
- Hersbach et al. 2020
- Lee 1980
