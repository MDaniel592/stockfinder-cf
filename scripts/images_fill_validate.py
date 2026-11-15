import json,sys,collections,re,tempfile,shutil
from pathlib import Path
R=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(R/'scripts'))
from catalog_images import enrich_images
fill=R/'data/images/_state/fill'
recs=[];by_file={}
for i in range(0,9):
    p=fill/f'fill-{i}.json'
    if not p.exists(): print('MISSING',p); continue
    d=json.loads(p.read_text()); by_file[i]=len(d); recs+=[dict(r,_lot=i) for r in d]
print('records',len(recs),by_file)
c=collections.Counter(r['product_id'] for r in recs); dup=[k for k,v in c.items() if v>1]
print('duplicate product ids',len(dup),dup[:5])
products0=[]
for pth in sorted((R/"data/catalog").glob("*.json")):
    d=json.loads(pth.read_text()); products0+=d if isinstance(d,list) else []
base=enrich_images(products0,R)
ids_miss={p["id"] for p in base if not p.get("image_path")}; have=set(c)
print('target',len(ids_miss),'covered',len(ids_miss&have),'not in fill',len(ids_miss-have),'extra',len(have-ids_miss))
print('not covered sample',sorted(ids_miss-have)[:25])
bad=[]
for r in recs:
    f=R/'apps/web/public'/r['image_path'].lstrip('/')
    if not f.exists(): bad.append((r['product_id'],'nofile'))
    elif f.stat().st_size<1500: bad.append((r['product_id'],'tiny %d'%f.stat().st_size))
    if not re.fullmatch(r'/images/products/[a-zA-Z0-9_/-]+\.(png|jpe?g|webp|svg)',r['image_path']): bad.append((r['product_id'],'badpath'))
    if r.get('image_scope') not in ('model','family'): bad.append((r['product_id'],'scope'))
    if not str(r.get('image_source_url','')).startswith('https://'): bad.append((r['product_id'],'src'))
print('record problems',len(bad),bad[:10])
print('origin',dict(collections.Counter(r.get('image_origin') for r in recs)),'scope',dict(collections.Counter(r.get('image_scope') for r in recs)))
products=[]
for pth in sorted((R/'data/catalog').glob('*.json')):
    d=json.loads(pth.read_text()); products+=d if isinstance(d,list) else []
tmp=Path(tempfile.mkdtemp(dir='/var/tmp')); (tmp/'data/images').mkdir(parents=True)
for f in (R/'data/images').glob('*.json'): shutil.copy(f,tmp/'data/images'/f.name)
rows=[]
for r in recs:
    x={k:r[k] for k in ('product_id','image_path','image_source_url','image_scope')}
    if r.get('image_origin')=='retailer': x['image_origin']='retailer'
    rows.append(x)
json.dump(rows,open(tmp/'data/images/fill-all.json','w'))
try:
    out=enrich_images(products,tmp)
    still=[p['id'] for p in out if not p.get('image_path')]
    print('enrich_images OK. products',len(out),'without image after merge',len(still))
    json.dump(still,open('/var/tmp/sfphotos-still-missing.json','w'))
except Exception as e: print('enrich_images ERROR',repr(e))
shutil.rmtree(tmp)
