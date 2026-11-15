# NISAR exposed-water experiment at Curuai

`wetland.py` ports the single-date threshold and paired-window checks from Set up QGIS (`nisar_wetland_validation.py` and `nisar_wetland_seasonal.py`). It also fits a class-balanced L2 logistic classifier to NISAR HH gamma0 dB and the HH minus HV observed dB contrast. Both classifiers are fitted to August only. The October file is opened after the coefficients and cutoff are saved.

## Reproduce the run

Use Python 3.12 or compatible, NumPy, h5py, SciPy and the GDAL Python bindings. This machine already has these through QGIS LTR and the Set up QGIS workspace's `burgas_deps` directory. The command below runs from the app repo root in PowerShell. Supply existing local files. The script performs no acquisitions and copies no source HDF5.

```powershell
$qgis = 'C:/Program Files/QGIS 3.44.15/bin/python-qgis-ltr.bat'
$previous = 'C:/Users/Yoan/Documents/Codex/2026-11-14/set'
$august = 'C:/Users/Yoan/Downloads/NISAR_L2_PR_GCOV_028_003_A_001_2005_DHDH_A_20260813T092154_20260813T092229_P05023_N_F_J_001.h5'
$october = 'C:/Users/Yoan/Downloads/NISAR_L2_PR_GCOV_033_003_A_001_2005_DHDH_A_20261012T092154_20261012T092229_P05023_N_F_J_001.h5'
$model = (Resolve-Path 'models/wetland.py').Path
$env:PYTHONIOENCODING = 'utf-8'
& $qgis -u -c "import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1), run_name='__main__')" `
  "$previous/work/burgas_deps" $model --august-h5 $august --october-h5 $october `
  --optical-directory "$previous/outputs/nisar_wetlands" --out 'results/wetland/curuai'
```

The optical directory supplies `curuai_20260813_optical_reference.tif` and `curuai_20261012_optical_reference.tif` with their matching `_optical_provenance.json` sidecars. Input IDs, timestamps, checksums and class counts are recorded in `research-report.json`. Each episode uses an ascending track 3, frame 1, left-looking radar pass and a Sentinel-2A Level-2A acquisition about 4 hours and 52 minutes later. [NISAR GCOV documentation](https://nisar-docs.asf.alaska.edu/gcov/), [August Sentinel-2A item](https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/S2A_21MXT_20260813_0_L2A), [October Sentinel-2A item](https://earth-search.aws.element84.com/v1/collections/sentinel-2-l2a/items/S2A_21MXT_20261012_0_L2A).

The radar filter requires consistent quality and valid power and noise estimates across all nine native 20 m postings in each 60 m cell. The fit uses linear gamma0 power averaged before conversion to dB. Preprocessing applies the original HH noise-floor margin, with additional dual-channel net SNR stress tests in the report. Optical labels require agreement between the Sentinel-2 scene classification layer and modified NDWI after a 40 m cloud and shadow buffer. At least eight of nine optical postings must agree for a 60 m reference label. Three-kilometre checkerboard blocks and a one-cell boundary buffer define the August fit and spatial holdout. October scores are available on both all clear cells and the geography withheld from the August fit. [Sentinel-2 processing](https://sentiwiki.copernicus.eu/web/s2-processing), [Xu's modified NDWI paper](https://doi.org/10.1080/01431160600589179).

## Results against the independent optical proxy

| Evaluation | Common assessed cells | HH threshold IoU | HH and HH–HV logistic IoU |
| --- | ---: | ---: | ---: |
| August spatial holdout | 247,769 | 0.972 | 0.971 |
| October temporal transfer on withheld blocks | 181,423 | 0.444 | 0.400 |
| August–October exposed-water loss on paired withheld blocks | 146,534 | 0.136 | 0.092 |

The threshold fit was −15 dB. For paired optical loss, the threshold recovered 4,359 of 31,808 labelled loss cells and the logistic model recovered 2,949. The latter did not improve transfer. There were no optical gain positives on paired assessed cells, so gain recall is undefined. One site and two dates support an exposed-water screening comparison. Field observations, inundation beneath vegetation, cross-site transfer and drought attribution remain untested.

## Output guide

- `frozen-model.json` contains the August-only cutoff, scaling and fit coefficients. Logistic scores are class-balanced decision scores without probability calibration.
- `metrics.csv` and `research-report.json` contain coverage, abstentions, counts and metrics. The original QGIS August calibration and confusion matrix were reproduced exactly. An independent Rasterio check rebuilt the 60 m optical labels and spatial holdout from the reference and re-counted the August, October and paired-loss confusion matrices for both classifiers. A separate local check reproduced native optical water and other labels from the cached SCL and MNDWI layers.
- Six EPSG:32721 GeoTIFFs contain research flags on the common dual-channel support. The date maps use 0 (unassessable), 1 (exposed-water flag), 2 (no exposed-water flag). The pair maps use 0 (missing support), 1 (loss flag), 2 (gain flag), 3 (no flag of change). The paired rasters were checked against both per-date rasters. No source data or review artifacts are needed to open the output GeoTIFFs.

## Recognition revalidation

`results/recognition/wetlands/baseline/` contains the fresh baseline run. The calibration and all 18 score rows reproduced exactly. `wetland_recognition.py` adds one feature to the two-feature logistic fit. It measures the standard deviation of HH across nine adjacent 60 m cells. The complete neighborhood must pass radar quality checks and belong to one side of the 3 km checkerboard split. Both fits use the same training and evaluation cells. Training and the test criterion were saved before loading October. October had already been used to benchmark the original models.

From the app root, use the QGIS and source variables established above.

```powershell
$model = (Resolve-Path 'models/wetland_recognition.py').Path
& $qgis -u -c "from pathlib import Path; import runpy,sys; deps=sys.argv.pop(1); script=sys.argv.pop(1); sys.path[:0]=[deps,str(Path(script).parent)]; runpy.run_path(script,run_name='__main__')" `
  "$previous/work/burgas_deps" $model --august-h5 $august --october-h5 $october `
  --optical-directory "$previous/outputs/nisar_wetlands" --out 'results/recognition/wetlands'
```

| Evaluation | Assessed on common support | HH cutoff IoU | HH and HH–HV IoU | Added HH texture IoU |
| --- | ---: | ---: | ---: | ---: |
| August spatial holdout | 247,502 | 0.972 | 0.971 | 0.969 |
| October temporal holdout | 181,130 | 0.444 | 0.400 | 0.406 |
| Paired exposed-water loss | 146,358 | 0.136 | 0.092 | 0.090 |

The texture candidate fell short of its joint improvement criterion. Its October exposed-water IoU improved by 0.0064 over the corresponding two-feature fit, with a within-site spatial resampling interval of 0.0045 to 0.0086. It scored below the original HH threshold on October and paired loss. An independent implementation read the source HDF5 slices, aligned the offline optical labels with Rasterio, and reproduced all nine confusion matrices. `summary.json` records the supported evidence scope. `protocol.json`, `frozen-models.json`, `metrics.csv` and `research-report.json` contain the full run records.

### Separate hydrologic check in the Everglades

`replicate_eden.py` reruns the original Set up QGIS workflow with the saved NISAR file, USGS EDEN depth model and USFWS National Wetlands Inventory. The spatial holdout evaluates modeled depth of at least 10 cm against modeled zero depth in current NWI-mapped emergent marsh. The rerun matches the archived results exactly. At 400 m, dual polarization improved IoU from 0.662 to 0.829 on 2,813 of 4,417 eligible cells, with 1,604 radar abstentions. The training and evaluation concern one date in the Everglades. October transfer at Curuai remains a distinct exposed-water test. [USGS EDEN surface-water model](https://pubs.usgs.gov/publication/sir20205083), [USFWS NWI](https://www.fws.gov/program/national-wetlands-inventory/data-download).

```powershell
$model = (Resolve-Path 'models/replicate_eden.py').Path
$everglades = 'C:/Users/Yoan/Downloads/NISAR_L2_PR_GCOV_034_047_A_014_2005_DHDH_M_20261027T104408_20261027T104439_P05036_N_F_J_001.h5'
& $qgis -u -c "import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); runpy.run_path(sys.argv.pop(1),run_name='__main__')" `
  "$previous/work/burgas_deps" $model --h5 $everglades --qgis-work "$previous/work" `
  --reference-directory "$previous/outputs/nisar_wetlands/everglades" --out 'results/recognition/wetlands/everglades'
```
