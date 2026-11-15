import json,glob,re,sys,collections
from pathlib import Path
R=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(R/'nas'))
import spec_overrides as so
INFER=re.compile(r'infer|dedu[cz]|asum|supuest|toda la serie|ficha de serie|suposici',re.I)
existing=set()
for f in glob.glob(str(R/'data/overrides/*.json')):
    for r in json.load(open(f)): existing.add((r['product_id'],r['key']))
keep=[];excl=[];coll=0;seen=set()
def reason(r,lot):
    np=r.get('status')=='not_published'; k=r['key']; pid=r['product_id']; note=r.get('note','')
    if INFER.search(note): return 'inferencia declarada en la nota'
    if k=='Disipador integrado' and not np: return 'lote9: "No" deducido de dimensiones'
    if lot=='8' and np and k=='Modular': return 'lote8: ausencia en ficha no prueba no publicado'
    if np and k=='Formato fuente' and 'forgeon' not in pid: return 'lote6/7: manual PDF sin revisar'
    if lot=='3' and k=='Formato' and ('goodram' in pid or 'gskill' in pid): return 'lote3: UDIMM inferido'
    if lot=='2' and k=='Formato' and 'gskill' in pid: return 'lote2: UDIMM inferido'
    if pid=='teamgroup-tf13d416g3600hc18jdc01' and k=='Perfil': return 'lote2: XMP recortado (ficha dice XMP o D.O.C.P)'
    if pid=='klevv-udimm-16gb-ddr4-3200-cl22': return 'lote2: not_published sin P/N'
    if pid in ('nvidia-geforce-rtx-5090','nvidia-geforce-rtx-5080','nvidia-geforce-rtx-5070') and k=='Longitud': return 'lote4: longitud FE en entrada genérica'
    return None
for f in sorted(glob.glob(str(R/'artifacts/spec-fill/spec-fill-*.json'))):
    lot=f.split('-')[-1][:-5]
    for r in json.load(open(f)):
        key=(r['product_id'],r['key'])
        if key in existing: coll+=1; continue
        if key in seen: continue
        why=reason(r,lot)
        err=so.validate_record(r)
        if err: excl.append(dict(r,_why='validador: %s'%err,_lot=lot)); continue
        if why: excl.append(dict(r,_why=why,_lot=lot)); continue
        seen.add(key); keep.append(r)
by=collections.defaultdict(list)
for r in keep: by[r['category_id']].append(r)
for cat,rs in by.items():
    json.dump(rs,open(R/f'data/overrides/agents-2026-11-10-{cat}.json','w'),ensure_ascii=False,indent=2)
json.dump(excl,open(R/'artifacts/spec-fill/excluded-for-review.json','w'),ensure_ascii=False,indent=1)
print('kept',len(keep),{c:len(v) for c,v in by.items()},'| excluded',len(excl),'| already existing',coll)
print(collections.Counter(e['_why'] for e in excl).most_common())
recs,errs=so.load_overrides(R); print('load_overrides total',len(recs),'errors',len(errs),list(errs.items())[:3] if isinstance(errs,dict) else errs[:3])
