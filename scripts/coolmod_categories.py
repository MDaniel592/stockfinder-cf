"""Inventario publico de categorias de Coolmod SIN navegador (curl_cffi). Nunca publica productos ni ofertas.

Sustituye a nas/scrapers/coolmod-categories.cjs (Playwright). Mismo fichero de estado (COOLMOD_INVENTORY, version 1)
y mismos candidatos, asi que coolmod_inventory_auto.py y coolmod_filter.py no cambian.

Como pagina: /<slug>/ y /<slug>/?pagina=N (la web hace exactamente eso al pulsar "siguiente"); robots.txt no restringe
`pagina` (solo filtros, orden, precio y /consultas_ajax.php). El total de productos viene en el HTML ("281 productos").

Reglas (iguales a coolmod_refresh.py): una peticion cada vez, >= 2 s entre peticiones (+ jitter); 403/429/503 o una
pagina de desafio sin productos detienen la tanda y fijan `blocked_until` en el estado (COOLMOD_BLOCKED_SECONDS, 2 h):
hasta entonces el script no hace ninguna peticion. El progreso se guarda tras cada pagina (reanudable).

Entorno: COOLMOD_INVENTORY, COOLMOD_PAGES (1-200 paginas por tanda), COOLMOD_CATEGORIES (lista separada por comas),
COOLMOD_BLOCKED_SECONDS.
"""
import json
import math
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

HOST = 'www.coolmod.com'
BASE = f'https://{HOST}'
MIN_DELAY = 2.0
DEFAULT_PAGE_SIZE = 25
BLOCK_STATUS = (403, 429, 503)
CHALLENGE = re.compile(r'just a moment|attention required|captcha|access denied|are you a robot|verifica que eres', re.I)
TOTAL = re.compile(r'(\d[\d.]*)\s+productos', re.I)
CATEGORIES = [
    ('cpu', 'componentes-pc-procesadores'),
    ('gpu', 'tarjetas-graficas'),
    ('motherboard', 'componentes-pc-placas-base'),
    ('ram', 'componentes-pc-memorias-ram'),
    ('case', 'componentes-pc-torres-cajas'),
    ('psu', 'componentes-pc-fuentes-alimentacion'),
    ('storage', 'componentes-pc-discos-ssd'),
    ('hdd', 'componentes-pc-discos-hdd'),
    ('cooler', 'componentes-pc-disipadores-ventiladores'),
]
MISSING = ['retailer_sku', 'mpn', 'ean', 'manufacturer_url', 'verified_specifications', 'price', 'stock']


class Blocked(Exception):
    """403/429/503 o pagina de desafio: parar y no insistir."""


def log(event, **data):
    print(json.dumps({'event': event, **data}, ensure_ascii=False), flush=True)


class CardParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.cards = []

    def handle_starttag(self, tag, attrs):
        if tag != 'a':
            return
        a = dict(attrs)
        if 'data-itemname' not in a:
            return
        self.cards.append({'url': a.get('href') or '', 'name': a.get('data-itemname') or '',
                           'brand': a.get('data-itembrand'), 'listing_id': a.get('data-itemid'),
                           'listing_category': a.get('data-itemcategory2'), 'listing_price': a.get('data-itemprice')})


def parse_page(html, page_url):
    """Devuelve (tarjetas con URL absoluta https de coolmod sin query/hash, total de productos o None)."""
    parser = CardParser()
    parser.feed(html)
    cards = []
    for card in parser.cards:
        parsed = urlparse(urljoin(page_url, card['url']))
        if parsed.hostname != HOST or parsed.scheme != 'https' or not card['name'].strip():
            continue
        cards.append({**card, 'url': f'https://{HOST}{parsed.path}'})
    match = TOTAL.search(html)
    total = int(match.group(1).replace('.', '')) if match else None
    return cards, total


def page_url(slug, number):
    return f'{BASE}/{slug}/' if number <= 1 else f'{BASE}/{slug}/?pagina={number}'


def page_number(url):
    values = parse_qs(urlparse(url).query).get('pagina')
    return int(values[0]) if values and values[0].isdigit() else 1


def valid_saved_url(url, slug):
    parsed = urlparse(url)
    return (parsed.hostname == HOST and parsed.path == f'/{slug}/'
            and all(key == 'pagina' for key in parse_qs(parsed.query)))


def candidate(card, category, now):
    key = card['url'].rstrip('/')
    return {'shop_id': 'coolmod', 'retailer_sku': key, 'url_public': card['url'], 'name': card['name'].strip(),
            'brand': card['brand'] or None, 'mpn': None, 'ean': None, 'category_hint': category,
            'price_cents': None, 'in_stock': None, 'last_checked_at': now, 'missing_fields': list(MISSING),
            'raw_properties': [{'key': 'listing_id', 'value': card['listing_id'] or ''},
                               {'key': 'listing_category', 'value': card['listing_category'] or ''},
                               {'key': 'listing_price_unverified', 'value': card['listing_price'] or ''}]}


def fetch(session, url):
    response = session.get(url, timeout=45)
    if response.status_code in BLOCK_STATUS:
        raise Blocked(f'http {response.status_code}')
    if response.status_code != 200 or urlparse(str(response.url)).hostname != HOST:
        raise RuntimeError(f'Category unavailable: http {response.status_code}')
    return response.text


def crawl(session, state, save, requested, limit, sleep=time.sleep, clock=time.time):
    """Recorre las categorias pendientes (maximo `limit` paginas). Modifica `state` y llama a save(state) por pagina."""
    seen = {c['url_public'].rstrip('/') for c in state['candidates']}
    pages = 0
    first_request = True
    for category, slug in CATEGORIES:
        if category not in requested:
            continue
        if pages >= limit:
            break
        progress = state['categories'].get(category) or {'last_url': None, 'pages': 0, 'complete': False}
        if progress.get('complete') or progress.get('exhausted'):
            continue
        if progress.get('last_url') and not valid_saved_url(progress['last_url'], slug):
            raise RuntimeError('Invalid saved page URL')
        number = page_number(progress['last_url']) + 1 if progress.get('last_url') else 1
        while pages < limit:
            if not first_request:
                sleep(MIN_DELAY + random.uniform(0, 1))
            first_request = False
            url = page_url(slug, number)
            html = fetch(session, url)
            cards, total = parse_page(html, url)
            if not cards:
                if CHALLENGE.search(html[:50000]):
                    raise Blocked('challenge page')
                if number == 1:
                    raise RuntimeError('Empty category page')
                progress.update(exhausted=True, end_reason='empty_page')
                state['categories'][category] = progress
                save(state)
                break
            now = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
            added = 0
            for card in cards:
                key = card['url'].rstrip('/')
                if key in seen:
                    continue
                seen.add(key)
                added += 1
                state['candidates'].append(candidate(card, category, now))
            if number > 1 and not added:
                progress.update(exhausted=True, end_reason='repeat_page')
            size = progress.get('page_size') or (len(cards) if number == 1 else DEFAULT_PAGE_SIZE)
            progress['page_size'] = size
            if total:
                progress['total_products'] = total
            progress['last_url'] = url
            progress['pages'] = progress.get('pages', 0) + 1
            pages += 1
            last_page = math.ceil(total / size) if total else None
            if not progress.get('exhausted') and ((last_page and number >= last_page) or len(cards) < size):
                progress['complete'] = True
            state['categories'][category] = progress
            save(state)
            log('category_page', category=category, page=progress['pages'], cards=len(cards), added=added,
                total=len(state['candidates']), last_page=last_page)
            if progress.get('complete') or progress.get('exhausted'):
                break
            number += 1
    return pages


def load_state(path):
    try:
        state = json.loads(Path(path).read_text())
    except FileNotFoundError:
        state = {'version': 1, 'categories': {}, 'candidates': []}
    if state.get('version') != 1 or not isinstance(state.get('candidates'), list):
        raise RuntimeError('Invalid inventory')
    state.setdefault('categories', {})
    return state


def write_state(path, state):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    os.replace(temporary, path)


def main(env=os.environ, make_session=None, sleep=time.sleep):
    output = env.get('COOLMOD_INVENTORY', '/output/coolmod-inventory.json')
    limit = int(env.get('COOLMOD_PAGES', 8))
    names = [name for name, _ in CATEGORIES]
    requested = [c for c in env.get('COOLMOD_CATEGORIES', ','.join(names)).split(',') if c]
    blocked_seconds = int(env.get('COOLMOD_BLOCKED_SECONDS', 7200))
    if not 1 <= limit <= 200:
        raise SystemExit('COOLMOD_PAGES must be 1-200')
    if any(c not in names for c in requested):
        raise SystemExit('Unknown category')
    state = load_state(output)
    wait = float(state.get('blocked_until', 0)) - time.time()
    if wait > 0:
        log('inventory_blocked_wait', seconds=int(wait))
        return 0
    if make_session is None:
        from curl_cffi import requests
        make_session = lambda: requests.Session(impersonate='chrome')
    try:
        pages = crawl(make_session(), state, lambda s: write_state(output, s), requested, limit, sleep=sleep)
    except Blocked as error:
        state['blocked_until'] = time.time() + blocked_seconds
        write_state(output, state)
        log('inventory_blocked', reason=str(error), retry_in=blocked_seconds)
        return 3
    except Exception as error:
        log('inventory_error', type=type(error).__name__, detail=str(error)[:200])
        return 1
    state.pop('blocked_until', None)
    write_state(output, state)
    log('inventory_complete', pages=pages, total=len(state['candidates']), categories=state['categories'])
    return 0


if __name__ == '__main__':
    sys.exit(main())
