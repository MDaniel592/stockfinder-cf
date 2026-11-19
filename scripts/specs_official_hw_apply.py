"""Aplica un dry-run de specs_official_hw_derive.py (placas base, CPU, GPU) con nas/catalog.write (alimenta el outbox).
Se ejecuta en el contenedor; el dry-run y la copia de seguridad viajan por el montaje /output (artifacts/neobyte).

  docker exec -i stockfinder-v2-neobyte-pipeline-1 python - /output/<dryrun>.json <inicio> <fin> [--apply] < scripts/specs_official_hw_apply.py

Sin --apply solo simula (dry-run por defecto). Lote = productos [inicio:fin) del dry-run ordenados por id (usar <=100).
Escribe /output/backup-<dryrun>-<inicio>-<fin>.json con el `data` previo (solo con --apply).
Reglas: relee cada producto; solo AÑADE claves que siguen ausentes; no toca nombre/marca/MPN/EAN ni otras specs;
omite el producto si cambió su nombre o categoría respecto al dry-run, si supera 24 claves o si algún valor no cumple
(clave <=80, valor <=250 caracteres, cadena no vacía). Un solo commit por lote."""
import sys, os, json, collections, psycopg
sys.path.insert(0, '/app/nas')
import catalog

path, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
APPLY = '--apply' in sys.argv
if b - a > 100:
    sys.exit('lote > 100 productos: usar rangos de 100 como máximo')
props = json.load(open(path))
by = collections.defaultdict(list)
for p in props:
    by[p['product_id']].append(p)
ids = sorted(by)[a:b]
conn = psycopg.connect('postgresql://stockfinder@postgres/stockfinder')
backup = {}
done = skipped = 0
reasons = collections.Counter()
for pid in ids:
    row = conn.execute("select data from catalog_entities where entity='product' and entity_id=%s and deleted_at is null", (pid,)).fetchone()
    if not row:
        skipped += 1; reasons['no existe'] += 1; continue
    data = row[0]
    first = by[pid][0]
    if data.get('name') != first['name'] or data.get('category_id') != first['category']:
        skipped += 1; reasons['nombre/categoría cambiados'] += 1; continue
    new = json.loads(json.dumps(data))
    specs = new.setdefault('specs', {})
    changed = []
    for p in by[pid]:
        k, v = p['key'], p['new']
        if not isinstance(v, str) or not v.strip() or len(k) > 80 or len(v) > 250 or k in specs:
            continue
        specs[k] = v
        changed.append(k)
    if not changed:
        skipped += 1; reasons['sin claves ausentes'] += 1; continue
    if len(specs) > 24:
        skipped += 1; reasons['>24 claves'] += 1; continue
    backup[pid] = data
    if APPLY:
        catalog.write(conn, 'product', pid, new)
    done += 1
if APPLY:
    conn.commit()
    json.dump(backup, open(f'/output/backup-{os.path.basename(path)[:-5]}-{a}-{b}.json', 'w'), ensure_ascii=False)
print(json.dumps(dict(apply=APPLY, productos=done, omitidos=skipped, motivos=dict(reasons), rango=[a, b], total_ids=len(by)), ensure_ascii=False))
