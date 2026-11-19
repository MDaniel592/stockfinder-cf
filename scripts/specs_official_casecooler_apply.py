"""Aplica a producción un dry-run de specs_official_casecooler_extract.py (cajas y refrigeración) con nas/catalog.write.
Se ejecuta en el contenedor; el dry-run y la copia de seguridad viajan por /output (artifacts/neobyte).
  docker exec -i stockfinder-v2-neobyte-pipeline-1 python - <categoria> /output/<dryrun>.json <inicio> <fin> [--apply] < scripts/specs_official_casecooler_apply.py
(o los envoltorios specs_official_case_apply.py / specs_official_cooler_apply.py, que fijan la categoría).
Sin --apply solo simula. Lote = productos [inicio:fin) del dry-run (de esa categoría) ordenados por id.
Vuelve a leer cada producto y solo AÑADE claves que siguen ausentes; nunca toca identidad ni otras specs.
Con --apply escribe /output/backup-specs-official-<categoria>-<inicio>-<fin>.json con el data previo.
Cuota D1: ~15 filas por producto reescrito; lotes <=100, pausa >=2 min y comprobar outbox=0 entre lotes."""
import sys, json, collections, psycopg
sys.path.insert(0, '/app/nas')
import catalog
from spec_normalize import normalize_value

cat, path, a, b = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
APPLY = '--apply' in sys.argv
items = json.load(open(path, encoding='utf-8'))['items']
by = collections.defaultdict(list)
for p in items:
    if p['category'] == cat:
        by[p['product_id']].append(p)
ids = sorted(by)[a:b]
conn = psycopg.connect('postgresql://stockfinder@postgres/stockfinder')
backup, done, skipped, keys = {}, 0, 0, collections.Counter()
for pid in ids:
    row = conn.execute("select data from catalog_entities where entity='product' and entity_id=%s and deleted_at is null", (pid,)).fetchone()
    if not row or row[0].get('category_id') != cat:
        skipped += 1
        continue
    data = row[0]
    new = json.loads(json.dumps(data))
    specs = new.setdefault('specs', {})
    changed = []
    for p in by[pid]:
        k = p['key']
        v = normalize_value(cat, k, p['raw'])
        if not isinstance(v, str) or not v or len(k) > 80 or len(v) > 250 or k in specs:
            continue
        specs[k] = v
        changed.append(k)
    if not changed or len(specs) > 24:
        skipped += 1
        continue
    backup[pid] = data
    keys.update(changed)
    if APPLY:
        catalog.write(conn, 'product', pid, new)
    done += 1
if APPLY:
    conn.commit()
    json.dump(backup, open(f'/output/backup-specs-official-{cat}-{a}-{b}.json', 'w', encoding='utf-8'), ensure_ascii=False)
print(json.dumps(dict(apply=APPLY, categoria=cat, productos=done, omitidos=skipped, claves=dict(keys), rango=[a, b], total_ids=len(by)), ensure_ascii=False))
