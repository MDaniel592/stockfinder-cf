"""Aplica a producción las propuestas de specs oficiales de fuentes (raw-*.json de specs_official_extract.py).
En el contenedor (monta nas/ y /output = artifacts/neobyte):
  docker exec -i stockfinder-v2-neobyte-pipeline-1 python - /output/specs-official-psu-20261119 <inicio> <fin> [--apply] < scripts/specs_official_psu_apply.py
Sin --apply solo simula y escribe /output/<dir>/dryrun-<inicio>-<fin>.json (producto, clave, valor normalizado, URL, fecha).
Con --apply: relee cada producto, SOLO añade claves aún ausentes (nunca sobrescribe ni toca marca/MPN/EAN/nombre), pasa por
normalize_specs, exige <=24 claves, clave <=80 y valor <=250 caracteres, escribe con nas/catalog.write + commit y guarda
/output/<dir>/backup-<inicio>-<fin>.json con el data previo. Lote = productos [inicio:fin) ordenados por id (usar lotes <=100)."""
import sys, os, json, glob, collections, psycopg
sys.path.insert(0, '/app/nas')
import catalog, spec_normalize as sn

d, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
APPLY = '--apply' in sys.argv
by = collections.defaultdict(list)
for f in sorted(glob.glob(f'{d}/raw-*.json')):
    for p in json.load(open(f)):
        by[p['product_id']].append(p)
ids = sorted(by)[a:b]
conn = psycopg.connect('postgresql://stockfinder@postgres/stockfinder')
backup, dry, skipped = {}, [], []
for pid in ids:
    row = conn.execute("select data from catalog_entities where entity='product' and entity_id=%s and deleted_at is null", (pid,)).fetchone()
    if not row:
        skipped.append((pid, 'no existe')); continue
    data = row[0]
    new = json.loads(json.dumps(data))
    specs = new.setdefault('specs', {})
    added = []
    for p in by[pid]:
        k = p['key']
        if k in specs:
            continue
        norm = sn.normalize_specs(data['category_id'], {**specs, k: p['raw']})
        v = norm.get(k)
        if not isinstance(v, str) or not v or len(k) > 80 or len(v) > 250:
            skipped.append((pid, f'{k}: valor no normalizable {p["raw"]!r}')); continue
        specs[k] = v
        added.append(dict(product_id=pid, name=data['name'], key=k, new=v, raw=p['raw'], source_url=p['source_url'], fetched_at=p['fetched_at'], evidence=p['evidence']))
    if not added or len(specs) > 24:
        skipped.append((pid, 'sin cambios o >24 claves')); continue
    dry += added
    backup[pid] = data
    if APPLY:
        catalog.write(conn, 'product', pid, new)
if APPLY:
    conn.commit()
    json.dump(backup, open(f'{d}/backup-{a}-{b}.json', 'w'), ensure_ascii=False)
json.dump(dry, open(f'{d}/dryrun-{a}-{b}.json', 'w'), ensure_ascii=False, indent=1)
print(json.dumps(dict(apply=APPLY, productos=len(backup), claves=len(dry), omitidas=skipped[:15], rango=[a, b], total_ids=len(by)), ensure_ascii=False))
