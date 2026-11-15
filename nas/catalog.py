"""Canonical writes and transactional outbox. Used by the synthetic adapter."""
import argparse
from datetime import datetime, timezone
import json
import os
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from generate_seed import events  # noqa: E402

# Un sondeo sin cambios renueva `last_checked_at` y solo se publica (nueva version/evento)
# si la ultima marca PUBLICADA de la oferta tiene al menos esta antiguedad. La frescura publica
# la lleva ahora la marca por tienda (`shop_check`, ver publish_shop_check): el Worker la combina
# con las ofertas confirmadas en la pasada, asi que el refresco por oferta solo es una red de
# seguridad (24 h) y deja de costar ~4 escrituras/dia/oferta en D1.
FRESHNESS_SECONDS = 86400

# shop_check: marca de "pasada completa" por tienda (una fila por tienda, no por oferta).
SHOP_CHECK_MIN_INTERVAL = 1800   # s entre marcas publicadas de una misma tienda
SHOP_CHECK_MIN_COVERAGE = 0.8    # fraccion minima de ofertas vivas confirmadas para avanzar la marca

def freshness_seconds():
    try: return max(0, int(os.environ.get('OFFER_FRESHNESS_SECONDS', FRESHNESS_SECONDS)))
    except ValueError: return FRESHNESS_SECONDS

def write(conn, entity, entity_id, data, deleted=False, freshness=None):
    # The row lock taken by ON CONFLICT serializes versions of the same entity.
    # For offers, the published mark is read from the outbox event of the current version
    # (catalog_entities.data keeps the latest observation, which may not have been published).
    seconds = freshness_seconds() if freshness is None else freshness
    return conn.execute('''INSERT INTO catalog_entities(entity,entity_id,data,deleted_at)
      VALUES(%s,%s,%s,CASE WHEN %s THEN clock_timestamp() ELSE NULL END)
      ON CONFLICT(entity,entity_id) DO UPDATE SET data=excluded.data,
       version=catalog_entities.version + CASE
        WHEN catalog_entities.entity='offer'
          AND (catalog_entities.data-'last_checked_at')=(excluded.data-'last_checked_at')
          AND (catalog_entities.deleted_at IS NULL)=(excluded.deleted_at IS NULL)
          AND COALESCE((excluded.data->>'last_checked_at')::timestamptz - (
                SELECT (o.data->>'last_checked_at')::timestamptz FROM catalog_outbox o
                WHERE o.entity=catalog_entities.entity AND o.entity_id=catalog_entities.entity_id
                  AND o.version=catalog_entities.version),
              make_interval(secs => %s)) < make_interval(secs => %s)
        THEN 0 ELSE 1 END,
       deleted_at=excluded.deleted_at, changed_at=clock_timestamp()
      WHERE catalog_entities.data IS DISTINCT FROM excluded.data
       OR (catalog_entities.deleted_at IS NULL) IS DISTINCT FROM (excluded.deleted_at IS NULL)
      RETURNING version''', (entity, entity_id, Jsonb(data), deleted, seconds, seconds)).fetchone()

def ensure_shop_check_schema(conn):
    """Amplia (una sola vez) el CHECK de `entity` para admitir `shop_check`; idempotente y barato si ya esta."""
    conn.execute((Path(__file__).resolve().parents[1]/'db/postgres-migrations/0005_shop_check.sql').read_text())

def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

def _parse(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))

def publish_shop_check(conn, shop_id, checked_at, confirmed_ids, now=None, min_interval=SHOP_CHECK_MIN_INTERVAL,
                       min_coverage=SHOP_CHECK_MIN_COVERAGE):
    """Publica la marca de pasada de una tienda: {checked_at, unconfirmed_offer_ids}.

    `confirmed_ids`: ofertas confirmadas (importadas) en la pasada que empezo en `checked_at`. Las ofertas vivas
    de la tienda que NO estan ahi (marketplace pendiente, ausentes, fallos, targets retirados...) se publican como
    no confirmadas y conservan su propia fecha. Devuelve dict(published, reason, version, unconfirmed).
    No avanza la marca si la pasada cubre menos de `min_coverage` de las ofertas vivas, si no es posterior a la
    ya publicada o si no han pasado `min_interval` segundos desde ella (evita escrituras inutiles en D1)."""
    now = now or datetime.now(timezone.utc)
    checked_at = min(checked_at, now)  # nunca en el futuro
    with conn.transaction():
        ensure_shop_check_schema(conn)
        live = {row[0] for row in conn.execute("""SELECT entity_id FROM catalog_entities
          WHERE entity='offer' AND deleted_at IS NULL AND data->>'shop_id'=%s""", (shop_id,)).fetchall()}
        if not live: return dict(published=False, reason='no_live_offers', version=None, unconfirmed=0)
        confirmed = set(confirmed_ids) & live
        if len(confirmed) < min_coverage * len(live):
            return dict(published=False, reason='partial_pass', version=None, unconfirmed=len(live) - len(confirmed))
        data = dict(checked_at=_iso(checked_at), unconfirmed_offer_ids=sorted(live - confirmed))
        row = conn.execute("""SELECT data FROM catalog_entities WHERE entity='shop_check' AND entity_id=%s
          AND deleted_at IS NULL FOR UPDATE""", (shop_id,)).fetchone()
        if row:
            previous = _parse(row[0]['checked_at'])
            if checked_at <= previous: return dict(published=False, reason='not_newer', version=None, unconfirmed=len(data['unconfirmed_offer_ids']))
            if (checked_at - previous).total_seconds() < min_interval:
                return dict(published=False, reason='too_soon', version=None, unconfirmed=len(data['unconfirmed_offer_ids']))
        result = write(conn, 'shop_check', shop_id, data)
    return dict(published=result is not None, reason='ok' if result else 'unchanged',
                version=result[0] if result else None, unconfirmed=len(data['unconfirmed_offer_ids']))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['migrate','seed','change-price','backfill','status'])
    parser.add_argument('--offer', default='offer-0000-0')
    parser.add_argument('--price-cents', type=int, default=49900)
    args = parser.parse_args()
    if args.price_cents < 0: parser.error('price must be nonnegative')
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        if args.command == 'migrate':
            conn.execute((Path(__file__).resolve().parents[1]/'db/postgres-migrations/0001_catalog.sql').read_text())
        elif args.command == 'seed':
            for e in events(): write(conn, e['entity'], e['entity_id'], e['data'])
        elif args.command == 'change-price':
            row = conn.execute("SELECT data FROM catalog_entities WHERE entity='offer' AND entity_id=%s FOR UPDATE", (args.offer,)).fetchone()
            if row is None: raise SystemExit('Offer not found. Run seed first.')
            write(conn, 'offer', args.offer, {**row[0], 'price_cents': args.price_cents})
        elif args.command == 'backfill':
            # Re-enqueue current versions without changing authoritative commercial data.
            # Persistent progress is the per-event ACK, including across process restarts.
            conn.execute('''INSERT INTO catalog_outbox(entity,entity_id,version,operation,changed_at,data)
              SELECT entity,entity_id,version,CASE WHEN deleted_at IS NULL THEN 'upsert' ELSE 'delete' END,
              changed_at,CASE WHEN deleted_at IS NULL THEN data ELSE NULL END FROM catalog_entities
              ON CONFLICT(entity,entity_id,version) DO UPDATE SET acknowledged_at=NULL''')
        elif args.command == 'status':
            row = conn.execute('''SELECT count(*),count(*) FILTER(WHERE acknowledged_at IS NULL),
              min(changed_at) FILTER(WHERE acknowledged_at IS NULL) FROM catalog_outbox''').fetchone()
            print(json.dumps(dict(events=row[0], backlog=row[1], oldest_pending=str(row[2]))))

if __name__ == '__main__': main()
