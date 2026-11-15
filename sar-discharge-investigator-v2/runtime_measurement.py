"""Measure backscatter on the common support of registered local NISAR rasters."""

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from math import isfinite, prod
from pathlib import Path
from threading import Lock


CHANNELS = frozenset(('HH', 'HV', 'VH', 'VV'))
MAX_DATES = 24
MAX_INPUT_CELLS = 18_000_000
MAX_ASSET_BYTES = 64_000_000
MAX_DISPLAY_SIDE = 1500
POWER_UNITS = frozenset(('unitless gamma0 power', 'unitless gamma0 linear power'))
ESSENTIAL = ('site_id', 'product', 'sensor', 'frequency', 'orbit', 'frame', 'direction', 'processing_version')
QUALITY = ('look_direction', 'noise_correction_applied')
NOTE = ('This measures the change in calibrated radar return on shared valid cells. '
        'Identifying a change in exposed water, flooding, drought, or oil requires independent evidence.')
NOISE_LIMIT = ('Per-date look direction or noise correction may be unknown for provider subsets. '
               'This comparison describes published product measurements with those settings unverified.')
COLORS = (('brightened', '#f38228', (243, 130, 40, 255)), ('darkened', '#6c6853', (108, 104, 83, 255)))


class _Unavailable(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def _unavailable(error):
    return {'status': 'unavailable', 'reason': error.reason, 'message': str(error)}


def _utc(value):
    if not isinstance(value, str):
        raise ValueError('Acquisition timestamp is missing.')
    time = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if time.tzinfo is None:
        raise ValueError('Acquisition timezone is missing.')
    return time.astimezone(timezone.utc)


def _comparable(rows):
    first = rows[0]
    for row in rows:
        if any(row.get(key) is None or row.get(key) == '' for key in ESSENTIAL):
            raise _Unavailable('geometry_metadata_missing', 'This comparison needs the registered track, frame, pass, frequency and processing version on every date.')
        if (row['frequency'] not in {'A', 'B'} or type(row['orbit']) is not int or type(row['frame']) is not int
                or not isinstance(row['direction'], str) or row['direction'].casefold() not in {'ascending', 'descending'}):
            raise _Unavailable('geometry_metadata_missing', 'The registered acquisition geometry is incomplete.')
        for key in ESSENTIAL:
            value, baseline = row[key], first[key]
            if key == 'direction':
                value, baseline = value.casefold(), baseline.casefold()
            if value != baseline:
                raise _Unavailable('incompatible_geometry', 'Use acquisitions from the same site, track, frame, pass direction, frequency and processing version.')
    unknown = []
    for key in QUALITY:
        values = [row.get(key) for row in rows]
        valid = [v for v in values if v is not None and v != '']
        if key == 'look_direction':
            if any(not isinstance(v, str) or v.casefold() not in ('left', 'right') for v in valid):
                raise _Unavailable('geometry_metadata_missing', 'A registered look direction is invalid.')
            valid = [v.casefold() for v in valid]
        elif any(type(v) is not bool for v in valid):
            raise _Unavailable('geometry_metadata_missing', 'A registered noise correction value is invalid.')
        if len(set(valid)) > 1:
            raise _Unavailable('incompatible_geometry', 'The look direction or radar noise correction changes between the registered observations.')
        if len(valid) < len(rows):
            unknown.append(key)
    return {'status': 'qualified' if unknown else 'matched',
            'unknown_fields': unknown,
            'site_id': first['site_id'], 'track': first['orbit'], 'frame': first['frame'],
            'frequency': first['frequency'], 'pass_direction': first['direction'],
            'processor': first['processing_version']}


def _load_tools():
    try:
        import numpy as np
        from models import analyze_nisar_matrix as scientific
    except ImportError as error:
        raise _Unavailable('measurement_dependencies_unavailable', 'Runtime measurements require NumPy and Rasterio on the server.') from error
    return np, scientific


def _grid(channel):
    epsg, shape, affine = channel.grid
    return {'epsg': epsg, 'shape': list(shape), 'affine': list(affine), 'posting_m': affine[0]}


def _source(row, layer):
    return {'observation_id': row['id'], 'granule_id': row['product_id'], 'acquired_at': row['acquired_at'],
            'science_url': layer.url, 'source_sha256': layer.sha256, 'evidence_id': layer.evidence_id}


def _render_change(np, before, after, rows, spec, polarization, comparison, geometry, threshold_db):
    try:
        from PIL import Image
        from rasterio.io import MemoryFile
        from rasterio.transform import Affine, array_bounds, from_bounds
        from rasterio.warp import transform, transform_bounds
    except ImportError as error:
        raise _Unavailable('measurement_dependencies_unavailable', 'Change images also require Pillow and Rasterio on the server.') from error
    paired = np.isfinite(before.db) & np.isfinite(after.db)
    change = after.db - before.db
    native = np.select((paired & (change >= threshold_db), paired & (change <= -threshold_db)), (1, 2), 0).astype(np.uint8)
    epsg, (height, width), affine = before.grid
    source_crs = f'EPSG:{epsg}'
    native_affine = Affine(*affine)

    with MemoryFile() as memory:
        with memory.open(driver='GTiff', dtype='float32', count=1, crs=source_crs, transform=native_affine,
                         height=height, width=width, nodata=np.nan, compress='deflate', predictor=3, tiled=True) as output:
            output.write(change.astype(np.float32), 1)
            output.set_band_description(1, 'Measured NISAR gamma0 difference, later minus earlier')
            output.set_band_unit(1, 'dB')
            output.update_tags(EARLIER_GRANULE=rows[0]['product_id'], LATER_GRANULE=rows[1]['product_id'],
                               EARLIER_SHA256=before.sha256, LATER_SHA256=after.sha256,
                               POLARIZATION=polarization, FREQUENCY=geometry['frequency'],
                               SUPPORT='Finite positive calibrated power on both registered grids',
                               CALCULATION='10 log10(later gamma0 / earlier gamma0)')
        tiff = memory.read()

    web = 'EPSG:3857'
    projected = transform_bounds(source_crs, web, *array_bounds(height, width, native_affine))
    # Reuse the registered map grid if it exists and is compact.
    registered_shape = spec.get('image_shape')
    if (spec.get('image_epsg') == 3857 and isinstance(registered_shape, (tuple, list)) and len(registered_shape) == 2
            and all(type(a) is int and 0 < a <= MAX_DISPLAY_SIDE for a in registered_shape)):
        image_height, image_width = registered_shape
        image_transform = spec.get('image_transform')
        if (not isinstance(image_transform, (list, tuple)) or len(image_transform) != 6
                or any(type(value) not in (float, int) or not isfinite(value) for value in image_transform)):
            raise _Unavailable('invalid_display_grid', 'The registered map rendering needs a complete display grid.')
        web_affine = Affine(*image_transform)
        if web_affine.b != 0 or web_affine.d != 0 or web_affine.a <= 0 or web_affine.e >= 0:
            raise _Unavailable('invalid_display_grid', 'The registered map rendering needs a north-up display grid.')
    else:
        west, south, east, north = projected
        scale = max((east-west)/min(width, MAX_DISPLAY_SIDE), (north-south)/min(height, MAX_DISPLAY_SIDE))
        image_width = min(MAX_DISPLAY_SIDE, max(1, round((east-west)/scale)))
        image_height = min(MAX_DISPLAY_SIDE, max(1, round((north-south)/scale)))
        web_affine = from_bounds(west, south, east, north, image_width, image_height)
    bounds = array_bounds(image_height, image_width, web_affine)
    display_pixel = max(web_affine.a, -web_affine.e)
    if any(abs(registered - measured) > 2*display_pixel for registered, measured in zip(bounds, projected)):
        raise _Unavailable('invalid_display_grid', 'The registered map image does not align with the scientific grid.')
    west, south, east, north = transform_bounds(web, 'EPSG:4326', *bounds)
    image_classes = np.zeros((image_height, image_width), dtype=np.uint8)
    display_support = 0
    # Each Web Mercator pixel samples the native class under its centre. This
    # preserves categorical edges and leaves outside-grid pixels transparent.
    for top in range(0, image_height, 128):
        bottom = min(top + 128, image_height)
        rr, cc = np.indices((bottom-top, image_width))
        x = web_affine.c + web_affine.a*(cc.ravel()+.5)
        y = web_affine.f + web_affine.e*(rr.ravel()+top+.5)
        eastings, northings = transform(web, source_crs, x.tolist(), y.tolist())
        col = (np.asarray(eastings)-native_affine.c)/native_affine.a
        row = (np.asarray(northings)-native_affine.f)/native_affine.e
        inside = np.isfinite(row) & np.isfinite(col) & (row >= 0) & (row < height) & (col >= 0) & (col < width)
        nr, nc = np.floor(row[inside]).astype(int), np.floor(col[inside]).astype(int)
        image_classes[top:bottom].ravel()[inside] = native[nr, nc]
        display_support += int(np.count_nonzero(paired[nr, nc]))
    palette = np.asarray([(0, 0, 0, 0), *(color for _, _, color in COLORS)], dtype=np.uint8)
    png_buffer = BytesIO()
    Image.fromarray(palette[image_classes]).save(png_buffer, format='PNG')
    png = png_buffer.getvalue()

    class_counts = np.bincount(image_classes.ravel(), minlength=3)
    native_counts = (comparison['brighter_at_least_3db_pixels'], comparison['darker_at_least_3db_pixels'])
    change_layer = {
        'site_id': geometry['site_id'], 'polarization': polarization,
        'earlier_id': rows[0]['id'], 'later_id': rows[1]['id'],
        'earlier_date': _utc(rows[0]['acquired_at']).date().isoformat(),
        'later_date': _utc(rows[1]['acquired_at']).date().isoformat(),
        'threshold_db': threshold_db, 'unit': 'dB',
        'image_epsg': 3857, 'image_bounds': [[south, west], [north, east]],
        'image_shape': [image_height, image_width], 'image_transform': list(web_affine)[:6],
        'image_sha256': sha256(png).hexdigest(), 'image_size_bytes': len(png),
        'science_epsg': epsg, 'science_shape': [height, width], 'science_transform': list(affine),
        'science_units': 'dB, later minus earlier', 'science_sha256': sha256(tiff).hexdigest(),
        'science_size_bytes': len(tiff),
        'classes': [{'id': name, 'color': color, 'native_pixels': native_counts[i-1], 'display_pixels': int(class_counts[i])}
                    for i, (name, color, _) in enumerate(COLORS, 1)],
        'paired_native_pixels': comparison['paired_pixels'],
        'paired_display_pixels': display_support, 'transparent_display_pixels': int(class_counts[0]),
        'note': NOTE + (' ' + NOISE_LIMIT if geometry['unknown_fields'] else ''),
    }
    return change_layer, {'image': png, 'science': tiff}


class RuntimeMeasurement:
    """Compute read-only measurements from the server's trusted observation registry."""

    def __init__(self, data_dir, observations):
        self.data_dir = Path(data_dir).resolve()
        self.science_dir = (self.data_dir/'science').resolve()
        if self.science_dir.parent != self.data_dir:
            raise ValueError('Science directory must be inside the data registry.')
        rows = observations.values() if isinstance(observations, Mapping) else observations
        self.observations = {}
        for row in rows:
            key = row['id']
            if key in self.observations:
                raise ValueError('Duplicate measurement observation registration.')
            self.observations[key] = deepcopy(row)
        self._lock = Lock()

    def _request(self, ids, polarization):
        if not isinstance(polarization, str) or polarization.upper() not in CHANNELS:
            raise _Unavailable('invalid_polarization', 'Choose a single HH, HV, VH or VV channel.')
        pol = polarization.upper()
        rows = []
        for key in ids:
            row = self.observations.get(key) if isinstance(key, str) else None
            if row is None:
                raise _Unavailable('observation_not_registered', 'Scientific measurement needs an observation registered with local measured pixels.')
            if (row.get('product') != 'NISAR GCOV' or row.get('sensor') != 'L-SAR' or
                    not str(row.get('product_id', '')).startswith('NISAR_L2_')):
                raise _Unavailable('unsupported_product', 'Runtime backscatter measurements currently use registered NISAR L-SAR GCOV.')
            rows.append(row)
        return rows, pol

    def _read(self, rows, polarization, scientific):
        prepared = []
        cells = 0
        for row in rows:
            asset = row.get('channels', {}).get(polarization) or {}
            if asset.get('status') != 'validated' or not asset.get('science_url'):
                raise _Unavailable('channel_unavailable', f"No calibrated {polarization} GeoTIFF is registered for {row['id']}.")
            pattern = scientific.LOCAL_ASSET.fullmatch(asset['science_url'])
            if not pattern or asset.get('science_units') not in POWER_UNITS:
                raise _Unavailable('scientific_asset_unavailable', 'The registration does not point to an approved local gamma0 power GeoTIFF.')
            local = (self.science_dir / pattern.group(1)).resolve()
            if local.parent != self.science_dir or not local.is_file():
                raise _Unavailable('scientific_asset_unavailable', 'A registered local scientific GeoTIFF is missing.')
            size = local.stat().st_size
            expected_size = asset.get('science_size_bytes', asset.get('source_bytes'))
            if not 1 <= size <= MAX_ASSET_BYTES or size != expected_size:
                raise _Unavailable('scientific_asset_unavailable', 'The local scientific file size does not match its bounded registration.')
            frequency = row['frequency']
            if (asset.get('term', polarization*2) != polarization*2 or
                    ('dataset_path' in asset and asset['dataset_path'] != f'/science/LSAR/GCOV/grids/frequency{frequency}/{polarization*2}') or
                    (asset.get('noise_correction_applied') is not None and
                     row.get('noise_correction_applied') != asset['noise_correction_applied'])):
                raise _Unavailable('incompatible_channel', 'Channel metadata does not match the requested polarization, frequency or observation processing.')
            shape = asset.get('science_shape') or asset.get('source_shape')
            if not isinstance(shape, (list, tuple)) or len(shape) != 2 or any(type(n) is not int or n < 1 for n in shape):
                raise _Unavailable('invalid_grid', 'The scientific file registration has no valid grid shape.')
            cells += prod(shape)
            if prod(shape) > scientific.MAX_SAMPLES or cells > MAX_INPUT_CELLS:
                raise _Unavailable('selection_too_large', 'Choose fewer dates or a smaller locally registered crop.')
            prepared.append(asset)
        try:
            layers = [scientific.read_channel(self.data_dir, row, asset) for row, asset in zip(rows, prepared)]
        except (KeyError, ValueError, OSError, AttributeError) as error:
            raise _Unavailable('invalid_scientific_asset', 'A registered scientific raster did not pass its checksum, grid, units or validity checks.') from error
        if any(layer.grid != layers[0].grid for layer in layers[1:]):
            raise _Unavailable('incompatible_grid', 'The acquisitions do not share the exact native grid and map coordinates.')
        return layers, prepared

    @staticmethod
    def _times(rows):
        try:
            times = [_utc(row['acquired_at']) for row in rows]
        except (ValueError, KeyError) as error:
            raise _Unavailable('acquisition_time_unknown', 'A registered acquisition has no usable UTC time.') from error
        if len({stamp.date() for stamp in times}) != len(rows) or len({row['product_id'] for row in rows}) != len(rows):
            raise _Unavailable('duplicate_acquisition', 'Choose distinct UTC acquisition dates and source granules.')
        return times

    def change(self, earlier_id, later_id, polarization):
        """Return change statistics, change-layer metadata and internal artifact bytes."""
        try:
            rows, pol = self._request((earlier_id, later_id), polarization)
            times = self._times(rows)
            if times[0] >= times[1]:
                raise _Unavailable('invalid_date_order', 'Select the earlier acquisition before the later acquisition.')
            geometry = _comparable(rows)
            np, scientific = _load_tools()
            with self._lock:
                layers, prepared = self._read(rows, pol, scientific)
                earlier, later = layers
                comparison = scientific.compare_channel(earlier, later)
                if comparison is None:
                    raise _Unavailable('no_common_support', 'The selected acquisitions have no shared finite positive power cells.')
                change_layer, artifacts = _render_change(np, earlier, later, rows, prepared[0], pol, comparison,
                                                        geometry, scientific.CHANGE_DB)
        except _Unavailable as error:
            return _unavailable(error)
        sources = [_source(row, layer) for row, layer in zip(rows, layers)]
        change_layer['evidence_ids'] = list(dict.fromkeys(layer.evidence_id for layer in layers))
        return {'status': 'measured', 'grid': _grid(earlier), 'comparison': comparison,
                'sources': sources, 'comparability': geometry, 'change_layer': change_layer, '_artifacts': artifacts}

    def time_series(self, site_id, polarization, observation_ids=None):
        """Summarise dates on the intersection of all positive, valid native pixels."""
        try:
            if not isinstance(site_id, str) or not isinstance(polarization, str) or polarization.upper() not in CHANNELS:
                raise _Unavailable('invalid_selection', 'Choose a registered study area and a single HH, HV, VH or VV channel.')
            excluded = []
            if observation_ids is None:
                ids = []
                for row in self.observations.values():
                    if row.get('site_id') != site_id:
                        continue
                    asset = row.get('channels', {}).get(polarization.upper()) or {}
                    if asset.get('status') == 'validated' and asset.get('science_url'):
                        ids.append(row['id'])
                    else:
                        excluded.append({'observation_id': row['id'], 'reason': 'channel_unavailable'})
            else:
                if not isinstance(observation_ids, (list, tuple)):
                    raise _Unavailable('invalid_selection', 'Send a list of registered observation IDs.')
                ids = observation_ids
            if len(ids) < 2:
                return _unavailable(_Unavailable('insufficient_dates', 'At least two registered observations with measured pixels are needed for this channel.')) | {'excluded_observations': excluded}
            if len(ids) > MAX_DATES:
                raise _Unavailable('selection_too_large', f'This measurement supports up to {MAX_DATES} dates at a time.')
            rows, pol = self._request(ids, polarization)
            if any(row.get('site_id') != site_id for row in rows):
                raise _Unavailable('incompatible_site', 'All acquisitions must belong to the selected study area.')
            times = self._times(rows)
            rows = [row for _, row in sorted(zip(times, rows))]
            geometry = _comparable(rows)
            np, scientific = _load_tools()
            with self._lock:
                layers, _ = self._read(rows, pol, scientific)
                common = np.isfinite(layers[0].db)
                for layer in layers[1:]:
                    common &= np.isfinite(layer.db)
                n = int(common.sum())
                if not n:
                    raise _Unavailable('no_common_support', 'The selected dates have no shared finite positive power cells.')
                points = []
                for row, layer in zip(rows, layers):
                    p10, median, p90 = scientific.quantiles(layer.db[common])
                    points.append({'observation_id': row['id'], 'acquired_at': row['acquired_at'],
                                   'value': median, 'p10': p10, 'p90': p90,
                                   'valid_pixels': n, 'source_sha256': layer.sha256})
        except _Unavailable as error:
            return _unavailable(error)
        evidence_ids = list(dict.fromkeys(layer.evidence_id for layer in layers))
        summary = 'Each point uses the same finite positive native cells. Dates without a measured value are not interpolated.'
        if geometry['unknown_fields']:
            summary += ' ' + NOISE_LIMIT
        series = {'id': f'{site_id}-{pol.lower()}-backscatter', 'site_id': site_id, 'polarization': pol,
                  'title': f'{pol} radar return over time',
                  'statistic': 'Median gamma0 backscatter over the same pixels on every date', 'units': 'dB',
                  'points': points, 'summary': summary, 'evidence_ids': evidence_ids}
        return {'status': 'measured', 'grid': _grid(layers[0]), 'time_series': series,
                'common_pixels': n, 'grid_pixels': int(common.size), 'common_fraction': n / common.size,
                'sources': [_source(row, layer) for row, layer in zip(rows, layers)],
                'comparability': geometry, 'excluded_observations': excluded}
