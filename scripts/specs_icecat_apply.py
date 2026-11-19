"""Aplica a produccion el dry-run de specs de Icecat (work/specs-icecat-<fecha>/dryrun.json de specs_icecat_dryrun.py).
Se ejecuta en el contenedor (monta nas/ scripts/ y /output = artifacts/neobyte); copiar antes el dryrun a /output:
  cp work/specs-icecat-20261119/dryrun.json artifacts/neobyte/specs-icecat-20261119/dryrun.json
  docker exec -i stockfinder-v2-neobyte-pipeline-1 python - /output/specs-icecat-20261119 <inicio> <fin> [--apply] < scripts/specs_icecat_apply.py
Sin --apply solo simula y escribe <dir>/apply-<inicio>-<fin>.json. Con --apply: relee cada producto, SOLO anade claves aun
ausentes (nunca sobrescribe ni toca marca/MPN/EAN/nombre), exige que el MPN siga siendo el verificado, vuelve a pasar el valor
por normalize_specs, valida formato (cadena sin saltos, clave <=80, valor <=250, <=24 claves), escribe con nas/catalog.write +
commit explicito y guarda <dir>/backup-<inicio>-<fin>.json con el data previo. Lote = productos [inicio:fin) ordenados por id
(maximo 50 por lote). El Worker rechaza un lote entero si un evento es invalido: por eso se valida todo antes de escribir."""
import collections, json, re, sys
import psycopg
sys.path.insert(0, '/app/nas')
import catalog
import spec_normalize as sn

d, a, b = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
APPLY = '--apply' in sys.argv
if b - a > 50:
    sys.exit('Lote maximo: 50 productos')
by = collections.defaultdict(list)
for e in json.load(open(f'{d}/dryrun.json', encoding='utf-8')):
    by[e['product_id']].append(e)
ids = sorted(by)[a:b]
conn = psycopg.connect('postgresql://stockfinder@postgres/stockfinder')
backup, applied, skipped = {}, [], []
for pid in ids:
    row = conn.execute("select data from catalog_entities where entity='product' and entity_id=%s and deleted_at is null", (pid,)).fetchone()
    if not row:
        skipped.append((pid, 'no existe')); continue
    data = row[0]
    exp = by[pid][0]
    if re.sub(r'\s+', '', data.get('mpn') or '').upper() != re.sub(r'\s+', '', exp['mpn']).upper() or data.get('category_id') != exp['category']:
        skipped.append((pid, 'MPN/categoria cambiados desde la verificacion')); continue
    new = json.loads(json.dumps(data))
    specs = new.setdefault('specs', {})
    added = []
    for e in by[pid]:
        k = e['key']
        if k in specs:
            continue
        v = sn.normalize_specs(data['category_id'], {**specs, k: e['mapped_value']}).get(k)
        if not isinstance(v, str) or not v or v != e['value'] or '\n' in v or len(k) > 80 or len(v) > 250 or len(specs) >= 24:
            skipped.append((pid, f'{k}: no valido ({e["mapped_value"]!r})')); continue
        specs[k] = v
        added.append(dict(product_id=pid, key=k, value=v, icecat_feature=e['icecat_feature'], icecat_id=e['icecat_id']))
    if not added:
        skipped.append((pid, 'sin claves nuevas')); continue
    if any(new.get(f) != data.get(f) for f in ('brand', 'mpn', 'name', 'category_id', 'ean')):
        skipped.append((pid, 'identidad alterada, abortado')); continue
    backup[pid] = data
    applied += added
    if APPLY:
        catalog.write(conn, 'product', pid, new)
if APPLY:
    conn.commit()
    json.dump(backup, open(f'{d}/backup-{a}-{b}.json', 'w', encoding='utf-8'), ensure_ascii=False)
json.dump(applied, open(f'{d}/apply-{a}-{b}.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
print(json.dumps(dict(apply=APPLY, productos=len(backup), claves=len(applied), omitidas=skipped[:15], rango=[a, b], total_ids=len(by)), ensure_ascii=False))
