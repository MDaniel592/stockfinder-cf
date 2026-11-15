#!/usr/bin/env bash
# Compila y despliega el Worker (assets incluidos) sin depender de un Node instalado en el host ni de /tmp.
# Usa la imagen oficial de Node en Docker; las credenciales las lee scripts/cloudflare.py de .env.cloudflare.local.
#   bash scripts/deploy.sh            # npm run build + wrangler deploy
#   bash scripts/deploy.sh build      # solo compilar
set -euo pipefail
cd "$(dirname "$0")/.."
IMG=${NODE_IMAGE:-node:22-bookworm}
CMD='npm run build && python3 scripts/cloudflare.py deploy'
[ "${1:-}" = "build" ] && CMD='npm run build'
exec docker run --rm -v "$PWD":/app -w /app -e HOME=/tmp "$IMG" sh -c "$CMD"
