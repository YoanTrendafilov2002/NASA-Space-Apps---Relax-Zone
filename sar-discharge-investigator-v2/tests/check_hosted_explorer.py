"""Acceptance checks for a running SARtainty explorer.

Run from the app directory with requests installed:
    python tests/check_hosted_explorer.py https://YOUR-EXPLORER-HOST --assistant --analysis
The assistant cases use up to six requests in the current visitor rate window.
The opt-in analysis cases use up to eight analysis POSTs and verify generated raster receipts.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import file_digest, sha256
from html.parser import HTMLParser
import json
from math import isclose, isfinite
from pathlib import Path
import re
import sys
from time import monotonic
from urllib.parse import urlsplit

import requests


DATA = Path(__file__).resolve().parents[1] / 'data'
EXPECTED_SITES = {'umm-al-quwain-nisar', 'curuai-nisar', 'burgas', 'everglades-nisar', 'uae-coastal-review',
                  'maranon-nisar', 'qeshm-nisar', 'gulf-eugene-nisar', 'eloi-bay-nisar', 'atanasovsko-nisar',
                  'hasik-nisar'}
MANIFESTS = ('non-sentinel-nisar.json', 'nisar-burgas.json', 'nisar-uae-coastal-review.json',
             'nisar-everglades.json', 'nisar-maranon.json', 'nisar-holdouts.json', 'nisar-wetland-holdout.json')
REVIEW_MANIFESTS = ('nisar-detections.json', 'nisar-wetland-context.json')
OBSERVATION_COUNT = 15
SOURCE_GRANULE_COUNT = 13
QESHM_HH_DATES = ('2026-07-17T01:35:18Z', '2026-08-10T01:35:17Z', '2026-08-22T01:35:16Z')
RASTER_FIELDS = ('image', 'science')
MIME_TYPES = {'.png': 'image/png', '.webp': 'image/webp', '.tif': 'image/tiff'}
VENDOR_TYPES = {'.png': 'image/png', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2'}
VENDOR_ASSETS = ('leaflet.js', 'leaflet.css', 'fonts.css', 'montagu-slab.woff2', 'source-sans-3.woff2',
                 'images/layers.png', 'images/layers-2x.png', 'images/marker-icon.png', 'images/marker-icon-2x.png', 'images/marker-shadow.png')
MAX_JSON_BYTES = 2_000_000
MAX_ASSET_BYTES = 50_000_000
MAX_ANALYSIS_POSTS = 10
ANALYSIS_ID = re.compile(r'analysis-[a-f0-9]{32}\Z')
PUBLISHED_ASSET = re.compile(r'/(?:sar|science)/nisar-[a-z0-9_-]+\.(?:png|webp|tif)\Z')
UAE_SEARCH = {'bbox': [55.6532, 25.5463, 55.7305, 25.6498], 'start': '2026-10-21',
              'end': '2026-10-21', 'mission': 'nisar', 'product': 'GCOV'}


class CheckError(Exception):
    """An acceptance condition failed."""


class Dependencies(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'script' and fields.get('src'):
            self.paths.add(fields['src'])
        if tag == 'link' and fields.get('rel') == 'stylesheet' and fields.get('href'):
            self.paths.add(fields['href'])


def require(condition, message):
    if not condition:
        raise CheckError(message)


def origin_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
            or (parsed.scheme == 'http' and parsed.hostname not in {'localhost', '127.0.0.1', '::1'})):
        raise argparse.ArgumentTypeError('Supply the HTTPS explorer origin or its local loopback HTTP URL.')
    return value.rstrip('/')


def local_release():
    manifests = [json.loads((DATA / name).read_text(encoding='utf-8-sig')) for name in MANIFESTS]
    observations = {row['id']: row for doc in manifests for row in doc['observations']}
    assets = {}

    def register(channel, field):
        path = channel.get(field + '_url')
        if not path:
            return
        require(PUBLISHED_ASSET.fullmatch(path), f'Invalid asset route in the local release: {path}')
        size = channel.get(field + '_size_bytes', channel.get('image_bytes' if field == 'image' else 'source_bytes'))
        require(isinstance(size, int) and 16 <= size <= MAX_ASSET_BYTES, f'Invalid size for {path}')
        digest = channel[field + '_sha256']
        require(re.fullmatch(r'[a-f0-9]{64}', digest), f'Invalid checksum for {path}')
        require(path not in assets, f'Duplicate release raster route: {path}')
        local = DATA / path.lstrip('/')
        require(local.is_file() and local.stat().st_size == size, f'Local size mismatch for {path}')
        with local.open('rb') as stream:
            require(file_digest(stream, 'sha256').hexdigest() == digest, f'Local checksum mismatch for {path}')
        assets[path] = size, digest

    for row in observations.values():
        for channel in row['channels'].values():
            for field in RASTER_FIELDS:
                register(channel, field)
    require(len(assets) == 50, f'Expected 50 approved channel assets, found {len(assets)}')
    layers = json.loads((DATA / 'nisar-change-layers.json').read_text(encoding='utf-8'))['layers']
    require(len(layers) == 4, 'Expected exactly four verified change layers')
    for layer in layers:
        require(layer['science_units'] == 'dB, later minus earlier', 'Change GeoTIFF must describe the signed dB unit')
        for field in RASTER_FIELDS:
            register(layer, field)
    require(len(assets) == 58, 'Expected eight change-layer assets')
    reviews = [layer for name in REVIEW_MANIFESTS for layer in
               json.loads((DATA / name).read_text(encoding='utf-8'))['layers']]
    time_series = json.loads((DATA / 'nisar-time-series.json').read_text(encoding='utf-8'))['series']
    require(len(reviews) == 6 and len(time_series) == 5, 'Expected six recognition reviews and five time series')
    for review in reviews:
        require(not review.get('image_url'), 'Expected vector recognition review geometry')
        register(review, 'science')
    require(len(assets) == 60 and sum(path.endswith('.tif') for path in assets) == 31,
            'Expected 60 rasters including 31 scientific downloads')
    source = next(doc['evidence'][0]['source_filename'] for doc in manifests if doc['evidence'][0].get('source_filename'))
    return observations, assets, source, manifests[0]['default_selection'], layers, reviews, time_series


def outline_bounds(geometry):
    require(geometry['type'] in {'Polygon', 'MultiPolygon'}, 'Recognition outline is not polygonal')
    polygons = [geometry['coordinates']] if geometry['type'] == 'Polygon' else geometry['coordinates']
    vertices = []
    for polygon in polygons:
        require(bool(polygon), 'Empty recognition polygon')
        for ring in polygon:
            require(len(ring) >= 4 and ring[0] == ring[-1], 'Invalid recognition polygon ring')
            for lon, lat in ring:
                require(-180 <= lon <= 180 and -90 <= lat <= 90, 'Outline coordinate outside WGS84 bounds')
                vertices.append((lon, lat))
    require(bool(vertices), 'Recognition geometry has no vertices')
    x, y = zip(*vertices)
    bounds = min(x), min(y), max(x), max(y)
    require(bounds[0] < bounds[2] and bounds[1] < bounds[3], 'Degenerate recognition outline')
    return bounds


def validate_reviews(reviews, observations, evidence_ids):
    selections = set()
    for review in reviews:
        observation = observations[review['observation_id']]
        channel = observation['channels'][review['polarization']]
        selection = review['site_id'], observation['id'], review['polarization']
        require(observation['site_id'] == review['site_id'] and channel['status'] == 'validated'
                and selection not in selections, f'Unmatched measured view for {review["id"]}')
        selections.add(selection)
        require(bool(review['evidence_ids']) and set(review['evidence_ids']) <= evidence_ids,
                f'Unregistered evidence for {review["id"]}')
        classes = {record['id'] for record in review['classes']}
        overlays = review['outlines']
        require(overlays['type'] == 'FeatureCollection' and bool(overlays['features']),
                f'Missing recognition geometry for {review["id"]}')
        (south, west), (north, east) = channel['image_bounds']
        covers_view = False
        for feature in overlays['features']:
            require(feature['type'] == 'Feature' and feature['properties']['class_id'] in classes,
                    f'Unregistered outline class for {review["id"]}')
            x1, y1, x2, y2 = outline_bounds(feature['geometry'])
            covers_view |= x1 < east and x2 > west and y1 < north and y2 > south
        require(covers_view, f'Recognition outlines do not intersect the measured display window for {review["id"]}')


def validate_time_series(series, observations, evidence_ids):
    selections = set()
    for record in series:
        label = record['id']
        selection = record['site_id'], record['polarization']
        points = record['points']
        dates = tuple(point['acquired_at'] for point in points)
        require(selection not in selections and record['units'] == 'dB' and len(dates) >= 2,
                f'Invalid time-series identity or units for {label}')
        selections.add(selection)
        require(dates == tuple(sorted(set(dates))), f'Unsorted or duplicate acquisition dates in {label}')
        require(bool(record['evidence_ids']) and set(record['evidence_ids']) <= evidence_ids,
                f'Unregistered evidence for {label}')
        counts, grids = set(), set()
        for point in points:
            observation = observations[point['observation_id']]
            channel = observation['channels'][record['polarization']]
            require(observation['site_id'] == record['site_id'] and observation['acquired_at'] == point['acquired_at']
                    and channel['status'] == 'validated', f'Unmatched measured observation in {label}')
            require(point['source_sha256'] == channel['science_sha256'], f'Source raster checksum mismatch in {label}')
            measured = [point[field] for field in ('p10', 'value', 'p90')]
            require(all(isfinite(number) for number in measured) and measured == sorted(measured),
                    f'Invalid dB quantiles in {label}')
            count = point['valid_pixels']
            require(type(count) is int and 0 < count <= channel['valid_pixels'], f'Invalid measured support in {label}')
            counts.add(count)
            grids.add((channel['science_epsg'], tuple(channel['science_shape']), tuple(channel['science_transform'])))
        require(len(counts) == len(grids) == 1, f'Time series uses inconsistent native support in {label}')
        if selection == ('qeshm-nisar', 'HH'):
            require(dates == QESHM_HH_DATES and counts == {1_610_000}, 'Qeshm HH must use the registered three-date common window')
    require(('qeshm-nisar', 'HH') in selections, 'Missing Qeshm HH time series')


def expect(response, status):
    require(response.status_code == status, f'HTTP {response.status_code}, expected HTTP {status}')


def json_body(response):
    require(response.headers.get('Content-Type', '').split(';')[0] == 'application/json', 'Expected a JSON response')
    payload = bytearray()
    for chunk in response.iter_content(16_384):
        payload.extend(chunk)
        require(len(payload) <= MAX_JSON_BYTES, 'JSON exceeded the acceptance-check body cap')
    return json.loads(payload)


class HostedChecks:
    def __init__(self, origin, session):
        self.origin = origin
        self.session = session
        self.session.headers['Accept-Encoding'] = 'identity'
        self.cases = []
        self.requests = 0
        self.evidence_ids = set()
        self.analysis_posts = 0

    def request(self, method, path, **options):
        self.requests += 1
        return self.session.request(method, self.origin + path, stream=True, allow_redirects=False,
                                    timeout=(8, 55), **options)

    def json(self, method, path, *, box=None, origin=None, status=200):
        headers = {'Origin': origin or self.origin} if method == 'POST' else {}
        with self.request(method, path, headers=headers, json=box) as response:
            expect(response, status)
            value = json_body(response)
            allow = response.headers.get('Access-Control-Allow-Origin')
            if method == 'POST' and status == 200 and origin is None and self.origin.startswith('https://'):
                require(allow == self.origin, 'Successful hosted POST omitted its exact allowed origin')
            if origin and origin != self.origin:
                require(allow != origin, 'Unrelated origin was incorrectly allowed to read the response')
            return value

    def run(self, label, check, *args, **options):
        started = monotonic()
        error = None
        details = None
        try:
            details = check(*args, **options)
        except (CheckError, KeyError, TypeError, ValueError, requests.RequestException) as exc:
            error = str(exc)
        entry = {'name': label, 'ok': error is None, 'seconds': round(monotonic() - started, 3)}
        if error is None:
            entry['details'] = details
        else:
            entry['error'] = error
        self.cases.append(entry)
        print(f'{"PASS" if error is None else "FAIL"} {label}' + (f' ({error})' if error else ''), flush=True)
        return error is None

    def health(self):
        value = self.json('GET', '/health')
        require(value.get('service') == 'relax-zone-explorer' and value.get('status') == 'ok', 'Wrong hosted service')
        require(value.get('sites') == len(EXPECTED_SITES) and value.get('recorded_observations') == OBSERVATION_COUNT,
                'Unexpected hosted release counts')
        return value

    def bootstrap(self, known, assets, default, layers, reviews, time_series):
        value = self.json('GET', '/api/explorer/bootstrap')
        require({site['id'] for site in value['sites']} == EXPECTED_SITES and len(value['sites']) == len(EXPECTED_SITES),
                'Unexpected hosted site list')
        require(value['default_selection'] == default, 'Unexpected initial geographic selection')
        rows = {row['id']: row for row in value['observations']}
        require(set(rows) == set(known) and len(value['observations']) == len(known) == OBSERVATION_COUNT,
                'Unexpected hosted observations')
        products = {row['product_id'] for row in rows.values()}
        require(len(products) == SOURCE_GRANULE_COUNT and products == {row['acquisition_id'] for row in rows.values()},
                'Unexpected unique source granules')
        require(value['change_layers'] == layers, 'Hosted change layers differ from the verified release')
        require(value['recognition_layers'] == reviews, 'Hosted recognition geometry or assets differ from the verified release')
        require(value['time_series'] == time_series, 'Hosted time series differ from the verified release')
        self.evidence_ids = {item['id'] for item in value['evidence']}
        require(len(self.evidence_ids) == len(value['evidence']), 'Bootstrap has duplicate evidence IDs')
        paths = set()
        for obs_id, row in rows.items():
            require(is_nisar(row), f'Non-NISAR row in bootstrap: {obs_id}')
            local = known[obs_id]
            require(row['product_id'] == local['product_id'] and row['acquired_at'] == local['acquired_at'], f'Unpinned observation: {obs_id}')
            require(row['site_id'] == local['site_id'] and row['geometry'] == local['geometry'],
                    f'Observation geometry differs from the release: {obs_id}')
            require(row.get('science_requires_earthdata', False) is False, f'Prepared science requires sign-in: {obs_id}')
            require(row['channels'] == local['channels'], f'Channel evidence differs from the release: {obs_id}')
            paths.update(path for channel in row['channels'].values() for key, path in channel.items() if key in {'image_url', 'science_url'})
        paths.update(record[field + '_url'] for record in [*layers, *reviews] for field in RASTER_FIELDS if record.get(field + '_url'))
        require(paths == set(assets), 'Bootstrap raster paths differ from the release')
        require({row['evidence_id'] for row in rows.values()} <= self.evidence_ids, 'Observation source evidence is absent')
        validate_reviews(reviews, rows, self.evidence_ids)
        validate_time_series(time_series, rows, self.evidence_ids)
        return {'sites': sorted(EXPECTED_SITES), 'observations': len(rows), 'source_granules': len(products), 'assets': len(paths),
                'evidence_records': len(self.evidence_ids), 'recognition_reviews': len(reviews),
                'time_series': len(time_series), 'prepared_science_requires_earthdata': False}

    def homepage(self):
        with self.request('GET', '/') as response:
            expect(response, 200)
            require(response.headers.get('Content-Type', '').startswith('text/html'), 'Wrong app MIME type')
            content = b''.join(response.iter_content(8192)).decode('utf-8')
        require('SARtainty' in content, 'Missing hosted product name')
        dependencies = Dependencies()
        dependencies.feed(content)
        require(all(not urlsplit(path).netloc and not urlsplit(path).scheme for path in dependencies.paths),
                'App shell references an external script or stylesheet')
        require({'/vendor/leaflet.js', '/vendor/leaflet.css', '/vendor/fonts.css'} <= dependencies.paths,
                'Missing local map library or font dependency')
        return {'local_dependencies': sorted(dependencies.paths)}

    def exact_asset(self, path, filename, media_type):
        local = (DATA.parent / filename).read_bytes()
        with self.request('GET', path) as response:
            expect(response, 200)
            require(response.headers.get('Content-Type', '').split(';')[0] == media_type, 'Wrong asset MIME type')
            declared_length = response.headers.get('Content-Length')
            require(declared_length is None or declared_length == str(len(local)), 'Wrong declared asset length')
            transfer_encoding = response.headers.get('Transfer-Encoding')
            received = bytearray()
            for chunk in response.iter_content(16_384):
                received.extend(chunk)
                require(len(received) <= len(local), 'Asset exceeds its pinned byte length')
        require(received == local, 'Hosted asset differs from the local release')
        return {'bytes': len(received), 'sha256': sha256(received).hexdigest(), 'transfer_encoding': transfer_encoding}

    def vendor(self, filename):
        path = 'vendor/' + filename
        return self.exact_asset('/' + path, path, VENDOR_TYPES[Path(filename).suffix])

    def research(self):
        value = self.json('GET', '/api/explorer/experiments')
        local = json.loads((DATA / 'nisar-experiments.json').read_text(encoding='utf-8'))
        require(value == local, 'Hosted experiment records differ from the reviewed release')
        rows = value['experiments']
        require(len(rows) == 6, 'Expected six reviewed experiments')
        for row in rows:
            require(row['status'] == 'completed' and row['outcome'] == 'research_only', 'Recognition result has an unexpected deployment status')
            require(set(row['evidence_ids']) <= self.evidence_ids, f'Experiment evidence absent: {row["id"]}')
        return {row['id']: row['outcome'] for row in rows}

    def raster(self, path, size, checksum, *, signatures=()):
        mime = MIME_TYPES[Path(path).suffix]
        with self.request('HEAD', path, headers={'Range': 'bytes=0-15'}) as response:
            head_status = response.status_code
            if head_status == 206:
                require(response.headers.get('Content-Range') == f'bytes 0-15/{size}', 'Wrong ranged HEAD representation size')
                require(response.headers.get('Content-Length') == '16', 'Wrong ranged HEAD segment length')
            else:
                expect(response, 200)
                require(response.headers.get('Content-Length') == str(size), 'Wrong HEAD representation size')
            require(response.headers.get('Content-Type') == mime, 'Wrong HEAD media type')
        count = 0
        hasher = sha256()
        prefix = bytearray()
        prefix_size = max((len(signature) for signature in signatures), default=0)
        with self.request('GET', path) as response:
            expect(response, 200)
            require(response.headers.get('Content-Type') == mime, 'Wrong raster MIME type')
            require(response.headers.get('Content-Length') == str(size), 'Wrong GET representation size')
            if mime == 'image/tiff':
                require(response.headers.get('Content-Disposition') == f'attachment; filename="{Path(path).name}"', 'Scientific download lacks its expected attachment filename')
            for chunk in response.iter_content(131_072):
                count += len(chunk)
                require(count <= size, 'Raster body exceeded the pinned size')
                hasher.update(chunk)
                if len(prefix) < prefix_size:
                    prefix.extend(chunk[:prefix_size-len(prefix)])
        require(count == size and hasher.hexdigest() == checksum, 'Raster bytes or SHA-256 differ from the local release')
        require(not signatures or any(prefix.startswith(signature) for signature in signatures),
                'Generated raster bytes have the wrong format signature')
        return {'bytes': count, 'sha256': checksum, 'range_head_status': head_status}

    def unregistered(self, path):
        with self.request('GET', path) as response:
            expect(response, 404)
        return {'status': 404}

    def invalid_origin(self):
        reply = self.json('POST', '/api/explorer/search', box=UAE_SEARCH,
                          origin='https://acceptance-check.invalid', status=403)
        require(reply['error']['code'] == 'origin_not_allowed', 'Mismatched origin was not rejected by the app')
        return {'status': 403}

    def invalid_mission(self):
        reply = self.json('POST', '/api/explorer/search', box=UAE_SEARCH | {'mission': 'sentinel-1'}, status=400)
        require(reply['error']['code'] == 'invalid_mission', 'An unsupported mission reached search')
        return {'status': 400}

    def live_query(self, observations):
        reply = self.json('POST', '/api/explorer/search', box=UAE_SEARCH)
        rows = reply['observations']
        require(not reply['errors'] and reply['query']['mission'] == 'nisar' and rows, 'Provider search returned errors or no observations')
        require(all(is_nisar(row) and row['acquired_at'].startswith('2026-10-21') for row in rows), 'Unexpected provider result in pinned query')
        local = observations['nisar-uae-20261021']
        require(any(row['id'] == local['id'] and row['channels']['HH']['status'] == 'validated' for row in rows), 'Pinned local frequency-A HH result is missing')
        require(any(row['product_id'] == local['product_id'] and row['frequency'] == 'B'
                    and row.get('science_requires_earthdata') is True for row in rows), 'No corresponding provider-derived frequency-B result')
        return {'observations': len(rows), 'acquired_on': '2026-10-21', 'frequency_b_provider_result': True}

    def assistant(self, text, action, *, site=None, must_say=(), evidence=(), known=(), change_layer=None, polarizations=None, answer_kind=None):
        reply = self.json('POST', '/api/assistant', box={'message': text, 'state': {}, 'observations': []})
        result = reply['action']
        require(result['type'] == action, f'Assistant returned {result["type"]!r}, expected {action!r}')
        require(all(item.casefold() in reply['message'].casefold() for item in must_say), 'Assistant answer omitted expected measured or scientific detail')
        require(set(evidence) <= set(reply['evidence_ids']), 'Assistant answer omitted expected source IDs')
        if action == 'answer':
            require(reply.get('answer_model') == 'nisar_matrix', 'Measured question did not use the matrix model')
        if answer_kind:
            require(reply.get('answer_kind') == answer_kind, 'Assistant did not answer the intended topic')
        if change_layer:
            require(result.get('change_layer_id') == change_layer, 'Assistant did not activate the verified change overlay')
        if polarizations:
            require(result.get('polarizations') == polarizations, 'Assistant selected unexpected polarization')
        if site:
            require(result.get('site_id') == site and result.get('mission') == 'nisar', 'Assistant selected the wrong site or mission')
            dated = [known[identifier] for identifier in result.get('selected_ids', ()) if identifier in known]
            require(dated and all(row['site_id'] == site for row in dated), 'Assistant did not select the expected real observation(s)')
            require(len(dated) == len(result['selected_ids']), 'Assistant selected an unverified observation ID')
            for polarization in result.get('polarizations', []):
                require(any(row['channels'].get(polarization, {}).get('status') in {'validated', 'advertised', 'preview', 'catalogue_only'} for row in dated),
                        f'Assistant selected absent polarization {polarization}')
        return {'action': action, 'site_id': result.get('site_id'), 'change_layer_id': result.get('change_layer_id'),
                'evidence_ids': reply['evidence_ids'], 'model_used': reply.get('model_used'), 'answer_kind': reply.get('answer_kind')}

    def analysis(self, kind, site, polarization, ids):
        require(self.analysis_posts < MAX_ANALYSIS_POSTS, 'Analysis POST check budget reached')
        self.analysis_posts += 1
        payload = {'kind': kind, 'site_id': site, 'polarization': polarization, 'observation_ids': list(ids)}
        reply = self.json('POST', '/api/explorer/analyze', box=payload)
        require(all(reply[key] == payload[key] for key in ('kind', 'site_id', 'polarization'))
                and len(reply['observation_ids']) == len(ids) and set(reply['observation_ids']) == set(ids),
                'Analysis response changed the requested scene or channel')
        require('_artifacts' not in reply, 'Analysis inlined internal raster bytes')
        return reply

    def analysis_result_id(self, result, layer):
        identifier = result['analysis_id']
        require(result['status'] == 'measured' and ANALYSIS_ID.fullmatch(identifier)
                and layer['id'] == layer['analysis_id'] == identifier, 'Missing fresh analysis identity')
        stamp = datetime.fromisoformat(result['created_at'].replace('Z', '+00:00'))
        require(stamp.utcoffset() == timezone.utc.utcoffset(stamp), 'Analysis receipt does not record a UTC timestamp')
        require(bool(layer['evidence_ids']) and set(layer['evidence_ids']) <= self.evidence_ids,
                'Analysis receipt references unregistered evidence')
        return identifier

    def analysis_raster(self, layer, field):
        identifier = layer['analysis_id']
        folder, suffix = ('sar', 'png') if field == 'image' else ('science', 'tif')
        path, size, digest = (layer[f'{field}_{key}'] for key in ('url', 'size_bytes', 'sha256'))
        require(path == f'/{folder}/nisar-{identifier}.{suffix}', 'Unexpected generated raster route')
        require(type(size) is int and 16 <= size <= MAX_ASSET_BYTES and re.fullmatch(r'[a-f0-9]{64}', digest),
                'Malformed generated raster receipt')
        signatures = (b'\x89PNG\r\n\x1a\n',) if field == 'image' else (b'II*\0', b'MM\0*', b'II+\0', b'MM\0+')
        return self.raster(path, size, digest, signatures=signatures)

    def qeshm_runtime_change(self, observations):
        ids = ('nisar-qeshm-20260717', 'nisar-qeshm-20260822')
        first = self.analysis('change', 'qeshm-nisar', 'HH', ids)
        require(first.get('status') == 'measured', first.get('message', 'Qeshm HH calculation failed'))
        initial_layer = first['change_layer']
        initial_id = self.analysis_result_id(first, initial_layer)
        second = self.analysis('change', 'qeshm-nisar', 'HH', ids[::-1])
        require(second.get('status') == 'measured', second.get('message', 'Repeated Qeshm HH calculation failed'))
        final_layer = second['change_layer']
        final_id = self.analysis_result_id(second, final_layer)
        require(initial_id != final_id, 'Separate change requests did not produce distinct analysis IDs')
        require(first['metrics'] == first['comparison'] == second['metrics'] == second['comparison'],
                'Identical acquisition inputs changed the measured results')
        metrics = first['metrics']
        require(metrics['paired_pixels'] == metrics['grid_pixels'] == 1_610_000 and metrics['paired_fraction'] == 1.0,
                'Qeshm change used an incorrect paired native coverage')
        require(abs(metrics['median_change_db'] - (-0.3267003459546505)) < 1e-9
                and metrics['brighter_at_least_3db_pixels'] == 91_413
                and metrics['darker_at_least_3db_pixels'] == 196_688, 'Qeshm signed backscatter changed from verified source values')
        require(first['sources'] == second['sources']
                and [row['observation_id'] for row in first['sources']] == list(ids),
                'Runtime change did not return the two chronologically ordered sources')
        for source in first['sources']:
            expected = observations[source['observation_id']]['channels']['HH']
            require(source['source_sha256'] == expected['science_sha256'], 'Runtime change used the wrong registered source raster')
        require(first['comparability'] == second['comparability']
                and first['comparability']['status'] == 'qualified'
                and set(first['comparability']['unknown_fields']) == {'look_direction', 'noise_correction_applied'},
                'Qeshm comparison omitted a known acquisition metadata limitation')
        rasters = {}
        for label, result, layer in (('first', first, initial_layer), ('second', second, final_layer)):
            source = observations[ids[0]]['channels']['HH']
            require(result['observation_ids'] == list(ids)
                    and (layer['earlier_id'], layer['later_id']) == ids
                    and (layer['earlier_date'], layer['later_date']) == ('2026-07-17', '2026-08-22'),
                    'Reversed request was not normalized to later minus earlier')
            require(layer['unit'] == 'dB' and layer['threshold_db'] == 3
                    and layer['science_units'] == 'dB, later minus earlier'
                    and layer['paired_native_pixels'] == 1_610_000
                    and all(layer[key] == source[key] for key in ('science_epsg', 'science_shape', 'science_transform')),
                    'Generated signed scientific receipt does not match the native measurement grid')
            require(layer['image_bounds'] == source['image_bounds'] and layer['image_epsg'] == 3857,
                    'Generated change image is not aligned to the registered map window')
            require({item['id']: item['native_pixels'] for item in layer['classes']}
                    == {'brightened': 91_413, 'darkened': 196_688}, 'Generated overlay classes differ from the paired measurements')
            rasters[label] = {field: self.analysis_raster(layer, field) for field in RASTER_FIELDS}
        require(all(rasters['first'][field]['sha256'] == rasters['second'][field]['sha256']
                    and rasters['first'][field]['bytes'] == rasters['second'][field]['bytes'] for field in RASTER_FIELDS),
                'Identical source pixels produced different generated artifact receipts')
        return {'analysis_ids': [initial_id, final_id], 'comparison': metrics, 'rasters': rasters}

    def qeshm_runtime_series(self, reference):
        earlier, later = 'nisar-qeshm-20260717', 'nisar-qeshm-20260810'
        ids = (earlier, later)
        result = self.analysis('time_series', 'qeshm-nisar', 'HH', ids[::-1])
        require(result.get('status') == 'measured', result.get('message', 'Qeshm HH series failed'))
        series = result['time_series']
        identifier = self.analysis_result_id(result, series)
        pinned = next(row for row in reference if (row['site_id'], row['polarization']) == ('qeshm-nisar', 'HH'))
        expected = [row for row in pinned['points'] if row['observation_id'] in ids]
        points = series['points']
        require(result['observation_ids'] == list(ids) and series['units'] == 'dB' and len(points) == len(expected) == 2,
                'Runtime dates or common-support measurements differ from the selected two-date subset')
        for actual, pinned_point in zip(points, expected):
            require(all(actual[key] == pinned_point[key] for key in ('observation_id', 'acquired_at', 'valid_pixels', 'source_sha256'))
                    and all(isclose(actual[key], pinned_point[key], rel_tol=0, abs_tol=1e-9) for key in ('p10', 'value', 'p90')),
                    'Runtime series point does not match the registered measurements for this date')
        require(result['metrics'] == {'common_pixels': 1_610_000, 'grid_pixels': 1_610_000, 'common_fraction': 1.0}
                and not result['excluded_observations'], 'Qeshm series used different native support')
        return {'analysis_id': identifier, 'points': points, 'common_pixels': 1_610_000}

    def runtime_recognition(self, mode, observation_id, registry):
        registered = registry['rows'][observation_id]
        site_id = registered['site_id']
        result = self.analysis(mode, site_id, 'HH', (observation_id,))
        require(result.get('status') == 'measured' and result.get('runtime_computed') is True and result.get('mode') == mode,
                result.get('message', f'Runtime {mode} screening did not run'))
        layer = result['recognition_layer']
        identifier = self.analysis_result_id(result, layer)
        require(layer['site_id'] == site_id and layer['observation_id'] == observation_id
                and layer['polarization'] == 'HH' and layer['origin'] == 'runtime_frozen_model',
                'Recognition overlay belongs to a different source or an archived result')
        source = result['source']
        model_hash = (registry['wetland_screen'] if mode == 'wetland' else registry['models'][mode])['sha256']
        require(source['product_id'] == registered['product_id']
                and source['input_sha256'] == registered['sha256']
                and source['map_science_sha256'] == registered['map_science_sha256']
                and source['model_sha256'] == result['model']['sha256'] == model_hash,
                'Runtime recognition used different input pixels or model coefficients')
        top, bottom, left, right = registered['crop']
        rows, cols = bottom-top, right-left
        counts = result['counts']
        wetland_classes = {'open-water-core': ('open_water_cells', 1), 'flooded-vegetation': ('flooded_vegetation_cells', 3),
                           'unresolved-water-edge': ('uncertain_shore_cells', 4)}
        expected_classes = wetland_classes if mode == 'wetland' else {'dark-water-candidate': ('flagged_cells', 1)}
        require(result['metrics'] == counts and source['shape'] == [rows, cols]
                and source['crs'] == f"EPSG:{registered['epsg']}" and source['cell_size_m'] == 60
                and counts['total_cells'] == rows*cols == counts['assessed_cells'] + counts['unassessed_cells']
                and 0 < counts['flagged_cells'] <= counts['assessed_cells']
                and counts['assessed_cells'] == counts['assessed_without_flag_cells']
                    + sum(counts[field] for field, _ in expected_classes.values()),
                'Recognition counts or geometry do not describe the same analysis crop')
        require(all(counts[key] == value for key, value in registered.get('counts_expected', {}).items()),
                'Runtime model counts differ from the verified reference counts')
        require({item['id']: item['code'] for item in layer['classes']}
                == {label: code for label, (_, code) in expected_classes.items()},
                'Recognition output lost its class identifiers or scientific class codes')
        if mode == 'wetland':
            require(counts['flagged_cells'] == counts['open_water_cells'] < counts['r6_flagged_cells']
                    and counts['uncertain_shore_cells'] > 0 and counts['flooded_vegetation_cells'] == 0
                    and counts['vegetation_evaluated_cells'] == 0 and result['model']['vegetation_status'] == 'withheld'
                    and set(layer['science_codes']) == {'0', '1', '2', '3', '4'},
                    'Wetland result omitted an uncertainty class or asserted unqualified flooded vegetation')
        rasters = {'science': self.analysis_raster(layer, 'science')}
        if layer.get('image_url'):
            rasters['image'] = self.analysis_raster(layer, 'image')
        else:
            geometry = layer['outlines']
            populated_classes = {label for label, (field, _) in expected_classes.items() if counts[field] > 0}
            require(geometry['type'] == 'FeatureCollection' and bool(geometry['features'])
                    and {feature['properties']['class_id'] for feature in geometry['features']} == populated_classes,
                    'Recognition outlines omit a populated class or show an empty class')
        return {'analysis_id': identifier, 'mode': mode, 'counts': counts, 'model_sha256': model_hash, 'rasters': rasters}

    def runtime_wetland_abstention(self, registry):
        observation_id = 'nisar-uae-coast-20261021'
        row = registry['rows'][observation_id]
        result = self.analysis('wetland', row['site_id'], 'HH', (observation_id,))
        require(result.get('status') == 'unsupported' and result.get('reason') == 'no_assessable_cells'
                and result.get('runtime_computed') is True and 'noise' in result['message'].lower()
                and not any(key in result for key in ('analysis_id', 'change_layer', 'time_series', 'recognition_layer')),
                'The UAE noise-limited crop yielded an overlay or omitted its abstention reason')
        counts = result['counts']
        require(result['metrics'] == counts
                and all(counts[key] == value for key, value in row['counts_expected'].items())
                and counts['total_cells'] == counts['unassessed_cells'] == 22_500
                and counts['r6_flagged_cells'] == 11_515 and result['model']['vegetation_status'] == 'withheld'
                and result['source']['input_sha256'] == row['sha256']
                and result['source']['model_sha256'] == registry['wetland_screen']['sha256'],
                'The UAE abstention did not preserve the verified scope, previous counts or provenance')
        return {'status': result['status'], 'reason': result['reason'], 'counts': counts}

    def runtime_missing_channel(self):
        result = self.analysis('change', 'qeshm-nisar', 'HV', ('nisar-qeshm-20260717', 'nisar-qeshm-20260822'))
        require(result['status'] == 'unavailable' and result['reason'] == 'channel_unavailable'
                and 'HV' in result['message'] and not any(key in result for key in
                    ('analysis_id', 'change_layer', 'time_series', 'recognition_layer', 'comparison', 'metrics')),
                'Missing Qeshm HV returned a fabricated measurement or no clear explanation')
        return {'status': result['status'], 'reason': result['reason']}


def is_nisar(row):
    return (row.get('mission') == 'nisar' and row.get('provider') == 'NASA ASF DAAC'
            and row.get('product', '').startswith('NISAR ') and row.get('product_id', '').startswith('NISAR_'))


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('origin', type=origin_url)
    cli.add_argument('--assistant', action='store_true', help='exercise the live assistant after its rate window clears')
    cli.add_argument('--analysis', action='store_true', help='run opt-in runtime analysis and generated file checks')
    cli.add_argument('--report', type=Path, help='write a machine-readable result to this path')
    args = cli.parse_args()
    observations, assets, source, default, layers, reviews, time_series = local_release()
    with requests.Session() as session:
        session.trust_env = False
        hosted = HostedChecks(args.origin, session)
        healthy = hosted.run('Health and release counts', hosted.health)
        if healthy:
            hosted.run('NISAR-only catalogue, recognition outlines and measured time series', hosted.bootstrap,
                       observations, assets, default, layers, reviews, time_series)
            hosted.run('Hosted app shell uses local assets', hosted.homepage)
            for filename in VENDOR_ASSETS:
                hosted.run(f'Local vendor dependency {filename}', hosted.vendor, filename)
            for path, filename, mime in (('/science-review', 'evidence.html', 'text/html'),
                                         ('/evidence.css', 'evidence.css', 'text/css'),
                                         ('/science-review-notes', 'NISAR_DATA_REVIEW.md', 'text/plain')):
                hosted.run(f'Evidence guide {path}', hosted.exact_asset, path, filename, mime)
            hosted.run('Six source-backed research statuses', hosted.research)
            for path, (size, digest) in sorted(assets.items()):
                hosted.run(f'Ranged HEAD and SHA-256 {path}', hosted.raster, path, size, digest)
            for path in ('/science/unregistered-acceptance-sample.tif', f'/science/{source}',
                         '/data/science/nisar-uae-20261021-hh.tif', '/data/explorer.json', '/app.js'):
                hosted.run(f'Unregistered route {path}', hosted.unregistered, path)
            hosted.run('Reject a different origin', hosted.invalid_origin)
            hosted.run('Reject a non-NISAR search', hosted.invalid_mission)
            hosted.run('Pinned real ASF query with local A and provider B', hosted.live_query, observations)
            if args.analysis:
                registry = json.loads((DATA / 'model-inputs/runtime-recognition.json').read_text(encoding='utf-8'))
                hosted.run('Runtime Qeshm HH reproducible signed change', hosted.qeshm_runtime_change, observations)
                hosted.run('Runtime Qeshm HH alternate two-date series', hosted.qeshm_runtime_series, time_series)
                hosted.run('Runtime Curuai frozen wetland screen', hosted.runtime_recognition,
                           'wetland', 'nisar-curuai-20261012', registry)
                hosted.run('Runtime Hasik wetland holdout and uncertain water edges', hosted.runtime_recognition,
                           'wetland', 'nisar-hasik-20260811', registry)
                hosted.run('Runtime UAE noise-limited wetland abstention', hosted.runtime_wetland_abstention, registry)
                hosted.run('Runtime Eugene Island frozen oil screen', hosted.runtime_recognition,
                           'oil', 'nisar-eugene-20260827', registry)
                hosted.run('Runtime Qeshm missing HV returns a clean gap', hosted.runtime_missing_channel)
            if args.assistant:
                cases = (
                    ('Show me wetlands', 'select', {'site': 'curuai-nisar', 'must_say': ('solid orange outlines',), 'evidence': ('nisar-curuai-20260813-source',)}),
                    ('Show me oil', 'select', {'site': 'uae-coastal-review', 'must_say': ('dashed red', 'mangroves'),
                                               'evidence': ('uae-falcon-eye-reference',), 'polarizations': ['HH']}),
                    ('Could you show what changed at Curuai in HV?', 'select', {'site': 'curuai-nisar', 'must_say': ('same ground', 'weaker return'),
                                                                             'change_layer': 'nisar-curuai-change-hv'}),
                    ('How did water levels change near Curuai?', 'answer', {'must_say': ('4.65 m', 'Curuai has its own lake level'),
                                                                           'evidence': ('ana-obidos-2026-10-12',), 'answer_kind': 'water_level'}),
                    ('Could you show what changed at Marañón in HH?', 'select', {'site': 'maranon-nisar', 'must_say': ('HH',),
                                                                                'change_layer': 'nisar-maranon-change-hh'}),
                    ('Why does a radar shadow form behind a hill?', 'answer', {'must_say': ('block', 'shadow'),
                                                                             'evidence': ('asf-radar-geometry',), 'answer_kind': 'terrain_shadow'}),
                )
                for message, action, options in cases:
                    if not hosted.run(f'Assistant {message}', hosted.assistant, message, action, known=observations, **options):
                        # Stop the model requests on failure to preserve the shared rate window.
                        break
    count = sum(case['ok'] for case in hosted.cases)
    result = {'origin': args.origin, 'ok': count == len(hosted.cases), 'requests': hosted.requests,
              'assistant_requested': args.assistant, 'analysis_requested': args.analysis,
              'analysis_posts': hosted.analysis_posts, 'cases': hosted.cases}
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(f'{count}/{len(hosted.cases)} cases passed in {hosted.requests} HTTP requests' +
          ('' if args.assistant else ' (assistant omitted).'), flush=True)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
