"""LDLC Spain discovery and exact-identity offers, restricted to an isolated local DB.

Run inside the sync-agent image with host scripts/nas mounted. No sync or replica
is started. Crawl checkpoints live outside watched production directories.
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'nas'), str(ROOT / 'scripts')]
from retailer_common import MissingListing, exact_product, retry_delay  # noqa: E402,F401
BASE = 'https://www.ldlc.com'
PREFIX = BASE + '/es-es/informatica/piezas-de-informatica/'
CATEGORIES = {
    'gpu': [('tarjeta-grafica', 4684)],
    'motherboard': [('placa-base', 4293)],
    'cpu': [('procesador', 4300)],
    'ram': [('memoria-pc', 4703)],
    'storage': [('disco-ssd', 4698), ('disco-duro-interno', 4697)],
    'psu': [('fuente-de-alimentacion', 4289)],
    'case': [('caja-de-pc', 4678)],
    'cooler': [('ventilador-procesador', 4712), ('watercooling', 4715)],
}


def refresh_session_started_at(state, now=None):
    """Return the active refresh cutoff, starting a new session after completion."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = state.get('refresh_started_at')
    if cutoff and state.get('status') != 'complete':
        try:
            return datetime.fromisoformat(cutoff.replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()
        except (AttributeError, TypeError, ValueError):
            pass
    return now.astimezone(timezone.utc).isoformat()


def snapshot_is_current(path, cutoff, timestamp_key='last_checked_at'):
    """Only reuse a cached observation created during this refresh session."""
    try:
        value = json.loads(path.read_text()).get(timestamp_key)
        if not value:
            return False
        observed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        started = datetime.fromisoformat(str(cutoff).replace('Z', '+00:00'))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return observed.astimezone(timezone.utc) >= started.astimezone(timezone.utc)
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return False


class Structured(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.active = False
        self.buffer = []
        self.documents = []
        self.next = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('type') == 'application/ld+json':
            self.active = True
            self.buffer = []
        if tag == 'link' and attrs.get('rel') == 'next':
            self.next = urljoin(BASE, attrs['href'])

    def handle_data(self, data):
        if self.active:
            self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.active = False
            value = json.loads(''.join(self.buffer))
            self.documents.extend(value if isinstance(value, list) else [value])


def product_url(url):
    return bool(re.fullmatch(r'https://www\.ldlc\.com/es-es/ficha/PB\d+\.html', url))


def inventory_links(html):
    documents = Structured(html).documents
    lists = [d for d in documents if d.get('@type') == 'ItemList']
    links = [item['item']['url'] for listing in lists for item in listing['itemListElement']]
    # Later pages omit JSON-LD; exclude recommendation carousels.
    if not lists:
        cards = re.findall(r'<li\b[^>]*class="pdt-item"[^>]*>(.*?)</li>', html, re.S)
        links = [urljoin(BASE, link) for card in cards
                 for link in re.findall(r'href="(/es-es/ficha/PB\d+\.html)"', card)]
    links = list(dict.fromkeys(link for link in links if product_url(link)))
    if not links:
        raise ValueError('Missing category inventory')
    return links


def candidate(html, url, category, checked):
    products = [d for d in Structured(html).documents if d.get('@type') == 'Product']
    if len(products) != 1 or not product_url(url):
        raise ValueError('Expected one Spanish product')
    p = products[0]
    offer = p.get('offers') or {}
    if not isinstance(offer, dict) or offer.get('priceCurrency') != 'EUR':
        raise ValueError('Expected one EUR offer')
    if offer.get('url') != url or offer.get('seller', {}).get('name') != 'LDLC':
        raise ValueError('Unverified URL or direct seller')
    if offer.get('itemCondition') != 'https://schema.org/NewCondition':
        raise ValueError('Not a new product')
    availability = offer.get('availability')
    states = {'https://schema.org/InStock': True, 'https://schema.org/OutOfStock': False,
              'https://schema.org/PreOrder': False, 'https://schema.org/BackOrder': False,
              'https://schema.org/Discontinued': False, 'https://schema.org/SoldOut': False}
    if availability not in states:
        raise ValueError('Unknown availability')
    price = Decimal(str(offer['price'])) * 100
    if not price.is_finite() or price != price.to_integral_value() or not 0 < price <= 100000000:
        raise ValueError('Invalid price')
    brand = p.get('brand', {})
    return dict(shop_id='ldlc', retailer_sku=re.search(r'PB\d+', url)[0], url_public=url,
                name=p['name'], brand=brand.get('name') if isinstance(brand, dict) else brand,
                mpn=p.get('mpn'), ean=p.get('gtin13'), category_hint=category,
                price_cents=int(price), in_stock=states[availability], last_checked_at=checked,
                missing_fields=['manufacturer_url', 'verified_specifications'],
                commercial_observation_source='LDLC Spanish product JSON-LD, direct seller',
                raw_properties=[{'key': 'ldlc_sku', 'value': p.get('sku')}])


def run(args):
    import psycopg
    from curl_cffi import requests
    from catalog import write
    from review_queue import stage_candidates, approve
    # No production DB name, and never inherit DATABASE_URL from the live service.
    if urlparse(args.database_url).path != '/stockfinder_ldlc_local':
        raise SystemExit('Only the isolated stockfinder_ldlc_local database is allowed')
    args.output.mkdir(parents=True, exist_ok=True)
    state_file = args.output / 'run-state.json'
    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state.get('retry_after_epoch', 0) > time.time():
            raise SystemExit('LDLC cooldown is active; retained checkpoints have not changed')
    refresh_cutoff = None
    if args.refresh:
        refresh_cutoff = refresh_session_started_at(state)
        state['refresh_started_at'] = refresh_cutoff
        state.pop('refresh_completed_at', None)
    request_delay = max(2, float(state.get('request_delay_seconds', 2)))
    if state.get('http_status') in (403, 429, 503):
        request_delay = max(4, request_delay)
    stats = dict(pages=0, discovered=0, checked=0, approved=0, pending=0, rejected_observations=0)

    def save_state(status, **extra):
        state.update(status=status, updated_at=datetime.now(timezone.utc).isoformat(), request_delay_seconds=request_delay, **stats, **extra)
        if args.refresh and status == 'complete':
            state['refresh_completed_at'] = datetime.now(timezone.utc).isoformat()
        temporary = state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(state_file)

    session = requests.Session(impersonate='chrome')
    last_request = 0

    def fetch(url):
        nonlocal last_request, request_delay
        parsed = urlparse(url)
        if parsed.hostname != 'www.ldlc.com' or not parsed.path.startswith('/es-es/') or parsed.scheme != 'https':
            raise ValueError('Unexpected crawl origin')
        time.sleep(max(0, request_delay - (time.monotonic() - last_request)))
        last_request = time.monotonic()
        r = session.get(url, timeout=45, allow_redirects=False)
        if r.status_code in (404, 410) and product_url(url):
            raise MissingListing(r.status_code, url)
        if r.status_code != 200:
            (args.output / 'last-http-failure.html').write_text(r.text[:250000])
            blocked = r.status_code in (403, 429, 503)
            if blocked:
                request_delay = min(12, request_delay * 2)
            save_state('blocked' if blocked else 'http_error', failed_url=url, http_status=r.status_code,
                       retry_after_epoch=time.time() + retry_delay(r.headers.get('Retry-After')) if blocked else 0)
            raise RuntimeError(f'HTTP {r.status_code}: {url}; crawl stopped')
        if 'Just a moment' in r.text or 'captcha' in r.text.lower() and '<title>captcha' in r.text.lower():
            save_state('blocked', failed_url=url, retry_after_epoch=time.time() + 900)
            raise RuntimeError('Challenge; crawl stopped')
        return r.text

    save_state('running')
    with psycopg.connect(args.database_url) as conn:
        write(conn, 'shop', 'ldlc', dict(name='LDLC', url_public=BASE + '/es-es/'))
        conn.commit()
        for category in args.categories or CATEGORIES:
            inventory_file = args.output / ('inventory-' + category + '.json')
            inventory_fresh = args.refresh and refresh_cutoff and snapshot_is_current(inventory_file, refresh_cutoff, 'collected_at')
            if inventory_file.exists() and (not args.refresh or inventory_fresh):
                saved = json.loads(inventory_file.read_text())
                urls = dict.fromkeys(saved['urls'])
                category_pages = saved['pages']
            else:
                urls = {}
                category_pages = 0
                for slug, code in CATEGORIES[category]:
                    url = f'{PREFIX}{slug}/c{code}/'
                    visited = set()
                    while url:
                        if url in visited:
                            raise RuntimeError('Pagination loop')
                        visited.add(url)
                        html = fetch(url)
                        doc = Structured(html)
                        for link in inventory_links(html):
                            urls[link] = None
                        category_pages += 1
                        print(json.dumps(dict(event='ldlc_page', category=category, page=len(visited), products=len(urls))), flush=True)
                        url = doc.next
                inventory_file.write_text(json.dumps(dict(urls=list(urls), pages=category_pages,
                                                          collected_at=datetime.now(timezone.utc).isoformat()), indent=2) + '\n')
            stats['pages'] += category_pages
            stats['discovered'] += len(urls)
            for url in urls:
                cache = args.output / (re.search(r'PB\d+', url)[0] + '.json')
                cache_fresh = args.refresh and refresh_cutoff and snapshot_is_current(cache, refresh_cutoff)
                if cache.exists() and (not args.refresh or cache_fresh):
                    row = json.loads(cache.read_text())
                    row['category_hint'] = category
                else:
                    checked = datetime.now(timezone.utc).isoformat()
                    try:
                        html = fetch(url)
                    except MissingListing as error:
                        stats['rejected_observations'] += 1
                        print(json.dumps(dict(event='ldlc_listing_absent', url=url, status=error.status)), flush=True)
                        continue
                    try:
                        row = candidate(html, url, category, checked)
                    except (ValueError, KeyError, TypeError, InvalidOperation) as error:
                        stats['rejected_observations'] += 1
                        print(json.dumps(dict(event='ldlc_rejected', url=url, reason=str(error))), flush=True)
                        continue
                    cache.write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n')
                with conn.transaction():
                    ids = stage_candidates(conn, [row])
                    pid = exact_product(conn, row)
                    if pid:
                        if ids:
                            approve(conn, ids[0], 'LDLC: exact verified MPN/EAN, matching category and direct EUR offer from the Spanish product page.', pid)
                        stats['approved'] += 1
                    else:
                        stats['pending'] += 1
                conn.commit()
                stats['checked'] += 1
                if stats['checked'] % 25 == 0:
                    save_state('running', category=category)
                    print(json.dumps(dict(event='ldlc_progress', **stats)), flush=True)
            save_state('running', category=category)
            print(json.dumps(dict(event='ldlc_category_done', category=category, **stats)), flush=True)
        (args.output / 'summary.json').write_text(json.dumps(stats, indent=2) + '\n')
    save_state('complete')
    print(json.dumps(dict(event='ldlc_done', **stats)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--categories', nargs='+', choices=CATEGORIES)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--watch', action='store_true', help='Resume automatically after transient failures')
    args = parser.parse_args()
    if not args.watch:
        run(args)
    else:
        import fcntl
        args.output.mkdir(parents=True, exist_ok=True)
        lock = (args.output / '.crawl.lock').open('w')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            state_file = args.output / 'run-state.json'
            state = json.loads(state_file.read_text()) if state_file.exists() else {}
            remaining = state.get('retry_after_epoch', 0) - time.time()
            if remaining > 0:
                print(json.dumps(dict(event='ldlc_cooldown', seconds=round(remaining))), flush=True)
                time.sleep(min(55, remaining))
                continue
            try:
                run(args)
                break
            except Exception as error:
                from curl_cffi.requests.exceptions import RequestException
                if not isinstance(error, (RuntimeError, RequestException)):
                    raise
                print(json.dumps(dict(event='ldlc_retry', error_type=type(error).__name__)), flush=True)
                # HTTP failures already have a persisted cooldown. Network failures
                # get one minute before the normal checkpoint-based resume.
                time.sleep(55)
