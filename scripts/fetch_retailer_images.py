"""Fetch shop product images (PcComponentes, Coolmod) as a FALLBACK for products without a manufacturer image.

Targets: canonical products that (a) have no image in data/images/*.json (curated or manufacturer-*.json; retailer-*.json
is ours and does not count), (b) are not 'ok' in the manufacturer pipeline state, and (c) have an APPROVED shop listing
(retailer_review_queue status='approved' -> product_id + shop SKU + product URL; exact identity already verified, so the
shop's image is model-scope for that SKU). The manufacturer image always wins: files are never overwritten, and
catalog_images.enrich_images gives precedence to non-retailer records.

Sources, in order:
  1. PcComponentes: image URL from /api/articles/list-by-ids (images.large, 40 SKUs per call -> no page requests).
  2. Coolmod: product page JSON-LD Product.image / og:image (robots.txt respected, >= 2 s between requests).
One request at a time per shop (PcComponentes >= 1 s, Coolmod >= 2 s). Challenges (403/429/"Just a moment"): the
product is recorded as blocked and a shop is stopped after repeated consecutive blocks. Images are accepted
only from the shop's own hosts (pccomponentes.com, coolmod.com); the host is recorded.

Provenance is staged in data/images/_state/retailer-<date>.json (manufacturer record format + image_origin='retailer',
attribution, image_scope='model', image_page_url, image_source_url, fetched_at). --finalize copies it to
data/images/retailer-<date>.json (the publisher then re-imports the catalog); deleting that one file removes every
retailer image from the catalog.

Usage: fetch_retailer_images.py [--limit N] [--dry-run] [--ids a,b] [--shop pccomponentes|coolmod] [--retry-skipped]
                               [--retry-failed] [--refresh-approved] [--approved FILE] [--status] [--finalize]
State: data/images/_state/retailer-progress.json (+ retailer-progress-summary.json).
"""
import argparse, hashlib, json, os, queue, random, signal, subprocess, sys, threading, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_product_images as fpi  # noqa: E402  (conversion/validation helpers, HTML parsing, robots, state helpers)

ROOT = fpi.ROOT
STATE_DIR = fpi.STATE_DIR
PCC_ENDPOINT = 'https://www.pccomponentes.com/api/articles/list-by-ids'
BATCH = 40
SHOPS = {
    'pccomponentes': {'name': 'PcComponentes', 'domain': 'pccomponentes.com', 'delay': 1.0},
    'coolmod': {'name': 'Coolmod', 'domain': 'coolmod.com', 'delay': 2.0},
}
SHOP_STOP_AFTER = 5
MAX_ATTEMPTS = 3
RIGHTS = 'Imagen de la ficha de la tienda usada solo como alternativa para identificar el producto; derechos reservados, sin licencia abierta asumida.'

def now(): return fpi.now()
def state_path(root): return Path(root)/STATE_DIR/'retailer-progress.json'
def staged_path(root, date): return Path(root)/STATE_DIR/f'retailer-{date}.json'

def load_state(root):
    path = state_path(root)
    if path.is_file(): return json.loads(path.read_text())
    return {'version': 1, 'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'products': {}, 'shops': {}}

def save_state(root, state): fpi.atomic_write(state_path(root), state)

# ---------- targets ----------
APPROVED_SQL = ("select coalesce(json_agg(t),'[]') from (select distinct on (shop_id,retailer_sku) shop_id,retailer_sku,"
                "proposed_product_id as product_id,candidate->>'url_public' as url_public,review_id from retailer_review_queue "
                "where status='approved' and proposed_product_id is not null order by shop_id,retailer_sku,review_id desc) t")

def export_approved(root):
    """Approved review rows straight from PostgreSQL (latest approved row per shop/SKU)."""
    cmd = ['docker', 'compose', '--env-file', '.env', '-f', 'infra/compose/compose.yaml', 'exec', '-T', 'postgres',
           'psql', '-U', 'stockfinder', '-d', 'stockfinder', '-At', '-c', APPROVED_SQL]
    out = subprocess.run(cmd, cwd=root, capture_output=True, text=True, stdin=subprocess.DEVNULL, check=True).stdout
    return json.loads(out)

def load_approved(root, path=None, refresh=False):
    cache = Path(root)/STATE_DIR/'retailer-approved-rows.json'
    if path: return json.loads(Path(path).read_text())
    if refresh or not cache.is_file():
        rows = export_approved(root); fpi.atomic_write(cache, rows); return rows
    return json.loads(cache.read_text())

def curated_image_ids(root):
    """Product ids with a non-retailer image record (curated files + manufacturer-*.json finalized)."""
    ids = set()
    for f in (Path(root)/'data/images').glob('*.json'):
        if f.name.startswith('retailer-'): continue
        ids |= {i['product_id'] for i in json.loads(f.read_text())}
    return ids

def manufacturer_status(root):
    path = Path(root)/STATE_DIR/'manufacturer-progress.json'
    try: return json.loads(path.read_text()).get('products', {})
    except (OSError, ValueError): return {}

def has_manufacturer_image(pid, curated, mfr_state):
    return pid in curated or mfr_state.get(pid, {}).get('status') == 'ok'

def mfr_priority(product, mfr_state):
    """0 = manufacturer pipeline cannot give an image (no URL / terminal skip), 1 = failed/blocked, 2 = still unprocessed."""
    if not (product.get('manufacturer_url') or '').startswith('https://'): return 0
    entry = mfr_state.get(product['id'])
    if not entry: return 2
    return 0 if entry['status'] == 'skip' else 1

def select_targets(products, rows, curated, mfr_state, state, args, public_dir=None):
    """List of {'product', 'listings': {shop: {'sku','url'}}} in processing order. Manufacturer images always win."""
    by_id = {p['id']: p for p in products}
    listings = {}
    for r in rows:
        if r['shop_id'] not in SHOPS or r['product_id'] not in by_id: continue
        if args.shop and r['shop_id'] != args.shop: continue
        listings.setdefault(r['product_id'], {}).setdefault(r['shop_id'], {'sku': str(r['retailer_sku']), 'url': r.get('url_public') or ''})
    out = []
    for pid, shops in listings.items():
        product = by_id[pid]
        if args.ids and pid not in args.ids: continue
        entry = state['products'].get(pid)
        if entry:
            if entry['status'] == 'ok': continue
            if entry['status'] == 'skip' and not args.retry_skipped: continue
            if entry['status'] in ('blocked', 'error') and entry.get('attempts', 1) >= MAX_ATTEMPTS and not args.retry_failed: continue
        if public_dir is not None and not entry and (Path(public_dir)/'images/products'/product['category_id']/f'{pid}.webp').exists():
            continue  # a file we did not create (manufacturer image): never overwritten
        out.append({'product': product, 'listings': shops})
    out.sort(key=lambda t: (mfr_priority(t['product'], mfr_state), t['product']['id']))
    return out

# ---------- sources ----------
def host_ok(url, shop):
    """Image host must be the shop's own registrable domain (https only). Returns (ok, host)."""
    parsed = urlparse(url or '')
    host = (parsed.hostname or '').lower()
    return parsed.scheme == 'https' and fpi.registrable(host) == SHOPS[shop]['domain'], host

def pcc_image_url(article):
    images = (article or {}).get('images') or {}
    for size in ('large', 'medium', 'small'):
        path = (images.get(size) or {}).get('path')
        if path: return path
    return None

def coolmod_image_url(parser, final_url, sku):
    """JSON-LD Product.image or og:image of the product page, hosted on coolmod.com. Returns ((url, kind), None) or (None, reason)."""
    skus = [str(n.get('sku')) for n in fpi._walk(parser.ld) if isinstance(n, dict) and fpi._is_product(n) and n.get('sku')]
    if skus and sku not in skus: return None, 'sku_mismatch'
    cands = [(k, u) for k, u in fpi.extract_candidates(parser, final_url) if k in ('jsonld', 'og')]
    if not cands: return None, 'no_image'
    foreign = False
    for kind, url in cands:
        if fpi.bad_image_url(url): continue
        if not host_ok(url, 'coolmod')[0]: foreign = True; continue
        return (url, kind), None
    return None, 'image_host_not_official' if foreign else 'no_image'

class Gap:
    """Minimum gap between requests of ONE shop (shared by API calls, pages and image downloads)."""
    def __init__(self, delay): self.delay, self.last = delay, 0.0
    def wait(self):
        gap = self.last + self.delay + random.uniform(0, 0.3) - time.monotonic()
        if gap > 0: time.sleep(gap)
        self.last = time.monotonic()

def make_fetcher(shop):
    f = fpi.Fetcher(2.0); f.throttle = Gap(SHOPS[shop]['delay']); return f

class Blocked(Exception): pass

def download_webp(fetcher, image_url):
    """Returns (skip_entry_or_None, webp, w, h); raises Blocked on 403/429/network failure."""
    try: status, ctype, data, _ = fetcher.get(image_url, binary=True)
    except Exception as exc: raise Blocked(f'image request failed: {type(exc).__name__}')
    if status in (403, 429): raise Blocked(f'image status {status}')
    if status != 200 or not ctype.startswith('image/') or 'svg' in ctype or 'gif' in ctype or len(data) > fpi.MAX_DOWNLOAD:
        return {'status': 'skip', 'reason': 'bad_image', 'http': status, 'content_type': ctype}, None, 0, 0
    try: webp, w, h = fpi.to_webp(data)
    except ValueError as exc:
        return {'status': 'skip', 'reason': 'image_too_small' if 'too_small' in str(exc) else 'bad_image'}, None, 0, 0
    return None, webp, w, h

def save_webp(root, product, webp):
    """Exclusive create: never replaces an existing file (e.g. a manufacturer image). Returns the web path or None."""
    rel = f"images/products/{product['category_id']}/{product['id']}.webp"
    dest = Path(root)/'apps/web/public'/rel; dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + f'.{os.getpid()}.{threading.get_ident()}.tmp'); tmp.write_bytes(webp)
    try: os.link(tmp, dest)
    except FileExistsError: return None
    finally: tmp.unlink(missing_ok=True)
    return '/' + rel

def finish_ok(root, product, shop, listing, image_url, kind, webp, w, h):
    path = save_webp(root, product, webp)
    if path is None: return {'status': 'skip', 'reason': 'file_exists'}
    return {'status': 'ok', 'shop': shop, 'image_path': path, 'image_url': image_url, 'page_url': listing['url'], 'source': kind,
            'image_host': urlparse(image_url).hostname.lower(), 'scope': 'model', 'attribution': SHOPS[shop]['name'], 'retailer_sku': listing['sku'],
            'bytes': len(webp), 'width': w, 'height': h, 'sha256': hashlib.sha256(webp).hexdigest(), 'fetched_at': now()}

def process_pcc(product, listing, article, fetcher, root, dry_run):
    if not article: return {'status': 'skip', 'reason': 'article_not_in_api'}
    image_url = pcc_image_url(article)
    if not image_url: return {'status': 'skip', 'reason': 'no_image'}
    ok, host = host_ok(image_url, 'pccomponentes')
    if not ok: return {'status': 'skip', 'reason': 'image_host_not_official', 'image_url': image_url}
    if fpi.bad_image_url(image_url): return {'status': 'skip', 'reason': 'bad_image', 'image_url': image_url}
    if dry_run: return {'status': 'dry-run', 'image_url': image_url, 'image_host': host}
    err, webp, w, h = download_webp(fetcher, image_url)
    if err: return {**err, 'image_url': image_url}
    return finish_ok(root, product, 'pccomponentes', listing, image_url, 'list-by-ids', webp, w, h)

def process_coolmod(product, listing, fetcher, robots, root, dry_run):
    url = listing['url']
    if not url.startswith('https://') or fpi.registrable(urlparse(url).hostname) != 'coolmod.com': return {'status': 'skip', 'reason': 'bad_url'}
    if not robots.allowed(url): return {'status': 'skip', 'reason': 'robots_disallow'}
    try: status, ctype, text, final_url = fetcher.get(url)
    except Exception as exc: raise Blocked(f'request failed: {type(exc).__name__}')
    if fpi.is_challenge(status, text): raise Blocked(f'challenge/status {status}')
    if status in (404, 410): return {'status': 'skip', 'reason': 'not_found', 'http': status}
    if status >= 500: return {'status': 'error', 'reason': f'http_{status}'}
    if status != 200 or 'html' not in ctype: return {'status': 'skip', 'reason': 'non_html', 'http': status}
    picked, reason = coolmod_image_url(fpi.parse_page(text), final_url, listing['sku'])
    if not picked: return {'status': 'skip', 'reason': reason}
    image_url, kind = picked
    if dry_run: return {'status': 'dry-run', 'image_url': image_url, 'image_host': urlparse(image_url).hostname.lower(), 'source': kind}
    err, webp, w, h = download_webp(fetcher, image_url)
    if err: return {**err, 'image_url': image_url}
    return finish_ok(root, product, 'coolmod', listing, image_url, kind, webp, w, h)

# ---------- manifest ----------
def file_sha(root, image_path):
    try: return hashlib.sha256((Path(root)/'apps/web/public'/image_path.lstrip('/')).read_bytes()).hexdigest()
    except OSError: return None

def build_manifest(state, root=None, curated=None, mfr_state=None):
    """Provenance records for accepted products. Dropped: products that meanwhile got a manufacturer image, files that are no
    longer ours (sha mismatch/missing, when root is given) and images shared by >=3 products (generic)."""
    ok = {}
    for pid, e in state['products'].items():
        if e['status'] != 'ok': continue
        if curated is not None and has_manufacturer_image(pid, curated, mfr_state or {}): continue
        if root is not None and file_sha(root, e['image_path']) != e['sha256']: continue
        ok[pid] = e
    uses = {}
    for pid, e in ok.items(): uses.setdefault(e['image_url'], set()).add(pid)
    records = []
    for pid in sorted(ok):
        e = ok[pid]
        if len(uses[e['image_url']]) >= 3: continue
        records.append({'product_id': pid, 'image_path': e['image_path'], 'image_source_url': e['image_url'], 'image_page_url': e['page_url'],
                        'image_scope': 'model', 'image_origin': 'retailer', 'attribution': e['attribution'], 'fetched_at': e['fetched_at'],
                        'image_host': e['image_host'], 'retailer_sku': e['retailer_sku'], 'sha256': e['sha256'], 'rights': RIGHTS})
    return records

def reconcile(root, state):
    """Rewrite the staged manifest; mark generic images (shared by >=3 products) as skipped and delete our files for them."""
    curated, mfr = curated_image_ids(root), manufacturer_status(root)
    records = build_manifest(state, root, curated, mfr)
    keep = {r['product_id'] for r in records}
    for pid, e in list(state['products'].items()):
        if e['status'] != 'ok' or pid in keep: continue
        if not has_manufacturer_image(pid, curated, mfr) and file_sha(root, e['image_path']) == e['sha256']:
            (Path(root)/'apps/web/public'/e['image_path'].lstrip('/')).unlink(missing_ok=True)
            state['products'][pid] = {'status': 'skip', 'reason': 'generic_image', 'image_url': e['image_url'], 'page_url': e['page_url']}
    fpi.atomic_write(staged_path(root, state['date']), records)
    return records

def finalize(root, state):
    records = reconcile(root, state)
    dest = Path(root)/f"data/images/retailer-{state['date']}.json"
    fpi.atomic_write(dest, records)
    sys.path.insert(0, str(Path(root)/'scripts'))
    from importlib import import_module
    import_module('catalog_images').enrich_images(fpi.load_products(root), root)   # raises on invalid/duplicate records
    return dest, len(records)

def summarize(state, total=None, started=None, done=0):
    counts, per_shop = {}, {}
    for e in state['products'].values():
        key = e['status'] if e['status'] == 'ok' else f"{e['status']}:{e.get('reason','')}"
        counts[key] = counts.get(key, 0) + 1
        if e['status'] == 'ok': per_shop[e['shop']] = per_shop.get(e['shop'], 0) + 1
    out = {'updated_at': now(), 'processed_total': len(state['products']), 'ok_by_shop': per_shop, 'counts': dict(sorted(counts.items())),
           'shops': {s: v for s, v in state['shops'].items() if v.get('stopped') or v.get('total_blocks')}}
    if total and started and done:
        rate = (time.monotonic() - started) / done
        out.update(run_done=done, run_total=total, eta_utc=datetime.fromtimestamp(time.time() + rate*(total-done), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    return out

# ---------- runner ----------
LOCK = threading.Lock()
SENTINEL = None

class Runner:
    """Two workers (one per shop, each strictly sequential): PcComponentes first; its failures fall through to Coolmod."""
    def __init__(self, root, state, args, fetchers=None):
        self.root, self.state, self.args = root, state, args
        self.stop = {'now': False}; self.progress = {'done': 0, 'started': time.monotonic(), 'total': 0}
        self.fetchers = fetchers or {}
        self.coolmod_q = queue.Queue()

    def fetcher(self, shop):
        if shop not in self.fetchers: self.fetchers[shop] = make_fetcher(shop)
        return self.fetchers[shop]

    def shop_state(self, shop):
        return self.state['shops'].setdefault(shop, {'consecutive_blocks': 0, 'total_blocks': 0, 'stopped': False})

    def note(self, shop, entry, blocked):
        """Update the shop's block counters and stamp the entry."""
        with LOCK:
            ss = self.shop_state(shop)
            if blocked:
                ss['consecutive_blocks'] += 1; ss['total_blocks'] += 1
                if ss['consecutive_blocks'] >= SHOP_STOP_AFTER and not ss['stopped']:
                    ss['stopped'] = True; ss['stopped_at'] = now(); print(f'!! shop {shop} stopped after {SHOP_STOP_AFTER} consecutive blocks', flush=True)
            elif entry['status'] != 'error': ss['consecutive_blocks'] = 0
        entry['ts'] = now(); entry.setdefault('shop', shop)
        return entry

    def commit(self, product, entry):
        with LOCK:
            self.progress['done'] += 1
            print(json.dumps({'id': product['id'], **{k: entry.get(k) for k in ('shop', 'status', 'reason', 'image_host', 'bytes', 'width', 'height') if entry.get(k) is not None}}), flush=True)
            if self.args.dry_run: return
            self.state['products'][product['id']] = entry
            save_state(self.root, self.state)
            if self.progress['done'] % 10 == 0:
                fpi.atomic_write(Path(self.root)/STATE_DIR/'retailer-progress-summary.json', summarize(self.state, self.progress['total'], self.progress['started'], self.progress['done']))

    def attempts(self, pid): return self.state['products'].get(pid, {}).get('attempts', 0) + 1

    def failure(self, product, exc):
        if isinstance(exc, Blocked): return {'status': 'blocked', 'reason': str(exc), 'attempts': self.attempts(product['id'])}, True
        return {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'[:200], 'attempts': self.attempts(product['id'])}, False

    def api_articles(self, fetcher, chunk):
        """(articles by id, blocked reason). One list-by-ids request for up to 40 SKUs."""
        try:
            fetcher.throttle.wait()
            skus = ','.join(t['listings']['pccomponentes']['sku'] for t in chunk)
            r = fetcher.session.get(PCC_ENDPOINT, params={'productIds': skus, 'sort': 'relevance', 'page': 1, 'pageSize': len(chunk)}, timeout=40,
                                    headers={'Accept': 'application/json', 'x-selected-language': 'es-ES'})
            if r.status_code != 200 or fpi.is_challenge(r.status_code, ''): return {}, f'api status {r.status_code}'
            return {str(a.get('id')): a for a in r.json().get('articles', [])}, None
        except Exception as exc: return {}, f'api request failed: {type(exc).__name__}'

    def run_pcc(self, targets):
        fetcher = self.fetcher('pccomponentes')
        try:
            for i in range(0, len(targets), BATCH):
                if self.stop['now'] or self.shop_state('pccomponentes')['stopped']: break
                chunk = targets[i:i+BATCH]
                articles, reason = self.api_articles(fetcher, chunk)
                for t in chunk:
                    if self.stop['now']: return
                    product, listing = t['product'], t['listings']['pccomponentes']
                    if reason: entry, blocked = {'status': 'blocked', 'reason': reason, 'attempts': self.attempts(product['id'])}, True
                    else:
                        try: entry, blocked = process_pcc(product, listing, articles.get(listing['sku']), fetcher, self.root, self.args.dry_run), False
                        except Exception as exc: entry, blocked = self.failure(product, exc)
                    entry = self.note('pccomponentes', entry, blocked)
                    if entry['status'] not in ('ok', 'dry-run') and 'coolmod' in t['listings']:
                        t['pcc_failure'] = entry; self.coolmod_q.put(t)   # fall back to Coolmod; final outcome decided there
                    else: self.commit(product, entry)
        finally:
            self.coolmod_q.put(SENTINEL)

    def run_coolmod(self, initial):
        fetcher = self.fetcher('coolmod'); robots = fpi.Robots(fetcher)
        def handle(t):
            product, listing = t['product'], t['listings']['coolmod']
            if self.shop_state('coolmod')['stopped'] or self.stop['now']:
                if 'pcc_failure' in t: self.commit(product, t['pcc_failure'])
                return
            try: entry, blocked = process_coolmod(product, listing, fetcher, robots, self.root, self.args.dry_run), False
            except Exception as exc: entry, blocked = self.failure(product, exc)
            entry = self.note('coolmod', entry, blocked)
            if entry['status'] not in ('ok', 'dry-run') and 'pcc_failure' in t:
                entry['pcc_tried'] = {k: t['pcc_failure'][k] for k in ('status', 'reason') if t['pcc_failure'].get(k)}
            self.commit(product, entry)
        for t in initial:
            if self.stop['now']: break
            handle(t)
        while True:
            t = self.coolmod_q.get()
            if t is SENTINEL: return
            if not self.stop['now']: handle(t)

    def run(self, targets):
        self.progress['total'] = len(targets)
        pcc = [t for t in targets if 'pccomponentes' in t['listings']]
        cool_only = [t for t in targets if 'pccomponentes' not in t['listings']]
        def job(fn, arg):
            try: fn(arg)
            except Exception as exc:
                print(f'worker error: {type(exc).__name__}: {exc}', flush=True)
                if fn == self.run_pcc: self.coolmod_q.put(SENTINEL)
        threads = [threading.Thread(target=job, args=(self.run_pcc, pcc)), threading.Thread(target=job, args=(self.run_coolmod, cool_only))]
        for th in threads: th.start()
        for th in threads: th.join()

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--limit', type=int); ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--ids', type=lambda s: set(s.split(','))); ap.add_argument('--shop', choices=list(SHOPS))
    ap.add_argument('--retry-skipped', action='store_true'); ap.add_argument('--retry-failed', action='store_true'); ap.add_argument('--reset-shops', action='store_true')
    ap.add_argument('--refresh-approved', action='store_true'); ap.add_argument('--approved', help='JSON file with approved rows (shop_id, retailer_sku, product_id, url_public)')
    ap.add_argument('--root', default=str(ROOT)); ap.add_argument('--status', action='store_true'); ap.add_argument('--finalize', action='store_true')
    args = ap.parse_args(argv); root = Path(args.root)
    state = load_state(root)
    if args.reset_shops: state['shops'] = {}
    if args.status: print(json.dumps(summarize(state), indent=1)); return 0
    if args.finalize:
        dest, n = finalize(root, state); save_state(root, state); print(f'finalized {n} records -> {dest}'); return 0
    rows = load_approved(root, args.approved, args.refresh_approved)
    todo = select_targets(fpi.load_products(root), rows, curated_image_ids(root), manufacturer_status(root), state, args, root/'apps/web/public')
    if args.limit: todo = todo[:args.limit]
    print(f'{len(todo)} products to process (dry_run={args.dry_run})', flush=True)
    runner = Runner(root, state, args)
    signal.signal(signal.SIGTERM, lambda *_: runner.stop.update(now=True)); signal.signal(signal.SIGINT, lambda *_: runner.stop.update(now=True))
    try: runner.run(todo)
    finally:
        if not args.dry_run:
            reconcile(root, state); save_state(root, state)
            fpi.atomic_write(root/STATE_DIR/'retailer-progress-summary.json', summarize(state, len(todo), runner.progress['started'], runner.progress['done']))
    print(json.dumps(summarize(state, len(todo), runner.progress['started'], runner.progress['done'])), flush=True)
    return 0

if __name__ == '__main__': sys.exit(main())
