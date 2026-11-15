"""Automatic, incremental PcComponentes discovery.

Every interval this worker reads the newest pages of each component category,
applies the generation rules, drops references it already emitted, and writes
one atomic batch of at most 200 candidates. The sync agent watches that
directory and stages the batch into the review queue. This worker never
approves, imports offers or publishes products.

State lives outside the watched directory so the sync agent only sees batches.
The worker also stops scanning while too many reviews are pending, which keeps
the queue bounded when nobody is reviewing.
"""
import argparse
import json
import os
import time
from pathlib import Path

from curl_cffi import requests

from pccomponentes_batch import log, now_utc
from pccomponentes_discover import (
    EXCLUDE_NAMES, REGISTRY, fetch_page, known_references, load_registry,
    make_candidate, normalize, resolve_category, save_registry, specs_from,
)
from pccomponentes_rules import classify

DEFAULT_CATEGORIES = [
    'procesadores', 'placas-base', 'tarjetas-graficas', 'memorias-ram',
    'fuentes-alimentacion', 'discos-duros', 'cajas-pc',
    'ventiladores-cpu', 'refrigeracion-liquida',
]
ROOT = Path(__file__).resolve().parents[1]


def load_state(path):
    state = {'seen_skus': [], 'seen_mpns': [], 'last_run': None,
             'last_result': None, 'runs': 0}
    if path.is_file():
        state.update(json.loads(path.read_text()))
    return state


def save_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text)
    os.replace(temporary, path)


def approved_references(path):
    """Approved targets have no shop_id, so read them explicitly."""
    if not path.is_file():
        return set(), set()
    skus, mpns = set(), set()
    for item in json.loads(path.read_text()):
        if isinstance(item, dict) and item.get('retailer_sku'):
            skus.add(str(item['retailer_sku']))
            if item.get('mpn'):
                mpns.add(normalize(item['mpn']))
    return skus, mpns


def pending_reviews(database_url):
    if not database_url:
        return None
    import psycopg
    with psycopg.connect(database_url) as conn:
        row = conn.execute("""SELECT count(*) FROM retailer_review_queue
          WHERE shop_id='pccomponentes' AND status='pending'""").fetchone()
        return row[0]


def collect(session, categories, pages, max_candidates, known_skus, known_mpns,
            registry, delay):
    candidates, seen_skus, seen_mpns = [], set(), set()
    stats, excluded = {}, {}
    for requested in categories:
        if len(requested) == 36 and requested.count('-') == 4:
            category = next((c for c in registry if c['uuid'] == requested),
                            {'slug': requested, 'name': requested, 'uuid': requested})
        else:
            category = resolve_category(requested, session, registry)
        scanned = accepted = 0
        category_excluded = {}
        for page in range(1, pages + 1):
            body = fetch_page(session, category['uuid'], page, 'activation_date')
            articles = body.get('articles') or []
            for article in articles:
                scanned += 1
                mpn = normalize(article.get('mpn'))
                sku = str(article.get('id'))
                if not mpn or not article.get('isPCC'):
                    continue
                if article.get('isRefurbished'):
                    continue
                if EXCLUDE_NAMES.match(article.get('name') or ''):
                    continue
                price = article.get('price')
                if not isinstance(price, (int, float)) or isinstance(price, bool):
                    continue
                verdict = classify(category['slug'], article.get('name'),
                                   specs_from(article))
                if not verdict['modern']:
                    reason = verdict['reason']
                    category_excluded[reason] = category_excluded.get(reason, 0) + 1
                    excluded[reason] = excluded.get(reason, 0) + 1
                    continue
                if sku in known_skus or mpn in known_mpns:
                    continue
                if sku in seen_skus or mpn in seen_mpns:
                    continue
                seen_skus.add(sku)
                seen_mpns.add(mpn)
                candidate = make_candidate(article, category, now_utc())
                candidate['raw_properties'] += [
                    {'key': 'generation', 'value': verdict['generation']},
                    {'key': 'generation_rule', 'value': verdict['reason']},
                ]
                candidates.append(candidate)
                accepted += 1
                if len(candidates) >= max_candidates:
                    break
            log('category_page', category=category['slug'], page=page,
                scanned=len(articles), total=body.get('total'))
            if len(candidates) >= max_candidates or page >= (body.get('totalPages') or 0):
                break
            time.sleep(delay)
        stats[category['slug']] = {'scanned': scanned, 'new': accepted,
                                   'excluded': category_excluded}
        if len(candidates) >= max_candidates:
            break
    return candidates, stats, excluded


def run_once(args):
    state = load_state(args.state)
    state['runs'] = int(state.get('runs') or 0) + 1

    pending = None
    if args.max_pending > 0:
        try:
            pending = pending_reviews(args.database_url)
        except Exception as error:
            log('pending_check_failed', type=type(error).__name__)
    if pending is not None and pending >= args.max_pending:
        state.update(last_run=now_utc(), last_result='backlog_full')
        save_atomic(args.state, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
        log('skipped', reason='pending_backlog', pending=pending,
            limit=args.max_pending)
        return 0

    known_skus, known_mpns = known_references([
        ROOT / 'data/retailers/pccomponentes-candidates.json',
        ROOT / 'data/retailers/pilot.json',
        ROOT / 'data/retailers/approved-coolmod-targets.json',
    ])
    approved_skus, approved_mpns = approved_references(
        ROOT / 'data/retailers/approved-pccomponentes-targets.json')
    known_skus |= approved_skus | set(state.get('seen_skus') or [])
    known_mpns |= approved_mpns | set(state.get('seen_mpns') or [])

    registry = load_registry(REGISTRY)
    session = requests.Session(impersonate='chrome')
    candidates, stats, excluded = collect(
        session, args.categories, args.pages, args.max_candidates,
        known_skus, known_mpns, registry, args.delay)
    try:
        save_registry(REGISTRY, registry)
    except OSError:
        log('registry_readonly')

    if candidates:
        payload = json.dumps(candidates, ensure_ascii=False, indent=2) + '\n'
        save_atomic(args.output, payload)
        state['seen_skus'] = sorted(set(state.get('seen_skus') or []) |
                                    {candidate['retailer_sku'] for candidate in candidates})
        state['seen_mpns'] = sorted(set(state.get('seen_mpns') or []) |
                                    {normalize(candidate['mpn']) for candidate in candidates})
        state['last_result'] = 'batch_written'
    else:
        state['last_result'] = 'no_new_candidates'
    state['last_run'] = now_utc()
    state['pending_reviews'] = pending
    save_atomic(args.state, json.dumps(state, ensure_ascii=False, indent=2) + '\n')
    log('summary', candidates=len(candidates), pending=pending,
        excluded=excluded, categories=stats, output=str(args.output))
    return len(candidates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--categories', nargs='+', default=DEFAULT_CATEGORIES)
    parser.add_argument('--pages', type=int, default=3,
                        help='Newest pages per category (default 3 = 120 refs)')
    parser.add_argument('--interval', type=int, default=43200,
                        help='Seconds between runs (default 43200 = 12 h)')
    parser.add_argument('--max-candidates', type=int, default=190,
                        help='Batch limit (review queue accepts 200)')
    parser.add_argument('--max-pending', type=int, default=400,
                        help='Pause discovery above this many pending reviews (0 disables)')
    parser.add_argument('--delay', type=float, default=1.1,
                        help='Seconds between category pages')
    parser.add_argument('--output', type=Path,
                        default=Path('/output/pccomponentes/batch.json'))
    parser.add_argument('--state', type=Path,
                        default=Path('/output/pccomponentes-state/state.json'))
    parser.add_argument('--database-url', default=os.environ.get('DATABASE_URL'))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    while True:
        try:
            run_once(args)
        except Exception as error:
            log('failure', type=type(error).__name__, detail=str(error))
            if args.once:
                raise SystemExit(1) from None
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == '__main__':
    main()
