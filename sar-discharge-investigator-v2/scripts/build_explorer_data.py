"""Rebuild the curated explorer from public catalogue and official vector records.

Run from any directory with Python 3, requests, Shapely and pyproj installed.
This retrieves only metadata and selected size-checked members of a single
official CEMS vector archive. It neither fetches SAR rasters nor uses a login.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import struct
from threading import Lock
from urllib.parse import urlsplit, urlunsplit
from zipfile import ZipFile

from pyproj import Transformer
import requests
from shapely import is_valid_reason, make_valid, transform
from shapely.geometry import MultiPolygon, box, mapping, shape
from shapely.ops import unary_union


OUT = Path(__file__).resolve().parents[1] / 'data'
ASF = 'https://api.daac.asf.alaska.edu/services/search/param'
PC = 'https://planetarycomputer.microsoft.com/api/stac/v1'
WFS = 'https://rsis.ramsar.org/geoserver/wfs'
CEMS = 'https://mapping.emergency.copernicus.eu/activations/EMSR773/'
CEMS_PREFIX = 'EMSR773_AOI01_DEL_PRODUCT'
CEMS_URL = f'https://rapidmapping.emergency.copernicus.eu/backend/EMSR773/AOI01/DEL_PRODUCT/{CEMS_PREFIX}_v1.zip'
CEMS_EXPECTED_ARCHIVE_BYTES = 23_112_708
CEMS_CREDIT = 'Contains modified Copernicus Service information 2024. Copernicus Emergency Management Service (© 2024 European Union), EMSR773 Valencia Province (AOI01), Delineation v1.'
SENTINEL_CREDIT = 'Contains modified Copernicus Sentinel data 2024. Catalogue metadata from NASA ASF DAAC and Microsoft Planetary Computer.'
SENTINEL_TERMS = 'https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice'
RSIS_TERMS = 'https://www.ramsar.org/licence-information'
PRESETS = {
    'tobago': {
        'name': 'Tobago · Gulfstream incident', 'topic': 'oil_spill',
        'bbox': [-60.9, 10.96, -60.60, 11.25], 'start': '2024-01-20', 'end': '2024-02-29',
        'orbit': 164, 'event_date': '2024-02-07', 'reference_acquisition_date': '2024-02-14',
        'evidence_id': 'tobago-ima',
        'stems': [
            'S1A_IW_GRDH_1SDV_20240121T221832_20240121T221857_052211_064FCC',
            'S1A_IW_GRDH_1SDV_20240202T221831_20240202T221856_052386_0655B8',
            'S1A_IW_GRDH_1SDV_20240214T221831_20240214T221856_052561_065B99',
            'S1A_IW_GRDH_1SDV_20240226T221831_20240226T221856_052736_066190',
        ],
    },
    'albufera': {
        'name': 'L’Albufera · October 2024 flood', 'topic': 'wetlands',
        'bbox': None, 'start': '2024-10-19', 'end': '2024-11-12',
        'orbit': 103, 'event_date': '2024-10-29', 'reference_acquisition_date': '2024-10-31',
        'evidence_id': 'ramsar-454',
        'stems': [
            'S1A_IW_GRDH_1SDV_20241019T180256_20241019T180321_056175_06E02E',
            'S1A_IW_GRDH_1SDV_20241031T180256_20241031T180321_056350_06E71E',
            'S1A_IW_GRDH_1SDV_20241112T180255_20241112T180320_056525_06EE16',
        ],
    },
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def stable_url(url):
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))


class Transfer:
    """Small shared meter for public metadata and exact CEMS archive ranges."""

    def __init__(self):
        self.bytes = 0
        self.reserved = 0
        self.entries = []
        self.lock = Lock()

    def read(self, url, *, cap, label, params=None, headers=None, expected_range=None, allow_missing=False):
        with self.lock:
            require(self.bytes + self.reserved + cap <= 16_000_000, 'Explorer bootstrap transfer budget exhausted')
            self.reserved += cap
        body = bytearray()
        try:
            with requests.get(url, params=params, headers={'Accept-Encoding': 'identity', **(headers or {})}, timeout=(8, 35), stream=True) as response:
                if response.status_code == 404 and allow_missing:
                    self.entries.append({'source_url': stable_url(url), 'label': label, 'status': 404, 'bytes': 0})
                    return None
                response.raise_for_status()
                if expected_range is not None:
                    require(response.status_code == 206, f'{label}: server did not honor Range')
                    require(re.fullmatch(expected_range, response.headers.get('Content-Range', '')), f'{label}: invalid Content-Range')
                announced = response.headers.get('Content-Length')
                require(announced is None or int(announced) <= cap, f'{label}: response exceeds byte cap')
                for chunk in response.iter_content(65536):
                    body.extend(chunk)
                    require(len(body) <= cap, f'{label}: streamed response exceeded byte cap')
                self.entries.append({'source_url': stable_url(url), 'label': label, 'status': response.status_code,
                    'bytes': len(body), 'sha256': sha256(body).hexdigest()})
                return bytes(body), response.headers, response.url
        finally:
            with self.lock:
                self.reserved -= cap
                self.bytes += len(body)

    def json(self, url, *, cap=1_000_000, **kwargs):
        payload = self.read(url, cap=cap, **kwargs)
        return json.loads(payload[0]) if payload else None


class CemsArchive(io.RawIOBase):
    """Size-check the official object and expose only small, exact Range reads."""

    def __init__(self, transfer):
        self.transfer = transfer
        self.position = 0
        self.used = 0
        _, headers, self.resolved_url = transfer.read(CEMS_URL, cap=1, label='CEMS archive length probe',
            headers={'Range': 'bytes=0-0'}, expected_range=r'bytes 0-0/\d+')
        self.length = int(headers['Content-Range'].split('/')[-1])
        require(self.length == CEMS_EXPECTED_ARCHIVE_BYTES, 'CEMS v1 archive length changed, review exact product before using it')
        require(urlsplit(self.resolved_url).hostname == 'rapidmapping.s3.amazonaws.com', 'Unexpected CEMS archive host')

    def tell(self):
        return self.position

    def seekable(self):
        return True

    def seek(self, offset, whence=0):
        self.position = (0 if whence == 0 else self.position if whence == 1 else self.length) + offset
        return self.position

    def read(self, amount=-1):
        available = self.length - self.position
        amount = min(available, amount) if amount >= 0 else available
        if amount == 0:
            return b''
        require(0 < amount <= 2_000_000 and self.used + amount <= 2_100_000, 'CEMS central directory/member range exceeds limit')
        start, end = self.position, self.position + amount - 1
        data, _, _ = self.transfer.read(self.resolved_url, cap=amount, label=f'CEMS archive bytes {start}–{end}',
            headers={'Range': f'bytes={start}-{end}'}, expected_range=fr'bytes {start}-{end}/{self.length}')
        require(len(data) == amount, 'Partial CEMS range did not have exact requested length')
        self.position += amount
        self.used += amount
        return data


def json_file(path, payload, pretty=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False,
        indent=2 if pretty else None, separators=None if pretty else (',', ':')) + '\n', encoding='utf-8')


def polygon_only(geometry):
    if geometry.geom_type in {'Polygon', 'MultiPolygon'}:
        return geometry
    if geometry.geom_type == 'GeometryCollection':
        polygons = [polygon_only(g) for g in geometry.geoms if g.geom_type in {'Polygon', 'MultiPolygon', 'GeometryCollection'}]
        return unary_union(polygons)
    return MultiPolygon()


def project(geometry, source, target):
    converter = Transformer.from_crs(source, target, always_xy=True)
    return transform(geometry, converter.transform, interleaved=False)


def rsis_boundary(transfer, site_no, provider_id, epsg, bounds_check):
    parameters = {'request':'GetFeature','service':'wfs','version':'1.0.0','typename':'ramsar_sdi:features_param_idrvis',
        'viewparams':f'idrvis:{provider_id}','outputformat':'application/json','srsName':'EPSG:4326'}
    body, _, request_url = transfer.read(WFS, params=parameters, cap=450_000, label=f'RSIS boundary {site_no}')
    document = json.loads(body)
    (feature,) = document['features']
    require(int(feature['properties']['v_idris']) == provider_id, 'Wrong RSIS provider feature ID')
    source_boundary = shape(feature['geometry'])
    require(source_boundary.geom_type in {'Polygon','MultiPolygon'} and not source_boundary.is_empty,
        f'Nonpolygonal RSIS geometry for site {site_no}')
    validity = is_valid_reason(source_boundary)
    boundary = source_boundary if source_boundary.is_valid else polygon_only(make_valid(source_boundary))
    require(boundary.is_valid and not boundary.is_empty and abs(boundary.area/source_boundary.area-1)<1e-6,
        f'RSIS geometry repair changed site {site_no} area or could not make a valid polygon')
    require(box(*bounds_check).covers(boundary), f'RSIS site {site_no} coordinates outside expected region or swapped axes')
    projected = project(boundary, 4326, epsg)
    area = projected.area / 10_000
    minimum, maximum = (19_000, 24_000) if site_no == 454 else (1_000, 2_000)
    require(minimum <= area <= maximum, f'RSIS site {site_no} area outside sanity bounds: {area:.1f} ha')
    geometry_path = f'geometry/rsis-{site_no}-{provider_id}.geojson'
    json_file(OUT / geometry_path, {'type':'Feature', 'geometry':mapping(boundary), 'properties':{
        'ramsar_site_number':site_no,'rsis_version_id':provider_id,'source_url':request_url,
        'source_sha256':sha256(body).hexdigest(),'approx_projected_area_ha':round(area,2),
        'credit':'Convention on Wetlands, Ramsar Sites Information Service', 'license':'CC BY 4.0', 'terms_url':RSIS_TERMS,
        'source_validity':validity,'topology_repair':None if source_boundary.is_valid else 'GEOS make_valid with zero-area nonpolygonal parts removed; relative area change below 0.0001%'}}, pretty=False)
    display = project(projected.simplify(15, preserve_topology=True), epsg, 4326)
    require(display.is_valid and abs(project(display,4326,epsg).area/projected.area-1) < .01, 'Boundary simplification changed area excessively')
    return boundary, display, geometry_path, AreaRecord(area, sha256(body).hexdigest(), request_url, validity)


class AreaRecord:
    def __init__(self, area, digest, url, validity):
        self.area = round(area, 2)
        self.digest = digest
        self.url = url
        self.validity = validity


def dbf_sources(data):
    """Read the small CEMS dBASE source table without loading GIS archives."""
    count, header_length, record_length = struct.unpack_from('<IHH', data, 4)
    require(count <= 100 and header_length >= 33
        and header_length + count*record_length <= len(data), 'Malformed CEMS source table')
    fields = [(data[offset:offset+11].rstrip(b'\0').decode('ascii'), data[offset+16])
        for offset in range(32,header_length-1,32)]
    require(sum(width for _,width in fields)+1==record_length, 'Unexpected CEMS source table record layout')
    for index in range(count):
        offset = header_length+index*record_length
        if data[offset:offset+1] == b'*':
            continue
        cursor = offset+1
        record = {}
        for name, width in fields:
            record[name] = data[cursor:cursor+width].decode('cp1252').strip()
            cursor += width
        yield record


def flood_geometry(transfer, boundary):
    archive = CemsArchive(transfer)
    member = f'{CEMS_PREFIX}_observedEventA_v1.json'
    image_member = f'{CEMS_PREFIX}_imageFootprintA_v1.json'
    source_member = f'{CEMS_PREFIX}_source_v1.dbf'
    with ZipFile(archive) as zip_file:
        event_info, image_info, source_info = (zip_file.getinfo(name) for name in (member,image_member,source_member))
        require(event_info.compress_size < 1_850_000 and event_info.file_size < 8_500_000, 'CEMS observed extent exceeds chosen size')
        require(image_info.compress_size < 15_000 and image_info.file_size < 30_000, 'CEMS image-footprint metadata exceeds chosen size')
        require(source_info.compress_size < 15_000 and source_info.file_size < 30_000, 'CEMS source table exceeds chosen size')
        raw_event = zip_file.read(event_info)
        raw_image = zip_file.read(image_info)
        image_sources = list(dbf_sources(zip_file.read(source_info)))
    event, image = json.loads(raw_event), json.loads(raw_image)
    require(event.get('crs', {}).get('properties', {}).get('name') == 'urn:ogc:def:crs:OGC:1.3:CRS84', 'Unexpected CEMS CRS')
    features, clipped_geometries, corrected = [], [], 0
    for index, feature in enumerate(event['features']):
        props = feature['properties']
        if props.get('notation') != 'Flooded area':
            continue
        geometry = shape(feature['geometry'])
        if not geometry.intersects(boundary):
            continue
        if not geometry.is_valid:
            geometry = polygon_only(make_valid(geometry))
            corrected += 1
        clipped = polygon_only(geometry.intersection(boundary))
        if clipped.is_empty or clipped.area == 0:
            continue
        require(clipped.is_valid, f'Invalid clipped CEMS flood feature {index}')
        clipped_geometries.append(clipped)
        props = {key:value for key,value in props.items() if key != 'area'}
        features.append({'type':'Feature','geometry':mapping(clipped),'properties':{
            **props,'source_feature_index':index,'source_layer':member,'modified_by':'intersection with RSIS Ramsar site 454 boundary'}})
    require(features, 'No published flooded area overlaps Ramsar site boundary')
    union = unary_union(clipped_geometries)
    projected = project(union, 4326, 25830)
    display = project(projected.simplify(20, preserve_topology=True), 25830, 4326).intersection(boundary)
    display = polygon_only(make_valid(display))
    require(display.is_valid and not display.is_empty and abs(project(display,4326,25830).area/projected.area-1) < .03,
        'CEMS display simplification made excessive area change')
    output_path = 'geometry/emsr773-aoi01-del-v1-observed-event-albufera.geojson'
    acquisition_ids = {int(item['properties']['or_src_id']) for item in image['features']}
    image_sources = [r for r in image_sources if int(r['src_id']) in acquisition_ids]
    require(len(image_sources)==len(acquisition_ids), 'Some CEMS image footprints lack a source record')
    used_ids = {int(f['properties']['dmg_src_id']) for f in features}
    archive_context = [{'reference_id':int(r['src_id']),'sensor':r['source_nam'],'phase':r['eventphase'],
        'acquired_at':datetime.strptime(r['src_date'],'%d/%m/%Y').date().isoformat()+r['source_tm'],
        'source_resolution':r['sensor_res'],'referenced_by_clipped_flood_features':int(r['src_id']) in used_ids} for r in image_sources]
    require(used_ids<=acquisition_ids, 'A clipped CEMS flood feature lacks an image reference')
    provenance = {'activation':'EMSR773','aoi':'AOI01 Valencia province', 'product':'DEL_PRODUCT v1','layer':'observedEventA',
        'source_url':CEMS_URL,'archive_bytes':archive.length,'retrieved_range_bytes':archive.used,
        'member':member,'member_sha256':sha256(raw_event).hexdigest(),'source_table_member':source_member,'source_acquisitions':archive_context,
        'source_feature_count':len(event['features']),'clipped_feature_count':len(features),'repaired_source_geometries':corrected,
        'clip':'Exact intersection with official RSIS site 454 polygon. Display geometry uses 20 m topology-preserving simplification; export retains clipped feature geometries.',
        'authoritative_for':'Published mapped flood extent for the CEMS product observation intervals',
        'credit':CEMS_CREDIT}
    json_file(OUT / output_path, {'type':'FeatureCollection','features':features,'provenance':provenance}, pretty=False)
    return display, output_path, provenance


def evidence(evidence_id, title, url, kind, notes, **extra):
    return {'id':evidence_id,'title':title,'url':url,'kind':kind,'notes':notes,**extra}


def scene_catalogues(transfer, site_id, site, area):
    date_from, date_until = site['start']+'T00:00:00Z', site['end']+'T23:59:59Z'
    raw = transfer.json(ASF, label=f'ASF exact-area inventory {site_id}', params={
        'platform':'Sentinel-1','processingLevel':'GRD_HD','bbox':','.join(map(str,site['bbox'])),
        'start':date_from,'end':date_until,'maxResults':65,'output':'geojson'})
    epsg = 32620 if site_id=='tobago' else 25830
    area_projected = project(area,4326,epsg)
    output, sources = [], []
    for stem in site['stems']:
        found = [f for f in raw['features'] if f['properties']['fileID'].startswith(stem+'_')]
        require(len(found)==1, f'{stem}: expected one exact ASF acquisition, found {len(found)}')
        source = found[0]
        source_props = source['properties']
        source_id = source_props['fileID']
        acquired = source_props['startTime']
        require(source_props['pathNumber']==site['orbit'] and source_props['beamModeType']=='IW'
            and source_props['flightDirection']=='ASCENDING' and source_props['polarization']=='VV+VH', f'{source_id}: unexpected ASF acquisition properties')
        pc_id = stem+'_rtc'
        pc_url = PC+'/collections/sentinel-1-rtc/items/'+pc_id
        pc = transfer.json(pc_url, label=f'PC exact item {pc_id}', allow_missing=True)
        missing_expected = site_id=='albufera' and acquired[:10]=='2024-10-31'
        require((pc is None)==missing_expected, f'{pc_id}: availability changed; independently re-review catalogue before regenerating pinned snapshot')
        source_geometry = shape(source['geometry'])
        rtc_date, rtc_dataset, rtc_assets = None, None, {}
        if pc:
            props = pc['properties']
            require(pc['id']==pc_id and props['sat:relative_orbit']==site['orbit'] and props['sat:orbit_state']=='ascending'
                and props['sar:instrument_mode']=='IW' and set(props['sar:polarizations'])=={'VV','VH'}
                and set(pc['assets']) >= {'vv','vh'}, f'{pc_id}: unexpected RTC metadata or missing channel asset')
            geometry = shape(pc['geometry'])
            rtc_date = props['datetime']
            require(acquired[:10]==rtc_date[:10], f'{pc_id}: parent acquisition date mismatch')
            parent = props.get('s1:product_identifier')
            require(parent in {None,source_props['sceneName']}, f'{pc_id}: inconsistent RTC parent ID')
            rtc_dataset = 'sentinel-1-rtc'
            rtc_assets = {k:{'media_type':pc['assets'][k]['type'],'source_url':stable_url(pc['assets'][k]['href'])} for k in ('vv','vh')}
        else:
            geometry = source_geometry
        require(geometry.is_valid and source_geometry.is_valid, f'{stem}: invalid catalogue geometry')
        coverage = 100*area_projected.intersection(project(geometry,4326,epsg)).area/area_projected.area
        asf_coverage = 100*area_projected.intersection(project(source_geometry,4326,epsg)).area/area_projected.area
        require(coverage>=95 and asf_coverage>=95, f'{stem}: incomplete study-area catalogue footprint: {coverage:.3f}% RTC/ASF, {asf_coverage:.3f}% ASF')
        row_id = pc_id if pc else source_id
        _, _, _, _, _, _, absolute_orbit, datatake = stem.split('_')
        acquisition_id = f'sentinel-1a-{absolute_orbit}-{datatake.lower()}'
        record_id = f'{site_id}-sentinel1-{acquired[:10]}'
        asf_link = ASF+'?granule_list='+source_id+'&output=geojson'
        auth_source = pc_url if pc else asf_link
        asset_notes = 'RTC COG channel advertised by the pinned STAC item. Raster values and valid-pixel coverage were not inspected.' if pc else 'The raw GRD catalogue advertises this channel. Matching PC RTC returned HTTP 404; no analysis asset or preview is supplied.'
        sources.append(evidence(record_id,'Sentinel‑1A '+acquired[:10]+' · '+('PC RTC and ASF GRD catalogues' if pc else 'ASF GRD catalogue'),
            auth_source,'catalogue',asset_notes,related_urls={'asf_grd':asf_link,'rtc_exact_item_check':pc_url},credit=SENTINEL_CREDIT,terms_url=SENTINEL_TERMS))
        output.append({'id':row_id,'acquisition_id':acquisition_id,'site_id':site_id,'provider':'Microsoft Planetary Computer' if pc else 'NASA ASF DAAC',
            'product':'Sentinel-1 RTC' if pc else 'Sentinel-1 GRD HD','sensor':'Sentinel-1A','wavelength':'C-band (~5.55 cm)',
            'wavelength_cm':5.55,'acquired_at':acquired,'acquisition_end':source_props['stopTime'],'rtc_datetime':rtc_date,
            'orbit':site['orbit'],'direction':'ascending','mode':'IW','geometry':mapping(geometry),
            'footprint_coverage':round(coverage,3),'footprint_coverage_units':'percent of study geometry covered by catalogue footprint',
            'valid_pixel_coverage':None,'evidence_id':record_id,'source_grd_id':source_id,'rtc_id':pc_id if pc else None,
            'rtc_collection':rtc_dataset,'rtc_status':'advertised' if pc else 'unavailable','measurement_status':'unavailable',
            'event_phase': 'before' if acquired[:10] < site['event_date'] else 'event_window' if acquired[:10]==site['reference_acquisition_date'] else 'after',
            'assets':[], 'measurements':[],
            'channels':{pol:{'status':'advertised' if pol in ('VV','VH') else 'unavailable','evidence_id':record_id,
                'notes':asset_notes if pol in ('VV','VH') else 'The provider acquisition mode lists VV and VH only.',
                **({'source_url':rtc_assets[pol.lower()]['source_url'], 'media_type':rtc_assets[pol.lower()]['media_type']} if pol.lower() in rtc_assets else {})}
                for pol in ('VV','VH','HH','HV')}})
    return output, sources


def main():
    transfer = Transfer()
    local = {k:{**v} for k,v in PRESETS.items()}
    boundary, shown, boundary_path, checked = rsis_boundary(transfer,454,51615175,25830,[-.6,39,-.1,39.6])
    srebarna, srebarna_shown, srebarna_path, bg_checked = rsis_boundary(transfer,64,29655018,32635,[26.8,43.9,27.4,44.4])
    a = local['albufera']; a.update(bbox=list(boundary.bounds),geometry=mapping(shown),geometry_kind='official_boundary',
        geometry_path=boundary_path,geometry_note='Official RSIS polygon with topological repair and 15 m display simplification. Detailed repaired polygon is linked.',
        event_evidence_id='emsr773-observed-flood')
    t = local['tobago']; t.update(geometry=mapping(box(*t['bbox'])), geometry_kind='study_area',
        geometry_note='Local study rectangle around the documented southern Tobago incident, not an observed slick extent.',
        event_geometry=None,event_geometry_kind=None,event_evidence_id='tobago-ima')
    sites = [{**v,'id':k} for k,v in local.items()]
    sites.append({'id':'srebarna','name':'Srebarna · flood and drought context','topic':'wetlands','bbox':list(srebarna.bounds),
        'start':'2026-08-18','end':'2026-11-15','evidence_id':'ramsar-64','geometry':mapping(srebarna_shown),
        'geometry_kind':'official_boundary','geometry_path':srebarna_path,'geometry_note':'Official RSIS polygon with topological repair and 15 m display simplification.',
        'measurement_status':'unavailable','notes':'The official boundary is available. No local hydrological series or drought classification is part of this snapshot.'})
    with ThreadPoolExecutor(max_workers=3) as pool:
        flood = pool.submit(flood_geometry,transfer,boundary)
        tobs = pool.submit(scene_catalogues,transfer,'tobago',t,box(*t['bbox']))
        aobs = pool.submit(scene_catalogues,transfer,'albufera',a,boundary)
        display, event_path, flood_meta = flood.result()
        tobago, t_sources = tobs.result()
        albufera, a_sources = aobs.result()
    next(x for x in sites if x['id']=='albufera').update(event_geometry=mapping(display),event_geometry_kind='observed_flood_extent',
        event_geometry_path=event_path,event_geometry_note='CEMS AOI01 delineation combines imagery from 30–31 October. Clipped to the Ramsar boundary and simplified by 20 m for display.',
        event_geometry_credit=CEMS_CREDIT)
    sources = [
        evidence('ramsar-454','Ramsar Sites Information Service · L’Albufera (454)','https://rsis.ramsar.org/ris/454','official_boundary',
            'Official GIS boundary. Approximate projected area is a sanity check, not a new legal site area.',data_url=checked.url,source_sha256=checked.digest,
            inventory_id=51615175,geometry_path=boundary_path,approx_projected_area_ha=checked.area,source_validity=checked.validity,credit='Convention on Wetlands, Ramsar Sites Information Service; Spain.',license='CC BY 4.0',terms_url=RSIS_TERMS),
        evidence('ramsar-64','Ramsar Sites Information Service · Srebarna (64)','https://rsis.ramsar.org/ris/64','official_boundary',
            'Official GIS boundary. The polygon indicates protected-site extent only.',data_url=bg_checked.url,source_sha256=bg_checked.digest,
            inventory_id=29655018,geometry_path=srebarna_path,approx_projected_area_ha=bg_checked.area,source_validity=bg_checked.validity,credit='Convention on Wetlands, Ramsar Sites Information Service; Bulgaria.',license='CC BY 4.0',terms_url=RSIS_TERMS),
        evidence('emsr773-observed-flood','Copernicus EMSR773 · AOI01 Delineation v1 observed flood',CEMS,'published_vector',
            'Published flooded-area features intersected with official Albufera boundary. This is a satellite-derived mapping product with its own imagery dates and coverage.',
            **flood_meta,terms_url='https://mapping.emergency.copernicus.eu/about/citation-guidelines/',geometry_path=event_path),
        evidence('tobago-ima','Trinidad and Tobago Institute of Marine Affairs · Gulfstream field response','https://www.ima.gov.tt/ima-assesses-tobagos-spill-affected-areas/',
            'official_incident_report','Government marine institute reports 7 February 2024 Cove Bay incident and visits to affected shoreline. The explorer rectangle is a study area.'),
        evidence('tobago-esa','ESA · Gulfstream Sentinel‑1 observation on 14 February 2024','https://www.esa.int/ESA_Multimedia/Images/2024/02/Tobago_oil_spill',
            'official_event_imagery','ESA identifies a Sentinel‑1 observation at 22:18 UTC. It documents the event region and does not supply a slick-extent polygon for this explorer.'),
        evidence('albufera-esa','ESA · Sentinel‑1 observations of October 2024 Albufera flooding','https://www.esa.int/sv/articles/about-us/directorates/earth-observation-programmes/copernicus/sentinel-1/10-ways-sentinel-1-data-lets-us-see-our-world',
            'official_event_imagery','ESA identifies Sentinel‑1 dates 19 and 31 October 2024. Its comparison covers a wider area and is independent of the explorer RTC availability check.'),
        evidence('srebarna-clms','CLMS 100 m monthly water-bodies product','https://land.copernicus.eu/en/products/water-bodies/water-bodies-global-v1-0-100m',
            'product_documentation','Potential monthly inland water-extent record from 2020 onward. No local series was calculated for this collection.'),
    ]
    existing = [
        ('maracaibo','Lake Maracaibo, Venezuela',[-71.85,9.60,-71.10,10.65],'NASA Earth Observatory · Troubled Waters','https://science.nasa.gov/earth/earth-observatory/troubled-waters-148894/'),
        ('galveston','Houston Ship Channel – Galveston Bay, USA',[-95.30,29.30,-94.70,29.80],'NOAA · ITC Tank Fire','https://darrp.noaa.gov/node/1668'),
        ('bonny','Bonny River estuary, Niger Delta, Nigeria',[6.95,4.35,7.30,4.80],'UNEP · Environmental Assessment of Ogoniland','https://www.unep.org/resources/assessment/environmental-assessment-ogoniland-site-factsheets-executive-summary-and-full'),
    ]
    for ident,name,bbox,title,url in existing:
        identifier = f'{ident}-context'
        sources.append(evidence(identifier,title,url,'historical_context','Primary source for historical context. The inherited discovery rectangle does not represent a documented spill extent.'))
        sites.append({'id':ident,'name':name,'topic':'oil_spill','bbox':bbox,'start':'2026-07-18','end':'2026-11-15','evidence_id':identifier,
            'geometry':mapping(box(*bbox)),'geometry_kind':'study_area','measurement_status':'unavailable',
            'notes':'Inherited discovery area and local catalogue query default. No curated acquisitions or event vector are included for this site.'})
    observations = tobago+albufera
    require(len(observations)==7 and len({o['id'] for o in observations})==7, 'Seven unique curated acquisitions required')
    require(len({o['acquisition_id'] for o in observations})==7, 'Seven distinct Sentinel-1 datatakes required')
    for site in sites:
        site.pop('stems',None)
    all_sources = sources+t_sources+a_sources
    source_ids = {e['id'] for e in all_sources}
    require(len(source_ids)==len(all_sources) and all(o['evidence_id'] in source_ids for o in observations), 'Missing or duplicate evidence ID')
    built = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    json_file(OUT/'explorer.json', {'schema_version':1,'generated_at':built,'sites':sites,'observations':observations,'evidence':all_sources}, pretty=False)
    json_file(OUT/'explorer-validation.json', {'generated_at':built,'validated_site_geometry_count':2,'event_geometry_count':1,
        'unique_curated_acquisitions':len(observations),'rtc_advertised':sum(o['rtc_status']=='advertised' for o in observations),
        'validated_scientific_rasters':0,'minimum_catalogue_footprint_coverage_percent':min(o['footprint_coverage'] for o in observations),
        'archive_transfer':'Only exact HTTP 206 ranges for ZIP directory, observedEventA and image footprint metadata. No satellite science asset requested.',
        'network_body_bytes':transfer.bytes,'network_entries':transfer.entries,'cems_provenance':flood_meta})
    print(f'{len(sites)} sites, {len(observations)} acquisitions, {len(all_sources)} sources, {transfer.bytes:,} public response bytes')


if __name__ == '__main__':
    main()
