"""Refresco de ofertas de Coolmod por el endpoint publico de precios (servicio propuesto `coolmod-offers`).

Sustituye la apertura de una ficha por target (coolmod.cjs, Playwright) por el mismo endpoint JSON que llaman los
listados de la propia web:

  GET /view_v2/ajax/category/newAjaxPricesForProductsWithCode.php?productCodes=["PROD-..",..]
      &DayId=PM&ZoneCode=PENINSULA&VatId=NV&TarId=1
  -> [{ProducCode, Price "94,95" (IVA incluido, igual que el JSON-LD de la ficha), OldPrice, Stock "50" (entero,
       tope 50), TextDelivered ("Agotado" <=> Stock 0), Slug "/ruta/", LifeCycleCode, BecomeBackinStockDate, ...}]

robots.txt no lo excluye (solo `Allow: /` y exclusiones de filtros/ordenacion). curl_cffi (huella TLS de Chrome),
una peticion cada vez, >= 2 s entre peticiones, `COOLMOD_AJAX_CHUNK` codigos por llamada (25, como la web).

Reglas (ProducCode = retailer_sku del target):
- Registro "coolmod-ajax" solo si: el target esta verificado en el catalogo (MPN exacto o alias revisado), la Slug
  coincide con la ruta de url_public, Price es un importe limpio > 0, Stock es un entero limpio y coherente con
  TextDelivered (Stock 0 <=> "Agotado"). in_stock = Stock > 0. Renueva last_checked_at: precio y stock son
  explicitos de la tienda en ese momento.
- Cualquier anomalia (codigo ausente, Slug distinta, formato raro, incoherencia) -> comprobacion de ficha.
- Ficha (curl_cffi, JSON-LD server-side): reverifica identidad (MPN exacto + SKU) cada COOLMOD_PAGE_VERIFY_DAYS (7),
  primero las anomalias y las nunca verificadas. Maximo COOLMOD_PAGE_CHECKS_PER_PASS fichas por pasada.
  404/410 o redireccion sin el producto -> `unavailable`. Identidad rota -> no se emite nada de ese target (ni por
  AJAX) hasta que otra ficha la confirme. Una ficha con stock propio solo publica si el JSON-LD y el
  data-itemavailability de la pagina coinciden (el JSON-LD de Coolmod dice InStock en productos agotados).
- 403/429/503 o pagina de verificacion: se para la pasada (sin fichas), se conserva lo hecho y se duerme
  COOLMOD_BLOCKED_SECONDS. Nunca se esquiva la proteccion.
- Fallo tecnico: no se emite nada de esos targets; se conserva el dato publicado.

Bandera: COOLMOD_AJAX_REFRESH=1 (apagada por defecto: sin ella el proceso no hace ninguna peticion).
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
HOST = 'www.coolmod.com'
BASE = f'https://{HOST}'
AJAX_PATH = '/view_v2/ajax/category/newAjaxPricesForProductsWithCode.php'
AJAX_QUERY = '&DayId=PM&ZoneCode=PENINSULA&VatId=NV&TarId=1'
REFERER = BASE + '/componentes-pc-memorias-ram/'
BLOCK_STATUS = {403, 429, 503}
CHALLENGE = re.compile(r'just a moment|attention required|captcha|access denied|are you a robot|verifica que eres', re.I)
PRICE = re.compile(r'^(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}$')
LD_PRICE = re.compile(r'^\d+(\.\d{1,2})?$')
STOCK = re.compile(r'^\d{1,6}$')
LD_JSON = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I)
ITEM_AVAILABILITY = re.compile(r'data-itemavailability="([A-Za-z]+)"')
AVAILABILITY = re.compile(r'^https?://schema\.org/(InStock|OutOfStock|SoldOut|Discontinued)$')
SHARD_SIZE = 400  # sync-agent acepta hasta 500 ofertas por fichero
MIN_DELAY = 2.0
MAX_CONSECUTIVE_FAILURES = 3
ANOMALY_RECHECK_SECONDS = 24 * 3600   # una anomalia persistente no reabre la ficha en cada pasada
IDENTITY_RETRY_SECONDS = 24 * 3600    # identidad rota: reintento de ficha
AJAX_EVIDENCE = ('Coolmod public price endpoint used by its own listings (newAjaxPricesForProductsWithCode): '
                 'ProducCode == retailer SKU, Slug == url path; explicit integer Stock consistent with TextDelivered; '
                 'curated exact MPN or reviewed alias; identity re-verified on the product page every {days} days.')
PAGE_EVIDENCE = ('Product page JSON-LD Product/Offer (curl_cffi): exact curated MPN or reviewed alias and retailer SKU; '
                 'EUR price; schema availability confirmed by the page data-itemavailability.')


class Blocked(Exception):
    """403/429/503 or a verification page: stop the pass, never retry around it."""


class IdentityError(Exception):
    """The product page does not show the curated MPN / retailer SKU."""


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def emit(event, **data):
    print(json.dumps({'event': event, 'shop': 'coolmod', 'ts': now_utc(), **data}, ensure_ascii=False), flush=True)


def error_fields(error):
    """type + short message without URL credentials or secret-looking query parameters."""
    message = re.sub(r'//[^\s/@]+@', '//***@', str(error))
    message = re.sub(r'([?&][^=&\s]*(?:token|key|secret|pass|sig|auth)[^=&\s]*=)[^&\s]+', r'\1***', message, flags=re.I)
    return {'type': type(error).__name__, 'message': message[:300] or None}


def path_key(url):
    try:
        return urlparse(url if '://' in url else BASE + url).path.rstrip('/').lower()
    except (ValueError, TypeError):
        return ''


def price_cents(text):
    """'1.234,95' / '94,95' -> cents (> 0), else None."""
    text = str(text if text is not None else '').strip()
    if not PRICE.match(text):
        return None
    cents = int(text.replace('.', '').replace(',', ''))
    return cents if 0 < cents <= 100000000 else None


# ---------------------------------------------------------------- targets e identidad (como coolmod.cjs)
def read_json(path, fallback):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return fallback
    except ValueError:
        emit('scraper_input_invalid', file=Path(path).name)
        return fallback


def valid_target(target):
    if not isinstance(target, dict):
        return False
    if not all(isinstance(target.get(k), str) and target[k].strip() for k in ('product_id', 'retailer_sku', 'mpn', 'url_public')):
        return False
    url = urlparse(target['url_public'])
    return url.scheme == 'https' and url.hostname == HOST


def load_targets(flow_file, data_dir):
    """Flow file (publicador) + pilot.json; si el flow falta o esta vacio, approved-coolmod-targets.json."""
    data_dir = Path(data_dir)
    pilot = [o for o in read_json(data_dir / 'retailers/pilot.json', []) if isinstance(o, dict) and o.get('shop_id') == 'coolmod']
    flow, source = read_json(flow_file, None), 'flow'
    if not isinstance(flow, list) or not flow:
        flow, source = read_json(data_dir / 'retailers/approved-coolmod-targets.json', []), 'approved'
        flow = flow if isinstance(flow, list) else []
    merged = [t for t in pilot + flow if valid_target(t)]
    targets = {}
    for target in merged:
        targets[target['retailer_sku']] = target
    return list(targets.values()), source, len(pilot) + len(flow) - len(merged)


def load_identity(data_dir):
    data_dir = Path(data_dir)
    products = {}
    for file in sorted((data_dir / 'catalog').glob('*.json')):
        for product in read_json(file, []):
            if isinstance(product, dict) and product.get('id'):
                products[product['id']] = product
    aliases = list(read_json(data_dir / 'product-identifiers.json', []) or [])
    for file in sorted((data_dir / 'aliases').glob('*.json')) if (data_dir / 'aliases').is_dir() else []:
        value = read_json(file, [])
        if isinstance(value, list):
            aliases.extend(value)
    return products, aliases


def verified_target(target, products, aliases):
    canonical = products.get(target['product_id'])
    if not canonical:
        return False
    norm = lambda value: str(value or '').strip().lower()
    if canonical.get('mpn') and norm(canonical['mpn']) == norm(target['mpn']):
        return True
    # Any reviewed mpn identifier of THIS product counts, whatever its evidence page (manufacturer page,
    # brand store or another shop): the shop id -> product link was approved in the review queue and the
    # periodic product-page check re-confirms MPN and SKU. Aliases of other products never count.
    return any(isinstance(a, dict) and a.get('product_id') == target['product_id'] and a.get('kind') == 'mpn'
               and norm(a.get('value')) == norm(target['mpn'])
               and str(a.get('source_url') or '').startswith('https://') and bool(a.get('note')) for a in aliases)


# ---------------------------------------------------------------- red
def fetch_prices(session, codes):
    """One AJAX call. Returns {ProducCode: entry}. Raises Blocked / RuntimeError."""
    url = BASE + AJAX_PATH + '?productCodes=' + quote(json.dumps(codes, separators=(',', ':')), safe='') + AJAX_QUERY
    response = session.get(url, timeout=30, headers={'Referer': REFERER, 'X-Requested-With': 'XMLHttpRequest'})
    if response.status_code in BLOCK_STATUS:
        raise Blocked(f'HTTP {response.status_code}')
    if response.status_code != 200:
        raise RuntimeError(f'Price endpoint HTTP {response.status_code}')
    try:
        data = json.loads(response.text)
    except ValueError:
        if CHALLENGE.search(response.text[:5000]):
            raise Blocked('challenge page') from None
        raise RuntimeError('Price endpoint returned non-JSON') from None
    if not isinstance(data, list):
        raise RuntimeError('Price endpoint returned no list')
    return {str(e.get('ProducCode')): e for e in data if isinstance(e, dict) and e.get('ProducCode')}


def fetch_page(session, url):
    """Returns (status, final_url, html). Raises Blocked."""
    response = session.get(url, timeout=45)
    if response.status_code in BLOCK_STATUS:
        raise Blocked(f'HTTP {response.status_code}')
    if response.status_code == 200 and not LD_JSON.search(response.text) and CHALLENGE.search(response.text[:5000]):
        raise Blocked('challenge page')
    return response.status_code, str(response.url), response.text


# ---------------------------------------------------------------- reglas
def classify_ajax(entry, target):
    """-> ({'price_cents', 'in_stock', 'stock'}, None) or (None, anomaly)."""
    if entry is None:
        return None, 'absent'
    if not entry.get('Slug') or path_key(str(entry['Slug'])) != path_key(target['url_public']):
        return None, 'slug_mismatch' if entry.get('Slug') else 'no_slug'
    cents = price_cents(entry.get('Price'))
    if cents is None:
        return None, 'price_format'
    stock = entry.get('Stock')
    stock = str(stock) if isinstance(stock, (str, int)) and not isinstance(stock, bool) else ''
    if not STOCK.match(stock):
        return None, 'stock_format'
    units = int(stock)
    if (units == 0) != (str(entry.get('TextDelivered') or '').strip().lower() == 'agotado'):
        return None, 'stock_text_conflict'
    return {'price_cents': cents, 'in_stock': units > 0, 'stock': units}, None


def products_in(documents):
    def flatten(value):
        if isinstance(value, list):
            return [x for item in value for x in flatten(item)]
        if isinstance(value, dict):
            return [value] + flatten(value.get('@graph') or [])
        return []
    types = lambda value: value.get('@type') if isinstance(value.get('@type'), list) else [value.get('@type')]
    return [v for v in flatten(documents) if 'Product' in types(v)]


def read_product_page(status, final_url, html, target):
    """-> {'kind': 'gone', 'reason'} | {'kind': 'ok', 'price_cents', 'ld_stock', 'page_stock'}; raises IdentityError/RuntimeError."""
    if status in (404, 410):
        return {'kind': 'gone', 'reason': f'HTTP {status}'}
    if status != 200 or urlparse(final_url).hostname != HOST:
        raise RuntimeError('Store request unavailable')
    documents = []
    for block in LD_JSON.findall(html):
        try:
            documents.append(json.loads(block))
        except ValueError:
            continue
    products = products_in(documents)
    listed = any(str(p.get('sku')) == target['retailer_sku'] for p in products)
    if path_key(final_url) != path_key(target['url_public']) and not listed:
        return {'kind': 'gone', 'reason': 'redirected away without the product'}
    product = next((p for p in products if str(p.get('mpn') or '').lower() == target['mpn'].lower()), None)
    if not product:
        raise IdentityError('Exact MPN absent')
    if str(product.get('sku')) != target['retailer_sku']:
        raise IdentityError('Retailer SKU changed')
    offers = product.get('offers') if isinstance(product.get('offers'), list) else [product.get('offers')]
    offer = next((o for o in offers if isinstance(o, dict) and o.get('priceCurrency') == 'EUR' and o.get('price') is not None), None)
    cents = None
    if offer and LD_PRICE.match(str(offer['price'])):
        cents = round(float(offer['price']) * 100)
        cents = cents if 0 < cents <= 100000000 else None
    match = AVAILABILITY.match(str((offer or {}).get('availability') or ''))
    ld_stock = match.group(1) == 'InStock' if match else None
    page = ITEM_AVAILABILITY.search(html)
    page_stock = {'InStock': True, 'OutOfStock': False}.get(page.group(1)) if page else None
    return {'kind': 'ok', 'price_cents': cents, 'ld_stock': ld_stock, 'page_stock': page_stock}


def base_record(target, checked):
    return {'product_id': target['product_id'], 'shop_id': 'coolmod', 'retailer_sku': target['retailer_sku'],
            'mpn': target['mpn'], 'url_public': target['url_public'], 'last_checked_at': checked}


def ajax_record(target, observed, checked, verify_days):
    return {**base_record(target, checked), 'price_cents': observed['price_cents'], 'in_stock': observed['in_stock'],
            'source': 'coolmod-ajax', 'match_evidence': AJAX_EVIDENCE.format(days=f'{verify_days:g}')}


def unavailable_record(target, reason, checked):
    return {**base_record(target, checked), 'unavailable': True, 'in_stock': False, 'source': 'coolmod-page',
            'match_evidence': f'Product page request: the store explicitly reports the listing as gone ({reason}); '
                              'previously verified exact MPN and retailer SKU; last published price kept.'}


def page_queue(targets, state, anomalies, now, verify_seconds, limit):
    """Anomalies first (not re-opened within 24 h), then identity re-verification: never verified, then oldest."""
    def entry(sku):
        return state.setdefault(sku, {})
    picked, seen = [], set()
    for target in sorted(targets, key=lambda t: t['retailer_sku']):
        sku = target['retailer_sku']
        if sku in anomalies and now - entry(sku).get('page_at', 0) >= ANOMALY_RECHECK_SECONDS:
            picked.append((target, 'anomaly:' + anomalies[sku])); seen.add(sku)
    due = []
    for target in targets:
        sku, info = target['retailer_sku'], entry(target['retailer_sku'])
        if sku in seen:
            continue
        if info.get('identity_failed_at'):
            if now - info.get('page_at', 0) >= IDENTITY_RETRY_SECONDS:
                due.append((0, info.get('page_at', 0), sku, target, 'identity_retry'))
        elif now - info.get('verified_at', 0) >= verify_seconds:
            due.append((1 if info.get('verified_at') else 0, info.get('verified_at', 0), sku, target,
                        'verify' if info.get('verified_at') else 'never_verified'))
    due.sort(key=lambda item: item[:3])
    picked.extend((item[3], item[4]) for item in due)
    return picked[:limit], max(0, len(picked) - limit)


def refresh(session, targets, state, config, fetch_prices=fetch_prices, fetch_page=fetch_page, sleep=time.sleep,
            clock=time.time):
    """One pass. `state` (sku -> {verified_at, page_at, identity_failed_at, anomaly}) is updated in place."""
    checked = now_utc()
    counts = {'calls': 0, 'pages': 0, 'ajax_ok': 0, 'anomalies': {}, 'page': {}, 'failed_codes': 0,
              'identity_blocked': 0, 'price_conflict': 0}
    records, anomalies, blocked, aborted = {}, {}, None, False
    pause = lambda: sleep(config['delay'] + random.uniform(0, 1))
    chunks = [targets[i:i + config['chunk']] for i in range(0, len(targets), config['chunk'])]
    consecutive = 0
    for index, chunk in enumerate(chunks):
        if counts['calls']:
            pause()
        counts['calls'] += 1
        try:
            entries = fetch_prices(session, [t['retailer_sku'] for t in chunk])
            consecutive = 0
        except Blocked as error:
            blocked = str(error); emit('scraper_blocked', phase='ajax', reason=blocked, calls=counts['calls']); break
        except Exception as error:  # noqa: BLE001 - technical failure: keep published data
            counts['failed_codes'] += len(chunk); consecutive += 1
            emit('ajax_chunk_failed', offset=index * config['chunk'], size=len(chunk), **error_fields(error))
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                aborted = True; emit('scraper_aborted', reason='consecutive failures', calls=counts['calls']); break
            continue
        for target in chunk:
            sku = target['retailer_sku']
            observed, anomaly = classify_ajax(entries.get(sku), target)
            if anomaly:
                anomalies[sku] = anomaly
                counts['anomalies'][anomaly] = counts['anomalies'].get(anomaly, 0) + 1
                state.setdefault(sku, {})['anomaly'] = anomaly
                continue
            state.setdefault(sku, {}).pop('anomaly', None)
            if state[sku].get('identity_failed_at'):
                counts['identity_blocked'] += 1
                continue
            counts['ajax_ok'] += 1
            records[sku] = ajax_record(target, observed, checked, config['verify_days'])
    pending = 0
    if not blocked and not aborted:
        queue, pending = page_queue(targets, state, anomalies, clock(), config['verify_days'] * 86400, config['page_limit'])
        for target, reason in queue:
            sku = target['retailer_sku']
            pause()
            counts['pages'] += 1
            info = state.setdefault(sku, {})
            info['page_at'] = clock()
            bump = lambda key: counts['page'].__setitem__(key, counts['page'].get(key, 0) + 1)
            try:
                result = read_product_page(*fetch_page(session, target['url_public']), target)
            except Blocked as error:
                blocked = str(error); emit('scraper_blocked', phase='page', reason=blocked, sku=sku); break
            except IdentityError as error:
                info['identity_failed_at'] = clock(); records.pop(sku, None); bump('identity_failed')
                emit('identity_failed', sku=sku, reason=str(error), trigger=reason); continue
            except Exception as error:  # noqa: BLE001
                bump('failed'); emit('page_failed', sku=sku, trigger=reason, **error_fields(error)); continue
            if result['kind'] == 'gone':
                records[sku] = unavailable_record(target, result['reason'], now_utc()); bump('gone'); continue
            info['verified_at'] = clock(); info.pop('identity_failed_at', None); bump('verified')
            if sku in records:  # clean AJAX this pass: the page only re-verifies identity (and price)
                if result['price_cents'] is not None and result['price_cents'] != records[sku]['price_cents']:
                    dropped = records.pop(sku); counts['price_conflict'] += 1
                    emit('price_conflict', sku=sku, ajax=dropped['price_cents'], page=result['price_cents'])
                continue
            if sku in anomalies and result['price_cents'] is not None and result['ld_stock'] is not None \
                    and result['page_stock'] in (None, result['ld_stock']):
                records[sku] = {**base_record(target, now_utc()), 'price_cents': result['price_cents'],
                                'in_stock': result['ld_stock'], 'source': 'coolmod-page', 'match_evidence': PAGE_EVIDENCE}
                bump('page_record')
            elif sku in anomalies:
                bump('stock_unconfirmed')
    return list(records.values()), {**counts, 'blocked': blocked, 'aborted': aborted, 'page_pending': pending}


# ---------------------------------------------------------------- ficheros
def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(text)
    os.replace(temporary, path)


def write_shards(records, output, shard_size=SHARD_SIZE):
    """coolmod.json, coolmod.2.json, ... (<= shard_size cada uno) y elimina los numerados que sobran."""
    output = Path(output)
    records = sorted(records, key=lambda r: r['retailer_sku'])
    shards = [records[i:i + shard_size] for i in range(0, len(records), shard_size)]
    names = []
    for number, shard in enumerate(shards, 1):
        path = output if number == 1 else output.with_name(f'{output.stem}.{number}{output.suffix}')
        write_atomic(path, json.dumps(shard, ensure_ascii=False, indent=2) + '\n')
        names.append(path.name)
    for stale in output.parent.glob(f'{output.stem}.*{output.suffix}'):
        if stale.name not in names and stale.stem.split('.')[-1].isdigit():
            stale.unlink()
    return names


def config_from_env(env=os.environ):
    def number(name, default, low, high):
        try:
            value = float(env.get(name, default))
        except ValueError:
            value = default
        return min(high, max(low, value))
    return {
        'enabled': str(env.get('COOLMOD_AJAX_REFRESH', '')).strip().lower() in ('1', 'true', 'yes', 'on'),
        'targets': env.get('COOLMOD_TARGETS_FILE', '/flow/targets/coolmod.json'),
        'data_dir': env.get('COOLMOD_DATA_DIR', str(ROOT / 'data')),
        'output': env.get('COOLMOD_OUTPUT_FILE', '/output/offers/coolmod.json'),
        'state': env.get('COOLMOD_REFRESH_STATE_FILE', '/output/state/coolmod-refresh-state.json'),
        'chunk': int(number('COOLMOD_AJAX_CHUNK', 25, 1, 100)),
        'delay': number('COOLMOD_DELAY_SECONDS', MIN_DELAY, MIN_DELAY, 60),
        'verify_days': number('COOLMOD_PAGE_VERIFY_DAYS', 7, 1, 90),
        'page_limit': int(number('COOLMOD_PAGE_CHECKS_PER_PASS', 20, 0, 500)),
        'interval': int(number('COOLMOD_REFRESH_INTERVAL_SECONDS', 3600, 900, 86400)),
        'blocked_seconds': int(number('COOLMOD_BLOCKED_SECONDS', 21600, 900, 86400)),
        'target_limit': int(number('COOLMOD_TARGET_LIMIT', 0, 0, 100000)),  # 0 = todos (solo para pruebas en seco)
    }


def run_cycle(config, session_factory, **fakes):
    started = time.monotonic()
    targets, source, dropped = load_targets(config['targets'], config['data_dir'])
    if not targets:
        emit('scraper_failure', type='NoTargets', source=source)
        return None
    products, aliases = load_identity(config['data_dir'])
    verified = [t for t in targets if verified_target(t, products, aliases)]
    unverified = len(targets) - len(verified)
    if config['target_limit']:
        verified = sorted(verified, key=lambda t: t['retailer_sku'])[:config['target_limit']]
    raw_state = read_json(config['state'], {})
    state = raw_state.get('targets') if isinstance(raw_state, dict) and isinstance(raw_state.get('targets'), dict) else {}
    emit('scraper_cycle_start', targets=len(targets), verified=len(verified), unverified=unverified, source=source,
         invalid_targets=dropped, calls=-(-len(verified) // config['chunk']))
    records, result = refresh(session_factory(), verified, state, config, **fakes)
    keep = {t['retailer_sku'] for t in targets}
    state = {sku: info for sku, info in state.items() if sku in keep}
    files = write_shards(records, config['output']) if records else []
    write_atomic(config['state'], json.dumps({'targets': state, 'last_run': now_utc(), 'last_result': result}))
    summary = dict(targets=len(targets), verified=len(verified), unverified=unverified, offers=len(records),
                   from_ajax=sum(1 for r in records if r['source'] == 'coolmod-ajax'),
                   from_page=sum(1 for r in records if r['source'] == 'coolmod-page' and not r.get('unavailable')),
                   unavailable=sum(1 for r in records if r.get('unavailable')),
                   in_stock=sum(1 for r in records if r.get('in_stock')), files=files,
                   duration_seconds=round(time.monotonic() - started), **result)
    emit('scraper_success' if records else 'scraper_failure', **summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--once', action='store_true', help='Una pasada y salir')
    args = parser.parse_args()
    config = config_from_env()

    def session_factory():
        from curl_cffi import requests
        return requests.Session(impersonate='chrome')

    while True:
        wait = config['interval']
        if not config['enabled']:
            emit('scraper_disabled', flag='COOLMOD_AJAX_REFRESH')
            if args.once:
                return
        else:
            try:
                summary = run_cycle(config, session_factory)
                if summary and summary.get('blocked'):
                    wait = max(wait, config['blocked_seconds'])
                if args.once and not (summary and summary['offers']):
                    raise SystemExit(1)
            except SystemExit:
                raise
            except Exception as error:  # noqa: BLE001
                emit('scraper_failure', **error_fields(error))
                if args.once:
                    raise SystemExit(1) from None
                wait = min(wait, 900)
            if args.once:
                return
        emit('scraper_sleep', seconds=wait)
        time.sleep(wait * random.uniform(0.95, 1.05))


if __name__ == '__main__':
    main()
