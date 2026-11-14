"""Python detector v2 parity tests (synthetic)."""
import sys, math, numpy as np
import os; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')); import analysis as A
rng=np.random.default_rng(3); fails=0
def ok(c,m):
    global fails; print(('PASS ' if c else 'FAIL ')+m); fails+= (not c)
sp=lambda mu,shape: mu+10*np.log10(rng.gamma(4.4,1/4.4,shape))
H,W=300,400; yy,xx=np.mgrid[0:H,0:W]
# noise-floor half
db=np.where(xx>=200,-23.5,-14.0); film=(xx>170)&(xx<230)&(abs(yy-150)<15); db=np.where(film,-21,db); db=sp(db,(H,W))
r=A.detect(db,np.ones((H,W),bool),np.full((H,W),100.),30,3,0.05,-21)
leak=[a for a in r['anoms'] if a['cx']>=200 and a['class']!='noise']
ok(max([a['area_km2'] for a in r['anoms']]+[0])<15 and not leak, f"noise-floor half: largest {max([a['area_km2'] for a in r['anoms']]+[0]):.2f} km2, non-noise features in it: {len(leak)}")
# shoreline + structures
occ=np.full((H,W),100.); occ[:, :40]=0; occ[::50, ::50]=0; occ[:, :40]=0
db=np.full((H,W),-12.); db[:,40:42]=-22; sl=((xx-250)/40)**2+((yy-150)/8)**2<=1; db[sl]=-20; db=sp(db,(H,W))
r=A.detect(db,np.ones((H,W),bool),occ,30,3,0.05,-21)
ok(len(r['anoms'])==1 and r['anoms'][0]['shore_km']>4, f"shore: {len(r['anoms'])} feature(s); shore distance {r['anoms'][0]['shore_km']:.2f} km; structures {r['n_struct']}")
# exact geometry
db=np.full((100,200),-12.); db[48:52,80:120]=-20
r=A.detect(db,np.ones((100,200),bool),np.full((100,200),100.),30,3,0.05,-21,px_xy=(30,31)); a=r['anoms'][0]
ok(abs(a['area_km2']-160*30*31/1e6)<1e-9 and abs(a['length_km']-1.2)<1e-9 and abs(a['fill_ratio']-3/math.pi)<0.02 and abs(a['contrast_db']-8)<0.3,
   f"geometry: area {a['area_km2']:.6f}, extent {a['length_km']:.4f} km, fill {a['fill_ratio']:.3f}, axis ratio {a['elongation']:.3f}, contrast {a['contrast_db']:.3f}")
# persistence
f=lambda mu0: sp(np.where(((xx-220)/60)**2+((yy-150)/12)**2<=1, mu0-8, mu0),(H,W))
o=np.full((H,W),100.); v=np.ones((H,W),bool)
rA=A.detect(f(-12),v,o,30,3,0.05,-21)
A.persistence(rA['anoms'],A.detect(f(-12),v,o,30,3,0.05,-21),3); s1=rA['anoms'][0]['persist_status']
A.persistence(rA['anoms'],A.detect(sp(np.full((H,W),-12.),(H,W)),v,o,30,3,0.05,-21),3); s2=rA['anoms'][0]['persist_status']
A.persistence(rA['anoms'],A.detect(sp(np.full((H,W),-24.),(H,W)),v,o,30,3,0.05,-21),3); s3=rA['anoms'][0]['persist_status']
ok((s1,s2,s3)==('recurs','absent','untestable'), f"persistence: {s1}, {s2}, {s3}")
print('ALL PASSED' if not fails else f'{fails} FAILED'); sys.exit(fails)
