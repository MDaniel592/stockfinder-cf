"""Fetch official manufacturer product images (one request at a time, >=2 s apart) and store them as local WebP.

For each canonical product with an https `manufacturer_url` and no image yet (data/images/*.json, excluding
manufacturer-*.json) the OFFICIAL page is fetched with browser TLS (curl_cffi, like fetch_official.py), the main
image (JSON-LD Product.image, og:image, or the primary gallery image) is accepted only when hosted on the
manufacturer's domain / a documented alias / a known CDN, converted to WebP (<=800 px long side, ~<80 KB) and
saved at apps/web/public/images/products/<category>/<product_id>.webp.

On anti-bot challenges (Incapsula, captcha, "Just a moment", 403/429) the product is skipped and
recorded, and a domain with repeated consecutive blocks is stopped.

Provenance is staged in data/images/_state/manufacturer-<date>.json (same record format as data/images/*.json
plus attribution/fetched_at) and only copied to data/images/manufacturer-<date>.json by --finalize, because the
publisher re-imports the whole catalog whenever data/images/*.json changes (and the files must be deployed first).

Usage:
  fetch_product_images.py [--limit N] [--dry-run] [--delay 2] [--retry-skipped] [--retry-failed] [--reset-domains]
                          [--brand NAME] [--category CAT] [--ids a,b] [--finalize] [--status]
Resumable: state in data/images/_state/manufacturer-progress.json; progress summary in ...-summary.json.
"""
import argparse, threading, hashlib, io, json, os, random, re, signal, sys, time, unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

_DEPS = os.environ.get('STOCKFINDER_PYDEPS', '/tmp/stockfinder-pydeps')
if os.path.isdir(_DEPS) and _DEPS not in sys.path: sys.path.append(_DEPS)

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = 'data/images/_state'
MAX_SIDE, TARGET_BYTES, MIN_SIDE = 800, 80_000, 200
MAX_DOWNLOAD = 12_000_000
DOMAIN_STOP_AFTER = 5          # consecutive blocks
MAX_ATTEMPTS = 3               # transient failures per product before it is treated as terminal
TERMINAL = {'non_html', 'no_image', 'image_host_not_official', 'image_too_small', 'generic_image', 'robots_disallow',
            'not_found', 'bad_image', 'bad_url'}
# CDN hosts (registrable domain) accepted in addition to the page's own domain and data/brand-domains.json.
# Keyed by the page's registrable domain. Add only documented first-party CDNs.
KNOWN_CDNS = {
    'corsair.com': ['corsair.com'], 'nzxt.com': ['nzxt.com'], 'kingston.com': ['kingston.com'],
    'xpg.com': ['adata.com'], 'adata.com': ['xpg.com'], 'crucial.com': ['micron.com'],
}
SECOND_LEVEL = {'co', 'com', 'org', 'net', 'ac', 'gov'}

def registrable(host):
    parts = (host or '').lower().strip('.').split('.')
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in SECOND_LEVEL: return '.'.join(parts[-3:])
    return '.'.join(parts[-2:])

def norm_brand(value):
    return re.sub(r'[^a-z0-9]', '', (value or '').lower())

def fold(text):
    return unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode().lower()

def tokens(text):
    return re.findall(r'[a-z0-9]+', fold(text))

# ---------- HTML extraction ----------
class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.links, self.ld, self.imgs, self.h1 = {}, [], [], [], []
        self.title = ''
        self._in = None; self._buf = []; self._ld_attrs = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or '') for k, v in attrs}
        if tag == 'meta':
            key = (a.get('property') or a.get('name') or '').lower()
            if key and a.get('content'): self.meta.setdefault(key, []).append(a['content'].strip())
        elif tag == 'link' and 'image_src' in a.get('rel', '').lower() and a.get('href'): self.links.append(a['href'])
        elif tag == 'script' and 'ld+json' in a.get('type', '').lower(): self._in = 'ld'; self._buf = []
        elif tag in ('title', 'h1'): self._in = tag; self._buf = []
        elif tag == 'img':
            src = a.get('src') or a.get('data-src') or a.get('data-lazy-src') or a.get('data-original') or ''
            if not src and a.get('srcset'): src = a['srcset'].split(',')[0].strip().split(' ')[0]
            if src and len(self.imgs) < 400: self.imgs.append({'src': src, 'hint': ' '.join([a.get('class', ''), a.get('id', ''), a.get('alt', '')]).lower(), 'width': a.get('width', '')})

    def handle_data(self, data):
        if self._in: self._buf.append(data)

    def handle_endtag(self, tag):
        if self._in == 'ld' and tag == 'script':
            try: self.ld.append(json.loads(''.join(self._buf)))
            except ValueError: pass
            self._in = None
        elif self._in == tag:
            text = ' '.join(''.join(self._buf).split())
            if tag == 'title' and not self.title: self.title = text
            elif tag == 'h1' and text: self.h1.append(text)
            self._in = None

def _walk(node):
    if isinstance(node, list):
        for item in node: yield from _walk(item)
    elif isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key != '@context': yield from _walk(value)

def _is_product(node):
    kind = node.get('@type')
    kinds = kind if isinstance(kind, list) else [kind]
    return any(isinstance(k, str) and k in ('Product', 'ProductGroup', 'IndividualProduct') for k in kinds)

def _image_urls(value):
    if isinstance(value, str): return [value]
    if isinstance(value, dict): return _image_urls(value.get('url') or value.get('contentUrl'))
    if isinstance(value, list): return [u for item in value for u in _image_urls(item)]
    return []

def parse_page(html):
    parser = PageParser()
    try: parser.feed(html)
    except Exception: pass  # malformed HTML: keep what was parsed
    return parser

def extract_candidates(parser, base_url):
    """Ordered (kind, absolute_url) candidates: JSON-LD Product.image, og:image, twitter/image_src, gallery img."""
    out = []
    def add(kind, url):
        if not url or url.startswith('data:'): return
        absolute = urljoin(base_url, url.strip())
        if absolute.startswith('https://') and all(absolute != u for _, u in out): out.append((kind, absolute))
    for node in _walk(parser.ld):
        if isinstance(node, dict) and _is_product(node):
            for url in _image_urls(node.get('image')): add('jsonld', url)
    for key in ('og:image:secure_url', 'og:image', 'og:image:url'):
        for url in parser.meta.get(key, []): add('og', url)
    for key in ('twitter:image', 'twitter:image:src'):
        for url in parser.meta.get(key, []): add('twitter', url)
    for url in parser.links: add('link', url)
    gallery = [i for i in parser.imgs if re.search(r'product|gallery|main|hero|zoom|slide', i['hint'])]
    for img in gallery[:6]:
        if img['width'].isdigit() and int(img['width']) < 150: continue
        add('gallery', img['src'])
    return out

BAD_URL = re.compile(r'(?<![a-z])(logo|favicon|sprite|icon|avatar|pixel|spacer|loader|payment|flag)s?(?![a-z])|no-?image|placeholder|(^|/)(default|blank)[-_.]|og-?default|share-?default|\.(svg|gif|ico)(\?|$)', re.I)
def bad_image_url(url):
    return bool(BAD_URL.search(urlparse(url).path.lower()))

def load_brand_domains(root=ROOT):
    path = Path(root)/'data/brand-domains.json'
    try: raw = json.loads(path.read_text())
    except (OSError, ValueError): return {}
    return {k: v for k, v in raw.items() if not k.startswith('_')}

def host_official(image_url, page_url, brand, brand_domains=None):
    """(ok, host): same registrable domain as the page, a documented brand alias, or a known CDN of that domain."""
    host = (urlparse(image_url).hostname or '').lower()
    reg, page_reg = registrable(host), registrable(urlparse(page_url).hostname)
    allowed = {page_reg, *KNOWN_CDNS.get(page_reg, []), *(brand_domains or {}).get(norm_brand(brand), [])}
    return reg in {registrable(d) for d in allowed}, host

# ---------- exact-model decision ----------
SPEC_TOKEN = re.compile(r'^(\d+(gb|tb|mb|mhz|mt|w|mm|hz|v|c)|ddr\d|cl\d+|gen\d|pcie|x\d+|\d{1,2})$')
VARIANT_NOISE = {'black', 'white', 'negro', 'negra', 'blanco', 'blanca', 'gris', 'grey', 'gray', 'silver', 'plata', 'rojo', 'red',
                 'kit', 'de', 'la', 'el', 'y', 'with', 'con', 'the', 'and', 'dual', 'channel', 'gb', 'tb', 'mhz', 'ddr4', 'ddr5', 'memory', 'memoria'}

def model_tokens(product):
    brand = set(tokens(product.get('brand', '')))
    out = []
    for t in tokens(product.get('name', '')):
        if t in brand or t in VARIANT_NOISE or SPEC_TOKEN.match(t): continue
        if t not in out: out.append(t)
    return out

def page_is_exact_model(product, parser, final_url):
    """True only when MPN or every distinctive model token of the product name appears in the page's
    title/h1/og:title/JSON-LD name/URL. Anything weaker is a family image."""
    names = [parser.title, *parser.h1, *parser.meta.get('og:title', [])]
    skus = []
    for node in _walk(parser.ld):
        if isinstance(node, dict) and _is_product(node):
            names.append(str(node.get('name', '')))
            skus += [str(node.get(k, '')) for k in ('sku', 'mpn')]
    path = urlparse(final_url).path
    if path.lower().endswith('.pdf') or '/qvl' in path.lower(): return False
    headline = ' '.join([*names, path])
    compact = re.sub(r'[^a-z0-9]', '', fold(headline))
    mpn = re.sub(r'[^a-z0-9]', '', fold(product.get('mpn') or ''))
    if len(mpn) >= 5 and (mpn in compact or any(re.sub(r'[^a-z0-9]', '', fold(s)) == mpn for s in skus)): return True
    need = model_tokens(product)
    if len(need) < 2: return False
    have = set(tokens(headline))
    return all(t in have or t in compact for t in need)

# ---------- challenge / fetch ----------
CHALLENGE_MARKERS = ('incapsula', 'captcha', 'just a moment', 'attention required', 'access denied', 'cf-chl', 'px-captcha', 'datadome')
def is_challenge(status, text):
    head = (text or '')[:20000].lower()
    return status in (403, 429, 503) or (len(text or '') < 30000 and any(m in head for m in CHALLENGE_MARKERS))

class Throttle:
    def __init__(self, delay): self.delay = max(2.0, delay); self.last = 0.0
    def wait(self):
        gap = self.last + self.delay + random.uniform(0, 0.4) - time.monotonic()
        if gap > 0: time.sleep(gap)
        self.last = time.monotonic()

class Fetcher:
    def __init__(self, delay):
        from curl_cffi import requests
        self.session = requests.Session(impersonate='chrome'); self.throttle = Throttle(delay)
    def get(self, url, binary=False):
        self.throttle.wait()
        # Images: no AVIF (not decodable everywhere); servers using content negotiation then send WebP/JPEG/PNG.
        headers = {'Accept': 'image/webp,image/jpeg,image/png,image/*;q=0.5,*/*;q=0.1'} if binary else None
        r = self.session.get(url, timeout=30, allow_redirects=True, max_redirects=5, headers=headers)
        body = r.content if binary else r.text
        return r.status_code, (r.headers.get('content-type') or '').lower(), body, str(r.url)

class Robots:
    def __init__(self, fetcher): self.fetcher, self.cache = fetcher, {}
    def allowed(self, url):
        from urllib.robotparser import RobotFileParser
        p = urlparse(url); key = f'{p.scheme}://{p.netloc}'
        if key not in self.cache:
            rp = RobotFileParser()
            try:
                status, _, text, _ = self.fetcher.get(key + '/robots.txt')
                valid = status == 200 and '<html' not in text[:500].lower()
                rp.parse(text.splitlines() if valid else [])  # no/invalid robots.txt: allow
            except Exception: rp.parse([])
            self.cache[key] = rp
        return self.cache[key].can_fetch('*', url)

# ---------- image conversion ----------
def to_webp(data):
    """bytes -> (webp_bytes, width, height). Raises ValueError on unusable images."""
    from PIL import Image, ImageOps
    try:
        img = Image.open(io.BytesIO(data)); img.load()
    except Exception as exc: raise ValueError(f'unreadable image: {exc}')
    img = ImageOps.exif_transpose(img)
    if max(img.size) < MIN_SIDE: raise ValueError('image_too_small')
    if not 0.4 <= img.width / img.height <= 2.2: raise ValueError('bad_aspect: banner-like image')
    if img.mode in ('P', 'LA', 'PA'): img = img.convert('RGBA')
    elif img.mode not in ('RGB', 'RGBA'): img = img.convert('RGB')
    if img.mode == 'RGBA' and img.getchannel('A').getextrema()[0] == 255: img = img.convert('RGB')
    scale = min(1.0, MAX_SIDE / max(img.size))
    for shrink in (1.0, 0.85, 0.72, 0.6):
        size = (max(1, round(img.width*scale*shrink)), max(1, round(img.height*scale*shrink)))
        work = img.resize(size, Image.LANCZOS) if size != img.size else img
        for quality in (82, 74, 66, 58, 50):
            buf = io.BytesIO(); work.save(buf, 'WEBP', quality=quality, method=6)
            if buf.tell() <= TARGET_BYTES: return buf.getvalue(), work.width, work.height
    return buf.getvalue(), work.width, work.height  # best effort: smallest attempt

# ---------- state ----------
def now(): return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def atomic_write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp'); tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + '\n'); tmp.replace(path)

def load_state(root):
    path = Path(root)/STATE_DIR/'manufacturer-progress.json'
    if path.is_file(): return json.loads(path.read_text())
    return {'version': 1, 'date': datetime.now(timezone.utc).strftime('%Y-%m-%d'), 'products': {}, 'domains': {}}

def save_state(root, state): atomic_write(Path(root)/STATE_DIR/'manufacturer-progress.json', state)

def load_products(root):
    seen, out = set(), []
    for f in sorted((Path(root)/'data/catalog').glob('*.json')):
        for p in json.loads(f.read_text()):
            if p['id'] not in seen: seen.add(p['id']); out.append(p)
    return out

def existing_image_ids(root):
    ids = set()
    for f in (Path(root)/'data/images').glob('*.json'):
        if f.name.startswith('manufacturer-'): continue
        ids |= {i['product_id'] for i in json.loads(f.read_text())}
    return ids

def pending_products(products, have_images, state, args):
    out = []
    for p in products:
        if p['id'] in have_images or not (p.get('manufacturer_url') or '').startswith('https://'): continue
        if args.ids and p['id'] not in args.ids: continue
        if args.brand and norm_brand(p['brand']) != norm_brand(args.brand): continue
        if args.category and p['category_id'] != args.category: continue
        entry = state['products'].get(p['id'])
        if entry:
            if entry['status'] == 'ok': continue
            if entry['status'] == 'skip' and not args.retry_skipped: continue
            if entry['status'] in ('blocked', 'error') and entry.get('attempts', 1) >= MAX_ATTEMPTS and not args.retry_failed: continue
        if state['domains'].get(registrable(urlparse(p['manufacturer_url']).hostname), {}).get('stopped'): continue
        out.append(p)
    # round-robin across domains so no single site is hit back-to-back
    buckets = {}
    for p in out: buckets.setdefault(registrable(urlparse(p['manufacturer_url']).hostname), []).append(p)
    order = []
    while any(buckets.values()):
        for key in sorted(buckets):
            if buckets[key]: order.append(buckets[key].pop(0))
    return order

# ---------- per-product processing ----------
class Blocked(Exception): pass

def process(product, fetcher, robots, brand_domains, root, dry_run):
    """Returns a state entry dict. Raises Blocked for challenges/403/429/timeouts (counted per domain)."""
    page_url = product['manufacturer_url']
    if urlparse(page_url).path.lower().endswith(('.pdf', '.zip')): return {'status': 'skip', 'reason': 'non_html', 'content_type': 'pdf/zip url'}
    if not robots.allowed(page_url): return {'status': 'skip', 'reason': 'robots_disallow'}
    try: status, ctype, text, final_url = fetcher.get(page_url)
    except Exception as exc: raise Blocked(f'request failed: {type(exc).__name__}')
    if is_challenge(status, text): raise Blocked(f'challenge/status {status}')
    if status in (404, 410): return {'status': 'skip', 'reason': 'not_found', 'http': status}
    if status >= 500: return {'status': 'error', 'reason': f'http_{status}'}
    if status != 200 or 'html' not in ctype: return {'status': 'skip', 'reason': 'non_html', 'http': status, 'content_type': ctype}
    parser = parse_page(text)
    cands = extract_candidates(parser, final_url)
    if not cands: return {'status': 'skip', 'reason': 'no_image'}
    chosen, saw_foreign = None, False
    for kind, url in cands:
        if bad_image_url(url): continue
        ok, host = host_official(url, final_url, product['brand'], brand_domains)
        if not ok: saw_foreign = True; continue
        chosen = (kind, url, host); break
    if not chosen: return {'status': 'skip', 'reason': 'image_host_not_official' if saw_foreign else 'no_image'}
    kind, image_url, host = chosen
    scope = 'model' if page_is_exact_model(product, parser, final_url) else 'family'
    result = {'page_url': page_url, 'final_url': final_url, 'image_url': image_url, 'image_host': host, 'source': kind, 'scope': scope}
    if dry_run: return {'status': 'dry-run', **result}
    try: istatus, ictype, data, _ = fetcher.get(image_url, binary=True)
    except Exception as exc: raise Blocked(f'image request failed: {type(exc).__name__}')
    if istatus in (403, 429): raise Blocked(f'image status {istatus}')
    if istatus != 200 or not ictype.startswith('image/') or 'svg' in ictype or 'gif' in ictype or len(data) > MAX_DOWNLOAD:
        return {'status': 'skip', 'reason': 'bad_image', 'http': istatus, 'content_type': ictype, **result}
    try: webp, w, h = to_webp(data)
    except ValueError as exc:
        return {'status': 'skip', 'reason': 'image_too_small' if 'too_small' in str(exc) else 'bad_image', **result}
    rel = f"images/products/{product['category_id']}/{product['id']}.webp"
    dest = Path(root)/'apps/web/public'/rel; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(webp)
    brand = product['brand']
    attribution = f"{brand} / PcComponentes" if registrable(urlparse(page_url).hostname) == 'pccomponentes.com' else brand
    return {'status': 'ok', **result, 'image_path': '/' + rel, 'bytes': len(webp), 'width': w, 'height': h,
            'sha256': hashlib.sha256(webp).hexdigest(), 'attribution': attribution, 'fetched_at': now()}

def build_manifest(state):
    """Provenance records for accepted products; images reused across >=3 different pages are generic (dropped)."""
    ok = {pid: e for pid, e in state['products'].items() if e['status'] == 'ok'}
    pages = {}
    for e in ok.values(): pages.setdefault(e['image_url'], set()).add(e['page_url'])
    records = []
    for pid in sorted(ok):
        e = ok[pid]
        if len(pages[e['image_url']]) >= 3: continue
        records.append({'product_id': pid, 'image_path': e['image_path'], 'image_source_url': e['image_url'],
                        'image_page_url': e['page_url'], 'image_scope': e['scope'], 'attribution': e['attribution'],
                        'fetched_at': e['fetched_at'], 'image_host': e['image_host'],
                        'rights': 'Imagen del fabricante usada para identificar el producto; derechos reservados, sin licencia abierta asumida.'})
    return records

def reconcile(root, state):
    """Mark generic images as skipped and delete their files; rewrite staged manifest."""
    keep = {r['product_id'] for r in build_manifest(state)}
    for pid, e in state['products'].items():
        if e['status'] == 'ok' and pid not in keep:
            (Path(root)/'apps/web/public'/e['image_path'].lstrip('/')).unlink(missing_ok=True)
            state['products'][pid] = {'status': 'skip', 'reason': 'generic_image', 'image_url': e['image_url'], 'page_url': e['page_url']}
    atomic_write(Path(root)/STATE_DIR/f"manufacturer-{state['date']}.json", build_manifest(state))

def summarize(state, total_pending=None, started=None, done_run=0):
    counts = {}
    for e in state['products'].values():
        key = 'ok' if e['status'] == 'ok' else f"{e['status']}:{e.get('reason','')}"
        counts[key] = counts.get(key, 0) + 1
    out = {'updated_at': now(), 'processed_total': len(state['products']), 'counts': dict(sorted(counts.items())),
           'domains': {d: v for d, v in state['domains'].items() if v.get('stopped') or v.get('total_blocks')}}
    if total_pending is not None and started and done_run:
        rate = (time.monotonic() - started) / done_run
        out.update(run_done=done_run, run_total=total_pending, eta_utc=datetime.fromtimestamp(time.time() + rate*(total_pending-done_run), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    return out

def finalize(root, state):
    reconcile(root, state)
    src = Path(root)/STATE_DIR/f"manufacturer-{state['date']}.json"
    dest = Path(root)/f"data/images/manufacturer-{state['date']}.json"
    atomic_write(dest, json.loads(src.read_text()))
    from importlib import import_module
    sys.path.insert(0, str(Path(root)/'scripts'))
    products = load_products(root)
    import_module('catalog_images').enrich_images(products, root)   # raises on duplicates / invalid records
    return dest, len(json.loads(src.read_text()))

STATE_LOCK = threading.Lock()   # guards state dict, state/summary writes and stdout lines (single logical writer)

def run_domain(dom, items, state, root, args, bd, stop, progress, total, fetcher):
    """One worker per registrable domain: own session/throttle (>=2 s between ITS requests), own robots cache."""
    robots = Robots(fetcher)
    for product in items:
        if stop['now']: return
        with STATE_LOCK:
            dstate = state['domains'].setdefault(dom, {'consecutive_blocks': 0, 'total_blocks': 0, 'stopped': False})
            if dstate['stopped']: return
            prev = dict(state['products'].get(product['id'], {}))
        try:
            entry = process(product, fetcher, robots, bd, root, args.dry_run); blocked = False
        except Blocked as exc:
            entry = {'status': 'blocked', 'reason': str(exc), 'attempts': prev.get('attempts', 0) + 1}; blocked = True
        except Exception as exc:
            entry = {'status': 'error', 'reason': f'{type(exc).__name__}: {exc}'[:200], 'attempts': prev.get('attempts', 0) + 1}; blocked = False
        entry['ts'] = now()
        with STATE_LOCK:
            if blocked:
                dstate['consecutive_blocks'] += 1; dstate['total_blocks'] += 1
                if dstate['consecutive_blocks'] >= DOMAIN_STOP_AFTER:
                    dstate['stopped'] = True
                    if not args.dry_run: dstate['stopped_at'] = now()
                    print(f'!! domain {dom} stopped after {DOMAIN_STOP_AFTER} consecutive blocks', flush=True)
            elif entry['status'] != 'error': dstate['consecutive_blocks'] = 0
            progress['done'] += 1
            print(json.dumps({'id': product['id'], 'domain': dom, **{k: entry.get(k) for k in ('status', 'reason', 'scope', 'image_host', 'bytes', 'width', 'height') if entry.get(k) is not None}}), flush=True)
            if args.dry_run: continue
            state['products'][product['id']] = entry
            save_state(root, state)
            if progress['done'] % 10 == 0: atomic_write(Path(root)/STATE_DIR/'manufacturer-progress-summary.json', summarize(state, total, progress['started'], progress['done']))

def run_all(todo, state, root, args, bd, stop, progress, make_fetcher):
    """Domains run concurrently (at most --workers at a time); each domain's products stay sequential."""
    buckets = {}
    for p in todo: buckets.setdefault(registrable(urlparse(p['manufacturer_url']).hostname), []).append(p)
    errors = []
    def job(dom):
        try: run_domain(dom, buckets[dom], state, root, args, bd, stop, progress, len(todo), make_fetcher())
        except Exception as exc: errors.append((dom, exc))
    # biggest domains first so the long tail does not dominate wall time
    order = sorted(buckets, key=lambda d: -len(buckets[d]))
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool: list(pool.map(job, order))
    if errors: print(f'worker errors: {errors}', flush=True)

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--limit', type=int); ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--delay', type=float, default=2.0, help='seconds between requests (minimum 2)')
    ap.add_argument('--workers', type=int, default=8, help='concurrent domains (each >=2 s between its own requests)')
    ap.add_argument('--retry-skipped', action='store_true'); ap.add_argument('--retry-failed', action='store_true')
    ap.add_argument('--reset-domains', action='store_true'); ap.add_argument('--brand'); ap.add_argument('--category')
    ap.add_argument('--ids', type=lambda s: set(s.split(','))); ap.add_argument('--root', default=str(ROOT))
    ap.add_argument('--finalize', action='store_true', help='copy staged provenance to data/images/ (publisher picks it up)')
    ap.add_argument('--status', action='store_true')
    args = ap.parse_args(argv); root = Path(args.root)
    state = load_state(root)
    if args.reset_domains: state['domains'] = {}
    if args.status: print(json.dumps(summarize(state), indent=1)); return 0
    if args.finalize:
        dest, n = finalize(root, state); save_state(root, state); print(f'finalized {n} records -> {dest}'); return 0
    products = load_products(root)
    todo = pending_products(products, existing_image_ids(root), state, args)
    if args.limit: todo = todo[:args.limit]
    print(f'{len(todo)} products to process (dry_run={args.dry_run})', flush=True)
    bd = load_brand_domains(root)
    stop = {'now': False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    signal.signal(signal.SIGINT, lambda *_: stop.update(now=True))
    progress = {'done': 0, 'started': time.monotonic()}
    try:
        run_all(todo, state, root, args, bd, stop, progress, lambda: Fetcher(args.delay))
    finally:
        if not args.dry_run:
            reconcile(root, state); save_state(root, state)
            atomic_write(root/STATE_DIR/'manufacturer-progress-summary.json', summarize(state, len(todo), progress['started'], progress['done']))
    started, done = progress['started'], progress['done']
    print(json.dumps(summarize(state, len(todo), started, done)), flush=True)
    return 0

if __name__ == '__main__': sys.exit(main())
