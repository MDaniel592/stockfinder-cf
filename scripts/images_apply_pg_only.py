"""Aplica fotos a productos que solo existen en PostgreSQL (no están en data/catalog, así que enrich_images no los ve).
Usa nas/catalog.write (versión + outbox -> sync). Idempotente. Debe ir DESPUÉS de desplegar los .webp.
  docker exec -i stockfinder-v2-catalog-publisher-1 python3 - < scripts/images_apply_pg_only.py            # simulación
  docker exec -i stockfinder-v2-catalog-publisher-1 python3 - --write < scripts/images_apply_pg_only.py    # aplica
Los registros están en data/images/pg-only/*.json (subcarpeta no leída por el publicador)."""
import os,sys,json
from pathlib import Path
ROOT=Path(os.environ.get('SF_ROOT','/app')); sys.path.insert(0,str(ROOT/'nas'))
import psycopg
from psycopg.types.json import Jsonb
from catalog import write
WRITE='--write' in sys.argv
KEYS=('image_path','image_source_url','image_scope')
recs=[r for f in sorted((ROOT/'data/images/pg-only').glob('*.json')) for r in json.loads(f.read_text())]
done=miss=same=0
with psycopg.connect(os.environ['DATABASE_URL']) as conn:
    for r in recs:
        row=conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id=%s AND deleted_at IS NULL",(r['product_id'],)).fetchone()
        if not row: miss+=1; print('no existe en BD',r['product_id']); continue
        data=dict(row[0]); new={**data,**{k:r[k] for k in KEYS}}
        if new==data: same+=1; continue
        if WRITE: write(conn,'product',r['product_id'],new)
        done+=1
    if WRITE: conn.commit()
print(json.dumps(dict(registros=len(recs),aplicados=done,sin_cambios=same,no_existen=miss,escrito=WRITE)))
