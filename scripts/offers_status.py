"""Antiguedad de las ofertas publicadas por tienda (p50/p95/max) y cuantas estan desactualizadas.

Lee PostgreSQL (DATABASE_URL), solo lectura. Uso, desde el host o contenedor:
  dc run --rm -T sync-agent python scripts/offers_status.py [--json]
La antiguedad es `now - last_checked_at` de la oferta autoritativa (no de lo ya publicado en D1: el backlog del
outbox se muestra aparte).
"""
import argparse
import json
import math
import os
from datetime import datetime, timezone

HOUR = 3600


def percentile(sorted_values, fraction):
    """Percentil por rango mas cercano (determinista, sin interpolar)."""
    if not sorted_values:
        return None
    return sorted_values[max(0, math.ceil(fraction * len(sorted_values)) - 1)]


def summarize(rows, now=None):
    """rows: iterable de (shop_id, last_checked_at datetime|str, in_stock). Devuelve {tienda: metricas}."""
    now = now or datetime.now(timezone.utc)
    shops = {}
    for shop, checked, in_stock in rows:
        if isinstance(checked, str):
            checked = datetime.fromisoformat(checked.replace('Z', '+00:00'))
        age = max(0.0, (now - checked).total_seconds())
        entry = shops.setdefault(shop, {'ages': [], 'in_stock': 0})
        entry['ages'].append(age)
        entry['in_stock'] += 1 if in_stock else 0
    result = {}
    for shop, entry in sorted(shops.items()):
        ages = sorted(entry['ages'])
        hours = lambda value: None if value is None else round(value / HOUR, 2)  # noqa: E731
        result[shop] = {
            'offers': len(ages), 'in_stock': entry['in_stock'],
            'age_p50_h': hours(percentile(ages, 0.50)), 'age_p95_h': hours(percentile(ages, 0.95)),
            'age_max_h': hours(ages[-1]),
            'stale_over_24h': sum(1 for a in ages if a > 24 * HOUR),
            'stale_over_48h': sum(1 for a in ages if a > 48 * HOUR),
        }
    return result


def collect(database_url):
    import psycopg
    with psycopg.connect(database_url) as conn:
        conn.read_only = True
        rows = conn.execute("""SELECT data->>'shop_id', (data->>'last_checked_at')::timestamptz, (data->>'in_stock')::boolean
          FROM catalog_entities WHERE entity='offer' AND deleted_at IS NULL""").fetchall()
        backlog = conn.execute("""SELECT count(*), min(changed_at) FROM catalog_outbox
          WHERE acknowledged_at IS NULL""").fetchone()
    return rows, backlog


def render(report, backlog):
    lines = [f'{"tienda":<14}{"ofertas":>8}{"stock":>7}{"p50 h":>8}{"p95 h":>8}{"max h":>8}{">24h":>7}{">48h":>7}']
    for shop, m in report.items():
        lines.append(f'{shop:<14}{m["offers"]:>8}{m["in_stock"]:>7}{m["age_p50_h"]:>8}{m["age_p95_h"]:>8}'
                     f'{m["age_max_h"]:>8}{m["stale_over_24h"]:>7}{m["stale_over_48h"]:>7}')
    lines.append(f'outbox pendiente: {backlog[0]}' + (f' (mas antiguo: {backlog[1].isoformat(timespec="seconds")})' if backlog[0] else ''))
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    rows, backlog = collect(os.environ['DATABASE_URL'])
    report = summarize(rows)
    if args.json:
        print(json.dumps({'shops': report, 'outbox_pending': backlog[0],
                          'outbox_oldest_pending': backlog[1].isoformat() if backlog[1] else None}, indent=2))
    else:
        print(render(report, backlog))


if __name__ == '__main__':
    main()
