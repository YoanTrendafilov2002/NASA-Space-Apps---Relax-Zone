# Rebuild the Saramurillo NISAR review

Run the build from the app folder with Python 3.11 or newer and an environment that contains h5py, NumPy, Pillow, rasterio and Shapely.

```bash
python models/build_maranon_map_assets.py --downloads "/path/to/saved-nisar" --qgis-review "/path/to/set/outputs/nisar_amazon"
```

The build reads the two original HDF5s by their NISAR product filenames. It checks each file against the ASF byte count and the pinned local SHA‑256. The QGIS folder supplies `saramurillo_calibrated_3db/screen_summary.json`, `oefa_saramurillo_inspection_area.geojson`, `NISAR_Maranon_sensitivity.csv` and `NISAR_Maranon_inspection_coverage.csv`. The build runs offline. [February 24 granule](https://cmr.earthdata.nasa.gov/search/granules.umm_json?concept_id=G4275155676-ASF) · [March 8 granule](https://cmr.earthdata.nasa.gov/search/granules.umm_json?concept_id=G4275159850-ASF)

The manifest is `data/nisar-maranon.json`. It references four Float32 GeoTIFFs in `data/science` and four display PNGs in `data/sar`. The four science layers share a 450 × 450, 20 m grid in EPSG:32718. They preserve measured frequency-A HH and HV gamma‑0 linear power and mark invalid observations with NaN. The source products have noise correction disabled. Only the display PNGs are reprojected, to EPSG:3857, with fixed dB ranges for each polarization. [ASF GCOV guide](https://nisar-docs.asf.alaska.edu/gcov/)

OEFA reported the March 7 incident at Station 1 Pontoon 4. The map marker uses the approximate QGIS location within the digitized inspection illustration. In the archived 60 m HH screen with a 40 m bank buffer, 246 river cells within 1 km were assessable. The screen returned zero connected patches at its 3 dB threshold and had zero assessable overlap with the digitized inspection footprint. The footprint lacks a material observation synchronized to the NISAR pass. The app rasters preserve the backscatter needed to inspect the pair. [OEFA report, slides 19–22](https://www3.congreso.gob.pe/Docs/comisiones2025/transportes/files/oefa_ppt_22_05_2026.pdf#page=10)
