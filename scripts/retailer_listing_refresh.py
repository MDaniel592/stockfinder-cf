"""Fast price/stock refresh for LDLC and Neobyte from category listing pages.

The full crawlers (ldlc_local.py, neobyte_local.py) fetch every product page. For
products already known (cached row with identity) the listing pages already carry
price and stock, so one request refreshes a whole page of products:

* LDLC embeds price and stock for each card in inline <script> blocks at the end of
  the listing HTML (`data-offer-id`, `<div class="price">110€<sup>95</sup>`,
  `stock stock-1` = in stock, `stock-9` = sold out).
* Neobyte cards carry `<span class="product-price" content="588.99">` and show
  "Añadir al carrito" when in stock, "Más detalles" when not.

Anything not trusted from the listing (new product, unknown stock code, missing or
unparseable price, marketplace seller, price jump >2x) falls back to the product page
and the existing JSON-LD `candidate()` parser. Identity, review queue and offer
import go through the same stage_candidates/approve code as the full crawlers, only
against the isolated local databases. Never touches production.

Run inside the sync-agent image (see retailer_listing_refresh.sh).
"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import importlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'nas'), str(ROOT / 'scripts')]

RETAILERS = {
    'ldlc': dict(module='ldlc_local', host='www.ldlc.com', database='/stockfinder_ldlc_local'),
    'neobyte': dict(module='neobyte_local', host='www.neobyte.es', database='/stockfinder_neobyte_local'),
}
STATE_NAME = 'listing-refresh-state.json'
SOURCE_NOTE = ' | price/stock refreshed from category listing'


# ----------------------------------------------------------------- listing parsers

def cents(value):
    value = Decimal(str(value)) * 100
    if not value.is_finite() or value != value.to_integral_value() or not 0 < value <= 100000000:
        raise ValueError('Invalid price')
    return int(value)


LDLC_MARKER = re.compile(r"""querySelectorAll\('\[id\*="pdt"\]\[data-offer-id="([A-Za-z0-9]+)"\]'\)""")
LDLC_PRICE = re.compile(r"""\.price'\)\.outerHTML\s*=\s*'<div class="price">(?:<div class="price">)?([^<]*?)€<sup>(\d{2})</sup>""")
LDLC_STOCK = re.compile(r'data-stock-web="(\d+)"')
LDLC_STOCK_CODES = {'1': True, '9': False}


def ldlc_scripts(html):
    """offer id -> dict(price_cents, in_stock) from the inline per-product scripts."""
    parts = LDLC_MARKER.split(html)
    found = {}
    for offer, body in zip(parts[1::2], parts[2::2]):
        if offer in found:
            continue
        body = body[:6000]
        data = dict(price_cents=None, in_stock=None)
        price = LDLC_PRICE.search(body)
        if price:
            whole = re.sub(r'\D', '', price.group(1).replace('&nbsp;', ''))
            if whole:
                try:
                    data['price_cents'] = cents(f"{int(whole)}.{price.group(2).ljust(2, '0')}")
                except (ValueError, InvalidOperation):
                    pass
        stock = LDLC_STOCK.search(body)
        if stock:
            data['in_stock'] = LDLC_STOCK_CODES.get(stock.group(1))
        found[offer] = data
    return found


def ldlc_cards(html, module):
    """url -> dict(retailer_sku, price_cents, in_stock) for the cards of this listing page."""
    allowed = set(module.inventory_links(html))
    scripts = ldlc_scripts(html)
    cards = {}
    for segment in re.split(r'(?=<li\b[^>]*class="pdt-item")', html)[1:]:
        segment = segment[:6000]
        link = re.search(r'href="(/es-es/ficha/PB\d+\.html)"', segment)
        offer = re.search(r'data-offer-id="([A-Za-z0-9]+)"', segment)
        if not link or not offer:
            continue
        url = module.BASE + link.group(1)
        if url not in allowed or url in cards:
            continue
        marketplace = re.search(r'data-is-marketplace="(\d)"', segment)
        data = dict(scripts.get(offer.group(1), dict(price_cents=None, in_stock=None)))
        if not marketplace or marketplace.group(1) != '0':
            data = dict(price_cents=None, in_stock=None)  # not verified as direct seller
        cards[url] = dict(retailer_sku=re.search(r'PB\d+', url)[0], **data)
    for url in allowed:
        cards.setdefault(url, dict(retailer_sku=re.search(r'PB\d+', url)[0], price_cents=None, in_stock=None))
    return cards


NEO_ARTICLE = re.compile(r'<article\b([^>]*class="[^"]*product-miniature[^"]*"[^>]*)>(.*?)</article>', re.S)


def neobyte_cards(html, module):
    allowed = set(module.inventory_links(html))
    cards = {}
    for attrs, body in NEO_ARTICLE.findall(html):
        ident = re.search(r'data-id-product="(\d+)"', attrs)
        link = re.search(r'href="(https://www\.neobyte\.es/[a-z0-9][a-z0-9-]*-(\d+)\.html)"', body)
        if not ident or not link or link.group(2) != ident.group(1) or link.group(1) not in allowed:
            continue
        data = dict(price_cents=None, in_stock=None)
        price = re.search(r'<span\b[^>]*class="product-price"[^>]*content="([0-9]+(?:\.[0-9]+)?)"', body)
        if price:
            try:
                data['price_cents'] = cents(price.group(1))
            except (ValueError, InvalidOperation):
                pass
        add = re.search(r'(?i)a(?:ñ|&ntilde;)adir al carrito', body)
        more = re.search(r'(?i)m(?:á|&aacute;)s detalles', body)
        if add and not more:
            data['in_stock'] = True
        elif more and not add:
            data['in_stock'] = False
        cards.setdefault(link.group(1), dict(retailer_sku=ident.group(1), **data))
    for url in allowed:
        cards.setdefault(url, dict(retailer_sku=module.product_id(url), price_cents=None, in_stock=None))
    return cards


PARSERS = dict(ldlc=ldlc_cards, neobyte=neobyte_cards)


# ----------------------------------------------------------------- merge logic

def merge_listing(row, card, checked):
    """Updated cached row, or None when the listing value must not be trusted."""
    price, stock = card['price_cents'], card['in_stock']
    if price is None or stock is None or not row.get('price_cents'):
        return None
    ratio = price / row['price_cents']
    if ratio > 2 or ratio < 0.5:
        return None
    note = row.get('commercial_observation_source', '')
    return {**row, 'price_cents': price, 'in_stock': stock, 'last_checked_at': checked,
            'commercial_observation_source': note if note.endswith(SOURCE_NOTE) else note + SOURCE_NOTE}


# ----------------------------------------------------------------- crawl

class Blocked(RuntimeError):
    pass


def run(args):
    module = importlib.import_module(RETAILERS[args.retailer]['module'])
    config = RETAILERS[args.retailer]
    args.output.mkdir(parents=True, exist_ok=True)
    state_file = args.output / STATE_NAME
    now = time.time()
    for name in (STATE_NAME, 'run-state.json'):
        path = args.output / name
        if path.exists() and json.loads(path.read_text()).get('retry_after_epoch', 0) > now:
            raise SystemExit(f'{args.retailer} cooldown is active ({name}); nothing fetched')
    if not args.dry_run and urlparse(args.database_url or '').path != config['database']:
        raise SystemExit(f"Only the isolated {config['database'][1:]} database is allowed")

    import fcntl
    lock = (args.output / '.crawl.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit('Another crawl holds the lock for this directory')

    from curl_cffi import requests
    state = {}
    # Never go faster than the full crawler has had to (it raises the delay after 403/429/503).
    request_delay = max([2.0] + [float(json.loads((args.output / name).read_text()).get('request_delay_seconds', 2))
                                 for name in (STATE_NAME, 'run-state.json') if (args.output / name).exists()])
    stats = dict(pages=0, listed=0, from_listing=0, fallback_pages=0, new=0, unchanged=0,
                 price_changed=0, stock_changed=0, missing_from_listing=0, rejected_observations=0,
                 approved=0, pending=0, verified=0, verify_mismatches=0)

    def save_state(status, **extra):
        state.update(status=status, updated_at=datetime.now(timezone.utc).isoformat(),
                     request_delay_seconds=request_delay, dry_run=args.dry_run, **stats, **extra)
        temporary = state_file.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, indent=2) + '\n')
        temporary.replace(state_file)

    session = requests.Session(impersonate='chrome')
    last = 0.0

    def fetch(url):
        nonlocal last, request_delay
        parsed = urlparse(url)
        if parsed.hostname != config['host'] or parsed.scheme != 'https':
            raise ValueError('Unexpected crawl origin')
        time.sleep(max(0, request_delay - (time.monotonic() - last)))
        last = time.monotonic()
        r = session.get(url, timeout=45, allow_redirects=False)
        if r.status_code in (404, 410) and module.product_url(url):
            raise module.MissingListing(r.status_code, url)
        if r.status_code != 200:
            blocked = r.status_code in (403, 429, 503)
            if blocked:
                request_delay = min(12, request_delay * 2)
            save_state('blocked' if blocked else 'http_error', failed_url=url, http_status=r.status_code,
                       retry_after_epoch=time.time() + module.retry_delay(r.headers.get('Retry-After')) if blocked else 0)
            raise RuntimeError(f'HTTP {r.status_code}: {url}; refresh stopped')
        if 'Just a moment' in r.text or ('captcha' in r.text.lower() and '<title>captcha' in r.text.lower()):
            save_state('blocked', failed_url=url, retry_after_epoch=time.time() + 900)
            raise RuntimeError('Challenge; refresh stopped')
        return r.text

    def cache_path(url):
        return args.output / ((re.search(r'PB\d+', url)[0] if args.retailer == 'ldlc' else module.product_id(url)) + '.json')

    def write_cache(path, row):
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(path)

    def product_page(url, category):
        checked = datetime.now(timezone.utc).isoformat()
        try:
            html = fetch(url)
        except module.MissingListing as error:
            stats['rejected_observations'] += 1
            print(json.dumps(dict(event=f'{args.retailer}_listing_absent', url=url, status=error.status)), flush=True)
            return None
        try:
            row = module.candidate(html, url, category, checked)
        except (ValueError, KeyError, TypeError, InvalidOperation) as error:
            stats['rejected_observations'] += 1
            print(json.dumps(dict(event=f'{args.retailer}_rejected', url=url, reason=str(error))), flush=True)
            return None
        stats['fallback_pages'] += 1
        return row

    conn = None
    if not args.dry_run:
        import psycopg
        from catalog import write
        from review_queue import stage_candidates, approve
        conn = psycopg.connect(args.database_url)
    verify_left = args.verify
    seen_cached = set()
    save_state('running')
    try:
        for category in args.categories or module.CATEGORIES:
            category_seen = set()
            for slug, code in module.CATEGORIES[category]:
                url = (f'{module.PREFIX}{slug}/c{code}/' if args.retailer == 'ldlc' else f'{module.BASE}/{slug}-{code}')
                visited = set()
                while url:
                    if url in visited:
                        raise RuntimeError('Pagination loop')
                    visited.add(url)
                    html = fetch(url)
                    checked = datetime.now(timezone.utc).isoformat()
                    cards = PARSERS[args.retailer](html, module)
                    trusted = sum(c['price_cents'] is not None and c['in_stock'] is not None for c in cards.values())
                    stats['pages'] += 1
                    print(json.dumps(dict(event=f'{args.retailer}_listing_page', category=category, page=len(visited),
                                          cards=len(cards), with_price_and_stock=trusted)), flush=True)
                    if trusted == 0 and not args.allow_empty_listing:
                        raise RuntimeError('Listing markup produced no price/stock; page layout may have changed')
                    for item_url, card in cards.items():
                        if item_url in category_seen:
                            continue
                        category_seen.add(item_url)
                        stats['listed'] += 1
                        cache = cache_path(item_url)
                        old = None
                        if cache.exists():
                            old = json.loads(cache.read_text())
                            old['category_hint'] = category
                            seen_cached.add(cache.name)
                        row = merge_listing(old, card, checked) if old else None
                        if row is not None and verify_left > 0:
                            verify_left -= 1
                            page_row = product_page(item_url, category)
                            stats['verified'] += 1
                            if page_row and (page_row['price_cents'], page_row['in_stock']) != (row['price_cents'], row['in_stock']):
                                stats['verify_mismatches'] += 1
                                print(json.dumps(dict(event='verify_mismatch', url=item_url,
                                                      listing=[row['price_cents'], row['in_stock']],
                                                      product_page=[page_row['price_cents'], page_row['in_stock']])), flush=True)
                        if row is None:
                            row = product_page(item_url, category)
                            if row is None:
                                continue
                            if old is None:
                                stats['new'] += 1
                        else:
                            stats['from_listing'] += 1
                        if old:
                            changed_price = old.get('price_cents') != row['price_cents']
                            changed_stock = old.get('in_stock') != row['in_stock']
                            stats['price_changed'] += changed_price
                            stats['stock_changed'] += changed_stock
                            stats['unchanged'] += not (changed_price or changed_stock)
                            if (changed_price or changed_stock) and args.show_changes:
                                print(json.dumps(dict(event='change', url=item_url, price=[old.get('price_cents'), row['price_cents']],
                                                      in_stock=[old.get('in_stock'), row['in_stock']])), flush=True)
                        if args.dry_run:
                            continue
                        write_cache(cache, row)
                        with conn.transaction():
                            ids = stage_candidates(conn, [row])
                            pid = module.exact_product(conn, row)
                            if pid:
                                if ids:
                                    approve(conn, ids[0], f'{args.retailer.upper() if args.retailer == "ldlc" else "Neobyte"}: exact verified MPN/EAN, matching category and direct EUR offer from the listing/product page.', pid)
                                stats['approved'] += 1
                            else:
                                stats['pending'] += 1
                        conn.commit()
                    save_state('running', category=category)
                    url = module.Structured(html).next
                    if args.max_pages and len(visited) >= args.max_pages:
                        break
        if not args.categories and not args.max_pages:
            missing = sorted(p.name for p in args.output.glob('*.json')
                             if re.fullmatch(r'(PB\d+|\d+)\.json', p.name) and p.name not in seen_cached)
            stats['missing_from_listing'] = len(missing)
            (args.output / 'listing-refresh-missing.json').write_text(json.dumps(missing, indent=2) + '\n')
    finally:
        if conn:
            conn.close()
    (args.output / 'listing-refresh-summary.json').write_text(json.dumps(stats, indent=2) + '\n')
    save_state('complete')
    print(json.dumps(dict(event=f'{args.retailer}_listing_refresh_done', **stats)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--retailer', choices=RETAILERS, required=True)
    parser.add_argument('--database-url')
    parser.add_argument('--output', type=Path, required=True, help='Checkpoint directory with the cached PB*.json / <id>.json rows')
    parser.add_argument('--categories', nargs='+')
    parser.add_argument('--dry-run', action='store_true', help='Fetch and compare only: no DB, no cache writes')
    parser.add_argument('--verify', type=int, default=0, help='Also fetch N product pages and compare with the listing values')
    parser.add_argument('--max-pages', type=int, default=0, help='Stop each category after N listing pages (testing)')
    parser.add_argument('--show-changes', action='store_true')
    parser.add_argument('--allow-empty-listing', action='store_true')
    args = parser.parse_args()
    module_categories = importlib.import_module(RETAILERS[args.retailer]['module']).CATEGORIES
    if args.categories and not set(args.categories) <= set(module_categories):
        parser.error('Unknown category: ' + ', '.join(sorted(set(args.categories) - set(module_categories))))
    if not args.dry_run and not args.database_url:
        parser.error('--database-url is required unless --dry-run')
    run(args)
