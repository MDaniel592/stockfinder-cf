#!/bin/sh
# Scraper de StockFinder en Railway. Sin volumenes compartidos: abre un tunel SSH hacia PostgreSQL (solo lectura) y
# sincroniza por rsync/SSH con el nucleo (Azure), que sigue siendo el unico que escribe en la base y publica.
#   - tunel : sftunnel@AZURE_HOST  (clave con permitopen=100.80.73.54:5432; sin shell ni otros destinos)
#   - ficheros: sfsync@AZURE_HOST  (clave con rrsync restringido a /srv/stockfinder)
#   - BD    : rol railway_ro, solo SELECT en retailer_review_queue
# Variables: SERVICE, AZURE_HOST, SSH_KEY_B64 (secreto), DB_PASSWORD (secreto, si el servicio usa BD),
#            AZURE_KNOWN_HOSTS (clave publica de host de Azure, recomendada), SSH_PORT, DB_TARGET, SYNC_SECONDS.
# SERVICE: pccomponentes-offers | pccomponentes-discovery | coolmod-offers | selftest
set -eu

SERVICE="${SERVICE:?define SERVICE}"
AZURE_HOST="${AZURE_HOST:?define AZURE_HOST}"
: "${SSH_KEY_B64:?define SSH_KEY_B64}"
SSH_PORT="${SSH_PORT:-22}"
DB_TARGET="${DB_TARGET:-100.80.73.54:5432}"
SYNC_SECONDS="${SYNC_SECONDS:-60}"
SSH_DIR="${HOME:-/root}/.ssh"

mkdir -p "$SSH_DIR" /output /flow/targets
chmod 700 "$SSH_DIR"
printf '%s' "$SSH_KEY_B64" | base64 -d > "$SSH_DIR/id"
chmod 600 "$SSH_DIR/id"
if [ -n "${AZURE_KNOWN_HOSTS:-}" ]; then
  printf '%s\n' "$AZURE_KNOWN_HOSTS" > "$SSH_DIR/known_hosts"
  STRICT=yes                      # clave de host fijada: no se confia en la primera conexion
else
  STRICT=accept-new
  echo "AVISO: sin AZURE_KNOWN_HOSTS (se acepta la clave de host en la primera conexion)" >&2
fi
SSH="ssh -p $SSH_PORT -i $SSH_DIR/id -o IdentitiesOnly=yes -o UserKnownHostsFile=$SSH_DIR/known_hosts -o StrictHostKeyChecking=$STRICT -o BatchMode=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 -o ConnectTimeout=15"
REMOTE="sfsync@$AZURE_HOST"

NEEDS_TUNNEL=0; TARGETS=""; STATE_DIRS=""; OUT_DIR=""; OUT_GLOB=""
case "$SERVICE" in
  pccomponentes-offers)
    NEEDS_TUNNEL=1; TARGETS="pccomponentes.json"; STATE_DIRS="pccomponentes-offers-state"
    OUT_DIR="offers"; OUT_GLOB="pccomponentes*.json"
    export PCC_REFRESH_INTERVAL_SECONDS="${PCC_REFRESH_INTERVAL_SECONDS:-10800}"
    export PCC_TARGETS_FILE=/flow/targets/pccomponentes.json
    export PCC_OUTPUT_FILE=/output/offers/pccomponentes.json
    export PCC_STATE_FILE=/output/pccomponentes-offers-state/state.json
    export PCC_REQUEST_DELAY_SECONDS="${PCC_REQUEST_DELAY_SECONDS:-2,3}"
    export PCC_RATE_LIMIT_COOLDOWNS="${PCC_RATE_LIMIT_COOLDOWNS:-60,180}"
    export PCC_RETRY_FAILED_CHUNKS="${PCC_RETRY_FAILED_CHUNKS:-1}"
    export PCC_MARKETPLACE_GONE_AS_UNAVAILABLE="${PCC_MARKETPLACE_GONE_AS_UNAVAILABLE:-1}"
    set -- python scripts/pccomponentes_refresh.py ;;
  pccomponentes-discovery)
    NEEDS_TUNNEL=1; STATE_DIRS="pccomponentes-state"; OUT_DIR="pccomponentes"; OUT_GLOB="batch.json"
    set -- python scripts/pccomponentes_auto.py --interval 43200 --pages 40 --max-candidates 190 --max-pending 3000 \
      --delay 1.1 --output /output/pccomponentes/batch.json --state /output/pccomponentes-state/state.json ;;
  coolmod-offers)
    TARGETS="coolmod.json"; STATE_DIRS="coolmod-offers-state"; OUT_DIR="offers"; OUT_GLOB="coolmod*.json"
    export COOLMOD_AJAX_REFRESH=1
    export COOLMOD_TARGETS_FILE=/flow/targets/coolmod.json
    export COOLMOD_DATA_DIR=/app/data
    export COOLMOD_OUTPUT_FILE=/output/offers/coolmod.json
    export COOLMOD_REFRESH_STATE_FILE=/output/coolmod-offers-state/state.json
    export COOLMOD_REFRESH_INTERVAL_SECONDS="${COOLMOD_REFRESH_INTERVAL_SECONDS:-3600}"
    export COOLMOD_AJAX_CHUNK="${COOLMOD_AJAX_CHUNK:-100}"
    export COOLMOD_BLOCKED_SECONDS="${COOLMOD_BLOCKED_SECONDS:-7200}"
    set -- python scripts/coolmod_refresh.py ;;
  selftest)
    NEEDS_TUNNEL=1; TARGETS="pccomponentes.json"; STATE_DIRS="pccomponentes-state"; OUT_DIR="pccomponentes"; OUT_GLOB="batch.json"
    set -- python /app/railway/selftest.py ;;
  *) echo "SERVICE desconocido: $SERVICE" >&2; exit 2 ;;
esac

if [ "$NEEDS_TUNNEL" = 1 ]; then
  : "${DB_PASSWORD:?define DB_PASSWORD}"
  export DATABASE_URL="postgresql://railway_ro:${DB_PASSWORD}@127.0.0.1:5432/stockfinder"
fi

pull_targets() { [ -z "$TARGETS" ] || rsync -t -e "$SSH" "$REMOTE:flow/targets/$TARGETS" /flow/targets/; }

pull_all() {  # al arrancar: estado y salidas propias vuelven desde el nucleo (espejo), y los objetivos
  for d in $STATE_DIRS; do
    mkdir -p "/output/$d"; rsync -rt -e "$SSH" "$REMOTE:retailer/$d/" "/output/$d/" || return 1
  done
  if [ -n "$OUT_DIR" ]; then
    mkdir -p "/output/$OUT_DIR"
    rsync -rt --include="$OUT_GLOB" --exclude='*' -e "$SSH" "$REMOTE:retailer/$OUT_DIR/" "/output/$OUT_DIR/" || return 1
  fi
  pull_targets
}

push_all() {  # estado y salidas propias hacia el nucleo; nunca borra si no hay ningun fichero local de salida
  for d in $STATE_DIRS; do
    rsync -rt --exclude='*.tmp' -e "$SSH" "/output/$d/" "$REMOTE:retailer/$d/" || return 1
  done
  if [ -n "$OUT_DIR" ] && ls /output/$OUT_DIR/$OUT_GLOB >/dev/null 2>&1; then
    rsync -rt --delete --include="$OUT_GLOB" --exclude='*' -e "$SSH" "/output/$OUT_DIR/" "$REMOTE:retailer/$OUT_DIR/" || return 1
  fi
}

PIDS=""
cleanup() { for p in $PIDS; do kill "$p" 2>/dev/null || true; done; }
trap 'cleanup; exit 143' TERM INT

echo "[$SERVICE] sincronizando desde el nucleo..."
pull_all || { echo "[$SERVICE] no se pudo sincronizar al arrancar" >&2; exit 1; }

if [ "$NEEDS_TUNNEL" = 1 ]; then
  ( while true; do
      $SSH -N -o ExitOnForwardFailure=yes -L "127.0.0.1:5432:$DB_TARGET" "sftunnel@$AZURE_HOST" || true
      sleep 5
    done ) &
  PIDS="$PIDS $!"
  i=0
  until python -c "import socket;socket.create_connection(('127.0.0.1',5432),2).close()" 2>/dev/null; do
    i=$((i+1)); [ "$i" -gt 30 ] && { echo "[$SERVICE] el tunel no se abre" >&2; cleanup; exit 1; }; sleep 1
  done
  echo "[$SERVICE] tunel a PostgreSQL abierto"
fi

( while true; do sleep "$SYNC_SECONDS"; pull_targets || echo "[$SERVICE] aviso: objetivos sin actualizar" >&2; push_all || echo "[$SERVICE] aviso: push fallido" >&2; done ) &
PIDS="$PIDS $!"

cd /app
"$@" &
MAIN=$!
PIDS="$PIDS $MAIN"
rc=0; wait "$MAIN" || rc=$?
push_all || true
cleanup
exit "$rc"
