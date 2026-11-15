"""Build local context-only basemap GeoJSON from pinned Natural Earth v5.1.2."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path

import requests
from shapely import make_valid
from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union


DATA = Path(__file__).resolve().parents[1] / 'data'
BASE = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/'
SIZES = {
    'ne_110m_land.geojson':138_160,
    'ne_110m_lakes.geojson':36_648,
    'ne_10m_land.geojson':10_157_965,
    'ne_10m_lakes.geojson':5_043_554,
    'ne_10m_lakes_europe.geojson':1_008_063,
}
DETAIL_EXTENTS = {
    'umm-al-quwain-nisar':[54.95,24.9,56.55,26.3],
    'curuai-nisar':[-56.05,-2.7,-54.8,-1.45],
    'burgas':[26.95,42.05,28.6,43.15],
    'everglades-nisar':[-81.55,24.9,-79.9,26.3],
}


def fetch(filename):
    declared = SIZES[filename]
    url = BASE+filename
    with requests.get(url, headers={'Accept-Encoding':'identity'}, timeout=(8,45), stream=True) as response:
        response.raise_for_status()
        if int(response.headers.get('Content-Length',0)) != declared:
            raise ValueError(f'{filename} differs from the verified v5.1.2 byte length')
        content = bytearray()
        for part in response.iter_content(65536):
            content.extend(part)
            if len(content)>declared:
                raise ValueError(f'{filename} exceeded its verified byte length')
    if len(content)!=declared:
        raise ValueError(f'{filename} response was incomplete')
    data=json.loads(content)
    if data.get('type')!='FeatureCollection':
        raise ValueError(f'{filename} did not contain a GeoJSON FeatureCollection')
    return filename, (data['features'], {'url':url,'bytes':declared,'sha256':sha256(content).hexdigest()})


def polygons(geometry):
    if geometry.is_empty:
        return
    if geometry.geom_type=='Polygon':
        yield geometry
    if geometry.geom_type in ('MultiPolygon','GeometryCollection'):
        for individual in geometry.geoms:
            yield from polygons(individual)


def geometry_of(feature):
    geometry=shape(feature['geometry'])
    return geometry if geometry.is_valid else make_valid(geometry)


def generate(kind, downloads, patches):
    global_id=f'ne_110m_{kind}.geojson'
    detailed_ids=[f'ne_10m_{kind}.geojson']
    if kind=='lakes':
        detailed_ids.append('ne_10m_lakes_europe.geojson')
    detail_mask=unary_union(list(patches.values()))
    features=[]

    def emit(geometry, resolution, site_id=None):
        for part in polygons(geometry):
            if not part.is_valid:
                raise ValueError(f'Invalid computed {kind} polygon')
            features.append({'type':'Feature','geometry':mapping(part),'properties':{
                'layer':kind,'source':'Natural Earth 5.1.2','scale':resolution,**({'site_id':site_id} if site_id else {})}})

    for feature in downloads[global_id][0]:
        geometry=geometry_of(feature)
        emit(geometry.difference(detail_mask), '1:110 million')
    for site_id, patch in patches.items():
        pieces=[]
        for filename in detailed_ids:
            if filename.endswith('_europe.geojson') and site_id != 'burgas':
                continue
            for feature in downloads[filename][0]:
                geometry=shape(feature['geometry'])
                if not geometry.envelope.intersects(patch):
                    continue
                if not geometry.is_valid:
                    geometry=make_valid(geometry)
                if geometry.intersects(patch):
                    pieces.extend(polygons(geometry.intersection(patch)))
        detail=unary_union(pieces).simplify(.0008, preserve_topology=True).intersection(patch)
        emit(detail,'1:10 million',site_id)
    if not features:
        raise ValueError(f'Empty {kind} basemap')
    collection={'type':'FeatureCollection','features':features,'metadata':{
        'source':'Natural Earth', 'source_version':'v5.1.2','license':'Public domain',
        'license_url':'https://www.naturalearthdata.com/about/terms-of-use/',
        'purpose':'Basemap context. Coastlines and inland water may be generalized or incomplete.',
        'processing':'Global 1:110m polygons outside the site context rectangles. Within each rectangle, clipped 1:10m polygons are simplified by 0.0008° with topology preserved.',
        'detail_extents_lonlat':DETAIL_EXTENTS,'source_data':[downloads[name][1] for name in [global_id,*detailed_ids]],
    }}
    body=json.dumps(collection, ensure_ascii=False, allow_nan=False, separators=(',',':')).encode('utf-8')+b'\n'
    if len(body)>1_000_000:
        raise ValueError(f'{kind} basemap exceeds 1 MB limit')
    return body, len(features)


def main():
    if sum(SIZES.values()) > 17_000_000:
        raise ValueError('Unexpected basemap transfer size')
    with ThreadPoolExecutor(max_workers=5) as pool:
        downloads=dict(pool.map(fetch,SIZES))
    patches={site_id:box(*extent) for site_id,extent in DETAIL_EXTENTS.items()}
    results={kind:generate(kind,downloads,patches) for kind in ('land','lakes')}
    for kind,(body,count) in results.items():
        (DATA/f'basemap-{kind}.geojson').write_bytes(body)
        print(f'{kind}: {count} valid polygons, {len(body):,} bytes')
    print(f'{sum(SIZES.values()):,} public vector response bytes')


if __name__=='__main__':
    main()
