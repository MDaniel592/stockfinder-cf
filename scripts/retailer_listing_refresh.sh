#!/usr/bin/env bash
# Fast price/stock refresh from category listings into the isolated local DB.
# Usage: scripts/retailer_listing_refresh.sh ldlc|neobyte [--dry-run] [--verify N] [--categories gpu ...]
# Uses the same checkpoint directories as ldlc_local.sh / neobyte_local.sh (SHARD supported for Neobyte).
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
RETAILER=${1:?usage: retailer_listing_refresh.sh ldlc|neobyte [options]}
shift
case "$RETAILER" in
  ldlc) DB=stockfinder_ldlc_local; CHECK=/artifacts/ldlc/checkpoints ;;
  neobyte) DB=stockfinder_neobyte_local; CHECK=/artifacts/neobyte/checkpoints${SHARD:+/$SHARD} ;;
  *) echo "unknown retailer: $RETAILER" >&2; exit 2 ;;
esac
PG=stockfinder-v2-postgres-1
[ -d "$ROOT$CHECK" ] || { echo "missing checkpoint directory $ROOT$CHECK (run the full crawler once first)" >&2; exit 1; }
# Read the password without writing it to disk or including it in command arguments.
python3 - "$ROOT" "$RETAILER" "$DB" "$CHECK" "$@" <<'PY'
import json, os, subprocess, sys
root, retailer, db, check, *arguments = sys.argv[1:]
info = json.loads(subprocess.check_output(['docker', 'inspect', 'stockfinder-v2-postgres-1']))[0]
password = next(v.split('=', 1)[1] for v in info['Config']['Env'] if v.startswith('POSTGRES_PASSWORD='))
subprocess.run(['docker', 'run', '--rm', '--network', 'stockfinder-v2_default', '-e', 'PGPASSWORD',
                '-v', root + ':/app:ro', '-v', root + check + ':/output',
                'stockfinder-v2-sync-agent', 'python', 'scripts/retailer_listing_refresh.py',
                '--retailer', retailer, '--database-url', f'postgresql://stockfinder@postgres/{db}',
                '--output', '/output', *arguments], env={**os.environ, 'PGPASSWORD': password}, check=True)
PY
