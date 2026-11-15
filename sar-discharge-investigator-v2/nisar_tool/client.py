"""Bounded public NISAR catalogue and provider-browse client.

This client never downloads scientific HDF5 assets or authenticates. All network
access goes through the injected, host-validating transport. Provider browse
images are verified as PNG/JPEG and remain explicitly non-scientific previews.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import math
import os
import re
import tempfile
import time
from datetime import date, datetime, time as daytime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, urlunsplit

from PIL import Image

from .errors import ApiError

ASF_SEARCH_URL = 'https://api.daac.asf.alaska.edu/services/search/param'
PRODUCT_TYPES = frozenset({'GCOV', 'GUNW', 'GOFF', 'SME2', 'GSLC', 'RSLC'})
MAX_RESULTS = 20
MAX_PREVIEW_BYTES = 8_000_000
MAX_PREVIEW_PIXELS = 40_000_000
MAX_INLINE_BYTES = 32_000_000
MAX_CACHE_BYTES = 256_000_000
CACHE_TTL_SECONDS = 600
_PRODUCT_ID = re.compile(r'NISAR_[A-Za-z0-9_-]{6,290}\Z')
_CONCEPT_COLLECTION = re.compile(r'C\d+-[A-Z0-9_]+\Z')
_CONCEPT_GRANULE = re.compile(r'G\d+-[A-Z0-9_]+\Z')


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def _stable_url(value, *, reject_query=False):
    if not isinstance(value, str):
        return None
    try:
        p = urlsplit(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password:
            return None
        if reject_query and p.query:
            return None
        return urlunsplit((p.scheme, p.netloc, p.path, '', ''))
    except ValueError:
        return None


def _size(value):
    if isinstance(value, dict):
        value = value.get('bytes')
    if isinstance(value, bool):
        return None
    try:
        n = int(value)
        if isinstance(value, float) and value != n:
            return None
        return n if n >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _validate_id(product_id):
    if not isinstance(product_id, str) or not _PRODUCT_ID.fullmatch(product_id):
        raise ApiError('invalid_product_id', 'Expected a NISAR provider product identifier.', status=400)
    return product_id


def _parse_date(value, *, end=False):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ApiError('invalid_date', 'Dates must be ISO dates or timezone-aware ISO datetimes.', status=400)
    value = value.strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            dt = datetime.combine(date.fromisoformat(value), daytime.max if end else daytime.min, timezone.utc)
        else:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if dt.tzinfo is None or dt.utcoffset() is None:
                raise ValueError('Missing timezone')
            dt = dt.astimezone(timezone.utc)
        return dt.isoformat(timespec='microseconds' if dt.microsecond else 'seconds').replace('+00:00', 'Z')
    except (ValueError, OverflowError):
        raise ApiError('invalid_date', 'Dates must be valid ISO dates or timezone-aware ISO datetimes.', status=400) from None


def _validate_bbox(bbox):
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ApiError('invalid_bbox', 'bbox must contain [west, south, east, north] in longitude/latitude degrees.', status=400)
    try:
        if any(isinstance(v, bool) for v in bbox):
            raise ValueError()
        b = [float(v) for v in bbox]
        west, south, east, north = b
        if not all(math.isfinite(v) for v in b) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError()
        return b
    except (ValueError, TypeError, OverflowError):
        raise ApiError('invalid_bbox', 'Use finite west < east and south < north within longitude/latitude limits; split antimeridian searches.', status=400) from None


class NisarClient:
    def __init__(self, transport, cache_dir: Path):
        self.transport = transport
        self.cache_dir = Path(cache_dir).resolve() / 'nisar-client-v1'
        for folder in ('search', 'products', 'previews'):
            (self.cache_dir / folder).mkdir(parents=True, exist_ok=True)

    def _path(self, category, identity, suffix='.json'):
        key = hashlib.sha256(identity.encode('utf-8')).hexdigest()
        return self.cache_dir / category / (key + suffix)

    @staticmethod
    def _read(path):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return None

    def _write(self, path, content):
        if not isinstance(content, bytes):
            content = json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.writing-', delete=False) as f:
                temporary = Path(f.name)
                f.write(content)
            os.replace(temporary, path)
            self._prune_cache()
        finally:
            if temporary and temporary.exists():
                temporary.unlink(missing_ok=True)

    def _prune_cache(self):
        # Soft LRU by last write/access. Only this client's generated cache files
        # are eligible; arbitrary user files within the cache folder are ignored.
        candidates = []
        for folder in ('search', 'products', 'previews'):
            for path in (self.cache_dir / folder).iterdir():
                if re.fullmatch(r'[a-f0-9]{64}\.(json|bin)', path.name) and path.is_file() and not path.is_symlink():
                    try:
                        stat = path.stat()
                        candidates.append((stat.st_mtime, stat.st_size, path))
                    except FileNotFoundError:
                        pass
        total = sum(n for _, n, _ in candidates)
        for _, n, path in sorted(candidates):
            if total <= MAX_CACHE_BYTES:
                break
            try:
                path.unlink()
                total -= n
            except FileNotFoundError:
                pass

    def _cached(self, path):
        cached = self._read(path)
        if not isinstance(cached, dict):
            return None
        timestamp=cached.get('cached_at_epoch')
        if isinstance(timestamp, bool) or not isinstance(timestamp,(int,float)) or not math.isfinite(timestamp) or time.time()-timestamp > CACHE_TTL_SECONDS:
            return None
        return cached

    def _product(self, feature):
        if not isinstance(feature, dict) or not isinstance(feature.get('properties'), dict):
            raise ApiError('invalid_upstream_response', 'ASF returned malformed product metadata.', status=502)
        p = feature['properties']
        product_id = p.get('fileID') or p.get('sceneName') or feature.get('id')
        if not isinstance(product_id, str) or not _PRODUCT_ID.fullmatch(product_id):
            raise ApiError('invalid_upstream_response', 'ASF returned a non-NISAR or invalid product identifier.', status=502)
        product_type = str(p.get('processingLevel', '')).upper()
        if product_type not in PRODUCT_TYPES:
            match = re.search(r'_(GCOV|GUNW|GOFF|SME2|GSLC|RSLC)_', product_id)
            product_type = match[1] if match else None
        if product_type not in PRODUCT_TYPES:
            raise ApiError('invalid_upstream_response', 'ASF returned an unsupported NISAR product type.', status=502)
        science_url = _stable_url(p.get('url'))
        sizes = p.get('bytes') if isinstance(p.get('bytes'), dict) else {}
        science_name = unquote(urlsplit(science_url).path.rsplit('/', 1)[-1]) if science_url else ''
        science_size = _size(sizes.get(science_name)) if sizes else _size(p.get('bytes'))
        browse = p.get('browse', [])
        if isinstance(browse, str):
            browse = [browse]
        if not isinstance(browse, list):
            browse = []
        preview_urls = []
        for candidate in browse:
            if isinstance(candidate, dict):
                candidate = candidate.get('href') or candidate.get('url')
            stable = _stable_url(candidate, reject_query=True)
            if stable and urlsplit(stable).path.lower().endswith(('.png', '.jpg', '.jpeg')) and stable not in preview_urls:
                preview_urls.append(stable)
        # Stable deterministic preference gives small provider thumbnails first;
        # each index still has its own byte endpoint and source URL.
        preview_urls.sort(key=lambda u: (0 if 'thumbnail' in urlsplit(u).path.lower() else 1, u))
        previews = []
        for index, url in enumerate(preview_urls):
            filename = unquote(urlsplit(url).path.rsplit('/', 1)[-1])
            previews.append({'index': index, 'source_url': url,
                'url': f'/api/products/{quote(product_id, safe="")}/previews/{index}',
                'kind': 'provider_browse', 'scientific': False,
                'size_bytes': _size(sizes.get(filename)),
                'media_type': 'image/png' if filename.lower().endswith('.png') else 'image/jpeg',
                'variant': 'thumbnail' if 'thumbnail' in filename.lower() else 'browse',
                'label': filename})
        concept = p.get('conceptID')
        collection_concept = concept if isinstance(concept, str) and _CONCEPT_COLLECTION.fullmatch(concept) else p.get('collectionConceptID')
        granule_concept = concept if isinstance(concept, str) and _CONCEPT_GRANULE.fullmatch(concept) else p.get('granuleConceptID')
        collection_concept = collection_concept if isinstance(collection_concept, str) and _CONCEPT_COLLECTION.fullmatch(collection_concept) else None
        granule_concept = granule_concept if isinstance(granule_concept, str) and _CONCEPT_GRANULE.fullmatch(granule_concept) else None
        safe_keys = ('fileID', 'sceneName', 'platform', 'sensor', 'collectionName', 'conceptID', 'groupID', 'processingLevel',
            'startTime', 'stopTime', 'pgeVersion', 'crid', 'productionConfiguration', 'processingDate', 'mainBandPolarization',
            'sideBandPolarization', 'polarization', 'beamModeType', 'pathNumber', 'frameNumber', 'orbit', 'orbitType',
            'flightDirection', 'frameCoverage', 'jointObservation', 'rangeBandwidth', 'granuleType')
        provider_metadata = {key: p[key] for key in safe_keys if key in p}
        return {'id': product_id, 'product_type': product_type, 'collection': p.get('collectionName'),
            'collection_concept_id': collection_concept, 'granule_concept_id': granule_concept,
            'acquisition_start': p.get('startTime'), 'acquisition_end': p.get('stopTime'), 'geometry': feature.get('geometry'),
            'platform': p.get('platform', 'NISAR'), 'processing_version': p.get('pgeVersion'),
            'production_configuration': p.get('productionConfiguration'), 'complex_scientific_product': product_type in {'GSLC', 'RSLC'},
            'metadata_only': True, 'science_asset': {'url': science_url, 'size_bytes': science_size,
                'requires_earthdata': True, 'downloaded': False}, 'previews': previews,
            'preview_unavailable_reason': None if previews else 'ASF advertised no stable public PNG/JPEG browse URL for this product; no substitute image was generated.',
            'provider_metadata': provider_metadata,
            'provenance': {'provider': 'NASA ASF DAAC', 'catalogue_endpoint': ASF_SEARCH_URL, 'fetched_at': _utc_now(),
                'preview_note': 'Provider browse/quicklook only; not a scientific raster, calibrated pixel export or independently validated spatial subset.'}}

    def _features(self, response):
        if not isinstance(response, dict) or response.get('type') != 'FeatureCollection' or not isinstance(response.get('features'), list):
            raise ApiError('invalid_upstream_response', 'ASF did not return a GeoJSON FeatureCollection.', status=502)
        return response['features']

    def search(self, bbox: list[float], start: str | None = None, end: str | None = None,
               product='GCOV', limit=5, previews='links', *, track=None, frame=None) -> dict:
        bbox = _validate_bbox(bbox)
        start, end = _parse_date(start), _parse_date(end, end=True)
        if start and end and datetime.fromisoformat(start.replace('Z', '+00:00')) > datetime.fromisoformat(end.replace('Z', '+00:00')):
            raise ApiError('invalid_date_range', 'start must not be later than end.', status=400)
        if not isinstance(product, str) or product.strip().upper() not in PRODUCT_TYPES:
            raise ApiError('invalid_product_type', 'Choose GCOV, GUNW, GOFF, SME2, GSLC or RSLC.', status=400)
        product = product.strip().upper()
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_RESULTS:
            raise ApiError('invalid_limit', f'limit must be an integer between 1 and {MAX_RESULTS}.', status=400)
        if not isinstance(previews,str) or previews not in {'none', 'links', 'inline'}:
            raise ApiError('invalid_preview_mode', 'previews must be none, links or inline.', status=400)
        params = {'platform': 'NISAR', 'processingLevel': product, 'bbox': ','.join(format(v, '.12g') for v in bbox),
            'maxResults': limit, 'output': 'geojson'}
        for label, keyword, value, maximum in (
            ('track', 'relativeOrbit', track, 173), ('frame', 'frame', frame, 176),
        ):
            if value is not None:
                if type(value) is not int or not 1 <= value <= maximum:
                    raise ApiError('invalid_' + label, f'NISAR {label} must be an integer between 1 and {maximum}.')
                params[keyword] = value
        if start: params['start'] = start
        if end: params['end'] = end
        identity = json.dumps(params, sort_keys=True, separators=(',', ':'))
        path = self._path('search', identity)
        cached = self._cached(path)
        if cached is not None:
            items = copy.deepcopy(cached['items']);fetched_at = cached['fetched_at']
        else:
            response = self.transport.get_json(ASF_SEARCH_URL, params=params, cap=8_000_000, purpose='Public NISAR catalogue search')
            items = [];seen = set()
            for feature in self._features(response)[:limit]:
                item = self._product(feature)
                if item['product_type'] != product or item['id'] in seen:
                    continue
                seen.add(item['id']);items.append(item)
                self._write(self._path('products', item['id']), {'cached_at_epoch': time.time(), 'item': item})
            fetched_at = _utc_now()
            self._write(path, {'cached_at_epoch': time.time(), 'fetched_at': fetched_at, 'items': items})
        result = {'type': 'NISARSearchResult', 'items': items, 'count': len(items),
            'query': {'bbox': bbox, 'start': start, 'end': end, 'product': product, 'limit': limit, 'previews': previews}
                | {key: value for key, value in (('track', track), ('frame', frame)) if value is not None},
            'metadata_only': True, 'scientific_data_downloaded': False,
            'provenance': {'provider': 'NASA ASF DAAC', 'endpoint': ASF_SEARCH_URL, 'fetched_at': fetched_at,
                'cache_hit': cached is not None, 'cache_ttl_seconds': CACHE_TTL_SECONDS,
                'selection': 'Bounded ASF catalogue results; footprint geometry is provider metadata, not pixel-level validation.'}}
        if previews == 'none':
            for item in items:
                item['previews'] = [];item['preview_unavailable_reason'] = 'Preview links were disabled by previews=none.'
        elif previews == 'inline':
            known = sum(p['size_bytes'] or 0 for item in items for p in item['previews'])
            if known > MAX_INLINE_BYTES:
                raise ApiError('response_too_large', 'Advertised preview sizes exceed the 32 MB inline budget; use previews=links or a smaller limit.', status=413)
            total = 0
            for item in items:
                for preview in item['previews']:
                    remaining = MAX_INLINE_BYTES - total
                    if remaining <= 0:
                        raise ApiError('response_too_large', 'Inline previews exceed 32 MB; use previews=links or a smaller limit.', status=413)
                    image = self._preview_for(item, preview['index'], byte_cap=min(MAX_PREVIEW_BYTES, remaining))
                    total += len(image['content'])
                    preview.update(data_uri='data:'+image['media_type']+';base64,'+base64.b64encode(image['content']).decode('ascii'),
                        sha256=image['sha256'],size_bytes=len(image['content']),width=image['width'],height=image['height'],media_type=image['media_type'])
            result['inline_policy'] = {'all_advertised_previews': True, 'maximum_raw_image_bytes': MAX_INLINE_BYTES,
                'raw_image_bytes': total, 'base64_overhead_included_in_limit': False}
        return result

    def get_product(self, product_id: str) -> dict:
        product_id = _validate_id(product_id)
        path = self._path('products', product_id)
        cached = self._cached(path)
        if cached is not None:
            item = copy.deepcopy(cached['item']);item['provenance']['cache_hit'] = True
            return item
        response = self.transport.get_json(ASF_SEARCH_URL,
            params={'product_list': product_id, 'output': 'geojson'},
            cap=8_000_000, purpose='Resolve exact public NISAR product identifier')
        for feature in self._features(response)[:2]:
            item = self._product(feature)
            if item['id'] == product_id:
                self._write(path, {'cached_at_epoch': time.time(), 'item': item})
                item['provenance']['cache_hit'] = False
                return item
        raise ApiError('product_not_found', 'No exact matching NISAR product was returned by the public ASF catalogue.', status=404)

    @staticmethod
    def _verify_image(content):
        if not isinstance(content, bytes) or not content or len(content) > MAX_PREVIEW_BYTES:
            raise ApiError('invalid_preview', 'Provider preview is empty or exceeds the 8 MB image limit.', status=502)
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                fmt = image.format
                if fmt not in {'PNG', 'JPEG'} or width <= 0 or height <= 0 or width * height > MAX_PREVIEW_PIXELS:
                    raise ValueError('Unsupported image format or dimensions')
                if getattr(image, 'n_frames', 1) != 1:
                    raise ValueError('Animated previews are unsupported')
                image.verify()
        except Exception as exc:
            raise ApiError('invalid_preview', 'Provider response is not a verified single-frame PNG/JPEG within 40 million pixels.', status=502,
                details={'reason': type(exc).__name__}) from None
        return {'width': width, 'height': height, 'media_type': 'image/png' if fmt == 'PNG' else 'image/jpeg',
            'extension': '.png' if fmt == 'PNG' else '.jpg'}

    def _preview_for(self, item, index, *, byte_cap=MAX_PREVIEW_BYTES):
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ApiError('invalid_preview_index', 'Preview index must be a nonnegative integer.', status=400)
        if index >= len(item['previews']):
            raise ApiError('preview_not_found', 'The provider did not advertise this preview index.', status=404)
        preview = item['previews'][index]
        source_url = preview['source_url']
        if preview.get('size_bytes') is not None and preview['size_bytes'] > byte_cap:
            raise ApiError('response_too_large', 'Provider preview exceeds the image/inline byte budget; use previews=links or a smaller limit.', status=413)
        meta_path = self._path('previews', source_url)
        bytes_path = self._path('previews', source_url, '.bin')
        cached = self._read(meta_path);content = None;cache_hit = False
        if isinstance(cached, dict) and cached.get('source_url') == source_url:
            try:
                if bytes_path.stat().st_size <= byte_cap:
                    candidate = bytes_path.read_bytes()
                    if hashlib.sha256(candidate).hexdigest() == cached.get('sha256'):
                        self._verify_image(candidate);content = candidate;cache_hit = True
                        os.utime(meta_path, None);os.utime(bytes_path, None)
                elif bytes_path.stat().st_size <= MAX_PREVIEW_BYTES:
                    raise ApiError('response_too_large', 'Cached preview exceeds the remaining 32 MB inline budget; use links or a smaller limit.', status=413)
            except (OSError, ValueError):
                pass
        if content is None:
            response = self.transport.get_bytes(source_url, cap=byte_cap, purpose='Public provider NISAR browse image; non-scientific preview only')
            if response.status_code != 200:
                raise ApiError('preview_unavailable', 'Provider browse request did not return HTTP 200.', status=502)
            content = response.content
            if len(content) > byte_cap:
                raise ApiError('response_too_large', 'Provider preview exceeds the remaining byte budget; use links or a smaller limit.', status=413)
            details = self._verify_image(content)
            digest = hashlib.sha256(content).hexdigest()
            self._write(bytes_path, content)
            self._write(meta_path, {'source_url': source_url, 'sha256': digest, 'size_bytes': len(content),
                'width': details['width'], 'height': details['height'], 'media_type': details['media_type'], 'verified_at': _utc_now()})
        else:
            details = self._verify_image(content);digest = hashlib.sha256(content).hexdigest()
        return {'content': content, 'media_type': details['media_type'], 'filename': item['id']+'_preview_'+str(index)+details['extension'],
            'sha256': digest, 'source_url': source_url, 'width': details['width'], 'height': details['height'],
            'cache_hit': cache_hit, 'scientific': False, 'kind': 'provider_browse'}

    def preview(self, product_id: str, index=0) -> dict:
        return self._preview_for(self.get_product(product_id), index)
