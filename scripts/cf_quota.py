#!/usr/bin/env python3
"""Comprueba el uso diario de Cloudflare frente a los límites del plan Free y avisa por Healthchecks.io.

Lee CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID y HEALTHCHECK_URL de .env.cloudflare.local
(o del entorno). Solo usa la librería estándar; nunca imprime credenciales.
Exit 0 = dentro del umbral, 1 = umbral superado, 2 = error al consultar.
Uso: python3 scripts/cf_quota.py [--threshold 70] [--dry-run]
"""
import argparse, datetime, json, os, sys, time, urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
# Límites Free (revisar si Cloudflare los cambia).
LIMITS = {'requests': 100_000, 'rowsRead': 5_000_000, 'rowsWritten': 100_000, 'd1Bytes': 500 * 1024 * 1024}
CPU_LIMIT_US = 10_000  # 10 ms de CPU por petición en Workers Free
RETRY_DELAYS = (2, 5)  # Dos reintentos, además del intento inicial.
RETRYABLE_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


def load_env():
    env = dict(os.environ)
    f = ROOT / '.env.cloudflare.local'
    if f.exists():
        for line in f.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k, v = line.split('=', 1)
                env.setdefault(k.strip(), v.strip())
    return env


def call(url, headers=None, data=None, method=None, timeout=20):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r) if 'json' in r.headers.get('content-type', '') else r.read()


def call_cloudflare(url, headers, data=None):
    """Reintenta fallos transitorios de red/API antes de declarar fallido el check."""
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            return call(url, headers, data)
        except HTTPError as e:
            if e.code not in RETRYABLE_HTTP_STATUS:
                raise
            error = e
        except (TimeoutError, URLError, ConnectionError) as e:
            error = e

        if attempt == len(RETRY_DELAYS):
            raise error

        delay = RETRY_DELAYS[attempt]
        if isinstance(error, HTTPError) and error.code == 429:
            try:
                delay = min(30, max(delay, int(error.headers.get('Retry-After', '0'))))
            except (TypeError, ValueError):
                pass
        print(f'cf_quota: Cloudflare no respondió ({type(error).__name__}); '
              f'reintento {attempt + 1}/{len(RETRY_DELAYS)} en {delay}s', file=sys.stderr)
        time.sleep(delay)


def collect(env):
    acc, h = env['CLOUDFLARE_ACCOUNT_ID'], {'Authorization': 'Bearer ' + env['CLOUDFLARE_API_TOKEN'], 'Content-Type': 'application/json'}
    day = datetime.datetime.now(datetime.timezone.utc).date().isoformat()  # Cloudflare cuenta por día UTC
    q = '{viewer{accounts(filter:{accountTag:"%s"}){w:workersInvocationsAdaptive(limit:50,filter:{date:"%s"}){sum{requests errors} quantiles{cpuTimeP99}} d:d1AnalyticsAdaptiveGroups(limit:50,filter:{date:"%s"}){sum{rowsRead rowsWritten}}}}}' % (acc, day, day)
    res = call_cloudflare('https://api.cloudflare.com/client/v4/graphql', h, json.dumps({'query': q}).encode())
    if res.get('errors'):
        raise RuntimeError('GraphQL: ' + json.dumps(res['errors'])[:200])
    a = res['data']['viewer']['accounts'][0]
    dbs = call_cloudflare(f'https://api.cloudflare.com/client/v4/accounts/{acc}/d1/database', h)['result']
    return day, {
        'requests': sum(x['sum']['requests'] for x in a['w']),
        'errors': sum(x['sum']['errors'] for x in a['w']),
        'rowsRead': sum(x['sum']['rowsRead'] for x in a['d']),
        'rowsWritten': sum(x['sum']['rowsWritten'] for x in a['d']),
        'd1Bytes': sum(d['file_size'] for d in dbs),
        'cpuP99': max([x['quantiles']['cpuTimeP99'] for x in a['w']] or [0]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--threshold', type=float, default=70.0, help='%% de cuota que dispara el aviso')
    ap.add_argument('--dry-run', action='store_true', help='no avisa a Healthchecks')
    args = ap.parse_args()
    env = load_env()
    hc = env.get('HEALTHCHECK_URL', '').rstrip('/')
    try:
        day, u = collect(env)
    except Exception as e:  # sin datos fiables, avisar como fallo
        msg = f'cf_quota: error consultando Cloudflare: {type(e).__name__}'
        print(msg, file=sys.stderr)
        if hc and not args.dry_run:
            try: call(hc + '/fail', data=msg.encode(), method='POST')
            except Exception: pass
        return 2
    lines, over = [f'Cloudflare {day} UTC (umbral {args.threshold:g}%)'], []
    for key, label in (('requests', 'Peticiones Worker'), ('rowsRead', 'D1 filas leídas'), ('rowsWritten', 'D1 filas escritas'), ('d1Bytes', 'D1 tamaño')):
        pct = 100 * u[key] / LIMITS[key]
        lines.append(f'{label}: {u[key]:,} / {LIMITS[key]:,} ({pct:.1f}%)')
        if pct >= args.threshold:
            over.append(label)
    cpu_pct = 100 * u['cpuP99'] / CPU_LIMIT_US
    lines.append(f'CPU p99: {u["cpuP99"] / 1000:.1f} ms / {CPU_LIMIT_US // 1000} ms ({cpu_pct:.0f}%) · errores Worker: {u["errors"]}')
    if cpu_pct >= 100:
        lines.append('AVISO: CPU p99 por encima del límite Free (informativo, no dispara fallo)')
    if over:
        lines.append('SUPERADO: ' + ', '.join(over))
    body = '\n'.join(lines)
    print(body)
    if hc and not args.dry_run:
        try: call(hc + ('/fail' if over else ''), data=body.encode(), method='POST')
        except Exception as e:
            print(f'No se pudo avisar a Healthchecks: {type(e).__name__}', file=sys.stderr)
            return 2
    elif not hc:
        print('HEALTHCHECK_URL no configurada: solo informe local.', file=sys.stderr)
    return 1 if over else 0


if __name__ == '__main__':
    raise SystemExit(main())
