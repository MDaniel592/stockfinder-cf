"""Aplica las imágenes de data/images/apply-20261119/apply.json (copia de work/images-fill-20261119/apply.json) a PostgreSQL (catalog.write + commit).

Simulación por defecto. Con APPLY=1 escribe (~15 filas D1 por producto al sincronizar). Aplicar SOLO tras desplegar la web (en el contenedor no se
comprueba el fichero; en el host sí). Solo toca productos activos que siguen sin image_path. Idempotente.
Variables: LIMIT=N (máximo de productos por ejecución), CATEGORY=psu (filtro).

  docker exec -i -e APPLY=1 -e LIMIT=300 stockfinder-v2-neobyte-pipeline-1 python - < scripts/images_apply_20261119.py
"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, '/app/nas')
import psycopg
from catalog import write

ROOT = Path(os.environ.get('SF_ROOT', '/app'))
APPLY = os.environ.get('APPLY') == '1'
LIMIT = int(os.environ.get('LIMIT') or 0); CATEGORY = os.environ.get('CATEGORY')
DSN = os.environ.get('DATABASE_URL') or 'postgresql://stockfinder@postgres/stockfinder'
PUBLIC = ROOT/'apps/web/public'          # en el contenedor no existe (no monta apps/): entonces no se comprueba el fichero
CHECK_FILES = PUBLIC.exists()
rows = json.loads((ROOT/'data/images/apply-20261119/apply.json').read_text())
stats = dict(registros=len(rows), aplicados=0, sin_fichero=0, ya_con_imagen=0, no_existe=0, filtrados=0)
with psycopg.connect(DSN) as conn:
    for r in rows:
        if CATEGORY and r['evidencia']['categoria'] != CATEGORY: stats['filtrados'] += 1; continue
        if LIMIT and stats['aplicados'] >= LIMIT: break
        cat = r['evidencia']['categoria']
        if CHECK_FILES and (not (PUBLIC/r['image_path'].lstrip('/')).exists() or not (PUBLIC/'images/thumbs'/cat/f"{r['id']}.webp").exists()):
            stats['sin_fichero'] += 1; continue
        row = conn.execute("select data from catalog_entities where entity='product' and entity_id=%s and deleted_at is null", (r['id'],)).fetchone()
        if not row: stats['no_existe'] += 1; continue
        data = dict(row[0])
        if data.get('image_path'): stats['ya_con_imagen'] += 1; continue
        new = {**data, 'image_path': r['image_path'], 'image_source_url': r['image_source_url'], 'image_scope': r['image_scope']}
        if APPLY: write(conn, 'product', r['id'], new)
        stats['aplicados'] += 1
    if APPLY: conn.commit()
print(json.dumps({**stats, 'escrito': APPLY}))
