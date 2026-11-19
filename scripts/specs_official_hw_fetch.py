"""Descarga fichas oficiales (para scripts/specs_official_*.py). Se ejecuta en el corredor Docker con curl_cffi.
  docker compose -f infra/compose/manufacturer-scrape.yaml run --rm scraper python /work/scripts/specs_official_hw_fetch.py <urls.json> <outdir>
urls.json: lista de URLs. Escribe <outdir>/<sha1(url)>.html y <outdir>/manifest.jsonl.
Un hilo por dominio, >= 1.5 s entre peticiones al mismo dominio; sin saltar WAF: lo que falla queda como error en el manifiesto."""
import sys, json, hashlib, time, threading, collections, os
from urllib.parse import urlparse
from curl_cffi import requests

urls = json.load(open(sys.argv[1])); out = sys.argv[2]
os.makedirs(out, exist_ok=True)
DELAY = float(os.environ.get('DELAY', '1.5'))
lock = threading.Lock()
done = set()
mp = os.path.join(out, 'manifest.jsonl')
if os.path.exists(mp):
    for l in open(mp):
        try:
            r = json.loads(l)
            if r.get('status') == 200: done.add(r['url'])
        except Exception: pass
by = collections.defaultdict(list)
for u in urls:
    if u not in done: by[urlparse(u).netloc].append(u)

def work(host, lst):
    s = requests.Session()
    for u in lst:
        h = hashlib.sha1(u.encode()).hexdigest()
        rec = {'url': u, 'sha1': h, 'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
        try:
            r = s.get(u, impersonate='chrome', timeout=40)
            rec['status'] = r.status_code; rec['bytes'] = len(r.content)
            if r.status_code == 200:
                open(os.path.join(out, h + '.html'), 'wb').write(r.content)
        except Exception as e:
            rec['status'] = 0; rec['error'] = str(e)[:200]
        with lock:
            open(mp, 'a').write(json.dumps(rec) + '\n')
        time.sleep(DELAY)

ts = [threading.Thread(target=work, args=(h, l)) for h, l in by.items()]
[t.start() for t in ts]; [t.join() for t in ts]
print('hecho', sum(len(l) for l in by.values()))
