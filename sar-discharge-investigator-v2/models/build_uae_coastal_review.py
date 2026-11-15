"""Build the native HH crop and map preview for the reviewed UAE QGIS scene."""

import argparse
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from osgeo import gdal, ogr, osr
from PIL import Image


PRODUCT = 'NISAR_L2_PR_GCOV_033_128_A_015_2005_DHDH_M_20261021T013516_20261021T013551_P05023_N_F_J_001'
COLLECTION = 'NISAR_L2_GCOV_PROVISIONAL_V1'
GRID = '/science/LSAR/GCOV/grids/frequencyA'
SITE_ID = 'uae-coastal-review'
OBSERVATION_ID = 'nisar-uae-coast-20261021'
SOURCE_ID = f'{OBSERVATION_ID}-source'
DISPLAY_RANGE = [-24.5, -11.0]
REFERENCE_TIFF = 'NISAR_Lband_HH_20261021T0135Z.tif'
WETLAND_INVENTORY = 'FGIC_Khor_Al_Beidah_within_NISAR_swath.geojson'
REFERENCE_TRANSFORM = (345000.0, 20.0, 0.0, 2872000.0, 0.0, -20.0)
REFERENCE_SHAPE = (2575, 2000)
NATIVE_EPSG, DISPLAY_EPSG = 32640, 3857
DISPLAY_POSTING_M = 23.0
ASSET_LIMIT_BYTES = 50_000_000


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def spatial_ref(epsg):
    crs = osr.SpatialReference()
    crs.ImportFromEPSG(epsg)
    crs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return crs


def raster_bounds(transform, shape):
    height, width = shape
    west, resolution_x, _, north, _, resolution_y = transform
    return west, north + height * resolution_y, west + width * resolution_x, north


def affine(transform):
    return [transform[index] for index in (1, 2, 0, 4, 5, 3)]


def epsg(dataset):
    return int(dataset.GetSpatialRef().GetAuthorityCode(None))


def power_db(power):
    with np.errstate(divide='ignore', invalid='ignore'):
        return 10 * np.log10(power)


def read_native_window(h5, reference):
    signed_transform = reference.GetGeoTransform()
    shape = (reference.RasterYSize, reference.RasterXSize)
    height, width = shape
    if (signed_transform, shape, epsg(reference)) != (REFERENCE_TRANSFORM, REFERENCE_SHAPE, NATIVE_EPSG):
        raise ValueError('QGIS HH reference grid differs from the recorded evidence window')
    if reference.GetMetadataItem('SOURCE_GRANULE') != PRODUCT:
        raise ValueError('QGIS HH reference belongs to a different granule')

    hh = gdal.Open(f'NETCDF:"{h5}":{GRID}/HHHH')
    transform = hh.GetGeoTransform()
    if epsg(hh) != NATIVE_EPSG or transform[1:3] != (20, 0) or transform[4:] != (0, -20):
        raise ValueError('Source HH is not on the expected 20 m UTM grid')
    x = (signed_transform[0] - transform[0]) / transform[1]
    y = (signed_transform[3] - transform[3]) / transform[5]
    if x != int(x) or y != int(y) or min(x, y) < 0 or x + width > hh.RasterXSize or y + height > hh.RasterYSize:
        raise ValueError('QGIS reference does not match an exact source window')
    window = (int(x), int(y), width, height)
    power = hh.ReadAsArray(*window)
    if power.dtype != np.float32:
        raise ValueError(f'Unexpected HH power dtype {power.dtype}')

    planes = {}
    for name in ('mask', 'numberOfLooks', 'inputDataExceptionMask'):
        dataset = gdal.Open(f'NETCDF:"{h5}":{GRID}/{name}')
        if (dataset.GetGeoTransform() != transform or epsg(dataset) != NATIVE_EPSG
                or dataset.RasterXSize != hh.RasterXSize or dataset.RasterYSize != hh.RasterYSize):
            raise ValueError(f'{name} grid differs from the HH power grid')
        planes[name] = dataset.ReadAsArray(*window)
    if planes['inputDataExceptionMask'].dtype != np.uint8:
        raise ValueError('Expected the current granule’s unsigned one-byte exception mask')
    multidim = gdal.OpenEx(str(h5), gdal.OF_MULTIDIM_RASTER, allowed_drivers=['netCDF'])
    subgroup = multidim.GetRootGroup()
    count = int(subgroup.OpenMDArrayFromFullname(f'{GRID}/numberOfSubSwaths').ReadAsArray().item())
    if count != 1 or subgroup.OpenMDArrayFromFullname(f'{GRID}/HVHV') is None:
        raise ValueError('Unexpected subswath count or provider HV availability')

    swath, looks, exceptions = (planes[name] for name in ('mask', 'numberOfLooks', 'inputDataExceptionMask'))
    valid = (swath >= 1) & (swath <= count) & (looks > 0) & (exceptions == 0) & np.isfinite(power) & (power > 0)
    power[~valid] = np.nan
    comparison = reference.ReadAsArray()
    derived_db = power_db(power)
    if not valid.any() or not np.array_equal(valid, np.isfinite(comparison)):
        raise ValueError('Source-derived mask differs from the existing QGIS HH reference')
    maximum_difference = float(np.max(np.abs(derived_db[valid] - comparison[valid])))
    if maximum_difference > 2e-5:
        raise ValueError('Source-derived dB values differ from the existing QGIS HH reference')

    check = {
        'reference': REFERENCE_TIFF,
        'compared_postings': int(valid.sum()),
        'qa_invalid_pixels': int(valid.size - valid.sum()),
        'validity_masks_identical': True,
        'maximum_db_difference': maximum_difference,
    }
    return power, valid, signed_transform, window, check


def review_points(power, transform, inventory_path):
    to_wgs84 = osr.CoordinateTransformation(spatial_ref(NATIVE_EPSG), spatial_ref(4326))
    inventory = ogr.Open(str(inventory_path))
    layer = inventory.GetLayer()
    if layer.GetSpatialRef().GetAuthorityCode(None) != '4326':
        raise ValueError('Wetland inventory must provide the QGIS WGS84 polygon crop')
    anchors = (
        ('uae-offshore-slick', 'oil_spill', 'Offshore dark-water candidate', 362000, 2852500,
         f'{OBSERVATION_ID}-offshore', 'visual_interpretation',
         'Manual interpretation of a dark feature in this HH acquisition. The held-out UAE screen did not establish oil detection.'),
        ('uae-khor-al-beidah', 'wetlands', 'Khor Al Beidah wetland', 362171, 2830157,
         f'{OBSERVATION_ID}-wetland', 'mapped_habitat',
         'Review point on a feature in the public mangrove inventory. This scene provides a backscatter observation.'),
    )
    points = []
    for identifier, topic, label, x, y, evidence, kind, notes in anchors:
        lon, lat, _ = to_wgs84.TransformPoint(x, y)
        col = math.floor((x - transform[0]) / transform[1])
        row = math.floor((y - transform[3]) / transform[5])
        if not (0 <= row < power.shape[0] and 0 <= col < power.shape[1] and np.isfinite(power[row, col])):
            raise ValueError(f'{label} falls outside measured HH coverage')
        point = ogr.Geometry(ogr.wkbPoint)
        point.AddPoint_2D(lon, lat)
        layer.SetSpatialFilter(point)
        within = any(feature.GetGeometryRef().Intersects(point) for feature in layer)
        if within != (topic == 'wetlands'):
            raise ValueError(f'{label} conflicts with the checked wetland geometry')
        points.append({
            'id': identifier, 'topic': topic, 'label': label, 'lon': lon, 'lat': lat,
            'evidence_id': evidence, 'kind': kind, 'notes': notes, 'native_pixel': [row, col],
            'hh_db': float(power_db(power[row, col])), 'intersects_fgic_mangrove': within,
        })
    return points


def make_assets(folder, power, valid, transform):
    height, width = power.shape
    source = gdal.GetDriverByName('MEM').Create('', width, height, 1, gdal.GDT_Float32)
    source.SetSpatialRef(spatial_ref(NATIVE_EPSG))
    source.SetGeoTransform(transform)
    source.SetMetadata({
        'SOURCE_GRANULE': PRODUCT, 'SOURCE_DATASET': f'{GRID}/HHHH', 'FREQUENCY': 'A',
        'POLARIZATION': 'HH', 'NOISE_CORRECTION_APPLIED': 'false',
        'QUALITY_MASK': 'Valid subswath, positive looks, zero exception, finite positive power',
    })
    band = source.GetRasterBand(1)
    band.SetNoDataValue(float('nan'))
    band.SetDescription('NISAR HH measured gamma0 linear power')
    band.SetUnitType('1')
    band.WriteArray(power)
    band.CreateMaskBand(gdal.GMF_PER_DATASET)
    band.GetMaskBand().WriteArray(valid.astype(np.uint8) * 255)

    tiff = folder / f'{OBSERVATION_ID}-hh.tif'
    image = folder / f'{OBSERVATION_ID}-hh.png'
    gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', 'YES')
    options = ['COMPRESS=DEFLATE', 'PREDICTOR=FLOATING_POINT', 'BLOCKSIZE=512', 'OVERVIEWS=NONE']
    gdal.Translate(str(tiff), source, format='COG', creationOptions=options).FlushCache()
    saved = gdal.Open(str(tiff))
    saved_band = saved.GetRasterBand(1)
    if (epsg(saved) != NATIVE_EPSG or saved.GetGeoTransform() != transform
            or saved_band.DataType != gdal.GDT_Float32 or saved_band.GetUnitType() != '1'
            or saved.GetMetadataItem('LAYOUT', 'IMAGE_STRUCTURE') != 'COG'
            or not np.array_equal(saved_band.ReadAsArray(), power, equal_nan=True)
            or not np.array_equal(saved_band.GetMaskBand().ReadAsArray(), valid.astype(np.uint8) * 255)):
        raise RuntimeError('Reopened science values, mask, or georeferencing differ from the native crop')
    saved_band = saved = None

    display = gdal.Warp('', source, format='MEM', dstSRS=f'EPSG:{DISPLAY_EPSG}',
                        xRes=DISPLAY_POSTING_M, yRes=DISPLAY_POSTING_M, resampleAlg='near',
                        srcNodata='None', dstNodata=float('nan'), errorThreshold=0.0)
    pixels = display.ReadAsArray()
    image_transform = display.GetGeoTransform()
    visible = np.isfinite(pixels)
    if not visible.any():
        raise RuntimeError('Display crop has no measured area')
    lo, hi = DISPLAY_RANGE
    layer = np.zeros((*pixels.shape, 2), dtype=np.uint8)
    layer[visible, 0] = np.rint(np.clip((power_db(pixels[visible]) - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
    layer[visible, 1] = 255
    Image.fromarray(layer).save(image, optimize=True)
    with Image.open(image) as reopened:
        if not np.array_equal(np.asarray(reopened), layer):
            raise RuntimeError('Reopened PNG pixels differ from the projected display')

    check_pixels = np.linspace(0, pixels.size - 1, 225, dtype=int)
    to_native = osr.CoordinateTransformation(spatial_ref(DISPLAY_EPSG), spatial_ref(NATIVE_EPSG))
    for index in check_pixels:
        row, col = divmod(int(index), pixels.shape[1])
        x = image_transform[0] + (col + 0.5) * image_transform[1]
        y = image_transform[3] + (row + 0.5) * image_transform[5]
        orig_x, orig_y, _ = to_native.TransformPoint(x, y)
        src_col = math.floor((orig_x - transform[0]) / transform[1])
        src_row = math.floor((orig_y - transform[3]) / transform[5])
        expected = power[src_row, src_col] if 0 <= src_row < height and 0 <= src_col < width else float('nan')
        if pixels[row, col] != expected and not (np.isnan(pixels[row, col]) and np.isnan(expected)):
            raise RuntimeError(f'Display pixel {row}, {col} does not match its native nearest posting')

    if any(path.stat().st_size >= ASSET_LIMIT_BYTES for path in (tiff, image)):
        raise RuntimeError('UAE coastal assets exceed the local transfer budget')
    web_to_wgs84 = osr.CoordinateTransformation(spatial_ref(DISPLAY_EPSG), spatial_ref(4326))
    west, south, east, north = web_to_wgs84.TransformBounds(*raster_bounds(image_transform, pixels.shape), 21)
    asset = {
        'status': 'validated', 'scientific': True, 'term': 'HHHH', 'dataset_path': f'{GRID}/HHHH',
        'units': 'dB', 'display_range_db': DISPLAY_RANGE, 'image_url': f'/sar/{image.name}',
        'image_bounds': [[south, west], [north, east]], 'image_epsg': DISPLAY_EPSG,
        'image_shape': list(pixels.shape), 'image_transform': affine(image_transform),
        'image_size_bytes': image.stat().st_size, 'image_sha256': digest(image),
        'science_url': f'/science/{tiff.name}', 'science_units': 'unitless gamma0 power',
        'science_epsg': NATIVE_EPSG, 'science_transform': affine(transform),
        'science_size_bytes': tiff.stat().st_size, 'science_sha256': digest(tiff),
        'science_shape': list(power.shape), 'science_posting_m': 20,
        'noise_correction_applied': False, 'evidence_id': SOURCE_ID,
        'valid_pixels': int(valid.sum()), 'valid_fraction': float(valid.mean()),
        'geographic_pixel_checks': len(check_pixels),
        'notes': 'Measured NISAR gamma0. The science COG preserves native linear samples and their quality mask. The map uses a fixed 10 log10 dB stretch.',
    }
    return tiff, image, asset


def build_manifest(baseline, source, transform, window, points, hh, check, inventory_sha256):
    west, south, east, north = raster_bounds(transform, REFERENCE_SHAPE)
    to_wgs84 = osr.CoordinateTransformation(spatial_ref(NATIVE_EPSG), spatial_ref(4326))
    corners = [(west, south), (east, south), (east, north), (west, north), (west, south)]
    geometry = {'type': 'Polygon', 'coordinates': [[list(to_wgs84.TransformPoint(x, y)[:2]) for x, y in corners]]}
    bbox = list(to_wgs84.TransformBounds(west, south, east, north, 21))
    site = {
        'id': SITE_ID, 'name': 'Umm Al Quwain coast, UAE', 'topic': 'oil_spill',
        'aliases': ['Umm Al Quwain coast', 'UAE coastal review', 'GEM NO. 2 coastal review'],
        'bbox': bbox, 'start': '2026-10-21', 'end': '2026-10-21', 'evidence_id': SOURCE_ID,
        'geometry': geometry, 'geometry_kind': 'study_area', 'review_points': points,
        'geometry_note': 'Extended QGIS review window from the same October 21 acquisition used in the smaller Umm Al Quwain view.',
    }
    observation = dict(baseline, id=OBSERVATION_ID, site_id=SITE_ID, geometry=geometry,
                       product_type='GCOV', evidence_id=SOURCE_ID, source_window=list(window),
                       collection=COLLECTION, collection_concept_id='C2854338529-ASF',
                       granule_concept_id='G4384361936-ASF', maturity='provisional',
                       channels={'HH': hh, 'HV': {
                           'status': 'catalogue_only', 'scientific': False, 'term': 'HVHV',
                           'dataset_path': f'{GRID}/HVHV', 'evidence_id': SOURCE_ID,
                           'notes': 'HV is in the provider granule. This extended review publishes the matched QGIS HH window.',
                       }})
    evidence = [
        dict(source, id=SOURCE_ID, title='NASA ASF NISAR HH · Umm Al Quwain coastal window',
             cmr_url='https://cmr.earthdata.nasa.gov/search/granules.umm_json?concept_id=G4384361936-ASF',
             qgis_reference_check=check,
             notes='Local frequency-A HH subset from the ASF HDF5. The native grid, quality mask, and all HH dB values match the saved QGIS reference.'),
        {
            'id': points[0]['evidence_id'], 'title': 'Offshore candidate and UAE incident context',
            'kind': 'visual_interpretation_with_incident_context',
            'url': 'https://www.wam.ae/en/article/c3051mm-oil-spill-from-gem-no-tanker-contained',
            'notes': 'The October 21 marker is the QGIS callout target selected from this HH image. The UAE authorities described a regional slick on October 30. That report cannot establish the material at this October 21 point.',
        }, {
            'id': points[1]['evidence_id'], 'title': 'Khor Al Beidah mapped coastal habitat', 'kind': 'habitat_inventory',
            'url': 'https://data.emiratesnaturewwf.ae/media/files/2024-12/Habitat_Map_Policy_Report_0.pdf',
            'government_reference_url': 'https://www.moccae.gov.ae/assets/38ab144b/UAE%20KBA%20Final%20Technical%20Report.pdf.aspx',
            'inventory_url': 'https://stgnsdi.fgic.gov.ae/hosting/rest/services/Environment/ENV_SL4_Mangroves_AR/MapServer/1',
            'inventory_crop_sha256': inventory_sha256,
            'notes': 'The WWF habitat survey documents the wetland. The QGIS callout target falls inside a mangrove polygon in the saved FGIC inventory crop. This point is a habitat reference.',
        },
    ]
    return {'schema_version': 1, 'sites': [site], 'observations': [observation], 'evidence': evidence}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--h5', type=Path, required=True)
    parser.add_argument('--qgis-layers', type=Path, required=True, help='Folder containing the original HH reference and FGIC inventory crop')
    parser.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    args = parser.parse_args()
    gdal.UseExceptions()
    ogr.UseExceptions()
    base = json.loads((args.data / 'non-sentinel-nisar.json').read_text(encoding='utf-8'))
    observation = next(row for row in base['observations'] if row['id'] == 'nisar-uae-20261021' and row['product_id'] == PRODUCT)
    source = next(row for row in base['evidence'] if row['id'] == observation['evidence_id'])
    h5 = args.h5.resolve(strict=True)
    if (h5.name != f'{PRODUCT}.h5' or h5.stat().st_size != source['provider_size_bytes']
            or digest(h5) != source['source_sha256']):
        raise ValueError('The input HDF5 differs from the verified local ASF source')
    reference = gdal.Open(str(args.qgis_layers / REFERENCE_TIFF))
    power, valid, transform, window, check = read_native_window(h5, reference)
    inventory_path = args.qgis_layers / WETLAND_INVENTORY
    points = review_points(power, transform, inventory_path)
    with TemporaryDirectory(dir=args.data) as staging:
        folder = Path(staging)
        tiff, image, asset = make_assets(folder, power, valid, transform)
        manifest = build_manifest(observation, source, transform, window, points, asset, check, digest(inventory_path))
        manifest_path = folder / 'nisar-uae-coastal-review.json'
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')
        for source_path, destination in ((tiff, args.data / 'science'), (image, args.data / 'sar'), (manifest_path, args.data)):
            destination.mkdir(parents=True, exist_ok=True)
            source_path.replace(destination / source_path.name)
    print(json.dumps({'source_window': window, 'qgis_match': check, 'review_points': points,
                      'science_size_bytes': asset['science_size_bytes'], 'image_size_bytes': asset['image_size_bytes']}, indent=2))


if __name__ == '__main__':
    main()
