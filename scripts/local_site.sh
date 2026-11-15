#!/usr/bin/env bash
# Vista previa local de StockFinder en la red de casa, con los datos actuales de PostgreSQL.
# No consume D1 de producción ni toca servicios en marcha: usa una D1 local propia en /tmp/sf-local.
#
#   bash scripts/local_site.sh            # compila la web, recrea la D1 local, carga datos y arranca
#   bash scripts/local_site.sh --no-build # sin recompilar apps/web/dist
#   bash scripts/local_site.sh --refresh  # solo recarga los datos en el servidor ya arrancado
#   bash scripts/local_site.sh --stop     # detiene la vista previa
#
# Abrir desde el PC: http://<IP-del-NAS>:${PORT:-8800}/
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
PORT=${PORT:-8800}
DIR=/tmp/sf-local
STATE=$DIR/state
export PATH=/tmp/stockfinder-toolchain/bin:$PATH
dc() { docker compose --env-file .env -f infra/compose/compose.yaml "$@"; }
mkdir -p "$DIR"

# Una sola ejecución a la vez: dos arranques simultáneos se pisan los datos y el puerto
exec 9>"$DIR/.lock"
if ! flock -n 9; then
  echo "Otra ejecución de local_site.sh está en curso; reinténtalo cuando termine." >&2
  exit 75
fi

stop() {
  if [ -f "$DIR/pid" ]; then
    pkill -TERM -P "$(cat "$DIR/pid")" 2>/dev/null || true
    kill "$(cat "$DIR/pid")" 2>/dev/null || true
    rm -f "$DIR/pid"
  fi
}

load_data() {
  # 1) exportar el outbox (solo lectura) dentro del contenedor; 2) reenviarlo al Worker local desde el host
  # El contenedor corre con otro usuario: carpeta de exportación propia (solo datos públicos del catálogo)
  install -d -m 0777 "$DIR/export"
  dc run --rm -T --no-deps -v "$ROOT/scripts:/app/scripts:ro" -v "$DIR/export:/out" \
    sync-agent python scripts/local_replay.py export --out /out/events.json >/dev/null
  set -a; . "$DIR/.dev.vars"; set +a
  LOCAL_SYNC_SECRET=$SYNC_SECRET python3 scripts/local_replay.py push \
    --in "$DIR/export/events.json" --endpoint "http://127.0.0.1:$PORT/internal/sync"
  # Histórico de precios (mismo generador que producción, solo lectura)
  dc run --rm -T --no-deps -v "$ROOT/scripts:/app/scripts:ro" \
    sync-agent python scripts/backfill_price_history.py > "$DIR/price-history.sql"
  npx wrangler d1 execute DB --local --config "$DIR/wrangler.jsonc" --persist-to "$STATE" \
    --file "$DIR/price-history.sql" >/dev/null && echo '{"event":"local_price_history","ok":true}'
}

case "${1:-}" in
  --stop) stop; echo "Vista previa detenida."; exit 0 ;;
  --refresh) load_data; exit 0 ;;
esac

# Secreto propio de la vista previa (nunca el de producción)
if [ ! -f "$DIR/.dev.vars" ]; then
  umask 077; echo "SYNC_SECRET=$(openssl rand -hex 32)" > "$DIR/.dev.vars"
fi

# Configuración derivada de wrangler.jsonc con rutas absolutas y D1 local
python3 - "$ROOT" "$DIR" <<'PY'
import json, sys
root, out = sys.argv[1], sys.argv[2]
import re
cfg = json.loads(re.sub(r'(?m)^\s*//.*$', '', open(f'{root}/wrangler.jsonc').read()))  # JSONC: comment lines
cfg.pop('$schema', None); cfg.pop('observability', None)
cfg['name'] = 'stockfinder-local'
cfg['main'] = f"{root}/{cfg['main']}"
cfg['assets']['directory'] = f"{root}/{cfg['assets']['directory']}"
for db in cfg['d1_databases']:
    db['database_id'] = '00000000-0000-0000-0000-000000000000'
    db['migrations_dir'] = f"{root}/{db['migrations_dir']}"
json.dump(cfg, open(f'{out}/wrangler.jsonc', 'w'), indent=2)
PY

[ "${1:-}" = "--no-build" ] || npx vite build --config apps/web/vite.config.ts >/dev/null

stop
rm -rf "$STATE"
npx wrangler d1 migrations apply DB --local --config "$DIR/wrangler.jsonc" --persist-to "$STATE" >/dev/null

# El Worker lee .dev.vars junto a su configuración
nohup npx wrangler dev --config "$DIR/wrangler.jsonc" --ip 0.0.0.0 --port "$PORT" \
  --persist-to "$STATE" --var STALE_AFTER_SECONDS:86400 > "$DIR/wrangler.log" 2>&1 &
echo $! > "$DIR/pid"
for _ in $(seq 60); do
  curl -sf "http://127.0.0.1:$PORT/api/v1/catalog/status" >/dev/null && break
  sleep 1
done
curl -sf "http://127.0.0.1:$PORT/api/v1/catalog/status" >/dev/null || { tail -20 "$DIR/wrangler.log"; exit 1; }

load_data
IP=$(hostname -I | awk '{print $1}')
echo "Vista previa lista: http://$IP:$PORT/  (registro: $DIR/wrangler.log)"
