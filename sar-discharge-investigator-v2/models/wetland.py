"""Fit and evaluate the Curuai NISAR exposed-water models with local data.

Port of Set up QGIS / nisar_wetland_validation.py and nisar_wetland_seasonal.py.
The original threshold recipe is retained as a benchmark. The optional two
feature model uses only NISAR inputs and is fitted before October is opened.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

import h5py  # Import before GDAL registers its bundled HDF5 runtime on Windows.
import numpy as np
from osgeo import gdal, osr
from scipy.interpolate import RegularGridInterpolator
from scipy.ndimage import distance_transform_edt
from scipy.optimize import minimize
from scipy.special import expit


ROOT = 'science/LSAR/GCOV'
GRID = f'{ROOT}/grids/frequencyA'
IDENTIFICATION = 'science/LSAR/identification'
EPSG = 32721
CELL = 60
SPATIAL_BLOCK = 3000
CALIBRATION_FRACTION_MINIMUM = .95
CALIBRATION_MIN_PER_CLASS = 30
NOISE_CLEARANCE_DB = 3
REFERENCE_MINIMUM_FRACTION = .80
PENALTY = .01
EXPECTED_GRANULES = {
    'august': 'NISAR_L2_PR_GCOV_028_003_A_001_2005_DHDH_A_20260813T092154_20260813T092229_P05023_N_F_J_001',
    'october': 'NISAR_L2_PR_GCOV_033_003_A_001_2005_DHDH_A_20261012T092154_20261012T092229_P05023_N_F_J_001',
}
EXPECTED_OPTICAL = {
    'august': 'S2A_21MXT_20260813_0_L2A',
    'october': 'S2A_21MXT_20261012_0_L2A',
}
EXPECTED_BOUNDS = (641200.0, 698000.0, 9745640.0, 9786640.0)
MASK_CODES = {'unassessable': 0, 'exposed_water_flag': 1, 'no_exposed_water_flag': 2}


def db(power):
    result = np.full(np.shape(power), np.nan, dtype=np.float32)
    valid = np.isfinite(power) & (power > 0)
    result[valid] = 10 * np.log10(power[valid])
    return result


def digest(path):
    result = sha256()
    with path.open('rb') as src:
        for data in iter(lambda: src.read(8_388_608), b''):
            result.update(data)
    return result.hexdigest()


def epsg_srs(epsg):
    result = osr.SpatialReference()
    result.ImportFromEPSG(epsg)
    result.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    return result


def save_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def reference_geometry(path, provenance, epoch):
    report = json.loads(provenance.read_text(encoding='utf-8'))
    if report['sentinel2_stac'].rsplit('/', 1)[-1] != EXPECTED_OPTICAL[epoch]:
        raise ValueError(f'{epoch}: unexpected independent optical source ID')
    image = gdal.Open(str(path))
    spatial_ref = image.GetSpatialRef()
    x, dx, r1, y, r2, dy = image.GetGeoTransform()
    bounds = (x, x + image.RasterXSize*dx, y + image.RasterYSize*dy, y)
    if spatial_ref.GetAuthorityCode(None) != str(EPSG) or (dx, dy, r1, r2) != (20, -20, 0, 0) or bounds != EXPECTED_BOUNDS:
        raise ValueError(f'{epoch}: unexpected reference CRS, posting, rotation or footprint')
    original = image.ReadAsArray()
    unique, count = np.unique(original, return_counts=True)
    classes = {str(int(key)): int(value) for key, value in zip(unique, count)}
    if classes != report['reference_counts'] or set(classes) != {'0','1','2','3'}:
        raise ValueError(f'{epoch}: reference class counts do not match independent optical provenance')
    return bounds, {'source_item':report['sentinel2_stac'], 'source_acquired_at':report['acquisition_utc'],
        'reference_filename':path.name,'reference_sha256':digest(path),'reference_classes':report['class_meanings'],
        'reference_counts':classes,'optical_cloud_buffer_m':report['cloud_buffer_m'],
        'provider_note':'Optically exposed water proxy based on independent Sentinel-2 SCL and MNDWI agreement.'}


def block_average(field, factor, height, width):
    return field[:height*factor, :width*factor].reshape(height, factor, width, factor).mean(axis=(1,3))


@dataclass
class Epoch:
    x: np.ndarray
    y: np.ndarray
    hh: np.ndarray
    hv: np.ndarray
    hh_noise_db: np.ndarray
    hv_noise_db: np.ndarray
    hh_net_snr_db: np.ndarray
    hv_net_snr_db: np.ndarray
    quality_hh: np.ndarray
    quality_hv: np.ndarray
    reference: np.ndarray
    source: dict


def noise_at(nesz, pol, gx, gy):
    interpolation = RegularGridInterpolator((nesz['yCoordinates'][:], nesz['xCoordinates'][:]),
        nesz[pol][:], bounds_error=False, fill_value=np.nan)
    output = np.empty((len(gy),len(gx)), dtype=np.float32)
    for row in range(0, len(gy), 512):
        block = slice(row, row+512)
        xx, yy = np.meshgrid(gx,gy[block])
        output[block] = interpolation((yy,xx))
    return output


def align_reference(path, x, y, posting):
    warped = gdal.Warp('',str(path),format='MEM',dstSRS=epsg_srs(EPSG).ExportToWkt(),
        outputBounds=(x[0]-posting/2, y[-1]-posting/2, x[-1]+posting/2, y[0]+posting/2),
        width=len(x),height=len(y),resampleAlg='near',dstNodata=0)
    return warped.ReadAsArray()


def extract(epoch, hdf, optical, provenance):
    bounds, reference_source = reference_geometry(optical, provenance, epoch)
    with h5py.File(hdf, 'r') as file:
        ident = file[IDENTIFICATION]
        granule = ident['granuleId'][()].decode()
        if granule != EXPECTED_GRANULES[epoch]:
            raise ValueError(f'{epoch}: unexpected NISAR product {granule}')
        grid = file[GRID]
        metadata = file[f'{ROOT}/metadata']
        parameters = metadata['processingInformation/parameters']
        norm = parameters['rtc/outputBackscatterNormalizationConvention'][()].decode().lower()
        correction = parameters['noiseCorrectionApplied'][()].decode().lower()
        nesz = metadata['calibrationInformation/frequencyA/noiseEquivalentBackscatter']
        longitude, latitude = grid['xCoordinates'][:],grid['yCoordinates'][:]
        posting = float(longitude[1]-longitude[0])
        if int(grid['projection'][()]) != EPSG or int(nesz['projection'][()]) != EPSG or posting != 20 \
                or not np.all(np.diff(longitude)==posting) or not np.all(np.diff(latitude)==-posting) \
                or norm != 'gamma0' or correction not in {'true','false'}:
            raise ValueError('Unsupported power normalization, noise correction, or native NISAR grid')
        west, east, south, north = bounds
        left, right = np.floor((west-longitude[0])/posting), np.ceil((east-longitude[0])/posting)
        top, bottom = np.ceil((north-latitude[0])/posting), np.floor((south-latitude[0])/posting)
        x = longitude[0]+posting*np.arange(left,right+1)
        y = latitude[0]+posting*np.arange(top,bottom-1,-1)
        columns = np.flatnonzero((longitude >= x[0]) & (longitude <= x[-1]))
        rows = np.flatnonzero((latitude >= y[-1]) & (latitude <= y[0]))
        if not np.array_equal(x,longitude[columns]) or not np.array_equal(y,latitude[rows]):
            raise ValueError('The whole reference window must align to this NISAR native grid')
        subset = np.s_[rows[0]:rows[-1]+1, columns[0]:columns[-1]+1]
        mask = grid['mask'][subset]
        exception = grid['inputDataExceptionMask'][subset]
        looks = grid['numberOfLooks'][subset]
        if exception.dtype not in (np.dtype('uint8'),np.dtype('uint16')):
            raise ValueError('Unsupported inputDataExceptionMask dtype')
        description = grid['inputDataExceptionMask'].attrs['description'].decode()
        if exception.dtype.itemsize==2 and not all(label in description for label in ('256:HH','512:HV')):
            raise ValueError('Product does not document the expected HH/HV focused bits')
        shared_native = ((mask>=1)&(mask<=grid['numberOfSubSwaths'][()])&np.isfinite(looks)&(looks>0))
        factor = int(CELL/posting)
        height,width = len(y)//factor,len(x)//factor
        average = lambda a: block_average(a,factor,height,width)
        power,native_quality,noise,net_snr,native_valid_counts = {},{},{},{},{}
        for pol in ('HH','HV'):
            measured = grid[pol*2][subset]
            estimated_noise = noise_at(nesz,pol,x,y)
            focused_bit = {'HH':256,'HV':512}[pol]
            clean = exception==0 if exception.dtype.itemsize==1 else ((exception&255)==0)&((exception&focused_bit)!=0)
            valid = shared_native & clean & np.isfinite(measured) & (measured>=0) & np.isfinite(estimated_noise) & (estimated_noise>0)
            native_valid_counts[pol] = int(np.count_nonzero(valid))
            native_quality[pol] = average(valid)==1
            observed = average(measured)
            aggregated_noise = average(estimated_noise)
            net = observed-aggregated_noise if correction=='false' else observed
            with np.errstate(divide='ignore',invalid='ignore'):
                net_snr[pol] = db(net/aggregated_noise)
            power[pol] = db(observed)
            noise[pol] = db(aggregated_noise)
        radar_start = ident['zeroDopplerStartTime'][()].decode()+'Z'
        radar_end = ident['zeroDopplerEndTime'][()].decode()+'Z'
        optical_lag = datetime.fromisoformat(reference_source['source_acquired_at'])-datetime.fromisoformat(radar_start)
        source = {'granule':granule,'source_filename':hdf.name,'source_size_bytes':hdf.stat().st_size,
            'frequency':'A','native_posting_m':posting,'projection_epsg':EPSG,'weighting':'Mean native linear covariance power before dB conversion',
            'noise_correction_applied':correction,'exception_mask_dtype':str(exception.dtype),
            'product_version':ident['productVersion'][()].decode(),'product_specification_version':ident['productSpecificationVersion'][()].decode(),
            'radar_start_utc':radar_start,'radar_end_utc':radar_end,'optical_lag_seconds':optical_lag.total_seconds(),
            'orbit_number':int(ident['absoluteOrbitNumber'][()]),'track_number':int(ident['trackNumber'][()]),
            'frame_number':int(ident['frameNumber'][()]),'pass_direction':ident['orbitPassDirection'][()].decode(),
            'look_direction':ident['lookDirection'][()].decode(),'native_valid_postings_by_pol':native_valid_counts,
            **reference_source}
    aligned = align_reference(optical,x,y,posting)
    reference = np.select([average(aligned==2)>=REFERENCE_MINIMUM_FRACTION,average(aligned==1)>=REFERENCE_MINIMUM_FRACTION],[1,2],0).astype(np.uint8)
    source['input_sha256'] = digest(hdf)
    return Epoch(x[:width*factor].reshape(width,factor).mean(axis=1),y[:height*factor].reshape(height,factor).mean(axis=1),
        power['HH'],power['HV'],noise['HH'],noise['HV'],net_snr['HH'],net_snr['HV'],native_quality['HH'],native_quality['HV'],reference,source)


def spatial_blocks(epoch):
    east = np.floor(epoch.x/SPATIAL_BLOCK).astype(np.int32)
    north = np.floor(epoch.y/SPATIAL_BLOCK).astype(np.int32)
    training = (east[None,:]+north[:,None])%2==0
    return distance_transform_edt(training)>1, distance_transform_edt(~training)>1


def threshold_fit(epoch, spatial_train):
    calibration = spatial_train & (epoch.reference>0) & epoch.quality_hh & np.isfinite(epoch.hh)
    hh, noise, label = epoch.hh[calibration], epoch.hh_noise_db[calibration], epoch.reference[calibration]
    history = []
    for threshold in np.arange(-32,-8.01,.05):
        tested = noise<=threshold-NOISE_CLEARANCE_DB
        water,other = tested&(label==1),tested&(label==2)
        if min(water.sum(),other.sum())<CALIBRATION_MIN_PER_CLASS or tested.sum()<CALIBRATION_FRACTION_MINIMUM*len(hh):
            continue
        selected = hh<=threshold
        score = .5*(selected[water].mean()+(~selected[other]).mean())
        history.append((round(float(threshold),3),float(score)))
    if not history:
        raise ValueError('No candidate cutoff has sufficient calibration support')
    best = max(score for _,score in history)
    tied = [cutoff for cutoff,score in history if score==best]
    cutoff = float(np.median(tied))
    return cutoff,{'method':'QGIS HH threshold','cutoff_db':cutoff,'training_balanced_accuracy':best,
        'calibration_cells':int(np.count_nonzero(calibration)),'tested_eligible_cutoffs':len(history),
        'tie_break':'Median of the maximizing cutoffs, as in the original QGIS method.'}


def predictors(epoch):
    return np.stack((epoch.hh,epoch.hh-epoch.hv),axis=-1)


def baseline_support(epoch, cutoff):
    return epoch.quality_hh & np.isfinite(epoch.hh) & (epoch.hh_noise_db<=cutoff-NOISE_CLEARANCE_DB)


def dual_support(epoch, cutoff):
    # The shared comparison uses baseline technical support and both QC'ed
    # channels. A separate stress test requires >=3 dB net SNR in both.
    return baseline_support(epoch,cutoff) & epoch.quality_hv & np.isfinite(epoch.hv)


def logit_fit(features, target):
    values = features.astype(np.float64)
    labels = target.astype(np.float64)
    positive = int(target.sum())
    negative = len(target)-positive
    if min(positive,negative)<CALIBRATION_MIN_PER_CLASS or not np.isfinite(values).all():
        raise ValueError('Dual-polarization training classes unavailable or nonfinite')
    mean,scale = values.mean(axis=0),values.std(axis=0)
    if np.any(scale<=0):
        raise ValueError('A radar feature is constant')
    design = np.column_stack((np.ones(len(values)),(values-mean)/scale))
    weights = np.where(target,.5/positive,.5/negative)

    def objective(coef):
        linear = design@coef
        error = weights*(expit(linear)-labels)
        gradient = design.T@error
        gradient[1:] += PENALTY*coef[1:]
        loss = np.sum(weights*(np.logaddexp(0,linear)-labels*linear)) + PENALTY*np.dot(coef[1:],coef[1:])/2
        return loss,gradient

    fit = minimize(objective,np.zeros(design.shape[1]),method='BFGS',jac=True)
    if not fit.success:
        raise RuntimeError(f'Wetland fit failed: {fit.message}')
    return {'method':'Class-balanced L2 logistic','features':['HH gamma0 dB','HH–HV observed gamma0 dB contrast'],
        'positive_train_cells':positive,'negative_train_cells':negative,'calibration_cells':len(labels),
        'centering':mean.tolist(),'scaling':scale.tolist(),'coefficients':fit.x.tolist(),
        'regularization':PENALTY,'decision_logit':0.0,'iterations':int(fit.nit), 'converged':True}


def logistic_classes(model, epoch, support):
    predicted = np.zeros(epoch.reference.shape,dtype=bool)
    mean,scale,coefficient = map(np.asarray,(model['centering'],model['scaling'],model['coefficients']))
    features = predictors(epoch)[support]
    standardized = (features-mean)/scale
    scores = standardized@coefficient[1:]+coefficient[0]
    predicted[support] = scores>=model['decision_logit']
    return predicted


def score(test, model, outcome, truth, predicted, eligible_reference, support):
    valid = eligible_reference & support
    tp,fn,fp,tn = (int(np.count_nonzero(valid & m)) for m in (truth&predicted,truth&~predicted,~truth&predicted,~truth&~predicted))
    ratio = lambda num,denom: num/denom if denom else None
    recall, specificity = ratio(tp,tp+fn),ratio(tn,tn+fp)
    return {'test':test,'model':model,'outcome':outcome,'reference_cells':int(eligible_reference.sum()),
        'assessed_cells':int(valid.sum()),'withheld_water_cells':int(np.count_nonzero(eligible_reference & ~support & truth)),
        'withheld_other_cells':int(np.count_nonzero(eligible_reference & ~support & ~truth)),
        'assessed_fraction':ratio(int(valid.sum()),int(eligible_reference.sum())),
        'matched_water_cells':tp,'missed_water_cells':fn,'extra_water_cells':fp,'matched_other_cells':tn,
        'recall':recall,'precision':ratio(tp,tp+fp),'iou':ratio(tp,tp+fn+fp),'specificity':specificity,
        'balanced_accuracy':(recall+specificity)/2 if recall is not None and specificity is not None else None,
        'matched_water_km2':tp*CELL**2/1e6,'missed_water_km2':fn*CELL**2/1e6,'extra_water_km2':fp*CELL**2/1e6}


def write_raster(path, data, epoch, description):
    h,w = data.shape
    output = gdal.GetDriverByName('GTiff').Create(str(path),w,h,1,gdal.GDT_Byte,['COMPRESS=DEFLATE','TILED=YES'])
    output.SetGeoTransform((epoch.x[0]-CELL/2,CELL,0,epoch.y[0]+CELL/2,0,-CELL))
    output.SetProjection(epsg_srs(EPSG).ExportToWkt())
    band = output.GetRasterBand(1)
    band.SetNoDataValue(0)
    band.SetDescription(description)
    band.WriteArray(data.astype(np.uint8))
    output.SetMetadata({'study':'Curuai exposed water benchmark','license':'NASA NISAR scientific inputs; see report for input provenance',
        'grid_posting_m':str(CELL),'water_display_colour':'#2b5b6c','science_scope':'Derived research screen; optical proxy evaluation'})
    output.FlushCache()
    band = None
    output = None
    verified = gdal.Open(str(path))
    if verified.GetSpatialRef().GetAuthorityCode(None)!=str(EPSG) or not np.array_equal(verified.ReadAsArray(),data):
        raise RuntimeError(f'Written GeoTIFF could not be independently verified: {path.name}')
    return {'filename':path.name,'sha256':digest(path),'class_counts':{str(int(k)):int(v) for k,v in Counter(data.ravel()).items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--august-h5',type=Path,required=True)
    parser.add_argument('--october-h5',type=Path,required=True)
    parser.add_argument('--optical-directory',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args = parser.parse_args()
    gdal.UseExceptions()
    args.out.mkdir(parents=True,exist_ok=True)

    def load(name, date, path):
        directory = args.optical_directory
        return extract(name,path,directory/f'curuai_{date}_optical_reference.tif',directory/f'curuai_{date}_optical_provenance.json')

    print('Reading the August NISAR and independent optical reference windows.',flush=True)
    august = load('august','20260813',args.august_h5)
    train,held_out = spatial_blocks(august)
    cutoff,threshold = threshold_fit(august,train)
    both_aug = dual_support(august,cutoff)
    calibration = train & both_aug & (august.reference>0)
    print(f'Fitting HH and HH–HV logistic model on {calibration.sum():,} August calibration cells.',flush=True)
    logistic = logit_fit(predictors(august)[calibration],august.reference[calibration]==1)
    freeze = {'training_site':'Lago Grande de Curuai, Brazil','training_epoch':'2026-08-13','calibration_grid_m':CELL,
        'calibration_block_m':SPATIAL_BLOCK,'spatial_buffer_cells':1,'reference_purity_minimum':REFERENCE_MINIMUM_FRACTION,
        'logical_models_frozen_before_opening_october':True,'threshold':threshold,'logistic':logistic,
        'training_input':august.source,'score_units':'Unitless class-balanced model scores. No probability calibration.'}
    save_json(args.out/'frozen-model.json',freeze)

    print('Scoring the frozen August models on the unseen October epoch.',flush=True)
    october = load('october','20261012',args.october_h5)
    if not np.array_equal(august.x,october.x) or not np.array_equal(august.y,october.y):
        raise ValueError('Curuai epochs must have exactly matching analysis grid centres')
    if any(august.source[field]!=october.source[field] for field in ('track_number','frame_number','pass_direction','look_direction')):
        raise ValueError('Epochs do not share the expected NISAR acquisition geometry')
    both_oct = dual_support(october,cutoff)
    base_aug,base_oct = baseline_support(august,cutoff),baseline_support(october,cutoff)
    hh_aug,hh_oct = august.hh<=cutoff,october.hh<=cutoff
    ml_aug,ml_oct = logistic_classes(logistic,august,both_aug),logistic_classes(logistic,october,both_oct)
    common = [('HH threshold',hh_aug,hh_oct),('HH and HH–HV logistic',ml_aug,ml_oct)]
    paired_clear = held_out & (august.reference>0) & (october.reference>0)
    ref_loss = (august.reference==1)&(october.reference==2)
    ref_gain = (august.reference==2)&(october.reference==1)
    metrics = [
        score('August spatial holdout','QGIS HH threshold','exposed water',august.reference==1,hh_aug,held_out&(august.reference>0),base_aug),
        score('October temporal holdout · spatial blocks withheld','QGIS HH threshold','exposed water',october.reference==1,hh_oct,held_out&(october.reference>0),base_oct),
        score('August to October · paired spatial holdout','QGIS HH threshold','exposed water loss',ref_loss,hh_aug&~hh_oct,paired_clear,base_aug&base_oct),
        score('August to October · paired spatial holdout','QGIS HH threshold','exposed water gain',ref_gain,~hh_aug&hh_oct,paired_clear,base_aug&base_oct),
    ]
    aug_strict = both_aug & (august.hh_net_snr_db>=3) & (august.hv_net_snr_db>=3)
    oct_strict = both_oct & (october.hh_net_snr_db>=3) & (october.hv_net_snr_db>=3)
    for model,first,last in common:
        for label,reference,predicted,eligible,support in [
            ('August spatial holdout · common dual-pol support',august.reference,first,held_out,both_aug),
            ('October temporal holdout · all clear blocks',october.reference,last,True,both_oct),
            ('October temporal holdout · spatial blocks withheld · common dual-pol support',october.reference,last,held_out,both_oct),
            ('August spatial holdout · both net SNR ≥3 dB',august.reference,first,held_out,aug_strict),
            ('October temporal holdout · spatial blocks withheld · both net SNR ≥3 dB',october.reference,last,held_out,oct_strict),
        ]:
            metrics.append(score(label,model,'exposed water',reference==1,predicted,eligible&(reference>0),support))
        for name,target,predicted in (('exposed water loss',ref_loss,first&~last),('exposed water gain',ref_gain,~first&last)):
            metrics.append(score('August to October · paired spatial holdout · common dual-pol support',model,name,target,predicted,paired_clear,both_aug&both_oct))

    artifacts = []
    for name, epoch, support, hh_prediction, ml_prediction in [('august',august,both_aug,hh_aug,ml_aug),('october',october,both_oct,hh_oct,ml_oct)]:
        for classifier,prediction in [('threshold',hh_prediction),('logistic',ml_prediction)]:
            state = np.select([~support,prediction],[0,1],default=2).astype(np.uint8)
            artifacts.append(write_raster(args.out/f'{name}-{classifier}-exposed-water.tif',state,epoch,'0 unassessable, 1 exposed-water flag, 2 no exposed-water flag'))
    for classifier,first,last in [('threshold',hh_aug,hh_oct),('logistic',ml_aug,ml_oct)]:
        temporal_support = both_aug & both_oct
        difference = np.select([~temporal_support,first&~last,~first&last],[0,1,2],default=3).astype(np.uint8)
        artifacts.append(write_raster(args.out/f'paired-{classifier}-exposed-water-change.tif',difference,august,'0 missing support, 1 loss flag, 2 gain flag, 3 no flag of change'))
    with (args.out/'metrics.csv').open('w',encoding='utf-8',newline='') as file:
        writer = csv.DictWriter(file,fieldnames=list(metrics[0]))
        writer.writeheader()
        writer.writerows(metrics)
    save_json(args.out/'research-report.json',{'scope':'Exposed-water screen at one Curuai site and two same-track dates',
        'train_date':'2026-08-13','unseen_date':'2026-10-12','method_origin':'Set up QGIS wetland validation and seasonal scripts',
        'input_unit':'Linear gamma0 power from scientific GCOV; model features in dB','class_source':'Sentinel-2 exposed-water proxy',
        'native_quality':'Correct subswath, fully focused, no sampled anomaly, positive looks and finite power/NESZ for every 20 m posting per 60 m cell.',
        'classification_support':'HH NESZ at least 3 dB below calibrated August cutoff. Common model comparison uses valid HH/HV. Optional stricter scoring requires net SNR at least 3 dB in both.',
        'threshold_state_codes':MASK_CODES,'display_water_color':'#2b5b6c','calibration':freeze,'evaluation_input':october.source,
        'frozen_spatial_holdout_cells':int(held_out.sum()),'all_clear_paired_holdout_cells':int(paired_clear.sum()),
        'metrics':metrics,'files':artifacts,
        'limitations':['The optical reference is a same-day SCL and spectral agreement proxy acquired about five hours after radar.',
            'The classifier uses measured gamma0 including thermal noise where correction is false. The stricter net SNR rows quantify sensitivity to that choice.',
            'Temporal transfer is evaluated at the same site, with a stricter test on spatial blocks withheld from August.',
            'Clouds, mixed optical cells and radar abstention change evaluated coverage. No field confirmation, habitat classification, inundation under canopy or drought attribution.']})
    print(json.dumps({'cutoff_db':cutoff,'august_train_cells':int(calibration.sum()),'logistic_coefficients':logistic['coefficients'],
        'score_rows':len(metrics),'scientific_products':len(artifacts),'report':str(args.out/'research-report.json')}),flush=True)


if __name__=='__main__':
    main()
