#!/usr/bin/env bash
# Clone the NAS catalogue once and crawl LDLC into the isolated copy only.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
PG=stockfinder-v2-postgres-1
DB=stockfinder_ldlc_local
if ! docker exec "$PG" psql -U stockfinder -d postgres -Atc "SELECT 1 FROM pg_database WHERE datname='$DB'" | grep -q '^1$'; then
  docker exec "$PG" createdb -U stockfinder "$DB"
  docker exec "$PG" pg_dump -U stockfinder -d stockfinder --no-owner --no-acl |
    docker exec -i "$PG" psql -v ON_ERROR_STOP=1 -U stockfinder -d "$DB" >/dev/null
fi
install -d -m 0777 artifacts/ldlc artifacts/ldlc/reviews artifacts/ldlc/checkpoints
# Read the password without writing it to disk or including it in command arguments.
python3 - "$ROOT" "$@" <<'PY'
import json, os, subprocess, sys
root = sys.argv[1]
arguments = sys.argv[2:]
pipeline = '--pipeline' in arguments
if pipeline:
    arguments.remove('--pipeline')
script = 'scripts/ldlc_pipeline.py' if pipeline else 'scripts/ldlc_local.py'
output = ['--directory', '/output'] if pipeline else ['--output', '/output']
volume = '/artifacts/ldlc:/output' if pipeline else '/artifacts/ldlc/checkpoints:/output'
info = json.loads(subprocess.check_output(['docker', 'inspect', 'stockfinder-v2-postgres-1']))[0]
password = next(v.split('=', 1)[1] for v in info['Config']['Env'] if v.startswith('POSTGRES_PASSWORD='))
subprocess.run(['docker', 'run', '--rm', '--network', 'stockfinder-v2_default', '-e', 'PGPASSWORD',
                '-v', root + ':/app:ro', '-v', root + volume,
                'stockfinder-v2-sync-agent', 'python', script,
                '--database-url', 'postgresql://stockfinder@postgres/stockfinder_ldlc_local',
                *output, *arguments], env={**os.environ, 'PGPASSWORD': password}, check=True)
PY
