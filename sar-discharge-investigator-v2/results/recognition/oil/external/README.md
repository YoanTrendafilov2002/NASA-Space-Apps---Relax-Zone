# September 22 Oman external-test screening

**Excluded before scoring.** The frozen model accepts NISAR frequency-A HH features on the calibrated 60 m grid. The candidate acquisition has only frequency-B VV at 80 m, and it observes the Caroline Bezengi incident already represented in the August 11 calibration case. Regridding the VV features would change the spatial support without providing the missing HH measurement or a different incident. The unchanged loader rejected the archive at its input-domain check.

| Evidence | Verified result |
| --- | --- |
| NISAR acquisition | 2026-09-22 01:40:48–01:41:22.999342 UTC, ascending track 56, frame 10. [NASA CMR granule G4326119938-ASF](https://cmr.earthdata.nasa.gov/search/granules.umm_json?concept_id=G4326119938-ASF) |
| Native archive | Provisional L-SAR GCOV `0005_NASV`; `/science/LSAR/GCOV/grids/frequencyB/VVVV`; Float32, EPSG:32640, 80 m, 4,185 rows × 4,266 columns. Local 268,435,456-byte HDF5 matches the ASF catalogue size. [ASF asset metadata](https://api.daac.asf.alaska.edu/services/search/param?granule_list=NISAR_L2_PR_GCOV_031_056_A_010_0005_NASV_A_20260922T014048_20260922T014122_P05023_N_F_J_001&output=geojson) · [GCOV layer definitions](https://nisar-docs.asf.alaska.edu/gcov/) |
| Cached screening grid | `sep22_alisio_80m/screen_layers.npz`, 340 rows × 297 columns, EPSG:32640. The axes advance 80 m east and 80 m south per cell. |
| Optical reference | IAC published an independent DRAGO-2 SWIR image for September 22 and identified the same tanker. The public source gives the date only. Local registration treats the outline as a positive-only proxy and records 46–999 m outline sensitivity when omitting individual control points. An exact radar–optical time offset remains unverified. [IAC mission report](https://www.iac.es/en/outreach/news/alisio-1-satellite-detects-oil-spill-arabian-sea-space?base_route_name=entity.node.canonical&overridden_route_name=entity.node.canonical&page_manager_page=node_view&page_manager_page_variant=node_view-panels_variant-2&page_manager_page_variant_weight=-4) |

The historical VV cache contains 4,092 proxy cells, all eligible under its own coverage, local reference and 3 dB signal-margin gate. Another 22,402 eligible cells lie outside the outline and have unknown material. These are archive checks. The frozen HH model was never applied, so its eligible and matched counts are unavailable.

The August 12 Oman archive also covers the same incident. The reviewed Burgas, Curuai and Everglades archives lack a suitable timed oil proxy for this test. The existing UAE held-out evaluation remains the available independent incident test.

**Input SHA-256**

- NISAR HDF5 `45fd1ac81915c911c9d64a9b997ac07408a89cc84f61463fdb95379c41c2e4cd`
- VV feature cache `ec091e50e2d2cc5c3c4a87a1dcc41a98b23520679ba597a46fadc2bc583ed725`
- Frozen recognition script `3fe0923b318859348ec4293a40bef255b9f5e872cf3fc89a235259a6d36ad84a`
