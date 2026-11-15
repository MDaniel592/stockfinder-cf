"""Synthetic lab: real PG -> Python agent -> local Worker/D1 -> NAS-off read.

Requires the local Worker on 127.0.0.1:8787, the project's PostgreSQL
container, its NAS image, and a precreated `stockfinder_lab` database.
Only this project's PostgreSQL container is briefly stopped and restarted.
"""
import json
import os
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
config = dict(line.split('=',1) for line in (ROOT/'.env').read_text().splitlines())
local = dict(line.split('=',1) for line in (ROOT/'.dev.vars').read_text().splitlines())
ip = subprocess.check_output(['docker','inspect','-f','{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}','stockfinder-v2-postgres-1'],text=True).strip()
env = {**os.environ, 'DATABASE_URL':f"postgresql://stockfinder:{config['POSTGRES_PASSWORD']}@{ip}:5432/stockfinder_lab",
       'SYNC_ENDPOINT':'http://127.0.0.1:8787/internal/sync', 'SYNC_SECRET':local['SYNC_SECRET']}
def run(*args):
    subprocess.run(['docker','run','--rm','--network','host','--env','DATABASE_URL','--env','SYNC_ENDPOINT','--env','SYNC_SECRET',
                    'stockfinder-v2-sync-agent','python',*args],env=env,check=True)
def read(path):
    request = urllib.request.Request('http://127.0.0.1:8787/api/v1/'+path,headers={'Cache-Control':'no-cache'})
    with urllib.request.urlopen(request,timeout=10) as response:return json.load(response)
def drain():
    run('-c', '''import psycopg,os
from nas.sync_agent import run_once
with psycopg.connect(os.environ['DATABASE_URL'],autocommit=True) as conn:
 while run_once(conn,os.environ['SYNC_ENDPOINT'],os.environ['SYNC_SECRET'],True):pass
''')
run('nas/catalog.py','migrate')
run('nas/catalog.py','seed')
drain()
run('nas/catalog.py','change-price','--offer','offer-0000-1','--price-cents','49900')
drain()
offer = next(o for o in read('products/part-0000/offers')['data'] if o['id']=='offer-0000-1')
assert offer['price_cents']==49900
try:
    subprocess.run(['docker','stop','stockfinder-v2-postgres-1'],check=True,capture_output=True)
    catalogue=read('products?sort=featured')
    assert catalogue['total']==120
    assert catalogue['data'][0]['min_price_cents']==49900
finally:
    subprocess.run(['docker','start','stockfinder-v2-postgres-1'],check=True,capture_output=True)
report=dict(environment='local-worker-d1-real-postgres',products=catalogue['total'],
            price_cents=49900,pg_to_outbox_to_d1=True,read_with_postgres_stopped=True,
            remote_network_verification=False)
(ROOT/'artifacts/lab-result.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
