"""Publish exact reference boundaries and supported radar agreement on the map."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from shutil import copyfile

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes
from rasterio.warp import transform_geom
from shapely import make_valid
from shapely.geometry import shape

from analyze_nisar_matrix import digest


OIL = '#e7330d'
WETLAND = '#f38228'
ASF = 'https://nisar-docs.asf.alaska.edu/gcov/'
NOAA = 'https://www.ospo.noaa.gov/data/ocean/marinepollution/2026/'


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def entry(identifier, label, color, code, dash='solid'):
    return {'id': identifier, 'label': label, 'color': color, 'code': code, 'outline_style': dash}


def evidence(identifier, title, kind, url, notes, **receipts):
    return {'id': identifier, 'title': title, 'kind': kind, 'url': url, 'notes': notes, **receipts}


def polygon_parts(geometry):
    if geometry.geom_type in {'Polygon', 'MultiPolygon'}:
        yield geometry
    elif geometry.geom_type == 'GeometryCollection':
        for part in geometry.geoms:
            yield from polygon_parts(part)


def feature(geometry, class_id):
    return {'type': 'Feature', 'geometry': geometry, 'properties': {'class_id': class_id}}


def categorical_features(path, class_id):
    with rasterio.open(path) as src:
        selected = src.read(1) == 1
        if not selected.any() or not src.crs.is_projected:
            raise ValueError(f'No marked projected science cells in {path.name}.')
        native = [part.__geo_interface__ for geometry, code in shapes(selected.astype('uint8'), mask=selected,
                  transform=src.transform, connectivity=4) if code == 1
                  for part in polygon_parts(make_valid(shape(geometry)))]
        roundtrip = rasterize(native, out_shape=src.shape, transform=src.transform, fill=0, dtype='uint8')
        if not np.array_equal(roundtrip, selected):
            raise ValueError(f'Vectorization changed the selected native cells in {path.name}.')
        return [feature(transform_geom(src.crs, 'EPSG:4326', geometry, precision=7), class_id)
                for geometry in native], int(selected.sum())


def source_features(path, class_id):
    return [feature(part.__geo_interface__, class_id) for item in read(path)['features'] if item.get('geometry')
            for part in polygon_parts(make_valid(shape(item['geometry'])))]


def record(identifier, site, observation, title, summary, classes, features, refs, data, science=None):
    if not features:
        raise ValueError(f'Missing real outlines for {identifier}.')
    layer = {'id': identifier, 'site_id': site, 'observation_id': observation, 'polarization': 'HH',
             'title': title, 'summary': summary, 'classes': classes, 'evidence_ids': refs,
             'outlines': {'type': 'FeatureCollection', 'features': features}}
    if science is not None:
        target = data / 'science' / (identifier + '.tif')
        copyfile(science, target)
        layer.update(science_url=f'/science/{target.name}', science_size_bytes=target.stat().st_size,
                     science_sha256=digest(target))
    return layer


def build(data, uae, wetland, oil):
    sources = []
    layers = []
    waterfront = uae / 'GEM_NO_2_oil_and_wetland_references.geojson'
    prior = read(waterfront)['features']
    if [row['properties']['name'] for row in prior] != ['Falcon Eye slick', 'Khor Al Beidah mapped mangroves']:
        raise ValueError('UAE reference geometry identities changed.')
    classes = [entry('slick-reference', 'Later independent slick outline', OIL, 1, 'dashed'),
               entry('mangrove-inventory', 'Mapped mangrove habitat', WETLAND, 2)]
    features = [feature(part.__geo_interface__, grouping['id']) for row, grouping in zip(prior, classes)
                for part in polygon_parts(make_valid(shape(row['geometry'])))]
    layers.append(record('nisar-uae-oil-wetland-review', 'uae-coastal-review', 'nisar-uae-coast-20261021',
                   'Slick and mangrove reference outlines',
                   'Red traces the independent slick interpretation about 39 minutes later. Orange traces inventoried mangroves. The NISAR oil screen assessed 172 of 46,942 reference cells and flagged zero.',
                   classes, features, ['nisar-uae-coast-20261021-source', 'uae-falcon-eye-reference',
                                      'nisar-uae-coast-20261021-wetland'], data))
    sources.append(evidence('uae-falcon-eye-reference', 'Falcon Eye · later slick outline', 'Independent analyst interpretation',
                     'https://www.ess-uae.me/post/satellite-oil-spill-monitoring-uae-umm-al-quwain',
                     'Outline was registered against independent Sentinel-1D radar. It shows a later observation and can move between passes.',
                     geometry_source_sha256=digest(waterfront)))

    october = wetland / 'curuai-october'
    source = read(october / 'summary.json')
    scores = source['counts']['threshold']['map_crop_withheld_blocks']
    subset = october / 'threshold-map-withheld-true-positive.tif'
    features, n = categorical_features(subset, 'water-agreement')
    if n != scores['tp']:
        raise ValueError('Curuai published water outlines and withheld test counts differ.')
    optical = source['source']
    sources.append(evidence('curuai-october-optical-agreement', 'Curuai · independent exposed-water check',
                            'Same-day optical water proxy', optical['source_item'], source['scope'],
                            assessed_cells=scores['assessed_cells'], shared_water_cells=n,
                            false_water_cells=scores['fp'], missed_water_cells=scores['fn'],
                            agreement_iou=scores['iou'], source_acquired_at=optical['source_acquired_at'],
                            reference_sha256=optical['reference_sha256'],
                            frozen_model_sha256=source['frozen_model_sha256']))
    layers.append(record('nisar-curuai-water-agreement-20261012-hh', 'curuai-nisar', 'nisar-curuai-20261012',
                   'Exposed water agreed by radar and optics',
                   f'The October radar screen and same-day optical reference agreed on {n:,} exposed-water cells in withheld blocks here. It also flagged {scores["fp"]:,} cells the optical reference classified as other surface. The outlines show agreement only.',
                   [entry('water-agreement', 'Radar and optical water agreement', WETLAND, 1)], features,
                   ['asf-nisar-gcov', 'curuai-october-optical-agreement'], data, subset))

    salt_lake = wetland / 'burgas-external-evaluation'
    optics = salt_lake / 'optical-water.tif'
    salt_review = read(salt_lake / 'summary.json')
    features, n = categorical_features(optics, 'optical-water')
    if n != salt_review['reference']['coarse_class_counts'][1] or salt_review['reference']['combined_support'] != 0:
        raise ValueError('The optical outline no longer agrees with the evaluated lake crop.')
    sources.append(evidence('atanasovsko-optical-20260713', 'Atanasovsko Lake · later optical water reference',
                            'Independent optical water proxy',
                            salt_review['input_sources']['optical']['reference_item'],
                            'The optical measurements were made 16 hours after NISAR. The frozen Curuai model withheld the radar cells because the background instrument noise was too high for its cutoff.',
                            independent_water_cells=n, optical_lag_seconds=salt_review['optical_lag_seconds'],
                            reference_sha256=digest(optics)))
    layers.append(record('nisar-atanasovsko-optical-review-20260712-hh', 'atanasovsko-nisar', 'nisar-atanasovsko-20260712',
                         'Atanasovsko Lake · later optical reference',
                         f'The dashed outlines trace {n:,} cells classified as exposed water in an independent optical image 16 hours later. All radar cells failed the frozen model’s noise check.',
                         [entry('optical-water', 'Later optical exposed water', WETLAND, 1, 'dashed')], features,
                         ['asf-nisar-gcov', 'atanasovsko-optical-20260713'], data))

    observed = read(oil / 'observed-trials' / 'observed-return-results.json')
    fit = observed['calibrated']
    sources.append(evidence('nisar-observed-oil-screen', 'NISAR · test of measured dark water', 'Held-out observed-return test', ASF,
                            'The observed-HH rule compares each cell with open water on both sides. Oman alone set the cutoff. This test has no pixel-level noise calibration for oil material.',
                            threshold_db=fit['threshold_db'], calibration_cells=fit['calibration_cells'], algorithm_sha256=observed['algorithm_sha256']))
    definitions = (
        ('gulf-eugene-nisar', 'nisar-eugene-20260827', 'nisar-eugene-observed-review-20260827-hh',
         'Eugene Island · NOAA comparison', [('20260827_1210_RADARSAT2_OIL', 'eugene-noaa-20260827')]),
        ('eloi-bay-nisar', 'nisar-eloi-20261012', 'nisar-eloi-reference-review-20261012-hh',
         'Eloi Bay · later NOAA boundaries', [('20261012_1626_LANDSAT8_OIL', 'eloi-noaa-20261012-a'),
                                             ('20261012_1626b_LANDSAT8_OIL', 'eloi-noaa-20261012-b')]),
    )
    for site, obs, slug, title, references in definitions:
        trial = observed['held_out_sites'][site]
        counts = trial['counts']
        features = []
        refs = ['nisar-observed-oil-screen']
        classes = [entry('noaa-reference', 'Independent NOAA possible-oil outline', OIL, 2, 'dashed')]
        for filename, source_id in references:
            path = oil / (filename + '.geojson')
            features.extend(source_features(path, 'noaa-reference'))
            sources.append(evidence(source_id, 'NOAA · mapped possible oil', 'Independent satellite analyst outline',
                                    'https://www.ospo.noaa.gov/products/ocean/marinepollution/archive/product.html?id=' + filename,
                                    'The analyst outline is a possible-oil reference. Its timing and source sensor differ from the plotted NISAR measurement.',
                                    outline_url=NOAA + path.name, geometry_sha256=digest(path), offset_minutes=trial['offset_minutes']))
            refs.append(source_id)
        science = None
        if trial['map_whole_cell_agreement']:
            science = oil / 'observed-trials' / (site + '-observed-overlap.tif')
            matches, n = categorical_features(science, 'radar-agreement')
            if n != trial['map_whole_cell_agreement']:
                raise ValueError('Published HH agreement and assessed cells differ.')
            features.extend(matches)
            classes.append(entry('radar-agreement', 'Measured dark water matching the outline', OIL, 1))
        if site == 'gulf-eugene-nisar':
            summary = (f'NISAR recorded darker water in {counts["reference_flagged_cells"]:,} of {counts["reference_assessed_cells"]:,} assessed cells in the independent outline. '
                       f'RADARSAT-2 passed 43 minutes later. Solid lines and the GeoTIFF show the {trial["map_whole_cell_agreement"]:,} agreement cells in this map crop. The material is unconfirmed.')
        else:
            summary = 'The dashed boundaries come from NOAA’s Landsat 8 observations more than five hours later. Parts extend beyond the measured radar crop. Moving water and possible slick drift limit position comparisons across this gap.'
        layers.append(record(slug, site, obs, title, summary, classes, features, refs, data, science))
    return {'schema_version': 1, 'layers': layers, 'evidence': sources}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    for name in ('data', 'uae', 'wetland', 'oil'):
        cli.add_argument('--' + name, required=True, type=Path)
    args = cli.parse_args()
    result = build(args.data, args.uae, args.wetland, args.oil)
    target = args.data / 'nisar-detections.json'
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n', encoding='utf-8')
    temp.replace(target)
    for layer in result['layers']:
        print(layer['id'], len(layer['outlines']['features']), [x['id'] for x in layer['classes']])


if __name__ == '__main__':
    main()
