"""Aplica un dry-run de specs_fill_derive.py a producción con nas/catalog.write (alimenta el outbox).
Se ejecuta en el contenedor; el dry-run y la copia de seguridad viajan por el montaje /output (artifacts/neobyte).
  docker exec -i stockfinder-v2-neobyte-pipeline-1 python - /output/dryrun.json <inicio> <fin> [--apply] < scripts/specs_fill_apply.py
Sin --apply solo simula. Lote = productos [inicio:fin) del dry-run ordenados por id. Escribe /output/backup-<inicio>-<fin>.json con el data previo.
Reglas: solo añade claves que siguen ausentes (o retira una clave solo si su valor sigue siendo el esperado); nunca toca identidad ni otras specs."""
import sys, json, collections, psycopg
sys.path.insert(0, '/app/nas')
import catalog

path, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
APPLY = '--apply' in sys.argv
props = json.load(open(path))
by = collections.defaultdict(list)
for p in props:
    by[p['product_id']].append(p)
ids = sorted(by)[a:b]
conn = psycopg.connect('postgresql://stockfinder@postgres/stockfinder')
backup = {}
done = skipped = 0
for pid in ids:
    row = conn.execute("select data from catalog_entities where entity='product' and entity_id=%s and deleted_at is null", (pid,)).fetchone()
    if not row:
        skipped += 1
        continue
    data = row[0]
    new = json.loads(json.dumps(data))
    specs = new.setdefault('specs', {})
    changed = []
    for p in by[pid]:
        k, v = p['key'], p['new']
        if not isinstance(v, str) or not v or len(k) > 80 or len(v) > 250:
            continue
        if k in specs:
            continue
        rem = p.get('remove') or []
        for r in rem:
            specs.pop(r, None)
        specs[k] = v
        changed.append(k)
    if not changed or len(specs) > 24:
        skipped += 1
        continue
    backup[pid] = data
    if APPLY:
        catalog.write(conn, 'product', pid, new)
    done += 1
if APPLY:
    conn.commit()
    json.dump(backup, open(f'/output/backup-{a}-{b}.json', 'w'), ensure_ascii=False)
print(json.dumps(dict(apply=APPLY, productos=done, omitidos=skipped, rango=[a, b], total_ids=len(by))))
