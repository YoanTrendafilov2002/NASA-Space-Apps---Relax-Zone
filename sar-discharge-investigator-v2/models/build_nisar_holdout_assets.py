"""Build bounded native NISAR crops from saved Harmony subsets and one local HDF5."""

import argparse
from contextlib import ExitStack
from datetime import datetime
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import h5py
import numpy as np
import rasterio
from rasterio.transform import Affine, array_bounds
from rasterio.warp import transform, transform_bounds, transform_geom
from rasterio.windows import Window
from shapely.geometry import Polygon, box, shape
from shapely.ops import unary_union

from build_nisar_map_assets import GRID, IDENT, ROOT, WGS84, digest, make_assets


COLLECTION = 'NISAR_L2_GCOV_PROVISIONAL_V1'
CONCEPT = 'C2854338529-ASF'
UNU = 'https://geilab.unu.edu/satellite-observations-reveal-extent-of-major-oil-slicks-near-iran-and-oman/'
NOAA_MPSR = 'https://www.ospo.noaa.gov/products/ocean/marinepollution/archive.html?year=2026'
QESHM = 'qeshm-nisar'
EUGENE = 'gulf-eugene-nisar'
ELOI = 'eloi-bay-nisar'
ATANASOVSKO = 'atanasovsko-nisar'
NATIVE_WINDOW = (10218, 8262, 270, 441)
NATIVE_TRANSFORM = Affine(20, 0, 537000, 0, -20, 4717800)
QESHM_LABEL = 'qeshm-unu-aug10-reference'
EUGENE_LABEL = 'eugene-noaa-aug27-reference'
ELOI_LABEL = 'eloi-noaa-oct12-reference'
ATANASOVSKO_LABEL = 'atanasovsko-ramsar-292'
SCENES = (
    ('july17-pre-event','nisar-qeshm-20260717',QESHM,32640,20,'G4254521451-ASF',128,15,'ASCENDING'),
    ('aug10-event','nisar-qeshm-20260810',QESHM,32640,20,'G4278857246-ASF',128,15,'ASCENDING'),
    ('aug22-follow-up','nisar-qeshm-20260822',QESHM,32640,20,'G4288286710-ASF',128,15,'ASCENDING'),
    ('eugene-20260827','nisar-eugene-20260827',EUGENE,32615,10,'G4291280213-ASF',33,16,'ASCENDING'),
    ('eloi-20261012','nisar-eloi-20261012',ELOI,32616,10,'G4364390789-ASF',4,17,'ASCENDING'),
    ('atanasovsko-20260712','nisar-atanasovsko-20260712',ATANASOVSKO,32635,20,'G4252772664-ASF',65,67,'DESCENDING'),
)


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def outline(path):
    geometry = unary_union([shape(item['geometry']) for item in read_json(path)['features']])
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError(f'Invalid independent outline in {path.name}')
    return geometry


def public_metadata(directory, scene):
    role, _, _, _, posting, granule_id, track, frame, direction = scene
    record = read_json(directory / f'holdout-{role}.json')
    attrs = record['attributes']
    product = record['product']
    fields = product.split('_')
    fmt = record['time']['RangeDateTime']
    time_from_name = datetime.strptime(fields[11],'%Y%m%dT%H%M%S').isoformat()+'Z'
    timestamp = datetime.fromisoformat(fmt['BeginningDateTime'])
    if (record['id'] != granule_id or record['collection_id'] != CONCEPT
            or attrs['PRODUCT_VERSION'] != ['1.0.11'] or attrs['TRACK_NUMBER'] != [str(track)] or attrs['FRAME_NUMBER'] != [str(frame)]
            or fields[6] != direction[0] or datetime.fromisoformat(time_from_name) != timestamp.replace(microsecond=0)
            or attrs['FREQUENCY_A_POLARIZATION'] != ['HH','HV']
            or float(attrs['FREQUENCY_A_RANGE_BANDWIDTH'][0]) != {10:40,20:20}[posting]):
        raise ValueError(f'CMR geometry, instrument or product identity mismatch for {role}')
    rings = record['geometry']['HorizontalSpatialDomain']['Geometry']['GPolygons']
    footprint = unary_union([Polygon([(v['Longitude'],v['Latitude']) for v in ring['Boundary']['Points']]) for ring in rings])
    source_bytes = next(item['SizeInBytes'] for item in record['size_records'] if item['Name']==f'{product}.h5')
    return record, time_from_name, fmt['EndingDateTime'], footprint, source_bytes


def native_window(source, region, target_outline):
    affine = source.transform
    crs = source.crs.to_string()
    if region == QESHM:
        x,y = (a[0] for a in transform(WGS84,crs,[56.00],[26.58]))
        col,row = (~affine)*(x,y)
        return Window(round(col)-575,round(row)-700,1150,1400)
    geometry = shape(transform_geom(WGS84,crs,target_outline.__geo_interface__))
    west,south,east,north = geometry.buffer(2400).bounds
    left,top = (~affine)*(west,north)
    right,bottom = (~affine)*(east,south)
    return Window(math.floor(left),math.floor(top),math.ceil(right)-math.floor(left),math.ceil(bottom)-math.floor(top))


def receipt_paths(downloads, product):
    paths = {}
    for term in ('HHHH','HVHV','mask','numberOfLooks'):
        path = downloads/f"{product}_{GRID.replace('/','_')}_{term}_subsetted.tif"
        if path.is_file():
            paths[term] = path
    if not {'HHHH','mask'} <= paths.keys():
        raise FileNotFoundError(f'Expected a received HH and mask Harmony GeoTIFF for {product}')
    return paths


def harmonized_inputs(downloads, scene, metadata, target_outline):
    _,_,region,epsg,posting,*_ = scene
    paths = receipt_paths(downloads,metadata['product'])
    arrays = {}
    with ExitStack() as opened:
        rasters = {name: opened.enter_context(rasterio.open(path)) for name,path in paths.items()}
        hh = rasters['HHHH']
        grid = (hh.crs,hh.transform,hh.shape)
        if (hh.crs.to_epsg()!=epsg or hh.res!=(posting,posting) or not hh.transform.is_rectilinear
                or any((r.crs,r.transform,r.shape)!=grid or r.count!=1 for r in rasters.values())
                or any(r.tags().get('mission_name')!='NISAR' for r in rasters.values())):
            raise ValueError(f'Native Harmony geometry mismatch for {scene[0]}')
        window = native_window(hh,region,target_outline)
        if (min(window.col_off,window.row_off)<0 or window.col_off+window.width>hh.width or window.row_off+window.height>hh.height
                or window.width*window.height>3_000_000):
            raise ValueError('Requested crop must fit the received native scene and pixel limit')
        affine = hh.window_transform(window)
        mask = rasters['mask'].read(1,window=window,masked=True)
        valid = ~np.ma.getmaskarray(mask) & (mask.data==1)
        quality = 'Valid source subswath mask and finite nonnegative gamma-zero power.'
        if 'numberOfLooks' in rasters:
            looks = rasters['numberOfLooks'].read(1,window=window,masked=True)
            valid &= ~np.ma.getmaskarray(looks) & np.isfinite(looks.data) & (looks.data>0)
            quality = 'Valid source subswath mask, positive looks and finite nonnegative gamma-zero power.'
        for polarization in ('HH','HV'):
            term = polarization*2
            if term not in rasters:
                continue
            native = rasters[term].read(1,window=window,masked=True)
            if native.dtype != np.float32:
                raise ValueError('Harmony covariance data must preserve Float32 gamma-zero power')
            arrays[polarization] = np.where(valid & ~np.ma.getmaskarray(native) & np.isfinite(native.data) & (native.data>=0),
                                            native.data,np.float32(np.nan))
    sources = {key:{'filename':path.name,'size_bytes':path.stat().st_size,'sha256':digest(path)} for key,path in paths.items()}
    qa = {'quality_rule':quality,'look_direction':None,'noise_correction_applied':None,'nesz_available':False,
          'source_grid_looks_available':'numberOfLooks' in paths,'source_exception_mask_available':False,
          'received_rasters':sources,'harmony_pixel_window':[int(v) for v in (window.col_off,window.row_off,window.width,window.height)]}
    return arrays,affine,qa


def atanasovsko_inputs(downloads, metadata, expected_size):
    path = downloads / f"{metadata['product']}.h5"
    if path.stat().st_size != expected_size:
        raise ValueError('The local Atanasovsko HDF5 no longer matches the provider byte count')
    col,row,width,height = NATIVE_WINDOW
    window = np.s_[row:row+height,col:col+width]
    with h5py.File(path,'r') as h5:
        ident = h5[IDENT]
        grid = h5[GRID]
        parms = h5[f'{ROOT}/metadata/processingInformation/parameters']
        if (ident['granuleId'][()].decode()!=metadata['product'] or ident['radarBand'][()]!=b'L'
                or ident['productVersion'][()]!=b'1.0.11' or int(grid['projection'][()])!=32635
                or parms['rtc/outputBackscatterNormalizationConvention'][()]!=b'gamma0'):
            raise ValueError('Unexpected HDF5 identity, processing or calibration units')
        x,y = grid['xCoordinates'][window[1]], grid['yCoordinates'][window[0]]
        if (not np.array_equal(x,NATIVE_TRANSFORM.c+20*(np.arange(width)+0.5))
                or not np.array_equal(y,NATIVE_TRANSFORM.f-20*(np.arange(height)+0.5))):
            raise ValueError('Native Atanasovsko coordinate vectors differ from the pinned window')
        noise = parms['noiseCorrectionApplied'][()].decode().lower()
        if noise not in ('true','false'):
            raise ValueError('Noise convention is unavailable in the native HDF5')
        mask,looks,exception = (grid[key][window] for key in ('mask','numberOfLooks','inputDataExceptionMask'))
        if exception.dtype!=np.uint8 or int(grid['numberOfSubSwaths'][()])!=1:
            raise ValueError('Unexpected native mask specification')
        valid = (mask==1) & (looks>0) & np.isfinite(looks) & (exception==0)
        arrays = {}
        for pol in ('HH','HV'):
            raster = grid[pol*2]
            if raster.dtype!=np.float32 or raster.attrs['units']!=b'1':
                raise ValueError('Unexpected native covariance representation')
            power = raster[window]
            arrays[pol] = np.where(valid & np.isfinite(power) & (power>=0),power,np.float32(np.nan))
        look = ident['lookDirection'][()].decode()
    qa = {'quality_rule':'Valid source subswath, positive looks, zero input exception and finite nonnegative power.',
          'look_direction':look,'noise_correction_applied':noise=='true','nesz_available':True,
          'source_grid_looks_available':True,'source_exception_mask_available':True,
          'source_filename':path.name,'source_size_bytes':path.stat().st_size,'source_sha256':digest(path),'source_window':list(NATIVE_WINDOW)}
    return arrays,NATIVE_TRANSFORM,qa


def export_channel(staging, i, product, epsg, posting, affine, pol, power, qa, evidence):
    asset = make_assets(staging,i,product,epsg,affine,pol,power,qa)
    destination = staging/asset['science_url'].lstrip('/')
    setting = qa['noise_correction_applied']
    with rasterio.open(destination,'r+') as native:
        native.update_tags(QUALITY_MASK=qa['quality_rule'],
                           NOISE_CORRECTION_APPLIED='unknown' if setting is None else str(setting).lower())
    with rasterio.open(destination) as native:
        if (native.res!=(posting,posting) or native.transform!=affine or native.crs.to_epsg()!=epsg
                or not np.array_equal(native.read(1),power,equal_nan=True)
                or not np.array_equal(native.read_masks(1)!=0,np.isfinite(power))):
            raise ValueError('Derived GeoTIFF failed grid, native sample or mask validation')
    return dict(asset,science_posting_m=posting,science_sha256=digest(destination),science_size_bytes=destination.stat().st_size,
                evidence_id=evidence,scientific=True,quality_mask=qa['quality_rule'],nesz_available=qa['nesz_available'])


def reference_sites(metadata):
    eugene_path = metadata/'20260827_1210_RADARSAT2_OIL.geojson'
    eloi_path = metadata/'20261012_1626_LANDSAT8_OIL.geojson'
    proxies = {EUGENE:outline(eugene_path),ELOI:outline(eloi_path)}
    qeshm = read_json(metadata/'qeshm-science-plan.json')['reference']
    evidence = [
        {'id':QESHM_LABEL,'title':'UNU-INWEH · Qeshm and Hengam oil interpretation','url':UNU,'kind':'Independent satellite interpretation',
         'notes':'UNU mapped oil near the islands from independently acquired Sentinel-1D imagery 39 minutes after the NISAR pass. The published chart provides regional evidence. Registration uncertainty is too high for pixel labels.',
         'reference_granule':qeshm['sentinel_1d_cmr'],'reference_start_offset_seconds':qeshm['difference_in_start_seconds'],
         'chart_sha256':qeshm['chart_sha256'],'chart_control_max_m':qeshm['georeferencing']['projective']['loo_max_m']},
        {'id':EUGENE_LABEL,'title':'NOAA NESDIS · Eugene Island surveillance outline','url':NOAA_MPSR,'kind':'Independent satellite interpretation',
         'notes':'The public RADARSAT-2 interpretation at 12:10 UTC falls roughly 43 minutes after the NISAR pass. The report describes possible oil; material has not been validated per cell.',
         'reference_sha256':digest(eugene_path),'incident_url':'https://incidentnews.noaa.gov/incident/11216'},
        {'id':ELOI_LABEL,'title':'NOAA · Eloi Bay field report and surveillance','url':'https://incidentnews.noaa.gov/incident/11235','kind':'Field incident report',
         'notes':'NOAA records an independently confirmed crude discharge. The Landsat 8 outline at 16:26 UTC on October 12 is later imagery. This crop covers the coastal outline and nearby water. The later eastward outline extends past that local view.',
         'reference_sha256':digest(eloi_path),'satellite_archive_url':NOAA_MPSR},
        {'id':ATANASOVSKO_LABEL,'title':'Ramsar · Atanasovsko Lake','url':'https://rsis.ramsar.org/ris/292','kind':'Official wetland designation',
         'notes':'Ramsar identifies the saline lagoon, marshes, reedbeds and managed salt pans. This single acquisition provides radar backscatter around them.'},
    ]
    centre = {site: list(proxies[site].representative_point().coords[0]) for site in proxies}
    sites = {
        QESHM:('Qeshm and Hengam coastal waters','oil_spill',['Qeshm','Hengam'],QESHM_LABEL,[56.006,26.576],'Regional interpretation in the UNU chart'),
        EUGENE:('Eugene Island offshore waters, Louisiana','oil_spill',['Eugene Island','Eugene Island 316'],EUGENE_LABEL,centre[EUGENE],'NOAA satellite interpretation'),
        ELOI:('Eloi Bay, Louisiana','oil_spill',['Eloi Bay'],ELOI_LABEL,centre[ELOI],'NOAA coastal interpretation'),
        ATANASOVSKO:('Atanasovsko Lake, Bulgaria','wetlands',['Atanasovsko','Atanasovsko Lake','Burgas salt pans'],ATANASOVSKO_LABEL,
                     [27+28/60+29/3600,42+33/60+54/3600],'Ramsar site location'),
    }
    return sites,proxies,evidence


def build(downloads, metadata, destination):
    definitions,proxies,evidence = reference_sites(metadata)
    sites = {}
    observations = []
    destination.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=destination) as stage:
        staging = Path(stage)
        for folder in ('sar','science'):
            (staging/folder).mkdir()
        qeshm_grid = None
        for scene in SCENES:
            role,identifier,region,epsg,posting,granule_id,track,frame,direction = scene
            cmr,started,ended,source_geometry,provider_size = public_metadata(metadata,scene)
            if region == ATANASOVSKO:
                arrays,affine,qa = atanasovsko_inputs(downloads,cmr,provider_size)
            else:
                arrays,affine,qa = harmonized_inputs(downloads,scene,cmr,proxies.get(region))
            height,width = arrays['HH'].shape
            grid = (epsg,affine,height,width)
            if region == QESHM:
                if qeshm_grid is not None and grid != qeshm_grid:
                    raise ValueError('Qeshm time-series crops differ in native grid')
                qeshm_grid = grid
            native_bounds = array_bounds(height,width,affine)
            window_geometry = shape(transform_geom(f'EPSG:{epsg}',WGS84,box(*native_bounds).__geo_interface__))
            verified_footprint = source_geometry.intersection(window_geometry)
            if verified_footprint.is_empty or verified_footprint.area==0:
                raise ValueError('The returned window falls outside the verified site granule footprint')
            geometry = verified_footprint.__geo_interface__
            bbox = list(transform_bounds(f'EPSG:{epsg}',WGS84,*native_bounds,densify_pts=21))
            qa['provider_footprint_fraction_of_window'] = verified_footprint.area/window_geometry.area
            if region in proxies and not window_geometry.covers(proxies[region]):
                raise ValueError('Local crop excludes part of its selected independent report outline')
            source_id = identifier+'-source'
            channels = {}
            for pol in ('HH','HV'):
                if pol in arrays:
                    channels[pol] = export_channel(staging,identifier,cmr['product'],epsg,posting,affine,pol,arrays[pol],qa,source_id)
                else:
                    channels[pol] = {'status':'catalogue_only','scientific':False,'term':pol*2,
                                     'dataset_path':f'/{GRID}/{pol*2}','evidence_id':source_id}
            name,topic,aliases,reference,point,marker = definitions[region]
            if region in proxies:
                mapped = verified_footprint.intersection(proxies[region])
                if mapped.is_empty or mapped.area==0:
                    raise ValueError('The ASF footprint misses the selected independent report outline')
                qa['provider_footprint_fraction_of_reference'] = mapped.area/proxies[region].area
                point = list(mapped.representative_point().coords[0])
            day = started[:10]
            if region not in sites:
                sites[region] = {'id':region,'name':name,'topic':topic,'aliases':aliases,'bbox':bbox,
                                 'start':day,'end':day,'evidence_id':reference,'geometry':geometry,'geometry_kind':'study_area',
                                 'review_points':[{'id':region+'-reference','topic':topic,'label':marker,'lon':point[0],
                                                   'lat':point[1],'evidence_id':reference,'kind':'independent_reference'}]}
            sites[region]['end'] = day
            observations.append({'id':identifier,'site_id':region,'product_id':cmr['product'],'acquisition_id':cmr['product'],
                                 'mission':'nisar','provider':'NASA ASF DAAC','product':'NISAR GCOV','product_type':'GCOV',
                                 'sensor':'L-SAR','wavelength':'L','frequency':'A','orbit':track,'frame':frame,'direction':direction,
                                 'collection':COLLECTION,'collection_concept_id':CONCEPT,'granule_concept_id':granule_id,
                                 'mode':'Frequency A · dual polarization','maturity':'provisional','geometry':geometry,
                                 'source_geometry':source_geometry.__geo_interface__,'geometry_kind':'local_coverage_window',
                                 'acquired_at':started,'acquisition_end':ended,'processing_version':'1.0.11','look_direction':qa['look_direction'],
                                 'noise_correction_applied':qa['noise_correction_applied'],'evidence_id':source_id,'reference_evidence_id':reference,
                                 'channels':channels,'source_url':cmr['direct_cmr_url'],'source_requires_earthdata':True,
                                 'science_requires_earthdata':False})
            recorded = 'Native GCOV HDF5' if region == ATANASOVSKO else 'Earthdata Harmony source GeoTIFF subsets'
            note = 'Native power and documented source validity bits retained.'
            if region != ATANASOVSKO:
                note = 'Native recorded power and available quality layers retained. The subset lacks spatial NESZ and input exception mask. Source noise correction setting is unverified.'
            evidence.append({'id':source_id,'title':f'NASA ASF DAAC · {name} · {day}','kind':recorded,'url':cmr['direct_cmr_url'],
                             'source_id':cmr['product'],'provider_size_bytes':provider_size,
                             'documentation_url':'https://nisar-docs.asf.alaska.edu/gcov/','notes':note,**qa})
        output = 'nisar-holdouts.json'
        manifest = {'schema_version':1,'sites':list(sites.values()),'observations':observations,'evidence':evidence}
        (staging/output).write_text(json.dumps(manifest,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        for folder in ('sar','science'):
            (destination/folder).mkdir(exist_ok=True)
        for path in sorted(p for p in staging.rglob('*') if p.is_file() and p.name!=output):
            path.replace(destination/path.relative_to(staging))
        (staging/output).replace(destination/output)
    print(json.dumps({'sites':len(sites),'observations':len(observations),'assets':[
        {'id':o['id'],'channels':{p:(a.get('science_shape'),a.get('valid_pixels'),a.get('status')) for p,a in o['channels'].items()}}
        for o in observations]},indent=2),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--downloads',type=Path,required=True)
    parser.add_argument('--metadata',type=Path,required=True)
    parser.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[1]/'data')
    args = parser.parse_args()
    build(args.downloads,args.metadata,args.data)


if __name__ == '__main__':
    main()
