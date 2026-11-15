"""Build measured backscatter overlays for verified same-grid NISAR pairs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageColor
import rasterio
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform, transform_bounds

from analyze_nisar_matrix import CHANGE_DB, MANIFESTS, digest, read_channel, same_geometry


PAIR_STEMS = {'curuai-nisar': 'nisar-curuai-change', 'maranon-nisar': 'nisar-maranon-change'}
WEB = 'EPSG:3857'
COLOURS = {'brightened': '#f38228', 'darkened': '#6c6853'}
WEB_FIELDS = ('image_epsg', 'image_bounds', 'image_shape', 'image_transform')


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def classify(earlier, later):
    paired = np.isfinite(earlier.db) & np.isfinite(later.db)
    change = later.db - earlier.db
    classes = np.zeros(paired.shape, dtype=np.uint8)
    classes[paired & (change >= CHANGE_DB)] = 1
    classes[paired & (change <= -CHANGE_DB)] = 2
    return paired, classes


def inverse_indices(x, y, src_epsg, src_transform):
    east, north = transform(WEB, f'EPSG:{src_epsg}', x.tolist(), y.tolist())
    col, row = (~src_transform) * (np.asarray(east), np.asarray(north))
    return np.floor(row).astype(int), np.floor(col).astype(int)


def display_grid(classes, paired, native_grid, asset):
    epsg, native_shape, coefficients = native_grid
    src_affine = Affine(*coefficients)
    height, width = asset['image_shape']
    web_affine = Affine(*asset['image_transform'])
    west, south, east, north = transform_bounds(WEB, 'EPSG:4326', *array_bounds(height, width, web_affine))
    if not np.allclose([[south, west], [north, east]], asset['image_bounds'], atol=1e-10, rtol=0):
        raise ValueError('Map bounds do not agree with the base display affine')
    display_classes = np.zeros((height, width), dtype=np.uint8)
    display_support = np.zeros((height, width), dtype=bool)
    for start in range(0, height, 128):
        end = min(start + 128, height)
        rows, cols = np.indices((end - start, width))
        x, y = web_affine * (cols.ravel() + .5, rows.ravel() + start + .5)
        nr, nc = inverse_indices(x, y, epsg, src_affine)
        inside = (nr >= 0) & (nc >= 0) & (nr < native_shape[0]) & (nc < native_shape[1])
        display_classes[start:end].ravel()[inside] = classes[nr[inside], nc[inside]]
        display_support[start:end].ravel()[inside] = paired[nr[inside], nc[inside]]
    return display_classes, display_support


def verify_original(downloads, before, after, pol, power, native_grid, asset, display):
    """Check complete native inputs and separately map geographic sample points."""
    import h5py
    from pyproj import Transformer

    raw = []
    for row, expected in zip((before, after), power):
        source = downloads / (row['product_id'] + '.h5')
        col, first, width, height = row['source_window']
        with h5py.File(source) as document:
            if document['science/LSAR/identification/granuleId'][()].decode() != row['product_id']:
                raise ValueError(f'Original HDF5 identity mismatch: {source.name}')
            values = document[row['channels'][pol]['dataset_path']][first:first + height, col:col + width]
        if not np.array_equal(values, expected, equal_nan=True):
            raise ValueError(f'Native GeoTIFF values differ from HDF5: {source.name}')
        raw.append(values.astype(np.float64))
    ratio = raw[1] / raw[0]
    source_class = np.select([ratio >= 10 ** (CHANGE_DB / 10), ratio <= 10 ** (-CHANGE_DB / 10)], [1, 2], 0)
    epsg, shape, coefficients = native_grid
    transform_point = Transformer.from_crs(WEB, epsg, always_xy=True)
    web_affine = Affine(*asset['image_transform'])
    native_affine = Affine(*coefficients)
    height, width = display.shape
    rows, cols = np.meshgrid(np.linspace(0, height - 1, 19, dtype=int),
                             np.linspace(0, width - 1, 23, dtype=int), indexing='ij')
    x, y = web_affine * (cols.ravel() + .5, rows.ravel() + .5)
    east, north = transform_point.transform(x, y)
    sx, sy = (~native_affine) * (east, north)
    sr, sc = np.floor(sy).astype(int), np.floor(sx).astype(int)
    inside = (sr >= 0) & (sc >= 0) & (sr < shape[0]) & (sc < shape[1])
    expected = np.zeros(len(sr), dtype=np.uint8)
    expected[inside] = source_class[sr[inside], sc[inside]]
    if not np.array_equal(expected, display[rows, cols].ravel()):
        raise ValueError('Display classifications differ from independent HDF5 ratios at map positions')
    return np.bincount(source_class.ravel(), minlength=3), len(sr)


def write_science_change(path, earlier, later, paired, before_record, after_record, pol):
    epsg, (height, width), coefficients = earlier.grid
    affine = Affine(*coefficients)
    delta = np.where(paired, later.db - earlier.db, np.nan).astype(np.float32)
    with rasterio.open(path, 'w', driver='GTiff', width=width, height=height, count=1,
                       dtype='float32', crs=f'EPSG:{epsg}', transform=affine, nodata=np.nan,
                       compress='deflate', predictor=3, tiled=True) as dataset:
        dataset.write(delta, 1)
        dataset.set_band_description(1, 'NISAR gamma0 radar return change, later minus earlier')
        dataset.set_band_unit(1, 'dB')
        dataset.update_tags(EARLIER_GRANULE=before_record['product_id'], LATER_GRANULE=after_record['product_id'],
                            EARLIER_SHA256=earlier.sha256, LATER_SHA256=later.sha256, POLARIZATION=pol,
                            FREQUENCY=before_record['frequency'], CALCULATION='10 log10(later gamma0 / earlier gamma0)',
                            SUPPORT='Both dates have finite positive valid calibrated power')
    # Independently check the saved difference against division in linear power.
    with rasterio.open(path) as dataset:
        result = dataset.read(1)
        expected = 10 * np.log10(later.power[paired].astype(np.float64) / earlier.power[paired].astype(np.float64))
        if (dataset.crs.to_epsg() != epsg or dataset.transform != affine or dataset.units != ('dB',)
                or not np.array_equal(np.isfinite(result), paired)
                or not np.allclose(result[paired], expected, rtol=0, atol=0.00001)):
            raise ValueError('Saved change GeoTIFF differs from the native gamma0 power ratio')
    return {'science_url': f'/science/{path.name}', 'science_sha256': digest(path),
            'science_size_bytes': path.stat().st_size, 'science_units': 'dB, later minus earlier',
            'science_epsg': epsg, 'science_shape': [height, width], 'science_transform': list(coefficients)}


def make_layer(data, row_before, row_after, pol, table, downloads):
    base, next_asset = (row['channels'][pol] for row in (row_before, row_after))
    if base['image_epsg'] != 3857 or any(base[key] != next_asset[key] for key in WEB_FIELDS):
        raise ValueError('Both dates must share their exact registered display grid')
    earlier = read_channel(data, row_before, base)
    later = read_channel(data, row_after, next_asset)
    if earlier.grid != later.grid:
        raise ValueError('Both dates must share their exact native measurement grid')
    paired, native = classify(earlier, later)
    measured = table['channels'][pol]
    counts = np.bincount(native.ravel(), minlength=3)
    if (int(paired.sum()), int(counts[1]), int(counts[2])) != (
            measured['paired_pixels'], measured['brighter_at_least_3db_pixels'], measured['darker_at_least_3db_pixels']):
        raise ValueError('Recomputed change counts differ from the verified matrix measurements')
    display, support = display_grid(native, paired, earlier.grid, base)
    checks = 0
    if downloads:
        hdf_counts, checks = verify_original(downloads, row_before, row_after, pol, (earlier.power, later.power),
                                             earlier.grid, base, display)
        if not np.array_equal(hdf_counts, counts):
            raise ValueError('Native overlay counts differ from independent HDF5 power ratios')
    palette = np.asarray([(0, 0, 0, 0), *(ImageColor.getrgb(color) + (255,) for color in COLOURS.values())], dtype=np.uint8)
    pixels = palette[display]
    name = f"{PAIR_STEMS[table['site_id']]}-{pol.lower()}"
    path = data / 'sar' / (name + '.png')
    science = write_science_change(data / 'science' / (name + '.tif'), earlier, later, paired, row_before, row_after, pol)
    Image.fromarray(pixels).save(path, optimize=True)
    with Image.open(path) as reopened:
        if reopened.mode != 'RGBA' or not np.array_equal(np.asarray(reopened), pixels):
            raise ValueError('Saved PNG did not preserve the class colours and transparency')
    display_counts = np.bincount(display.ravel(), minlength=3)
    classes = [{'id': key, 'color': colour, 'native_pixels': int(counts[i]), 'display_pixels': int(display_counts[i])}
               for i, (key, colour) in enumerate(COLOURS.items(), 1)]
    return {'id': name, 'site_id': table['site_id'], 'polarization': pol, 'earlier_id': table['earlier_id'], 'later_id': table['later_id'],
            'earlier_date': table['earlier_date'], 'later_date': table['later_date'], 'threshold_db': CHANGE_DB, 'unit': 'dB',
            'image_url': f'/sar/{path.name}', **{key: base[key] for key in WEB_FIELDS},
            **science,
            'image_sha256': digest(path), 'image_size_bytes': path.stat().st_size, 'classes': classes,
            'paired_native_pixels': int(paired.sum()), 'paired_display_pixels': int(support.sum()),
            'transparent_display_pixels': int(display_counts[0]), 'source_hdf5_coordinate_checks': checks,
            'evidence_ids': table['evidence_ids'],
            'note': 'Colours show a measured change in gamma0 backscatter on the paired grid. Independent evidence is needed to attribute the changes to water, oil or other causes.'}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--data', type=Path, default=Path(__file__).resolve().parents[1] / 'data')
    cli.add_argument('--downloads', type=Path, help='optionally verify the native windows against the original HDF5s')
    args = cli.parse_args()
    records = {row['id']: row for manifest in MANIFESTS for row in read_json(args.data / manifest)['observations']}
    table = read_json(args.data / 'nisar-measurements.json')
    if table['method']['change_threshold_db'] != CHANGE_DB:
        raise ValueError('Threshold differs from the verified matrix')
    layers = []
    for pair in table['comparisons']:
        if pair['site_id'] not in PAIR_STEMS:
            continue
        row_before, row_after = records[pair['earlier_id']], records[pair['later_id']]
        if row_before['site_id'] != pair['site_id'] or row_after['site_id'] != pair['site_id'] or not same_geometry(row_before, row_after):
            raise ValueError('The two observations must share their site and qualified acquisition geometry')
        layers.extend(make_layer(args.data, row_before, row_after, pol, pair, args.downloads) for pol in pair['channels'])
    output = args.data / 'nisar-change-layers.json'
    temp = output.with_suffix('.tmp')
    temp.write_text(json.dumps({'schema_version': 1, 'layers': layers}, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')
    temp.replace(output)
    for record in layers:
        print(record['id'], record['image_size_bytes'], record['classes'])


if __name__ == '__main__':
    main()
