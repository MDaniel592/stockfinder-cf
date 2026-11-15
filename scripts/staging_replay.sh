#!/usr/bin/env bash
# Carga la base Supabase (modelo de lectura de staging) con el estado actual del outbox del NAS, igual que
# scripts/local_site.sh hace con la D1 local: exportación SOLO LECTURA de PostgreSQL y reenvío firmado a
# /internal/sync de un Worker de STAGING (nunca producción; el sync-agent de producción no se toca).
#
#   bash scripts/staging_replay.sh --local    # Worker de staging local (wrangler dev + Hyperdrive local -> Supabase)
#   bash scripts/staging_replay.sh --remote   # Worker desplegado stockfinder-staging (requiere Hyperdrive, ver docs)
#   bash scripts/staging_replay.sh --stop     # detiene el Worker local
#
# Secretos (de .env.supabase.local, nunca impresos): STAGING_SYNC_SECRET y SUPABASE_WORKER_URL_SESSION.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
DIR=/tmp/sf-staging
PORT=${PORT:-8810}
IMAGE=${NODE_IMAGE:-mcr.microsoft.com/playwright:v1.56.1-noble}
STAGING_URL=${STAGING_URL:-https://stockfinder-staging.stockfinder.workers.dev}
dc() { docker compose --env-file .env -f infra/compose/compose.yaml "$@"; }
secret() { grep -E "^$1=" .env.supabase.local | head -1 | cut -d= -f2-; }
mkdir -p "$DIR"; chmod 700 "$DIR"

stop() { docker rm -f sf-staging-dev >/dev/null 2>&1 || true; }

start_local() {
  stop
  umask 077
  # Variables del proceso wrangler (cadena de Hyperdrive local) y del Worker (.env de dev): ficheros 0600 fuera del repo.
  printf 'CLOUDFLARE_HYPERDRIVE_LOCAL_CONNECTION_STRING_HYPERDRIVE=%s\nWRANGLER_SEND_METRICS=false\n' "$(secret SUPABASE_WORKER_URL_SESSION)" > "$DIR/docker.env"
  printf 'SYNC_SECRET=%s\n' "$(secret STAGING_SYNC_SECRET)" > "$DIR/dev.env"
  docker run -d --name sf-staging-dev --network host --env-file "$DIR/docker.env" -v "$ROOT:/work" -v "$DIR:/staging" -w /work "$IMAGE" \
    npx wrangler dev --config wrangler.staging.jsonc --ip 127.0.0.1 --port "$PORT" --env-file /staging/dev.env --persist-to /staging/state >/dev/null
  for _ in $(seq 90); do
    curl -sf "http://127.0.0.1:$PORT/api/v1/catalog/status" >/dev/null && break
    sleep 1
  done
  curl -sf "http://127.0.0.1:$PORT/api/v1/catalog/status" >/dev/null || { docker logs --tail 30 sf-staging-dev 2>&1 | sed -E 's#postgres(ql)?://[^ ]+#<url>#g'; exit 1; }
}

load() {
  local endpoint=$1
  install -d -m 0777 "$DIR/export"
  dc run --rm -T --no-deps -v "$ROOT/scripts:/app/scripts:ro" -v "$DIR/export:/out" \
    sync-agent python scripts/local_replay.py export --out /out/events.json >/dev/null
  LOCAL_SYNC_SECRET=$(secret STAGING_SYNC_SECRET) python3 scripts/local_replay.py push --in "$DIR/export/events.json" --endpoint "$endpoint"
  # Histórico de precios: mismo generador que producción (solo lectura), aplicado como administrador en Supabase,
  # y después se quitan observaciones consecutivas repetidas (la reproducción añade la última).
  dc run --rm -T --no-deps -v "$ROOT/scripts:/app/scripts:ro" sync-agent python scripts/backfill_price_history.py > "$DIR/price-history.sql"
  { echo 'SET search_path = stockfinder;'; cat "$DIR/price-history.sql"
    echo 'DELETE FROM offer_price_history h USING (SELECT id, LAG(price_cents) OVER w AS lp, LAG(in_stock) OVER w AS ls FROM offer_price_history
      WINDOW w AS (PARTITION BY offer_id ORDER BY observed_at, id)) x WHERE h.id = x.id AND x.lp = h.price_cents AND x.ls = h.in_stock;'
  } > "$DIR/price-history.pg.sql"
  python3 scripts/supabase.py sql-file "$DIR/price-history.pg.sql"
}

case "${1:-}" in
  --stop) stop; echo "Worker de staging local detenido." ;;
  --local) start_local; load "http://127.0.0.1:$PORT/internal/sync"; echo "Staging local: http://127.0.0.1:$PORT/ (docker logs sf-staging-dev)" ;;
  --remote) load "$STAGING_URL/internal/sync" ;;
  *) sed -n '2,10p' "$0"; exit 64 ;;
esac
