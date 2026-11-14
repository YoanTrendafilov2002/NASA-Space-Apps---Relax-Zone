/* SAR Discharge Investigator — frontend logic
 * NASA Space Apps 2026 "Dancing with the SARs"
 *
 * All satellite, weather and facility data are fetched live from public APIs:
 *   - ASF Search API (NISAR L2 GCOV catalogue + public browse quick-looks)
 *   - Microsoft Planetary Computer STAC + data API (Sentinel-1 RTC, JRC Global Surface Water)
 *   - Open-Meteo historical API (ERA5 reanalysis wind/precipitation)
 *   - OpenStreetMap Overpass API (mapped infrastructure, unverified)
 *   - Nominatim (place search)
 * Nothing in this file is synthetic observation data.
 */
'use strict';

/* ------------------------------------------------------------------ */
/* 1. Reference library (verified sources)                              */
/* ------------------------------------------------------------------ */
const REFS = {
  alpers89: {t:'Alpers W., Hühnerfuss H. (1989). The damping of ocean waves by surface films: a new look at an old problem. J. Geophys. Res. 94(C5), 6251–6265.', u:'https://doi.org/10.1029/JC094iC05p06251'},
  gade98: {t:'Gade M., Alpers W., Hühnerfuss H., Masuko H., Kobayashi T. (1998). Imaging of biogenic and anthropogenic ocean surface films by the multifrequency/multipolarization SIR-C/X-SAR. J. Geophys. Res. 103(C9), 18851–18866.', u:'https://doi.org/10.1029/97JC01915'},
  brekke05: {t:'Brekke C., Solberg A.H.S. (2005). Oil spill detection by satellite remote sensing. Remote Sens. Environ. 95(1), 1–13.', u:'https://doi.org/10.1016/j.rse.2004.11.015'},
  fingas14: {t:'Fingas M., Brown C. (2014). Review of oil spill remote sensing. Mar. Pollut. Bull. 83(1), 9–23.', u:'https://doi.org/10.1016/j.marpolbul.2014.03.059'},
  minchew12: {t:'Minchew B., Jones C.E., Holt B. (2012). Polarimetric analysis of backscatter from the Deepwater Horizon oil spill using L-band synthetic aperture radar. IEEE TGRS 50(10), 3812–3830.', u:'https://doi.org/10.1109/TGRS.2012.2185804'},
  topouzelis08: {t:'Topouzelis K. (2008). Oil spill detection by SAR images: dark formation detection, feature extraction and classification algorithms. Sensors 8(10), 6642–6659.', u:'https://doi.org/10.3390/s8106642'},
  nasaEO21: {t:'NASA Earth Observatory (2021). Troubled Waters — oil leaks and algae in Lake Maracaibo (MODIS, Landsat 8, Sentinel-2, Sept 2021).', u:'https://earthobservatory.nasa.gov/images/148894/troubled-waters'},
  squier15: {t:'Squier M., Bridges J., Garber J., Warneke W., Khan S. (2015). Observing oil surface area due to seepage in Lake Maracaibo (ALOS PALSAR L-band). AAPG Search and Discovery #90219.', u:'https://searchanddiscovery.com/abstracts/html/2015/90219gcags/abstracts/177.html'},
  nasaEO03: {t:'NASA Earth Observatory (2003). Oil slicks on Lake Maracaibo, Venezuela (Terra MISR, 26 Dec 2002).', u:'https://science.nasa.gov/earth/earth-observatory/oil-slicks-on-lake-maracaibo-venezuela-3478/'},
  unep11: {t:'UNEP (2011). Environmental Assessment of Ogoniland. United Nations Environment Programme, Nairobi.', u:'https://www.unep.org/resources/assessment/environmental-assessment-ogoniland-site-factsheets-executive-summary-and-full'},
  onuh21: {t:'Onuh P.A. et al. (2021). Artisanal refining of crude oil in the Niger Delta: a challenge to clean-up and remediation in Ogoniland. Local Economy 36(6), 468–486.', u:'https://doi.org/10.1177/02690942211071075'},
  noaaITC: {t:'NOAA DARRP. ITC Tank Fire, Deer Park, Texas (March 2019): oil products and fire water reached Tucker Bayou, San Jacinto River and the Houston Ship Channel.', u:'https://darrp.noaa.gov/node/1668'},
  nisarOil: {t:'NASA/JPL. NISAR Applications: Oil Spills (fact sheet).', u:'https://assets.science.nasa.gov/content/dam/science/missions/nisar/nisar-jpl/pdf/NISAR_Applications_OilSpills1.pdf'},
  asfGcov: {t:'ASF NISAR Data User Guide — GCOV product (layers, pixel spacing by bandwidth, RTC).', u:'https://hyp3-docs.asf.alaska.edu/nisar-docs/gcov'},
  wu09: {t:'Wu T.Y., Mohammad A.W., Jahim J.Md., Anuar N. (2009). A holistic approach to managing palm oil mill effluent (POME): biotechnological advances in the sustainable reuse of POME. Biotechnol. Adv. 27(1), 40–52.', u:'https://research.monash.edu/en/publications/a-holistic-approach-to-managing-palm-oil-mill-effluent-pome-biote/'},
  emsa: {t:'EMSA CleanSeaNet — European satellite-based oil spill and vessel detection service.', u:'https://www.emsa.europa.eu/csn-menu.html'},
  era5: {t:'Hersbach H. et al. (2020). The ERA5 global reanalysis. Q. J. R. Meteorol. Soc. 146, 1999–2049 (accessed via Open-Meteo historical API).', u:'https://doi.org/10.1002/qj.3803'},
  pekel16: {t:'Pekel J.-F. et al. (2016). High-resolution mapping of global surface water and its long-term changes. Nature 540, 418–422 (JRC Global Surface Water).', u:'https://doi.org/10.1038/nature20584'},
  lee80: {t:'Lee J.-S. (1980). Digital image enhancement and noise filtering by use of local statistics. IEEE TPAMI 2(2), 165–168.', u:'https://doi.org/10.1109/TPAMI.1980.4766994'},
  noaaMPSR: {t:'NOAA OSPO Marine Pollution Surveillance Reports (analyst-reviewed SAR oil analyses, US waters).', u:'https://www.ospo.noaa.gov/Products/ocean/marinepollution/'},
};

/* ------------------------------------------------------------------ */
/* 2. Industry research matrix (Q1–Q4)                                  */
/* detect: S = supported, C = conditional, U = unlikely, K = unknown    */
/* ------------------------------------------------------------------ */
const INDUSTRIES = [
  {id:'oil_production', name:'Crude oil production & pipelines (ageing infrastructure)', rank:1, detect:'S',
   illicit:'Legitimate operators with chronic, unrepaired leaks; oil theft (hot-tapping) from pipelines.',
   processes:'Well heads, flow lines, submerged pipelines, storage tanks, flow stations.',
   waste:'Crude oil; produced water containing dispersed oil.',
   pathway:'Direct to lake, estuary or sea; via creeks from onshore lines.',
   mechanism:'Floating crude forms a viscous surface film that damps cm–dm Bragg waves, lowering backscatter (dark patch). Demonstrated at C- and L-band.',
   pattern:{doc:['Chronic, recurring slicks at fixed infrastructure: “The oil spills are multiple and continuous, and you can easily spot the sources” (Lake Maracaibo, NASA EO 2021).','L-band (ALOS PALSAR) time series showed changing oil coverage linked to seepage (Lake Maracaibo, 2010).'], hyp:['Leak frequency tied to pipeline pressure/production rate — not independently documented here.']},
   confounders:'Cyanobacteria/algal films, low-wind patches, river plumes, rain cells.',
   hotspots:['maracaibo','bonny'], refs:['nasaEO21','squier15','alpers89','fingas14','minchew12'], conf:'High (mechanism) · Medium (site attribution)'},
  {id:'bilge', name:'Shipping & shipyards — illegal bilge / oily-waste discharge', rank:2, detect:'S',
   illicit:'Discharge of bilge water, sludge and oily residues in breach of MARPOL Annex I.',
   processes:'Engine-room bilge accumulation, tank cleaning, ship repair.',
   waste:'Bilge oil, sludge, oily water mixtures.',
   pathway:'Shipping lanes, port approaches, estuary channels, sea.',
   mechanism:'Thin mineral-oil films damp Bragg waves; long narrow dark features, often attached to or aligned with a vessel track.',
   pattern:{doc:['Linear or tailed slicks along shipping routes — the most frequent SAR-detected illicit discharge in European waters (CleanSeaNet).'], hyp:['Discharges inside rivers/inner ports — not established by the sources reviewed here.']},
   confounders:'Calm ship wakes, natural films, low-wind lanes, current shear lines.',
   hotspots:['galveston','maracaibo'], refs:['brekke05','topouzelis08','emsa','fingas14'], conf:'High'},
  {id:'artisanal_refining', name:'Artisanal crude refining & oil theft (illegal)', rank:3, detect:'C',
   illicit:'Unlicensed “bush” refineries cooking stolen crude in mangrove creeks.',
   processes:'Pipeline tapping, open-pot distillation, transport by boat.',
   waste:'Crude and distillation residues dumped or leaked; refined-product spills; burnt residues.',
   pathway:'Mangrove creeks → estuary (Bonny River) → coast.',
   mechanism:'Floating crude/residue damps waves as above. Light refined products (kerosene, diesel) spread thin and evaporate within hours–days, shortening the detection window.',
   pattern:{doc:['Many small, distributed sites in creeks and mangroves; widespread hydrocarbon contamination of creeks (UNEP Ogoniland assessment).','Artisanal refining undermines clean-up/remediation in Ogoniland (Onuh et al. 2021).'], hyp:['Night-time operation — not independently documented in the sources reviewed; testable with dawn/dusk SAR passes.']},
   confounders:'Narrow creeks below resolution, mangrove shadow/layover, tidal mudflats, low wind, biogenic films.',
   hotspots:['bonny'], refs:['unep11','onuh21','fingas14'], conf:'Medium'},
  {id:'refining_petrochem', name:'Licensed refineries, petrochemical plants & storage terminals', rank:4, detect:'C',
   illicit:'Permit exceedances, bypasses or unreported releases from otherwise legal facilities.',
   processes:'Distillation, cracking, chemical synthesis, tank storage, loading.',
   waste:'Oily wastewater (oil & grease after separators), dissolved organics (phenols, ammonia, aromatics), accidental product releases.',
   pathway:'Outfalls, drainage ditches and bayous → ship channel → bay.',
   mechanism:'Only a free-floating oil/product phase is SAR-relevant. Treated effluent with low oil & grease and dissolved chemicals do not change surface roughness.',
   pattern:{doc:['Incident releases: in 2019 the ITC Deer Park tank fire sent oil products and fire water through ditches into Tucker Bayou, the San Jacinto River and the Houston Ship Channel (NOAA DARRP).'], hyp:['Discharges tied to production cycles or storm events — requires permit discharge-monitoring data, not SAR alone.']},
   confounders:'Narrow channels near resolution limit, ship wakes, harbour structures, low wind in sheltered bayous.',
   hotspots:['galveston'], refs:['noaaITC','fingas14','noaaMPSR'], conf:'Medium'},
  {id:'waste_oil', name:'Illegal dumping of used / waste oil', rank:5, detect:'C',
   illicit:'Unlicensed disposal of lubricants, sludge and oily waste into drains or watercourses.',
   processes:'Vehicle/engine servicing, informal recycling.',
   waste:'Used lubricating oil, sludge.',
   pathway:'Storm drains → rivers → estuary.',
   mechanism:'Floating oil films damp waves if the released volume covers an area several resolution cells wide.',
   pattern:{doc:[], hyp:['Episodic, small-volume releases — likely below SAR detectability in narrow rivers; no SAR case studies found.']},
   confounders:'As for mineral oil; most events too small for 10–20 m SAR.',
   hotspots:[], refs:['fingas14'], conf:'Low'},
  {id:'palm_veg_oil', name:'Palm-oil & vegetable-oil mills', rank:6, detect:'C',
   illicit:'Untreated or partially treated effluent (POME) released to rivers.',
   processes:'Sterilisation, pressing, clarification.',
   waste:'High BOD/COD organic matter, oil & grease, suspended solids (POME).',
   pathway:'Rivers and canals near mills.',
   mechanism:'Free fatty/vegetable-oil films are surface-active and can damp waves like biogenic monomolecular films; most POME oil is emulsified or retained in ponds. Single-frequency SAR cannot separate these films from natural slicks.',
   pattern:{doc:['Effluent composition (oil & grease, BOD/COD) documented (Wu et al. 2009).'], hyp:['Seasonal discharge pulses with crop cycle — not tested with SAR.']},
   confounders:'Natural biogenic films (essentially the same physics), low wind.',
   hotspots:[], refs:['wu09','gade98'], conf:'Low'},
  {id:'food_processing', name:'Food processing (dairy, rendering, fish processing)', rank:7, detect:'K',
   illicit:'Unpermitted discharge of fats, oils and grease (FOG).',
   processes:'Washing, rendering, cooking.',
   waste:'Emulsified FOG, proteins, organic load.',
   pathway:'Drains → rivers.',
   mechanism:'Emulsified fats mix into the water column quickly; a surface film forms only if a free fat phase separates. No SAR-specific evidence found.',
   pattern:{doc:[], hyp:['Batch/shift-linked discharges — not documented for SAR.']},
   confounders:'Biogenic films.', hotspots:[], refs:['gade98'], conf:'Low'},
  {id:'metalworking', name:'Metalworking & machining', rank:8, detect:'U',
   illicit:'Disposal of spent cutting fluids and degreasing baths.',
   processes:'Machining, degreasing, plating.',
   waste:'Oil-in-water emulsions (stabilised), dissolved metals, solvents.',
   pathway:'Drains → rivers.',
   mechanism:'Stabilised emulsions and dissolved metals do not form a surface film. Only a separated free-oil phase would be SAR-relevant.',
   pattern:{doc:[], hyp:[]}, confounders:'—', hotspots:[], refs:['fingas14'], conf:'Medium (physics)'},
  {id:'pulp_paper', name:'Pulp & paper', rank:9, detect:'U',
   illicit:'Untreated black-liquor or effluent release.',
   processes:'Pulping, bleaching.',
   waste:'Dissolved lignin/organics, resin acids, fibres, colour; foam.',
   pathway:'Rivers.',
   mechanism:'Dissolved and suspended constituents do not damp surface waves. Foam tends to be radar-bright, not dark. Effect of surfactant films: unknown.',
   pattern:{doc:[], hyp:[]}, confounders:'—', hotspots:[], refs:['fingas14'], conf:'Medium (physics)'},
  {id:'textile', name:'Textile dyeing & finishing', rank:10, detect:'U',
   illicit:'Unlicensed dye-houses discharging untreated effluent.',
   processes:'Dyeing, washing, finishing.',
   waste:'Dissolved dyes, salts, surfactants, high temperature.',
   pathway:'Rivers and canals.',
   mechanism:'Colour and dissolved load are optical, not radar, signatures. Dissolved pollutants are invisible to SAR.',
   pattern:{doc:[], hyp:[]}, confounders:'—', hotspots:[], refs:['fingas14'], conf:'Medium (physics)'},
  {id:'tannery', name:'Tanneries', rank:11, detect:'U',
   illicit:'Unregistered tanneries bypassing treatment.',
   processes:'Liming, chrome tanning.',
   waste:'Chromium (III), sulphide, salts, proteins (dissolved/suspended).',
   pathway:'Rivers.',
   mechanism:'Dissolved/suspended — no change in surface roughness; not SAR-detectable.',
   pattern:{doc:[], hyp:[]}, confounders:'—', hotspots:[], refs:['fingas14'], conf:'Medium (physics)'},
];
const DET = {S:['Supported','b-sup'], C:['Conditional','b-con'], U:['Unlikely','b-unl'], K:['Unknown','b-unk']};

/* ------------------------------------------------------------------ */
/* 3. Hotspots (Q5) — real coordinates; evidence from cited sources     */
/* ------------------------------------------------------------------ */
const HOTSPOTS = [
  {id:'maracaibo', name:'Lake Maracaibo', country:'Venezuela', water:'estuary', waterLabel:'Estuarine lake (open water, ~100 km wide)',
   center:[9.85,-71.55], search:[-71.85,9.60,-71.10,10.65], demo:[-71.64,10.02,-71.34,10.32], primary:true,
   industries:['oil_production','bilge','refining_petrochem'],
   why:'More than 10,000 oil installations and thousands of km of ~50-year-old, partly unmapped pipelines leak into the lake (NASA EO 2021). L-band ALOS PALSAR has previously imaged oil on the lake surface (Squier et al. 2015). Open water ≫ SAR resolution.',
   caveats:'Tropical: frequent low wind and intense cyanobacteria blooms (“verdín”) produce look-alike films.',
   feasibility:'High — wide water body, dense NISAR GCOV and Sentinel-1 coverage.', refs:['nasaEO21','squier15','nasaEO03']},
  {id:'galveston', name:'Houston Ship Channel – Galveston Bay', country:'USA', water:'estuary', waterLabel:'Ship channel (150–300 m) + bay (20–30 km)',
   center:[29.55,-94.95], search:[-95.30,29.30,-94.70,29.80], demo:[-95.12,29.45,-94.82,29.72], primary:false,
   industries:['refining_petrochem','bilge'],
   why:'One of the largest petrochemical and refining complexes in the US. Documented 2019 ITC Deer Park release reached Tucker Bayou, the San Jacinto River and the Ship Channel (NOAA DARRP). NOAA analysts publish SAR-based pollution reports for US waters — a reference for validation.',
   caveats:'The ship channel is only tens of SAR pixels wide; bayous are narrower. Heavy ship traffic produces wake look-alikes.',
   feasibility:'Medium — bay resolvable, channel marginal.', refs:['noaaITC','noaaMPSR']},
  {id:'bonny', name:'Bonny River estuary, Niger Delta', country:'Nigeria', water:'creeks', waterLabel:'Estuary + mangrove creeks (tens–hundreds m)',
   center:[4.55,7.12], search:[6.95,4.35,7.30,4.80], demo:[6.98,4.38,7.22,4.62], primary:false,
   industries:['artisanal_refining','oil_production'],
   why:'UNEP documented widespread hydrocarbon contamination of creeks in Ogoniland, with artisanal refining as a continuing source (UNEP 2011; Onuh et al. 2021). Persistent cloud cover makes SAR the only regular observer.',
   caveats:'Most creeks are below reliable slick-detection width; mangrove edges and tidal mudflats confound dark-feature detection.',
   feasibility:'Low–medium — main estuary channel only.', refs:['unep11','onuh21']},
];

/* Catalogue snapshot (ASF Search API, retrieved 2026-11-14) used ONLY if the live query fails. */
const SNAPSHOT = {retrieved:'2026-11-14', note:'ASF Search API, NISAR L2 GCOV, point queries', counts:{maracaibo:21, galveston:'81+', bonny:54}};

/* ------------------------------------------------------------------ */
/* 4. Sensor constants                                                  */
/* ------------------------------------------------------------------ */
const SENSORS = {
  S1: {name:'Sentinel-1 C-band (5.405 GHz)', pol:'VV', nesz:-21, neszNote:'−22 dB σ⁰ (IW spec) expressed as γ⁰ at ~39° incidence (÷cos θ ≈ +1 dB); ±1 dB across the swath', band:'C', braggCm:'≈4.8 cm at 35° incidence',
       product:'Sentinel-1 IW GRD, radiometrically terrain-corrected γ⁰ (Microsoft Planetary Computer “sentinel-1-rtc”)'},
  NISAR: {name:'NISAR L-band (1.257 GHz)', pol:'HH', nesz:null, band:'L', braggCm:'≈21 cm at 35° incidence',
       product:'NISAR L2 GCOV (geocoded, RTC γ⁰ covariance)'},
};
const DB_MIN = -30, DB_MAX = 0; // display/quantisation range for in-browser analysis

/* ------------------------------------------------------------------ */
/* 5. State                                                             */
/* ------------------------------------------------------------------ */
const S = {
  screen:'discover', hs:'maracaibo',
  filt:{industry:'all', water:'all', from:isoDay(-120), to:isoDay(0)},
  acq:{status:'idle', nisar:[], s1:[], err:{}, hs:null},
  scene:null, comp:null, nisarSel:null,
  params:{T:3, minKm2:0.05},
  run:null,          // last analysis result
  sel:null,          // selected anomaly id
  ctx:{},            // per-anomaly context: wind, facilities
  imported:[],       // analysis.py results
  layers:{sar:true, anom:true, comp:false, fac:true, nisar:false},
  visited:new Set(['discover']),
};
function isoDay(off){const d=new Date(); d.setUTCDate(d.getUTCDate()+off); return d.toISOString().slice(0,10);}

/* ------------------------------------------------------------------ */
/* 6. Utilities                                                         */
/* ------------------------------------------------------------------ */
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = (x, d=1) => (x==null || !isFinite(x)) ? '—' : Number(x).toFixed(d);
const hsById = id => HOTSPOTS.find(h => h.id===id);
const indById = id => INDUSTRIES.find(i => i.id===id);
function toast(msg, ms=3500){const t=$('#toast'); t.textContent=msg; t.style.display='block'; clearTimeout(t._h); t._h=setTimeout(()=>t.style.display='none', ms);}
function refShort(k){const t=REFS[k].t; const m=t.match(/^(.*?)\((\d{4})\)/); if(!m) return t.split('.')[0];
  const au=m[1].trim(); if(/^[A-ZÀ-Ž][^\s,]+ [A-Z]\./.test(au)){ const first=au.split(' ')[0]; const many=(au.match(/\.,/g)||[]).length>=1; return `${first}${many?' et al.':''} ${m[2]}`; } return `${au} ${m[2]}`;}
function refLink(k){const r=REFS[k]; return r ? `<a href="${r.u}" target="_blank" rel="noopener" title="${esc(r.t)}">${esc(refShort(k))}</a>` : '';}
function bboxWKT(b){const [w,s,e,n]=b; return `POLYGON((${w} ${s},${e} ${s},${e} ${n},${w} ${n},${w} ${s}))`;}
function bboxGeo(b){const [w,s,e,n]=b; return {type:'Polygon', coordinates:[[[w,s],[e,s],[e,n],[w,n],[w,s]]]};}
function haversineKm(a,b){const R=6371, r=Math.PI/180; const dLa=(b[0]-a[0])*r, dLo=(b[1]-a[1])*r; const x=Math.sin(dLa/2)**2+Math.cos(a[0]*r)*Math.cos(b[0]*r)*Math.sin(dLo/2)**2; return 2*R*Math.asin(Math.sqrt(x));}
async function fetchJSON(url, opt={}, timeout=45000){
  const ac=new AbortController(); const h=setTimeout(()=>ac.abort(), timeout);
  try{const r=await fetch(url,{...opt, signal:ac.signal}); if(!r.ok) throw new Error(`HTTP ${r.status} from ${new URL(url).host}`); return await r.json();}
  catch(e){ if(e.name==='AbortError') throw new Error(`Timed out contacting ${new URL(url).host}`); throw e; }
  finally{clearTimeout(h);}
}

/* ------------------------------------------------------------------ */
/* 7. Data access                                                       */
/* ------------------------------------------------------------------ */
const MPC = 'https://planetarycomputer.microsoft.com/api';
const API = {
  async nisar(hs, from, to){
    const q = new URLSearchParams({platform:'NISAR', processingLevel:'GCOV', intersectsWith:bboxWKT(hs.search),
      start:from+'T00:00:00Z', end:to+'T23:59:59Z', maxResults:'250', output:'geojson'});
    const j = await fetchJSON('https://api.daac.asf.alaska.edu/services/search/param?'+q);
    // de-duplicate reprocessed versions: keep production (P) CRID, highest counter
    const best = new Map();
    for(const f of j.features||[]){
      const p=f.properties; const key=`${p.pathNumber}_${p.frameNumber}_${p.startTime}_${p.rangeBandwidth}`;
      const score=(p.crid||'').startsWith('P') ? 'B'+p.fileID : 'A'+p.fileID;
      if(!best.has(key) || best.get(key).score<score) best.set(key,{score,f});
    }
    return [...best.values()].map(v=>v.f).sort((a,b)=>b.properties.startTime.localeCompare(a.properties.startTime));
  },
  async s1(bbox, from, to){
    const body={collections:['sentinel-1-rtc'], intersects:bboxGeo(bbox), datetime:`${from}T00:00:00Z/${to}T23:59:59Z`, limit:100,
      sortby:[{field:'properties.datetime',direction:'desc'}]};
    const j = await fetchJSON(MPC+'/stac/v1/search',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)});
    // group slices of the same datatake into one acquisition (mosaicked at analysis time)
    const g=new Map();
    for(const f of j.features||[]){ const p=f.properties; const k=`${p.platform}_${p['s1:datatake_id']||''}_${p.datetime.slice(0,10)}`;
      if(!g.has(k)) g.set(k,{id:f.id, items:[], geometry:f.geometry, properties:{...p}}); const a=g.get(k); a.items.push(f);
      if(p.datetime<a.properties.datetime) a.properties.datetime=p.datetime; }
    return [...g.values()].sort((a,b)=>b.properties.datetime.localeCompare(a.properties.datetime));
  },
  async gswItem(bbox){
    const j = await fetchJSON(MPC+'/stac/v1/search',{method:'POST',headers:{'content-type':'application/json'},
      body:JSON.stringify({collections:['jrc-gsw'], intersects:bboxGeo(bbox), limit:4})});
    if(!j.features?.length) throw new Error('No JRC Global Surface Water tile for this window');
    return j.features;
  },
  async png(url){
    const ac=new AbortController(); const h=setTimeout(()=>ac.abort(), 90000);
    let r; try{ r=await fetch(url,{signal:ac.signal}); } catch(e){ throw new Error(e.name==='AbortError'?'Planetary Computer data API timed out':'Network error: '+e.message); } finally{clearTimeout(h);}
    if(!r.ok){ let t=''; try{t=(await r.text()).slice(0,160);}catch{} throw new Error(`Data API HTTP ${r.status} ${t}`); }
    const blob=await r.blob(); const bm=await createImageBitmap(blob);
    const c=document.createElement('canvas'); c.width=bm.width; c.height=bm.height; const g=c.getContext('2d'); g.drawImage(bm,0,0);
    return {data:g.getImageData(0,0,bm.width,bm.height).data, w:bm.width, h:bm.height, url:URL.createObjectURL(blob)};
  },
  s1Bbox(item, bbox, w, h){
    const q=new URLSearchParams({collection:'sentinel-1-rtc', item, expression:'10*log10(vv)', asset_as_band:'True', rescale:`${DB_MIN},${DB_MAX}`});
    return `${MPC}/data/v1/item/bbox/${bbox.join(',')}/${w}x${h}.png?${q}`;
  },
  gswBbox(item, bbox, w, h){
    const q=new URLSearchParams({collection:'jrc-gsw', item, assets:'occurrence', rescale:'0,100', nodata:'255'});
    return `${MPC}/data/v1/item/bbox/${bbox.join(',')}/${w}x${h}.png?${q}`;
  },
  s1Tiles(item){
    const q=new URLSearchParams({collection:'sentinel-1-rtc', item, expression:'10*log10(vv)', asset_as_band:'True', rescale:`${DB_MIN},${DB_MAX}`, tile_format:'png'});
    return `${MPC}/data/v1/item/tiles/WebMercatorQuad/{z}/{x}/{y}@1x?${q}`;
  },
  async wind(lat, lon, iso){
    const d=iso.slice(0,10);
    const q=new URLSearchParams({latitude:lat.toFixed(3), longitude:lon.toFixed(3), start_date:d, end_date:d,
      hourly:'wind_speed_10m,wind_direction_10m,precipitation', wind_speed_unit:'ms', models:'era5', timezone:'GMT'});
    const j=await fetchJSON('https://archive-api.open-meteo.com/v1/archive?'+q);
    const t=j.hourly?.time||[]; if(!t.length) throw new Error('No ERA5 data for this date yet (ERA5 has ~5-day latency)');
    const target=new Date(iso).getTime(); let k=0, best=1e15;
    t.forEach((s,i)=>{const dt=Math.abs(new Date(s+'Z').getTime()-target); if(dt<best){best=dt;k=i;}});
    const ws=j.hourly.wind_speed_10m[k]; if(ws==null) throw new Error('ERA5 wind not yet available for this hour');
    const pr=j.hourly.precipitation; const near=[k-1,k,k+1].filter(i=>i>=0&&i<pr.length).map(i=>pr[i]??0);
    return {ws, wd:j.hourly.wind_direction_10m[k], precip3h:near.reduce((a,b)=>a+b,0), time:t[k]+'Z', grid:[j.latitude,j.longitude]};
  },
  async facilities(lat, lon, rKm=15){
    const r=Math.round(rKm*1000);
    const ql=`[out:json][timeout:60];(
      node["man_made"~"^(offshore_platform|petroleum_well|storage_tank|wastewater_plant|works)$"](around:${r},${lat},${lon});
      way["man_made"~"^(pipeline|storage_tank|wastewater_plant|works|offshore_platform)$"](around:${r},${lat},${lon});
      nwr["industrial"](around:${r},${lat},${lon});
      way["landuse"~"^(industrial|port)$"](around:${r},${lat},${lon});
      way["waterway"~"^(drain|canal)$"](around:${r},${lat},${lon});
    );out center tags 600;`;
    const j=await fetchJSON('https://overpass-api.de/api/interpreter',{method:'POST',body:'data='+encodeURIComponent(ql)},90000);
    return (j.elements||[]).map(e=>({id:e.type+'/'+e.id, lat:e.lat??e.center?.lat, lon:e.lon??e.center?.lon, tags:e.tags||{}, cat:osmCat(e.tags||{})}))
      .filter(e=>e.lat!=null).map(e=>({...e, dkm:haversineKm([lat,lon],[e.lat,e.lon])}));
  },
  async geocode(q){
    const j=await fetchJSON('https://nominatim.openstreetmap.org/search?'+new URLSearchParams({q, format:'json', limit:'1'}));
    if(!j.length) throw new Error('Place not found'); return j[0];
  },
};
function osmCat(t){
  if(/offshore_platform|petroleum_well/.test(t.man_made||'')) return 'Oil wells / platforms';
  if((t.man_made||'')==='pipeline') return 'Pipelines';
  if((t.man_made||'')==='storage_tank') return 'Storage tanks';
  if(/refinery|oil|petroleum/.test(t.industrial||'')) return 'Refinery / oil industry';
  if(/chemical/.test(t.industrial||'')) return 'Chemical plant';
  if(/shipyard|port/.test(t.industrial||'') || t.landuse==='port') return 'Port / shipyard';
  if((t.man_made||'')==='wastewater_plant') return 'Wastewater plant';
  if(t.waterway) return 'Drains / canals';
  if((t.man_made||'')==='works') return 'Works (factory)';
  return 'Industrial land use';
}
const CAT_IND = {'Oil wells / platforms':['oil_production'],'Pipelines':['oil_production','refining_petrochem'],'Storage tanks':['refining_petrochem','oil_production'],
  'Refinery / oil industry':['refining_petrochem'],'Chemical plant':['refining_petrochem'],'Port / shipyard':['bilge'],'Wastewater plant':[],
  'Drains / canals':[],'Works (factory)':['metalworking','food_processing','textile','tannery','pulp_paper','palm_veg_oil'],'Industrial land use':[]};
const CAT_COL = {'Oil wells / platforms':'#d97706','Pipelines':'#7c3aed','Storage tanks':'#0891b2','Refinery / oil industry':'#b91c1c','Chemical plant':'#be185d',
  'Port / shipyard':'#1d4ed8','Wastewater plant':'#059669','Drains / canals':'#0ea5e9','Works (factory)':'#57534e','Industrial land use':'#78716c'};

/* ------------------------------------------------------------------ */
/* 8. SAR dark-feature analysis (runs on calibrated γ⁰ in dB)           */
/* ------------------------------------------------------------------ */
/* v2 (validated): noise-floor-aware membership, two-pass background that excludes dark
 * candidates, shoreline defined by land (not by in-water structures), shoreline buffer,
 * linear-power radiometry, metric-space shape descriptors, explicit scale/noise classes. */
const DET_DEFAULTS = {enl:4.4, shoreBufM:100, holeKm2:0.1, bgWinM:3000, ptDb:10, quantDb:30/255};
function integral(src, mask, W, H){
  const S=new Float64Array((W+1)*(H+1)), C=new Float64Array((W+1)*(H+1));
  for(let y=0;y<H;y++){let rs=0, rc=0; for(let x=0;x<W;x++){const i=y*W+x; if(mask[i]){rs+=src?src[i]:1; rc++;}
    const j=(y+1)*(W+1)+x+1; S[j]=S[j-W-1]+rs; C[j]=C[j-W-1]+rc;}}
  return {S,C,box(x,y,r){const x0=Math.max(0,x-r), y0=Math.max(0,y-r), x1=Math.min(W,x+r+1), y1=Math.min(H,y+r+1);
    const a=y0*(W+1)+x0, b=y0*(W+1)+x1, c=y1*(W+1)+x0, d=y1*(W+1)+x1; return [S[d]-S[b]-S[c]+S[a], C[d]-C[b]-C[c]+C[a]];}};
}
/** 4-connected components of a binary mask; returns {lab, sizes, touchBorder} */
function components4(mask, W, H){
  const N=W*H, lab=new Int32Array(N).fill(-1), sizes=[], touch=[]; const st=new Int32Array(N);
  for(let i0=0;i0<N;i0++){ if(!mask[i0]||lab[i0]>=0) continue; const id=sizes.length; let sp=0, n=0, tb=false; st[sp++]=i0; lab[i0]=id;
    while(sp){ const i=st[--sp]; n++; const x=i%W, y=(i/W)|0; if(x===0||y===0||x===W-1||y===H-1) tb=true;
      if(x>0&&mask[i-1]&&lab[i-1]<0){lab[i-1]=id;st[sp++]=i-1;} if(x<W-1&&mask[i+1]&&lab[i+1]<0){lab[i+1]=id;st[sp++]=i+1;}
      if(y>0&&mask[i-W]&&lab[i-W]<0){lab[i-W]=id;st[sp++]=i-W;} if(y<H-1&&mask[i+W]&&lab[i+W]<0){lab[i+W]=id;st[sp++]=i+W;} }
    sizes.push(n); touch.push(tb); }
  return {lab, sizes, touch};
}
/** chamfer (3-4) distance to the nearest `src` pixel, in units of 1/3 pixel */
function chamfer(src, W, H){
  const N=W*H, d=new Float32Array(N), INF=1e9; for(let i=0;i<N;i++) d[i]=src[i]?0:INF;
  for(let y=0;y<H;y++) for(let x=0;x<W;x++){const i=y*W+x; if(!d[i]) continue; let v=d[i];
    if(x>0) v=Math.min(v,d[i-1]+3); if(y>0){v=Math.min(v,d[i-W]+3); if(x>0) v=Math.min(v,d[i-W-1]+4); if(x<W-1) v=Math.min(v,d[i-W+1]+4);} d[i]=v;}
  for(let y=H-1;y>=0;y--) for(let x=W-1;x>=0;x--){const i=y*W+x; if(!d[i]) continue; let v=d[i];
    if(x<W-1) v=Math.min(v,d[i+1]+3); if(y<H-1){v=Math.min(v,d[i+W]+3); if(x<W-1) v=Math.min(v,d[i+W+1]+4); if(x>0) v=Math.min(v,d[i+W-1]+4);} d[i]=v;}
  return d;
}
const lin2db = v => 10*Math.log10(Math.max(v,1e-12));
/** Core pipeline. db: γ⁰ dB (Float32), valid: data mask, occ: JRC occurrence 0–100 (NaN = unknown).
 *  p: {T, minKm2, nesz, pxX, pxY, enl, shoreBufM, holeKm2, bgWinM} */
function analyzeScene(db, valid, occ, W, H, pxM, pIn){
  const p={...DET_DEFAULTS, ...pIn}; const N=W*H;
  const pxX=p.pxX||pxM, pxY=p.pxY||pxM, pxL=Math.sqrt(pxX*pxY), pxKm2=pxX*pxY/1e6, nesz=p.nesz, T=p.T;
  // --- 1. water / land / in-water structures (from JRC occurrence, independent of SAR coverage)
  const water=new Uint8Array(N), landRaw=new Uint8Array(N), perm=new Uint8Array(N);
  for(let i=0;i<N;i++){ const o=occ[i]; if(o>=80) water[i]=1; else if(o===o) landRaw[i]=1; if(o>=90) perm[i]=1; }
  const lc=components4(landRaw,W,H); const holePx=p.holeKm2/pxKm2;
  const land=new Uint8Array(N), struct=new Uint8Array(N); let nStruct=0;
  lc.sizes.forEach((s,k)=>{ if(s<holePx && !lc.touch[k]) nStruct++; });
  for(let i=0;i<N;i++){ const k=lc.lab[i]; if(k<0) continue; if(lc.sizes[k]<holePx && !lc.touch[k]) struct[i]=1; else land[i]=1; }
  const dLand=chamfer(land,W,H);             // distance to real shoreline (1/3 px units)
  const bufThird=3*p.shoreBufM/pxL;
  // analysable water: valid SAR, water, outside the shoreline buffer, not touching a structure
  const wm=new Uint8Array(N); let nWater=0;
  for(let y=0;y<H;y++) for(let x=0;x<W;x++){ const i=y*W+x; if(!water[i]||!valid[i]||dLand[i]<=bufThird) continue;
    if((x>0&&struct[i-1])||(x<W-1&&struct[i+1])||(y>0&&struct[i-W])||(y<H-1&&struct[i+W])) continue; wm[i]=1; nWater++; }
  if(nWater<500) return {error:'Too little open water in this window (JRC occurrence ≥ 80 %, outside the shoreline buffer). Move the window over open water.'};
  // --- 2. linear power + Lee filter (5x5) on analysable water
  const lin=new Float32Array(N), sq=new Float32Array(N); for(let i=0;i<N;i++){ lin[i]=Math.pow(10,db[i]/10); sq[i]=lin[i]*lin[i]; }
  const I1=integral(lin,wm,W,H), I2=integral(sq,wm,W,H), Cu2=1/p.enl; const filt=new Float32Array(N);
  for(let y=0;y<H;y++) for(let x=0;x<W;x++){const i=y*W+x; if(!wm[i]) continue;
    const [s,c]=I1.box(x,y,2), [s2]=I2.box(x,y,2); const m=s/c, v=Math.max(0,s2/c-m*m), Ci2=v/(m*m+1e-12);
    const w=Ci2>0? Math.min(1,Math.max(0,(1-Cu2/Ci2)/(1+Cu2))) : 0; filt[i]=m+w*(lin[i]-m);}
  // --- 3. two-pass background: pass 1 (window bgWinM), then exclude dark candidates and re-estimate over 2x window
  const R=Math.max(10,Math.round(p.bgWinM/2/pxL)), R2=2*R;
  const IB=integral(filt,wm,W,H); const bg1=new Float32Array(N), cand=new Uint8Array(N);
  for(let y=0;y<H;y++) for(let x=0;x<W;x++){const i=y*W+x; if(!wm[i]) continue; const [s,c]=IB.box(x,y,R); bg1[i]=s/c; if(lin2db(bg1[i])-lin2db(filt[i])>=T/2) cand[i]=1;}
  const keepBg=new Uint8Array(N); for(let i=0;i<N;i++) keepBg[i]=wm[i]&&!cand[i]?1:0;
  const IK=integral(filt,keepBg,W,H), IW=integral(null,wm,W,H);
  const fdb=new Float32Array(N), bdb=new Float32Array(N).fill(NaN), con=new Float32Array(N).fill(NaN), meas=new Uint8Array(N), bgLin=new Float32Array(N);
  let lowN=0, measN=0; const bgMin = nesz!=null ? nesz+T+1 : -Infinity;
  for(let y=0;y<H;y++) for(let x=0;x<W;x++){const i=y*W+x; if(!wm[i]) continue; fdb[i]=lin2db(filt[i]);
    const [s,c]=IK.box(x,y,R2), [,cw]=IW.box(x,y,R2); if(c<0.25*cw || c<20) continue;   // background undefined
    bgLin[i]=s/c; bdb[i]=lin2db(bgLin[i]); con[i]=bdb[i]-fdb[i];
    if(nesz!=null && bdb[i]<nesz+3) lowN++;
    if(bdb[i]>=bgMin){ meas[i]=1; measN++; } }
  // --- 4. bright point targets (platforms/vessels, unidentified): raw ≥ max(background, NESZ) + ptDb, on water or structures
  const ptMask=new Uint8Array(N);
  for(let y=0;y<H;y++) for(let x=0;x<W;x++){const i=y*W+x; if(!valid[i]||!(water[i]||struct[i])) continue; const [s,c]=IB.box(x,y,R); if(c<10) continue;
    const ref=Math.max(lin2db(s/c), nesz!=null?nesz:-Infinity); if(db[i]>=ref+p.ptDb) ptMask[i]=1;}
  const pc=components4(ptMask,W,H); const pts=pc.sizes.map(()=>[0,0]); for(let i=0;i<N;i++){const k=pc.lab[i]; if(k>=0){pts[k][0]+=i%W; pts[k][1]+=(i/W)|0;}}
  pts.forEach((q,k)=>{q[0]/=pc.sizes[k]; q[1]/=pc.sizes[k];});
  // --- 5. hysteresis segmentation restricted to MEASURABLE pixels (seed ≥ T, grow ≥ T/2), 8-connected
  const lab=new Int32Array(N).fill(-1), comps=[], st=new Int32Array(N);
  for(let i0=0;i0<N;i0++){ if(!meas[i0]||lab[i0]!==-1||con[i0]<T) continue; const id=comps.length; let sp=0; st[sp++]=i0; lab[i0]=id; const pix=[];
    while(sp){const i=st[--sp]; pix.push(i); const x=i%W, y=(i/W)|0;
      for(let dy=-1;dy<=1;dy++) for(let dx=-1;dx<=1;dx++){ if(!dx&&!dy) continue; const xx=x+dx, yy=y+dy; if(xx<0||yy<0||xx>=W||yy>=H) continue;
        const j=yy*W+xx; if(meas[j]&&lab[j]===-1&&con[j]>=T/2){lab[j]=id; st[sp++]=j;}}}
    comps.push(pix); }
  // --- 6. descriptors
  const minPx=Math.max(6,Math.ceil(p.minKm2/pxKm2)); const regionalKm2=Math.PI*Math.pow(R2*pxL/1000,2);
  const anoms=[]; const keep=new Int32Array(N).fill(-1);
  comps.forEach((pix,ci)=>{ const n=pix.length; if(n<minPx) return;
    let sl=0, sbg=0, mx=0, mnD=Infinity, pw=0, nz=0, sx=0, sy=0; const cs=new Float32Array(n);
    pix.forEach((i,k)=>{ sl+=lin[i]; sbg+=bgLin[i]; cs[k]=con[i]; if(con[i]>mx) mx=con[i]; if(dLand[i]<mnD) mnD=dLand[i]; pw+=perm[i];
      if(nesz!=null && fdb[i]<nesz+1) nz++; sx+=(i%W)*pxX; sy+=((i/W)|0)*pxY; });
    cs.sort(); const p95=cs[Math.min(n-1,Math.floor(0.95*n))];
    const meanDb=lin2db(sl/n), bgDb=lin2db(sbg/n), contrast=bgDb-meanDb;
    const cx=sx/n, cy=sy/n; let xx=0,yy=0,xy=0; for(const i of pix){const dx=(i%W)*pxX-cx, dy=((i/W)|0)*pxY-cy; xx+=dx*dx; yy+=dy*dy; xy+=dx*dy;}
    xx/=n; yy/=n; xy/=n; const tr=xx+yy, det=xx*yy-xy*xy, disc=Math.sqrt(Math.max(0,tr*tr/4-det)); const fl=pxL*pxL/12;
    const l1=tr/2+disc+fl, l2=Math.max(tr/2-disc,0)+fl;       // + pixel variance: moments of a continuous pixel footprint
    const ang=Math.atan2(l1-fl-xx, xy||1e-12); const ux=Math.abs(xy)<1e-12&&xx>=yy?1:Math.cos(ang), uy=Math.abs(xy)<1e-12&&xx>=yy?0:Math.sin(ang);
    let pmin=Infinity, pmax=-Infinity; for(const i of pix){const t=((i%W)*pxX-cx)*ux+(((i/W)|0)*pxY-cy)*uy; if(t<pmin) pmin=t; if(t>pmax) pmax=t;}
    const ellA=2*Math.sqrt(l1), ellB=2*Math.sqrt(l2);
    // edge step: linear means of the outer water ring vs. the inner boundary (dB difference)
    let oS=0,oN=0,iS=0,iN=0; const seen=new Set();
    for(const i of pix){ let edge=false; for(const j of [i-1,i+1,i-W,i+W]){ if(j<0||j>=N) continue; if(lab[j]!==ci && wm[j]){ edge=true; if(!seen.has(j)){seen.add(j); oS+=filt[j]; oN++;} } } if(edge){iS+=filt[i]; iN++;} }
    const area=n*pxKm2, noiseFrac=nesz!=null? nz/n : null, regional=area>regionalKm2;
    const se=4.343*Math.sqrt(1/(p.enl*n)) ; const u=Math.sqrt(se*se + Math.pow(p.quantDb/Math.sqrt(12),2)*2);
    const id=anoms.length; for(const i of pix) keep[i]=id;
    anoms.push({id, n, pix, cx:cx/pxX, cy:cy/pxY, area, meanDb, bgDb, contrast, contrastU:u, p95Contrast:p95, maxContrast:mx,
      elong:Math.sqrt(l1/l2), ellipseMajorKm:2*ellA/1000, lengthKm:(pmax-pmin+Math.abs(ux)*pxX+Math.abs(uy)*pxY)/1000, fillRatio:(n*pxX*pxY)/(Math.PI*ellA*ellB),
      edgeStep:(oN&&iN)? lin2db(oS/oN)-lin2db(iS/iN) : null, shoreKm: mnD>=1e8? null : (mnD/3)*pxL/1000, touchesShoreBuffer: mnD<1e8 && (mnD/3)*pxL<=p.shoreBufM+1.5*pxL,
      permFrac:pw/n, noiseFrac, regional, cls: (noiseFrac!=null&&noiseFrac>=0.5)?'noise':(regional?'regional':'measurable')});
  });
  anoms.sort((a,b)=>b.area-a.area);
  const top=anoms.slice(0,40); const remap=new Map(top.map((a,k)=>[a.id,k]));
  const labels=new Int16Array(N).fill(-1); for(let i=0;i<N;i++){ if(keep[i]>=0 && remap.has(keep[i])) labels[i]=remap.get(keep[i]); }
  top.forEach((a,k)=>a.id=k);
  // point targets relative to each anomaly: inside, or within 2 km of its boundary (exact pixel distance)
  for(const a of top){ let x0=1e9,x1=-1,y0=1e9,y1=-1; const bnd=[];
    for(const i of a.pix){const x=i%W,y=(i/W)|0; if(x<x0)x0=x; if(x>x1)x1=x; if(y<y0)y0=y; if(y>y1)y1=y;
      if(x===0||y===0||x===W-1||y===H-1||labels[i-1]!==a.id||labels[i+1]!==a.id||labels[i-W]!==a.id||labels[i+W]!==a.id) bnd.push([x,y]); }
    const mX=2000/pxX, mY=2000/pxY; let inside=0, near=0, nearest=Infinity;
    for(const [qx,qy] of pts){ if(qx<x0-mX||qx>x1+mX||qy<y0-mY||qy>y1+mY) continue; const qi=Math.round(qy)*W+Math.round(qx);
      if(labels[qi]===a.id){inside++; nearest=0; continue;}
      let d=Infinity; for(const [bx,by] of bnd){ const dd=Math.hypot((qx-bx)*pxX,(qy-by)*pxY); if(dd<d) d=dd; }
      d/=1000; if(d<=2) near++; if(d<nearest) nearest=d; }
    a.ptInside=inside; a.ptN=near; a.ptMinKm=isFinite(nearest)?nearest:null; }
  let wsum=0; for(let i=0;i<N;i++) if(wm[i]) wsum+=lin[i];
  return {anoms:top, totalFound:anoms.length, labels, nPoints:pts.length, nStruct, fdb, bdb, con, meas, lin, bgLin, wm, nWater,
          lowFrac:nesz!=null? lowN/nWater : null, measFrac:measN/nWater, waterMeanDb:lin2db(wsum/nWater), R, R2, minPx, regionalKm2, params:p};
}
/** temporal persistence: re-measure the same pixels in a second, independently processed scene */
function persistence(run, comp){
  const T=run.T;
  for(const a of run.anoms){ let n=0, sl=0, sb=0, dark=0;
    for(const i of a.pix){ if(!comp.meas[i]) continue; n++; sl+=comp.lin[i]; sb+=comp.bgLin[i]; if(comp.con[i]>=T/2) dark++; }
    a.persistCover=n/a.n;
    if(a.persistCover<0.5){ a.persist=null; a.persistFrac=null; a.persistStatus='untestable'; continue; }
    a.persist=lin2db(sb/n)-lin2db(sl/n); a.persistFrac=dark/n;
    a.persistStatus = (a.persist>=T/2 && a.persistFrac>=0.5)? 'recurs' : (a.persist<T/2 && a.persistFrac<0.25)? 'absent' : 'partial'; }
}
/** acquisition comparability (Sentinel-1C vs -1D etc.) from STAC metadata and scene statistics */
function comparability(sceneA, sceneB, resA, resB){
  const pa=sceneA.properties, pb=sceneB.properties; const notes=[];
  const sameTrack = pa['sat:relative_orbit']!=null && pa['sat:relative_orbit']===pb['sat:relative_orbit'];
  const sameDir = pa['sat:orbit_state']===pb['sat:orbit_state'];
  const dDays = Math.abs(new Date(pa.datetime)-new Date(pb.datetime))/864e5;
  if(pa.platform!==pb.platform) notes.push(`Different satellites (${pa.platform} vs ${pb.platform}). Both units fly the same reference orbit; absolute calibration offsets between units are not quantified here but cancel to first order because contrast is measured against a background in the same scene.`);
  if(pa['sat:relative_orbit']==null || pb['sat:relative_orbit']==null) notes.push('Relative orbit not reported in the metadata — acquisition geometry cannot be verified.');
  else notes.push(sameTrack&&sameDir? `Same relative orbit (${pa['sat:relative_orbit']}) and pass direction (${pa['sat:orbit_state']}): the same ground point is seen at nominally the same incidence angle.` :
    `Different geometry (relative orbit ${pa['sat:relative_orbit']} ${pa['sat:orbit_state']} vs ${pb['sat:relative_orbit']} ${pb['sat:orbit_state']}). Water backscatter depends strongly on incidence angle and RTC does not normalise this, so only relative contrast is compared.`);
  notes.push(`Water background: ${fmt(resA.waterMeanDb)} dB vs ${fmt(resB.waterMeanDb)} dB (Δ ${fmt(resB.waterMeanDb-resA.waterMeanDb)} dB, mostly wind-driven); measurable water ${fmt(resA.measFrac*100,0)} % vs ${fmt(resB.measFrac*100,0)} %.`);
  if(pa['s1:instrument_configuration_ID']!==pb['s1:instrument_configuration_ID']) notes.push('Different instrument configuration IDs.');
  return {sameTrack, sameDir, dDays, sameGeometry:sameTrack&&sameDir, notes};
}

/* ------------------------------------------------------------------ */
/* 9. Set-membership reasoning engine (v2: four evidence levels)        */
/* Each check returns one of:                                          */
/*   'supports'    – a measurement positively favours the explanation   */
/*                   over at least one alternative                      */
/*   'consistent'  – measured, and inside the explanation's feasible set*/
/*   'untested'    – not measured / not measurable (never counts as     */
/*                   support or consistency)                            */
/*   'contradicts' – a measured interval lies entirely outside the set  */
/* 'context' rows (infrastructure) never change any status.             */
/* ------------------------------------------------------------------ */
const HYP = [
  {id:'oil', name:'Industrial / mineral-oil surface film', short:'Oil film'},
  {id:'bio', name:'Natural (biogenic) surface film, e.g. algal/cyanobacterial', short:'Biogenic film'},
  {id:'wind', name:'Low-wind area (no Bragg waves)', short:'Low wind'},
  {id:'curr', name:'Current front / internal wave / shear line', short:'Current effect'},
  {id:'rain', name:'Rain cell (damped or enhanced roughness)', short:'Rain cell'},
  {id:'shore', name:'Shoreline, shallow-water or water-mask artefact', short:'Shore artefact'},
  {id:'noise', name:'Sensor noise floor (signal too weak to interpret)', short:'Noise floor'},
];
const EV = {supports:['ev-sup','▲','supports'], consistent:['ev-con','✓','consistent'], untested:['ev-unt','?','untested'], contradicts:['ev-bad','✗','contradicts'], context:['ev-ctx','○','context only']};
function buildObs(a, run, ctx){
  const w=ctx.wind; const sensor=run.sensor; const nz=sensor.nesz;
  const conflict = w && nz!=null && (w.ws+2)<3 && a.bgDb > nz+6;
  return [
    {k:'contrast', label:'Backscatter contrast vs. local background', val:`${fmt(a.contrast)} ± ${fmt(a.contrastU??0.3,2)} dB (95th pct ${fmt(a.p95Contrast??a.maxContrast)} dB)`, src:`${sensor.name}, ${run.sceneId}`, kind:'measured'},
    {k:'noise', label:'Anomaly level above noise floor', val: nz!=null? `${fmt(a.meanDb-nz)} dB (mean ${fmt(a.meanDb)} dB; NESZ ≈ ${nz} dB γ⁰)${a.noiseFrac!=null?`; ${fmt(a.noiseFrac*100,0)} % of pixels within 1 dB of NESZ`:''}` : 'NESZ not available — noise test not possible', src:'Sensor specification', kind: nz!=null?'measured':'missing'},
    {k:'wind', label:'10 m wind speed at acquisition', val: w? `${fmt(w.ws)} m/s from ${fmt(w.wd,0)}° (ERA5 ${w.time.slice(11,16)} UTC; interval ±2 m/s)${conflict?` — conflicts with the SAR image: the surrounding water is wind-roughened (background ${fmt(a.bgDb)} dB, ${fmt(a.bgDb-nz)} dB above noise), so ERA5 is not used`:''}` : (ctx.windErr||'Not retrieved'), iv: (w && !conflict)? [Math.max(0,w.ws-2), w.ws+2] : null, src:'ERA5 via Open-Meteo (≈25 km grid)', kind: w?(conflict?'conflict':'model'):'missing'},
    {k:'bg', label:'Local background backscatter', val:`${fmt(a.bgDb)} dB${nz!=null?` (${fmt(a.bgDb-nz)} dB above noise floor)`:''}`, src:sensor.name, kind:'measured'},
    {k:'scale', label:'Feature size vs. background window', val: a.regional!=null? `${fmt(a.area,2)} km² ${a.regional?'exceeds':'within'} the ${fmt(run.res?.regionalKm2 ?? run.meta?.scene?.regional_km2,0)} km² background window — ${a.regional?'regional darkening; local contrast is ill-defined':'local contrast well defined'}` : 'not computed', src:'Segmentation', kind:a.regional!=null?'measured':'missing'},
    {k:'precip', label:'Precipitation ±1 h', val: w? `${fmt(w.precip3h,1)} mm (ERA5)` : '—', src:'ERA5 (cannot resolve small convective cells)', kind: w?'model':'missing'},
    {k:'shore', label:'Distance to land / permanent-water share', val:`${a.shoreKm==null?'no land in window':fmt(a.shoreKm,2)+' km from land'} (in-water structures excluded) · ${fmt(a.permFrac*100,0)} % permanent water`, src:'JRC Global Surface Water occurrence', kind:'measured'},
    {k:'shape', label:'Shape', val:`${fmt(a.area,2)} km², extent ${fmt(a.lengthKm,1)} km, axis ratio ${fmt(a.elong,1)}${a.fillRatio!=null?`, fill ${fmt(a.fillRatio,2)}`:''}, edge step ${fmt(a.edgeStep)} dB`, src:'Segmentation', kind:'measured'},
    {k:'persist', label:'Same pixels in comparison scene', val: a.persistStatus==='untestable'? `untestable — only ${fmt((a.persistCover||0)*100,0)} % of the pixels are measurable in ${run.compId}` : a.persist!=null? `${a.persistStatus}: contrast ${fmt(a.persist)} dB, ${fmt(a.persistFrac*100,0)} % of pixels dark (${run.compId})` : 'No comparison scene analysed', src:'Second acquisition'+(run.compare? (run.compare.sameGeometry?' (same geometry)':' (different geometry)') :''), kind: a.persist!=null?'measured':'missing'},
    {k:'pts', label:'Bright point targets in the SAR image (platforms/vessels, unidentified)', val: a.ptN!=null? `${a.ptInside??0} inside, ${a.ptN} within 2 km of the edge; nearest ${a.ptMinKm!=null?fmt(a.ptMinKm,1)+' km':'—'}` : 'not computed', src:'Same SAR scene (γ⁰ ≥ max(background, NESZ) + 10 dB)', kind:'context'},
    {k:'infra', label:'Mapped infrastructure within 15 km', val: ctx.fac? facSummary(ctx.fac) : (ctx.facErr||'Not retrieved'), src:'OpenStreetMap (unverified)', kind:'context'},
  ];
}
function facSummary(f){ if(!f.length) return 'none mapped'; const c={}; f.forEach(x=>c[x.cat]=(c[x.cat]||0)+1);
  return Object.entries(c).sort((a,b)=>b[1]-a[1]).slice(0,4).map(([k,v])=>`${v}× ${k}`).join(', ')+(f.length>=600?' (truncated)':''); }
function reason(a, run, ctx){
  const obs=buildObs(a, run, ctx); const O=Object.fromEntries(obs.map(o=>[o.k,o]));
  const nz=run.sensor.nesz, W=O.wind.iv, R=[]; const shoreKm = a.shoreKm==null? Infinity : a.shoreKm;  // null = no land in the window
  const chk=(h,k,ev,why)=>R.push({h,k,ev,why});
  // Noise floor (decisive when the anomaly itself sits at the floor)
  const atFloor = nz!=null && (a.meanDb<=nz+1 || (a.noiseFrac!=null && a.noiseFrac>=0.5));
  if(nz==null){ chk('noise','noise','untested','No noise-equivalent σ⁰ for this product.'); }
  else if(atFloor){ chk('noise','noise','supports',`Anomaly mean is ${fmt(a.meanDb-nz)} dB above NESZ${a.noiseFrac!=null?` and ${fmt(a.noiseFrac*100,0)} % of its pixels are within 1 dB of it`:''}: the "dark" signal is at the instrument floor.`);
    ['oil','bio','wind','curr','rain','shore'].forEach(h=>chk(h,'noise','untested','Signal at the noise floor: the surface cannot be characterised.')); }
  else if(a.meanDb>=nz+3) chk('noise','noise','contradicts',`Anomaly is ${fmt(a.meanDb-nz)} dB above NESZ — a measurable surface signal.`);
  else chk('noise','noise','consistent','Anomaly is 1–3 dB above NESZ; noise contributes substantially.');
  // Wind (ERA5) — detection windows: Brekke & Solberg 2005; Fingas & Brown 2014; Gade et al. 1998
  if(W){
    chk('oil','wind', W[0]>14?'contradicts':(W[1]<2?'untested':'consistent'), W[0]>14?'Lower wind bound > 14 m/s: oil films are generally not observable.':(W[1]<2?'Upper wind bound < 2 m/s: no Bragg waves for a film to damp — film presence cannot be assessed.':'Wind interval overlaps the ~2–12 m/s window in which films are observable.'));
    chk('bio','wind', W[0]>10?'contradicts':'consistent', W[0]>10?'Lower wind bound > 10 m/s: natural monomolecular films are dispersed.':'Wind compatible with natural films (best seen at low–moderate wind).');
    const shelter=shoreKm<=5;
    if(W[0]>6 && !shelter) chk('wind','wind','contradicts',`Lower wind bound ${fmt(W[0])} m/s > 6 m/s and ${isFinite(shoreKm)?fmt(shoreKm,1)+' km from land':'no land in the window'} (no sheltering).`);
    else if(W[1]<3) chk('wind','wind','supports',`ERA5 indicates calm conditions (upper bound ${fmt(W[1])} m/s) that are not contradicted by the SAR background.`);
    else chk('wind','wind','consistent', shelter?'Within 5 km of land: local sheltering possible.':'Wind interval reaches values where Bragg waves are weak.');
  } else ['oil','bio','wind'].forEach(h=>chk(h,'wind','untested', O.wind.kind==='conflict'?'ERA5 wind contradicts the SAR-observed roughness — not used.':'Wind not available.'));
  // Scale
  if(a.regional) { chk('wind','scale','consistent','Darkening larger than the background window is typical of wind-speed variability.'); chk('oil','scale','consistent','Large slicks exist, but local contrast is ill-defined at this scale.'); }
  // Shoreline (land, not structures)
  if(a.permFrac>=0.95 && shoreKm>=1) chk('shore','shore','contradicts',`${fmt(a.permFrac*100,0)} % permanent water and ${isFinite(shoreKm)?fmt(shoreKm,2)+' km from land':'no land in the analysis window'}.`);
  else if(a.touchesShoreBuffer) chk('shore','shore','supports','Feature reaches the shoreline buffer: mixed land/water pixels or shallow-water damping are likely contributors.');
  else chk('shore','shore','consistent','Near land or on seasonally wet pixels.');
  // Rain
  if(ctx.wind) chk('rain','precip', ctx.wind.precip3h>=0.5?'supports':'untested', ctx.wind.precip3h>=0.5?`ERA5 shows ${fmt(ctx.wind.precip3h,1)} mm near the acquisition time.`:'ERA5 shows no rain, but a 25 km reanalysis cannot exclude a convective cell — untested.');
  else chk('rain','precip','untested','Precipitation not available.');
  chk('curr','shape','untested','No current, plume or stratification data are connected; shape alone cannot test this.');
  // Persistence (not discriminating between explanations → consistent, never supports)
  if(a.persistStatus && a.persistStatus!=='untestable' && a.persist!=null){
    const rec=a.persistStatus==='recurs';
    chk('oil','persist','consistent', rec?'Recurs at the same pixels — consistent with a fixed source (also with recurring sheltered calm).':'Not recurring — consistent with an intermittent release or a transient film/wind.');
    chk('wind','persist','consistent', rec?'Recurrence also occurs in topographically sheltered areas.':'Transient — consistent with variable wind.');
  } else chk('oil','persist','untested', a.persistStatus==='untestable'?'Comparison scene not measurable over these pixels.':'No comparison scene.');
  // Context (never changes status)
  if(a.ptN!=null) chk('oil','pts','context', `${(a.ptInside||0)+a.ptN} radar-bright point targets inside or within 2 km — possible platforms or vessels; their presence is not evidence of a discharge.`);
  if(ctx.fac) chk('oil','infra','context', ctx.fac.length? 'Mapped industrial features nearby (unverified; proximity ≠ causation).':'No mapped features (absence of mapping is not evidence of absence).');
  const hyps=HYP.map(h=>{ const rows=R.filter(r=>r.h===h.id); const ev=rows.map(r=>r.ev);
    let status = ev.includes('contradicts')?'contradicted' : ev.includes('supports')?'supported' : ev.includes('consistent')?'consistent' : 'untested';
    if(atFloor && h.id!=='noise' && status!=='contradicted') status='untested';   // a signal at the floor cannot test any surface explanation
    return {...h, rows, status}; });
  const by=s=>hyps.filter(h=>h.status===s); const oil=hyps.find(h=>h.id==='oil');
  let verdict;
  if(hyps.find(h=>h.id==='noise').status==='supported') verdict={cls:'neg', title:'Not interpretable — signal at the noise floor', text:'The anomaly’s backscatter is at the instrument noise floor, so no surface explanation (including an oil film) can be tested from this scene. Choose a windier acquisition.'};
  else if(oil.status==='contradicted') verdict={cls:'neg', title:'Industrial surface film contradicted', text:'A measured condition lies outside the range in which an oil film could produce this feature.'};
  else if(by('contradicted').length===HYP.length-1 && oil.status!=='untested') verdict={cls:'pos', title:'Only a surface-film explanation remains', text:'All alternatives are contradicted. SAR still cannot identify the substance — in-situ sampling is required.'};
  else verdict={cls:'inc', title:'Inconclusive', text:`Supported: ${by('supported').map(h=>h.short).join(', ')||'none'} · Consistent: ${by('consistent').map(h=>h.short).join(', ')||'none'} · Untested: ${by('untested').map(h=>h.short).join(', ')||'none'} · Contradicted: ${by('contradicted').map(h=>h.short).join(', ')||'none'}.`};
  const needs=[];
  if(verdict.cls==='neg' && hyps.find(h=>h.id==='noise').status==='supported') needs.push('A scene with wind-roughened water (background ≥ NESZ + 4 dB) over this location.');
  if(!W) needs.push(O.wind.kind==='conflict'? 'Scene-scale wind retrieved from the SAR image itself (e.g. CMOD5.N on Sentinel-1 VV) — ERA5 disagrees with the observed roughness.' : 'Wind at acquisition time (ERA5 once available, or SAR-derived wind).');
  if(['supported','consistent','untested'].includes(hyps.find(h=>h.id==='bio').status)) needs.push('Multi-frequency comparison (NISAR L-band vs Sentinel-1 C-band) and near-simultaneous Sentinel-2 optical imagery to separate biogenic from mineral-oil films (Gade et al. 1998).');
  if(a.persist==null) needs.push('A second, measurable acquisition with the same geometry (same relative orbit) to test recurrence.');
  if(hyps.find(h=>h.id==='rain').status!=='contradicted') needs.push('Satellite rainfall (GPM IMERG, 30 min) to test rain cells.');
  needs.push('Current/plume data (river discharge, tide phase) to test frontal explanations.');
  needs.push('In-situ water sampling or aerial observation — the only way to identify the substance and its source.');
  const cannot=['The chemical identity, thickness or volume of the film.','Which facility (if any) released it — proximity is not causation.','Whether any release was legal or illegal.'];
  return {obs, hyps, verdict, needs, cannot};
}

/* ------------------------------------------------------------------ */
/* 10. Map                                                              */
/* ------------------------------------------------------------------ */
let map, L_hot, L_foot, L_sar, L_img, L_anom, L_comp, L_fac, L_win, L_nisar, L_sel;
function initMap(){
  map=L.map('map',{zoomControl:true, preferCanvas:true}).setView([15,-40],3);
  // Basemaps: key-free, documented public tile services (CARTO now returns "API KEY REQUIRED" tiles)
  const SARATTR=' | SAR: ESA Copernicus via Microsoft Planetary Computer; NISAR via ASF DAAC';
  const osm=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19, attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'+SARATTR});
  const esriImg=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:19, attribution:'Imagery © Esri, Maxar, Earthstar Geographics'+SARATTR});
  const esriGray=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',{maxZoom:16, attribution:'© Esri, HERE, Garmin, © OpenStreetMap contributors'+SARATTR});
  osm.addTo(map); let osmErr=0;
  osm.on('tileerror',()=>{ if(++osmErr===8 && map.hasLayer(osm)){ map.removeLayer(osm); esriGray.addTo(map); toast('OpenStreetMap tiles unavailable — switched to Esri Light Gray basemap'); } });
  L.control.layers({'OpenStreetMap':osm,'Esri World Imagery':esriImg,'Esri Light Gray':esriGray},null,{position:'bottomleft'}).addTo(map);
  map.createPane('sar'); map.getPane('sar').style.zIndex=350;     // SAR tiles + NISAR quick-look
  map.createPane('ovl'); map.getPane('ovl').style.zIndex=380;     // analysed window image (above tiles)
  map.createPane('anom'); map.getPane('anom').style.zIndex=390;   // detected anomalies
  L_hot=L.layerGroup().addTo(map); L_foot=L.layerGroup().addTo(map); L_fac=L.layerGroup().addTo(map); L_win=L.layerGroup().addTo(map); L_sel=L.layerGroup().addTo(map);
}
function drawHotspots(){
  L_hot.clearLayers();
  for(const h of filteredHotspots()){
    const [w,s,e,n]=h.search; const sel=h.id===S.hs;
    L.rectangle([[s,w],[n,e]],{color:sel?'#1f5fae':'#5d6b7a', weight:sel?2:1, dashArray:'4 4', fill:false, interactive:false}).addTo(L_hot);
    L.circleMarker(h.center,{radius:sel?10:8, color:'#fff', weight:2, fillColor:sel?'#1f5fae':'#ff2d7a', fillOpacity:1})
      .bindTooltip(`<b>${esc(h.name)}</b><br>${esc(h.country)} · ${esc(h.waterLabel)}`).on('click',()=>selectHotspot(h.id)).addTo(L_hot);
  }
}
function drawFootprints(){
  L_foot.clearLayers(); if(S.screen!=='discover') return;
  const add=(geom, col, tip)=>{ try{ L.geoJSON(geom,{style:{color:col, weight:1, fillOpacity:0.03}, interactive:false}).addTo(L_foot);}catch{} };
  S.acq.nisar.slice(0,6).forEach(f=>add(f.geometry,'#7c3aed'));
  S.acq.s1.slice(0,4).forEach(a=>a.items.forEach(f=>add(f.geometry,'#0891b2')));
}
function setLegend(){
  const el=$('#legend'); const parts=[];
  if(S.screen==='discover'){
    parts.push('<b>Discover</b><br><i style="background:#ff2d7a;border-radius:50%"></i>Candidate hotspot');
    if(S.acq.nisar.length||S.acq.s1.length) parts.push('<i style="border:1.5px solid #7c3aed"></i>NISAR GCOV footprint<br><i style="border:1.5px solid #0891b2"></i>Sentinel-1 RTC footprint');
  } else {
    parts.push('<b>Investigate</b>');
    if(S.layers.sar) parts.push(`γ⁰ VV <span class="sw"></span> ${DB_MIN}…${DB_MAX} dB`);
    parts.push('<i style="background:#ff2d7a"></i>Measurable dark anomaly (SAR-observed)<br><i style="background:#f59e0b"></i>Regional darkening (> background window)<br><i style="background:#8b5cf6"></i>At noise floor (not interpretable)<br><i style="background:rgba(30,64,140,.35)"></i>Water not measurable (background &lt; NESZ + T + 1 dB)<br><i style="background:#ffd400"></i>Selected anomaly<br><i style="border:1.5px dashed #1f5fae"></i>Analysis window');
    if(S.ctx[S.sel]?.fac?.length){ const cats=[...new Set(S.ctx[S.sel].fac.map(f=>f.cat))]; parts.push('<b>Mapped infrastructure (OSM, unverified)</b><br>'+cats.map(c=>`<i style="background:${CAT_COL[c]};border-radius:50%"></i>${esc(c)}`).join('<br>')); }
  }
  el.innerHTML=parts.join('<br>');
}
function setMapTools(){
  const el=$('#maptools'); if(S.screen!=='investigate'){ el.innerHTML=''; return; }
  const b=(k,lbl,dis)=>`<button class="pill ${S.layers[k]?'on':''}" data-layer="${k}" ${dis?'disabled style="opacity:.45"':''}>${lbl}</button>`;
  el.innerHTML = b('sar','SAR scene',!S.scene) + b('anom','Anomalies',!S.run) + b('comp','Comparison',!S.run?.compUrl) + b('fac','Facilities',!S.ctx[S.sel]?.fac) + b('nisar','NISAR quick-look',!S.nisarSel);
  el.querySelectorAll('button').forEach(x=>x.onclick=()=>{ if(x.disabled) return; const k=x.dataset.layer; S.layers[k]=!S.layers[k]; if(k==='comp'&&S.layers.comp) S.layers.sar=S.layers.sar; refreshLayers(); setMapTools(); setLegend(); });
}
function refreshLayers(){
  [L_sar,L_img,L_anom,L_comp,L_nisar].forEach(l=>{ if(l) map.removeLayer(l); });
  L_sar=L_img=L_anom=L_comp=L_nisar=null;
  if(S.screen!=='investigate') { L_fac.clearLayers(); L_sel.clearLayers(); L_win.clearLayers(); return; }
  if(S.layers.nisar && S.nisarSel){ const g=S.nisarSel.geometry.coordinates[0]; const xs=g.map(p=>p[0]), ys=g.map(p=>p[1]);
    const u=(S.nisarSel.properties.browse||[]).find(x=>/_LATLON\.png$/.test(x));
    if(u) L_nisar=L.imageOverlay(u,[[Math.min(...ys),Math.min(...xs)],[Math.max(...ys),Math.max(...xs)]],{opacity:0.9, pane:'sar'}).addTo(map); }
  if(S.scene && S.layers.sar && !(S.layers.comp && S.run?.compUrl)) L_sar=L.layerGroup(S.scene.items.map(it=>L.tileLayer(API.s1Tiles(it.id),{pane:'sar', opacity:1, maxNativeZoom:14, maxZoom:18}))).addTo(map);
  if(S.run){ const [w,s,e,n]=S.run.bbox; const B=[[s,w],[n,e]];
    if(S.layers.comp && S.run.compUrl) L_comp=L.imageOverlay(S.run.compUrl,B,{pane:'ovl'}).addTo(map);
    else if(S.layers.sar) L_img=L.imageOverlay(S.run.url,B,{pane:'ovl'}).addTo(map);
    if(S.layers.anom) L_anom=L.imageOverlay(anomalyOverlay(),B,{pane:'anom', opacity:0.85}).addTo(map); }
  drawSel(); drawFacilities(); drawWindow();
}
function anomalyOverlay(){
  const r=S.run; const c=document.createElement('canvas'); c.width=r.W; c.height=r.H; const g=c.getContext('2d'); const im=g.createImageData(r.W,r.H);
  const ca=currentAnom(); const selK = (ca && r.anoms.includes(ca)) ? ca.id : null;
  const COL={measurable:[255,45,122],regional:[245,158,11],noise:[139,92,246]};
  for(let i=0;i<r.W*r.H;i++){ const l=r.res.labels[i];
    if(l<0){ if(r.res.wm && r.res.meas && r.res.wm[i] && !r.res.meas[i]){ im.data[i*4]=30; im.data[i*4+1]=64; im.data[i*4+2]=140; im.data[i*4+3]=70; } continue; }
    const s=(selK===l); const c0=s?[255,212,0]:(COL[r.anoms[l].cls]||COL.measurable);
    im.data[i*4]=c0[0]; im.data[i*4+1]=c0[1]; im.data[i*4+2]=c0[2]; im.data[i*4+3]=s?235:170; }
  g.putImageData(im,0,0); return c.toDataURL();
}
function drawWindow(){ L_win.clearLayers(); const b=S.run?.bbox || S.pendingWin; if(!b || S.screen!=='investigate') return;
  L.rectangle([[b[1],b[0]],[b[3],b[2]]],{color:'#1f5fae', weight:1.5, dashArray:'6 4', fill:false, interactive:false}).addTo(L_win); }
function drawSel(){ L_sel.clearLayers(); const a=currentAnom(); if(!a || S.screen!=='investigate') return;
  L.circleMarker(a.latlon,{radius:14, color:'#ffd400', weight:3, fill:false}).bindTooltip(`Anomaly ${a.label}`).addTo(L_sel); }
function drawFacilities(){ L_fac.clearLayers(); const f=S.ctx[S.sel]?.fac; if(!f || !S.layers.fac || S.screen!=='investigate') return;
  f.slice(0,600).forEach(x=>L.circleMarker([x.lat,x.lon],{radius:3.5, color:CAT_COL[x.cat], weight:1, fillOpacity:0.8})
    .bindTooltip(`${esc(x.cat)}${x.tags.name?': '+esc(x.tags.name):''}<br>${fmt(x.dkm,1)} km · OSM ${esc(x.id)} (unverified)`).addTo(L_fac)); }

/* ------------------------------------------------------------------ */
/* 11. Workflow actions                                                 */
/* ------------------------------------------------------------------ */
function filteredHotspots(){
  return HOTSPOTS.filter(h=> (S.filt.industry==='all' || h.industries.includes(S.filt.industry)) && (S.filt.water==='all' || h.water===S.filt.water));
}
function selectHotspot(id){
  if(S.hs!==id){ S.hs=id; S.acq={status:'idle', nisar:[], s1:[], err:{}, hs:null}; S.scene=null; S.comp=null; S.nisarSel=null; S.run=null; S.sel=null; S.pendingWin=null; S.scr=null; S.runStatus=null; }
  const h=hsById(id); const [w,s,e,n]=h.search; map.fitBounds([[s,w],[n,e]],{padding:[30,30]});
  drawHotspots(); drawFootprints(); render();
}
async function findData(){
  const h=hsById(S.hs); S.acq={status:'loading', nisar:[], s1:[], err:{}, hs:h.id}; render();
  const [a,b]=await Promise.allSettled([API.nisar(h,S.filt.from,S.filt.to), API.s1(h.search,S.filt.from,S.filt.to)]);
  if(a.status==='fulfilled') S.acq.nisar=a.value; else S.acq.err.nisar=a.reason.message;
  if(b.status==='fulfilled') S.acq.s1=b.value; else S.acq.err.s1=b.reason.message;
  S.acq.status='done';
  if(S.acq.s1.length && !S.scene) S.scene=S.acq.s1[0];
  if(S.acq.s1.length) screenScenes();
  if(S.acq.nisar.length && !S.nisarSel) S.nisarSel=S.acq.nisar[0];
  drawFootprints(); render();
}
/** Surface-condition screening: median water γ⁰ of each acquisition over the hotspot demo window.
 *  Bragg scattering (and hence any slick contrast) needs wind-roughened water well above the noise floor. */
async function screenScenes(){
  const h=hsById(S.hs), bbox=h.demo, n=128; S.scr={state:'loading', done:0, total:S.acq.s1.length}; render();
  try{
    const gs=await API.gswItem(bbox); const occ=new Float32Array(n*n);
    for(const it of gs){ const g=await API.png(API.gswBbox(it.id,bbox,n,n)); URL.revokeObjectURL(g.url); for(let i=0;i<n*n;i++) if(g.data[i*4+3]>0) occ[i]=Math.max(occ[i], g.data[i*4]/255*100); }
    const nW=occ.reduce((a,v)=>a+(v>=80),0);
    const todo=[...S.acq.s1]; const hsAtStart=S.hs;
    const worker=async()=>{ while(todo.length){ const a=todo.shift(); const v=[];
      try{ const seen=new Uint8Array(n*n);
        for(const it of a.items){ const im=await API.png(API.s1Bbox(it.id,bbox,n,n)); URL.revokeObjectURL(im.url);
          for(let i=0;i<n*n;i++) if(!seen[i] && occ[i]>=80 && im.data[i*4+3]>0){ seen[i]=1; v.push(DB_MIN+im.data[i*4]*(DB_MAX-DB_MIN)/255); } }
        v.sort((x,y)=>x-y); const med=v.length? v[v.length>>1] : null; const cover=v.length/Math.max(1,nW);
        a.scr={median:med, cover, ok: med!=null && cover>=0.3 && med>=SENSORS.S1.nesz+4};
      }catch(e){ a.scr={err:e.message, ok:false}; }
      if(S.hs!==hsAtStart) return; S.scr.done++; if(S.scr.done%4===0) render(); } };
    await Promise.all([worker(),worker(),worker(),worker(),worker(),worker()]);
    if(S.hs!==hsAtStart) return;
    const ok=S.acq.s1.filter(a=>a.scr?.ok);
    S.scr={state:'done', ok:ok.length, total:S.acq.s1.length};
    if(ok.length && !S.run){ S.scene=ok[0]; S.comp=ok.find(a=>a!==ok[0] && a.properties['sat:orbit_state']===ok[0].properties['sat:orbit_state']) || ok[1] || null; refreshLayers(); }
  }catch(e){ S.scr={state:'error', msg:e.message}; }
  render();
}
function scrTag(a){ const r=a.scr; if(!r) return ''; if(r.err) return ' · screening failed'; if(r.median==null||r.cover<0.3) return ' · outside window';
  return r.ok? ` · ✓ usable (water ${fmt(r.median)} dB)` : ` · low signal (${fmt(r.median)} dB)`; }
function gotoScreen(s){
  S.screen=s; S.visited.add(s); document.getElementById('main').classList.toggle('wide', !['discover','investigate'].includes(s));
  if(['discover','investigate'].includes(s)) setTimeout(()=>map.invalidateSize(),50);
  if(s==='investigate' && !S.run){ const h=hsById(S.hs); const [w,so,e,n]=h.demo; S.pendingWin=h.demo; map.fitBounds([[so,w],[n,e]]); }
  drawFootprints(); refreshLayers(); render();
}
function windowFromView(){
  const b=map.getBounds(); const w=b.getWest(), e=b.getEast(), s=b.getSouth(), n=b.getNorth();
  // shrink to at most 0.4° x 0.4° around the centre
  const cx=(w+e)/2, cy=(s+n)/2, hw=Math.min((e-w)/2,0.2), hh=Math.min((n-s)/2,0.2);
  return [cx-hw, cy-hh, cx+hw, cy+hh].map(v=>+v.toFixed(4));
}
async function loadScene(item, bbox){
  const lat=(bbox[1]+bbox[3])/2; const wM=(bbox[2]-bbox[0])*111320*Math.cos(lat*Math.PI/180), hM=(bbox[3]-bbox[1])*110540;
  let px=Math.max(20, Math.max(wM,hM)/1024); px=Math.ceil(px/5)*5;
  const W=Math.round(wM/px), H=Math.round(hM/px);
  const N=W*H, db=new Float32Array(N), valid=new Uint8Array(N); let nv=0;
  const c=document.createElement('canvas'); c.width=W; c.height=H; const g=c.getContext('2d'); const out=g.createImageData(W,H);
  for(const it of item.items){ // mosaic all slices of the datatake
    const img=await API.png(API.s1Bbox(it.id,bbox,W,H)); URL.revokeObjectURL(img.url);
    for(let i=0;i<N;i++){ if(!valid[i] && img.data[i*4+3]>0){ valid[i]=1; nv++; const v=img.data[i*4]; db[i]=DB_MIN+v*(DB_MAX-DB_MIN)/255;
      out.data[i*4]=out.data[i*4+1]=out.data[i*4+2]=v; out.data[i*4+3]=255; } } }
  g.putImageData(out,0,0);
  return {W,H,px,pxX:wM/W,pxY:hM/H,db,valid,nv,url:c.toDataURL()};
}
async function runAnalysis(useView){
  if(!S.scene){ toast('Select a Sentinel-1 scene first'); return; }
  const bbox = useView ? windowFromView() : hsById(S.hs).demo;
  S.pendingWin=bbox; S.runStatus={state:'loading', msg:'Reading calibrated γ⁰ subset from Planetary Computer…'}; S.run=null; S.sel=null; render(); drawWindow();
  try{
    const sc=await loadScene(S.scene,bbox);
    if(sc.nv < sc.W*sc.H*0.2) throw new Error('This scene covers less than 20 % of the analysis window. Choose another date or move the window.');
    S.runStatus.msg='Reading JRC Global Surface Water mask…'; render();
    const gsw=await API.gswItem(bbox);
    const occ=new Float32Array(sc.W*sc.H).fill(NaN);
    for(const it of gsw){ const g=await API.png(API.gswBbox(it.id,bbox,sc.W,sc.H));
      for(let i=0;i<sc.W*sc.H;i++){ if(g.data[i*4+3]>0) occ[i]=isNaN(occ[i])? g.data[i*4]/255*100 : Math.max(occ[i], g.data[i*4]/255*100); } }
    S.runStatus.msg='Speckle filtering, adaptive thresholding, segmentation…'; render();
    await new Promise(r=>setTimeout(r,20));
    const p={T:S.params.T, minKm2:S.params.minKm2, nesz:SENSORS.S1.nesz, pxX:sc.pxX, pxY:sc.pxY};
    const res=analyzeScene(sc.db, sc.valid, occ, sc.W, sc.H, sc.px, p);
    if(res.error) throw new Error(res.error);
    const run={src:'browser', bbox, W:sc.W, H:sc.H, px:sc.px, url:sc.url, res, anoms:res.anoms, T:p.T, sensor:SENSORS.S1,
      sceneId:S.scene.id, sceneTime:S.scene.properties.datetime, platform:S.scene.properties.platform, orbit:S.scene.properties['sat:orbit_state'], occ};
    run.anoms.forEach(a=>{ a.latlon=[bbox[3]-(a.cy+0.5)/sc.H*(bbox[3]-bbox[1]), bbox[0]+(a.cx+0.5)/sc.W*(bbox[2]-bbox[0])]; a.label='S1-'+(a.id+1); a.key=run.sceneId+'#'+a.id; });
    if(S.comp && S.comp.id!==S.scene.id){
      S.runStatus.msg='Analysing comparison scene for persistence…'; render();
      try{ const c2=await loadScene(S.comp,bbox); const r2=analyzeScene(c2.db,c2.valid,occ,c2.W,c2.H,c2.px,p);
        if(!r2.error){ persistence(run, r2); run.compId=S.comp.id; run.compTime=S.comp.properties.datetime; run.compUrl=c2.url; run.compStats=r2;
          run.compare=comparability(S.scene,S.comp,res,r2); run.compPlatform=S.comp.properties.platform; } else run.compErr=r2.error; }
      catch(e){ run.compErr=e.message; }
    }
    S.run=run; S.runStatus={state:'done'}; S.layers.anom=true;
    if(run.anoms.length) selectAnom(run.anoms[0].key, false);
  }catch(e){ S.runStatus={state:'error', msg:e.message}; }
  refreshLayers(); setMapTools(); setLegend(); render();
}
function allAnoms(){ const a=[...(S.run?.anoms||[])]; S.imported.forEach(r=>a.push(...r.anoms)); return a; }
function currentAnom(){ return allAnoms().find(a=>a.key===S.sel) || null; }
function runOf(a){ if(!a) return null; if(S.run && S.run.anoms.includes(a)) return S.run; return S.imported.find(r=>r.anoms.includes(a)); }
async function selectAnom(key, fly=true){
  S.sel=key; const a=currentAnom(); if(!a) return;
  if(S.run && S.run.anoms.includes(a)) { /* browser run: label index */ S.sel=key; }
  if(fly) map.panTo(a.latlon);
  refreshLayers(); render();
  const run=runOf(a); const c=S.ctx[key] || (S.ctx[key]={});
  if(!c.wind && !c.windLoading){ c.windLoading=true; API.wind(a.latlon[0],a.latlon[1],run.sceneTime).then(w=>c.wind=w).catch(e=>c.windErr=e.message).finally(()=>{c.windLoading=false; render();}); }
  if(!c.fac && !c.facLoading){ c.facLoading=true; API.facilities(a.latlon[0],a.latlon[1]).then(f=>c.fac=f.sort((x,y)=>x.dkm-y.dkm)).catch(e=>c.facErr=e.message).finally(()=>{c.facLoading=false; drawFacilities(); setMapTools(); setLegend(); render();}); }
}
function importResults(file){
  const rd=new FileReader(); rd.onload=()=>{ try{
    const j=JSON.parse(rd.result); if(!j.anomalies || !j.source) throw new Error('Not an analysis.py result file');
    const sensor = j.source.platform==='NISAR' ? {...SENSORS.NISAR, nesz:j.nesz_db ?? null} : {...SENSORS.S1};
    const run={src:'file', bbox:j.bbox, sensor, sceneId:j.source.granule, sceneTime:j.source.start, platform:j.source.platform, T:j.params?.contrast_db ?? 3, file:file.name, meta:j,
      anoms:j.anomalies.map((x,k)=>({...x, id:k, latlon:x.centroid, area:x.area_km2, meanDb:x.mean_db, bgDb:x.bg_db, contrast:x.contrast_db, contrastU:x.contrast_unc_db ?? null, p95Contrast:x.p95_contrast_db ?? null, maxContrast:x.max_contrast_db,
        elong:x.elongation, lengthKm:x.length_km, ellipseMajorKm:x.ellipse_major_km ?? null, fillRatio:x.fill_ratio ?? null, ptN:x.point_targets_2km ?? null, ptInside:x.point_targets_inside ?? null, ptMinKm:x.nearest_point_target_km ?? null, edgeStep:x.edge_step_db, shoreKm:x.shore_km, touchesShoreBuffer:x.touches_shore_buffer ?? null, permFrac:x.perm_water_frac,
        noiseFrac:x.noise_frac ?? null, regional:x.regional ?? null, cls:x.class ?? null, persist:x.persist_db ?? null, persistFrac:x.persist_frac ?? null, persistCover:x.persist_cover ?? null, persistStatus:x.persist_status ?? (x.persist_db!=null?'measured':null),
        label:(j.source.platform==='NISAR'?'NI-':'PY-')+(k+1), key:j.source.granule+'#'+k}))};
    S.imported=S.imported.filter(r=>r.sceneId!==run.sceneId); S.imported.push(run);
    if(j.quicklook_png && j.bbox){ const [w,s,e,n]=j.bbox; L.imageOverlay(j.quicklook_png,[[s,w],[n,e]],{pane:'anom',opacity:0.95}).bindTooltip(`${esc(run.platform)} analysis.py result`).addTo(map); map.fitBounds([[s,w],[n,e]]); }
    toast(`Loaded ${run.anoms.length} anomalies from ${file.name}`); render();
  }catch(e){ toast('Could not load file: '+e.message, 6000); } }; rd.readAsText(file);
}

/* ------------------------------------------------------------------ */
/* 12. Rendering                                                        */
/* ------------------------------------------------------------------ */
const STEPS=[['discover','Discover'],['investigate','Investigate'],['reason','Scientific Reasoning'],['industry','Industry Profile'],['findings','Findings']];
function renderSteps(){
  $('#steps').innerHTML=STEPS.map(([k,l],i)=>`${i?'<span class="sep">›</span>':''}<button data-s="${k}" class="${S.screen===k?'active':''} ${S.visited.has(k)&&S.screen!==k?'done':''}"><span class="n">${i+1}</span><span class="lbl">${l}</span></button>`).join('');
  $('#steps').querySelectorAll('button').forEach(b=>b.onclick=()=>gotoScreen(b.dataset.s));
}
function render(){
  renderSteps(); setLegend(); setMapTools();
  const fn={discover:rDiscover, investigate:rInvestigate, reason:rReason, industry:rIndustry, findings:rFindings}[S.screen];
  fn();
}
function stateBox(kind, msg){ return `<div class="state ${kind}">${kind==='loading'?'<span class="spin"></span>':''}${msg}</div>`; }

function rDiscover(){
  const h=hsById(S.hs); const hs=filteredHotspots();
  const indOpts=['<option value="all">All industries</option>'].concat(INDUSTRIES.filter(i=>HOTSPOTS.some(h=>h.industries.includes(i.id))).map(i=>`<option value="${i.id}" ${S.filt.industry===i.id?'selected':''}>${esc(i.name)}</option>`)).join('');
  const a=S.acq;
  let acqHtml='';
  if(a.status==='idle') acqHtml=stateBox('empty','No search yet. Choose a date range and press <b>Find SAR data</b>.');
  else if(a.status==='loading') acqHtml=stateBox('loading','Querying ASF (NISAR GCOV) and Planetary Computer (Sentinel-1 RTC)…');
  else {
    const ni=a.nisar, s1=a.s1;
    acqHtml += `<h3>NISAR L2 GCOV <span class="badge b-info">${ni.length}</span></h3>`;
    if(a.err.nisar) acqHtml+=stateBox('error',`ASF search failed: ${esc(a.err.nisar)}. Catalogue snapshot (${SNAPSHOT.retrieved}): ${SNAPSHOT.counts[h.id]} GCOV granules intersect this hotspot.`);
    else if(!ni.length) acqHtml+=stateBox('empty','No NISAR GCOV products in this date range. Widen the range.');
    else acqHtml+=`<div class="list">${ni.map((f,i)=>{const p=f.properties; return `<div class="it ${S.nisarSel===f?'sel':''}" data-ni="${i}"><span>${p.startTime.slice(0,16).replace('T',' ')} UTC · ${p.flightDirection==='ASCENDING'?'Asc':'Desc'} ${p.pathNumber}/${p.frameNumber}</span><span class="muted">${esc(p.mainBandPolarization||'')} · ${esc((p.rangeBandwidth||[]).join())} MHz</span></div>`;}).join('')}</div>`;
    acqHtml += `<h3>Sentinel-1 RTC (fallback / cross-frequency) <span class="badge b-info">${s1.length}</span></h3>`;
    if(S.scr?.state==='loading') acqHtml+=stateBox('loading',`Screening surface conditions ${S.scr.done}/${S.scr.total}…`);
    else if(S.scr?.state==='done') acqHtml+=stateBox(S.scr.ok?'ok':'warn',`${S.scr.ok} of ${S.scr.total} acquisitions show wind-roughened water suitable for slick detection.`);
    if(a.err.s1) acqHtml+=stateBox('error',`Planetary Computer search failed: ${esc(a.err.s1)}`);
    else if(!s1.length) acqHtml+=stateBox('empty','No Sentinel-1 RTC scenes in this date range.');
    else acqHtml+=`<div class="list">${s1.map((f,i)=>{const p=f.properties; return `<div class="it ${S.scene===f?'sel':''}" data-s1="${i}"><span>${p.datetime.slice(0,16).replace('T',' ')} UTC · ${esc(p.platform)}</span><span class="muted">${esc(p['sat:orbit_state']||'')}${f.scr? (f.scr.ok?' · <b style="color:var(--ok)">usable</b>':' · low signal') : ''}</span></div>`;}).join('')}</div>`;
    acqHtml+=`<p class="small muted">NISAR quick-looks open in Investigate. Calibrated NISAR analysis runs in <code>analysis.py</code> (Earthdata login). Sentinel-1 γ⁰ is analysed directly in the browser.</p>`;
  }
  $('#side').innerHTML=`
    <h2>1 · Discover</h2><p class="lead">Choose a candidate hotspot, then find SAR acquisitions over it.</p>
    <div class="row" style="margin-bottom:10px"><input type="text" id="q" placeholder="Search a place (e.g. Port Harcourt)"><button class="btn" id="qgo">Go</button></div>
    <div class="grid2" style="margin-bottom:6px">
      <div><label class="f">Industry</label><select id="fInd">${indOpts}</select></div>
      <div><label class="f">Water body</label><select id="fWat"><option value="all">All</option><option value="estuary" ${S.filt.water==='estuary'?'selected':''}>Estuary / estuarine lake</option><option value="creeks" ${S.filt.water==='creeks'?'selected':''}>Delta creeks</option></select></div>
      <div><label class="f">From</label><input type="date" id="fFrom" value="${S.filt.from}"></div>
      <div><label class="f">To</label><input type="date" id="fTo" value="${S.filt.to}"></div>
    </div>
    <h3>Candidate hotspots <span class="muted small">(${hs.length})</span></h3>
    ${hs.length? hs.map(x=>`<div class="card click ${x.id===S.hs?'sel':''}" data-hs="${x.id}">
       <div class="row" style="justify-content:space-between"><b>${esc(x.name)}</b>${x.primary?'<span class="badge b-info">Primary demo</span>':''}</div>
       <div class="small muted">${esc(x.country)} · ${esc(x.waterLabel)}</div>
       ${x.id===S.hs? `<p class="small" style="margin:8px 0 4px">${esc(x.why)}</p><p class="small" style="margin:4px 0"><b>Caveat:</b> ${esc(x.caveats)}</p>
       <p class="small" style="margin:4px 0"><b>SAR feasibility:</b> ${esc(x.feasibility)}</p>
       <div class="small">${x.industries.map(i=>{const I=indById(i); return `<span class="badge ${DET[I.detect][1]}" title="SAR detectability">${esc(I.name.split('(')[0].split('—')[0].trim())}: ${DET[I.detect][0]}</span>`;}).join(' ')}</div>
       <div class="small" style="margin-top:6px">Sources: ${x.refs.map(refLink).join('; ')}</div>`:''}
     </div>`).join('') : stateBox('empty','No hotspot matches these filters.')}
    <button class="btn primary block" id="find" ${a.status==='loading'?'disabled':''}>Find SAR data for ${esc(h.name)}</button>
    ${acqHtml}
    <div style="margin-top:14px"><button class="btn primary block" id="toInv" ${(!S.acq.s1.length && !S.acq.nisar.length)?'disabled':''}>Investigate →</button></div>`;
  $('#qgo').onclick=async()=>{ const q=$('#q').value.trim(); if(!q) return; try{ const r=await API.geocode(q); map.fitBounds([[+r.boundingbox[0],+r.boundingbox[2]],[+r.boundingbox[1],+r.boundingbox[3]]]); toast(r.display_name.slice(0,90)); }catch(e){ toast('Search: '+e.message); } };
  $('#q').onkeydown=e=>{ if(e.key==='Enter') $('#qgo').click(); };
  $('#fInd').onchange=e=>{ S.filt.industry=e.target.value; const f=filteredHotspots(); if(f.length && !f.find(x=>x.id===S.hs)) selectHotspot(f[0].id); else { drawHotspots(); render(); } };
  $('#fWat').onchange=e=>{ S.filt.water=e.target.value; const f=filteredHotspots(); if(f.length && !f.find(x=>x.id===S.hs)) selectHotspot(f[0].id); else { drawHotspots(); render(); } };
  $('#fFrom').onchange=e=>{ S.filt.from=e.target.value; };
  $('#fTo').onchange=e=>{ S.filt.to=e.target.value; };
  document.querySelectorAll('[data-hs]').forEach(el=>el.onclick=()=>selectHotspot(el.dataset.hs));
  $('#find').onclick=()=>{ if(S.filt.from>S.filt.to){ toast('Start date must be before end date'); return; } findData(); };
  $('#toInv').onclick=()=>gotoScreen('investigate');
  document.querySelectorAll('[data-ni]').forEach(el=>el.onclick=()=>{ S.nisarSel=S.acq.nisar[+el.dataset.ni]; render(); });
  document.querySelectorAll('[data-s1]').forEach(el=>el.onclick=()=>{ S.scene=S.acq.s1[+el.dataset.s1]; render(); });
}

const CLS={measurable:['b-sup','Measurable','Contrast measured against a valid local background, above the noise floor.'],
  regional:['b-con','Regional','Larger than the background window: a regional darkening (often wind) rather than a local feature; contrast is ill-defined.'],
  noise:['b-unl','Noise floor','Most pixels are within 1 dB of the instrument noise floor — not interpretable.']};
function run_nesz(a){ const r=runOf(a); const nz=r?.sensor?.nesz; return nz==null? 'NESZ not available' : `${fmt(a.meanDb-nz)} dB (NESZ ≈ ${nz} dB γ⁰)${a.noiseFrac!=null?` · ${fmt(a.noiseFrac*100,0)} % of pixels within 1 dB`:''}`; }
function clsBadge(a){ const c=CLS[a.cls]; return c? `<span class="badge ${c[0]}" title="${esc(c[2])}">${c[1]}</span>` : ''; }
function anomRow(a){ return `<div class="it ${a.key===S.sel?'sel':''}" data-an="${esc(a.key)}"><span><b>${esc(a.label)}</b> · ${fmt(a.area,2)} km² · −${fmt(a.contrast)}${a.contrastU!=null?'±'+fmt(a.contrastU,1):''} dB</span><span class="muted">${clsBadge(a)} ${fmt(a.shoreKm,1)} km from land</span></div>`; }
function rInvestigate(){
  const h=hsById(S.hs); const p=S.scene?.properties;
  if(!S.acq.s1.length && !S.acq.nisar.length && S.acq.status!=='loading'){
    $('#side').innerHTML=`<h2>2 · Investigate</h2>${stateBox('empty','No SAR acquisitions loaded yet.')}<button class="btn primary block" id="go">Find SAR data for ${esc(h.name)}</button>`;
    $('#go').onclick=async()=>{ await findData(); gotoScreen('investigate'); }; return;
  }
  const s1opts=S.acq.s1.map((f,i)=>`<option value="${i}" ${S.scene===f?'selected':''}>${f.properties.datetime.slice(0,16).replace('T',' ')} · ${f.properties.platform.replace('sentinel-','S')}${scrTag(f)}</option>`).join('');
  const compOpts='<option value="">— none —</option>'+S.acq.s1.map((f,i)=>`<option value="${i}" ${S.comp===f?'selected':''} ${S.scene===f?'disabled':''}>${f.properties.datetime.slice(0,16).replace('T',' ')} · ${f.properties.platform.replace('sentinel-','S')}${scrTag(f)}</option>`).join('');
  const niOpts=S.acq.nisar.map((f,i)=>`<option value="${i}" ${S.nisarSel===f?'selected':''}>${f.properties.startTime.slice(0,16).replace('T',' ')} · ${f.properties.flightDirection.slice(0,4)} ${f.properties.pathNumber}/${f.properties.frameNumber}</option>`).join('');
  const rs=S.runStatus; const r=S.run;
  let runHtml='';
  if(rs?.state==='loading') runHtml=stateBox('loading',esc(rs.msg));
  else if(rs?.state==='error') runHtml=stateBox('error',esc(rs.msg));
  else if(r){
    const warn = r.res.lowFrac!=null && r.res.lowFrac>0.5 ? stateBox('warn',`${fmt(r.res.lowFrac*100,0)} % of open water is within 3 dB of the noise floor — scene-wide low wind is likely; dark features here are not interpretable.`) : '';
    const cc={measurable:0,regional:0,noise:0}; r.anoms.forEach(x=>cc[x.cls]++);
    runHtml=`${stateBox('ok',`${r.res.totalFound} dark features ≥ ${S.params.minKm2} km² found (${r.anoms.length} largest listed: ${cc.measurable} measurable, ${cc.regional} regional, ${cc.noise} at noise floor). Water mean γ⁰ ${fmt(r.res.waterMeanDb)} dB; ${fmt(r.res.measFrac*100,0)} % of open water measurable (background ≥ NESZ + T + 1 dB); ${r.res.nPoints} bright point targets; ${r.res.nStruct} in-water structures masked.`)}${warn}
      ${r.compErr?stateBox('warn','Comparison scene: '+esc(r.compErr)):''}
      ${r.compare? `<details class="small"><summary>Comparison scene: ${r.compare.sameGeometry?'same geometry':'different geometry'} · Δ ${fmt(r.compare.dDays,0)} days</summary><ul>${r.compare.notes.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></details>`:''}
      ${r.anoms.length? `<div class="list" style="max-height:200px">${r.anoms.map(anomRow).join('')}</div>` : stateBox('empty','No dark anomalies above the threshold in this window.')}`;
  }
  const imp = S.imported.map(R=>`<h3>${esc(R.platform)} · ${esc(R.file)}</h3><div class="list">${R.anoms.map(anomRow).join('')||'<div class="it">No anomalies in file</div>'}</div>`).join('');
  const a=currentAnom(); const c=a?S.ctx[a.key]:null; const ar=runOf(a);
  const det = a? `<div class="card" style="margin-top:10px"><div class="row" style="justify-content:space-between"><b>Anomaly ${esc(a.label)}</b><span>${clsBadge(a)} <span class="badge b-info" title="Tier 1 of 3: SAR-observed surface anomaly. Not a possible discharge until tested; never a verified source.">Tier 1 · SAR-observed</span></span></div>
     ${a.cls==='noise'? stateBox('warn','Most of this feature is at the instrument noise floor: the “dark” signal is not a measurable surface property.') : a.cls==='regional'? stateBox('warn','This feature is larger than the background window: it is a regional darkening (commonly wind variability), and its contrast is only indicative.') : ''}
     <dl class="kv" style="margin-top:6px">
       <dt>Centre</dt><dd>${fmt(a.latlon[0],4)}°, ${fmt(a.latlon[1],4)}°</dd>
       <dt>Area</dt><dd>${fmt(a.area,2)} km² <span class="tip" title="Pixel count × true pixel area of the analysis grid">ⓘ</span></dd>
       <dt>Extent / axis ratio</dt><dd>${fmt(a.lengthKm,1)} km along the principal axis · ratio ${fmt(a.elong,1)}${a.fillRatio!=null?` · fill ${fmt(a.fillRatio,2)}`:''} <span class="tip" title="Extent: range of pixel positions projected on the principal axis. Axis ratio: √(λ1/λ2) of the second-moment (equivalent) ellipse. Fill: area / equivalent-ellipse area (low = ragged or branching).">ⓘ</span></dd>
       <dt>γ⁰ inside / background</dt><dd>${fmt(a.meanDb)} dB / ${fmt(a.bgDb)} dB</dd>
       <dt>Contrast</dt><dd>−${fmt(a.contrast)} ± ${fmt(a.contrastU??0,2)} dB${a.p95Contrast!=null?` (95th pct −${fmt(a.p95Contrast)} dB)`:''} <span class="tip" title="Ratio of linear means (background / anomaly) in dB. ± = speckle standard error of the mean (ENL 4.4) combined with 8-bit quantisation. Excludes NESZ and calibration uncertainty.">ⓘ</span></dd>
       <dt>Above noise floor</dt><dd>${run_nesz(a)}</dd>
       <dt>Edge step</dt><dd>${fmt(a.edgeStep)} dB <span class="tip" title="Sharp edges are more typical of films than of gradual wind-speed changes, but this is an indicator, not a test.">ⓘ</span></dd>
       <dt>Land</dt><dd>${fmt(a.shoreKm,2)} km${a.touchesShoreBuffer?' (reaches shoreline buffer)':''} · ${fmt(a.permFrac*100,0)} % permanent water</dd>
       <dt>Persistence</dt><dd>${a.persistStatus==='untestable'?`untestable (${fmt((a.persistCover||0)*100,0)} % measurable in comparison)` : a.persist!=null? `${esc(a.persistStatus||'')}: −${fmt(a.persist)} dB, ${a.persistFrac!=null?fmt(a.persistFrac*100,0)+' % of pixels dark':''}`:'not assessed'}</dd>
       <dt>Wind (ERA5)</dt><dd>${c?.wind? `${fmt(c.wind.ws)} m/s, ${fmt(c.wind.wd,0)}°` : c?.windLoading? '<span class="spin"></span>loading' : esc(c?.windErr||'—')}</dd>
       <dt>SAR point targets</dt><dd>${a.ptN!=null? `${a.ptInside??0} inside, ${a.ptN} within 2 km (platforms/vessels, unidentified)` : '—'}</dd>
       <dt>Nearby mapped infrastructure</dt><dd>${c?.fac? esc(facSummary(c.fac)) : c?.facLoading? '<span class="spin"></span>querying OpenStreetMap' : esc(c?.facErr||'—')}</dd>
     </dl>
     <p class="small muted" style="margin:8px 0">Observed: a region of reduced radar backscatter (tier 1). Whether it is a possible industrial discharge (tier 2) is tested on the next screen; a verified pollution source (tier 3) requires in-situ evidence and is never established here. Nearby facilities are context only.</p>
     <button class="btn primary block" id="toReason">Investigate explanations →</button></div>` : '';
  $('#side').innerHTML=`
    <h2>2 · Investigate</h2><p class="lead">${esc(h.name)} — detect dark surface anomalies in calibrated SAR.</p>
    <div class="tabs"><button id="tS1" class="${S.invTab!=='nisar'?'on':''}">Sentinel-1 RTC (${S.acq.s1.length})</button><button id="tNI" class="${S.invTab==='nisar'?'on':''}">NISAR GCOV (${S.acq.nisar.length})</button></div>
    ${S.invTab==='nisar' ? `
      ${S.acq.nisar.length? `<label class="f">Acquisition</label><select id="niSel">${niOpts}</select>
      ${S.nisarSel? `<dl class="kv" style="margin:8px 0"><dt>Product</dt><dd>${esc(S.nisarSel.properties.collectionName)}</dd><dt>Granule</dt><dd class="small">${esc(S.nisarSel.properties.fileID)}</dd>
        <dt>Start</dt><dd>${esc(S.nisarSel.properties.startTime)}</dd><dt>Pass</dt><dd>${esc(S.nisarSel.properties.flightDirection)} · track ${S.nisarSel.properties.pathNumber} · frame ${S.nisarSel.properties.frameNumber}</dd>
        <dt>Polarisation</dt><dd>${esc(S.nisarSel.properties.mainBandPolarization)} (main band ${esc((S.nisarSel.properties.rangeBandwidth||[]).join())} MHz)</dd>
        <dt>Size</dt><dd>${fmt((S.nisarSel.properties.bytes?.[S.nisarSel.properties.fileName+'.h5']?.bytes||Object.values(S.nisarSel.properties.bytes||{})[0]?.bytes||0)/1e9,2)} GB HDF5</dd></dl>
        <button class="btn block" id="niShow">${S.layers.nisar?'Hide':'Show'} public quick-look (uncalibrated display)</button>
        <p class="small muted">The quick-look is ASF's public colour-stretched browse image, placed on the footprint's bounding box (approximate) — useful for orientation, not for measurement. For calibrated HH γ⁰ analysis run:<br><code>python analysis.py nisar --hotspot ${h.id} --granule ${esc(S.nisarSel.properties.fileID)}</code><br>then load the resulting JSON below.</p>`:''}`
      : stateBox('empty','No NISAR acquisitions in the selected date range.')}
      <label class="btn block" style="text-align:center;margin-top:8px">Load analysis.py result (.json)<input type="file" id="imp" accept=".json,application/json" hidden></label>`
    : `
      ${S.scr?.state==='loading'? stateBox('loading',`Screening surface conditions ${S.scr.done}/${S.scr.total} (median water γ⁰ in the demo window)…`) : S.scr?.state==='done'? stateBox(S.scr.ok?'ok':'warn', `${S.scr.ok} of ${S.scr.total} acquisitions have wind-roughened water ≥ ${SENSORS.S1.nesz+4} dB (NESZ + 4 dB). Others are near the noise floor — calm water hides any film contrast.`) : S.scr?.state==='error'? stateBox('error','Screening failed: '+esc(S.scr.msg)) : ''}
      ${S.acq.s1.length? `<label class="f">Scene to analyse</label><select id="s1Sel">${s1opts}</select>
      ${p? `<dl class="kv" style="margin:8px 0"><dt>Satellite</dt><dd>${esc(p.platform)} · ${esc(p['sat:orbit_state']||'')} · ${esc(p['sar:instrument_mode']||'IW')}</dd><dt>Acquired</dt><dd>${esc(p.datetime)}</dd><dt>Product</dt><dd>${esc(S.scene.id)}</dd><dt>Polarisation</dt><dd>VV (analysed), VH</dd></dl>`:''}
      <label class="f">Before/after comparison (optional)</label><select id="cmpSel">${compOpts}</select>
      <h3>Detection settings</h3>
      <label class="f">Contrast threshold <span class="tip" title="A pixel seeds an anomaly when its speckle-filtered γ⁰ is at least this many dB below the local water background (~3 km window). Regions grow down to half the threshold.">ⓘ</span>: <span class="slider-val">${S.params.T} dB</span></label>
      <input type="range" id="pT" min="1.5" max="8" step="0.5" value="${S.params.T}">
      <label class="f">Minimum area: <span class="slider-val">${S.params.minKm2} km²</span></label>
      <input type="range" id="pA" min="0.02" max="1" step="0.01" value="${S.params.minKm2}">
      <div class="grid2" style="margin-top:8px"><button class="btn" id="runDemo" title="Use the predefined window for this hotspot">Demo window</button><button class="btn primary" id="runView" title="Analyse up to 0.4°×0.4° around the current map centre">Analyse map view</button></div>`
      : stateBox('empty','No Sentinel-1 RTC scenes in the selected date range.')}`}
    ${runHtml}${imp}${det}`;
  // wiring
  $('#tS1').onclick=()=>{S.invTab='s1'; render();}; $('#tNI').onclick=()=>{S.invTab='nisar'; render();};
  const sel=(id,fn)=>{const e=document.getElementById(id); if(e) e.onchange=fn;};
  sel('s1Sel',e=>{S.scene=S.acq.s1[+e.target.value]; S.run=null; S.runStatus=null; S.sel=null; refreshLayers(); render();});
  sel('cmpSel',e=>{S.comp=e.target.value===''?null:S.acq.s1[+e.target.value]; render();});
  sel('niSel',e=>{S.nisarSel=S.acq.nisar[+e.target.value]; refreshLayers(); render();});
  const pT=$('#pT'); if(pT) pT.oninput=e=>{S.params.T=+e.target.value; e.target.previousElementSibling.querySelector('.slider-val').textContent=S.params.T+' dB';};
  const pA=$('#pA'); if(pA) pA.oninput=e=>{S.params.minKm2=+e.target.value; e.target.previousElementSibling.querySelector('.slider-val').textContent=S.params.minKm2+' km²';};
  const rd=$('#runDemo'); if(rd) rd.onclick=()=>{ const [w,s,e,n]=h.demo; map.fitBounds([[s,w],[n,e]]); runAnalysis(false); };
  const rv=$('#runView'); if(rv) rv.onclick=()=>runAnalysis(true);
  const ns=$('#niShow'); if(ns) ns.onclick=()=>{ S.layers.nisar=!S.layers.nisar; if(S.layers.nisar){ const g=S.nisarSel.geometry.coordinates[0]; map.fitBounds(g.map(q=>[q[1],q[0]])); } refreshLayers(); render(); };
  const im=$('#imp'); if(im) im.onchange=e=>{ if(e.target.files[0]) importResults(e.target.files[0]); };
  document.querySelectorAll('[data-an]').forEach(el=>el.onclick=()=>selectAnom(el.dataset.an));
  const tr=$('#toReason'); if(tr) tr.onclick=()=>gotoScreen('reason');
}

function needAnom(title){
  $('#page').innerHTML=`<h2>${title}</h2>${stateBox('empty','No anomaly selected yet. Detect and select an anomaly in Investigate first.')}<button class="btn primary" id="back">Go to Investigate</button>`;
  $('#back').onclick=()=>gotoScreen('investigate');
}
const STATUS_BADGE={supported:['b-info','Supported'],consistent:['b-con','Consistent'],untested:['b-unk','Untested'],contradicted:['b-unl','Contradicted']};
function tierBanner(){ return `<div class="tiers"><div class="tier on"><b>1 · SAR-observed surface anomaly</b><span>measured reduction of radar backscatter</span></div><div class="tier"><b>2 · Possible industrial discharge</b><span>one hypothesis among several — tested below</span></div><div class="tier off"><b>3 · Verified pollution source</b><span>requires in-situ sampling & attribution — never established by this tool</span></div></div>`; }
function rReason(){
  const a=currentAnom(); if(!a) return needAnom('3 · Scientific Reasoning');
  const run=runOf(a), c=S.ctx[a.key]||{}; const R=reason(a,run,c);
  const loading = c.windLoading||c.facLoading;
  const cols=[['noise','Noise floor'],['wind','Wind'],['scale','Scale'],['shore','Shore/mask'],['precip','Rain'],['shape','Currents'],['persist','Persistence'],['pts','SAR targets'],['infra','Mapped infra']];
  const cell=(h,k)=>{ const rs=h.rows.filter(x=>x.k===k); if(!rs.length) return '<td class="ev-na" title="This observation does not bear on this explanation">·</td>';
    return `<td>${rs.map(r=>{const m=EV[r.ev]; return `<span class="${m[0]}" title="${esc(m[2]+': '+r.why)}">${m[1]}</span>`;}).join(' ')}</td>`; };
  const group=(st,title,empty)=>{ const hs=R.hyps.filter(h=>h.status===st); return `<div class="card"><h3 style="margin-top:0"><span class="badge ${STATUS_BADGE[st][0]}">${STATUS_BADGE[st][1]}</span> ${title}</h3>${hs.length? `<ul>${hs.map(h=>`<li><b>${esc(h.name)}</b>${h.rows.filter(r=>r.ev!=='context').map(r=>`<div class="small muted">${EV[r.ev][1]} ${esc(r.why)}</div>`).join('')}</li>`).join('')}</ul>` : `<p class="muted small">${empty}</p>`}</div>`; };
  $('#page').innerHTML=`
    <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px"><div><h2>3 · Scientific Reasoning — anomaly ${esc(a.label)}</h2>
      <p class="lead">${esc(run.sensor.name)} · ${esc(run.sceneTime)} · ${fmt(a.latlon[0],4)}°, ${fmt(a.latlon[1],4)}° · ${clsBadge(a)}</p></div>
      <div class="row"><button class="btn" id="bk">← Investigate</button><button class="btn primary" id="fw">Industry profile →</button></div></div>
    ${tierBanner()}
    ${loading? stateBox('loading','Retrieving ERA5 wind and mapped infrastructure — the evaluation updates automatically.'):''}
    <div class="verdict ${R.verdict.cls}"><b>${esc(R.verdict.title)}</b><div>${esc(R.verdict.text)}</div>
      <div class="small muted" style="margin-top:6px">Set-membership test with four evidence levels. <b>Supported</b>: a measurement favours it over an alternative. <b>Consistent</b>: measured and compatible. <b>Untested</b>: no usable measurement — never counted as support. <b>Contradicted</b>: a measured interval lies outside what it can produce. No probabilities are assigned.</div></div>
    <div class="cols" style="grid-template-columns:minmax(0,1fr)">
      <div class="card"><h3 style="margin-top:0">Evidence matrix</h3>
        <div style="overflow-x:auto"><table class="t"><tr><th>Explanation</th>${cols.map(c=>`<th>${c[1]}</th>`).join('')}<th>Status</th></tr>
        ${R.hyps.map(h=>`<tr><td>${esc(h.name)}</td>${cols.map(cc=>cell(h,cc[0])).join('')}<td><span class="badge ${STATUS_BADGE[h.status][0]}">${STATUS_BADGE[h.status][1]}</span></td></tr>`).join('')}</table></div>
        <p class="small muted"><span class="ev-sup">▲</span> supports · <span class="ev-con">✓</span> consistent · <span class="ev-unt">?</span> untested / not measurable · <span class="ev-bad">✗</span> contradicts · <span class="ev-ctx">○</span> context only (never changes a status) · · not applicable. Hover a symbol for the reasoning.</p></div>
      <div class="card"><h3 style="margin-top:0">1 · What the satellite measured</h3><table class="t">${R.obs.map(o=>`<tr><td><b>${esc(o.label)}</b><div class="small muted">${esc(o.src)}${o.kind==='context'?' · context only':o.kind==='missing'?' · not available':''}</div></td><td>${esc(o.val)}</td></tr>`).join('')}</table></div>
    </div>
    <div class="cols" style="margin-top:14px">
      ${group('supported','2a · Favoured by a measurement','No explanation is positively favoured by the available measurements.')}
      ${group('consistent','2b · Physically possible (compatible, not favoured)','—')}
      ${group('untested','2c · Not testable with the connected data','—')}
      ${group('contradicted','3 · Contradicted by the evidence','No explanation could be excluded with the available measurements.')}
      <div class="card"><h3 style="margin-top:0">4 · Additional measurements needed</h3><ul>${R.needs.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div>
      <div class="card"><h3 style="margin-top:0">5 · What cannot be concluded</h3><ul>${R.cannot.map(n=>`<li>${esc(n)}</li>`).join('')}</ul></div>
    </div>
    <details><summary>Physical basis and thresholds</summary><div class="small" style="max-width:900px">
      <p>Radar backscatter from water is dominated by Bragg scattering from short waves (${esc(SENSORS.S1.braggCm)} for C-band; ${esc(SENSORS.NISAR.braggCm)} for L-band). Surface films raise surface viscoelasticity and damp these waves, producing dark areas (${refLink('alpers89')}). Mineral oil and natural monomolecular films act the same way; their damping differs with radar frequency and wind, so discrimination is best with multi-frequency data at low-to-moderate wind (${refLink('gade98')}). Oil slicks are generally observable within a wind window of roughly 2–3 to 10–12 m/s (${refLink('brekke05')}; ${refLink('fingas14')}). Dissolved, suspended, submerged or rapidly mixed pollutants do not alter surface roughness and are invisible to SAR.</p>
      <p>Rules: noise floor <i>supported</i> if the anomaly mean is ≤ NESZ + 1 dB or ≥ 50 % of its pixels are within 1 dB of NESZ (then every surface explanation is <i>untested</i>), <i>contradicted</i> if ≥ NESZ + 3 dB. Oil film contradicted if the wind lower bound &gt; 14 m/s, untested if the upper bound &lt; 2 m/s; biogenic film contradicted &gt; 10 m/s; low wind contradicted if &gt; 6 m/s and &gt; 5 km from land, supported if ERA5 upper bound &lt; 3 m/s and not in conflict with the SAR background. Shore artefact contradicted if ≥ 95 % permanent water and ≥ 1 km from land, supported if the feature reaches the ${DET_DEFAULTS.shoreBufM} m shoreline buffer. Rain supported if ERA5 ≥ 0.5 mm, otherwise untested (a 25 km reanalysis cannot exclude a cell). ERA5 wind carries ±2 m/s. NESZ for Sentinel-1 is expressed in γ⁰ (−22 dB σ⁰ ÷ cos 39° ≈ −21 dB, ±1 dB across the swath).</p></div></details>`;
  $('#bk').onclick=()=>gotoScreen('investigate'); $('#fw').onclick=()=>gotoScreen('industry');
}

function relevantIndustries(){
  const h=hsById(S.hs); const a=currentAnom(); const fac=a? S.ctx[a.key]?.fac : null; const set=new Set(h.industries);
  if(fac) fac.forEach(f=>(CAT_IND[f.cat]||[]).forEach(i=>set.add(i)));
  return set;
}
function rIndustry(){
  const rel=relevantIndustries(); const sorted=[...INDUSTRIES].sort((a,b)=>a.rank-b.rank);
  if(!S.ind || !indById(S.ind)) S.ind=[...rel][0]||sorted[0].id;
  const I=indById(S.ind); const a=currentAnom(); const fac=a? (S.ctx[a.key]?.fac||[]) : [];
  const near=fac.filter(f=>(CAT_IND[f.cat]||[]).includes(I.id)).slice(0,8);
  const hsList=I.hotspots.map(id=>hsById(id)?.name).filter(Boolean);
  $('#page').innerHTML=`
    <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px"><div><h2>4 · Industry Profile</h2><p class="lead">Which industries could produce a SAR-visible film here — and which cannot.</p></div>
      <div class="row"><button class="btn" id="bk">← Reasoning</button><button class="btn primary" id="fw">Findings →</button></div></div>
    <div class="ind-tabs">${sorted.map(i=>`<button data-ind="${i.id}" class="${i.id===S.ind?'on':''} ${rel.has(i.id)?'rel':''}" title="${rel.has(i.id)?'Relevant to the selected hotspot/anomaly':''}">${rel.has(i.id)?'● ':''}${esc(i.name.split('(')[0].split('—')[0].trim())}</button>`).join('')}</div>
    <div class="cols">
      <div class="card"><div class="row" style="justify-content:space-between"><h3 style="margin:0">${esc(I.name)}</h3><span class="badge ${DET[I.detect][1]}">SAR: ${DET[I.detect][0]}</span></div>
        <dl class="kv" style="margin-top:10px">
          <dt>Illicit pattern</dt><dd>${esc(I.illicit)}</dd><dt>Processes</dt><dd>${esc(I.processes)}</dd><dt>Waste streams</dt><dd>${esc(I.waste)}</dd>
          <dt>Pathway</dt><dd>${esc(I.pathway)}</dd><dt>Radar visibility</dt><dd>${esc(I.mechanism)}</dd><dt>Look-alikes</dt><dd>${esc(I.confounders)}</dd>
          <dt>Evidence confidence</dt><dd>${esc(I.conf)}</dd><dt>Candidate regions</dt><dd>${hsList.length?esc(hsList.join('; ')):'— (no hotspot selected for this industry)'}</dd></dl></div>
      <div class="card"><h3 style="margin-top:0">Operating patterns</h3>
        <p class="small"><span class="badge b-sup">Documented</span></p>${I.pattern.doc.length?`<ul class="small">${I.pattern.doc.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<p class="small muted">No pattern documented in the sources reviewed.</p>'}
        <p class="small"><span class="badge b-unk">Hypothesis — not established</span></p>${I.pattern.hyp.length?`<ul class="small">${I.pattern.hyp.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:'<p class="small muted">—</p>'}
        <h3>Why it matters for ${a?'anomaly '+esc(a.label):'this hotspot'}</h3>
        <p class="small">${rel.has(I.id)? (I.detect==='S'||I.detect==='C' ? 'This industry is present in the hotspot evidence or among mapped features near the anomaly, and can produce a floating film that SAR can detect under suitable wind.' : 'Present nearby, but its typical waste streams are dissolved or emulsified — it is an unlikely explanation for a SAR dark feature.') : 'Not documented at this hotspot or near the selected anomaly.'}</p>
        ${a? `<p class="small"><b>Mapped features near the anomaly (OSM, unverified):</b> ${near.length? near.map(f=>`${esc(f.cat)}${f.tags.name?' “'+esc(f.tags.name)+'”':''} (${fmt(f.dkm,1)} km)`).join('; ') : 'none of this type within 15 km'}.</p>
        <p class="small muted">Listing a mapped facility does not imply that it discharged anything.</p>`:''}
        <p class="small"><b>References:</b> ${I.refs.map(refLink).join('; ')}</p></div>
    </div>
    <details open><summary>Research matrix — all industries ranked by SAR evidence (Q1–Q4)</summary>
      <div class="matrix-wrap" style="margin-top:8px"><table class="t"><tr><th>#</th><th>Industry</th><th>Waste</th><th>Water pathway</th><th>SAR mechanism</th><th>Detectability</th><th>Operating pattern (documented)</th><th>Confounders</th><th>Hotspots</th><th>Evidence</th></tr>
      ${sorted.map(i=>`<tr><td>${i.rank}</td><td><b>${esc(i.name)}</b></td><td>${esc(i.waste)}</td><td>${esc(i.pathway)}</td><td>${esc(i.mechanism)}</td><td><span class="badge ${DET[i.detect][1]}">${DET[i.detect][0]}</span></td><td>${esc(i.pattern.doc.join(' ')||'—')}</td><td>${esc(i.confounders)}</td><td>${esc(i.hotspots.map(x=>hsById(x)?.name).join('; ')||'—')}</td><td>${i.refs.map(refLink).join('; ')}<div class="muted">${esc(i.conf)}</div></td></tr>`).join('')}</table></div>
      <button class="btn" id="csv" style="margin-top:8px">Export matrix (CSV)</button></details>`;
  document.querySelectorAll('[data-ind]').forEach(b=>b.onclick=()=>{S.ind=b.dataset.ind; rIndustry();});
  $('#bk').onclick=()=>gotoScreen('reason'); $('#fw').onclick=()=>gotoScreen('findings');
  $('#csv').onclick=()=>{ const q=s=>'"'+String(s).replace(/"/g,'""')+'"'; const rows=[['rank','industry','waste','pathway','mechanism','detectability','pattern_documented','pattern_hypotheses','confounders','hotspots','references','confidence']]
    .concat(sorted.map(i=>[i.rank,i.name,i.waste,i.pathway,i.mechanism,DET[i.detect][0],i.pattern.doc.join(' | '),i.pattern.hyp.join(' | '),i.confounders,i.hotspots.join(';'),i.refs.map(k=>REFS[k].u).join(' '),i.conf]));
    download('industry_matrix.csv', rows.map(r=>r.map(q).join(',')).join('\n'), 'text/csv'); };
}

function buildReport(){
  const h=hsById(S.hs); const a=currentAnom(); const run=runOf(a); const c=a?S.ctx[a.key]||{}:{}; const R=a?reason(a,run,c):null;
  const refs=new Set(['alpers89','gade98','brekke05','fingas14','pekel16','era5','lee80',...h.refs]); if(run?.platform==='NISAR') refs.add('asfGcov');
  relevantIndustries().forEach(i=>indById(i).refs.forEach(r=>refs.add(r)));
  return {h,a,run,c,R,refs:[...refs], generated:new Date().toISOString()};
}
function rFindings(){
  const D=buildReport(); const {h,a,run,c,R}=D;
  const body = !a ? stateBox('empty','No anomaly has been investigated yet. The report below covers the hotspot and data search only.') : '';
  const scen = run? `<ul><li><b>Satellite / product:</b> ${esc(run.sensor.product)}</li><li><b>Scene:</b> ${esc(run.sceneId)}</li><li><b>Acquired:</b> ${esc(run.sceneTime)} (${esc(run.platform||'')}${run.orbit?', '+esc(run.orbit):''})</li>
      ${run.compId?`<li><b>Comparison scene:</b> ${esc(run.compId)} (${esc(run.compTime)})</li>`:''}<li><b>Analysis window:</b> ${run.bbox.map(v=>fmt(v,3)).join(', ')} (W,S,E,N)${run.px?` · ${run.px} m pixels`:''}</li>
      <li><b>Processing (detector v2):</b> ${run.src==='browser'?`γ⁰ VV (RTC, already calibrated & geocoded) read as dB in ${DB_MIN}…${DB_MAX} dB (8-bit, 0.12 dB steps) → JRC water (occurrence ≥ 80 %), land = non-water components ≥ ${DET_DEFAULTS.holeKm2} km², ${DET_DEFAULTS.shoreBufM} m shoreline buffer, in-water structures masked → 5×5 Lee filter (ENL 4.4) → two-pass background (${fmt((2*run.res.R+1)*run.px/1000,1)} km, then ${fmt((2*run.res.R2+1)*run.px/1000,1)} km excluding dark candidates) → only pixels with background ≥ NESZ + T + 1 dB (NESZ ${run.sensor.nesz} dB γ⁰) may seed or grow → hysteresis (seed ≥ ${run.T} dB, grow ≥ ${run.T/2} dB) → min area ${S.params.minKm2} km²`:`analysis.py (${esc(run.file)})`}</li>
      ${run.compare?`<li><b>Comparability of comparison scene:</b> ${run.compare.notes.map(esc).join(' ')}</li>`:''}</ul>` : '<p class="muted">No SAR scene analysed.</p>';
  $('#page').innerHTML=`
    <div class="row noprint" style="justify-content:space-between;flex-wrap:wrap;gap:10px;margin-bottom:12px"><div><h2>5 · Findings</h2><p class="lead">Investigation report — export or print.</p></div>
      <div class="row"><button class="btn" id="bk">← Industry</button><button class="btn" id="md">Export Markdown</button><button class="btn" id="js">Export JSON</button><button class="btn primary" id="pr">Print / PDF</button></div></div>
    ${body}
    <div class="report" id="report">
      <h1>SAR surface-anomaly investigation — ${esc(h.name)}, ${esc(h.country)}</h1>
      <div class="muted small">Generated ${esc(D.generated)} · SAR Discharge Investigator (NASA Space Apps 2026)</div>
      <h2>Location</h2><p>${esc(h.waterLabel)}. Hotspot rationale: ${esc(h.why)}</p>
      ${a?`<p>Anomaly <b>${esc(a.label)}</b> centred at ${fmt(a.latlon[0],4)}°N, ${fmt(a.latlon[1],4)}°E.</p>`:''}
      <h2>Satellite and acquisition</h2>${scen}
      <p class="small">NISAR GCOV acquisitions found for this hotspot in ${esc(S.filt.from)} – ${esc(S.filt.to)}: ${S.acq.status==='done'?S.acq.nisar.length:'not searched'}${S.acq.nisar[0]?` (latest ${esc(S.acq.nisar[0].properties.startTime)})`:''}.</p>
      ${a?`<h2>Observed anomaly (tier 1 — SAR-observed surface anomaly)</h2><ul><li>Class: ${CLS[a.cls]?CLS[a.cls][1]+' — '+CLS[a.cls][2]:'—'}</li><li>Area ${fmt(a.area,2)} km², principal-axis extent ${fmt(a.lengthKm,1)} km, axis ratio ${fmt(a.elong,1)}${a.fillRatio!=null?`, fill ratio ${fmt(a.fillRatio,2)}`:''}</li><li>γ⁰ ${fmt(a.meanDb)} dB vs. background ${fmt(a.bgDb)} dB (contrast −${fmt(a.contrast)} ± ${fmt(a.contrastU??0,2)} dB${a.p95Contrast!=null?`; 95th percentile −${fmt(a.p95Contrast)} dB`:''}); ${run.sensor.nesz!=null?`${fmt(a.meanDb-run.sensor.nesz)} dB above NESZ${a.noiseFrac!=null?`, ${fmt(a.noiseFrac*100,0)} % of pixels within 1 dB of NESZ`:''}; `:''}edge step ${fmt(a.edgeStep)} dB</li><li>${fmt(a.shoreKm,2)} km from land; ${fmt(a.permFrac*100,0)} % permanent water</li><li>Persistence: ${a.persistStatus==='untestable'?'untestable in comparison scene':a.persist!=null?`${esc(a.persistStatus||'')} (contrast ${fmt(a.persist)} dB${a.persistFrac!=null?`, ${fmt(a.persistFrac*100,0)} % of pixels dark`:''})`:'not assessed'}</li><li>Bright point targets: ${a.ptInside??0} inside, ${a.ptN??'—'} within 2 km (unidentified)</li></ul>
      <h2>Environmental conditions</h2><p>${c.wind?`ERA5 10 m wind ${fmt(c.wind.ws)} m/s from ${fmt(c.wind.wd,0)}° at ${esc(c.wind.time)}; precipitation ±1 h ${fmt(c.wind.precip3h,1)} mm.`:'Wind not available: '+esc(c.windErr||'not retrieved')}</p>
      <h2>Candidate explanations (tier 2 — hypotheses, including a possible industrial discharge)</h2><p><b>${esc(R.verdict.title)}.</b> ${esc(R.verdict.text)}</p>
      <ul>${R.hyps.map(x=>`<li><b>${esc(x.name)}</b> — <i>${STATUS_BADGE[x.status][1].toLowerCase()}</i>${x.rows.map(r=>`; [${EV[r.ev][2]}] ${esc(r.why)}`).join('')}</li>`).join('')}</ul>
      <p class="small">Tier 3 — verified pollution source: not established. This requires in-situ sampling and source attribution.</p>
      <h2>Supporting and contradictory evidence</h2><ul>${R.obs.map(o=>`<li><b>${esc(o.label)}:</b> ${esc(o.val)} <span class="muted">(${esc(o.src)})</span></li>`).join('')}</ul>
      <p class="small">Mapped infrastructure is listed from OpenStreetMap and has not been verified. Proximity is not evidence of a discharge.</p>
      <h2>Remaining uncertainties</h2><ul>${R.cannot.map(x=>`<li>${esc(x)}</li>`).join('')}<li>ERA5 wind (±2 m/s assumed) cannot resolve local sheltering or gusts.</li><li>8-bit quantisation of γ⁰ (0.12 dB) and resampling to ${run?.px||'—'} m reduce sensitivity to small features.</li><li>Noise floor: ${esc(run?.sensor?.neszNote||'not available for this product')}.</li><li>Contrast uncertainty reflects speckle and quantisation only; calibration and NESZ uncertainty (±1 dB) are not included.</li></ul>
      <h2>Recommended follow-up observations</h2><ul>${R.needs.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}
      <h2>Relevant industries (research matrix)</h2><ul>${[...relevantIndustries()].map(i=>{const I=indById(i); return `<li><b>${esc(I.name)}</b> — SAR detectability: ${DET[I.detect][0]}. ${esc(I.mechanism)}</li>`;}).join('')}</ul>
      <h2>Scientific references</h2><ol class="small">${D.refs.map(k=>`<li>${esc(REFS[k].t)} <a href="${REFS[k].u}">${esc(REFS[k].u)}</a></li>`).join('')}</ol>
      <p class="foot">This report describes radar observations and their physically possible explanations. It does not identify a pollutant, attribute a source, or establish legal wrongdoing.</p>
    </div>`;
  $('#bk').onclick=()=>gotoScreen('industry'); $('#pr').onclick=()=>window.print();
  $('#md').onclick=()=>download(`sar_investigation_${h.id}.md`, htmlToMd($('#report')), 'text/markdown');
  $('#js').onclick=()=>download(`sar_investigation_${h.id}.json`, JSON.stringify(reportJSON(D),null,2), 'application/json');
}
function reportJSON(D){ const {h,a,run,c,R}=D; const strip=x=>{ if(!x) return x; const {pix,...rest}=x; return rest; };
  return {generated:D.generated, hotspot:{id:h.id,name:h.name,country:h.country,center:h.center,why:h.why,refs:h.refs.map(k=>REFS[k])},
    data_search:{from:S.filt.from,to:S.filt.to,nisar_gcov:S.acq.nisar.map(f=>f.properties.fileID), sentinel1_rtc:S.acq.s1.map(f=>f.id)},
    analysis: run?{detector_version:'v2', source:run.src, sensor:run.sensor.product, nesz_db:run.sensor.nesz, scene:run.sceneId, acquired:run.sceneTime, comparison:run.compId||null, comparability:run.compare||null, bbox:run.bbox, pixel_m:run.px||null, contrast_threshold_db:run.T,
      scene_stats: run.res? {total_dark_features:run.res.totalFound, water_mean_db:run.res.waterMeanDb, measurable_water_frac:run.res.measFrac, low_signal_water_frac:run.res.lowFrac, bright_point_targets:run.res.nPoints, in_water_structures:run.res.nStruct, regional_threshold_km2:run.res.regionalKm2}:null}:null,
    anomaly:strip(a), environment:c.wind||null, facilities_osm_unverified:(c.fac||[]).slice(0,50).map(f=>({osm:f.id,cat:f.cat,name:f.tags.name||null,dist_km:+f.dkm.toFixed(2)})),
    evidence_tier:'1 — SAR-observed surface anomaly (not a verified pollution source)',
    reasoning:R?{verdict:R.verdict, evidence_levels:['supports','consistent','untested','contradicts','context'], hypotheses:R.hyps.map(x=>({id:x.id,name:x.name,status:x.status,checks:x.rows})), needs:R.needs, cannot_conclude:R.cannot}:null,
    references:D.refs.map(k=>REFS[k])}; }
function htmlToMd(el){
  let out=''; const walk=n=>{ for(const c of n.childNodes){ if(c.nodeType===3){ out+=c.textContent.replace(/\s+/g,' '); continue; } const t=c.tagName;
    if(t==='H1'){ out+='\n# '; walk(c); out+='\n'; } else if(t==='H2'){ out+='\n\n## '; walk(c); out+='\n'; } else if(t==='LI'){ out+='\n- '; walk(c); }
    else if(t==='P'){ out+='\n'; walk(c); out+='\n'; } else if(t==='B'){ out+='**'; walk(c); out+='**'; } else if(t==='A'){ out+=`[${c.textContent}](${c.href})`; } else walk(c); } };
  walk(el); return out.replace(/\n{3,}/g,'\n\n').trim()+'\n';
}
function download(name, text, type){ const b=new Blob([text],{type}); const u=URL.createObjectURL(b); const a=document.createElement('a'); a.href=u; a.download=name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(()=>URL.revokeObjectURL(u),2000); }

/* ------------------------------------------------------------------ */
/* 13. Boot                                                             */
/* ------------------------------------------------------------------ */
function boot(){
  if(typeof L==='undefined'){ document.body.innerHTML='<div class="state error" style="margin:40px">Could not load the Leaflet map library from unpkg.com. Check your internet connection.</div>'; return; }
  initMap(); drawHotspots(); render();
  const h=hsById(S.hs); const [w,s,e,n]=h.search; map.fitBounds([[s,w],[n,e]],{padding:[30,30]});
}
window.SARApp={S, analyzeScene, reason, API, runAnalysis, selectAnom, gotoScreen};
boot();
