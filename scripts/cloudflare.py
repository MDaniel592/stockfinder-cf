"""Load local Cloudflare credentials without shell evaluation or printing them."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
env = dict(os.environ)
config = ROOT/'.env.cloudflare.local'
if config.exists():
    for line in config.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith('#'):
            key, value = line.split('=', 1)
            if key in ('CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID'):
                env[key] = value.strip()
env['WRANGLER_SEND_METRICS'] = 'false'
raise SystemExit(subprocess.call(['node', str(ROOT/'node_modules/wrangler/bin/wrangler.js'), *sys.argv[1:]], cwd=ROOT, env=env))
