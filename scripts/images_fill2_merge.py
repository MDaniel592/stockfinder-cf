import json,sys,collections,re,shutil,tempfile
from pathlib import Path
R=Path('/opt/docker/StockFinder'); sys.path.insert(0,str(R/'scripts'))
from catalog_images import enrich_images
recs=[]
for i in range(1,5):
    recs+=json.loads((R/f'data/images/_state/fill2/fill-{i}.json').read_text())
print('registros',len(recs),'duplicados',[k for k,v in collections.Counter(r['product_id'] for r in recs).items() if v>1])
bad=[]
for r in recs:
    f=R/'apps/web/public'/r['image_path'].lstrip('/')
    if not f.exists() or f.stat().st_size<1500: bad.append((r['product_id'],'fichero'))
    if r.get('image_scope') not in('model','family'): bad.append((r['product_id'],'scope'))
    if not str(r.get('image_source_url','')).startswith('https://'): bad.append((r['product_id'],'url'))
print('problemas',bad)
rows=[]
for r in recs:
    x={k:r[k] for k in('product_id','image_path','image_source_url','image_scope') }
    for k in('image_page_url','attribution','fetched_at'):
        if r.get(k): x[k]=r[k]
    x['image_origin']='retailer' if r.get('image_origin')=='retailer' else 'manufacturer'; rows.append(x)
print('origen',dict(collections.Counter(x['image_origin'] for x in rows)),'ámbito',dict(collections.Counter(x['image_scope'] for x in rows)))
products=[]
for p in sorted((R/'data/catalog').glob('*.json')):
    d=json.loads(p.read_text()); products+=d if isinstance(d,list) else []
tmp=Path(tempfile.mkdtemp(dir='/var/tmp')); (tmp/'data/images').mkdir(parents=True)
for f in (R/'data/images').glob('*.json'): shutil.copy(f,tmp/'data/images'/f.name)
json.dump(rows,open(tmp/'data/images/fill2.json','w'))
out=enrich_images(products,tmp); print('enrich_images OK; sin imagen en catálogo tras fusionar:',sum(1 for p in out if not p.get('image_path')))
shutil.rmtree(tmp)
if '--write' in sys.argv:
    (R/'data/images/fill-2026-11-10b.json').write_text(json.dumps(rows,ensure_ascii=False,indent=1)); print('manifiesto escrito')
