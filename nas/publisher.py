"""Publicador automático del catálogo: lotes verificados -> PostgreSQL -> targets para los scrapers.

Cada ciclo (idempotente, con lock advisory propio y pausa por fichero /flow/PAUSE):
  a. valida y detecta cambios (hash) en data/catalog/*.json, data/aliases/*.json y product-identifiers.json;
     un fichero inválido o a medio escribir se registra y se omite sin bloquear al resto;
  b. importa productos (import_catalog.import_products) y c. alias (import_catalog.import_aliases);
  d. aplica decisiones artifacts/decisions-*.json con review_queue.bulk (las que fallen por producto o
     alias aún ausente se reintentan en ciclos posteriores);
  e. auto-aprueba filas pendientes cuyo MPN normalizado coincide EXACTAMENTE con un identificador verificado;
  f. exporta de forma atómica los targets aprobados a /flow/targets/<tienda>.json (solo si cambian);
  g. escribe /flow/state/publisher.json y logs JSON sin secretos.
Nunca aprueba por parecido de nombre. Ver docs/catalog-flow.md.
"""
import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flow_validation as fv  # noqa: E402
import import_catalog as ic  # noqa: E402
import review_queue as rq  # noqa: E402
from catalog import write  # noqa: E402
from catalog_images import enrich_images  # noqa: E402
from generate_seed import CATEGORIES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LOCK_ID = 73610942  # distinto del lock del sync-agent (73610941)
SHOPS = ('coolmod', 'pccomponentes')
RECOVERABLE = ('Observación sin MPN/EAN', 'Canonical product absent', 'Verified identifier does not match', 'Unknown canonical product')
BLOCKING_STATUS = ('review', 'conflict', 'mismatch', 'changed')


def now():
    return datetime.now(timezone.utc)


def log(event, **fields):
    print(json.dumps({'ts': now().isoformat(timespec='seconds'), 'event': event, **fields}, ensure_ascii=False, default=str), flush=True)


class Config:
    def __init__(self, **overrides):
        env = os.environ
        self.root = Path(env.get('PUBLISH_ROOT', ROOT))
        self.flow_dir = Path(env.get('FLOW_DIR', '/flow'))
        self.artifacts_dir = Path(env.get('ARTIFACTS_DIR', self.root / 'artifacts'))
        self.decisions_glob = env.get('DECISIONS_GLOB', 'decisions-*.json')
        self.interval = int(env.get('PUBLISH_INTERVAL_SECONDS', '300'))
        self.auto_approve = env.get('AUTO_APPROVE_KNOWN', '1') != '0'
        self.auto_approve_max = int(env.get('AUTO_APPROVE_MAX', '200'))
        self.retailer_codes = env.get('PUBLISH_RETAILER_CODES') == '1'
        self.decision_max_attempts = int(env.get('DECISION_MAX_ATTEMPTS', '2016'))  # ~7 días a 5 min
        for key, value in overrides.items():
            setattr(self, key, value)
        self.schema_ready = False

    @property
    def state_file(self):
        return self.flow_dir / 'state/publisher.json'

    @property
    def pause_file(self):
        return self.flow_dir / 'PAUSE'


def atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    os.replace(tmp, path)


def load_state(cfg):
    try:
        state = json.loads(cfg.state_file.read_text())
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def ensure_schema(conn, cfg):
    if cfg.schema_ready:
        return
    with conn.transaction():
        conn.execute((ic.ROOT / 'db/postgres-migrations/0002_identifiers.sql').read_text())
        rq.migrate(conn)
    cfg.schema_ready = True


def _note_error(report, step, error):
    report['errors'].append({'step': step, 'error': f'{type(error).__name__}: {str(error)[:300]}'})
    log('step_failed', step=step, type=type(error).__name__, detail=str(error)[:300])


# ---------------------------------------------------------------- a-c. productos y alias
def import_catalog_step(conn, cfg, scan, fstate, report):
    files = fstate.setdefault('catalog', {})
    stats = report['catalog'] = {'files': len(scan['catalog']), 'invalid': 0, 'imported_files': 0, 'products': 0, 'failed': 0}
    with conn.transaction():
        for slug, name in CATEGORIES:
            write(conn, 'category', slug, dict(slug=slug, name=name))
        for shop_id, name, url in ic.SHOPS:
            write(conn, 'shop', shop_id, dict(name=name, url_public=url))
    parseable = [p for info in scan['catalog'].values() if info['items'] is not None
                 for p in info['items'] if isinstance(p, dict) and 'id' in p]
    try:
        enriched = {p['id']: p for p in enrich_images(parseable, cfg.root)}
    except Exception as error:  # manifiesto de imágenes roto: se importa sin imágenes nuevas
        _note_error(report, 'images', error)
        enriched = {p['id']: p for p in parseable}
    valid_products = [p for info in scan['catalog'].values() if info['valid'] for p in info['items']]
    aliases_all = [a for info in scan['aliases'].values() if info['valid'] for a in info['items']]
    if scan['legacy'] and scan['legacy']['valid']:
        aliases_all += scan['legacy']['items']
    # Correcciones manuales (data/overrides): un fichero inválido se omite entero y se registra; no bloquea el resto.
    overrides, override_errors = ic.spec_overrides.load_overrides(cfg.root)
    for override_file, errors in override_errors.items():
        _note_error(report, 'overrides', ValueError(f'{override_file}: {errors[0]}'))
    overrides = ic.spec_overrides.index(overrides)
    stats['overrides'] = sum(len(v) for v in overrides.values())
    codes, codes_hash = None, ''
    if cfg.retailer_codes:
        codes = ic.retailer_codes(aliases_all, valid_products)
        codes_hash = fv.sha256(json.dumps(codes, sort_keys=True).encode())
    for name, info in scan['catalog'].items():
        entry = files.setdefault(name, {})
        if not info['valid']:
            stats['invalid'] += 1
            if entry.get('invalid_hash') != info['hash'] or entry.get('invalid_hash') is None:
                log('batch_invalid', file=name, errors=info['errors'][:5], total_errors=len(info['errors']))
            entry.update(status='invalid', invalid_hash=info['hash'], errors=info['errors'][:10])
            continue
        # Solo los ficheros con productos corregidos cambian de clave (sin correcciones, la clave es la de siempre).
        file_overrides = {p['id']: overrides[p['id']] for p in info['items'] if p['id'] in overrides}
        overrides_part = '|o' + fv.sha256(json.dumps(file_overrides, sort_keys=True).encode()) if file_overrides else ''
        key = fv.sha256(f"{info['hash']}|{scan['images_hash']}|{codes_hash}|b{ic.BRAND_MAP_VERSION}{overrides_part}".encode())
        if entry.get('imported_key') == key:
            stats['products'] += len(info['items'])
            continue
        try:
            with conn.transaction():
                count = ic.import_products(conn, [enriched[p['id']] for p in info['items']], codes, overrides)
            entry.update(status='imported', imported_key=key, hash=info['hash'], products=count, errors=[], invalid_hash=None)
            entry.pop('error', None)
            stats['imported_files'] += 1
            stats['products'] += count
            log('batch_imported', file=name, products=count)
        except Exception as error:
            stats['failed'] += 1
            message = f'{type(error).__name__}: {str(error)[:200]}'
            if entry.get('error') != message:
                log('batch_import_failed', file=name, detail=message)
            entry.update(status='error', error=message, hash=info['hash'], errors=[message])
    for name in list(files):
        if name not in scan['catalog']:
            files.pop(name)
    return {p['id']: enriched.get(p['id'], p) for name, info in scan['catalog'].items()
            if files.get(name, {}).get('imported_key') and info['items'] is not None
            for p in info['items'] if isinstance(p, dict) and 'id' in p}


def import_alias_step(conn, scan, fstate, imported_products, report):
    files = fstate.setdefault('aliases', {})
    stats = report['aliases'] = {'files': len(scan['aliases']), 'invalid': 0, 'imported': 0, 'pending': 0}
    products = list(imported_products.values())
    sources = list(scan['aliases'].items())
    if scan['legacy']:
        sources.append((fv.LEGACY_IDENTIFIERS, scan['legacy']))
    for name, info in sources:
        entry = files.setdefault(name, {})
        if not info['valid']:
            stats['invalid'] += 1
            if entry.get('invalid_hash') != info['hash'] or entry.get('invalid_hash') is None:
                log('alias_file_invalid', file=name, errors=info['errors'][:5], total_errors=len(info['errors']))
            entry.update(status='invalid', invalid_hash=info['hash'], errors=info['errors'][:10])
            continue
        if entry.get('done_hash') == info['hash']:
            continue
        failed = []
        for alias in info['items']:
            try:
                with conn.transaction():
                    ic.import_aliases(conn, products, [alias])
            except Exception as error:
                who = alias.get('product_id') if isinstance(alias, dict) else '?'
                failed.append(f'{who}: {str(error)[:160]}')
        if failed:  # producto aún ausente u otro fallo: se reintenta en ciclos posteriores
            stats['pending'] += 1
            entry.update(status='partial', failed=len(failed), errors=failed[:10], invalid_hash=None)
        else:
            stats['imported'] += 1
            entry.update(status='imported', done_hash=info['hash'], failed=0, errors=[], invalid_hash=None)
            log('aliases_imported', file=name, aliases=len(info['items']))
    names = {name for name, _ in sources}
    for name in list(files):
        if name not in names:
            files.pop(name)


# ---------------------------------------------------------------- d. decisiones
def _decision_ok(item):
    if not isinstance(item, dict) or item.get('action', 'approve') not in ('approve', 'reject'):
        return False
    try:
        int(item['review_id'])
    except (KeyError, TypeError, ValueError):
        return False
    return isinstance(item.get('note', ''), str)


def apply_decisions_step(conn, cfg, fstate, report):
    files = fstate.setdefault('decisions', {})
    totals = report['decisions'] = {'files': 0, 'approved': 0, 'rejected': 0, 'already_decided': 0,
                                    'retry': 0, 'permanent_errors': 0, 'malformed': 0, 'invalid_files': 0}
    seen_names = set()
    for path in sorted(cfg.artifacts_dir.glob(cfg.decisions_glob)):
        seen_names.add(path.name)
        totals['files'] += 1
        entry = files.setdefault(path.name, {})
        digest = None
        try:
            raw = path.read_bytes()
            digest = fv.sha256(raw)
            decisions = json.loads(raw)
            if not isinstance(decisions, list):
                raise ValueError('no es un array')
        except (OSError, ValueError) as error:  # a medio escribir: se reintenta cuando cambie
            totals['invalid_files'] += 1
            if entry.get('invalid_hash') != digest or digest is None:
                log('decisions_invalid', file=path.name, type=type(error).__name__)
            entry.update(status='invalid', invalid_hash=digest)
            continue
        if entry.get('hash') != digest:
            entry.clear()
            entry.update(hash=digest, attempts=0)
        entry.pop('invalid_hash', None)
        if entry.get('done') or entry.get('attempts', 0) >= cfg.decision_max_attempts:
            entry['status'] = 'done' if entry.get('done') else 'stuck'
            continue
        entry['attempts'] = entry.get('attempts', 0) + 1
        good = [d for d in decisions if _decision_ok(d)]
        malformed = len(decisions) - len(good)
        totals['malformed'] += malformed
        ids = [int(d['review_id']) for d in good]
        status = {}
        if ids:
            status = dict(conn.execute('SELECT review_id,status FROM retailer_review_queue WHERE review_id=ANY(%s)', (ids,)).fetchall())
        todo, decided, missing = [], 0, 0
        for d in good:
            current = status.get(int(d['review_id']))
            if current is None:
                missing += 1
            elif current != 'pending':
                decided += 1
            else:
                todo.append(d)
        result = rq.bulk(conn, todo) if todo else {'approved': 0, 'rejected': 0, 'offers': 0, 'errors': []}
        recoverable = [e for e in result['errors'] if any(m in e['error'] for m in RECOVERABLE)]
        permanent = [e for e in result['errors'] if e not in recoverable]
        entry.update(status='retrying' if recoverable else 'done', done=not recoverable,
                     approved=entry.get('approved', 0) + result['approved'],
                     rejected=entry.get('rejected', 0) + result['rejected'],
                     already_decided=decided, unknown_reviews=missing, malformed=malformed,
                     retry=len(recoverable), permanent_errors=permanent[:20], last_errors=recoverable[:10])
        totals['approved'] += result['approved']
        totals['rejected'] += result['rejected']
        totals['already_decided'] += decided
        totals['retry'] += len(recoverable)
        totals['permanent_errors'] += len(permanent) + missing
        if result['approved'] or result['rejected'] or recoverable or permanent:
            log('decisions_applied', file=path.name, approved=result['approved'], rejected=result['rejected'],
                retry=len(recoverable), errors=len(permanent), already_decided=decided)
    for name in list(files):
        if name not in seen_names:
            files.pop(name)


# ---------------------------------------------------------------- e. auto-aprobación
def auto_approve_reason(conn, proposed, candidate, review_note):
    """None si la fila se puede auto-aprobar; si no, el motivo (clave corta) por el que se deja pendiente."""
    if review_note and review_note.strip().lower().startswith('pendiente'):
        return 'held'
    if candidate.get('shop_id') not in SHOPS:
        return 'shop'
    missing = candidate.get('missing_fields') or []
    status = str(candidate.get('identity_status') or '').lower()
    if 'manufacturer_variant_conflict' in missing or any(m in status for m in BLOCKING_STATUS):
        return 'identity_conflict'
    if 'direct_seller_verification' in missing:
        return 'seller'
    mpn = rq.normalize(str(candidate.get('mpn') or ''))
    if not mpn:
        return 'no_mpn'
    product = conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id=%s AND deleted_at IS NULL", (proposed,)).fetchone()
    if not product:
        return 'product_absent'
    verified = conn.execute("SELECT product_id FROM product_identifiers WHERE kind='mpn' AND scope=%s AND value=%s", (product[0]['brand'], mpn)).fetchone()
    if not verified or verified[0] != proposed:
        return 'mpn_not_verified'
    ean = rq.normalize(str(candidate.get('ean') or ''))
    if ean:
        other = conn.execute("SELECT product_id FROM product_identifiers WHERE kind='ean' AND scope='global' AND value=%s", (ean,)).fetchone()
        if other and other[0] != proposed:
            return 'ean_conflict'
    return None


def refresh_proposals_step(conn, report):
    """Filas pendientes sin producto propuesto cuyo MPN (o EAN global) coincide EXACTAMENTE con un único
    identificador verificado: guarda la propuesta (misma regla que review_queue.stage_candidates)."""
    mpns, eans = {}, {}
    for kind, value, product_id in conn.execute("SELECT kind,value,product_id FROM product_identifiers WHERE kind IN ('mpn','ean')").fetchall():
        (mpns if kind == 'mpn' else eans).setdefault(value, set()).add(product_id)
    updated = 0
    rows = conn.execute("""SELECT review_id,candidate FROM retailer_review_queue
      WHERE status='pending' AND proposed_product_id IS NULL AND (candidate->>'mpn' IS NOT NULL OR candidate->>'ean' IS NOT NULL)""").fetchall()
    for review_id, candidate in rows:
        found = mpns.get(rq.normalize(str(candidate.get('mpn') or '')), set())
        if len(found) != 1 and candidate.get('ean'):
            found = eans.get(rq.normalize(str(candidate['ean'])), set())
        if len(found) == 1:
            conn.execute("UPDATE retailer_review_queue SET proposed_product_id=%s WHERE review_id=%s AND status='pending' AND proposed_product_id IS NULL",
                         (next(iter(found)), review_id))
            updated += 1
    report['proposals_refreshed'] = updated


def auto_approve_step(conn, cfg, report):
    stats = report['auto_approve'] = {'enabled': cfg.auto_approve, 'approved': 0, 'errors': 0, 'skipped': {}}
    if not cfg.auto_approve:
        return
    day = now().strftime('%Y-%m-%d')
    rows = conn.execute("""SELECT review_id,proposed_product_id,candidate,review_note FROM retailer_review_queue
      WHERE status='pending' AND proposed_product_id IS NOT NULL ORDER BY review_id""").fetchall()
    for review_id, proposed, candidate, note in rows:
        if stats['approved'] >= cfg.auto_approve_max:
            stats['skipped']['limit'] = stats['skipped'].get('limit', 0) + 1
            continue
        reason = auto_approve_reason(conn, proposed, candidate, note)
        if reason:
            stats['skipped'][reason] = stats['skipped'].get(reason, 0) + 1
            continue
        evidence = f'Auto-aprobado: MPN exacto coincide con el identificador verificado de {proposed} ({day})'
        try:
            with conn.transaction():
                rq.approve(conn, review_id, evidence, proposed)
            stats['approved'] += 1
        except Exception as error:
            stats['errors'] += 1
            log('auto_approve_failed', review_id=review_id, detail=str(error)[:160])
    if stats['approved']:
        log('auto_approved', count=stats['approved'])


# ---------------------------------------------------------------- f. targets
def export_targets(conn, cfg, dry_run, report):
    stats = report['targets'] = {}
    for shop in SHOPS:
        rows = conn.execute("""SELECT DISTINCT ON(retailer_sku) candidate,proposed_product_id FROM retailer_review_queue
          WHERE status='approved' AND shop_id=%s ORDER BY retailer_sku,review_id DESC""", (shop,)).fetchall()
        targets = sorted(({'product_id': row[1], 'retailer_sku': row[0].get('retailer_sku'), 'mpn': row[0].get('mpn'),
                           'url_public': row[0].get('url_public')} for row in rows), key=lambda t: str(t['retailer_sku']))
        text = json.dumps(targets, ensure_ascii=False, indent=2) + '\n'
        path = cfg.flow_dir / 'targets' / f'{shop}.json'
        try:
            same = path.read_text() == text
        except OSError:
            same = False
        if not same and not dry_run:
            atomic_write(path, text)
            log('targets_exported', shop=shop, count=len(targets))
        stats[shop] = {'count': len(targets), 'changed': not same}


# ---------------------------------------------------------------- f2. enriquecimiento (solo lectura)
def export_enrich_targets(conn, cfg, dry_run, report):
    """Filas coolmod PENDIENTES sin MPN (inventario solo de listados) para nas/scrapers/coolmod-enrich.cjs, que lee la
    ficha de cada producto y rellena MPN/EAN/SKU. Escritura atómica en /flow/enrich/coolmod.json solo si cambia."""
    rows = conn.execute("""SELECT DISTINCT ON(rtrim(split_part(split_part(candidate->>'url_public','?',1),'#',1),'/'))
        review_id,candidate FROM retailer_review_queue
      WHERE status='pending' AND shop_id='coolmod' AND coalesce(candidate->>'mpn','')='' AND coalesce(candidate->>'url_public','')<>''
      ORDER BY rtrim(split_part(split_part(candidate->>'url_public','?',1),'#',1),'/'),review_id""").fetchall()
    targets = sorted(({'review_id': rid, 'url_public': c.get('url_public'), 'retailer_sku': c.get('retailer_sku'),
                       'name': c.get('name'), 'brand': c.get('brand'), 'category_hint': c.get('category_hint')}
                      for rid, c in rows), key=lambda t: t['review_id'])
    text = json.dumps(targets, ensure_ascii=False, indent=1) + '\n'
    path = cfg.flow_dir / 'enrich' / 'coolmod.json'
    try:
        same = path.read_text() == text
    except OSError:
        same = False
    if not same and not dry_run:
        atomic_write(path, text)
        log('enrich_targets_exported', shop='coolmod', count=len(targets))
    report['enrich_targets'] = {'count': len(targets), 'changed': not same}


def queue_summary(conn):
    rows = conn.execute("""SELECT shop_id,coalesce(candidate->>'category_hint','sin clasificar'),status,count(*)
      FROM retailer_review_queue GROUP BY 1,2,3 ORDER BY 1,2,3""").fetchall()
    summary = {}
    for shop, category, status, count in rows:
        summary.setdefault(shop, {}).setdefault(category, {})[status] = count
    return summary


# ---------------------------------------------------------------- ciclo
def run_cycle(conn, cfg, dry_run=False):
    """Un ciclo completo. En dry_run todo se ejecuta dentro de una transacción que se revierte y no se escriben ficheros."""
    started = now()
    report = {'started_at': started.isoformat(timespec='seconds'), 'dry_run': dry_run, 'paused': False, 'errors': []}
    state = load_state(cfg)
    if cfg.pause_file.exists():
        report['paused'] = True
        log('paused')
    else:
        def body():
            ensure_schema(conn, cfg)
            fstate = state.setdefault('files', {})
            scan, imported = None, {}
            try:
                scan = fv.scan(cfg.root)
                imported = import_catalog_step(conn, cfg, scan, fstate, report)
            except Exception as error:
                _note_error(report, 'import_products', error)
            try:
                if scan is not None:
                    import_alias_step(conn, scan, fstate, imported, report)
            except Exception as error:
                _note_error(report, 'import_aliases', error)
            for step, call in (('decisions', lambda: apply_decisions_step(conn, cfg, fstate, report)),
                               ('proposals', lambda: refresh_proposals_step(conn, report)),
                               ('auto_approve', lambda: auto_approve_step(conn, cfg, report)),
                               ('targets', lambda: export_targets(conn, cfg, dry_run, report)),
                               ('enrich_targets', lambda: export_enrich_targets(conn, cfg, dry_run, report))):
                try:
                    call()
                except Exception as error:
                    _note_error(report, step, error)
            try:
                report['queue'] = queue_summary(conn)
            except Exception as error:
                _note_error(report, 'queue', error)
        if dry_run:
            with conn.transaction():
                body()
                raise psycopg.Rollback()
        else:
            body()
    finished = now()
    report['finished_at'] = finished.isoformat(timespec='seconds')
    report['seconds'] = round((finished - started).total_seconds(), 2)
    if not dry_run:
        totals = state.setdefault('totals', {})
        for key, value in (('cycles', 1), ('auto_approved', report.get('auto_approve', {}).get('approved', 0)),
                           ('decisions_approved', report.get('decisions', {}).get('approved', 0)),
                           ('decisions_rejected', report.get('decisions', {}).get('rejected', 0))):
            totals[key] = totals.get(key, 0) + value
        state.update(version=1, last_cycle=report, paused=report['paused'], updated_at=report['finished_at'])
        if report['errors']:
            state['recent_errors'] = ([{'at': report['finished_at'], **e} for e in report['errors']] + state.get('recent_errors', []))[:20]
        try:
            atomic_write(cfg.state_file, json.dumps(state, ensure_ascii=False, indent=2, default=str) + '\n')
        except OSError as error:
            log('state_write_failed', type=type(error).__name__)
    log('cycle', paused=report['paused'], dry_run=dry_run, seconds=report['seconds'], errors=len(report['errors']),
        imported_files=report.get('catalog', {}).get('imported_files'), approved=report.get('decisions', {}).get('approved'),
        auto_approved=report.get('auto_approve', {}).get('approved'))
    return report


def locked_cycle(conn, cfg, dry_run=False, wait=0):
    deadline = time.time() + wait
    while not conn.execute('SELECT pg_try_advisory_lock(%s)', (LOCK_ID,)).fetchone()[0]:
        if time.time() >= deadline:
            return None
        time.sleep(2)
    try:
        return run_cycle(conn, cfg, dry_run)
    finally:
        conn.execute('SELECT pg_advisory_unlock(%s)', (LOCK_ID,))


# ---------------------------------------------------------------- estado para `catalog_flow.py status`
def status_report(conn, cfg):
    out = {'queue': queue_summary(conn)}
    out['products_by_category'] = dict(conn.execute("""SELECT data->>'category_id',count(*) FROM catalog_entities
      WHERE entity='product' AND deleted_at IS NULL GROUP BY 1 ORDER BY 1""").fetchall())
    offers = conn.execute("""SELECT p.data->>'category_id',count(*),count(*) FILTER(WHERE (o.data->>'in_stock')='true'),
      min((o.data->>'last_checked_at')::timestamptz),max((o.data->>'last_checked_at')::timestamptz)
      FROM catalog_entities o JOIN catalog_entities p ON p.entity='product' AND p.entity_id=o.data->>'product_id'
      WHERE o.entity='offer' AND o.deleted_at IS NULL GROUP BY 1 ORDER BY 1""").fetchall()
    out['offers_by_category'] = {c: {'offers': n, 'in_stock': s, 'oldest_check': lo.isoformat(), 'newest_check': hi.isoformat()} for c, n, s, lo, hi in offers}
    backlog = conn.execute("SELECT count(*),min(changed_at) FROM catalog_outbox WHERE acknowledged_at IS NULL").fetchone()
    out['sync'] = {'backlog': backlog[0], 'oldest_pending': backlog[1].isoformat() if backlog[1] else None}
    state = load_state(cfg)
    files = {kind: {name: e for name, e in entries.items() if e.get('status') not in ('imported', 'done')}
             for kind, entries in (state.get('files') or {}).items()}
    out['unprocessed_files'] = {kind: {n: {'status': e.get('status'), 'errors': (e.get('errors') or e.get('last_errors') or [])[:2]} for n, e in list(f.items())[:15]}
                                for kind, f in files.items() if f}
    out['publisher'] = {'paused': cfg.pause_file.exists(), 'updated_at': state.get('updated_at'), 'totals': state.get('totals'),
                        'last_cycle': state.get('last_cycle'), 'recent_errors': state.get('recent_errors', [])[:5]}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true', help='un ciclo y salir')
    mode.add_argument('--watch', action='store_true', help='bucle continuo (por defecto)')
    mode.add_argument('--status', action='store_true', help='imprime el estado (JSON) y sale')
    parser.add_argument('--dry-run', action='store_true', help='ejecuta el ciclo en una transacción que se revierte; no escribe ficheros')
    parser.add_argument('--interval', type=int)
    args = parser.parse_args()
    cfg = Config()
    if args.interval:
        cfg.interval = args.interval
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    failures = 0
    while not stop:
        try:
            with psycopg.connect(os.environ['DATABASE_URL'], autocommit=True) as conn:
                if args.status:
                    print(json.dumps(status_report(conn, cfg), ensure_ascii=False, default=str))
                    return
                while not stop:
                    report = locked_cycle(conn, cfg, args.dry_run, wait=120 if args.once else 0)
                    failures = 0
                    if args.once:
                        if report is None:
                            raise SystemExit('Otro publicador está ejecutando un ciclo; reintenta en unos minutos')
                        print(json.dumps(report, ensure_ascii=False, default=str))
                        return
                    for _ in range(cfg.interval):
                        if stop:
                            break
                        time.sleep(1)
        except SystemExit:
            raise
        except Exception as error:
            log('publisher_failure', type=type(error).__name__)  # sin cadenas de conexión ni secretos
            if args.once or args.status:
                raise SystemExit(1) from None
            failures += 1
            time.sleep(min(60, 2 ** min(failures, 6)))


if __name__ == '__main__':
    main()
