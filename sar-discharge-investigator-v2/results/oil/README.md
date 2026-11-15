# NISAR dark-water screen calibration

The study used saved NISAR L-SAR GCOV frequency-A HH power derived from real acquisitions, on a 60 m grid. The proxy masks came from a separate optical or radar sensor and described selected surface features. Both outside areas have unknown material labels. The results establish spatial overlap with these proxies under a signal and coverage screen.

| Case | Reference and timing | Proxy eligible | Overlap of eligible proxy | Flags in eligible unlabelled buffer |
| --- | --- | ---: | ---: | ---: |
| Oman, 11 August 2026 (calibration) | Suomi NPP VIIRS bright-core digitization, 319–326 min earlier | 21,866 of 54,577 cells (40.06%) | 123 cells, 0.4428 km² (0.563%) | 1,247 of 266,638 cells (0.468%) |
| Umm Al Quwain, 21 October 2026 (held out) | Falcon Eye outline from independent Sentinel-1D VV, 38–40 min later | 2,075 of 46,942 cells (4.42%) | 0 cells (0%) | 7 of 39,396 cells (0.018%) |

The fitted 1.5 dB contrast threshold maximized Oman proxy overlap among tested thresholds with a 1% review limit in eligible unlabelled buffer cells. That limit is a study design choice. The 3 dB baseline had zero eligible overlap in both cases. No overlap was observed in the UAE test after fixing the threshold on Oman. The UAE reference had little eligible area. Possible surface motion and sensor-specific feature visibility affect both references. Precision and false-positive rate are unestimated because neither proxy provides verified negative labels. [NASA GIBS](https://nasa-gibs.github.io/gibs-api-docs/), [Falcon Eye source figure and case](https://www.ess-uae.me/post/satellite-oil-spill-monitoring-uae-umm-al-quwain)

- [Machine-readable results and input provenance](summary.json)
- [Oman threshold sweep](oman-calibration.csv)
- [Oman assessment preview](oman-screen.png)
- [UAE assessment preview](uae-screen.png)

## Reproduce

From the application folder, run the existing QGIS Python installation with the local `set` dataset directory. This step reads the saved feature caches and independent polygons. The upstream feature preparation is in `set/work/nisar_slick_validation.py`. It uses GCOV masks, modeled noise and bilateral local water controls. The re-run verifies that eligibility and contrast/noise relationships match the saved arrays.

```powershell
& 'C:/Program Files/QGIS 3.44.15/bin/python-qgis-ltr.bat' -B models/oil.py `
    --data-root 'C:/Users/Yoan/Documents/Codex/2026-11-14/set' --output-dir results/oil
```
