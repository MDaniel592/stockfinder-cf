"""Normaliza claves de specs de cajas en produccion (Abysm, Neobyte): alias -> claves canonicas. Dry-run salvo APPLY=1."""
import os,sys,json,psycopg
sys.path.insert(0,'/app/nas')
from catalog import write
from spec_normalize import normalize_specs
APPLY=os.environ.get('APPLY')=='1'
c=psycopg.connect('postgresql://stockfinder@postgres/stockfinder')
rows=c.execute("select entity_id,data from catalog_entities where entity='product' and deleted_at is null and data->>'category_id'='case'").fetchall()
changed=[];backup={}
for pid,data in rows:
    new=normalize_specs('case',data.get('specs',{}))
    if new!=data.get('specs') and len(new)<=24:
        changed.append((pid,data,new))
print('cajas',len(rows),'a cambiar',len(changed))
import collections
cnt=collections.Counter()
for pid,d,n in changed:
    for k in set(n)-set(d['specs']): cnt['+'+k]+=1
    for k in set(d['specs'])-set(n): cnt['-'+k]+=1
print(dict(cnt.most_common(14)))
if APPLY:
    with c.transaction():
        for pid,d,n in changed: write(c,'product',pid,{**d,'specs':n})
    c.commit(); print('aplicado')
