#!/usr/bin/env bash
# Ejecuta la suite del Worker (tests/edge*.test.ts: edge, edge-aggregates, edge-pg) contra un PostgreSQL 17 DESECHABLE.
# Nunca usa el PostgreSQL del NAS ni Supabase: crea un contenedor postgres:17-alpine con datos en tmpfs, en una red
# Docker propia y sin publicar puertos, y lo borra al terminar (también si falla).
#
#   bash scripts/test_pg.sh                 # suite completa en modo Postgres
#   bash scripts/test_pg.sh -t 'Price'      # argumentos extra para vitest
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
ID="sf-pgtest-$$-$RANDOM"
NET="$ID-net"; PG="$ID-pg"
PASS=$(openssl rand -hex 16)
IMAGE=${TEST_IMAGE:-mcr.microsoft.com/playwright:v1.56.1-noble}
cleanup() { docker rm -f "$PG" >/dev/null 2>&1 || true; docker network rm "$NET" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker network create "$NET" >/dev/null
docker run -d --name "$PG" --network "$NET" -e POSTGRES_PASSWORD="$PASS" --tmpfs /var/lib/postgresql/data postgres:17-alpine >/dev/null
for _ in $(seq 60); do
  docker exec "$PG" pg_isready -U postgres -h 127.0.0.1 >/dev/null 2>&1 && break
  sleep 1
done
docker run --rm --network "$NET" -v "$ROOT:/work" -w /work -e TEST_PG_URL="postgres://postgres:$PASS@$PG:5432/postgres" "$IMAGE" \
  sh -c 'npm run build:worker >/dev/null && npx vitest run tests/edge "$@"' sh "$@"
