"""Coolmod PDP enrichment SIN navegador (curl_cffi): rellena MPN / EAN / SKU de Coolmod (+ precio, stock, vendedor) en
las filas PENDING de la cola de revision que el inventario por categorias dejo solo con datos de listado.

Sustituye a nas/scrapers/coolmod-enrich.cjs (Playwright) con el mismo contrato:
  Entrada : ENRICH_TARGETS_FILE (por defecto /flow/enrich/coolmod.json): [{review_id, url_public, retailer_sku, name,
            brand, category_hint}] (lo escribe en solo lectura nas/publisher.py).
  Salida  : lotes `enriched-<run>-<n>.json` en ENRICH_OUTPUT_DIR (los que sync-agent ya pone en cola). No aprueba nada.
  Estado  : ENRICH_STATE_FILE, tras cada pagina (reanudable; ninguna pagina se pide dos veces dentro de su cooldown).
            Mismo formato que el script antiguo (`at` en milisegundos), asi que el estado actual sigue valiendo.
Reglas: una pagina cada vez, >= 2 s entre paginas; un 403/429/503, una pagina de desafio o 5 fallos tecnicos seguidos
        detienen la tanda (codigo 3) conservando el progreso.
Fuente: la ficha trae en el HTML del servidor el JSON-LD Product/Offer y los atributos data-flix-ean / data-flix-mpn
        (EAN solo si pasa el digito de control GS1), asi que no hace falta ejecutar JavaScript.
CLI   : python coolmod_enrich.py [--limit N] [--dry-run] [--watch] [--input fichero]
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urljoin, urlparse

SHOP_HOST = 'www.coolmod.com'
MIN_PAGE_DELAY = 2.0
HOUR_MS = 3600 * 1000
COOLDOWN_MS = {'ok': 6 * HOUR_MS, 'gone': 7 * 24 * HOUR_MS, 'no_identity': 7 * 24 * HOUR_MS,
               'mismatch': 24 * HOUR_MS, 'failed': HOUR_MS}
BLOCK_STATUS = (403, 429, 503)
CHALLENGE = re.compile(r'just a moment|attention required|captcha|access denied|are you a robot|verifica que eres', re.I)
FLUSH_EVERY, MAX_STAGE, MAX_CONSECUTIVE_FAILURES = 25, 190, 5
LD_JSON = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
TITLE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
SKU = re.compile(r'^[A-Za-z0-9._-]{3,40}$')
PRICE = re.compile(r'^\d+(\.\d{1,2})?$')
AVAILABILITY = re.compile(r'^https?://schema.org/(InStock|OutOfStock|SoldOut|Discontinued)$')
CONTROL = re.compile(r'[\x00-\x1f]')


class BlockedError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class IdentityError(Exception):
    """La pagina no es el producto pedido o dos fuentes discrepan."""


def emit(stream, event, **data):
    print(json.dumps({'event': event, 'job': 'coolmod-enrich',
                      'ts': datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'), **data}),
          file=stream, flush=True)


def log(event, **data):
    emit(sys.stdout, event, **data)


def warn(event, **data):
    emit(sys.stderr, event, **data)


def text_of(value):
    """Equivalente a String(value || '') de JavaScript."""
    return str(value) if value else ''


def url_key(url):
    return text_of(url).split('?')[0].split('#')[0].rstrip('/').lower()


def path_key(url):
    try:
        return urlparse(urljoin('https://' + SHOP_HOST, str(url))).path.rstrip('/').lower()
    except ValueError:
        return ''


def same_path(a, b):
    return path_key(a) == path_key(b)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def now_ms():
    return int(time.time() * 1000)


# --- identidad (mismas reglas que extractIdentity de coolmod.cjs) -------------------------------------------------
def flatten_documents(documents):
    def flatten(value):
        if isinstance(value, list):
            return [item for entry in value for item in flatten(entry)]
        if isinstance(value, dict):
            return [value, *flatten(value.get('@graph') or [])]
        return []
    found = [item for document in documents for item in flatten(document)]
    return [v for v in found if 'Product' in (v.get('@type') if isinstance(v.get('@type'), list) else [v.get('@type')])]


def valid_ean(value):
    """EAN-8/12/13/14 con digito de control GS1 valido, si no None."""
    text = text_of(value).strip()
    if not re.fullmatch(r'\d{8}|\d{12}|\d{13}|\d{14}', text):
        return None
    digits = [int(c) for c in text]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return text if (10 - total % 10) % 10 == check else None


def normalize_code(value):
    return re.sub(r'[^A-Z0-9]', '', text_of(value).upper())


def clean(value):
    text = text_of(value).strip()
    return text if 2 <= len(text) <= 64 and not CONTROL.search(text) else None


def cents_of(price):
    if not PRICE.match(str(price)):
        return None
    cents = int(float(price) * 100 + 0.5)
    return cents if 0 <= cents <= 100000000 else None


def extract_identity(documents, url_public, flix=None):
    """Identidad + foto comercial de una ficha. Falla (IdentityError) si no es el producto pedido o hay discrepancias."""
    flix = flix or {}
    products = [v for v in flatten_documents(documents) if SKU.match(text_of(v.get('sku')))]
    wanted = path_key(url_public)
    by_url = [v for v in products if v.get('url') and path_key(v['url']) == wanted]
    candidates = by_url or products
    if len(candidates) != 1:
        raise IdentityError('Product ambiguous' if candidates else 'Product JSON-LD absent')
    product = candidates[0]
    mpn = clean(product.get('mpn')) or clean(product.get('productID'))
    flix_mpn = clean(flix.get('mpn'))
    if mpn and flix_mpn and normalize_code(mpn) != normalize_code(flix_mpn):
        raise IdentityError('MPN sources disagree')
    ld_ean = valid_ean(product.get('gtin13') or product.get('gtin14') or product.get('gtin12')
                       or product.get('gtin8') or product.get('gtin'))
    flix_ean = valid_ean(flix.get('ean'))
    if ld_ean and flix_ean and ld_ean != flix_ean:
        raise IdentityError('EAN sources disagree')
    offers = product.get('offers') or []
    offers = offers if isinstance(offers, list) else [offers]
    offer = next((o for o in offers if isinstance(o, dict) and o.get('priceCurrency') == 'EUR' and 'price' in o), None)
    price_cents = in_stock = seller = None
    if offer:
        price_cents = cents_of(offer['price'])
        availability = text_of(offer.get('availability'))
        if AVAILABILITY.match(availability):
            in_stock = availability.endswith('/InStock')
        if isinstance(offer.get('seller'), dict):
            seller = text_of(offer['seller'].get('name')).strip() or None
    brand_value = product.get('brand')
    brand = (text_of(brand_value.get('name')) if isinstance(brand_value, dict) else text_of(brand_value)).strip()
    return {'retailer_sku': str(product['sku']), 'mpn': mpn or flix_mpn, 'ean': ld_ean or flix_ean,
            'brand': brand or None, 'price_cents': price_cents, 'in_stock': in_stock, 'seller': seller,
            'seller_direct': not seller or bool(re.search('coolmod', seller, re.I))}


# --- lectura de la ficha ------------------------------------------------------------------------------------------
class FlixParser(HTMLParser):
    """Primer elemento con data-flix-ean / data-flix-mpn (como querySelector del script antiguo)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.flix = {}

    def handle_starttag(self, tag, attrs):
        if self.flix:
            return
        a = dict(attrs)
        if 'data-flix-ean' in a or 'data-flix-mpn' in a:
            self.flix = {'ean': a.get('data-flix-ean'), 'mpn': a.get('data-flix-mpn')}


def read_page(session, target):
    response = session.get(target['url_public'], timeout=45)
    status, final_url = response.status_code, str(response.url)
    if status != 200:
        return {'status': status, 'final_url': final_url, 'documents': []}
    html = response.text
    documents = []
    for block in LD_JSON.findall(html):
        try:
            documents.append(json.loads(block))
        except ValueError:
            continue
    parser = FlixParser()
    parser.feed(html)
    title = TITLE.search(html)
    return {'status': status, 'final_url': final_url, 'documents': documents, 'flix': parser.flix,
            'title': title.group(1).strip() if title else ''}


def enrich_target(session, target, deps):
    """{kind:'ok',identity} | {kind:'gone'|'no_identity'|'mismatch',reason}; lanza BlockedError / errores tecnicos."""
    result = deps.read_page(session, target)
    status = result['status']
    if status in BLOCK_STATUS:
        raise BlockedError(f'HTTP {status}')
    if status in (404, 410):
        return {'kind': 'gone', 'reason': f'HTTP {status}'}
    if status != 200 or urlparse(result['final_url']).hostname != SHOP_HOST:
        raise RuntimeError('Store request unavailable')
    if not result['documents'] and CHALLENGE.search(result.get('title') or ''):
        raise BlockedError('challenge page')
    if not same_path(result['final_url'], target['url_public']):
        return {'kind': 'gone', 'reason': 'redirected away'}
    try:
        identity = extract_identity(result['documents'], target['url_public'], result.get('flix') or {})
    except IdentityError as error:
        return {'kind': 'mismatch' if re.search('disagree|ambiguous', str(error)) else 'no_identity', 'reason': str(error)}
    if not identity['mpn']:
        return {'kind': 'no_identity', 'reason': 'page has no MPN'}
    return {'kind': 'ok', 'identity': identity}


def is_transient(error):
    module = type(error).__module__ or ''
    return module.startswith('curl_cffi') or isinstance(error, (TimeoutError, ConnectionError))


# --- candidatos, estado y seleccion -------------------------------------------------------------------------------
def build_candidate(target, identity, now):
    missing = ['manufacturer_url', 'verified_specifications']
    if not identity['ean']:
        missing.append('ean')
    if identity['price_cents'] is None:
        missing.append('price_cents')
    if identity['in_stock'] is None:
        missing.append('in_stock')
    if not identity['seller_direct']:
        missing.append('direct_seller_verification')
    candidate = {'shop_id': 'coolmod', 'url_public': target['url_public'], 'retailer_sku': identity['retailer_sku'],
                 'mpn': identity['mpn']}
    if identity['ean']:
        candidate['ean'] = identity['ean']
    name = target.get('name')
    brand = target.get('brand') or identity['brand']
    if name:
        candidate['name'] = name
    if brand:
        candidate['brand'] = brand
    if target.get('category_hint'):
        candidate['category_hint'] = target['category_hint']
    candidate.update({
        'price_cents': identity['price_cents'], 'in_stock': identity['in_stock'] if identity['seller_direct'] else None,
        'last_checked_at': now, 'commercial_observed_at': now,
        'commercial_observation_source': 'Coolmod product page JSON-LD Product/Offer (enrichment)',
        'missing_fields': missing,
        'raw_properties': [{'key': 'enrichment', 'value': 'coolmod-pdp'},
                           {'key': 'seller', 'value': identity['seller'] or 'Coolmod'},
                           {'key': 'direct_seller', 'value': identity['seller_direct']}]})
    return candidate


def valid_target(target):
    if not isinstance(target, dict) or not isinstance(target.get('url_public'), str):
        return False
    try:
        parsed = urlparse(target['url_public'])
    except ValueError:
        return False
    return parsed.scheme == 'https' and parsed.hostname == SHOP_HOST


def read_json(path, fallback):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return fallback
    except ValueError:
        warn('scraper_input_invalid', file=Path(path).name)
        return fallback


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(text)
    os.replace(temporary, path)


def load_state(path):
    state = read_json(path, {})
    state = state if isinstance(state, dict) else {}
    totals = {'ok': 0, 'gone': 0, 'no_identity': 0, 'mismatch': 0, 'failed': 0, 'blocked': 0}
    totals.update(state.get('totals') or {})
    return {'attempts': state.get('attempts') if isinstance(state.get('attempts'), dict) else {},
            'runs': int(state.get('runs') or 0), 'totals': totals, 'last_run': state.get('last_run')}


def select_targets(targets, state, now=None):
    """Objetivos fuera del cooldown de su ultimo resultado: primero los nunca intentados, luego el intento mas antiguo."""
    now = now_ms() if now is None else now
    seen, due = set(), []
    for target in targets:
        if not valid_target(target):
            continue
        key = url_key(target['url_public'])
        if key in seen:
            continue
        seen.add(key)
        last = state['attempts'].get(key)
        if last and now - last['at'] < COOLDOWN_MS.get(last.get('outcome'), HOUR_MS):
            continue
        due.append((last['at'] if last else 0, target.get('review_id') or 0, target))
    due.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in due]


class Lock:
    def __init__(self, path):
        self.path = Path(path)

    def touch(self):
        try:
            os.utime(self.path)
        except OSError:
            pass

    def release(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def acquire_lock(state_file, now=None):
    lock = Path(f'{state_file}.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    try:
        if now - lock.stat().st_mtime < 10 * 60:
            return None
    except FileNotFoundError:
        pass
    lock.write_text(str(os.getpid()))
    return Lock(lock)


def config_from_env(env=os.environ):
    def number(value, fallback):
        try:
            return float(value) if float(value) > 0 else fallback
        except (TypeError, ValueError):
            return fallback
    return {
        'targets_file': env.get('ENRICH_TARGETS_FILE') or '/flow/enrich/coolmod.json',
        'output_dir': env.get('ENRICH_OUTPUT_DIR') or '/output/coolmod',
        'state_file': env.get('ENRICH_STATE_FILE') or '/output/state/coolmod-enrich-state.json',
        'page_delay': max(MIN_PAGE_DELAY, number(env.get('ENRICH_PAGE_DELAY_MS'), MIN_PAGE_DELAY * 1000) / 1000),
        'batch_size': int(number(env.get('ENRICH_BATCH_SIZE'), 300)),
        'idle_seconds': number(env.get('ENRICH_IDLE_SECONDS'), 900),
        'blocked_seconds': number(env.get('ENRICH_BLOCKED_SECONDS'), 6 * 3600),
    }


def prune(directory, now):
    """Los lotes son idempotentes; se conservan dos dias por si sync-agent se reinicia y luego se descartan."""
    try:
        for entry in Path(directory).iterdir():
            if re.match(r'^enriched-.*\.json$', entry.name) and now - entry.stat().st_mtime > 2 * 24 * 3600:
                entry.unlink()
    except OSError:
        pass


def run_enrich(config, deps, limit=float('inf'), dry_run=False, input_path=None):
    """Una pasada por los objetivos pendientes. `deps` permite probar sin red (session, read_page, sleep, now)."""
    lock = SimpleNamespace(touch=lambda: None, release=lambda: None) if dry_run else acquire_lock(config['state_file'])
    if not lock:
        return {'skipped': 'locked'}
    started = time.time()
    run_id = datetime.fromtimestamp(started, timezone.utc).strftime('%Y%m%d%H%M%S') + f'{int(started * 1000) % 1000:03d}'
    try:
        targets = read_json(input_path or config['targets_file'], [])
        if not isinstance(targets, list):
            raise ValueError('Targets file is not a list')
        state = load_state(config['state_file'])
        due = select_targets(targets, state)[:int(min(limit, config['batch_size']))]
        summary = {'targets': len(targets), 'due': len(due), 'ok': 0, 'gone': 0, 'no_identity': 0, 'mismatch': 0,
                   'failed': 0, 'blocked': None, 'files': 0, 'with_ean': 0, 'records': []}
        log('enrich_start', targets=len(targets), due=len(due), dry_run=dry_run)
        if not due:
            return summary
        pending, consecutive, file_no = [], 0, 0

        def flush():
            nonlocal pending, file_no
            if pending and not dry_run:
                file_no += 1
                summary['files'] += 1
                write_atomic(Path(config['output_dir']) / f'enriched-{run_id}-{file_no:03d}.json',
                             json.dumps(pending, indent=2))
            pending = []

        def save_state():
            if dry_run:
                return
            state['last_run'] = now_iso()
            write_atomic(config['state_file'], json.dumps(state))
            lock.touch()

        try:
            for index, target in enumerate(due):
                if index > 0:
                    deps.sleep(config['page_delay'] + random.random())
                outcome = error = None
                for attempt in range(2):
                    if outcome:
                        break
                    try:
                        outcome = enrich_target(deps.session, target, deps)
                    except BlockedError as failure:
                        error = failure
                        break
                    except Exception as failure:  # noqa: BLE001 - errores tecnicos: un reintento si es transitorio
                        error = failure
                        if attempt == 0 and is_transient(failure):
                            deps.sleep(config['page_delay'])
                            continue
                        break
                key = url_key(target['url_public'])
                if not outcome and isinstance(error, BlockedError):
                    summary['blocked'] = error.reason
                    state['totals']['blocked'] += 1
                    warn('enrich_blocked', reason=error.reason, processed=index)
                    break
                if not outcome:
                    consecutive += 1
                    summary['failed'] += 1
                    state['totals']['failed'] += 1
                    code = 'transient' if is_transient(error) else 'extract'
                    state['attempts'][key] = {'at': now_ms(), 'outcome': 'failed', 'code': code}
                    warn('enrich_failed', review_id=target.get('review_id'), code=code)
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        summary['blocked'] = 'consecutive failures'
                        warn('enrich_blocked', reason=summary['blocked'])
                        break
                else:
                    consecutive = 0
                    kind = outcome['kind']
                    summary[kind] += 1
                    state['totals'][kind] += 1
                    attempt_state = {'at': now_ms(), 'outcome': kind}
                    if outcome.get('reason'):
                        attempt_state['reason'] = outcome['reason']
                    if kind == 'ok':
                        attempt_state['mpn'] = outcome['identity']['mpn']
                    state['attempts'][key] = attempt_state
                    if kind == 'ok':
                        candidate = build_candidate(target, outcome['identity'], deps.now())
                        summary['with_ean'] += 1 if candidate.get('ean') else 0
                        summary['records'].append(candidate)
                        pending.append(candidate)
                        if len(pending) >= min(FLUSH_EVERY, MAX_STAGE):
                            flush()
                if not dry_run:
                    save_state()
        finally:
            try:
                flush()
            except Exception as failure:  # noqa: BLE001
                warn('enrich_flush_failed', type=type(failure).__name__)
        state['runs'] += 1
        save_state()
        if not dry_run:
            prune(config['output_dir'], started)
        log('enrich_done', **{k: v for k, v in summary.items() if k != 'records'}, dry_run=dry_run,
            duration_seconds=round(time.time() - started))
        return summary
    finally:
        lock.release()


def main(argv=None, env=os.environ, make_session=None, sleep=time.sleep):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--limit', type=int)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--input')
    args = parser.parse_args(argv)
    config = config_from_env(env)
    limit = max(1, args.limit) if args.limit else float('inf')
    if make_session is None:
        from curl_cffi import requests
        make_session = lambda: requests.Session(impersonate='chrome')
    deps = SimpleNamespace(session=make_session(), read_page=read_page, sleep=sleep, now=now_iso)
    code = 0
    while True:
        wait = config['idle_seconds']
        try:
            result = run_enrich(config, deps, limit=limit, dry_run=args.dry_run, input_path=args.input)
            if args.dry_run:
                print(json.dumps(result, indent=2))
            if result.get('blocked'):
                code = 3
                wait = config['blocked_seconds']
            elif result.get('due') and result['due'] >= min(limit, config['batch_size']):
                wait = 5
        except Exception as error:  # noqa: BLE001
            warn('enrich_failure', type=type(error).__name__, message=str(error)[:160])
            if not args.watch:
                code = 1
        if not args.watch:
            return code
        log('enrich_sleep', seconds=wait)
        sleep(wait)


if __name__ == '__main__':
    sys.exit(main())
