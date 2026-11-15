/* Shared SAR selection, provenance, and read-only assistant interface. */
(() => {
  'use strict';

  const channels = ['VV', 'VH', 'HH', 'HV'];
  const palette = {land: '#1b3c34', outlines: '#6c6853', extent: '#f38228', active: '#e7330d', water: '#2b5b6c'};
  const reviewDash = {solid: null, dashed: '7 5', dotted: '2 4'};
  const sciencePath = /^\/science\/[a-z0-9][a-z0-9_-]{0,119}\.tif$/;
  const previewPath = /^\/sar\/[a-z0-9][a-z0-9_-]{0,119}\.(?:png|webp)$/;
  const providerPreviewPath = /^\/api\/products\/NISAR_[A-Za-z0-9_-]{6,290}\/previews\/[0-9]+$/;
  const analysisModes = {
    change: {label: 'Radar return change', min: 2, max: 2},
    time_series: {label: 'Radar return over time', min: 2, max: 6},
    wetland: {label: 'Wetland water and vegetation', min: 1, max: 1, channel: 'HH'},
    oil: {label: 'Dark-water research screen', min: 1, max: 1, channel: 'HH'},
  };
  const analysisBriefs = {
    change: ['Places where the radar signal got stronger or weaker.', channel => `Calibrated ${channel} radar measurements from the same pixels on two dates.`],
    time_series: ['Whether radar readings tend to rise or fall across the selected dates.', channel => `Calibrated ${channel} radar measurements from the same usable area on every date.`],
    wetland: ['Likely open water, uncertain edges, and signs of water under vegetation when a drier reference is available.', () => 'Calibrated HH and HV radar measurements. The flooded-vegetation check also needs a comparable drier image and a vegetation map.'],
    oil: ['Dark patches on the water worth checking for possible oil.', () => 'Calibrated HH radar measurements, compared with nearby water on both sides of the patch.'],
  };
  const topics = {wetlands: 'Wetlands', wetland_flood: 'Wetland flooding', wetland_drought: 'Wetland drought', oil_spill: 'Oil spill investigation'};
  const guidance = {
    oil_spill: 'Select a reference period and check radar anomalies against wind, surface conditions, and independent incident records.',
    wetlands: 'Compare NISAR acquisitions from the same track and polarization. Review the study boundary and observation dates in Source audit.',
    wetland_flood: 'Compare equivalent radar geometry before and after the event. Use independent records to interpret inundation.',
    wetland_drought: 'Compare equivalent seasons and check independent rainfall or water-level records before interpreting changes.',
  };
  const $ = id => document.getElementById(id);
  const safeText = value => value == null || value === '' ? '—' : String(value);
  const make = (tag, className, label) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (label != null) element.textContent = label;
    return element;
  };
  const button = (label, className, click) => {
    const element = make('button', className, label);
    element.type = 'button';
    element.addEventListener('click', click);
    return element;
  };
  function httpURL(raw) {
    if (typeof raw !== 'string' || !raw) return null;
    try {
      const url = new URL(raw, location.href);
      if (url.username || url.password || (url.origin !== location.origin && url.protocol !== 'https:')) return null;
      if (!['https:', 'http:'].includes(url.protocol)) return null;
      return url;
    } catch {
      return null;
    }
  }
  function sourceLink(raw, label) {
    const url = httpURL(raw);
    if (!url) return null;
    const link = make('a', '', label);
    link.href = url.href;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    return link;
  }
  function wikipediaLink(raw, label) {
    const url = httpURL(raw);
    if (!url || !['https://simple.wikipedia.org', 'https://en.wikipedia.org'].includes(url.origin)) return null;
    const article = url.pathname.startsWith('/wiki/') && !url.search;
    const search = ['/w/index.php', '/wiki/Special:Search'].includes(url.pathname) && url.searchParams.size === 1
      && url.searchParams.get('search')?.trim();
    return article || search ? sourceLink(url.href, label) : null;
  }
  function localRasterURL(raw, pattern) {
    const url = httpURL(raw);
    return url?.origin === location.origin && url.href === `${url.origin}${url.pathname}` && pattern.test(url.pathname) ? url.href : null;
  }
  function validBounds(box) {
    return Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)
      && box[0] < box[2] && box[1] < box[3]
      && box[0] >= -180 && box[2] <= 180 && box[1] >= -90 && box[3] <= 90;
  }
  function leafletBounds([west, south, east, north]) {
    return [[south, west], [north, east]];
  }
  function validImageBounds(bounds) {
    return Array.isArray(bounds) && bounds.length === 2
      && bounds.every(corner => Array.isArray(corner) && corner.length === 2)
      && validBounds([bounds[0][1], bounds[0][0], bounds[1][1], bounds[1][0]]);
  }
  function reviewOutlines(value, classIds) {
    if (value?.type !== 'FeatureCollection' || !Array.isArray(value.features) || !value.features.length) return null;
    const position = point => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite)
      && Math.abs(point[0]) <= 180 && Math.abs(point[1]) <= 85.05113;
    const ring = path => Array.isArray(path) && path.length >= 4 && path.every(position)
      && path[0].every((coordinate, index) => coordinate === path.at(-1)[index]);
    const polygon = paths => Array.isArray(paths) && paths.length && paths.every(ring);
    for (const feature of value.features) {
      const geometry = feature?.geometry;
      if (feature?.type !== 'Feature' || !classIds.has(feature.properties?.class_id)) return null;
      if (geometry?.type === 'Polygon' && polygon(geometry.coordinates)) continue;
      if (geometry?.type === 'MultiPolygon' && Array.isArray(geometry.coordinates)
          && geometry.coordinates.length && geometry.coordinates.every(polygon)) continue;
      return null;
    }
    return value;
  }
  function validReviewReceipt(layer, kind) {
    const size = layer[`${kind}_size_bytes`];
    const hash = layer[`${kind}_sha256`];
    return Number.isSafeInteger(size) && size > 0 && typeof hash === 'string' && /^[a-f0-9]{64}$/.test(hash);
  }
  function missionOf(row) {
    const mission = [row.mission, row.sensor, row.product].filter(Boolean).join(' ').toLowerCase();
    return mission.includes('nisar') ? 'nisar' : 'other';
  }
  function bandOf(row) {
    return /^([LS])(?:\b|$)/i.exec(row.wavelength || '')?.[1].toUpperCase() || '';
  }
  function productOf(row) {
    return /\bGCOV\b/i.test(row.product || '') ? 'GCOV' : '';
  }
  function mapAsset(row, pol) {
    if (!row) return null;
    const candidate = row.channels?.[pol];
    const entries = candidate ? [candidate] : [];
    if (!pol || row.polarization === pol || row.default_polarization === pol) entries.push(row);
    for (const asset of entries) {
      const url = httpURL(asset.image_url);
      const bounds = asset.image_bounds;
      if (url && validImageBounds(bounds)
          && (!asset.image_crs || asset.image_crs === 'EPSG:3857')
          && (!asset.image_epsg || Number(asset.image_epsg) === 3857)) {
        return {kind: 'image', url: url.href, bounds, units: asset.units, range: asset.display_range_db};
      }
      const tileURL = asset.tiles_url || asset.tile_url;
      if (httpURL(tileURL)) return {kind: 'tile', url: tileURL, units: asset.units, range: asset.display_range_db};
    }
    return null;
  }
  function bestChannel(row, preferred = null) {
    if (!row) return null;
    if (preferred && mapAsset(row, preferred)) return preferred;
    const mapped = channels.find(pol => mapAsset(row, pol));
    if (mapped) return mapped;
    return preferred && row.channels?.[preferred] ? preferred
      : channels.find(pol => ['catalogue', 'preview', 'measured'].includes(stateOf(row.channels?.[pol]).key)) || null;
  }
  function stateOf(channel) {
    if (!channel) return {key: 'unknown', label: 'Unknown'};
    const raw = String(channel.status || '').toLowerCase().replace(/[ -]+/g, '_');
    if (['not_acquired', 'missing', 'absent', 'not_in_product', 'not_available', 'unavailable'].includes(raw)) {
      return {key: 'missing', label: 'Unavailable'};
    }
    if (['failed', 'temporarily_unavailable', 'error'].includes(raw)) return {key: 'error', label: 'Load failed'};
    if ((channel.scientific === true || channel.science_url || channel.image_url)
        && ['verified', 'validated', 'calibrated', 'science_ready', 'calibrated_layer_verified'].includes(raw)) {
      return {key: 'measured', label: 'Verified raster'};
    }
    if (channel.preview_url || channel.tile_url || channel.tiles_url || channel.image_url
        || ['preview', 'browse', 'preview_available', 'display_only'].includes(raw)) {
      return {key: 'preview', label: 'Display image'};
    }
    if (['catalogue', 'catalog', 'catalogue_only', 'metadata_only', 'advertised', 'available', 'present', 'metadata'].includes(raw)) {
      return {key: 'catalogue', label: 'Catalogue'};
    }
    return {key: 'unknown', label: 'Unknown'};
  }
  let baseGeography;
  function loadBaseGeography() {
    if (!baseGeography) {
      baseGeography = Promise.all(['basemap-land.geojson', 'basemap-lakes.geojson'].map(async name => {
        const response = await fetch('data/' + name);
        if (!response.ok) throw new Error('Base geometry is unavailable.');
        const geometry = await response.json();
        if (geometry.type !== 'FeatureCollection') throw new Error('Base geometry is unavailable.');
        return geometry;
      })).catch(error => {
        baseGeography = null;
        throw error;
      });
    }
    return baseGeography;
  }
  function createBasemaps(map, onStatus = () => {}) {
    const L = window.L;
    const bases = {
      'Study palette': L.layerGroup(),
      'OpenStreetMap': L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
      }),
      'Esri World Imagery': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19, attribution: 'Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community',
      }),
      'Esri Light Gray': L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}', {
        maxNativeZoom: 16, maxZoom: 19, attribution: '© Esri, HERE, Garmin, © OpenStreetMap contributors',
      }),
      'No background': L.layerGroup(),
    };
    const control = L.control.layers(bases, null, {position: 'topright'}).addTo(map);
    const study = bases['Study palette'];
    study.addTo(map);
    map.getContainer().classList.add('rz-study-palette');
    map.createPane('rzBase');
    map.getPane('rzBase').style.zIndex = 190;
    loadBaseGeography().then(([land, lakes]) => {
      const options = color => ({pane: 'rzBase', interactive: false, style: {weight: 0, fillColor: color, fillOpacity: 1}});
      L.geoJSON(land, {...options(palette.land), attribution: '<a href="https://www.naturalearthdata.com/">Natural Earth</a> · generalized cartography'}).addTo(study);
      L.geoJSON(lakes, options(palette.water)).addTo(study);
    }).catch(() => {
      if (map.hasLayer(study)) {
        map.removeLayer(study);
        bases.OpenStreetMap.addTo(map);
        onStatus('The study palette could not load. Showing OpenStreetMap.');
      }
    });
    const failures = new Map();
    map.on('baselayerchange', event => {
      failures.set(event.name, 0);
      map.getContainer().classList.toggle('rz-study-palette', event.name === 'Study palette');
      onStatus(event.name === 'No background' ? 'Showing geographic overlays without background tiles.' : '');
    });
    for (const [name, layer] of Object.entries(bases)) {
      if (name === 'No background') continue;
      layer.on('tileload', () => failures.set(name, 0));
      layer.on('tileerror', () => {
        if (!map.hasLayer(layer)) return;
        const errors = (failures.get(name) || 0) + 1;
        failures.set(name, errors);
        if (errors < 5) return;
        const replacement = name === 'OpenStreetMap' ? 'Esri Light Gray' : 'No background';
        map.removeLayer(layer);
        bases[replacement].addTo(map);
        onStatus(`${name} tiles could not load. Showing ${replacement.toLowerCase()}.`);
      });
    }
    return {control, select(name) {
      const next = bases[name];
      if (!next || map.hasLayer(next)) return;
      for (const layer of Object.values(bases)) {
        if (map.hasLayer(layer)) map.removeLayer(layer);
      }
      next.addTo(map);
      map.getContainer().classList.toggle('rz-study-palette', next === study);
      onStatus('');
    }};
  }

  const blank = () => ({
    topic: '', site_id: '', bbox: null, start: '', end: '', mission: 'nisar', band: 'all', product: 'GCOV',
    polarizations: [], selected_ids: [], active_id: '', channel: null, results_ids: null, showUnavailableChannels: false,
    change_layer_id: '', review_layer_id: '', analysis_id: '',
  });
  const sites = new Map();
  const observations = new Map();
  const recordedObservationIds = new Set();
  const changeLayers = new Map();
  const recognitionLayers = new Map();
  const timeSeries = new Map();
  const analysisResults = new Map();
  const runtimeChangeIds = new Set();
  const runtimeReviewIds = new Set();
  const evidence = new Map();
  const glossaryEntries = new Map();
  const glossarySources = new Map();
  const settingsProfiles = new Map();
  const history = [];
  const messages = [];
  let hiddenReviews = new Set();
  let selection = blank();
  let view = '';
  let map, overlayGroup, dataOverlay;
  let basemaps;
  let eventBasemapSite = '';
  let overlayURL = '';
  let changeOverlay = null;
  let changeOverlayID = '';
  let changeStatus = '';
  let reviewOverlay = null;
  let reviewOverlayID = '';
  let reviewStatus = '';
  let seriesResizeObserver;
  let pendingFit = false;
  let mapLayerStatus = '';
  let mapBaseStatus = '';
  let picker = '';
  let status = '';
  let statusTone = '';
  let bootstrap;
  let snapshotMode = false;
  let previewAnalysisAvailable = false;
  let previewTask;
  let renderedPreview;
  let analysisAvailable = false;
  let analysisModelObservations = null;
  let datasetSynopses = {};
  let analysisDraft;
  let analysisTask;
  let searchController;
  let generation = 0;
  let searchBusy = false;
  let sending = false;
  let sourceFocus = '';
  let displayEnabled = false;
  let displayOpacity = 1;
  let selectionRevision = 0;
  let experimentsRequest;
  let focusedExperiment = '';
  let subsetHandoff;
  let settingsCaseId = '';
  let manualMethod = '';

  function currentSite() { return sites.get(selection.site_id); }
  function eventReference() { return currentSite()?.event_reference; }
  function reviewPoints(site) {
    if (site?.reference_acquisition_date
        && formatDate(activeRow()?.acquired_at) !== site.reference_acquisition_date) return [];
    const points = site?.review_points;
    if (!Array.isArray(points)) return [];
    return points.filter(Boolean).map(point => ({...point, latitude: point.lat ?? point.latitude,
      longitude: point.lon ?? point.longitude, note: point.notes ?? point.note})).filter(point =>
      Number.isFinite(point.latitude) && Math.abs(point.latitude) <= 85.0511 &&
      Number.isFinite(point.longitude) && Math.abs(point.longitude) <= 180 &&
      ['oil_spill', 'wetlands'].includes(point.topic) && typeof point.label === 'string' && point.label.trim());
  }
  function reviewPointType(point) {
    return point.topic === 'oil_spill' ? 'Oil review point' : 'Wetland review point';
  }
  function reviewPointReturn(point) {
    return Number.isFinite(point.hh_db) ? `${point.hh_db.toFixed(2)} dB` : null;
  }
  function fitsTopic(site, topic) {
    if (!topic) return true;
    if (topic === 'oil_spill') return site.topic === 'oil_spill';
    return typeof site.topic === 'string' && site.topic.startsWith('wetland');
  }
  function activeRow() { return observations.get(selection.active_id); }
  function changeMatchesSelection(layer, ids) {
    return layer.site_id === selection.site_id && layer.polarization === selection.channel
      && [layer.earlier_id, layer.later_id].includes(selection.active_id)
      && ids.has(layer.earlier_id) && ids.has(layer.later_id);
  }
  function activeChange() {
    return displayEnabled ? changeLayers.get(selection.change_layer_id) : null;
  }
  function selectedAnalysis(rows = rowsFor()) {
    const result = analysisResults.get(selection.analysis_id);
    if (!result || result.site_id !== selection.site_id || result.polarization !== selection.channel
        || !result.observation_ids.includes(selection.active_id)) return null;
    const visible = new Set(rows.map(row => row.id));
    return result.observation_ids.every(id => visible.has(id)) ? result : null;
  }
  function availableChange(rows) {
    const ids = new Set(rows.map(row => row.id));
    const requested = selectedAnalysis(rows)?.change_layer;
    if (requested && changeMatchesSelection(requested, ids)) return requested;
    return [...changeLayers.values()].find(layer => !runtimeChangeIds.has(layer.id) && changeMatchesSelection(layer, ids));
  }
  function reviewMatchesSelection(layer) {
    return layer.site_id === selection.site_id && layer.observation_id === selection.active_id
      && layer.polarization === selection.channel;
  }
  function availableReview() {
    const requested = selectedAnalysis()?.recognition_layer;
    if (requested && reviewMatchesSelection(requested)) return requested;
    return [...recognitionLayers.values()].find(layer => !runtimeReviewIds.has(layer.id) && reviewMatchesSelection(layer));
  }
  function activeReview() {
    const review = recognitionLayers.get(selection.review_layer_id);
    return displayEnabled && review && reviewMatchesSelection(review) ? review : null;
  }
  function availableSeries(rows = rowsFor()) {
    const series = selectedAnalysis(rows)?.time_series || timeSeries.get(`${selection.site_id}:${selection.channel}`);
    if (!series) return null;
    const visible = new Set(rows.map(row => row.id));
    const points = series.points.filter(point => visible.has(point.observation_id));
    return points.length >= 2 ? {...series, points} : null;
  }
  function keepEvidence() { sourceFocus = ''; }
  function saveSelection() {
    cancelAnalysisRequest();
    selectionRevision++;
    history.push({selection: structuredClone(selection), displayEnabled, hiddenReviews: new Set(hiddenReviews)});
    if (history.length > 25) history.shift();
  }
  function cancelSearch() {
    generation++;
    searchController?.abort();
    searchBusy = false;
  }
  function updateStatus(message, tone = '') {
    status = message;
    statusTone = tone;
    $('rzStatus').textContent = message;
    $('rzStatus').dataset.tone = tone;
  }
  function validSeries(series) {
    if (!series || series.units !== 'dB' || !sites.has(series.site_id) || !channels.includes(series.polarization)
        || [series.id, series.title, series.summary, series.statistic].some(value => typeof value !== 'string' || !value.trim())
        || !Array.isArray(series.points) || series.points.length < 2 || !Array.isArray(series.evidence_ids)
        || !series.evidence_ids.length || series.evidence_ids.some(id => !evidence.has(id))) return null;
    const dates = new Set();
    const counts = new Set();
    for (const point of series.points) {
      const row = observations.get(point?.observation_id);
      const time = Date.parse(point?.acquired_at);
      if (row?.site_id !== series.site_id || stateOf(row.channels?.[series.polarization]).key !== 'measured'
          || !Number.isFinite(time) || time !== Date.parse(row.acquired_at) || dates.has(time)
          || !Number.isFinite(point.value) || !Number.isSafeInteger(point.valid_pixels) || point.valid_pixels <= 0) return null;
      dates.add(time);
      counts.add(point.valid_pixels);
    }
    if (counts.size !== 1) return null;
    return {...series, points: [...series.points].sort((a, b) => Date.parse(a.acquired_at) - Date.parse(b.acquired_at))};
  }
  function merge(raw, fallbackSite = '', origin = 'live') {
    mergeGlossary(raw.glossary);
    for (const site of raw.sites || []) {
      if (!site?.id || !validBounds(site.bbox)) continue;
      const reference = site.event_reference;
      const outlines = reviewOutlines(reference?.outlines, new Set(['slick-reference']));
      const valid = outlines && [reference.title, reference.summary, reference.evidence_id]
        .every(value => typeof value === 'string' && value.trim())
        && /^\d{4}-\d{2}-\d{2}$/.test(reference.date);
      sites.set(site.id, {...site, event_reference: valid ? {...reference, outlines} : null});
    }
    for (const row of raw.observations || []) {
      if (!row?.id || missionOf(row) !== 'nisar' || productOf(row) !== 'GCOV'
          || typeof row.acquired_at !== 'string' || !Number.isFinite(Date.parse(row.acquired_at))) continue;
      const site = row.site_id || fallbackSite;
      const previous = observations.get(row.id);
      const catalogueOrigin = origin === 'assistant' ? previous?.catalogue_origin || 'live' : origin;
      const retainedChannels = {...previous?.channels, ...row.channels};
      for (const pol of channels) {
        if (previous?.channels?.[pol]?.science_url && !row.channels?.[pol]?.science_url) retainedChannels[pol] = previous.channels[pol];
      }
      observations.set(row.id, {...previous, ...row, channels: retainedChannels, site_id: site, catalogue_origin: catalogueOrigin});
      if (origin === 'recorded') recordedObservationIds.add(row.id);
    }
    for (const source of raw.evidence || []) {
      if (source?.id) evidence.set(source.id, source);
    }
    for (const layer of raw.change_layers || []) {
      const url = httpURL(layer?.image_url);
      if (!layer?.id || !sites.has(layer.site_id) || !channels.includes(layer.polarization)
          || !url || url.origin !== location.origin || !validImageBounds(layer.image_bounds)
          || (layer.image_epsg && Number(layer.image_epsg) !== 3857)
          || !Number.isFinite(layer.threshold_db) || layer.threshold_db <= 0
          || !Number.isSafeInteger(layer.paired_native_pixels) || layer.paired_native_pixels <= 0
          || !Number.isFinite(Date.parse(layer.earlier_date)) || !Number.isFinite(Date.parse(layer.later_date))) continue;
      const earlier = observations.get(layer.earlier_id);
      const later = observations.get(layer.later_id);
      if (earlier?.site_id !== layer.site_id || later?.site_id !== layer.site_id
          || formatDate(earlier.acquired_at) !== formatDate(layer.earlier_date)
          || formatDate(later.acquired_at) !== formatDate(layer.later_date)
          || Date.parse(layer.earlier_date) >= Date.parse(layer.later_date)
          || !mapAsset(earlier, layer.polarization) || !mapAsset(later, layer.polarization)) continue;
      if (!Array.isArray(layer.classes)) continue;
      const classes = ['brightened', 'darkened'].map(id => layer.classes.find(entry => entry?.id === id));
      if (classes.some(entry => !entry || !/^#[a-f0-9]{6}$/i.test(entry.color)
          || !Number.isSafeInteger(entry.native_pixels) || entry.native_pixels < 0)
          || classes.reduce((sum, entry) => sum + entry.native_pixels, 0) > layer.paired_native_pixels) continue;
      const science = typeof layer.science_units === 'string' && layer.science_units.trim()
        ? localRasterURL(layer.science_url, sciencePath) : null;
      changeLayers.set(layer.id, {...layer, classes, image_url: url.href, science_url: science});
    }
    for (const layer of Array.isArray(raw.recognition_layers) ? raw.recognition_layers : []) {
      const row = observations.get(layer?.observation_id);
      if (!row || !sites.has(layer.site_id) || row.site_id !== layer.site_id || !channels.includes(layer.polarization)
          || !mapAsset(row, layer.polarization) || stateOf(row.channels?.[layer.polarization]).key !== 'measured'
          || [layer.id, layer.title, layer.summary].some(item => typeof item !== 'string' || !item.trim())
          || !Array.isArray(layer.evidence_ids) || !layer.evidence_ids.length || layer.evidence_ids.some(id => !evidence.has(id))) continue;
      const classes = layer.classes;
      if (!Array.isArray(classes) || !classes.length || classes.some(entry => typeof entry?.id !== 'string' || !entry.id
          || typeof entry.label !== 'string' || !entry.label.trim() || !/^#[a-f0-9]{6}$/i.test(entry.color)
          || !Number.isSafeInteger(entry.code) || entry.code < 0
          || (entry.outline_style != null && !Object.hasOwn(reviewDash, entry.outline_style)))) continue;
      const ids = new Set(classes.map(entry => entry.id));
      if (ids.size !== classes.length || new Set(classes.map(entry => entry.code)).size !== classes.length) continue;
      const outlines = reviewOutlines(layer.outlines, ids);
      const image = localRasterURL(layer.image_url, previewPath);
      const science = localRasterURL(layer.science_url, sciencePath);
      if ((!outlines && !image) || (layer.outlines != null && !outlines)
          || (layer.image_url && (!image || !validImageBounds(layer.image_bounds) || !validReviewReceipt(layer, 'image')
            || (layer.image_epsg && Number(layer.image_epsg) !== 3857)))
          || (layer.science_url && (!science || !validReviewReceipt(layer, 'science')))) continue;
      recognitionLayers.set(layer.id, {...layer, image_url: image, science_url: science, outlines});
    }
    for (const rawSeries of Array.isArray(raw.time_series) ? raw.time_series : []) {
      const series = validSeries(rawSeries);
      if (series) timeSeries.set(`${series.site_id}:${series.polarization}`, series);
    }
  }
  async function request(path, body, signal) {
    const options = {signal};
    if (body) Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const response = await fetch(path, options);
    const type = response.headers.get('Content-Type') || '';
    if (!type.includes('application/json')) throw new Error('The explorer service returned an unexpected response.');
    const result = await response.json();
    if (!response.ok) throw new Error(result?.error?.message || `The explorer service returned HTTP ${response.status}.`);
    if (!result || typeof result !== 'object' || Array.isArray(result)) throw new Error('The explorer service returned incomplete data.');
    return result;
  }
  function ensureBootstrap() {
    if (bootstrap) return bootstrap;
    bootstrap = (async () => {
      let raw;
      try {
        raw = await request('/api/explorer/bootstrap');
        merge(raw, '', 'recorded');
        mergeSettings(raw.use_case_settings);
        previewAnalysisAvailable = raw.preview_analysis_available === true;
        analysisAvailable = raw.analysis_available === true;
        datasetSynopses = raw.dataset_synopses || {};
        if (raw.analysis_model_observations) {
          analysisModelObservations = new Map(['wetland', 'oil'].map(kind => [kind,
            new Set(Array.isArray(raw.analysis_model_observations[kind]) ? raw.analysis_model_observations[kind] : [])]));
        }
        snapshotMode = Boolean(raw.snapshot_only);
      } catch {
        try {
          raw = await request('data/non-sentinel-nisar.json');
          merge(raw, '', 'recorded');
          snapshotMode = true;
        } catch {
          bootstrap = null;
          updateStatus('Catalogue setup is unavailable. The Manual remains available.', 'error');
          render();
          return;
        }
      }
      if (!selection.site_id && !selectionRevision) {
        const initial = raw.default_selection || {};
        const prioritized = observations.get(initial.observation_id);
        const fallback = [...observations.values()].find(row => sites.has(row.site_id) && mapAsset(row, bestChannel(row)));
        const row = prioritized || fallback;
        const requested = new URLSearchParams(location.search).get('site');
        const siteId = sites.has(requested) ? requested
          : initial.site_id && sites.has(initial.site_id) ? initial.site_id : row?.site_id;
        if (siteId && sites.has(siteId)) {
          chooseSite(siteId, {remember: false, redraw: false});
          if (row && rowsFor().some(candidate => candidate.id === row.id)) {
            selection.active_id = row.id;
            selection.selected_ids = [row.id];
            selection.channel = bestChannel(row, initial.polarization);
            displayEnabled = Boolean(mapAsset(row, selection.channel));
          }
        }
      }
      if (!selection.site_id) updateStatus(snapshotMode ? 'Recorded catalogue. Choose a study area in Manual.' : 'Choose a study area in Manual.');
      render();
    })();
    return bootstrap;
  }
  function rowsFor(state = selection) {
    if (!state.site_id && !state.results_ids) return [];
    const chosen = state.results_ids ? new Set(state.results_ids) : null;
    return [...observations.values()].filter(row => {
      if (chosen && !chosen.has(row.id)) return false;
      if (!chosen && row.site_id !== state.site_id) return false;
      if (state.band !== 'all' && bandOf(row) !== state.band) return false;
      const acquisitionDay = new Date(row.acquired_at).toISOString().slice(0, 10);
      if ((state.start && acquisitionDay < state.start) || (state.end && acquisitionDay > state.end)) return false;
      if (!state.polarizations.length || state.showUnavailableChannels) return true;
      return state.polarizations.some(pol => ['catalogue', 'preview', 'measured'].includes(stateOf(row.channels?.[pol]).key));
    }).sort((a, b) => Date.parse(b.acquired_at) - Date.parse(a.acquired_at) || a.id.localeCompare(b.id));
  }
  function reconcile(rows = rowsFor()) {
    const ids = new Set(rows.map(row => row.id));
    selection.selected_ids = selection.selected_ids.filter(id => ids.has(id));
    if (!ids.has(selection.active_id)) {
      selection.active_id = selection.selected_ids[0] || rows[0]?.id || '';
      selection.channel = bestChannel(activeRow(), selection.channel);
      displayEnabled = Boolean(mapAsset(activeRow(), selection.channel));
    }
    if (selection.active_id && !selection.selected_ids.includes(selection.active_id)) {
      selection.selected_ids.unshift(selection.active_id);
    }
    const analysis = selectedAnalysis(rows);
    if (!analysis) selection.analysis_id = '';
    const change = changeLayers.get(selection.change_layer_id);
    if (change && (!changeMatchesSelection(change, ids) || (runtimeChangeIds.has(change.id) && analysis?.change_layer?.id !== change.id))) selection.change_layer_id = '';
    const review = availableReview();
    selection.review_layer_id = review && !selection.change_layer_id && !hiddenReviews.has(review.id) ? review.id : '';
  }
  function chooseSite(id, {remember = true, redraw = true} = {}) {
    const site = sites.get(id);
    if (!site) return;
    if (remember) saveSelection();
    cancelSearch();
    picker = '';
    keepEvidence();
    displayEnabled = false;
    const defaultEnd = site.search_end || site.end || '';
    const defaultStart = site.search_start || site.start || '';
    const topic = selection.topic && fitsTopic(site, selection.topic) ? selection.topic : site.topic || '';
    selection = {...blank(), topic, site_id: id, bbox: [...site.bbox], start: defaultStart, end: defaultEnd};
    const initial = rowsFor().find(row => formatDate(row.acquired_at) === site.reference_acquisition_date);
    if (initial) {
      selection.active_id = initial.id;
      selection.channel = bestChannel(initial, 'HH');
      displayEnabled = Boolean(mapAsset(initial, selection.channel));
    }
    reconcile();
    pendingFit = true;
    const rows = rowsFor();
    const recorded = rows.filter(row => row.catalogue_origin === 'recorded').length;
    updateStatus(site.event_reference && !rows.length ? 'Showing the published incident outline.'
      : rows.length ? `${rows.length} acquisition${rows.length === 1 ? '' : 's'}${recorded ? ` · ${recorded} recorded` : ''}` : 'Catalogue results will appear for this study area.');
    if (redraw) render();
  }
  function selectRow(id, pol = null) {
    if (!rowsFor().some(row => row.id === id) || (pol && !channels.includes(pol))) return;
    const row = observations.get(id);
    const nextChannel = pol || bestChannel(row, selection.channel);
    if (id === selection.active_id && selection.channel === nextChannel) return;
    saveSelection();
    keepEvidence();
    pendingFit = id !== selection.active_id;
    selection.active_id = id;
    selection.selected_ids = [id];
    selection.channel = nextChannel;
    displayEnabled = Boolean(mapAsset(row, nextChannel));
    render();
  }
  function showPicker(topic = 'wetlands') {
    picker = topic;
    render();
  }
  function pickableSites() {
    return [...sites.values()].filter(site => fitsTopic(site, picker || 'wetlands'));
  }
  async function searchLive({remember = true} = {}) {
    if (!validBounds(selection.bbox)) {
      updateStatus('Choose a study area before searching.', 'error');
      return;
    }
    if (selection.start && selection.end && selection.start > selection.end) {
      updateStatus('Enter an end date on or after the start date.', 'error');
      return;
    }
    if (remember) saveSelection();
    cancelSearch();
    const requestGeneration = generation;
    const controller = new AbortController();
    searchController = controller;
    searchBusy = true;
    const site = currentSite();
    const payload = {bbox: selection.bbox, start: selection.start, end: selection.end, mission: 'nisar', product: 'GCOV'};
    if (!payload.start) delete payload.start;
    if (!payload.end) delete payload.end;
    updateStatus('Searching the selected catalogue…');
    render();
    try {
      const raw = await request('/api/explorer/search', payload, controller.signal);
      if (requestGeneration !== generation) return;
      merge(raw, site?.id);
      selection.results_ids = (raw.observations || []).map(row => row.id);
      for (const row of rowsFor({...selection, results_ids: null})) {
        if (mapAsset(row, bestChannel(row)) && !selection.results_ids.includes(row.id)) selection.results_ids.push(row.id);
      }
      selection.showUnavailableChannels = false;
      reconcile();
      const counts = rowsFor().length;
      const suffix = raw.partial ? ' Some provider results are unavailable or limited.' : '';
      updateStatus(counts ? `${counts} live catalogue acquisitions for these filters.${suffix}` : `No acquisitions returned for these filters.${suffix}`);
    } catch (error) {
      if (requestGeneration !== generation || error.name === 'AbortError') return;
      updateStatus(`${error.message} The matching recorded acquisitions remain available.`, 'error');
    } finally {
      if (requestGeneration === generation) {
        searchBusy = false;
        render();
      }
    }
  }
  function undo() {
    const prior = history.pop();
    if (!prior) return;
    cancelSearch();
    cancelAnalysisRequest();
    selectionRevision++;
    picker = '';
    pendingFit = prior.selection.site_id !== selection.site_id;
    selection = prior.selection;
    displayEnabled = prior.displayEnabled;
    hiddenReviews = prior.hiddenReviews;
    keepEvidence();
    updateStatus('Restored previous selection.');
    render();
  }
  function changeView(next) {
    view = next;
    $('rzExplorer').dataset.drawer = next;
    document.querySelectorAll('[data-rz-view]').forEach(item => item.setAttribute('aria-expanded', String(item.dataset.rzView === next)));
    for (const [key, id] of [['explore', 'rzExplorePanel'], ['manual', 'rzManualPanel'], ['assistant', 'rzAssistantTranscript'],
      ['observations', 'rzObservationsPanel'], ['evidence', 'rzEvidencePanel'], ['glossary', 'rzGlossaryPanel'],
      ['settings', 'rzSettingsPanel']]) {
      $(id).hidden = key !== next;
    }
    $('rzAssistantPanel').dataset.expanded = String(next === 'assistant');
    $('rzAssistantExpand').setAttribute('aria-expanded', String(next === 'assistant'));
    $('rzAssistantExpand').textContent = next === 'assistant' ? 'Collapse chat' : 'Expand chat';
    render();
  }

  function mergeSettings(profiles) {
    if (!Array.isArray(profiles)) return;
    const select = $('rzSettingsCase');
    for (const profile of profiles) {
      if (!profile || ![profile.id, profile.label, profile.summary, profile.availability_note, profile.question]
        .every(value => typeof value === 'string' && value.trim())
        || !['in_app', 'reference_only'].includes(profile.availability) || !Array.isArray(profile.fields)
        || settingsProfiles.has(profile.id)) continue;
      settingsProfiles.set(profile.id, profile);
      const option = make('option', '', profile.label);
      option.value = profile.id;
      if (select.disabled) select.replaceChildren();
      select.append(option);
      select.disabled = false;
    }
  }

  function renderSettings() {
    const select = $('rzSettingsCase');
    const panel = $('rzSettingsBody');
    panel.replaceChildren();
    if (!settingsProfiles.size) {
      panel.append(make('p', 'rz-muted', 'Use-case settings are unavailable for this catalogue.'));
      return;
    }
    if (!settingsProfiles.has(settingsCaseId)) {
      settingsCaseId = settingsProfiles.has(selection.topic) ? selection.topic : settingsProfiles.keys().next().value;
    }
    const profile = settingsProfiles.get(settingsCaseId);
    select.value = settingsCaseId;
    const availability = make('p', 'rz-settings-availability');
    availability.dataset.availability = profile.availability;
    availability.append(make('strong', '', profile.availability === 'in_app' ? 'Available in SARtainty' : 'Reference workflow'),
      make('span', '', profile.availability_note));
    panel.append(make('p', '', profile.summary), availability);
    describePairs(panel, profile.fields.filter(field => typeof field?.label === 'string' && typeof field.value === 'string')
      .map(field => [field.label, field.value]));
    const links = make('ul', 'rz-settings-sources');
    for (const id of new Set(Array.isArray(profile.evidence_ids) ? profile.evidence_ids : [])) {
      const source = evidence.get(id);
      const link = sourceLink(source?.url, source?.title);
      if (!link) continue;
      const item = make('li');
      item.append(link);
      links.append(item);
    }
    if (links.childElementCount) panel.append(links);
    const ask = button('Ask the assistant', 'rz-button rz-secondary rz-small', () => {
      void sendMessage(profile.question, {explainOnly: true});
    });
    ask.disabled = sending;
    panel.append(ask);
  }

  function glossaryText(value) {
    return value.normalize('NFKD').toLowerCase().replace(/\p{M}/gu, '').replace(/[^\p{L}\p{N}]+/gu, ' ').trim();
  }
  function mergeGlossary(raw) {
    if (!raw || !Array.isArray(raw.entries) || !Array.isArray(raw.sources)) return;
    for (const item of raw.sources) {
      const url = httpURL(item?.url);
      if (typeof item?.id === 'string' && typeof item.title === 'string' && url) glossarySources.set(item.id, {...item, url: url.href});
    }
    for (const entry of raw.entries) {
      if (!entry || [entry.id, entry.term, entry.definition, entry.example].some(value => typeof value !== 'string' || !value.trim())) continue;
      const aliases = Array.isArray(entry.aliases) ? entry.aliases.filter(value => typeof value === 'string' && value.trim()) : [];
      const names = [entry.term, ...aliases].map(glossaryText);
      const search = glossaryText([entry.term, ...aliases, entry.category, entry.definition, entry.example].join(' '));
      glossaryEntries.set(entry.id, {...entry, aliases, names, search});
    }
  }
  function renderGlossary(keepExpanded = true) {
    const results = $('rzGlossaryResults');
    const current = new Set(keepExpanded ? [...results.querySelectorAll('details[open]')].map(row => row.dataset.entry) : []);
    const query = $('rzGlossarySearch').value.trim();
    const normalized = glossaryText(query);
    const words = normalized.split(' ').filter(Boolean);
    const matches = [...glossaryEntries.values()].filter(entry => words.every(word => entry.search.includes(word)));
    const rank = entry => entry.names.includes(normalized) ? 0 : entry.names.some(name => name.startsWith(normalized)) ? 1 : 2;
    matches.sort((a, b) => rank(a) - rank(b) || a.term.localeCompare(b.term));
    results.replaceChildren();
    const count = matches.length;
    $('rzGlossaryStatus').textContent = count ? `${count} ${count === 1 ? 'term' : 'terms'}`
      : query ? 'No local matches' : 'Glossary entries are unavailable';
    const exact = normalized ? matches.find(entry => rank(entry) === 0) : null;
    for (const entry of matches) {
      const detail = make('details', 'rz-glossary-entry');
      detail.dataset.entry = entry.id;
      detail.open = current.has(entry.id) || exact?.id === entry.id || count === 1;
      const label = make('summary', '', entry.term);
      if (typeof entry.category === 'string' && entry.category) label.append(make('span', 'rz-glossary-category', entry.category));
      const content = make('div', 'rz-glossary-content');
      content.append(make('p', '', entry.definition));
      const example = make('p', 'rz-glossary-example');
      example.append(make('strong', '', 'In the explorer'), document.createTextNode(` ${entry.example}`));
      content.append(example);
      const links = make('ul', 'rz-glossary-sources');
      for (const id of new Set(Array.isArray(entry.source_ids) ? entry.source_ids : [])) {
        const source = glossarySources.get(id);
        if (!source) continue;
        const link = sourceLink(source.url, source.title);
        if (link) {
          const item = make('li');
          item.append(link);
          links.append(item);
        }
      }
      if (links.childElementCount) content.append(links);
      detail.append(label, content);
      results.append(detail);
    }
  }

  function closeView() {
    const toggle = document.querySelector(`[data-rz-view="${view}"]`);
    changeView('');
    toggle?.focus({preventScroll: true});
  }

  function describePairs(parent, pairs) {
    const dl = make('dl', 'rz-meta');
    for (const [label, value] of pairs) {
      if (value == null || value === '') continue;
      dl.append(make('dt', '', label), make('dd', '', safeText(value)));
    }
    parent.append(dl);
  }
  function evidenceIds(row = activeRow()) {
    const channel = row?.channels?.[selection.channel];
    const analysis = selectedAnalysis();
    const analysisInputs = (analysis?.observation_ids || []).map(id => observations.get(id));
    return [...new Set([sourceFocus, currentSite()?.evidence_id, currentSite()?.event_evidence_id, eventReference()?.evidence_id, row?.evidence_id, channel?.evidence_id,
      ...(currentSite()?.evidence_ids || []), ...(row?.evidence_ids || []), ...(channel?.evidence_ids || []),
      ...(activeChange()?.evidence_ids || []), ...(activeReview()?.evidence_ids || []),
      ...(availableSeries()?.evidence_ids || []),
      ...(analysis?.evidence || []).map(source => source.id),
      ...analysisInputs.flatMap(input => [input?.evidence_id, input?.channels?.[analysis.polarization]?.evidence_id]),
      ...reviewPoints(currentSite()).map(point => point.evidence_id)].filter(Boolean))];
  }
  function openSourceRecord(id) {
    sourceFocus = id;
    changeView('evidence');
    if (!currentSite() && !activeRow()) sourceCards($('rzEvidence'), [id]);
    const card = document.getElementById(`rz-evidence-${id}`);
    if (card) {
      card.tabIndex = -1;
      card.focus({preventScroll: true});
      card.scrollIntoView({block: 'nearest'});
    }
  }
  function sourceCards(parent, ids, prefix = 'rz-evidence') {
    for (const id of ids) {
      const source = evidence.get(id);
      const card = make('div', 'rz-evidence-card');
      card.id = `${prefix}-${id}`;
      card.append(make('strong', '', source?.title || 'Source record'), make('div', 'rz-token', id));
      if (source?.notes) card.append(make('p', 'rz-muted', source.notes));
      describePairs(card, [['Type', source?.kind], ['Source ID', source?.source_id], ['Accessed', source?.accessed_at]]);
      const link = sourceLink(source?.url, 'Open provider source');
      if (link) card.append(link);
      parent.append(card);
    }
  }
  function harmonyRequestURL(value) {
    if (typeof value !== 'string' || !value.startsWith('https://harmony.earthdata.nasa.gov/')) return null;
    const url = httpURL(value);
    if (!url || url.origin !== 'https://harmony.earthdata.nasa.gov' || url.hash) return null;
    if (!/^\/C\d+-[A-Z0-9_-]+\/ogc-api-coverages\/1\.0\.0\/collections\/parameter_vars\/coverage\/rangeset$/.test(url.pathname)) return null;
    return url.href;
  }
  function renderSubsetHandoff(panel, row, pol, channel) {
    if (row.catalogue_origin !== 'live' || missionOf(row) !== 'nisar' || productOf(row) !== 'GCOV'
        || row.science_requires_earthdata !== true || !row.product_id || !validBounds(selection.bbox)) return;
    const section = make('section', 'rz-subset-handoff');
    section.append(make('h3', '', 'Scientific subset'));
    panel.append(section);
    if (!pol || !['catalogue', 'preview', 'measured'].includes(stateOf(channel).key)) {
      section.append(make('p', 'rz-muted', 'Select an available polarization in the matrix to prepare a subset.'));
      return;
    }
    // Use an explicitly advertised dataset path. The provider can infer only
    // main-band defaults when a row lacks a path.
    const expectedPath = `/science/LSAR/GCOV/grids/frequency${row.frequency}/${pol}${pol}`;
    const variable = ['A', 'B'].includes(row.frequency) && channel.dataset_path === expectedPath ? channel.dataset_path : null;
    if (!variable && row.frequency === 'B') {
      section.append(make('p', 'rz-muted', 'This frequency has no verified dataset path for a subset request.'));
      return;
    }
    const payload = {product_id: row.product_id, bbox: [...selection.bbox]};
    if (variable) payload.variables = [variable];
    const key = JSON.stringify([row.id, pol, payload]);
    const previous = subsetHandoff?.key === key && subsetHandoff.revision === selectionRevision ? subsetHandoff : null;
    const prepare = button(previous?.pending ? 'Preparing subset…' : 'Prepare Earthdata subset', 'rz-button rz-secondary rz-small', async () => {
      const handoff = {key, revision: selectionRevision, pending: true};
      subsetHandoff = handoff;
      renderEvidence();
      const stillSelected = () => subsetHandoff === handoff && selectionRevision === handoff.revision
        && selection.active_id === row.id && selection.channel === pol;
      try {
        const plan = await request('/api/subset-plan', payload);
        if (!stillSelected()) return;
        const url = harmonyRequestURL(plan.browser_request_url);
        if (!url || plan.status !== 'planned_not_submitted') throw new Error('NASA Harmony returned an unavailable handoff.');
        handoff.plan = plan;
        handoff.url = url;
      } catch (error) {
        if (stillSelected()) handoff.error = error.message || 'Subset preparation failed.';
      } finally {
        if (stillSelected()) {
          handoff.pending = false;
          if (view === 'evidence') renderEvidence();
        }
      }
    });
    prepare.disabled = Boolean(previous?.pending);
    section.append(prepare);
    if (!variable) section.append(make('p', 'rz-muted', 'The plan will list the provider’s default main-band variables for review.'));
    if (previous?.pending || previous?.error) {
      const status = make('p', 'rz-muted', previous.error || 'Checking collection capabilities…');
      status.setAttribute('role', 'status');
      section.append(status);
    }
    if (previous?.plan) {
      const {plan, url} = previous;
      const ready = make('p', 'rz-muted', 'Plan ready. Review the area and variables, then open the link in your signed-in Earthdata browser to submit it. Check the result size before downloading.');
      ready.setAttribute('role', 'status');
      section.append(ready);
      describePairs(section, [
        ['Collection', plan.collection_id], ['CMR granule', plan.granule_id],
        ['BBox (WGS84)', validBounds(plan.bbox) ? plan.bbox.join(', ') : payload.bbox.join(', ')],
        ['Variables', Array.isArray(plan.variables) ? plan.variables.join(', ') : null],
      ]);
      section.append(sourceLink(url, 'Open request in Earthdata browser'));
    }
  }
  function renderEvidence() {
    const panel = $('rzEvidence');
    panel.replaceChildren();
    const site = currentSite();
    const row = activeRow();
    const channel = row?.channels?.[selection.channel];
    const channelStatus = selection.channel ? stateOf(channel) : null;
    $('rzExport').disabled = !site && !row;
    if (!site && !row) {
      panel.append(make('p', 'rz-muted', 'Choose a study area and acquisition to inspect its source records.'));
      return;
    }
    panel.append(make('strong', '', site?.name || 'Selected acquisition'));
    if (site?.geometry_note) panel.append(make('p', 'rz-muted', site.geometry_note));
    if (site) describePairs(panel, [[eventReference() ? 'Mapped date' : 'Incident date', site.event_date],
      ['Reference acquisition', site.reference_acquisition_date]]);
    if (site) appendRelevantExperiments(panel, site.id);
    const points = reviewPoints(site);
    if (points.length) {
      const section = make('section', 'rz-review-records');
      section.append(make('h3', '', `Review points (${points.length})`));
      for (const point of points) {
        const record = make('article', 'rz-review-record');
        const type = make('span', 'rz-review-type', reviewPointType(point));
        type.prepend(reviewSymbol(point.topic));
        record.append(type, make('strong', '', point.label));
        if (typeof point.note === 'string') record.append(make('p', 'rz-muted', point.note));
        describePairs(record, [['Location', `${point.latitude.toFixed(4)}°, ${point.longitude.toFixed(4)}°`],
          ['HH radar return', reviewPointReturn(point)]]);
        if (point.evidence_id) record.append(button('Source record', 'rz-text-button', () => openSourceRecord(point.evidence_id)));
        section.append(record);
      }
      panel.append(section);
    }
    if (row) {
      describePairs(panel, [
        ['Granule', row.granule_id || row.product_id || row.id], ['Provider', row.provider], ['Product', row.product], ['Sensor', row.sensor],
        ['Acquired UTC', row.acquired_at], ['Catalogue entry', row.catalogue_origin === 'recorded' ? 'Recorded' : 'Live'],
        ['Frequency', row.frequency], ['Wavelength', row.wavelength], ['Track', row.orbit],
        ['Direction', row.direction], ['Mode', row.mode], ['Channel', selection.channel], ['Availability', channelStatus?.label],
        ['Catalogue footprint coverage', typeof row.footprint_coverage === 'number' ? `${row.footprint_coverage}%` : row.footprint_coverage],
        ['Dataset path', channel?.dataset_path || channel?.term], ['Science units', channel?.science_units || channel?.units],
        ['Science CRS', channel?.science_epsg ? `EPSG:${channel.science_epsg}` : channel?.crs],
        ['Pixel spacing', channel?.science_posting_m ? `${channel.science_posting_m} m` : null],
        ['Valid pixels', Number.isFinite(channel?.valid_fraction) ? `${(channel.valid_fraction * 100).toFixed(1)}%` : channel?.valid_coverage],
        ['SHA-256', channel?.science_sha256 || channel?.sha256],
      ]);
      const provider = sourceLink(row.source_url, 'Open this granule at its provider');
      if (provider) panel.append(provider);
      const download = sourceLink(channel?.science_url, 'Download scientific GeoTIFF');
      if (download) {
        download.className = 'rz-science-link';
        download.setAttribute('download', '');
        panel.append(download);
      }
      if (channel?.notes) panel.append(make('p', 'rz-muted', channel.notes));
      renderSubsetHandoff(panel, row, selection.channel, channel);
      const imgURL = httpURL(channel?.preview_url || row.preview_url);
      if (imgURL) {
        const figure = make('figure', 'rz-preview');
        const image = make('img');
        image.alt = channel?.preview_url ? `${selection.channel} provider browse image` : 'Parent scene provider browse image';
        image.loading = 'lazy';
        image.src = imgURL.href;
        image.addEventListener('error', () => figure.replaceChildren(make('figcaption', '', 'Provider browse image is unavailable.')));
        figure.append(image, make('figcaption', '', channel?.preview_url ? 'Provider browse · display only' : 'Parent scene browse · display only'));
        panel.append(figure);
      }
    } else if (eventReference()) {
      panel.append(make('p', 'rz-muted', eventReference().summary));
    } else {
      panel.append(make('p', 'rz-muted', 'No acquisition selected for this study area.'));
    }
    const analysis = selectedAnalysis();
    if (analysis) {
      const section = make('section', 'rz-analysis-result');
      section.append(make('h3', '', 'Analysis run'), make('p', '', analysis.message));
      describePairs(section, [
        ['Analysis', analysisModes[analysis.kind].label], ['Channel', analysis.polarization],
        ['Run UTC', analysis.created_at], ['Analysis ID', analysis.analysis_id], ...analysisMetrics(analysis),
      ]);
      const geometry = analysis.comparability;
      if (geometry) describePairs(section, [
        ['Matched geometry', `Track ${geometry.track} · Frame ${geometry.frame} · ${geometry.pass_direction} · Frequency ${geometry.frequency}`],
        ['Processing version', geometry.processor],
        ['Unverified on one or more dates', Array.isArray(geometry.unknown_fields) && geometry.unknown_fields.length
          ? geometry.unknown_fields.map(field => ({look_direction: 'look direction', noise_correction_applied: 'noise correction'})[field] || field).join(', ') : null],
      ]);
      if (analysis.kind === 'wetland') describePairs(section, [
        ['Method SHA-256', analysis.source?.model_sha256],
        ['Wetland grid', analysis.source?.cell_size_m ? `${analysis.source.cell_size_m} m` : null],
        ['Vegetation assessment', analysis.model?.vegetation_reason || 'Compared with a registered drier date'],
        ['Drier observation', analysis.source?.paired_vegetation?.baseline_observation_id],
      ]);
      for (const id of analysis.observation_ids) {
        const input = observations.get(id);
        const raster = input?.channels?.[analysis.polarization];
        const link = sourceLink(localRasterURL(raster?.science_url, sciencePath), `Source GeoTIFF · ${formatDate(input?.acquired_at)} UTC`);
        if (link) {
          link.className = 'rz-science-link';
          link.setAttribute('download', '');
          section.append(link);
        }
      }
      panel.append(section);
    }
    const review = activeReview();
    if (review) {
      const section = make('section');
      section.setAttribute('aria-label', 'Recognition review');
      section.append(make('h3', '', review.title), make('p', '', review.summary));
      describePairs(section, [
        ['Acquired UTC', row.acquired_at], ['Polarization', review.polarization],
        ['Display SHA-256', review.image_sha256], ['Review GeoTIFF SHA-256', review.science_sha256],
      ]);
      if (review.science_url) {
        const download = sourceLink(review.science_url, 'Download review GeoTIFF');
        download.className = 'rz-science-link';
        download.setAttribute('download', '');
        section.append(download);
        const codes = review.science_codes && typeof review.science_codes === 'object' ? Object.entries(review.science_codes)
          .filter(([code, label]) => /^\d+$/.test(code) && typeof label === 'string') : [];
        describePairs(section, (codes.length ? codes : review.classes.map(entry => [entry.code, entry.label]))
          .map(([code, label]) => [`Code ${code}`, label]));
      }
      panel.append(section);
    }
    const change = activeChange();
    if (change) {
      const section = make('section');
      section.append(make('h3', '', 'Radar change layer'));
      describePairs(section, [
        ['Earlier date UTC', formatDate(change.earlier_date)], ['Later date UTC', formatDate(change.later_date)],
        ['Polarization', change.polarization], ['Threshold', `±${change.threshold_db} dB`],
        ['Paired native pixels', change.paired_native_pixels.toLocaleString()],
        [`Stronger return (≥ +${change.threshold_db} dB)`, change.classes[0].native_pixels.toLocaleString()],
        [`Weaker return (≤ −${change.threshold_db} dB)`, change.classes[1].native_pixels.toLocaleString()],
        ['Display SHA-256', change.image_sha256],
      ]);
      if (change.science_url) {
        const download = sourceLink(change.science_url, 'Download change GeoTIFF');
        download.className = 'rz-science-link';
        download.setAttribute('download', '');
        section.append(download);
        describePairs(section, [
          ['Change units', change.science_units], ['Change CRS', change.science_epsg ? `EPSG:${change.science_epsg}` : null],
          ['Change GeoTIFF SHA-256', change.science_sha256],
        ]);
      }
      section.append(make('p', 'rz-muted', 'Positive dB change means a stronger return on the later date. Negative dB change means a weaker return.'));
      for (const [label, id, date] of [['Earlier radar image', change.earlier_id, change.earlier_date],
        ['Later radar image', change.later_id, change.later_date]]) {
        const asset = mapAsset(observations.get(id), change.polarization);
        const link = sourceLink(asset?.kind === 'image' ? asset.url : null, `${label} · ${formatDate(date)}`);
        if (link) {
          link.className = 'rz-science-link';
          section.append(link);
        }
      }
      if (typeof change.note === 'string') section.append(make('p', 'rz-muted', change.note));
      section.append(sourceLink('/science-review#compare', 'How to compare radar dates'));
      panel.append(section);
    }
    sourceCards(panel, evidenceIds(row));
  }
  function formatDate(value) {
    return value ? new Date(value).toISOString().slice(0, 10) : '—';
  }
  function renderTimeline(rows) {
    const panel = $('rzTimeline');
    panel.replaceChildren();
    if (!rows.length) {
      panel.append(make('p', 'rz-muted', 'Acquisition dates appear when a study area has catalogue results.'));
      return;
    }
    const items = rows.slice().reverse();
    const reference = currentSite()?.reference_acquisition_date;
    $('rzTimelineLabel').textContent = reference ? `UTC · Reference acquisition ${reference}` : 'UTC · Select to highlight on the map';
    for (const row of items) {
      const el = button(formatDate(row.acquired_at), 'rz-epoch', () => selectRow(row.id));
      el.append(make('small', '', row.sensor || row.provider || ''));
      if (formatDate(row.acquired_at) === reference) {
        el.dataset.reference = 'true';
        el.append(make('small', '', 'Reference'));
      }
      el.setAttribute('aria-pressed', String(row.id === selection.active_id));
      el.title = row.acquired_at;
      panel.append(el);
    }
  }
  function previewChannel(row) {
    if (row?.product !== 'NISAR GCOV') return null;
    const candidates = [selection.channel, ...channels];
    return candidates.find(pol => pol && ['catalogue', 'preview', 'measured'].includes(stateOf(row?.channels?.[pol]).key));
  }
  function previewPayload() {
    const row = activeRow();
    const polarization = previewChannel(row);
    const days = $('rzPreviewDays').valueAsNumber;
    if (!row || !polarization || !Number.isInteger(days) || days < 1 || days > 90) return null;
    return {observation_id: row.id, polarization, days};
  }
  function reconcilePreviewTask() {
    if (previewTask && previewTask.key !== JSON.stringify(previewPayload())) {
      previewTask.controller?.abort();
      previewTask = null;
    }
  }
  function previewFrameURL(frame) {
    return localRasterURL(frame.preview_url, previewPath) || localRasterURL(frame.preview_url, providerPreviewPath);
  }
  function previewKindLabel(frame) {
    return frame.preview_kind === 'prepared_crop' ? 'Prepared site preview' : 'ASF frame preview';
  }
  function previewFrameCard(frame, observationId, markUnavailable) {
    const date = formatDate(frame.acquired_at);
    const card = make('li');
    const isTarget = frame.observation_id === observationId;
    card.dataset.target = String(isTarget);
    const count = Math.abs(frame.offset_days);
    const relation = isTarget ? 'Selected date' : count ? `${count} day${count === 1 ? '' : 's'} ${frame.offset_days < 0 ? 'before' : 'after'}` : 'Same UTC date';
    const heading = make('div', 'rz-preview-card-head');
    const time = make('time', '', date);
    time.dateTime = frame.acquired_at;
    heading.append(time, make('span', '', relation));
    const visual = make('div', 'rz-preview-frame');
    const image = previewFrameURL(frame);
    const caption = make('p', 'rz-muted');
    const comparison = make('p', 'rz-muted');
    if (image) {
      const label = previewKindLabel(frame);
      const img = make('img');
      img.alt = `${label} for ${date} UTC`;
      img.loading = 'lazy';
      img.decoding = 'async';
      img.addEventListener('error', () => {
        visual.textContent = 'Preview could not load';
        caption.hidden = true;
        comparison.hidden = true;
        markUnavailable(frame.observation_id);
      }, {once: true});
      img.src = image;
      const enlarge = sourceLink(image, '');
      enlarge.setAttribute('aria-label', `Open ${label.toLowerCase()} for ${date} UTC`);
      enlarge.append(img);
      visual.append(enlarge);
      caption.textContent = label;
    } else {
      visual.textContent = 'Preview unavailable';
      caption.hidden = true;
    }
    const summary = frame.appearance?.summary;
    if (typeof summary === 'string' && summary.trim()) comparison.textContent = summary;
    else comparison.hidden = true;
    card.append(heading, visual, caption, comparison);
    const source = sourceLink(frame.source_url, 'Source record');
    if (source) {
      source.setAttribute('aria-label', `Source record for ${date} UTC`);
      card.append(source);
    }
    return card;
  }
  function renderPreviewComparison(container, result, observationId) {
    const target = result.frames.find(frame => frame.observation_id === observationId && frame.offset_days === 0);
    const baseDay = Date.parse(`${formatDate(target.acquired_at)}T00:00:00Z`);
    const dayAt = offset => formatDate(baseDay + offset * 86400000);
    const failed = new Set();
    const imageFrames = result.frames.filter(frame => previewFrameURL(frame));

    const summary = typeof result.summary === 'string' && result.summary.trim()
      ? result.summary : imageFrames.length > 1 ? 'Review the closest available captures for visible changes in shape or texture.'
        : imageFrames.length ? 'One image is available. Widen the date window to look for another.'
          : 'No preview images are available in this date window.';
    container.append(make('p', 'rz-preview-summary', summary));

    const viewer = make('section', 'rz-preview-viewer');
    const label = make('label', '', 'Requested date · UTC');
    label.htmlFor = 'rzPreviewSlider';
    const slider = make('input');
    slider.id = 'rzPreviewSlider';
    slider.type = 'range';
    slider.min = -result.days;
    slider.max = result.days;
    slider.step = 1;
    slider.value = 0;
    const scale = make('div', 'rz-preview-scale');
    scale.setAttribute('aria-hidden', 'true');
    for (const offset of [-result.days, 0, result.days]) scale.append(make('span', '', dayAt(offset)));
    const shown = make('p', 'rz-preview-shown');
    const image = make('div', 'rz-preview-enlarged');
    viewer.append(label, slider, scale, shown, image);
    viewer.hidden = !imageFrames.length;

    let displayedId;
    function showNearestCapture() {
      const offset = slider.valueAsNumber;
      const requested = dayAt(offset);
      slider.setAttribute('aria-valuetext', `${requested} UTC`);
      let nearest;
      let nearestDistance = Infinity;
      for (const frame of imageFrames) {
        if (failed.has(frame.observation_id)) continue;
        const distance = Math.abs(frame.offset_days - offset);
        if (distance < nearestDistance || (distance === nearestDistance && frame.observation_id === observationId)) {
          nearest = frame;
          nearestDistance = distance;
        }
      }
      if (nearest) {
        const capture = formatDate(nearest.acquired_at);
        shown.textContent = nearestDistance ? `Requested ${requested} · Showing nearest capture ${capture} UTC` : `Showing ${capture} UTC`;
      } else {
        shown.textContent = `Requested ${requested} UTC · Preview unavailable`;
      }
      const nextId = nearest?.observation_id || '';
      if (nextId === displayedId) return;
      displayedId = nextId;
      image.replaceChildren();
      if (!nearest) return;
      const url = previewFrameURL(nearest);
      const title = `${previewKindLabel(nearest)} for ${formatDate(nearest.acquired_at)} UTC`;
      const link = sourceLink(url, '');
      link.setAttribute('aria-label', `Open ${title}`);
      const img = make('img');
      img.alt = title;
      img.decoding = 'async';
      img.addEventListener('error', () => markUnavailable(nextId), {once: true});
      img.src = url;
      link.append(img);
      image.append(link);
    }
    function markUnavailable(id) {
      failed.add(id);
      showNearestCapture();
    }
    slider.addEventListener('input', showNearestCapture);
    showNearestCapture();
    container.append(viewer);

    const timeline = make('ol', 'rz-preview-dates');
    timeline.setAttribute('aria-label', 'Nearby acquisition previews in date order');
    for (const frame of result.frames) timeline.append(previewFrameCard(frame, observationId, markUnavailable));
    container.append(timeline);
  }
  function renderPreviewAnalysis() {
    const row = activeRow();
    const pol = previewChannel(row);
    const enabled = previewAnalysisAvailable && row?.product === 'NISAR GCOV';
    $('rzPreviewPanel').hidden = !enabled;
    const open = $('rzOpenPreview');
    open.hidden = !enabled;
    open.setAttribute('aria-expanded', String(enabled && view === 'observations'));
    if (!enabled || view !== 'observations') return;

    $('rzPreviewAnchor').textContent = `${formatDate(row.acquired_at)} UTC${pol ? ` · ${pol}` : ''}`;
    const guidance = $('rzPreviewGuidance');
    guidance.replaceChildren();
    describePairs(guidance, [
      ['Looking for', 'Images that look brighter, darker or more contrasted around the selected date.'],
      ['Using', pol ? `NISAR ${pol} radar previews from the same track and frame, before and after. This is a visual check.`
        : 'Choose a radar channel to find matching NISAR previews.'],
    ]);
    const payload = previewPayload();
    const pending = Boolean(previewTask?.controller);
    const submit = $('rzPreviewSubmit');
    submit.disabled = !payload || pending;
    submit.textContent = pending ? 'Finding previews…' : 'Find nearby previews';
    const status = $('rzPreviewStatus');
    const result = previewTask?.result;
    status.dataset.tone = previewTask?.error ? 'error' : '';
    status.textContent = previewTask?.error || (pending ? 'Checking acquisitions and reading available previews…'
      : result ? (result.partial ? 'Preview screen ready with gaps.' : 'Preview screen ready.')
        : pol ? '' : 'Choose an available channel in the polarization matrix.');
    const container = $('rzPreviewResults');
    container.setAttribute('aria-busy', String(pending));
    if (result === renderedPreview) return;
    renderedPreview = result;
    container.replaceChildren();
    if (!result) return;

    renderPreviewComparison(container, result, payload.observation_id);
    const titles = make('ul', 'rz-preview-notes rz-muted');
    for (const note of result.messages) titles.append(make('li', '', note));
    if (titles.childElementCount) container.append(titles);
  }
  async function runPreviewAnalysis() {
    const payload = previewPayload();
    const submit = $('rzPreviewSubmit');
    if (!previewAnalysisAvailable || !payload || submit.disabled) return;
    const returnFocus = document.activeElement === submit;
    const task = {key: JSON.stringify(payload), controller: new AbortController()};
    previewTask = task;
    renderPreviewAnalysis();
    const applicable = () => previewTask === task && task.key === JSON.stringify(previewPayload());
    try {
      const result = await request('/api/explorer/preview-analysis', payload, task.controller.signal);
      if (!applicable()) return;
      if (result.scope !== 'visual_only' || result.days !== payload.days || result.polarization !== payload.polarization
          || !Array.isArray(result.messages) || result.messages.some(message => typeof message !== 'string')
          || !Array.isArray(result.frames) || !result.frames.some(frame => frame?.observation_id === payload.observation_id && frame.offset_days === 0)
          || result.frames.some(frame => !frame?.observation_id || !Number.isFinite(Date.parse(frame.acquired_at))
            || !Number.isInteger(frame.offset_days) || Math.abs(frame.offset_days) > payload.days)) {
        throw new Error('The explorer service returned an incomplete preview screen.');
      }
      task.result = result;
    } catch (error) {
      if (applicable()) task.error = error.message || 'The preview screen could not run.';
    } finally {
      if (applicable()) {
        task.controller = null;
        renderPreviewAnalysis();
        if (returnFocus && view === 'observations' && document.activeElement === document.body) submit.focus({preventScroll: true});
      }
    }
  }
  function cancelAnalysisRequest() {
    if (!analysisTask?.controller) return;
    analysisTask.controller.abort();
    analysisTask = null;
  }
  function analysisScope(rows) {
    return JSON.stringify([selection.site_id, selection.channel, rows.map(row => row.id)]);
  }
  function analysisRows(rows, kind, pol) {
    const measured = (row, channel) => stateOf(row.channels?.[channel]).key === 'measured'
      && row.channels[channel].science_url && mapAsset(row, channel);
    const modelInputs = analysisModelObservations?.get(kind);
    return rows.filter(row => row.site_id === selection.site_id && recordedObservationIds.has(row.id)
      && (!modelInputs || modelInputs.has(row.id)) && measured(row, pol)
      && (kind !== 'wetland' || modelInputs || measured(row, 'HV'))).reverse();
  }
  function analysisSelect(node, choices, value) {
    node.replaceChildren();
    for (const [id, label] of choices) {
      const option = make('option', '', label);
      option.value = id;
      node.append(option);
    }
    node.value = value;
  }
  function analysisPayload() {
    return analysisDraft && {kind: analysisDraft.kind, site_id: selection.site_id,
      polarization: analysisDraft.polarization, observation_ids: analysisDraft.observation_ids};
  }
  function renderAnalysis(rows = rowsFor()) {
    $('rzAnalysisPanel').hidden = !analysisAvailable;
    if (!analysisAvailable) return;
    const scope = analysisScope(rows);
    const topic = currentSite()?.topic;
    const modes = ['change', 'time_series'];
    if (topic?.startsWith('wetland') || rows.some(row => analysisModelObservations?.get('wetland')?.has(row.id))) modes.push('wetland');
    if (topic === 'oil_spill') modes.push('oil');
    if (analysisDraft?.scope !== scope) {
      cancelAnalysisRequest();
      const sameSite = analysisDraft?.site === selection.site_id;
      const kind = sameSite && modes.includes(analysisDraft.kind) ? analysisDraft.kind : 'change';
      analysisDraft = {scope, site: selection.site_id, kind, polarization: selection.channel, observation_ids: null};
    }
    const draft = analysisDraft;
    const mode = analysisModes[draft.kind];
    const channelChoices = mode.channel ? [mode.channel] : channels.filter(pol => analysisRows(rows, draft.kind, pol).length >= mode.min);
    if (!channelChoices.includes(draft.polarization)) draft.polarization = channelChoices[0] || '';
    const candidates = analysisRows(rows, draft.kind, draft.polarization);
    if (!draft.observation_ids) {
      const active = candidates.find(row => row.id === selection.active_id);
      const previous = candidates.filter(row => row.acquired_at < active?.acquired_at).at(-1);
      const next = candidates.find(row => row.acquired_at > active?.acquired_at);
      const defaults = mode.max === 1 ? [active || candidates.at(-1)].filter(Boolean)
        : draft.kind === 'change' && active && (previous || next) ? [active, previous || next]
          : candidates.slice(-mode.max);
      const ids = new Set(defaults.map(row => row.id));
      draft.observation_ids = candidates.filter(row => ids.has(row.id)).map(row => row.id);
    }
    analysisSelect($('rzAnalysisKind'), modes.map(id => [id, analysisModes[id].label]), draft.kind);
    analysisSelect($('rzAnalysisChannel'), channelChoices.length ? channelChoices.map(pol => [pol, pol]) : [['', 'Unavailable']], draft.polarization);
    $('rzAnalysisChannel').disabled = channelChoices.length < 2;
    const brief = $('rzAnalysisBrief');
    const [lookingFor, using] = analysisBriefs[draft.kind];
    brief.replaceChildren();
    describePairs(brief, [['Looking for', lookingFor], ['Using', using(draft.polarization || 'NISAR')]]);
    const datePanel = $('rzAnalysisDates');
    datePanel.replaceChildren();
    for (const row of candidates) {
      const label = make('label');
      const input = make('input');
      input.type = mode.max === 1 ? 'radio' : 'checkbox';
      input.name = 'analysis-dates';
      input.dataset.observation = row.id;
      input.checked = draft.observation_ids.includes(row.id);
      input.disabled = mode.max > 1 && !input.checked && draft.observation_ids.length >= mode.max;
      input.addEventListener('change', () => {
        cancelAnalysisRequest();
        const ids = new Set(draft.observation_ids);
        if (mode.max === 1) ids.clear();
        if (input.checked) ids.add(row.id);
        else ids.delete(row.id);
        draft.observation_ids = candidates.filter(item => ids.has(item.id)).map(item => item.id);
        renderAnalysis();
        [...datePanel.querySelectorAll('input')].find(item => item.dataset.observation === row.id)?.focus({preventScroll: true});
      });
      const time = make('time', '', formatDate(row.acquired_at));
      time.dateTime = row.acquired_at;
      if (candidates.some(other => other.id !== row.id && formatDate(other.acquired_at) === formatDate(row.acquired_at))) {
        time.append(document.createTextNode(` ${row.acquired_at.slice(11, 19)}`));
      }
      label.append(input, time);
      datePanel.append(label);
    }
    const key = JSON.stringify(analysisPayload());
    const task = analysisTask?.key === key ? analysisTask : null;
    const pending = Boolean(task?.controller);
    const count = draft.observation_ids.length;
    $('rzAnalysisDateField').hidden = !candidates.length;
    $('rzAnalyze').disabled = !draft.polarization || count < mode.min || count > mode.max || pending;
    $('rzAnalyze').textContent = pending ? 'Analyzing…' : 'Analyze selection';
    const status = $('rzAnalysisStatus');
    status.dataset.tone = task?.error ? 'error' : '';
    status.textContent = task?.error || (pending ? 'Reading the recorded scientific rasters…'
      : candidates.length < mode.min ? (mode.max === 1 ? 'No prepared inputs for this research screen in the current selection.' : 'Choose a site and date range with at least two verified rasters in the same channel.')
        : count < mode.min ? `Choose ${mode.min === mode.max ? '' : 'at least '}${mode.min} acquisitions.`
          : count === mode.max && candidates.length > count && !task?.result ? 'Clear a selected date to use another.' : '');
    renderAnalysisResult(task?.result);
  }
  function analysisMetrics(result) {
    const layer = result.change_layer;
    const measured = result.metrics || {};
    if (layer) return [
      ['Usable on both dates', `${layer.paired_native_pixels.toLocaleString()} pixels`],
      [`Stronger return ≥ +${layer.threshold_db} dB`, layer.classes[0].native_pixels.toLocaleString()],
      [`Weaker return ≤ −${layer.threshold_db} dB`, layer.classes[1].native_pixels.toLocaleString()],
      ['Median change', Number.isFinite(measured.median_change_db) ? `${measured.median_change_db.toFixed(2)} dB` : null],
    ];
    if (result.time_series) return [['Acquisitions used', result.time_series.points.length],
      ['Usable on every date', `${result.time_series.points[0].valid_pixels.toLocaleString()} pixels`]];
    if (result.kind === 'wetland') return [
      ['Open-water candidates', measured.open_water_cells],
      ['Possible water edge · uncertain', measured.uncertain_shore_cells],
      ['Open-water assessment withheld', measured.open_water_unassessed_cells],
      ['Flooded-vegetation candidates', measured.vegetation_evaluated_cells > 0 ? measured.flooded_vegetation_cells : null],
    ].filter(([, value]) => Number.isSafeInteger(value) && value >= 0).map(([label, value]) => [label, value.toLocaleString()]);
    return [['Evaluated cells', measured.assessed_cells], ['Unassessed cells', measured.unassessed_cells],
      ['Cells with a screen flag', measured.flagged_cells]].filter(([, value]) => Number.isSafeInteger(value) && value >= 0)
      .map(([label, value]) => [label, value.toLocaleString()]);
  }
  function renderAnalysisResult(result) {
    const panel = $('rzAnalysisResults');
    panel.replaceChildren();
    if (!result) return;
    const card = make('div', 'rz-analysis-result');
    const summary = make('p', '', result.message);
    summary.setAttribute('role', 'status');
    card.append(summary);
    const metrics = analysisMetrics(result);
    if (metrics.length) describePairs(card, metrics);
    if (result.status === 'measured') {
      const actions = make('div', 'rz-analysis-actions');
      const active = selectedAnalysis() === result;
      const mapResult = result.change_layer || result.recognition_layer;
      const resultButton = button(mapResult ? 'View result on map' : 'View result in Source audit', 'rz-button rz-secondary rz-small', () => {
        if (!active) applyAnalysisResult(result);
        changeView(mapResult ? '' : 'evidence');
      });
      actions.append(resultButton);
      if (active) actions.append(button('Return to saved layers', 'rz-button rz-secondary rz-small', () => {
        saveSelection();
        selection.analysis_id = '';
        render();
      }));
      card.append(actions);
    }
    panel.append(card);
  }
  function analysisMatches(record, query) {
    return record?.site_id === query.site_id && record?.polarization === query.polarization;
  }
  function validateAnalysisResult(result, query) {
    const invalid = () => { throw new Error('The analysis result could not be matched to the chosen acquisitions.'); };
    if (!result || !['measured', 'unavailable', 'unsupported'].includes(result.status) || result.kind !== query.kind
        || !analysisMatches(result, query) || !Array.isArray(result.observation_ids)
        || result.observation_ids.length !== query.observation_ids.length
        || new Set(result.observation_ids).size !== query.observation_ids.length
        || result.observation_ids.some(id => !query.observation_ids.includes(id))
        || typeof result.message !== 'string' || !result.message.trim()) invalid();
    if (result.status !== 'measured') return result;
    if (typeof result.analysis_id !== 'string' || !result.analysis_id) invalid();
    const sources = Array.isArray(result.evidence) ? result.evidence.filter(source => typeof source?.id === 'string') : [];
    merge({evidence: sources});
    let change, series, review;
    if (result.change_layer) {
      const layer = result.change_layer;
      if (query.kind !== 'change' || !analysisMatches(layer, query) || [layer.earlier_id, layer.later_id].some((id, index) => id !== query.observation_ids[index])
          || !localRasterURL(layer.image_url, previewPath) || !localRasterURL(layer.science_url, sciencePath)
          || (changeLayers.has(layer.id) && !runtimeChangeIds.has(layer.id))) invalid();
      const previous = changeLayers.get(layer.id);
      merge({change_layers: [layer]});
      change = changeLayers.get(layer.id);
      if (change === previous || !change?.science_url || !analysisMatches(change, query)) invalid();
      runtimeChangeIds.add(change.id);
    }
    if (result.time_series) {
      series = validSeries(result.time_series);
      if (query.kind !== 'time_series' || !series || !analysisMatches(series, query)
          || series.points.length !== query.observation_ids.length
          || series.points.some((point, index) => point.observation_id !== query.observation_ids[index])) invalid();
    }
    if (result.recognition_layer) {
      const layer = result.recognition_layer;
      if (!['wetland', 'oil'].includes(query.kind) || !analysisMatches(layer, query) || layer.observation_id !== query.observation_ids[0]
          || (recognitionLayers.has(layer.id) && !runtimeReviewIds.has(layer.id))) invalid();
      const previous = recognitionLayers.get(layer.id);
      merge({recognition_layers: [layer]});
      review = recognitionLayers.get(layer.id);
      if (review === previous || !analysisMatches(review, query) || review.observation_id !== query.observation_ids[0]) invalid();
      runtimeReviewIds.add(review.id);
    }
    if ((query.kind === 'change' && !change) || (query.kind === 'time_series' && !series)) invalid();
    const run = {...result, observation_ids: query.observation_ids, evidence: sources,
      change_layer: change, time_series: series, recognition_layer: review};
    analysisResults.set(run.analysis_id, run);
    return run;
  }
  function applyAnalysisResult(result) {
    const rows = rowsFor();
    const ids = new Set(rows.map(row => row.id));
    if (result.site_id !== selection.site_id || !result.observation_ids.every(id => ids.has(id))) return;
    saveSelection();
    keepEvidence();
    const active = result.observation_ids.at(-1);
    pendingFit = selection.active_id !== active;
    selection.analysis_id = result.analysis_id;
    selection.selected_ids = [...result.observation_ids].reverse();
    selection.active_id = active;
    selection.channel = result.polarization;
    selection.change_layer_id = result.change_layer?.id || '';
    if (result.recognition_layer) hiddenReviews.delete(result.recognition_layer.id);
    displayEnabled = Boolean(mapAsset(activeRow(), selection.channel));
    if (analysisDraft) analysisDraft.scope = analysisScope(rows);
    render();
  }
  async function runAnalysis() {
    if (!analysisAvailable || $('rzAnalyze').disabled) return;
    const submit = $('rzAnalyze');
    const invokedBySubmit = document.activeElement === submit;
    const payload = analysisPayload();
    const task = {controller: new AbortController(), key: JSON.stringify(payload), revision: selectionRevision};
    analysisTask = task;
    renderAnalysis();
    const applicable = () => analysisTask === task && task.revision === selectionRevision && task.key === JSON.stringify(analysisPayload());
    try {
      const response = await request('/api/explorer/analyze', payload, task.controller.signal);
      if (!applicable()) return;
      task.result = validateAnalysisResult(response, payload);
    } catch (error) {
      if (applicable()) task.error = error.message || 'The analysis could not run.';
    } finally {
      if (applicable()) {
        task.controller = null;
        if (task.result?.status === 'measured') applyAnalysisResult(task.result);
        else renderAnalysis();
        if (invokedBySubmit && view === 'observations' && document.activeElement === document.body) submit.focus({preventScroll: true});
      }
    }
  }
  function drawSeries(canvas, series) {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    const context = canvas.getContext('2d');
    if (!width || !height || !context) return;
    const scale = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    context.scale(scale, scale);
    const values = series.points.map(point => point.value);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const padding = Math.max(.2, .14 * (max - min));
    const low = min - padding;
    const high = max + padding;
    const first = Date.parse(series.points[0].acquired_at);
    const span = Date.parse(series.points.at(-1).acquired_at) - first;
    const y = value => 11 + (high - value) / (high - low) * (height - 32);
    const left = 52;
    context.font = '11px "Source Sans 3", sans-serif';
    context.textAlign = 'right';
    context.textBaseline = 'middle';
    for (const tick of [high, (high + low) / 2, low]) {
      context.strokeStyle = '#45534a';
      context.beginPath();
      context.moveTo(left, y(tick));
      context.lineTo(width - 9, y(tick));
      context.stroke();
      context.fillStyle = '#b3beb3';
      context.fillText(`${tick.toFixed(1)} dB`, left - 7, y(tick));
    }
    const points = series.points.map(point => ({
      x: left + 6 + (Date.parse(point.acquired_at) - first) / span * (width - left - 21),
      y: y(point.value), active: point.observation_id === selection.active_id,
      release: point.processing_release,
    }));
    context.strokeStyle = '#e5d7b9';
    context.lineWidth = 1.5;
    context.beginPath();
    points.forEach((point, index) => {
      const previous = points[index - 1];
      if (!previous || (previous.release && point.release && previous.release !== point.release)) context.moveTo(point.x, point.y);
      else context.lineTo(point.x, point.y);
    });
    context.stroke();
    for (const point of points) {
      context.fillStyle = point.active ? getComputedStyle(document.body).getPropertyValue('--rz-topic').trim() : '#e5d7b9';
      context.beginPath();
      context.arc(point.x, point.y, point.active ? 4.5 : 3, 0, 2 * Math.PI);
      context.fill();
    }
    context.fillStyle = '#b3beb3';
    context.textAlign = 'left';
    context.fillText(formatDate(series.points[0].acquired_at), left, height - 7);
    context.textAlign = 'right';
    context.fillText(`${formatDate(series.points.at(-1).acquired_at)} UTC`, width - 9, height - 7);
  }
  function renderTimeSeries(rows) {
    const panel = $('rzTimeSeries');
    const series = availableSeries(rows);
    $('rzTimeSeriesPanel').hidden = !series;
    seriesResizeObserver?.disconnect();
    panel.replaceChildren();
    if (!series) return;
    $('rzTimeSeriesTitle').textContent = series.title;
    const canvas = make('canvas', 'rz-series-plot');
    canvas.setAttribute('aria-hidden', 'true');
    const values = make('ul', 'rz-series-values');
    values.setAttribute('aria-label', 'Measured time points');
    for (const point of series.points) {
      const item = make('li');
      const date = make('time', '', `${formatDate(point.acquired_at)} UTC`);
      date.dateTime = point.acquired_at;
      const activate = button('', 'rz-series-point', () => {
        selectRow(point.observation_id, series.polarization);
        [...panel.querySelectorAll('.rz-series-point')].find(el => el.dataset.observation === point.observation_id)?.focus({preventScroll: true});
      });
      activate.dataset.observation = point.observation_id;
      activate.setAttribute('aria-pressed', String(point.observation_id === selection.active_id));
      activate.title = `${point.acquired_at} · ${point.value} dB · ${point.valid_pixels.toLocaleString()} common pixels`;
      activate.append(date, make('strong', '', `${point.value.toFixed(2)} dB`));
      if (typeof point.label === 'string' && point.label) activate.append(make('small', '', point.label));
      if (typeof point.processing_release === 'string' && point.processing_release) {
        activate.append(make('small', 'rz-muted', point.processing_release));
      }
      item.append(activate);
      values.append(item);
    }
    panel.append(make('p', 'rz-series-statistic', `${series.polarization} · ${series.statistic}`), canvas, values,
      make('p', 'rz-muted', series.summary),
      button('Series sources', 'rz-text-button', () => openSourceRecord(series.evidence_ids[0])));
    seriesResizeObserver = new ResizeObserver(() => drawSeries(canvas, series));
    seriesResizeObserver.observe(canvas);
  }
  function renderPolarizations() {
    const group = $('rzPolarizations');
    group.replaceChildren();
    for (const pol of channels) {
      const el = button(pol, 'rz-chip', () => {
        saveSelection();
        const enabled = selection.polarizations.includes(pol);
        selection.polarizations = enabled ? selection.polarizations.filter(value => value !== pol) : [...selection.polarizations, pol];
        selection.showUnavailableChannels = false;
        keepEvidence();
        render();
        $('rzPolarizations').querySelector(`[data-pol="${pol}"]`)?.focus();
      });
      el.dataset.pol = pol;
      el.title = `Filter results by ${pol}`;
      el.setAttribute('aria-label', `Filter by ${pol} availability`);
      el.setAttribute('aria-pressed', String(selection.polarizations.includes(pol)));
      group.append(el);
    }
  }
  function renderMatrix(rows) {
    const panel = $('rzMatrix');
    panel.replaceChildren();
    if (!rows.length) {
      panel.append(make('p', 'rz-panel-body rz-muted', 'No acquisition matches the current selection.'));
      return;
    }
    const table = make('table', 'rz-table');
    const head = make('thead');
    const headers = make('tr');
    for (const label of ['Acquired UTC', 'Dataset', 'Frequency', 'Track / pass', ...channels]) {
      const title = make('th', '', label);
      title.scope = 'col';
      headers.append(title);
    }
    head.append(headers);
    table.append(head);
    const body = make('tbody');
    for (const row of rows) {
      const line = make('tr');
      line.dataset.current = String(row.id === selection.active_id);
      const date = make('td');
      const choose = button(formatDate(row.acquired_at), 'rz-acq', () => selectRow(row.id));
      choose.title = row.id;
      date.append(choose, make('small', 'rz-muted', row.acquired_at.slice(11, 19)));
      const dataset = make('td');
      dataset.append(make('strong', '', row.sensor || row.provider || ''), make('div', 'rz-muted', row.dataset_title || row.product));
      line.append(date, dataset, make('td', '', safeText(row.frequency || row.wavelength)), make('td', '', [row.orbit, row.direction].filter(Boolean).join(' · ') || '—'));
      for (const pol of channels) {
        const cell = make('td');
        const value = stateOf(row.channels?.[pol]);
        const chooseChannel = button(value.label, 'rz-channel', () => {
          selectRow(row.id, pol);
          $('rzMatrix').querySelector('.rz-channel[aria-pressed="true"]')?.focus();
        });
        chooseChannel.dataset.state = value.key;
        chooseChannel.setAttribute('aria-label', `${pol} on ${formatDate(row.acquired_at)}: ${value.label}`);
        chooseChannel.setAttribute('aria-pressed', String(row.id === selection.active_id && pol === selection.channel));
        cell.append(chooseChannel);
        line.append(cell);
      }
      body.append(line);
    }
    table.append(body);
    panel.append(table);
  }
  function mapStatus() {
    $('rzMapStatus').textContent = [mapBaseStatus, mapLayerStatus, changeStatus, reviewStatus].filter(Boolean).join(' ');
  }
  function initExplorerMap() {
    if (map) return true;
    if (!window.L) {
      mapBaseStatus = 'The map library could not load. The matrix and source records are available.';
      mapStatus();
      return false;
    }
    map = window.L.map('rzMap', {preferCanvas: true, zoomControl: false, maxZoom: 19, zoomSnap: .25, zoomDelta: .5}).setView([27, 0], 2);
    window.L.control.zoom({position: 'topright'}).addTo(map);
    window.L.control.scale({position: 'bottomleft', imperial: false, maxWidth: 95}).addTo(map);
    basemaps = createBasemaps(map, message => { mapBaseStatus = message; mapStatus(); });
    map.createPane('rzDisplay');
    map.getPane('rzDisplay').style.zIndex = 320;
    map.createPane('rzChange');
    map.getPane('rzChange').style.zIndex = 330;
    map.createPane('rzRecognition');
    map.getPane('rzRecognition').style.zIndex = 340;
    overlayGroup = window.L.layerGroup().addTo(map);
    return true;
  }
  function fitStudyArea() {
    const raster = mapAsset(activeRow(), selection.channel);
    const bounds = raster?.bounds || (validBounds(selection.bbox) ? leafletBounds(selection.bbox) : null);
    if (!map || !bounds) return;
    const size = map.getSize();
    const mobile = size.x < 620;
    const chat = $('rzAssistantPanel').getBoundingClientRect();
    const chatExpanded = view === 'assistant';
    const top = mobile ? Math.min(108, size.y * .25) : 35;
    const bottom = mobile ? Math.min(Math.max(140, chat.height + 16), size.y * .58, Math.max(0, size.y - top - 100)) : 52;
    const right = !mobile && chatExpanded ? Math.min(chat.width + 32, size.x * .43) : (mobile ? 16 : 42);
    map.fitBounds(bounds, {
      paddingTopLeft: [mobile ? 16 : 42, top],
      paddingBottomRight: [right, bottom],
      maxZoom: 17,
      animate: false,
    });
  }
  function drawGeometry(geometry, options, name, onClick) {
    if (!geometry) return;
    try {
      const layer = window.L.geoJSON(geometry, {
        style: () => options,
        pointToLayer: (_feature, point) => window.L.circleMarker(point, {...options, radius: 6}),
      });
      if (name) layer.bindTooltip(make('span', '', name));
      if (onClick) layer.on('click', onClick);
      layer.addTo(overlayGroup);
    } catch {
      // Missing or invalid provider geometry is represented by its absence on the map.
    }
  }
  function reviewSymbol(topic) {
    const symbol = make('i', 'rz-review-symbol');
    symbol.dataset.topic = topic;
    symbol.setAttribute('aria-hidden', 'true');
    return symbol;
  }
  function drawReviewPoint(point) {
    const L = window.L;
    const type = reviewPointType(point);
    const label = `${type}: ${point.label}`;
    const icon = L.divIcon({className: 'rz-review-marker', html: reviewSymbol(point.topic), iconSize: [26, 26], iconAnchor: [13, 13]});
    const content = make('div', 'rz-review-popup');
    content.append(make('span', 'rz-muted', type), make('strong', '', point.label));
    if (typeof point.note === 'string') content.append(make('p', '', point.note));
    if (reviewPointReturn(point)) describePairs(content, [['HH radar return', reviewPointReturn(point)]]);
    if (point.evidence_id) content.append(button('Source record', 'rz-text-button', () => openSourceRecord(point.evidence_id)));
    const marker = L.marker([point.latitude, point.longitude], {icon, title: label, keyboard: true, riseOnHover: true})
      .bindTooltip(make('span', '', point.label))
      .bindPopup(content, {offset: [0, -7], maxWidth: 265})
      .addTo(overlayGroup);
    marker.getElement()?.setAttribute('aria-label', label);
  }
  function renderChangeOverlay(rows) {
    const available = availableChange(rows);
    const active = activeChange();
    const toggle = $('rzChangeToggle');
    toggle.hidden = !available;
    toggle.disabled = !displayEnabled;
    toggle.setAttribute('aria-pressed', String(Boolean(active)));
    $('rzExplorer').dataset.changeActive = String(Boolean(active));
    if ((active?.id || '') === changeOverlayID) return active;
    if (changeOverlay) map.removeLayer(changeOverlay);
    changeOverlay = null;
    changeStatus = '';
    changeOverlayID = active?.id || '';
    if (active) {
      changeOverlay = window.L.imageOverlay(active.image_url, active.image_bounds, {
        pane: 'rzChange',
        alt: `Paired ${active.polarization} radar return change, ${formatDate(active.earlier_date)} to ${formatDate(active.later_date)}`,
        interactive: false,
      }).addTo(map);
      changeOverlay.once('error', () => {
        if (changeOverlayID !== active.id) return;
        selection.change_layer_id = '';
        render();
        changeStatus = 'Radar change layer could not load.';
        mapStatus();
      });
    }
    return active;
  }
  function describeChange(legend, layer) {
    const info = make('div', 'rz-change-legend');
    info.setAttribute('role', 'group');
    info.setAttribute('aria-label', 'Measured radar change legend');
    info.append(make('span', 'rz-change-dates',
      `${layer.polarization} · ${formatDate(layer.earlier_date)} → ${formatDate(layer.later_date)} UTC`));
    for (const entry of layer.classes) {
      const stronger = entry.id === 'brightened';
      const label = stronger ? 'Stronger radar return' : 'Weaker radar return';
      const threshold = stronger ? `≥ +${layer.threshold_db} dB` : `≤ −${layer.threshold_db} dB`;
      const item = make('span', '', `${label} ${threshold}`);
      const swatch = make('i');
      swatch.style.setProperty('--swatch', entry.color);
      item.prepend(swatch);
      info.append(item);
    }
    legend.append(info);
  }
  function renderRecognitionOverlay() {
    const toggle = $('rzReviewToggle');
    const review = activeReview();
    toggle.hidden = !availableReview();
    toggle.disabled = !displayEnabled;
    toggle.setAttribute('aria-pressed', String(Boolean(review)));
    $('rzExplorer').dataset.reviewActive = String(Boolean(review));
    if ((review?.id || '') === reviewOverlayID) return review;
    if (reviewOverlay) map.removeLayer(reviewOverlay);
    reviewOverlay = null;
    reviewOverlayID = review?.id || '';
    reviewStatus = '';
    if (!review) return null;
    const options = {pane: 'rzRecognition', interactive: false};
    if (review.outlines) {
      const styles = new Map(review.classes.map(entry => [entry.id, {
        color: entry.color, dashArray: reviewDash[entry.outline_style || 'solid'], weight: 2.5, opacity: 1, fill: false,
      }]));
      reviewOverlay = window.L.geoJSON(review.outlines, {...options, style: feature => styles.get(feature.properties.class_id)}).addTo(map);
    } else {
      reviewOverlay = window.L.imageOverlay(review.image_url, review.image_bounds, {...options, alt: review.title}).addTo(map);
      reviewOverlay.once('error', () => {
        if (reviewOverlayID !== review.id) return;
        hiddenReviews.add(review.id);
        render();
        reviewStatus = 'Recognition review layer could not load.';
        mapStatus();
      });
    }
    return review;
  }
  function describeRecognition(legend, review) {
    const block = make('div', 'rz-change-legend rz-review-layer-legend');
    block.setAttribute('role', 'group');
    block.setAttribute('aria-label', `${review.title} legend`);
    const scope = review.origin === 'habitat_reference' ? 'Independent habitat context and archived radar comparison' : 'Research review';
    block.append(make('strong', 'rz-review-layer-title', review.title),
      make('span', 'rz-change-dates', `${formatDate(activeRow().acquired_at)} UTC · ${review.polarization} · ${scope}`));
    const mapped = review.outlines ? new Set(review.outlines.features.map(feature => feature.properties.class_id))
      : Array.isArray(review.mapped_class_ids) ? new Set(review.mapped_class_ids) : null;
    for (const entry of review.classes) {
      if (mapped && !mapped.has(entry.id)) continue;
      const item = make('span', '', entry.label);
      const swatch = make('i');
      swatch.style.setProperty('--swatch', entry.color);
      if (mapped) swatch.dataset.style = review.outlines && entry.outline_style && entry.outline_style !== 'solid'
        ? entry.outline_style : 'outline';
      item.prepend(swatch);
      block.append(item);
    }
    legend.append(block);
  }
  function drawEventReference(reference, legend) {
    const layer = window.L.geoJSON(reference.outlines, {
      pane: 'rzRecognition',
      style: {color: palette.active, weight: 2.8, fillColor: '#fda48d', fillOpacity: .34},
      onEachFeature: (feature, polygon) => {
        const label = typeof feature.properties.label === 'string' ? feature.properties.label : 'Mapped possible slick';
        polygon.bindTooltip(make('span', '', label), {sticky: true});
        const popup = make('div', 'rz-review-popup');
        popup.append(make('strong', '', label), make('p', '', `${reference.date} · Greenpeace satellite interpretation`),
          button('Source record', 'rz-text-button', () => openSourceRecord(reference.evidence_id)));
        polygon.bindPopup(popup);
      },
    });
    layer.addTo(overlayGroup);
    const block = make('div', 'rz-change-legend rz-review-layer-legend rz-event-reference-legend');
    block.setAttribute('role', 'group');
    block.setAttribute('aria-label', reference.title);
    const key = make('span', '', 'Mapped possible slick');
    const swatch = make('i');
    swatch.dataset.style = 'outline';
    swatch.style.setProperty('--swatch', palette.active);
    key.prepend(swatch);
    block.append(make('strong', 'rz-review-layer-title', reference.title),
      make('span', 'rz-change-dates', `${reference.date} · Greenpeace reference`), key);
    legend.append(block);
  }
  function renderMap(rows) {
    if (!initExplorerMap()) return;
    overlayGroup.clearLayers();
    const L = window.L;
    const site = currentSite();
    const reference = eventReference();
    if (reference && site.id !== eventBasemapSite) basemaps.select('Esri World Imagery');
    eventBasemapSite = reference ? site.id : '';
    const points = reviewPoints(site);
    const current = activeRow();
    const codes = new Set(selection.selected_ids);
    const asset = mapAsset(current, selection.channel);
    const oil = selection.topic === 'oil_spill';
    const topicAccent = oil ? palette.active : palette.extent;
    const topicLabel = oil ? 'Oil study area' : selection.topic.startsWith('wetland') ? 'Wetland study area' : 'Study area';
    if (!reference && validBounds(selection.bbox)) {
      L.rectangle(leafletBounds(selection.bbox), {color: topicAccent, dashArray: oil ? '2 4' : '7 5', weight: 2, fill: false, interactive: false}).addTo(overlayGroup);
    }
    const inventory = ['official_boundary', 'official_site_point'].includes(site?.geometry_kind);
    const siteName = site?.geometry_kind === 'official_site_point' ? 'Inventory site location' : 'Inventory boundary';
    if (inventory) drawGeometry(site.geometry, {color: '#f0efe4', fillColor: palette.land, fillOpacity: .12, weight: 2, interactive: false}, siteName);
    drawGeometry(site?.water_geometry, {color: palette.water, fillColor: palette.water, fillOpacity: .85, weight: 1, interactive: false}, 'Mapped open water');
    for (const row of rows) {
      const active = row.id === current?.id;
      const selected = codes.has(row.id);
      if (active) continue;
      drawGeometry(row.geometry, {color: selected ? topicAccent : palette.outlines, dashArray: '8 5', fill: false, weight: selected ? 2 : 1}, `${row.sensor || row.provider} · ${formatDate(row.acquired_at)}`, () => selectRow(row.id));
    }
    if (asset?.bounds) {
      L.rectangle(asset.bounds, {color: topicAccent, weight: 2, fill: false, interactive: false}).addTo(overlayGroup);
    } else {
      drawGeometry(current?.geometry, {color: topicAccent, weight: 2, fill: false, interactive: false});
    }
    for (const point of points) drawReviewPoint(point);
    const nextURL = displayEnabled && asset ? JSON.stringify([asset.url, asset.bounds]) : '';
    if (nextURL !== overlayURL) {
      if (dataOverlay) map.removeLayer(dataOverlay);
      dataOverlay = null;
      overlayURL = nextURL;
      mapLayerStatus = '';
      if (nextURL) {
        const options = {pane: 'rzDisplay', opacity: displayOpacity, interactive: false};
        dataOverlay = (asset.kind === 'image'
          ? L.imageOverlay(asset.url, asset.bounds, {...options, alt: `NISAR GCOV ${selection.channel || ''} radar backscatter`})
          : L.tileLayer(asset.url, {...options, maxZoom: 19})).addTo(map);
        dataOverlay.once(asset.kind === 'image' ? 'error' : 'tileerror', () => {
          if (overlayURL === nextURL) {
            mapLayerStatus = 'Radar display could not load.';
            mapStatus();
          }
        });
      }
    }
    $('rzRasterToggle').hidden = !asset;
    $('rzRasterToggle').textContent = displayEnabled ? 'Hide raster' : 'Show raster';
    $('rzRasterToggle').setAttribute('aria-pressed', String(displayEnabled));
    $('rzOpacityLabel').hidden = !asset || !displayEnabled;
    $('rzFit').textContent = asset?.bounds ? 'Fit raster' : 'Fit study area';
    const activeDifference = renderChangeOverlay(rows);
    const activeRecognition = renderRecognitionOverlay();
    mapStatus();
    const legend = $('rzMapLegend');
    legend.replaceChildren();
    if (displayEnabled && Array.isArray(asset?.range) && asset.range.length === 2 && asset.range.every(Number.isFinite)) {
      const item = make('span', 'rz-radar-scale', `${asset.range[0]} to ${asset.range[1]} dB`);
      item.prepend(make('i', 'rz-radar-ramp'));
      legend.append(item);
    }
    for (const [label, color, style] of [
      ...(!reference && validBounds(selection.bbox) ? [[topicLabel, topicAccent, 'dashed']] : []),
      ...(inventory && site?.geometry ? [[siteName, '#efeee4', 'outline']] : []),
      ...(site?.water_geometry ? [['Mapped open water', palette.water]] : []),
      ...(rows.length > 1 ? [['Catalogue footprint', palette.outlines, 'dashed']] : []),
      ...(current ? [[asset?.bounds ? 'Raster crop' : 'Selected acquisition', topicAccent, 'outline']] : []),
    ]) {
      const item = make('span', '', label);
      const swatch = make('i');
      swatch.style.setProperty('--swatch', color);
      if (style) swatch.dataset.style = style;
      item.prepend(swatch);
      legend.append(item);
    }
    for (const topic of new Set(points.map(point => point.topic))) {
      const count = points.filter(point => point.topic === topic).length;
      const item = make('span', '', `${reviewPointType({topic})}${count > 1 ? `s (${count})` : ''}`);
      item.prepend(reviewSymbol(topic));
      legend.append(item);
    }
    if (activeDifference) describeChange(legend, activeDifference);
    if (activeRecognition) describeRecognition(legend, activeRecognition);
    const eventToggle = $('rzEventToggle');
    const showReference = Boolean(reference && !hiddenReviews.has(`event:${site.id}`));
    eventToggle.hidden = !reference;
    eventToggle.setAttribute('aria-pressed', String(showReference));
    if (showReference) drawEventReference(reference, legend);
    if (pendingFit) {
      pendingFit = false;
      requestAnimationFrame(() => {
        map.invalidateSize({pan: false});
        fitStudyArea();
      });
    }
  }
  function renderPicker(id) {
    const panel = $(id);
    const assistantPicker = id === 'rzChatPicker';
    panel.hidden = !picker;
    panel.dataset.topic = picker;
    panel.replaceChildren();
    if (!picker) return;
    panel.append(make('p', '', pickableSites().length ? 'Choose a verified study area' : 'No verified preset for this topic.'));
    for (const site of pickableSites()) {
      const item = button(site.name, 'rz-button rz-secondary rz-small', () => {
        if (selection.site_id !== site.id) chooseSite(site.id, {redraw: false});
        picker = '';
        if (assistantPicker) {
          void sendMessage(`Show ${site.name}`, {explainOnly: true});
        } else {
          changeView('');
        }
      });
      item.dataset.siteId = site.id;
      item.disabled = assistantPicker && sending;
      panel.append(item);
    }
    panel.append(button('Dismiss', 'rz-button rz-secondary rz-small', () => {
      picker = '';
      render();
    }));
  }
  function renderFilters() {
    const select = $('rzSite');
    const manualTopic = selection.topic === 'wetlands' ? '' : selection.topic;
    const options = [...sites.values()].filter(site => site.id === selection.site_id || fitsTopic(site, manualTopic));
    const key = options.map(site => site.id).join('|');
    if (select.dataset.sites !== key) {
      select.dataset.sites = key;
      select.replaceChildren(make('option', '', 'Choose a site'));
      select.options[0].value = '';
      for (const site of options) {
        const option = make('option', '', site.name);
        option.value = site.id;
        select.append(option);
      }
    }
    select.value = selection.site_id;
    $('rzTopic').value = manualMethod || manualTopic;
    $('rzCatalogueFields').hidden = Boolean(manualMethod);
    for (const guide of $('rzManualMethod').children) guide.hidden = guide.dataset.rzGuide !== manualMethod;
    $('rzBand').value = selection.band;
    $('rzStart').value = selection.start;
    $('rzEnd').value = selection.end;
    $('rzSearch').disabled = Boolean(manualMethod) || searchBusy || !validBounds(selection.bbox);
    $('rzUndo').disabled = !history.length;
    $('rzFit').disabled = !validBounds(selection.bbox);
    $('rzStatus').textContent = status;
    $('rzStatus').dataset.tone = statusTone;
    $('rzManualStatus').textContent = status;
    $('rzManualStatus').hidden = Boolean(manualMethod);
    const hint = $('rzGuidance');
    hint.textContent = guidance[selection.topic] || '';
    hint.hidden = Boolean(manualMethod) || !hint.textContent;
  }
  function renderManual() {
    const panel = $('rzManualBody');
    if (panel.childElementCount) return;
    for (const guide of window.RelaxZoneManual || []) {
      const article = make('article', 'rz-panel rz-panel-body');
      article.id = `manual-${guide.id}`;
      article.append(make('h2', '', guide.title));
      for (const paragraph of [].concat(guide.body || [])) article.append(make('p', '', paragraph));
      const list = make('ul');
      for (const item of guide.links || []) {
        const link = sourceLink(item.url, item.label);
        if (!link) continue;
        const li = make('li');
        li.append(link);
        list.append(li);
      }
      if (list.childElementCount) article.append(list);
      if (guide.id === 'map' || guide.id === 'wetlands' || guide.id === 'first-look' || guide.id === 'start') {
        article.append(button('Choose a wetland', 'rz-button rz-secondary rz-small', () => {
          changeView('explore');
          showPicker();
        }));
      }
      if (guide.id === 'sources') {
        const audit = sourceLink('/science-review', 'Read the science review');
        if (audit) article.append(audit);
      }
      panel.append(article);
    }
    const experiments = make('section', 'rz-panel rz-panel-body rz-experiments');
    experiments.append(make('h2', '', 'Recognition tests'));
    experiments.append(make('p', 'rz-muted', 'See how each research model performed against an independent reference.'));
    const results = make('div', 'rz-experiment-results');
    results.setAttribute('aria-live', 'polite');
    const refresh = button('Refresh results', 'rz-button rz-secondary rz-small', () => void loadExperiments(results, refresh, true));
    experiments.append(refresh, results);
    panel.append(experiments);
    void loadExperiments(results, refresh);
  }
  async function experimentEntries() {
    const pending = experimentsRequest ||= request('/api/explorer/experiments');
    try {
      const payload = await pending;
      merge({evidence: Array.isArray(payload.evidence) ? payload.evidence : []});
      return Array.isArray(payload.experiments) ? payload.experiments.filter(entry =>
        typeof entry?.id === 'string' && typeof entry.title === 'string') : [];
    } catch (error) {
      if (experimentsRequest === pending) experimentsRequest = null;
      throw error;
    }
  }
  function appendRelevantExperiments(panel, siteId) {
    const link = make('div', 'rz-recognition-link');
    panel.append(link);
    void experimentEntries().then(entries => {
      const matched = entries.find(entry => Array.isArray(entry.site_ids) && entry.site_ids.includes(siteId));
      if (!matched || !link.isConnected || view !== 'evidence' || selection.site_id !== siteId) return;
      link.append(button('Recognition tests', 'rz-button rz-secondary rz-small', () => {
        focusedExperiment = matched.id;
        changeView('manual');
        focusExperiment();
      }));
    }).catch(() => {});
  }
  function focusExperiment() {
    if (!focusedExperiment || view !== 'manual') return;
    const match = [...$('rzManualBody').querySelectorAll('[data-experiment-id]')].find(article => article.dataset.experimentId === focusedExperiment);
    if (!match) return;
    focusedExperiment = '';
    const details = match.querySelector('details');
    if (details) details.open = true;
    match.querySelector('summary')?.focus({preventScroll: true});
    match.scrollIntoView({block: 'start'});
  }
  async function loadExperiments(container, refresh, force = false) {
    const labels = {pending: 'Pending', queued: 'Pending', running: 'Running', completed: 'Completed', complete: 'Completed', failed: 'Failed'};
    if (force) experimentsRequest = null;
    refresh.disabled = true;
    container.replaceChildren(make('p', 'rz-muted', 'Checking experiment status…'));
    try {
      const entries = await experimentEntries();
      container.replaceChildren();
      for (const [index, entry] of entries.entries()) {
        const article = make('article', 'rz-experiment');
        article.dataset.experimentId = entry.id;
        const heading = make('div', 'rz-experiment-heading');
        const status = entry.outcome === 'research_only' ? 'Research result' : labels[entry.status] || 'Pending review';
        heading.append(make('span', 'rz-experiment-status', status), make('h3', '', entry.title));
        article.append(heading);
        if (typeof entry.plain_result === 'string') article.append(make('p', 'rz-experiment-lead', entry.plain_result));
        const details = make('details', 'rz-experiment-details');
        details.append(make('summary', '', 'Method, metrics and sources'));
        if (typeof entry.scope === 'string') details.append(make('p', '', entry.scope));
        const metrics = Array.isArray(entry.metrics) ? entry.metrics.filter(item => typeof item?.label === 'string' && Number.isFinite(item.value)) : [];
        if (metrics.length) {
          const format = new Intl.NumberFormat(undefined, {maximumFractionDigits: 3});
          describePairs(details, metrics.map(item => [item.label, `${format.format(item.value)}${item.unit === '%' ? '%' : typeof item.unit === 'string' ? ` ${item.unit}` : ''}`]));
        }
        if (Array.isArray(entry.notes)) {
          for (const note of entry.notes) if (typeof note === 'string') details.append(make('p', 'rz-muted', note));
        }
        const ids = Array.isArray(entry.evidence_ids) ? entry.evidence_ids.filter(id => evidence.has(id)) : [];
        sourceCards(details, ids, `rz-experiment-${index}`);
        if (details.childElementCount > 1) article.append(details);
        container.append(article);
      }
      if (!container.childElementCount) container.append(make('p', 'rz-muted', 'Recognition results are pending.'));
      focusExperiment();
    } catch {
      container.replaceChildren(make('p', 'rz-muted', 'Recognition results are temporarily unavailable.'));
    } finally {
      refresh.disabled = false;
    }
  }
  function renderDatasetSynopsis(row, pol) {
    let synopsis = row && (datasetSynopses[row.id]?.[pol] || datasetSynopses[row.id]?.HH);
    if (!synopsis && row) {
      const dataset = `${currentSite()?.name || 'NISAR acquisition'} · ${formatDate(row.acquired_at)}`;
      if (mapAsset(row, pol)) {
        synopsis = {title: 'Radar image', dataset, looking_for: 'Shows the radar return for this date.',
          using: 'Oil and wetland screening need additional prepared inputs for this crop.'};
      } else if (row.channels?.[pol]?.preview_url || row.preview_url) {
        synopsis = {title: 'Preview only', dataset, looking_for: 'Compare how nearby images look to choose dates to investigate.',
          using: 'Uses display previews. Oil and wetland screening require calibrated radar inputs.'};
      } else {
        synopsis = {title: 'Catalogue only', dataset, looking_for: 'This record identifies the acquisition. Add a preview or radar image to inspect it.'};
      }
    }
    const reference = eventReference();
    if (!synopsis && reference) {
      synopsis = {title: 'Oil slick reference', dataset: `${currentSite().name} · ${reference.date}`,
        looking_for: reference.summary};
    }
    synopsis ||= {title: 'Current dataset', looking_for: 'Select a NISAR date to see which checks its data supports.'};
    const change = activeChange();
    const run = selectedAnalysis();
    let currentResult = synopsis.result;
    if (change) {
      currentResult = `The map highlights where the ${pol} radar signal got stronger or weaker between ${formatDate(change.earlier_date)} and ${formatDate(change.later_date)}.`;
    } else if (run?.time_series) {
      currentResult = `The chart compares the same area across ${run.time_series.points.length} dates.`;
    } else if (run?.kind === 'oil') {
      currentResult = run.metrics?.flagged_cells > 0 ? 'This run flagged dark-water candidates.'
        : 'This run found no candidates in the water it could check.';
    } else if (run?.kind === 'wetland') {
      const {open_water_cells = 0, flooded_vegetation_cells = 0} = run.metrics || {};
      if (flooded_vegetation_cells > 0) currentResult = 'This run flagged possible water under vegetation.';
      else if (open_water_cells > 0) currentResult = `This run flagged likely open water. ${currentResult || ''}`.trim();
    }
    const update = (id, value) => { if ($(id).textContent !== value) $(id).textContent = value; };
    update('rzSynopsisTitle', synopsis.title);
    update('rzSynopsisDataset', [synopsis.dataset, pol && row ? `${pol} view` : null].filter(Boolean).join(' · '));
    update('rzSynopsisDetails', [synopsis.looking_for, synopsis.using, synopsis.why, currentResult].filter(Boolean).join(' '));
  }
  function renderChat() {
    const log = $('rzChatLog');
    log.hidden = !messages.length;
    log.replaceChildren();
    for (const message of messages) {
      const item = make('div', 'rz-chat-message');
      item.dataset.kind = message.kind;
      item.append(make('strong', '', message.kind === 'user' ? 'You' : message.label || 'Qwen Assistant'), make('p', '', message.text));
      for (const id of message.evidenceIds || []) {
        if (!evidence.has(id)) continue;
        const link = button(evidence.get(id).title || id, 'rz-button rz-secondary rz-small rz-chat-source', () => openSourceRecord(id));
        item.append(link);
      }
      if (settingsProfiles.has(message.settingsCaseId)) {
        item.append(button('Open use-case settings', 'rz-button rz-secondary rz-small rz-chat-source', () => {
          settingsCaseId = message.settingsCaseId;
          changeView('settings');
          $('rzSettingsBody').scrollTop = 0;
          $('rzSettingsCase').focus({preventScroll: true});
        }));
      }
      const online = wikipediaLink(message.wikipediaURL, message.wikipediaTitle || 'Read the Wikipedia article');
      if (online) {
        online.className = 'rz-chat-wikipedia';
        item.append(online);
      }
      const offeredChange = changeLayers.get(message.changeLayerId);
      if (offeredChange && activeChange()?.id !== offeredChange.id) {
        item.append(button('Show radar change', 'rz-button rz-secondary rz-small rz-chat-source', () => {
          void applyAction({
            type: 'select', site_id: offeredChange.site_id, topic: sites.get(offeredChange.site_id)?.topic,
            start: formatDate(offeredChange.earlier_date), end: formatDate(offeredChange.later_date),
            polarizations: [offeredChange.polarization],
            selected_ids: [offeredChange.later_id, offeredChange.earlier_id],
            change_layer_id: offeredChange.id,
          }, {});
        }));
      }
      log.append(item);
    }
    if (sending) log.append(make('p', 'rz-muted', 'Checking the selected data…'));
    log.scrollTop = log.scrollHeight;
    $('rzSend').disabled = sending;
    document.querySelectorAll('[data-rz-prompt]').forEach(el => { el.disabled = sending; });
    renderPicker('rzChatPicker');
    const panel = $('rzChatContext');
    panel.replaceChildren();
    describePairs(panel, [
      ['Study area', currentSite()?.name || 'Choose a site'], ['Topic', topics[selection.topic]],
      ['Date range', selection.start && selection.end ? `${selection.start} to ${selection.end}` : '—'],
      ['Mission', 'NISAR'],
      ['Wavelength', selection.band === 'all' ? 'All bands' : `${selection.band}-band`],
      ['Product', 'GCOV'],
      ['Polarizations', selection.polarizations.join(', ') || 'All channels'],
      ['Active acquisition', activeRow()?.id], ['Channel', selection.channel],
      ['Settings lookup', settingsProfiles.get(settingsCaseId)?.label],
    ]);
  }
  function render() {
    reconcile();
    reconcilePreviewTask();
    const rows = rowsFor();
    const site = currentSite();
    const row = activeRow();
    const pol = selection.channel;
    const mapped = stateOf(row?.channels?.[pol]).key === 'measured' && mapAsset(row, pol);
    document.body.dataset.topic = selection.topic;
    $('rzSceneLabel').textContent = selection.topic === 'oil_spill'
      ? 'Oil investigation' : selection.topic.startsWith('wetland') ? 'Wetland observation' : 'NISAR radar';
    $('rzTitle').textContent = site?.name || 'NISAR Explorer';
    const caption = $('rzSubtitle');
    caption.replaceChildren();
    if (row) {
      caption.append(make('span', 'rz-frame-product', row.product));
      if (pol) {
        const channelLabel = make('span', 'rz-frame-channel', pol);
        channelLabel.setAttribute('aria-label', `Polarization ${pol}`);
        caption.append(channelLabel);
      }
      const time = make('time', 'rz-frame-date', `${formatDate(row.acquired_at)} UTC`);
      time.dateTime = row.acquired_at;
      caption.append(time);
      if (mapped) caption.append(make('span', 'rz-frame-source', ['Calibrated backscatter', row.provider].filter(Boolean).join(' · ')));
    } else if (eventReference()) {
      const reference = eventReference();
      caption.append(make('span', 'rz-frame-product', 'Published slick reference'),
        make('span', 'rz-frame-date', reference.date));
    } else {
      caption.textContent = 'Open Manual or ask the assistant for an observation.';
    }
    $('rzObservationCount').textContent = rows.length ? String(rows.length) : '';
    renderFilters();
    renderPicker('rzPicker');
    renderPreviewAnalysis();
    if (view === 'evidence') renderEvidence();
    if (view === 'observations') {
      renderTimeline(rows);
      renderAnalysis(rows);
      renderTimeSeries(rows);
      renderPolarizations();
      renderMatrix(rows);
    } else seriesResizeObserver?.disconnect();
    if (view === 'manual') renderManual();
    if (view === 'glossary') renderGlossary();
    if (view === 'settings') renderSettings();
    renderDatasetSynopsis(row, pol);
    if (view === 'assistant' || picker) renderChat();
    else $('rzChatPicker').hidden = true;
    renderMap(rows);
  }
  async function applyAction(action, result) {
    if (action?.type === 'pick_site') {
      showPicker(action.topic);
      return;
    }
    if (action?.type !== 'select') return;
    const liveRows = Array.isArray(result?.observations) ? result.observations : null;
    if (liveRows || result?.evidence) merge({observations: liveRows || [], evidence: result.evidence || []}, action.site_id || selection.site_id, 'assistant');
    const requested = Array.isArray(action.selected_ids) ? action.selected_ids.filter(id => observations.has(id)).slice(0, 20) : [];
    const siteId = sites.has(action.site_id) ? action.site_id : observations.get(requested[0])?.site_id || selection.site_id;
    if ((!siteId || !sites.has(siteId)) && !validBounds(action.bbox)) {
      showPicker(action.topic);
      return;
    }
    saveSelection();
    const siteChanged = siteId !== selection.site_id;
    if (siteChanged) chooseSite(siteId, {remember: false, redraw: false});
    if (validBounds(action.bbox) && action.bbox.some((value, index) => value !== selection.bbox?.[index])) {
      cancelSearch();
      selection.bbox = [...action.bbox];
      selection.results_ids = [];
      pendingFit = true;
    }
    if (liveRows || requested.length) selection.results_ids = requested.length ? requested : liveRows.map(row => row.id);
    selection.topic = topics[action.topic] ? action.topic : selection.topic;
    if (requested.some(id => selection.band !== 'all' && bandOf(observations.get(id)) !== selection.band)) selection.band = 'all';
    if (typeof action.start === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(action.start)) selection.start = action.start;
    if (typeof action.end === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(action.end)) selection.end = action.end;
    if (Array.isArray(action.polarizations)) selection.polarizations = [...new Set(action.polarizations.filter(pol => channels.includes(pol)))];
    selection.showUnavailableChannels = false;
    if (requested.length && selection.polarizations.length && !rowsFor().length) {
      selection.showUnavailableChannels = true;
    }
    if (requested.length) {
      pendingFit ||= selection.active_id !== requested[0];
      selection.selected_ids = requested;
      selection.active_id = requested[0];
      selection.channel = selection.polarizations.length === 1 ? selection.polarizations[0] : bestChannel(activeRow(), selection.channel);
    }
    keepEvidence();
    picker = '';
    reconcile();
    if (selection.polarizations.length === 1 && selection.active_id) selection.channel = selection.polarizations[0];
    displayEnabled = Boolean(mapAsset(activeRow(), selection.channel));
    const requestedChange = changeLayers.get(action.change_layer_id);
    if (requestedChange && changeMatchesSelection(requestedChange, new Set(rowsFor().map(row => row.id)))) {
      selection.analysis_id = '';
      selection.change_layer_id = requestedChange.id;
    } else {
      const requestedReview = recognitionLayers.get(action.recognition_layer_id);
      if (displayEnabled && requestedReview && reviewMatchesSelection(requestedReview)) {
        selection.analysis_id = '';
        hiddenReviews.delete(requestedReview.id);
        selection.change_layer_id = '';
      }
    }
    if (rowsFor().length || liveRows) {
      const suffix = (result?.partial ? ' Some provider results are unavailable or limited.' : '')
        + (selection.showUnavailableChannels ? ' The requested channels are unavailable in these acquisitions.' : '');
      updateStatus(`Assistant applied filters. ${rowsFor().length} matching catalogue acquisitions.${suffix}`);
    }
    render();
    if (!liveRows && !rowsFor().length) await searchLive({remember: false});
    if (selection.polarizations.length === 1 && selection.active_id) {
      selection.channel = selection.polarizations[0];
      displayEnabled = Boolean(mapAsset(activeRow(), selection.channel));
      render();
    }
  }
  async function sendMessage(raw, {explainOnly = false} = {}) {
    const text = raw.trim();
    if (!text || sending) return;
    const genericWetlands = /^(?:(?:please )?show (?:me )?(?:the )?)?wetlands[.!?]*$/i.test(text);
    sending = true;
    picker = '';
    messages.push({kind: 'user', text});
    $('rzQuestion').value = '';
    changeView('assistant');
    await ensureBootstrap();
    const context = selection.site_id ? {...selection, results_ids: null, polarizations: [], band: 'all'} : selection;
    const known = [...new Set([selection.active_id, ...rowsFor(context).map(row => row.id)])].filter(Boolean).slice(0, 20);
    const revision = selectionRevision;
    const {topic, site_id, bbox, start, end, mission, polarizations, selected_ids, active_id, channel} = selection;
    const change_layer_id = activeChange()?.id || null;
    const priorTerms = messages.findLast(message => Array.isArray(message.glossaryMentionedIds))?.glossaryMentionedIds || [];
    const glossary_context = priorTerms.filter(id => glossaryEntries.has(id));
    try {
      const result = await request('/api/assistant', {
        message: text,
        state: {topic, site_id, bbox, start, end, mission, polarizations, selected_ids, active_id, channel, change_layer_id,
          glossary_context, settings_case_id: settingsCaseId},
        observations: known,
      });
      if (explainOnly && selectionRevision !== revision) return;
      if (Array.isArray(result.evidence)) merge({evidence: result.evidence});
      const referenceAnswer = eventReference() && !activeRow() && result.evidence_ids?.includes(eventReference().evidence_id);
      const label = referenceAnswer ? 'Evidence explanation'
        : result.answer_model === 'glossary' ? 'Glossary' : result.answer_model === 'ecosystems_atbd' ? 'NISAR Ecosystems ATBD'
        : result.answer_model === 'nisar_atbd' ? 'NISAR ATBD' : result.answer_model === 'use_case_settings' ? 'Use-case settings'
        : result.answer_model === 'dataset_synopsis' ? 'Dataset synopsis'
        : result.answer_model === 'nisar_matrix' ? 'NISAR analysis'
        : result.model_used === false ? 'Explorer guidance' : 'Qwen Assistant';
      messages.push({
        kind: 'assistant', text: result.message || 'Choose an action in SAR Explorer.', evidenceIds: result.evidence_ids, label,
        settingsCaseId: result.settings_case_id,
        changeLayerId: result.change_layer_id || result.action?.change_layer_id,
        glossaryMentionedIds: result.answer_model === 'glossary' ? undefined
          : [...new Set(Array.isArray(result.glossary_mentioned_ids) ? result.glossary_mentioned_ids : [])]
            .filter(id => glossaryEntries.has(id)).slice(0, 8),
        wikipediaURL: result.glossary_online_url,
        wikipediaTitle: typeof result.glossary_online_title === 'string' ? result.glossary_online_title : '',
      });
      if (!explainOnly && selectionRevision === revision) await applyAction(result.action, result);
    } catch (error) {
      messages.push({kind: 'assistant', label: 'Explorer guidance', text: genericWetlands && sites.size
        ? 'The assistant is unavailable. You can open one of the wetland study areas below.'
        : `The assistant could not respond. ${error.message} You can still use Manual.`});
      if (genericWetlands && sites.size) showPicker();
    } finally {
      sending = false;
      render();
    }
  }
  function exportEvidence() {
    const change = activeChange();
    const analysis = selectedAnalysis();
    const exportedIds = new Set([...selection.selected_ids, change?.earlier_id, change?.later_id, ...(analysis?.observation_ids || [])]);
    const selected = rowsFor().filter(row => exportedIds.has(row.id));
    const ids = new Set(evidenceIds());
    for (const row of selected) {
      if (row.evidence_id) ids.add(row.evidence_id);
      for (const channel of Object.values(row.channels || {})) if (channel?.evidence_id) ids.add(channel.evidence_id);
    }
    const payload = {
      format: 'RelaxZoneExplorerSelection', version: 1, exported_at: new Date().toISOString(),
      selection, site: currentSite(), observations: selected,
      radar_change: change || null,
      recognition_review: activeReview(),
      time_series: availableSeries(),
      surface_analysis: analysis,
      evidence: [...ids].map(id => evidence.get(id)).filter(Boolean),
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}));
    const download = make('a');
    download.href = url;
    download.download = `relax-zone-${selection.site_id || 'selection'}-evidence.json`;
    download.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function filterChange(id, key) {
    $(id).addEventListener('change', event => {
      saveSelection();
      cancelSearch();
      selection[key] = event.target.value;
      selection.showUnavailableChannels = false;
      keepEvidence();
      updateStatus('Filters updated. Search the catalogue for additional acquisitions.');
      render();
    });
  }
  function boot() {
    const toggle = next => view === next ? closeView() : changeView(next);
    document.querySelectorAll('[data-rz-view]').forEach(item => item.addEventListener('click', () => {
      toggle(item.dataset.rzView);
      if (view === 'glossary') $('rzGlossarySearch').focus({preventScroll: true});
      else if (view === 'settings' && !$('rzSettingsCase').disabled) $('rzSettingsCase').focus({preventScroll: true});
      else if (view === item.dataset.rzView) {
        $(item.getAttribute('aria-controls')).querySelector('[data-rz-close]')?.focus({preventScroll: true});
      }
    }));
    document.querySelectorAll('[data-rz-close]').forEach(item => item.addEventListener('click', closeView));
    $('rzGlossarySearch').addEventListener('input', () => renderGlossary(false));
    $('rzSettingsCase').addEventListener('change', event => {
      if (!settingsProfiles.has(event.target.value)) return;
      settingsCaseId = event.target.value;
      renderSettings();
      $('rzSettingsBody').scrollTop = 0;
    });
    $('rzOpenPreview').addEventListener('click', () => {
      changeView('observations');
      $('rzPreviewDays').focus();
    });
    $('rzPreviewDays').addEventListener('input', () => {
      reconcilePreviewTask();
      renderPreviewAnalysis();
    });
    $('rzPreviewForm').addEventListener('submit', event => {
      event.preventDefault();
      void runPreviewAnalysis();
    });
    $('rzAnalysisKind').addEventListener('change', event => {
      cancelAnalysisRequest();
      analysisDraft.kind = event.target.value;
      analysisDraft.observation_ids = null;
      renderAnalysis();
    });
    $('rzAnalysisChannel').addEventListener('change', event => {
      cancelAnalysisRequest();
      analysisDraft.polarization = event.target.value;
      analysisDraft.observation_ids = null;
      renderAnalysis();
    });
    $('rzAnalysisForm').addEventListener('submit', event => {
      event.preventDefault();
      void runAnalysis();
    });
    $('rzAssistantExpand').addEventListener('click', () => toggle('assistant'));
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && view) {
        if (view === 'glossary') event.preventDefault();
        closeView();
      }
    });
    $('rzSite').addEventListener('change', event => {
      if (event.target.value) chooseSite(event.target.value);
      else {
        saveSelection();
        cancelSearch();
        selection = {...blank(), topic: selection.topic};
        displayEnabled = false;
        keepEvidence();
        updateStatus('Choose a study area.');
        render();
      }
    });
    $('rzTopic').addEventListener('change', event => {
      const topic = event.target.value;
      manualMethod = event.target.selectedOptions[0]?.hasAttribute('data-rz-method') ? topic : '';
      if (manualMethod) {
        picker = '';
        render();
        return;
      }
      saveSelection();
      cancelSearch();
      picker = '';
      keepEvidence();
      const site = currentSite();
      if (site && !fitsTopic(site, topic)) {
        selection = blank();
        displayEnabled = false;
      }
      selection.topic = topic;
      const hasPreset = [...sites.values()].some(site => fitsTopic(site, topic));
      updateStatus(selection.site_id ? 'Use case updated.'
        : hasPreset ? 'Choose a study area for this use case.' : 'No NISAR study areas are ready for this use case.');
      render();
    });
    filterChange('rzBand', 'band');
    filterChange('rzStart', 'start');
    filterChange('rzEnd', 'end');
    $('rzFilters').addEventListener('submit', event => { event.preventDefault(); void searchLive(); });
    $('rzUndo').addEventListener('click', undo);
    $('rzFit').addEventListener('click', fitStudyArea);
    $('rzRasterToggle').addEventListener('click', () => {
      displayEnabled = !displayEnabled;
      render();
    });
    $('rzChangeToggle').addEventListener('click', () => {
      const available = availableChange(rowsFor());
      if (!available || !displayEnabled) return;
      saveSelection();
      selection.change_layer_id = activeChange() ? '' : available.id;
      render();
    });
    $('rzReviewToggle').addEventListener('click', () => {
      const available = availableReview();
      if (!available || !displayEnabled) return;
      saveSelection();
      if (activeReview()) hiddenReviews.add(available.id);
      else {
        hiddenReviews.delete(available.id);
        selection.change_layer_id = '';
      }
      render();
    });
    $('rzEventToggle').addEventListener('click', () => {
      if (!eventReference()) return;
      saveSelection();
      const id = `event:${selection.site_id}`;
      if (hiddenReviews.has(id)) hiddenReviews.delete(id);
      else hiddenReviews.add(id);
      render();
    });
    $('rzOpacity').addEventListener('input', event => {
      displayOpacity = Number(event.target.value);
      dataOverlay?.setOpacity(displayOpacity);
    });
    $('rzExport').addEventListener('click', exportEvidence);
    $('rzChatForm').addEventListener('submit', event => { event.preventDefault(); void sendMessage($('rzQuestion').value); });
    document.querySelectorAll('[data-rz-prompt]').forEach(el => el.addEventListener('click', () => void sendMessage(el.dataset.rzPrompt)));
    changeView('');
    updateStatus('Loading recorded NISAR acquisitions…');
    void ensureBootstrap();
  }
  window.RelaxZoneExplorer = {createBasemaps, getSelection: () => structuredClone(selection)};
  boot();
})();
