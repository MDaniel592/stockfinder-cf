"""Explicit admin-only synthetic backfill via D1 API (never used by scrapers)."""
import argparse
import json
from pathlib import Path
import urllib.request
from generate_seed import events, sql

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--confirm-synthetic-backfill', action='store_true')
args = parser.parse_args()
if not args.confirm_synthetic_backfill:
    parser.error('Pass --confirm-synthetic-backfill to modify the configured remote D1')
config = json.loads((ROOT/'wrangler.jsonc').read_text())
if config.get('vars', {}).get('DATA_MODE') != 'synthetic':
    raise SystemExit('Refusing to seed a non-synthetic environment')
credentials = dict(l.split('=',1) for l in (ROOT/'.env.cloudflare.local').read_text().splitlines() if l and not l.startswith('#'))
binding = config['d1_databases'][0]
url = ('https://api.cloudflare.com/client/v4/accounts/'+credentials['CLOUDFLARE_ACCOUNT_ID']+
       '/d1/database/'+binding['database_id']+'/query')
def query(statement):
    req = urllib.request.Request(url,data=json.dumps({'sql':statement}).encode(),headers={
        'Authorization':'Bearer '+credentials['CLOUDFLARE_API_TOKEN'],'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=30) as response: result = json.load(response)
    if not result.get('success') or any(not item.get('success') for item in result.get('result', [])):
        raise RuntimeError('D1 query failed')
    return result['result']
count = query('SELECT count(*) AS n FROM catalog_products')[0]['results'][0]['n']
if count:
    raise SystemExit('Refusing to seed a populated database; use PostgreSQL outbox/backfill instead')
statements = sql(events()).splitlines()
for index in range(0,len(statements),20): query('\n'.join(statements[index:index+20]))
print('120 synthetic products seeded in remote D1')
