#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
root=$PWD
name=stockfinder-coolmod-inventory
if docker container inspect "$name" >/dev/null 2>&1; then
  if [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ]; then
    echo 'Inventory already running'
  else
    docker start "$name" >/dev/null
    echo "Inventory resumed: $name"
  fi
  exit
fi
mkdir -p artifacts
docker run -d --name "$name" --init --memory 768m --cpus 1 --pids-limit 256 \
  --cap-drop ALL --security-opt no-new-privileges:true --shm-size 128m \
  -v "$root/nas/scrapers:/work/scrapers:ro" \
  -v "$root/scripts:/work/scripts:ro" -v "$root/node_modules:/work/node_modules:ro" \
  -v "$root/data:/work/data:ro" -v "$root/artifacts:/work/artifacts" \
  -w /work -e COOLMOD_INVENTORY=/work/artifacts/coolmod-inventory.json \
  -e COOLMOD_PAGES=200 mcr.microsoft.com/playwright:v1.56.1-noble \
  sh -c 'node /work/scrapers/coolmod-categories.cjs; result=$?; python3 /work/scripts/coolmod_filter.py /work/artifacts/coolmod-inventory.json || result=$?; exit "$result"' >/dev/null
echo "Inventory started: $name"
