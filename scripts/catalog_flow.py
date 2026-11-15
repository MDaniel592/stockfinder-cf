#!/usr/bin/env python3
"""Operación del flujo tienda -> catálogo -> web desde el host (ver docs/catalog-flow.md).

  status                      cola, productos y ofertas por categoría, antigüedad de ofertas, publicador y sync
  shards                      reparte las observaciones pendientes SIN revisar en shards (<=60) para agentes de IA
  validate                    valida todos los lotes de data/catalog y data/aliases (solo lectura)
  publish --once              ejecuta un ciclo del publicador (--dry-run: transacción que se revierte)
  discover --backfill CAT...  descubrimiento profundo de PcComponentes por relevancia + staging en la cola

Solo usa la biblioteca estándar y `docker compose ... run --rm -T` (nunca up/restart/build).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ['docker', 'compose', '--env-file', '.env', '-f', 'infra/compose/compose.yaml']
CODE_MOUNTS = ['-v', f'{ROOT}/nas:/app/nas:ro', '-v', f'{ROOT}/scripts:/app/scripts:ro', '-v', f'{ROOT}/db:/app/db:ro']
CATEGORY_MAP = {'hdd': 'storage', 'fuentes-alimentacion': 'psu', 'discos-duros': 'storage', 'cajas-pc': 'case', 'ventiladores-cpu': 'cooler',
                'refrigeracion-liquida': 'cooler', 'memorias-ram': 'ram', 'placas-base': 'motherboard', 'procesadores': 'cpu',
                'tarjetas-graficas': 'gpu', 'gpu': 'gpu'}
STAGE_BATCH = 190  # la cola acepta hasta 200 candidatos por lote


def dc(*args, mount_code=False, volumes=(), capture=True, check=True):
    """docker compose ... run --rm -T <servicio> <cmd>: contenedor efímero, no toca servicios en ejecución."""
    service, *command = args
    cmd = [*COMPOSE, 'run', '--rm', '-T', *(CODE_MOUNTS if mount_code else []), *[x for v in volumes for x in ('-v', v)], service, *command]
    result = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=capture, stdin=subprocess.DEVNULL)
    if check and result.returncode:
        sys.stderr.write((result.stderr or '')[-2000:])
        raise SystemExit(result.returncode)
    return result


def last_json(text):
    """Primer documento JSON de la salida (la salida de compose puede traer líneas de estado antes)."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r'^[\[{]', text, re.M):
        try:
            return decoder.raw_decode(text[match.start():])[0]
        except ValueError:
            continue
    raise SystemExit('Salida sin JSON: ' + text[-300:])


def category_of(row):
    hint = (row['candidate'].get('category_hint') or '').strip().lower() or 'misc'
    return CATEGORY_MAP.get(hint, hint)


def age(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - stamp).total_seconds() / 3600
        return f'{hours / 24:.1f} d' if hours >= 48 else f'{hours:.1f} h'
    except (TypeError, ValueError):
        return '-'


# ------------------------------------------------------------------ status
def cmd_status(args):
    data = last_json(dc('catalog-publisher', 'python', 'nas/publisher.py', '--status', mount_code=args.mount_code).stdout)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    print('COLA DE REVISIÓN (pendiente / aprobada / rechazada)')
    totals = Counter()
    for shop, categories in sorted(data['queue'].items()):
        for category, counts in sorted(categories.items()):
            print(f"  {shop:14} {category:24} {counts.get('pending', 0):6} {counts.get('approved', 0):6} {counts.get('rejected', 0):6}")
            totals.update(counts)
    print(f"  {'TOTAL':39} {totals['pending']:6} {totals['approved']:6} {totals['rejected']:6}")
    print('\nPUBLICADO (productos / ofertas / con stock / oferta más antigua / más reciente)')
    for category in sorted(set(data['products_by_category']) | set(data['offers_by_category'])):
        offers = data['offers_by_category'].get(category, {})
        print(f"  {category:12} {data['products_by_category'].get(category, 0):6} {offers.get('offers', 0):6} {offers.get('in_stock', 0):6}"
              f"   {age(offers.get('oldest_check')):>8} {age(offers.get('newest_check')):>8}")
    sync = data['sync']
    print(f"\nSYNC a Cloudflare: backlog {sync['backlog']}" + (f", evento pendiente más antiguo {age(sync['oldest_pending'])}" if sync['oldest_pending'] else ''))
    pub = data['publisher']
    print(f"PUBLICADOR: {'PAUSADO' if pub['paused'] else 'activo'}; último ciclo {pub['updated_at'] or 'nunca'}; acumulado {pub['totals'] or '-'}")
    cycle = pub.get('last_cycle') or {}
    for key in ('catalog', 'aliases', 'decisions', 'auto_approve', 'targets'):
        if key in cycle:
            print(f'  {key}: {json.dumps(cycle[key], ensure_ascii=False)[:300]}')
    for kind, files in data.get('unprocessed_files', {}).items():
        for name, info in files.items():
            print(f"  PENDIENTE {kind}/{name}: {info['status']} {json.dumps(info['errors'], ensure_ascii=False)[:160]}")
    for error in pub.get('recent_errors', []):
        print(f"  ERROR {error.get('at')} {error.get('step')}: {error.get('error')}")
    ps = subprocess.run([*COMPOSE, 'ps', '--format', '{{.Service}}: {{.Status}}'], cwd=ROOT, text=True, capture_output=True, stdin=subprocess.DEVNULL).stdout
    print('\nSERVICIOS\n' + ''.join(f'  {l}\n' for l in ps.splitlines() if l.split(':')[0] in
                                    ('sync-agent', 'catalog-publisher', 'coolmod-scraper', 'pccomponentes-discovery', 'pccomponentes-refresh', 'postgres')))
    return 0


# ------------------------------------------------------------------ shards
def unreviewed(row, include_retained):
    note = (row.get('review_note') or '').strip()
    if not note or note.startswith('Observación de oferta'):  # nota automática, no es una revisión
        return True
    return include_retained and note.lower().startswith('pendiente')


def pack(rows, size):
    """Agrupa por marca (que quede junta en un shard) y rellena shards de hasta `size` filas."""
    groups = defaultdict(list)
    for row in rows:
        brand = (row['candidate'].get('brand') or (row['candidate'].get('name') or '?').split()[0]).strip().lower()
        groups[brand].append(row)
    shards = []
    for _, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        while len(items) > size:
            shards.append(items[:size]); items = items[size:]
        target = next((s for s in shards if len(s) + len(items) <= size), None)
        if target is None:
            shards.append(list(items))
        else:
            target.extend(items)
    return shards


def cmd_shards(args):
    rows = last_json(dc('sync-agent', 'python', 'nas/review_queue.py', 'export', mount_code=args.mount_code).stdout)
    selected = [r for r in rows if r['status'] == 'pending' and unreviewed(r, args.include_retained)
                and (not r['proposed_product_id'] or args.include_known)
                and (not args.shop or r['candidate'].get('shop_id') in args.shop)
                and (not args.category or category_of(r) in args.category)]
    by_category = defaultdict(list)
    for row in selected:
        by_category[category_of(row)].append(row)
    prefix = args.prefix or f'q{date.today():%Y%m%d}'
    out = Path(args.out) if args.out else ROOT / 'artifacts/shards'
    plan = []
    for category, items in sorted(by_category.items()):
        for number, shard in enumerate(pack(sorted(items, key=lambda r: r['review_id']), args.size), 1):
            plan.append((f'{prefix}-{category}-{number}', category, shard))
    if args.limit:
        plan = plan[:args.limit]
    print(f'{len(selected)} observaciones sin revisar -> {len(plan)} shards (<= {args.size} filas)')
    for name, category, shard in plan:
        brands = Counter((r['candidate'].get('brand') or '?') for r in shard)
        print(f"  {name:34} {len(shard):3} filas  {', '.join(b for b, _ in brands.most_common(4))}")
    if args.dry_run or not plan:
        return 0
    out.mkdir(parents=True, exist_ok=True)
    clash = [n for n, _, _ in plan if (out / f'{n}.json').exists()]
    if clash and not args.force:
        raise SystemExit(f'Ya existen {len(clash)} shards con ese prefijo ({clash[0]}...); usa --prefix distinto o --force')
    index = []
    for name, category, shard in plan:
        (out / f'{name}.json').write_text(json.dumps(shard, ensure_ascii=False, indent=1) + '\n')
        index.append({'shard': name, 'file': f'{name}.json', 'category': category, 'rows': len(shard),
                      'shops': sorted({r['candidate'].get('shop_id') for r in shard})})
    (out / f'{prefix}-index.json').write_text(json.dumps(index, ensure_ascii=False, indent=1) + '\n')
    print(f'Escritos en {out}; índice {prefix}-index.json. Protocolo: docs/catalog-shard-protocol.md')
    return 0


# ------------------------------------------------------------------ validate
def cmd_validate(args):
    sys.path.insert(0, str(ROOT / 'nas'))
    import flow_validation
    scan = flow_validation.scan(ROOT)
    bad = 0
    groups = [('catalog', scan['catalog']), ('aliases', scan['aliases'])]
    for label, files in groups:
        for name, info in files.items():
            ok = info['valid']
            bad += not ok
            if not ok or args.verbose:
                print(f"{'OK   ' if ok else 'ERROR'} {label}/{name}: {len(info['items'] or [])} elementos, {len(info['errors'])} errores")
            for line in info['errors'][:args.max_errors]:
                print('      ', line)
    print(f"{sum(len(f) for _, f in groups)} ficheros, {bad} inválidos; "
          f"{sum(len(i['items'] or []) for i in scan['catalog'].values())} productos, "
          f"{sum(len(i['items'] or []) for i in scan['aliases'].values())} alias")
    return 1 if bad else 0


# ------------------------------------------------------------------ publish
def cmd_publish(args):
    command = ['python', 'nas/publisher.py', '--once'] + (['--dry-run'] if args.dry_run else [])
    result = dc('catalog-publisher', *command, mount_code=args.mount_code, check=False)
    report = None
    for line in result.stdout.splitlines():
        if line.startswith('{"started_at"'):
            report = json.loads(line)
        elif line.startswith('{'):
            print(line)
    if result.returncode:
        sys.stderr.write(result.stderr[-1500:])
        return result.returncode
    if report:
        print(json.dumps({k: report.get(k) for k in ('dry_run', 'paused', 'seconds', 'catalog', 'aliases', 'decisions', 'auto_approve', 'targets', 'errors')},
                         ensure_ascii=False, indent=1))
    return 0


# ------------------------------------------------------------------ discover
def cmd_discover(args):
    if not args.backfill:
        raise SystemExit('Indica --backfill CATEGORIA [CATEGORIA ...]')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    work = ROOT / 'artifacts' / f'discovery-backfill-{stamp}'
    work.mkdir(parents=True)
    env = dict(os.environ)
    deps = '/tmp/stockfinder-pydeps'
    if Path(deps).is_dir():
        env['PYTHONPATH'] = deps + os.pathsep + env.get('PYTHONPATH', '')
    raw = work / 'candidates.json'
    cmd = [sys.executable, 'scripts/pccomponentes_discover.py', '--categories', *args.backfill, '--pages', str(args.pages),
           '--sort', args.sort, '--max-candidates', str(args.max_candidates), '--output', str(raw)]
    if subprocess.run(cmd, cwd=ROOT, env=env).returncode:
        raise SystemExit('El descubrimiento falló (consulta el log anterior)')
    candidates = json.loads(raw.read_text())
    chunks = [candidates[i:i + STAGE_BATCH] for i in range(0, len(candidates), STAGE_BATCH)]
    for number, chunk in enumerate(chunks, 1):
        (work / f'batch-{number:03}.json').write_text(json.dumps(chunk, ensure_ascii=False) + '\n')
    print(f'{len(candidates)} candidatos nuevos -> {len(chunks)} lotes en {work.relative_to(ROOT)}')
    if args.no_stage:
        return 0
    staged = 0
    for number in range(1, len(chunks) + 1):
        out = dc('sync-agent', 'python', 'nas/review_queue.py', 'stage', '--file', f'/staging/batch-{number:03}.json',
                 volumes=[f'{work}:/staging:ro'], mount_code=args.mount_code).stdout
        staged += len(last_json(out)['review_ids'])
    print(f'{staged} observaciones puestas en la cola (pendientes; nada se publica sin verificación)')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--mount-code', action='store_true', help='monta nas/ scripts/ db/ del host sobre la imagen (antes de reconstruirla)')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('status'); p.add_argument('--json', action='store_true'); p.set_defaults(fn=cmd_status)
    p = sub.add_parser('shards')
    p.add_argument('--prefix', help='prefijo de los nombres de shard (por defecto qAAAAMMDD)')
    p.add_argument('--size', type=int, default=60); p.add_argument('--limit', type=int, help='máximo de shards a escribir')
    p.add_argument('--shop', nargs='+'); p.add_argument('--category', nargs='+')
    p.add_argument('--include-retained', action='store_true', help="incluye filas con nota 'Pendiente: ...' (re-revisión)")
    p.add_argument('--include-known', action='store_true', help='incluye filas con producto propuesto (las auto-aprueba el publicador)')
    p.add_argument('--out'); p.add_argument('--force', action='store_true'); p.add_argument('--dry-run', action='store_true')
    p.set_defaults(fn=cmd_shards)
    p = sub.add_parser('validate'); p.add_argument('-v', '--verbose', action='store_true'); p.add_argument('--max-errors', type=int, default=8)
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser('publish'); p.add_argument('--once', action='store_true', required=True)
    p.add_argument('--dry-run', action='store_true'); p.set_defaults(fn=cmd_publish)
    p = sub.add_parser('discover')
    p.add_argument('--backfill', nargs='+', metavar='CATEGORIA'); p.add_argument('--pages', type=int, default=5)
    p.add_argument('--sort', default='relevance'); p.add_argument('--max-candidates', type=int, default=2000)
    p.add_argument('--no-stage', action='store_true', help='solo genera los lotes, sin ponerlos en la cola')
    p.set_defaults(fn=cmd_discover)
    args = parser.parse_args()
    sys.exit(args.fn(args))


if __name__ == '__main__':
    main()
