"""Export windows from three local NISAR GCOV files for the explorer."""

import argparse
from datetime import datetime, timezone
from hashlib import file_digest
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen

import h5py
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import array_bounds, from_bounds, from_origin
from rasterio.warp import transform, transform_bounds, transform_geom


ASF = 'https://api.daac.asf.alaska.edu/services/search/param'
ROOT = 'science/LSAR/GCOV'
GRID = f'{ROOT}/grids/frequencyA'
IDENT = 'science/LSAR/identification'
WEB = 'EPSG:3857'
WGS84 = 'EPSG:4326'
POSTING = 20
SIDE = 750
RANGES = {'HH': (-26, -8), 'HV': (-33, -16)}
UAE_GRANULE = 'NISAR_L2_PR_GCOV_033_128_A_015_2005_DHDH_M_20261021T013516_20261021T013551_P05023_N_F_J_001'
AUG_GRANULE = 'NISAR_L2_PR_GCOV_028_003_A_001_2005_DHDH_A_20260813T092154_20260813T092229_P05023_N_F_J_001'
OCT_GRANULE = 'NISAR_L2_PR_GCOV_033_003_A_001_2005_DHDH_A_20261012T092154_20261012T092229_P05023_N_F_J_001'
SCENES = (
    ('nisar-uae-20261021', 'umm-al-quwain-nisar', UAE_GRANULE, 32640, (369440, 2832060)),
    ('nisar-curuai-20260813', 'curuai-nisar', AUG_GRANULE, 32721, (669000, 9772000)),
    ('nisar-curuai-20261012', 'curuai-nisar', OCT_GRANULE, 32721, (669000, 9772000)),
)


def digest(path):
    with path.open('rb') as handle:
        return file_digest(handle, 'sha256').hexdigest()


def provider_metadata(granule, source):
    catalogue = f'{ASF}?'+urlencode({'platform':'NISAR','granule_list':granule,'output':'geojson'})
    with urlopen(catalogue,timeout=12) as response:
        payload = response.read(100_001)
    if len(payload)>100_000:
        raise ValueError('Provider catalogue response exceeded its metadata cap')
    features = json.loads(payload)['features']
    matches = [item for item in features if item['properties']['fileID']==granule]
    if len(matches)!=1:
        raise ValueError(f'Expected exactly one official entry for {granule}')
    record = matches[0]
    props = record['properties']
    advertised_size = props['bytes'][source.name]['bytes']
    uri = urlsplit(props['url'])
    if props['platform']!='NISAR' or props['processingLevel']!='GCOV' or props['sensor']!='L-SAR' \
            or uri.scheme!='https' or uri.netloc!='nisar.asf.earthdatacloud.nasa.gov' or uri.query or uri.fragment \
            or uri.path.rsplit('/',1)[-1]!=source.name or advertised_size!=source.stat().st_size:
        raise ValueError(f'Provider file identity or byte count did not match {source.name}')
    if not record.get('geometry') or record['geometry']['type'] not in {'Polygon','MultiPolygon'}:
        raise ValueError('Missing provider scene footprint')
    return catalogue, record['geometry'], props


def read_window(source, granule, epsg, centre):
    with h5py.File(source, 'r') as hdf:
        ident = hdf[IDENT]
        grid = hdf[GRID]
        params = hdf[f'{ROOT}/metadata/processingInformation/parameters']
        norm = params['rtc/outputBackscatterNormalizationConvention'][()].decode().lower()
        noise = params['noiseCorrectionApplied'][()].decode().lower()
        if ident['granuleId'][()].decode()!=granule or ident['radarBand'][()].decode()!='L' \
                or int(grid['projection'][()])!=epsg or norm!='gamma0' or noise not in {'true','false'}:
            raise ValueError('Unexpected HDF5 source identity, CRS or radiometry')
        half = SIDE//2
        x,y = grid['xCoordinates'][:],grid['yCoordinates'][:]
        col = int(np.argmin(np.abs(x-centre[0])))-half
        row = int(np.argmin(np.abs(y-centre[1])))-half
        cols,rows = slice(col,col+SIDE),slice(row,row+SIDE)
        if row<0 or col<0 or row+SIDE>len(y) or col+SIDE>len(x) \
                or not np.all(np.diff(x[cols])==POSTING) or not np.all(np.diff(y[rows])==-POSTING):
            raise ValueError('Crop must fit entirely on a regular 20 m source grid')
        affine = from_origin(x[col]-POSTING/2,y[row]+POSTING/2,POSTING,POSTING)
        mask,exception,looks = (grid[key][rows,cols] for key in ('mask','inputDataExceptionMask','numberOfLooks'))
        if exception.dtype!=np.uint8 or not np.isin(exception,(0,2)).all():
            raise ValueError('Unexpected input exception mask encoding for these product versions')
        valid = ((mask>=1)&(mask<=grid['numberOfSubSwaths'][()])&(exception==0)&np.isfinite(looks)&(looks>0))
        arrays = {}
        for pol in RANGES:
            raster = grid[pol*2]
            if raster.dtype!=np.dtype('float32') or raster.attrs['units'].decode()!='1':
                raise ValueError('Unrecognized covariance storage or unit')
            power = raster[rows,cols]
            arrays[pol] = np.where(valid&np.isfinite(power)&(power>=0),power,np.float32(np.nan))
        meta = {'orbit':int(ident['trackNumber'][()]),'absolute_orbit':int(ident['absoluteOrbitNumber'][()]),
            'frame':int(ident['frameNumber'][()]),'look_direction':ident['lookDirection'][()].decode(),
            'direction':ident['orbitPassDirection'][()].decode().upper(),
            'acquired_at':ident['zeroDopplerStartTime'][()].decode().split('.')[0]+'Z',
            'acquisition_end':ident['zeroDopplerEndTime'][()].decode()+'Z',
            'processing_version':ident['productVersion'][()].decode(),
            'product_specification_version':ident['productSpecificationVersion'][()].decode(),
            'noise_correction_applied':noise=='true','source_window':[col,row,SIDE,SIDE]}
    return arrays,affine,meta


def confirm_qgis_tiff(path, data, affine, source_granule):
    """Compare HH to the pre-existing approved crop without copying its TIFF."""
    with rasterio.open(path) as old:
        if old.tags()['SOURCE_GRANULE']!=source_granule or old.crs.to_epsg()!=32640 or old.res!=(20,20):
            raise ValueError('Unexpected UAE reference TIFF identity')
        x,y = affine*(.5,.5)
        row,col = old.index(x,y)
        window = rasterio.windows.Window(col,row,SIDE,SIDE)
        if old.window_transform(window)!=affine:
            raise ValueError('The UAE crop does not exactly match the archived grid')
        compare = old.read(1,window=window)
    with np.errstate(divide='ignore',invalid='ignore'):
        db = np.where(data>0,10*np.log10(data),np.nan)
    if not np.allclose(compare,db,rtol=0,atol=2e-5,equal_nan=True):
        raise ValueError('The UAE raw-HDF5 HH crop differs from the archived QGIS HH values')
    return {'reference':path.name,'compared_postings':int(np.isfinite(compare).sum()),'maximum_db_difference':float(np.nanmax(np.abs(compare-db)))}


def make_assets(data_dir, identifier, granule, epsg, affine, pol, power, meta):
    crs = f'EPSG:{epsg}'
    dataset_path = f'/{GRID}/{pol*2}'
    name = f'{identifier}-{pol.lower()}'
    science = data_dir/'science'/f'{name}.tif'
    image = data_dir/'sar'/f'{name}.png'
    height,width = power.shape
    with rasterio.open(science,'w',driver='GTiff',width=width,height=height,count=1,dtype='float32',crs=crs,
                       transform=affine,nodata=np.nan,compress='deflate',predictor=3,tiled=True) as dst:
        dst.write(power,1)
        dst.set_band_description(1,f'NISAR {pol} measured gamma0 power')
        dst.set_band_unit(1,'1')
        dst.update_tags(SOURCE_GRANULE=granule,SOURCE_DATASET=dataset_path,FREQUENCY='A',POLARIZATION=pol,
                        NOISE_CORRECTION_APPLIED=str(meta['noise_correction_applied']).lower(),
                        QUALITY_MASK='Valid subswath; zero input anomaly; positive looks; finite nonnegative power')
    bounds = array_bounds(height,width,affine)
    wm_bounds = transform_bounds(crs,WEB,*bounds,densify_pts=21)
    west,south,east,north = wm_bounds
    resolution = max((east-west)/width,(north-south)/height)
    out_w,out_h = int(np.ceil((east-west)/resolution)),int(np.ceil((north-south)/resolution))
    web_affine = from_bounds(*wm_bounds,out_w,out_h)
    web_power = np.full((out_h,out_w),np.nan,dtype=np.float32)
    # Inverse-map each target pixel centre exactly so subpixel warp approximation
    # cannot extend coverage by a native posting at the edge of this small crop.
    for first in range(0,out_h,128):
        last = min(first+128,out_h)
        rows,cols = np.indices((last-first,out_w))
        x,y = web_affine*(cols.ravel()+.5,rows.ravel()+first+.5)
        src_x,src_y = transform(WEB,crs,x.tolist(),y.tolist())
        sc,sr = (~affine)*(np.asarray(src_x),np.asarray(src_y))
        inside = (sc>=0)&(sr>=0)&(sc<width)&(sr<height)
        block = web_power[first:last].ravel()
        block[inside] = power[np.floor(sr[inside]).astype(int),np.floor(sc[inside]).astype(int)]
    measured = np.isfinite(web_power)&(web_power>0)
    display = np.zeros((out_h,out_w,2),dtype=np.uint8)
    lo,hi = RANGES[pol]
    display[measured,0] = np.rint(np.clip((10*np.log10(web_power[measured])-lo)/(hi-lo),0,1)*255).astype(np.uint8)
    display[measured,1] = 255
    Image.fromarray(display).save(image,optimize=True)
    with rasterio.open(science) as reopened:
        if reopened.transform!=affine or reopened.crs.to_epsg()!=epsg or reopened.units!=('1',) \
                or not np.array_equal(reopened.read(1),power,equal_nan=True):
            raise RuntimeError('Written science crop differs from its native power source')
    with Image.open(image) as reopened:
        if not np.array_equal(np.asarray(reopened),display):
            raise RuntimeError('Written PNG differs from the warped display pixels')
    sample_row,sample_col = np.where(measured)
    if not len(sample_row):
        raise ValueError('This local product has no measured samples in the crop')
    # Check independent inverse coordinates against nearest source pixel centres.
    example = np.linspace(0,len(sample_row)-1,27,dtype=int)
    rr,cc = sample_row[example],sample_col[example]
    x,y = web_affine*(cc+.5,rr+.5)
    src_x,src_y = transform(WEB,crs,x.tolist(),y.tolist())
    sc,sr = (~affine)*(np.asarray(src_x),np.asarray(src_y))
    if not np.array_equal(web_power[rr,cc],power[np.floor(sr).astype(int),np.floor(sc).astype(int)]):
        raise RuntimeError('Warped sample values failed the geographic pixel-centre check')
    swne = transform_bounds(WEB,WGS84,*array_bounds(out_h,out_w,web_affine))
    finite = np.isfinite(power)
    return {'status':'validated','term':pol*2,'dataset_path':dataset_path,
        'image_url':f'/sar/{image.name}','image_bounds':[[swne[1],swne[0]],[swne[3],swne[2]]],
        'image_epsg':3857,'image_shape':[out_h,out_w],'image_transform':list(web_affine)[:6],
        'image_sha256':digest(image),'image_size_bytes':image.stat().st_size,'display_range_db':[lo,hi],
        'units':'dB','science_url':f'/science/{science.name}','science_units':'unitless gamma0 power',
        'science_epsg':epsg,'science_posting_m':POSTING,'science_shape':[height,width],
        'science_transform':list(affine)[:6],'science_sha256':digest(science),
        'science_size_bytes':science.stat().st_size,'valid_fraction':float(finite.mean()),
        'valid_pixels':int(finite.sum()),'geographic_pixel_checks':len(example),
        'noise_correction_applied':meta['noise_correction_applied'],
        'notes':'Measured GCOV gamma0 power. The map displays a fixed 10 log10 dB stretch. The native GeoTIFF preserves linear power and source validity.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--downloads',type=Path,required=True)
    parser.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[1]/'data')
    parser.add_argument('--uae-qgis',type=Path,required=True)
    args = parser.parse_args()
    for sub in ('sar','science'):
        (args.data/sub).mkdir(parents=True,exist_ok=True)
    manifest = {'default_selection':{'site_id':'umm-al-quwain-nisar','observation_id':'nisar-uae-20261021','polarization':'HH'},
                'sites':[],'observations':[],'evidence':[]}
    sites = {}
    for identifier,site_id,granule,epsg,centre in SCENES:
        source = args.downloads/f'{granule}.h5'
        catalogue,provider_geometry,provider = provider_metadata(granule,source)
        arrays,affine,meta = read_window(source,granule,epsg,centre)
        if (meta['acquired_at'],meta['orbit'],meta['frame'],meta['direction'])!= \
                (provider['startTime'],provider['pathNumber'],provider['frameNumber'],provider['flightDirection']):
            raise ValueError('Embedded acquisition metadata does not match the ASF catalogue')
        verification = confirm_qgis_tiff(args.uae_qgis,arrays['HH'],affine,granule) if granule==UAE_GRANULE else None
        evidence_id = identifier+'-source'
        channels = {pol:{**make_assets(args.data,identifier,granule,epsg,affine,pol,values,meta),'evidence_id':evidence_id}
                    for pol,values in arrays.items()}
        print(identifier,[(pol,asset['image_url'],asset['image_bounds'],asset['valid_fraction']) for pol,asset in channels.items()],flush=True)
        crs = f'EPSG:{epsg}'
        bounds = array_bounds(SIDE,SIDE,affine)
        w,s,e,n = bounds
        geometry = transform_geom(crs,WGS84,{'type':'Polygon','coordinates':[[(w,s),(e,s),(e,n),(w,n),(w,s)]]})
        bbox = list(transform_bounds(crs,WGS84,*bounds))
        date = meta['acquired_at'][:10]
        if site_id not in sites:
            aliases = ['Umm Al Quwain', 'UAE', 'Khor Al Beidah'] if granule == UAE_GRANULE else [
                'Lago Grande de Curuai', 'Curuai', 'Curuaí', 'Amazon']
            sites[site_id] = {'id':site_id,'name':'Umm Al Quwain, UAE' if granule==UAE_GRANULE else 'Lago Grande de Curuai, Brazil',
                'aliases':aliases, 'bbox':bbox,'start':date,'end':date,'topic':'wetlands','evidence_id':evidence_id}
        sites[site_id]['end'] = max(sites[site_id]['end'],date)
        manifest['observations'].append({'id':identifier,'site_id':site_id,'product_id':granule,'acquisition_id':granule,
            'mission':'nisar','provider':'NASA ASF DAAC','product':'NISAR GCOV','sensor':'L-SAR','wavelength':'L',
            'mode':'Frequency A · dual polarization','frequency':'A','geometry':geometry,'source_geometry':provider_geometry,
            'geometry_kind':'local_coverage_window','channels':channels,'evidence_id':evidence_id,
            'source_url':catalogue,'science_requires_earthdata':False,'source_requires_earthdata':True,
            **meta})
        manifest['evidence'].append({'id':evidence_id,'title':f'ASF NISAR L2 GCOV scientific download · {date}',
            'url':catalogue,'kind':'downloaded_scientific_hdf5','source_id':granule,
            'accessed_at':datetime.now(timezone.utc).date().isoformat(),'provider_asset_url':provider['url'],
            'source_filename':source.name,'source_size_bytes':source.stat().st_size,
            'provider_size_bytes':provider['bytes'][source.name]['bytes'],'source_sha256':digest(source),
            'source_authentication':'NASA Earthdata Login required for original HDF5',
            'documentation_url':'https://nisar-docs.asf.alaska.edu/gcov/',
            'qgis_reference_check':verification,
            'notes':'Existing local HDF5 matches ASF product ID, acquisition metadata and byte count. The local crop uses measured frequency-A HH and HV covariance power.'})
    manifest['sites'] = list(sites.values())
    output = args.data/'non-sentinel-nisar.json'
    total_bytes = sum(asset['image_size_bytes']+asset['science_size_bytes'] for row in manifest['observations'] for asset in row['channels'].values())
    if total_bytes>35_000_000:
        raise RuntimeError('NISAR asset staging exceeds its 35 MB allocation')
    temp = output.with_suffix('.tmp')
    temp.write_text(json.dumps(manifest,indent=2,ensure_ascii=False,allow_nan=False)+'\n',encoding='utf-8')
    temp.replace(output)
    print(f'Published {len(manifest["observations"])} candidate observations, {total_bytes:,} scientific and display bytes.',flush=True)


if __name__=='__main__':
    main()
