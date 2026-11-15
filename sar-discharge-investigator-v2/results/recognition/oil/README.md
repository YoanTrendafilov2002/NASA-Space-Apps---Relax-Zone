# NISAR oil imagery recognition trial

This experiment fits a coherent dark-water screen using NISAR L-band HH only. It learns a single threshold on the Oman acquisition and checks the frozen rule against the separate UAE case. Independent reference images supply timed proxy outlines for evaluation. The screen outputs candidate darkening and abstains where radar or surrounding support is insufficient.

## Results

| Case and method | Assessable proxy area | Overlap among assessable proxy cells | Flags in eligible unlabelled water |
| --- | ---: | ---: | ---: |
| Oman calibration, coherent | 21,147 of 54,577 cells (38.75%) | 1,446 of 21,147 (6.84%) | 8,649 of 262,230 (3.30%) |
| Oman calibration, prior 1.5 dB | 21,866 of 54,577 (40.06%) | 123 of 21,866 (0.56%) | 1,247 of 266,638 (0.47%) |
| UAE held out, coherent | 172 of 46,942 (0.37%) | 0 of 172 | 361 of 25,434 (1.42%) |
| UAE held out, prior 1.5 dB | 2,075 of 46,942 (4.42%) | 0 of 2,075 | 7 of 39,396 (0.018%) |

The coherent score increased descriptive overlap in Oman and flagged more unlabelled water. The UAE test yielded zero overlap under either rule, with the coherent rule assessing fewer proxy cells. This trial provides no positive evidence that the new rule transfers to a separate incident. Unlabelled water can contain either class and the reported incidents do not supply time-matched pixel labels. Precision, false-positive rate and confirmed oil area remain unestimated. [Falcon Eye UAE perimeter](https://www.ess-uae.me/post/satellite-oil-spill-monitoring-uae-umm-al-quwain) · [NASA GIBS optical data guide](https://nasa-gibs.github.io/gibs-api-docs/) · [ASF NISAR GCOV radiometry](https://nisar-docs.asf.alaska.edu/gcov/)

## Reproduce

The predeclared variant averages capped local contrast in dB with a Gaussian kernel of 180 m standard deviation. A cell must pass the upstream 3 dB signal-to-noise and bilateral-reference controls, and have at least 75% weighted neighbourhood support. The original minimum component size of three cells stays in force. The Oman-only 99th percentile was 0.4723 dB across 1,558,099 radar-eligible cells. Rounded upward to the next 0.05 dB, that sets the frozen threshold to 0.50 dB. The calibration crop was originally chosen around the proxy. Labels were used only after fitting. The 1% calibration quantile concerns the full radar crop, while the table describes the more concentrated reference buffers. No parameter was adjusted in response to the UAE results.

From the app folder, use the local QGIS Python environment and the cached NISAR screens under `set`.

```powershell
& 'C:/Program Files/QGIS 3.44.15/bin/python-qgis-ltr.bat' -B models/oil_recognition.py `
    --data-root 'C:/Users/Yoan/Documents/Codex/2026-11-14/set' --output-dir results/recognition/oil
```

[Metrics and source hashes](summary.json) · [Oman screen](oman-coherent-screen.png) · [UAE screen](uae-coherent-screen.png)
