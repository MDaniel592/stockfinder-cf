"""Refresco automatico de ofertas de PcComponentes (servicio `pccomponentes-offers`).

Cada PCC_REFRESH_INTERVAL_SECONDS (3 h por defecto) lee los targets aprobados, consulta precio/stock/vendedor con
`/api/articles/list-by-ids` (40 IDs por llamada, el maximo que admite: 41 devuelve HTTP 400; 2-3 s entre llamadas;
nunca `/api/articles/search`, vetado por robots.txt) con curl_cffi (huella TLS de navegador) y escribe ofertas listas para `import_offers.py` en el directorio de snapshots que vigila el sync-agent.
La logica de observacion (solo vendedor directo PcComponentes, MPN exacto, solo availabilityCode 1/3 es stock)
se reutiliza de `pccomponentes_batch.py`.

Targets, por orden: /flow/targets/pccomponentes.json (publicador), consulta a PostgreSQL equivalente a
`review_queue.py targets --shop pccomponentes`, y data/retailers/approved-pccomponentes-targets.json. Ademas se
anaden las ofertas PcComponentes de data/retailers/pilot.json (PCC_PILOT_TARGETS_FILE; '' lo desactiva) cuyo SKU no
este ya: son instantaneas manuales publicadas que ningun target cubria y quedaban sin refrescar.

Reglas de datos:
- Fallo tecnico (bloqueo, timeout, respuesta vacia, vendedor marketplace, MPN distinto): no se emite nada para esa
  oferta, se conserva el dato publicado y se registra el fallo.
- Ficha ausente en dos sondeos consecutivos con respuesta correcta, o artefacto DISCONTINUED con precio ausente:
  se publica `unavailable: true` / `in_stock: false` (import_offers conserva el ultimo precio). Nunca se borra.
- PCC_MARKETPLACE_GONE_AS_UNAVAILABLE=1: si en dos sondeos seguidos la ficha solo la vende un marketplace y el
  availabilityCode es 7/9999 (PcComponentes la ha descatalogado como vendedor directo), tambien `unavailable`.
- `unavailable` solo se emite para ofertas ya publicadas (si hay DATABASE_URL); el resto se cuenta como
  `unavailable_unpublished` en vez de generar rechazos en el sync-agent.

Limite de peticiones (HTTP 429): no se reintenta en caliente; se espera Retry-After o PCC_RATE_LIMIT_COOLDOWNS
(60,180 s por defecto) y se sigue. Los lotes fallidos se reintentan una vez al final de la pasada
(PCC_RETRY_FAILED_CHUNKS=1) y los SKU que sigan fallando van primero en la pasada siguiente (estado `retry_first`).
Sin enfriamientos disponibles, 3 lotes seguidos fallidos abortan la pasada (comportamiento anterior).
"""
import argparse
import hashlib
import json
import os
import random
import sys
import time
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pccomponentes_batch import BASE, BATCH_SIZE, RateLimited, fetch_batch, now_utc, observe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GONE_CODES = {7, 9999}  # DISCONTINUED, FORCE_DISCONTINUED: tienda explicita
MISSES_TO_CONFIRM_GONE = 2
MAX_CONSECUTIVE_CHUNK_FAILURES = 3
DEFAULT_DELAY = (2.0, 3.0)  # politica del propietario: peticiones >=2 s separadas
DEFAULT_COOLDOWNS = (60, 180)
MAX_COOLDOWN_SECONDS = 600
SHARD_SIZE = 400  # sync-agent acepta hasta 500 ofertas por fichero
TARGETS_SQL = """SELECT DISTINCT ON(retailer_sku) candidate,proposed_product_id FROM retailer_review_queue
  WHERE status='approved' AND shop_id='pccomponentes' ORDER BY retailer_sku,review_id DESC"""
PUBLISHED_SQL = """SELECT entity_id FROM catalog_entities
  WHERE entity='offer' AND deleted_at IS NULL AND entity_id LIKE 'pccomponentes-%'"""


def emit(event, **data):
    print(json.dumps({'event': event, 'shop': 'pccomponentes', 'ts': now_utc(), **data}, ensure_ascii=False), flush=True)


def valid_target(target):
    if not isinstance(target, dict):
        return False
    if not str(target.get('retailer_sku', '')).isdigit():
        return False
    if not all(isinstance(target.get(key), str) and target[key].strip() for key in ('product_id', 'mpn', 'url_public')):
        return False
    return target['url_public'].startswith(BASE + '/')


def read_list(path):
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, list) else None


def query_targets(database_url):
    import psycopg
    with psycopg.connect(database_url) as conn:
        rows = conn.execute(TARGETS_SQL).fetchall()
    return [{**{key: row[0].get(key) for key in ('retailer_sku', 'mpn', 'url_public')}, 'product_id': row[1]} for row in rows]


def pilot_targets(path):
    """Ofertas PcComponentes de pilot.json como targets (sin precio/stock: solo identidad verificada)."""
    rows = read_list(path) if path else None
    return [{key: row.get(key) for key in ('retailer_sku', 'mpn', 'url_public', 'product_id')}
            for row in rows or () if isinstance(row, dict) and row.get('shop_id') == 'pccomponentes']


def load_targets(flow_file, database_url=None, fallback_file=None, query=query_targets, extra=()):
    """Devuelve (targets validos unicos por SKU, origen, descartados). `extra` solo anade SKU nuevos."""
    raw, source = read_list(flow_file) if flow_file else None, 'flow'
    if not raw:
        raw = None
        if database_url:
            try:
                raw, source = query(database_url), 'database'
            except Exception as error:  # noqa: BLE001 - never log the connection string
                emit('targets_database_failed', type=type(error).__name__)
        if not raw and fallback_file:
            raw, source = read_list(fallback_file), 'approved_file'
    raw = raw or []
    targets = {}
    for target in raw:
        if valid_target(target):
            targets[str(target['retailer_sku'])] = {**target, 'retailer_sku': str(target['retailer_sku'])}
    dropped = len(raw) - len(targets)
    if targets:
        for target in extra:
            if valid_target(target) and str(target['retailer_sku']) not in targets:
                targets[str(target['retailer_sku'])] = {**target, 'retailer_sku': str(target['retailer_sku'])}
    return list(targets.values()), source, dropped


def offer_identity(sku):
    """Mismo id que nas/import_offers.py asigna a la oferta."""
    return 'pccomponentes-' + hashlib.sha256(str(sku).encode()).hexdigest()[:24]


def query_published(database_url):
    import psycopg
    with psycopg.connect(database_url) as conn:
        conn.read_only = True
        return {row[0] for row in conn.execute(PUBLISHED_SQL).fetchall()}


def env_flag(name, default):
    return os.environ.get(name, default).strip().lower() in ('1', 'true', 'yes', 'on')


def env_numbers(name, default):
    """'60,180' -> (60.0, 180.0); cadena vacia -> (); valor invalido -> default."""
    raw = os.environ.get(name)
    if raw is None:
        return tuple(default)
    try:
        return tuple(float(part) for part in raw.split(',') if part.strip())
    except ValueError:
        return tuple(default)


def unavailable_record(target, reason, evidence=None):
    return {
        'product_id': target['product_id'], 'shop_id': 'pccomponentes', 'retailer_sku': target['retailer_sku'],
        'mpn': target['mpn'], 'url_public': target['url_public'], 'unavailable': True, 'in_stock': False,
        'last_checked_at': now_utc(),
        'match_evidence': evidence or (f'PcComponentes /api/articles/list-by-ids: the store explicitly reports the listing as '
                                       f'gone ({reason}); previously verified exact MPN; last published price kept.'),
    }


def refresh(session, targets, misses, fetch=fetch_batch, sleep=time.sleep, delay=DEFAULT_DELAY, batch_size=BATCH_SIZE,
            cooldowns=DEFAULT_COOLDOWNS, retry_failed=True, marketplace_gone=False):
    """Una pasada completa. `misses` (sku -> sondeos consecutivos sin oferta directa) se modifica en sitio.

    Devuelve (records, {skipped, failed, failed_skus, aborted, calls, cooldowns, retried})."""
    records, skipped = [], {}
    cooldowns = list(cooldowns)
    stats = {'calls': 0, 'cooldowns': 0, 'retried': 0}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    def observe_target(target, article):
        sku = target['retailer_sku']
        previous_misses = misses.pop(sku, 0)
        if not article:
            misses[sku] = previous_misses + 1
            if misses[sku] >= MISSES_TO_CONFIRM_GONE:
                records.append(unavailable_record(target, 'absent from the article API in consecutive checks'))
            else:
                skip('absent_pending_confirmation')
            return
        record, reason = observe(article, target, False)
        code = (article.get('delivery') or {}).get('availabilityCode')
        if record:
            records.append(record)
        elif reason == 'price_absent' and code in GONE_CODES:
            records.append(unavailable_record(target, f'availabilityCode={code}'))
        elif marketplace_gone and reason in ('marketplace_seller', 'not_direct_seller') and code in GONE_CODES:
            misses[sku] = previous_misses + 1
            if misses[sku] >= MISSES_TO_CONFIRM_GONE:
                records.append(unavailable_record(target, None, evidence=(
                    'PcComponentes /api/articles/list-by-ids: in consecutive checks the listing is only sold by marketplace '
                    f'seller {article.get("sellerName") or "?"} with availabilityCode={code} (discontinued as direct seller); '
                    'previously verified exact MPN; last published price kept.')))
            else:
                skip('marketplace_gone_pending_confirmation')
        else:
            skip(reason)

    def attempt(chunk, label):
        """(True, None) si el lote se proceso; si no (False, RateLimited|None)."""
        if stats['calls']:
            sleep(random.uniform(*delay))
        stats['calls'] += 1
        try:
            articles = {str(a['id']): a for a in fetch(session, [t['retailer_sku'] for t in chunk])}
            if not articles:
                raise RuntimeError('Empty article response')
        except Exception as error:  # noqa: BLE001 - technical failure: keep published data
            limited = error if isinstance(error, RateLimited) else None
            emit('chunk_failed', offset=label, size=len(chunk), type=type(error).__name__,
                 **({'retry_after': limited.retry_after} if limited is not None else {}))
            return False, limited
        for target in chunk:
            observe_target(target, articles.get(target['retailer_sku']))
        return True, None

    def cool_down(limited):
        wait = cooldowns.pop(0)
        if limited is not None and limited.retry_after:
            wait = max(wait, limited.retry_after)
        wait = min(wait, MAX_COOLDOWN_SECONDS)
        stats['cooldowns'] += 1
        emit('cooldown', seconds=wait, reason='rate_limited' if limited is not None else 'consecutive_failures')
        sleep(wait)

    chunks = [targets[i:i + batch_size] for i in range(0, len(targets), batch_size)]
    failed_chunks, consecutive, aborted = [], 0, False
    for index, chunk in enumerate(chunks):
        ok, limited = attempt(chunk, index * batch_size)
        if ok:
            consecutive = 0
            continue
        failed_chunks.append((index * batch_size, chunk))
        consecutive += 1
        if (limited is not None or consecutive >= MAX_CONSECUTIVE_CHUNK_FAILURES) and cooldowns:
            cool_down(limited)
            consecutive = 0
        elif consecutive >= MAX_CONSECUTIVE_CHUNK_FAILURES:
            aborted = True
            failed_chunks += [((index + 1 + n) * batch_size, rest) for n, rest in enumerate(chunks[index + 1:])]
            break
    if retry_failed and failed_chunks and not aborted:
        pending, failed_chunks = failed_chunks, []
        for position, (label, chunk) in enumerate(pending):
            stats['retried'] += 1
            ok, limited = attempt(chunk, label)
            if not ok:
                failed_chunks.append((label, chunk))
                if limited is not None:  # sigue limitado: no insistir; la proxima pasada empieza por estos
                    failed_chunks += pending[position + 1:]
                    break
    failed_skus = [t['retailer_sku'] for _, chunk in failed_chunks for t in chunk]
    return records, {'skipped': skipped, 'failed': len(failed_skus), 'failed_skus': failed_skus, 'aborted': aborted,
                     **stats}


def drop_unpublished_unavailable(records, published):
    """Un `unavailable` sin oferta publicada solo produce un rechazo en import_offers: no se emite."""
    if published is None:
        return records, 0
    kept = [r for r in records if not r.get('unavailable') or offer_identity(r['retailer_sku']) in published]
    return kept, len(records) - len(kept)


def prioritize(targets, retry_first):
    """Los SKU que fallaron en la pasada anterior van primero (orden estable)."""
    first = set(retry_first or ())
    return [t for t in targets if t['retailer_sku'] in first] + [t for t in targets if t['retailer_sku'] not in first]


def write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(text)
    os.replace(temporary, path)


def write_shards(records, output, shard_size=SHARD_SIZE):
    """pccomponentes.json, pccomponentes.2.json, ... (cada uno <= shard_size) y elimina los que sobran."""
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


def load_state(path):
    try:
        state = json.loads(Path(path).read_text())
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def run_cycle(args, session_factory, fetch=None, sleep=time.sleep, published_query=query_published):
    started = time.monotonic()
    database_url = os.environ.get('DATABASE_URL')
    targets, source, dropped = load_targets(args.targets, database_url, args.fallback_targets,
                                            extra=pilot_targets(getattr(args, 'pilot_targets', None)))
    state = load_state(args.state)
    misses = {sku: n for sku, n in (state.get('misses') or {}).items() if isinstance(n, int)}
    if not targets:
        emit('refresh_skipped', reason='no_targets', source=source)
        return None
    retry_first = [sku for sku in (state.get('retry_first') or ()) if isinstance(sku, str)]
    targets = prioritize(targets, retry_first)
    cooldowns = tuple(getattr(args, 'cooldowns', DEFAULT_COOLDOWNS))
    if fetch is None:  # con enfriamientos, un 429 se gestiona aqui y no se reintenta en caliente
        fetch = partial(fetch_batch, retry_429=not cooldowns, sleep=sleep)
    emit('refresh_start', targets=len(targets), source=source, invalid_targets=dropped,
         calls=-(-len(targets) // BATCH_SIZE), retry_first=len(retry_first))
    records, result = refresh(session_factory(), targets, misses, fetch, sleep,
                              delay=getattr(args, 'delay', DEFAULT_DELAY), cooldowns=cooldowns,
                              retry_failed=getattr(args, 'retry_failed', True),
                              marketplace_gone=getattr(args, 'marketplace_gone', False))
    published = None
    if database_url and published_query and any(r.get('unavailable') for r in records):
        try:
            published = published_query(database_url)
        except Exception as error:  # noqa: BLE001 - never log the connection string
            emit('published_offers_query_failed', type=type(error).__name__)
    records, unpublished = drop_unpublished_unavailable(records, published)
    if unpublished:
        result['skipped']['unavailable_unpublished'] = unpublished
    keep = {t['retailer_sku'] for t in targets}
    state['misses'] = {sku: n for sku, n in misses.items() if sku in keep}
    state['retry_first'] = [sku for sku in result['failed_skus'] if sku in keep]
    state.update(last_run=now_utc(), targets=len(targets), records=len(records), skipped=result['skipped'],
                 failed=result['failed'], aborted=result['aborted'], calls=result['calls'])
    shards = write_shards(records, args.output) if records else []
    write_atomic(args.state, json.dumps(state))
    summary = dict(targets=len(targets), offers=len(records), unavailable=sum(1 for r in records if r.get('unavailable')),
                   in_stock=sum(1 for r in records if r.get('in_stock')), failed=result['failed'],
                   skipped=result['skipped'], aborted=result['aborted'], calls=result['calls'],
                   cooldowns=result['cooldowns'], retried_chunks=result['retried'], files=shards,
                   duration_seconds=round(time.monotonic() - started))
    emit('refresh_done' if records else 'refresh_failure', **summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', default=os.environ.get('PCC_TARGETS_FILE', '/flow/targets/pccomponentes.json'))
    parser.add_argument('--fallback-targets', default=str(ROOT / 'data/retailers/approved-pccomponentes-targets.json'))
    parser.add_argument('--pilot-targets', default=os.environ.get('PCC_PILOT_TARGETS_FILE', str(ROOT / 'data/retailers/pilot.json')))
    parser.add_argument('--output', type=Path, default=Path(os.environ.get('PCC_OUTPUT_FILE', '/output/offers/pccomponentes.json')))
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('PCC_STATE_FILE', '/output/pccomponentes-offers-state/state.json')))
    parser.add_argument('--interval', type=int, default=int(os.environ.get('PCC_REFRESH_INTERVAL_SECONDS', '10800')),
                        help='Seconds between passes (default 3 h; minimum 900)')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    interval = max(900, args.interval)
    delay = env_numbers('PCC_REQUEST_DELAY_SECONDS', DEFAULT_DELAY) or DEFAULT_DELAY  # 'min,max'
    args.delay = (max(0.0, delay[0]), max(0.0, delay[0], delay[-1]))
    args.cooldowns = env_numbers('PCC_RATE_LIMIT_COOLDOWNS', DEFAULT_COOLDOWNS)  # '' = abortar como antes
    args.retry_failed = env_flag('PCC_RETRY_FAILED_CHUNKS', '1')
    args.marketplace_gone = env_flag('PCC_MARKETPLACE_GONE_AS_UNAVAILABLE', '0')
    emit('config', delay=args.delay, cooldowns=args.cooldowns, retry_failed=args.retry_failed,
         marketplace_gone_as_unavailable=args.marketplace_gone)

    def session_factory():
        from curl_cffi import requests
        return requests.Session(impersonate='chrome')

    while True:
        wait = interval
        try:
            run_cycle(args, session_factory)
        except Exception as error:  # noqa: BLE001
            emit('refresh_error', type=type(error).__name__)
            wait = min(interval, 900)
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            return
        time.sleep(wait * random.uniform(0.95, 1.05))


if __name__ == '__main__':
    main()
