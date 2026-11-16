"""Neobyte discovery and exact-identity offers, restricted to an isolated local DB.

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
BASE = 'https://www.neobyte.es'
DATABASE = '/stockfinder_neobyte_local'
CATEGORIES = {
    'gpu': [('tarjetas-graficas', 111)],
    'motherboard': [('placas-base', 106)],
    'cpu': [('procesadores', 107)],
    'ram': [('memorias-ram', 108)],
    'storage': [('discos-duros-ssd', 144), ('discos-duros-3-5-sata', 142), ('discos-duros-para-portatiles', 143)],
    'psu': [('fuentes-de-alimentacion', 113)],
    'case': [('torres', 153)],
    'cooler': [('ventiladores-cpu', 138), ('refrigeracion-liquida', 139)],
}


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
            for document in value if isinstance(value, list) else [value]:
                self.documents.extend(document['@graph'] if isinstance(document, dict) and '@graph' in document else [document])


def product_url(url):
    return bool(re.fullmatch(r'https://www\.neobyte\.es/[a-z0-9][a-z0-9-]*-\d+\.html', url))


def product_id(url):
    return re.search(r'-(\d+)\.html$', url)[1]


def inventory_links(html):
    documents = Structured(html).documents
    lists = [d for d in documents if d.get('@type') == 'ItemList']
    links = [item['url'] for listing in lists for item in listing['itemListElement']]
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
    if offer.get('url') != url or offer.get('seller', {}).get('name') != 'Neobyte':
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
    return dict(shop_id='neobyte', retailer_sku=product_id(url), url_public=url,
                name=p['name'], brand=brand.get('name') if isinstance(brand, dict) else brand,
                mpn=p.get('mpn'), ean=p.get('gtin13'), category_hint=category,
                price_cents=int(price), in_stock=states[availability], last_checked_at=checked,
                missing_fields=['manufacturer_url', 'verified_specifications'],
                commercial_observation_source='Neobyte product JSON-LD, direct seller',
                raw_properties=[{'key': 'neobyte_sku', 'value': p.get('sku')}])


def run(args):
    # No production DB name, and never inherit DATABASE_URL from the live service.
    # Neobyte joined production on 2026-11-16 (user decision). The isolated copy stays allowed.
    if urlparse(args.database_url).path not in (DATABASE, '/stockfinder'):
        raise SystemExit('Only stockfinder or the isolated stockfinder_neobyte_local database is allowed')
    import psycopg
    from catalog import write
    from review_queue import stage_candidates, approve
    from retailer_transport import browser_session
    args.output.mkdir(parents=True, exist_ok=True)
    state_file = args.output / 'run-state.json'
    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text())
        if state.get('retry_after_epoch', 0) > time.time():
            raise SystemExit('Neobyte cooldown is active; retained checkpoints have not changed')
    request_delay = max(2, float(state.get('request_delay_seconds', 2)))
    if state.get('http_status') in (403, 429, 503):
        request_delay = max(4, request_delay)
    stats = dict(pages=0, discovered=0, checked=0, approved=0, pending=0, rejected_observations=0)

    def save_state(status, **extra):
        state = dict(status=status, updated_at=datetime.now(timezone.utc).isoformat(), request_delay_seconds=request_delay, **stats, **extra)
        temporary = state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(state_file)

    session = browser_session('neobyte')
    last_request = 0

    def fetch(url):
        nonlocal last_request, request_delay
        parsed = urlparse(url)
        if parsed.hostname != 'www.neobyte.es' or parsed.scheme != 'https':
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
        write(conn, 'shop', 'neobyte', dict(name='Neobyte', url_public=BASE + '/'))
        conn.commit()
        for category in args.categories or CATEGORIES:
            inventory_file = args.output / ('inventory-' + category + '.json')
            if inventory_file.exists() and not (args.refresh or args.refresh_inventory):
                saved = json.loads(inventory_file.read_text())
                urls = dict.fromkeys(saved['urls'])
                category_pages = saved['pages']
            else:
                urls = {}
                category_pages = 0
                for slug, code in CATEGORIES[category]:
                    url = f'{BASE}/{slug}-{code}'
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
                        print(json.dumps(dict(event='neobyte_page', category=category, page=len(visited), products=len(urls))), flush=True)
                        url = doc.next
                inventory_file.write_text(json.dumps(dict(urls=list(urls), pages=category_pages,
                                                          collected_at=datetime.now(timezone.utc).isoformat()), indent=2) + '\n')
            stats['pages'] += category_pages
            stats['discovered'] += len(urls)
            for url in urls:
                cache = args.output / (product_id(url) + '.json')
                if cache.exists() and not args.refresh:
                    row = json.loads(cache.read_text())
                    row['category_hint'] = category
                else:
                    checked = datetime.now(timezone.utc).isoformat()
                    try:
                        html = fetch(url)
                    except MissingListing as error:
                        stats['rejected_observations'] += 1
                        print(json.dumps(dict(event='neobyte_listing_absent', url=url, status=error.status)), flush=True)
                        continue
                    try:
                        row = candidate(html, url, category, checked)
                    except (ValueError, KeyError, TypeError, InvalidOperation) as error:
                        stats['rejected_observations'] += 1
                        print(json.dumps(dict(event='neobyte_rejected', url=url, reason=str(error))), flush=True)
                        continue
                    cache.write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n')
                with conn.transaction():
                    ids = stage_candidates(conn, [row])
                    pid = exact_product(conn, row)
                    approved = False
                    if pid:
                        try:
                            if ids:
                                approve(conn, ids[0], 'Neobyte: exact verified MPN/EAN, matching category and direct EUR offer from the product page.', pid)
                            approved = True
                        except ValueError as error:
                            # Real identity blocks (variant conflicts) stay pending; they must not abort the crawl.
                            print(json.dumps(dict(event='neobyte_left_pending', url=url, reason=str(error))), flush=True)
                    if approved:
                        stats['approved'] += 1
                    else:
                        stats['pending'] += 1
                conn.commit()
                stats['checked'] += 1
                if stats['checked'] % 25 == 0:
                    save_state('running', category=category)
                    print(json.dumps(dict(event='neobyte_progress', **stats)), flush=True)
            save_state('running', category=category)
            print(json.dumps(dict(event='neobyte_category_done', category=category, **stats)), flush=True)
        (args.output / 'summary.json').write_text(json.dumps(stats, indent=2) + '\n')
    save_state('complete')
    print(json.dumps(dict(event='neobyte_done', **stats)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--categories', nargs='+', choices=CATEGORIES)
    parser.add_argument('--refresh', action='store_true', help='Re-read the category listings and every product page')
    parser.add_argument('--refresh-inventory', action='store_true',
                        help='Re-read the category listings to discover new products; only new product pages are fetched '
                             '(price and stock of known ones come from the listing refresh)')
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
                print(json.dumps(dict(event='neobyte_cooldown', seconds=round(remaining))), flush=True)
                time.sleep(min(55, remaining))
                continue
            try:
                run(args)
                break
            except Exception as error:
                from curl_cffi.requests.exceptions import RequestException
                if not isinstance(error, (RuntimeError, RequestException)):
                    raise
                print(json.dumps(dict(event='neobyte_retry', error_type=type(error).__name__)), flush=True)
                # HTTP failures already have a persisted cooldown. Network failures
                # get one minute before the normal checkpoint-based resume.
                time.sleep(55)
