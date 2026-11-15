// Regression tests for detector v2 + reasoning v2 on SYNTHETIC scenes (test only — not observations).
const fs=require('fs'); const src=fs.readFileSync(process.argv[2]||require('path').join(__dirname,'..','app.js'),'utf8');
const pre=src.slice(src.indexOf("const REFS"), src.indexOf("/* 10. Map"));
const M=new Function('document','window', pre+'\nreturn {analyzeScene, persistence, reason, comparability, weatherAtAcquisition, persistenceLabel, SENSORS, fmt};')({}, {});
let seed=7; const rnd=()=>{ seed=(seed*1103515245+12345)%2147483648; return seed/2147483648; };
function rg(k){ const d=k-1/3,c=1/Math.sqrt(9*d); for(;;){let x,v; do{ const u1=rnd()||1e-9,u2=rnd(); x=Math.sqrt(-2*Math.log(u1))*Math.cos(2*Math.PI*u2); v=1+c*x;}while(v<=0); v=v*v*v; const u=rnd(); if(u<1-0.0331*x**4) return d*v; if(Math.log(u)<0.5*x*x+d*(1-v+Math.log(v))) return d*v; } }
const q8=d=>{ d=Math.max(-30,Math.min(0,d)); return -30+Math.round((d+30)/30*255)*30/255; };
function scene(W,H,fn){ const N=W*H, db=new Float32Array(N), valid=new Uint8Array(N).fill(1), occ=new Float32Array(N);
  for(let y=0;y<H;y++) for(let x=0;x<W;x++){ const i=y*W+x; const r=fn(x,y); occ[i]=r.occ??100; db[i]=q8(r.mu + 10*Math.log10(rg(4.4)/4.4)); } return {db,valid,occ}; }
let fails=0; const ok=(c,msg)=>{ console.log((c?'PASS ':'FAIL ')+msg); if(!c) fails++; };
const NESZ=-21, base={T:3,minKm2:0.05,nesz:NESZ};

try{
// T1 ellipse slick in open water: area recovery
{ const W=400,H=300,px=30; let truth=0; const s=scene(W,H,(x,y)=>{const inS=((x-220)/60)**2+((y-150)/12)**2<=1; if(inS) truth++; return {mu:inS?-20:-12, occ:x<30?0:100};});
  const r=M.analyzeScene(s.db,s.valid,s.occ,W,H,px,base); const a=r.anoms[0]; const tk=truth*px*px/1e6;
  ok(r.totalFound===1 && Math.abs(a.area-tk)/tk<0.15, `T1 slick area ${a.area.toFixed(3)} vs truth ${tk.toFixed(3)} km2, ${r.totalFound} feature(s), class ${a.cls}`);
  ok(Math.abs(a.contrast-8)<1.0, `T1 contrast (linear-mean ratio) ${a.contrast.toFixed(2)} ± ${a.contrastU.toFixed(2)} dB vs truth 8.0`);
  ok(Math.abs(a.lengthKm-3.6)<0.4 && Math.abs(a.elong-5)<0.6, `T1 extent ${a.lengthKm.toFixed(2)} km (truth 3.6), axis ratio ${a.elong.toFixed(2)} (truth 5)`);
  ok(a.cls==='measurable', 'T1 class measurable'); }
}catch(e){ ok(false, 'T1 crashed: '+e.message); }
try{
// T2 speckle only
for(const T of [2,3,4]){ const s=scene(400,300,()=>({mu:-12})); const r=M.analyzeScene(s.db,s.valid,s.occ,400,300,30,{...base,T}); ok(r.totalFound===0, `T2 speckle-only T=${T}: ${r.totalFound} false features`); }
}catch(e){ ok(false, 'T2 crashed: '+e.message); }
try{
// T3 noise-floor leakage: right half calm water at -23.5 dB (below NESZ), left half -14 dB; a dark film at the boundary
{ const W=400,H=300; const s=scene(W,H,(x,y)=>{ const film=x>170&&x<230&&Math.abs(y-150)<15; return {mu: film? -21 : (x>=200? -23.5 : -14)}; });
  const r=M.analyzeScene(s.db,s.valid,s.occ,W,H,30,base); const big=r.anoms.reduce((m,a)=>Math.max(m,a.area),0);
  const inNoise=r.anoms.filter(a=>a.cx>=200); const leak=inNoise.filter(a=>a.cls!=='noise');
  ok(big<15 && leak.length===0, `T3 noise-floor half (54 km2): largest feature ${big.toFixed(2)} km2 (v1 grows without bound); features centred in it: ${inNoise.length}, all classified "noise": ${leak.length===0}; measurable water ${(r.measFrac*100).toFixed(0)} %`); }
}catch(e){ ok(false, 'T3 crashed: '+e.message); }
try{
// T4 regional darkening larger than the background window
{ const W=500,H=500,px=40; const s=scene(W,H,(x,y)=>({mu: ((x-250)**2+(y-250)**2<170**2)? -17.5 : -12}));
  const r=M.analyzeScene(s.db,s.valid,s.occ,W,H,px,base); const a=r.anoms[0];
  ok(a && a.cls==='regional', `T4 large darkening classified ${a?a.cls:'none'} (area ${a?a.area.toFixed(1):'-'} km2 > ${r.regionalKm2.toFixed(1)} km2)`);
  console.log(`INFO T4 contrast of regional feature ${a?a.contrast.toFixed(2):'-'} dB vs truth 5.5 — flagged ill-defined by design (interior has no valid background)`); }
}catch(e){ ok(false, 'T4 crashed: '+e.message); }
try{
// T5 shoreline: dark rim along the shore inside the buffer; small in-water structures are not "shore"
{ const W=400,H=300,px=30; const s=scene(W,H,(x,y)=>{ const land=x<40; const struct=(x%50===0&&y%50===0); const rim=x>=40&&x<42; const slick=((x-250)/40)**2+((y-150)/8)**2<=1;
    return {occ: land||struct?0:100, mu: rim?-22:(slick?-20:-12)}; });
  const r=M.analyzeScene(s.db,s.valid,s.occ,W,H,px,base);
  ok(r.anoms.every(a=>a.shoreKm>0.1), `T5 shoreline rim inside the ${100} m buffer not detected (features: ${r.anoms.length})`);
  const a=r.anoms[0]; ok(a && a.shoreKm>4, `T5 shore distance measured to land, not to in-water structures: ${a?a.shoreKm.toFixed(2):'-'} km (truth 4.26 km: nearest border-touching land blob); structures masked: ${r.nStruct}`); }
}catch(e){ ok(false, 'T5 crashed: '+e.message); }
try{
// T6 exact geometry: 40x4 px rectangle, anisotropic pixels
{ const W=200,H=100; const s={db:new Float32Array(W*H).fill(-12),valid:new Uint8Array(W*H).fill(1),occ:new Float32Array(W*H).fill(100)};
  for(let y=48;y<52;y++) for(let x=80;x<120;x++) s.db[y*W+x]=-20;
  const r=M.analyzeScene(s.db,s.valid,s.occ,W,H,30,{...base,pxX:30,pxY:31}); const a=r.anoms[0];
  ok(Math.abs(a.area-160*30*31/1e6)<1e-9, `T6 area ${a.area.toFixed(6)} km2 = 160 px × 30 m × 31 m`);
  ok(Math.abs(a.lengthKm-1.2)<1e-9, `T6 extent ${a.lengthKm.toFixed(4)} km = 40 × 30 m`);
  ok(Math.abs(a.elong-Math.sqrt((1200**2/12)/(124**2/12)))<0.05, `T6 axis ratio ${a.elong.toFixed(3)} vs analytic ${Math.sqrt((1200**2)/(124**2)).toFixed(3)}`);
  ok(Math.abs(a.fillRatio-3/Math.PI)<0.02, `T6 fill ratio ${a.fillRatio.toFixed(3)} vs 3/π for a rectangle`);
  ok(Math.abs(a.contrast-8)<0.3, `T6 noiseless contrast ${a.contrast.toFixed(3)} dB (truth 8)`); }
}catch(e){ ok(false, 'T6 crashed: '+e.message); }
try{
// T7 persistence
{ const W=400,H=300,px=30; const f=(mu0)=>(x,y)=>({mu: ((x-220)/60)**2+((y-150)/12)**2<=1? mu0-8 : mu0});
  const A=scene(W,H,f(-12)), B=scene(W,H,f(-12)), Cq=scene(W,H,()=>({mu:-12})), Dn=scene(W,H,()=>({mu:-24}));
  const rA=M.analyzeScene(A.db,A.valid,A.occ,W,H,px,base); const run={anoms:rA.anoms, T:3};
  M.persistence(run, M.analyzeScene(B.db,B.valid,B.occ,W,H,px,base)); ok(run.anoms[0].persistStatus==='recurs', `T7a same slick in second scene → ${run.anoms[0].persistStatus} (${run.anoms[0].persist.toFixed(2)} dB)`);
  M.persistence(run, M.analyzeScene(Cq.db,Cq.valid,Cq.occ,W,H,px,base)); ok(run.anoms[0].persistStatus==='absent', `T7b clean second scene → ${run.anoms[0].persistStatus}`);
  M.persistence(run, M.analyzeScene(Dn.db,Dn.valid,Dn.occ,W,H,px,base)); ok(run.anoms[0].persistStatus==='untestable', `T7c second scene at noise floor → ${run.anoms[0].persistStatus}`); }
}catch(e){ ok(false, 'T7 crashed: '+e.message); }
try{
// T8 reasoning semantics
{ const run={sensor:M.SENSORS.S1, sceneId:'TEST', sceneTime:'2026-01-01T00:00:00Z', T:3, res:{regionalKm2:28}};
  const a={meanDb:-14, bgDb:-6, contrast:8, contrastU:0.2, p95Contrast:10, area:2, lengthKm:3.6, elong:5, fillRatio:0.9, edgeStep:3, shoreKm:5, touchesShoreBuffer:false, permFrac:1, noiseFrac:0, regional:false, cls:'measurable', persist:null, ptN:0, ptInside:0, ptMinKm:null};
  const R0=M.reason(a,run,{}); const st=id=>R0.hyps.find(h=>h.id===id).status;
  ok(st('rain')==='untested' && st('curr')==='untested', `T8a missing rain/current data → untested (rain ${st('rain')}, current ${st('curr')})`);
  ok(st('oil')==='untested', `T8b oil with no wind/persistence/context → ${st('oil')} (never consistent from missing evidence)`);
  ok(st('noise')==='untested' && st('shore')==='untested', 'T8c nominal noise and archival shore screens cannot rule out local explanations');
  const R1=M.reason(a,run,{fac:[]}); ok(R1.hyps.find(h=>h.id==='oil').status==='untested', 'T8d "no mapped facilities" is context only — does not change status');
  const R2=M.reason(a,run,{wind:{ws:17,wd:0,precip3h:0,time:'2026-01-01T00:00Z'}}); ok(R2.hyps.find(h=>h.id==='oil').status==='untested', 'T8e coarse high wind does not rule out a film');
  const R3=M.reason({...a,meanDb:-21.5,noiseFrac:0.8,cls:'noise'},run,{wind:{ws:5,wd:0,precip3h:0,time:'2026-01-01T00:00Z'}});
  ok(R3.verdict.title.startsWith('Not interpretable') && R3.hyps.find(h=>h.id==='oil').status==='untested', `T8f noise-floor anomaly → "${R3.verdict.title}", oil ${R3.hyps.find(h=>h.id==='oil').status}`);
  const R4=M.reason(a,run,{wind:{ws:5,wd:0,precip3h:2,time:'2026-01-01T00:00Z'}}); ok(R4.hyps.find(h=>h.id==='rain').status==='consistent', 'T8g ERA5 precipitation supplies compatible context without confirming a local rain cell');
  ok(!R4.hyps.some(h=>h.id==='oil'&&h.status==='supported'), 'T8h oil is never "supported" by single-band SAR evidence'); }
}catch(e){ ok(false, 'T8 crashed: '+e.message); }
{
  const date1='2026-01-01T00:00:00Z', date2='2026-01-13T00:00:00Z';
  const first={id:'A',properties:{datetime:date1,'sat:relative_orbit':94,platform:'sentinel-1c'}};
  const second={id:'B',properties:{...first.properties,datetime:date2}};
  ok(!M.comparability(first,second).canAssess,'T9a missing pass metadata does not establish comparability');
  first.properties['sat:orbit_state']=second.properties['sat:orbit_state']='descending';
  ok(M.comparability(first,second).canAssess,'T9b distinct date and matching pass and orbit admit descriptive comparison');
  second.properties.datetime=date1;
  ok(!M.comparability(first,second).canAssess,'T9c duplicate acquisition time is not a recurrence test');
  second.properties.datetime=date2; second.properties['sat:orbit_state']='ascending';
  ok(!M.comparability(first,second).canAssess,'T9d opposite pass is not a recurrence test');
  ok(M.persistenceLabel('absent')==='Below recurrence threshold','T9e displayed status qualifies a below-threshold comparison');
}
{
  const hourly={time:['2025-12-31T23:00','2026-01-01T00:00','2026-01-01T01:00'],wind_speed_10m:[4,null,6],precipitation:[0.2,0.4,null]};
  const target=Date.parse('2026-01-01T00:15:00Z');
  const missing=M.weatherAtAcquisition(hourly,target);
  ok(missing.ws===null && missing.precip3h===null,'T10a null wind and incomplete precipitation remain unknown');
  hourly.precipitation[2]=0.4;
  ok(Math.abs(M.weatherAtAcquisition(hourly,target).precip3h-1)<1e-8,'T10b three valid hourly totals span UTC midnight');
  hourly.time[2]='2026-01-01T02:00';
  ok(M.weatherAtAcquisition(hourly,target).precip3h===null,'T10c missing model hour invalidates the three-hour total');
}
console.log(fails? `${fails} FAILED` : 'ALL PASSED'); process.exit(fails?1:0);
