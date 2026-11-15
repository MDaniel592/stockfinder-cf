#!/usr/bin/env bash
# Herramienta interna de specs incompletas (scripts/spec_gaps_web.py), solo en la red de casa. No es la web pública.
#
#   bash scripts/spec_gaps.sh          # arranca (o reinicia) en http://<IP-del-NAS>:${PORT:-8801}/
#   bash scripts/spec_gaps.sh --stop   # la detiene
set -euo pipefail
cd "$(dirname "$0")/.."
PORT=${PORT:-8801}
DIR=/tmp/sf-spec-gaps
mkdir -p "$DIR"
stop() {
  if [ -f "$DIR/pid" ]; then kill "$(cat "$DIR/pid")" 2>/dev/null || true; rm -f "$DIR/pid"; fi
}
if [ "${1:-}" = "--stop" ]; then stop; echo "Herramienta detenida."; exit 0; fi
stop
# Solo la IP de la LAN: nunca 0.0.0.0
IP=${HOST_IP:-$(hostname -I | awk '{print $1}')}
nohup python3 scripts/spec_gaps_web.py --host "$IP" --port "$PORT" > "$DIR/server.log" 2>&1 &
echo $! > "$DIR/pid"
for _ in $(seq 30); do curl -sf "http://$IP:$PORT/" >/dev/null && break; sleep 1; done
curl -sf "http://$IP:$PORT/" >/dev/null || { tail -20 "$DIR/server.log"; exit 1; }
echo "Specs incompletas: http://$IP:$PORT/  (registro: $DIR/server.log)"
