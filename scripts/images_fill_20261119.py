"""Rastreo de imágenes oficiales para productos de PostgreSQL sin image_path (no escribe en BD ni en apps/web).

Reutiliza scripts/fetch_product_images.py (robots.txt, >=2 s por dominio, WebP <=800 px, host oficial, 'model' vs 'family').
Entrada: work/images-fill-20261119/targets.json (volcado de PG de solo lectura). Salida en work/images-fill-20261119/:
  stage/apps/web/public/images/{products,thumbs}/...  y  state.json (resultado por producto, reanudable).
Solo se aceptan imágenes con ámbito 'model'. Después `finalize` (en el host) copia al repo y genera apply.json.
Uso (contenedor): docker compose -f infra/compose/manufacturer-scrape.yaml run --rm scraper python /work/scripts/images_fill_20261119.py fetch [--limit N] [--category C]
Uso (host):       python3 scripts/images_fill_20261119.py finalize
"""
import argparse, collections, json, shutil, sys, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
sys.path.insert(0, str(HERE))
WORK = ROOT/'work/images-fill-20261119'; STAGE = WORK/'stage'; STATE = WORK/'state.json'
PUBLIC = ROOT/'apps/web/public'
LOCK = threading.Lock()

def load_targets():
    t = json.loads((WORK/'targets.json').read_text())
    for p in t: p['offers'] = p.get('offers') or []
    return t

def priority(p):   # primero los que tienen más ofertas con stock
    return (-sum(1 for o in p['offers'] if o['stock'] in ('true', True)), -len(p['offers']))

def fetch(args):
    import fetch_product_images as fpi
    from make_thumbnails import make
    fpi.VARIANT_NOISE |= {'boxed', 'tray', 'box', 'wof', 'oem', 'bulk', 'retail'}   # mismo producto, distinto embalaje
    targets = load_targets()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    bd = fpi.load_brand_domains(ROOT)
    todo = [p for p in targets if (not args.category or p['cat'] == args.category) and p['id'] not in state
            and (p.get('murl') or '').startswith('https://')]
    todo.sort(key=priority)
    if args.limit: todo = todo[:args.limit]
    buckets = collections.defaultdict(list)
    for p in todo: buckets[fpi.registrable(urlparse(p['murl']).hostname)].append(p)
    print(len(todo), 'productos,', len(buckets), 'dominios', flush=True)
    done = [0]
    def tally(): return dict(collections.Counter(e['status'] + ':' + e.get('reason', '') for e in state.values()))
    def job(dom):
        fetcher = fpi.Fetcher(args.delay); robots = fpi.Robots(fetcher); blocks = 0
        for p in buckets[dom]:
            if blocks >= fpi.DOMAIN_STOP_AFTER: break
            prod = {'id': p['id'], 'brand': p['brand'] or '', 'name': p['name'], 'mpn': p['mpn'] or '', 'category_id': p['cat'], 'manufacturer_url': p['murl']}
            if (PUBLIC/'images/products'/p['cat']/f"{p['id']}.webp").exists(): entry = {'status': 'skip', 'reason': 'file_exists'}
            else:
                try: entry = fpi.process(prod, fetcher, robots, bd, STAGE, False); blocks = 0
                except fpi.Blocked as e: entry = {'status': 'blocked', 'reason': str(e)}; blocks += 1
                except Exception as e: entry = {'status': 'error', 'reason': f'{type(e).__name__}: {e}'[:200]}
            if entry['status'] == 'ok':
                src = STAGE/'apps/web/public'/entry['image_path'].lstrip('/')
                if entry['scope'] != 'model':
                    src.unlink(missing_ok=True)
                    entry = {'status': 'skip', 'reason': 'family_scope', 'image_url': entry['image_url'], 'page_url': entry['page_url']}
                else:
                    make(src, STAGE/'apps/web/public/images/thumbs'/p['cat']/f"{p['id']}.webp")
            with LOCK:
                state[p['id']] = entry; done[0] += 1
                STATE.write_text(json.dumps(state, ensure_ascii=False))
                if done[0] % 25 == 0: print(done[0], 'de', len(todo), tally(), flush=True)
    with ThreadPoolExecutor(args.workers) as pool: list(pool.map(job, sorted(buckets, key=lambda d: -len(buckets[d]))))
    print('fin', tally())

def finalize(args):
    """Host: descarta imágenes compartidas por >=2 productos (una imagen = un modelo), copia al repo y escribe apply.json."""
    state = json.loads(STATE.read_text()); targets = {p['id']: p for p in json.loads((WORK/'targets.json').read_text())}
    ok = {k: e for k, e in state.items() if e['status'] == 'ok'}
    by_url = collections.defaultdict(set); by_sha = collections.defaultdict(set)
    for k, e in ok.items(): by_url[e['image_url']].add(k); by_sha[e['sha256']].add(k)
    shared = {k for s in list(by_url.values()) + list(by_sha.values()) if len(s) > 1 for k in s}
    rows = []; moved = size = 0
    for k, e in sorted(ok.items()):
        rel = e['image_path'].lstrip('/'); cat = targets[k]['cat']
        src = STAGE/'apps/web/public'/rel; thumb = STAGE/'apps/web/public/images/thumbs'/cat/f'{k}.webp'
        if k in shared:
            state[k] = {'status': 'skip', 'reason': 'imagen_compartida_por_varios_modelos', 'image_url': e['image_url']}; continue
        d = PUBLIC/rel; dt = PUBLIC/'images/thumbs'/cat/f'{k}.webp'
        if d.exists(): state[k] = {'status': 'skip', 'reason': 'file_exists'}; continue
        d.parent.mkdir(parents=True, exist_ok=True); dt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, d); shutil.copy2(thumb, dt); size += d.stat().st_size + dt.stat().st_size; moved += 1
        rows.append({'id': k, 'image_path': e['image_path'], 'image_source_url': e['image_url'], 'image_scope': 'model',
                     'evidencia': {'pagina_oficial': e['page_url'], 'host': e['image_host'], 'origen': e['source'], 'sha256': e['sha256'],
                                   'bytes': e['bytes'], 'ancho': e['width'], 'alto': e['height'], 'obtenida': e['fetched_at'], 'categoria': cat}})
    (WORK/'apply.json').write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (ROOT/'data/images/apply-20261119').mkdir(exist_ok=True); shutil.copy2(WORK/'apply.json', ROOT/'data/images/apply-20261119/apply.json')
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    print('copiados', moved, 'bytes', size, 'compartidas descartadas', len(shared))

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['fetch', 'finalize'])
    ap.add_argument('--limit', type=int); ap.add_argument('--category'); ap.add_argument('--delay', type=float, default=2.0); ap.add_argument('--workers', type=int, default=8)
    a = ap.parse_args(); fetch(a) if a.cmd == 'fetch' else finalize(a)
