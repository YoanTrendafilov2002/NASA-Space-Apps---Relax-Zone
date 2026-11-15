# NISAR map assets

The explorer uses 15 km square windows from three NASA–ISRO NISAR L-band GCOV granules archived by ASF DAAC. Umm Al Quwain has one acquisition on 21 October 2026. The Curuai floodplain has two matching windows, acquired on 13 August and 12 October 2026. Every window contains 750 × 750 native samples at 20 m posting and contains frequency-A HH and HV. [ASF GCOV guide](https://nisar-docs.asf.alaska.edu/gcov/), [Curuai floodplain study](https://doi.org/10.1002/2013WR014091).

`data/non-sentinel-nisar.json` records the official acquisition footprint and local coverage window separately. Each evidence entry lists the original Earthdata file URL, the verified ASF byte count, and the local file's SHA-256. The original files remain in the existing download directory. The builder reads small scientific windows and streams source checksums in place.

## Rebuild

Run the following in PowerShell from the app repo using Python 3.11 or later with h5py, NumPy, Pillow and Rasterio. The three source basenames are listed in `models/build_nisar_map_assets.py`. ASF catalogue metadata is fetched over HTTPS. The builder uses existing local HDF5s and the archived QGIS HH TIFF to check the extraction.

```powershell
$python = 'C:/Users/Yoan/Documents/Codex/2026-11-14/files-pasted-by-the-user-build/work/venv/Scripts/python.exe'
$qgisRaster = 'C:/Users/Yoan/Documents/Codex/2026-11-14/set/outputs/nisar_uae/nisar_only_map/layers/NISAR_Lband_HH_20261021T0135Z.tif'
& $python -u models/build_nisar_map_assets.py --downloads 'C:/Users/Yoan/Downloads' --uae-qgis $qgisRaster
```

## Scientific downloads and display

- `/science/` GeoTIFFs use native UTM and preserve measured, unitless linear gamma0 Float32 power. NaN represents samples excluded by the subswath, exception, looks and finite-power checks. Embedded HDF5 metadata records thermal noise correction as false for these three products.
- `/sar/` PNGs are transparent, north-up Web Mercator renderings. Each display pixel centre maps to its nearest source cell. Display stretches use `10 × log10(power)` with fixed HH limits of −26 to −8 dB and HV limits of −33 to −16 dB. The map bounds derive from the actual destination affine transform.
- The full six science windows matched the corresponding HDF5 arrays bit for bit. Independent PyProj and Rasterio checks reproduced the PNG intensity from source power at 330 geographic pixel centres. Every local coverage window lies inside the official ASF footprint. The 562,500 UAE HH postings also matched the archived QGIS HH values exactly after conversion to dB.

## Measure the observation matrix

Run `python models/analyze_nisar_matrix.py` with NumPy and Rasterio to produce `data/nisar-measurements.json`. The script reads the fourteen authenticated-source backscatter GeoTIFFs, verifies their checksums and grids, and computes dB statistics from the native linear gamma0 values. HH–HV contrast uses positive power at the same location in both channels. Temporal differences use the same band, native grid, site and acquisition geometry, with positive power on both dates.

Curuai has one matched date pair and 562,500 common cells per channel. The median per-cell change from August to October was +0.31 dB for HH and +0.08 dB for HV. Some HH cells changed strongly in either direction. A total of 18.17% brightened by at least 3 dB and 12.06% darkened by at least 3 dB. Thresholds describe radar backscatter change. Event attribution requires independent field or other reference observations. Umm Al Quwain and Burgas each have one locally validated date.

The [Marañón pair](MARANON_MAP_ASSETS.md) has 202,500 common power samples per channel. Run `python models/build_nisar_change_layers.py --downloads <download-folder>` to produce both pairs' signed, native dB-change GeoTIFFs and registered map PNGs. The builder checks saved values against an independently computed `10 log10(later/earlier)` ratio and tests source coordinates against the original HDF5. The optional overlays color changes of 3 dB or more in either direction.

## Everglades imagery behind the EDEN experiment

`data/nisar-everglades.json` lists the 27 October 2026 NISAR scene and its measured HH and HV layers over Shark River Slough. The 15 km square crop falls entirely inside the official SAR footprint and the benchmark study area. It covers 1,444 EDEN grid centres, including 882 centres assessed in the separate 400 m benchmark. The source images show measured backscatter at 20 m posting. The EDEN depth and National Wetlands Inventory datasets are referenced for the benchmark audit. [ASF granule metadata](https://cmr.earthdata.nasa.gov/search/granules.umm_json?concept_id=G4406681165-ASF), [USGS EDEN methodology](https://pubs.usgs.gov/publication/sir20205083), [USFWS inventory](https://www.fws.gov/program/national-wetlands-inventory/data-download).

Use the same Python environment with Shapely available. The builder checks embedded per-polarization focus flags, obtains provider metadata and uses the original archived benchmark grid to verify overlap.

```powershell
$everglades = 'C:/Users/Yoan/Downloads/NISAR_L2_PR_GCOV_034_047_A_014_2005_DHDH_M_20261027T104408_20261027T104439_P05036_N_F_J_001.h5'
$archive = 'C:/Users/Yoan/Documents/Codex/2026-11-14/set/outputs/nisar_wetlands/everglades'
& $python -u models/build_everglades_map_assets.py --h5 $everglades --reference-directory $archive
```

Each GeoTIFF matched all 562,500 source power samples exactly. An independent transform reproduced grayscale intensities at 194 map pixel centres across the two PNGs. The [separate recognition protocol and results](WETLAND.md) describe what the hydrologic reference can support.
