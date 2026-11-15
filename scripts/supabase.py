"""Administración de la base Supabase de StockFinder (modelo de lectura público) vía Management API.

Lee SUPABASE_ACCESS_TOKEN de .env.supabase.local y NUNCA imprime credenciales ni cadenas de conexión.

  python3 scripts/supabase.py migrate            aplica db/supabase-migrations/*.sql pendientes (una transacción por fichero)
  python3 scripts/supabase.py worker-credentials crea/rota la contraseña de stockfinder_worker y guarda las URL en .env.supabase.local
  python3 scripts/supabase.py staging-secret     genera STAGING_SYNC_SECRET en .env.supabase.local si falta
  python3 scripts/supabase.py check              verificación de seguridad y recuento de filas (sin secretos)
  python3 scripts/supabase.py data-api-off       deja la Data API (PostgREST) sin esquemas expuestos
  python3 scripts/supabase.py sql-file FICHERO   ejecuta un SQL de administración (p. ej. backfill de historial)
"""
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.env.supabase.local'
REF = 'yasasecrkqudvuwhwgmx'
POOLER = 'aws-0-eu-west-1.pooler.supabase.com'


def read_env():
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip().lstrip('﻿')
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip()
    return out


def write_env(values):
    current = read_env()
    current.update(values)
    tmp = ENV.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as fh:
        fh.write(''.join(f'{k}={v}\n' for k, v in current.items()))
    os.chmod(tmp, 0o600)
    os.replace(tmp, ENV)


def api(method, path, body=None):
    req = urllib.request.Request(f'https://api.supabase.com/v1/{path}', method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f"Bearer {read_env()['SUPABASE_ACCESS_TOKEN']}",
                                          'Content-Type': 'application/json', 'User-Agent': 'stockfinder-admin'})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        # The error body may echo SQL; it never contains the token. Truncate anyway.
        raise SystemExit(f'Management API {method} {path}: HTTP {e.code}: {e.read().decode()[:800]}')


def sql(query):
    return api('POST', f'projects/{REF}/database/query', {'query': query})


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def migrate():
    sql('CREATE SCHEMA IF NOT EXISTS stockfinder_admin; REVOKE ALL ON SCHEMA stockfinder_admin FROM PUBLIC; '
        'CREATE TABLE IF NOT EXISTS stockfinder_admin.migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());')
    done = {r['version'] for r in sql('SELECT version FROM stockfinder_admin.migrations')}
    for path in sorted((ROOT / 'db/supabase-migrations').glob('*.sql')):
        if path.name in done:
            print(json.dumps({'event': 'migration_skipped', 'file': path.name}))
            continue
        sql(f'BEGIN;\n{path.read_text()}\n;\nINSERT INTO stockfinder_admin.migrations(version) VALUES ({quote(path.name)});\nCOMMIT;')
        print(json.dumps({'event': 'migration_applied', 'file': path.name}))


def worker_credentials():
    password = secrets.token_urlsafe(32)
    sql(f'ALTER ROLE stockfinder_worker LOGIN PASSWORD {quote(password)}')
    user = f'stockfinder_worker.{REF}'
    write_env({
        'SUPABASE_WORKER_PASSWORD': password,
        # Supavisor en modo sesión (IPv4): recomendado para Hyperdrive (Hyperdrive ya agrupa conexiones).
        'SUPABASE_WORKER_URL_SESSION': f'postgres://{user}:{password}@{POOLER}:5432/postgres?sslmode=require',
        # Conexión directa (solo IPv6 sin el complemento IPv4).
        'SUPABASE_WORKER_URL_DIRECT': f'postgres://stockfinder_worker:{password}@db.{REF}.supabase.co:5432/postgres?sslmode=require',
    })
    print(json.dumps({'event': 'worker_credentials_rotated', 'stored_in': str(ENV.name)}))


def staging_secret():
    if 'STAGING_SYNC_SECRET' in read_env():
        print(json.dumps({'event': 'staging_secret_exists'}))
        return
    write_env({'STAGING_SYNC_SECRET': secrets.token_hex(32)})
    print(json.dumps({'event': 'staging_secret_created', 'stored_in': str(ENV.name)}))


CHECK = """
SELECT
  (SELECT json_agg(json_build_object('role', r, 'schema_usage', has_schema_privilege(r, 'stockfinder', 'USAGE'),
     'any_table', (SELECT bool_or(has_table_privilege(r, format('stockfinder.%I', t.tablename), 'SELECT,INSERT,UPDATE,DELETE'))
                   FROM pg_tables t WHERE t.schemaname = 'stockfinder'),
     'any_function', (SELECT bool_or(has_function_privilege(r, p.oid, 'EXECUTE')) FROM pg_proc p
                      JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'stockfinder')))
   FROM unnest(ARRAY['anon', 'authenticated', 'service_role', 'stockfinder_worker']) r) AS privileges,
  (SELECT json_agg(json_build_object('table', c.relname, 'rls', c.relrowsecurity)) FROM pg_class c
     JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'stockfinder' AND c.relkind = 'r') AS rls,
  (SELECT COUNT(*) FROM pg_policies WHERE schemaname = 'stockfinder') AS policies,
  (SELECT json_build_object('login', rolcanlogin, 'bypassrls', rolbypassrls, 'config', rolconfig) FROM pg_roles WHERE rolname = 'stockfinder_worker') AS worker,
  (SELECT json_object_agg(t, n) FROM (VALUES
     ('catalog_categories', (SELECT COUNT(*) FROM stockfinder.catalog_categories)), ('catalog_shops', (SELECT COUNT(*) FROM stockfinder.catalog_shops)),
     ('catalog_products', (SELECT COUNT(*) FROM stockfinder.catalog_products)), ('catalog_offers', (SELECT COUNT(*) FROM stockfinder.catalog_offers)),
     ('offer_price_history', (SELECT COUNT(*) FROM stockfinder.offer_price_history)), ('product_reports', (SELECT COUNT(*) FROM stockfinder.product_reports)),
     ('sync_nonces', (SELECT COUNT(*) FROM stockfinder.sync_nonces))) v(t, n)) AS rows,
  (SELECT row_to_json(s) FROM stockfinder.sync_status s) AS sync_status,
  pg_size_pretty(pg_database_size(current_database())) AS database_size,
  (SELECT json_agg(version ORDER BY version) FROM stockfinder_admin.migrations) AS migrations
"""


def check():
    print(json.dumps(sql(CHECK)[0], indent=1, default=str))
    cfg = api('GET', f'projects/{REF}/postgrest') or {}
    print(json.dumps({'data_api_exposed_schemas': cfg.get('db_schema')}))  # only this field: the response also holds secrets


def data_api_off():
    cfg = api('PATCH', f'projects/{REF}/postgrest', {'db_schema': ''}) or {}
    print(json.dumps({'data_api_exposed_schemas': cfg.get('db_schema')}))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'migrate':
        migrate()
    elif cmd == 'worker-credentials':
        worker_credentials()
    elif cmd == 'staging-secret':
        staging_secret()
    elif cmd == 'check':
        check()
    elif cmd == 'data-api-off':
        data_api_off()
    elif cmd == 'sql-file':
        res = sql(Path(sys.argv[2]).read_text())
        print(json.dumps({'event': 'sql_file_applied', 'file': sys.argv[2], 'rows': len(res or [])}))
    else:
        raise SystemExit(__doc__)
