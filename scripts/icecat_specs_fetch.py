"""Consulta Icecat (host de la VM, no el contenedor) para productos con specs faltantes y guarda respuestas resumidas.
Uso: python3 scripts/icecat_specs_fetch.py <dir work> <max consultas> [--cats a,b]
Entradas: <dir>/candidates.jsonl (exportado de la BD). Salidas: <dir>/results.jsonl (una linea por producto intentado,
con todas las caracteristicas EN resumidas), <dir>/brand_stats.json. Reanudable: no repite un producto ya intentado.
Reglas: >=2 s entre peticiones; HTTP 403/404 se registran, no se infiere ausencia ni se reintenta; se detiene ante 401/429/5xx.
Credenciales: se leen de /opt/stockfinder/.env.icecat.local y nunca se imprimen."""
import json, os, re, sys, time, sqlite3, datetime, urllib.request, urllib.parse, urllib.error, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from icecat_spec_map import TARGET_KEYS

API = 'https://live-origin.icecat.biz/api'
OBS_DB = '/opt/stockfinder/work/research-evidence-20261115/evidence.sqlite3'
PRIORITY = {'storage': 0, 'cooler': 1, 'motherboard': 2, 'case': 3, 'psu': 4, 'ram': 5}
norm = lambda s: re.sub(r'\s+', '', s or '').upper()


def config():
    cfg = {}
    for line in open('/opt/stockfinder/.env.icecat.local', encoding='utf-8'):
        k, sep, v = line.partition('=')
        if sep and k in ('ICECAT_API_TOKEN', 'ICECAT_CONTENT_TOKEN'):
            cfg[k] = v.strip()
    return cfg


def feature_rows(groups):
    rows = []
    for g in groups or []:
        for f in g.get('Features') or []:
            ft = f.get('Feature') or {}
            rows.append({'name': (ft.get('Name') or {}).get('Value'), 'fid': ft.get('ID'), 'pv': f.get('PresentationValue'),
                         'raw': f.get('RawValue'), 'unit': ((ft.get('Measure') or {}).get('Signs') or {}).get('_'),
                         'type': f.get('Type')})
    return rows


def query(cfg, brand=None, mpn=None, gtin=None):
    params = {'lang': 'EN', 'content': 'generalinfo,featuregroups'}
    params.update({'GTIN': gtin} if gtin else {'Brand': brand, 'ProductCode': mpn})
    req = urllib.request.Request(API + '?' + urllib.parse.urlencode(params), headers={
        'Accept': 'application/json', 'User-Agent': 'StockFinder specs research/1.0',
        'api-token': cfg['ICECAT_API_TOKEN'], 'content-token': cfg['ICECAT_CONTENT_TOKEN']})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            payload = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read(8192).decode())
            sc = d.get('StatusCode', d.get('statusCode'))
        except Exception:
            sc = None
        return {'status': 'http_%s' % e.code, 'status_code': sc}
    except (urllib.error.URLError, TimeoutError) as e:
        return {'status': 'net_error', 'detail': type(e).__name__}
    info = (payload.get('data') or {}).get('GeneralInfo') if isinstance(payload.get('data'), dict) else None
    if payload.get('msg') != 'OK' or not info:
        return {'status': 'invalid', 'status_code': payload.get('statusCode')}
    return {'status': 'ok', 'icecat_id': info.get('IcecatId'), 'code': info.get('BrandPartCode'), 'icecat_brand': info.get('Brand'),
            'title': info.get('Title'), 'gtins': info.get('GTIN') or [], 'features': feature_rows(payload['data'].get('FeaturesGroups'))}


def main():
    wd, limit = sys.argv[1], int(sys.argv[2])
    cats = set(sys.argv[sys.argv.index('--cats') + 1].split(',')) if '--cats' in sys.argv else set(PRIORITY)
    cands = [json.loads(l) for l in open(f'{wd}/candidates.jsonl', encoding='utf-8') if l.strip()]
    rpath = f'{wd}/results.jsonl'
    done = {}
    if os.path.exists(rpath):
        for l in open(rpath, encoding='utf-8'):
            r = json.loads(l)
            done[r['product_id']] = r
    stats = collections.defaultdict(collections.Counter)
    for r in done.values():
        if r.get('queried'):
            stats[r['brand']][r['status']] += 1
    obs = collections.defaultdict(list)  # reutilizacion gratuita de observaciones previas
    try:
        con = sqlite3.connect(OBS_DB)
        for br, code, feats, ic, gt, title in con.execute(
                'select brand,brand_part_code,features_json,icecat_id,returned_gtins_json,title from icecat_observations where features_json is not null'):
            obs[(norm(br), norm(code))].append((feats, ic, gt, title))
    except Exception:
        pass
    out = open(rpath, 'a', encoding='utf-8')

    def gaps(c):
        return sum(1 for k in TARGET_KEYS.get(c['cat'], ()) if k not in c['specs'])

    todo = collections.defaultdict(list)
    for c in cands:
        if c['cat'] in cats and c['id'] not in done and gaps(c) > 0 and len(c['mpn']) >= 4:
            todo[c['brand']].append(c)
    for b in todo:
        todo[b].sort(key=lambda c: (PRIORITY[c['cat']], -gaps(c)))
    big = {b: len(v) for b, v in todo.items()}
    cfg, n, last = config(), 0, 0.0
    while n < limit and todo:
        def score(b):
            s = stats[b]
            att = sum(s.values())
            p = (s['ok'] + 0.5) / (att + 1)
            if att >= 4 and s['ok'] == 0:
                p = 0.001
            # exploracion: 3 sondeos por marca con >=5 candidatos antes de explotar las que responden
            if att < 3 and big.get(b, 0) >= 5:
                return (10 - att, big[b])
            return (p, -PRIORITY[todo[b][0]['cat']])
        b = max(todo, key=score)
        c = todo[b].pop(0)
        if not todo[b]:
            del todo[b]
        rec = {'product_id': c['id'], 'cat': c['cat'], 'brand': c['brand'], 'mpn': c['mpn'], 'ean': c.get('ean'),
               'at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
        hit = obs.get((norm(c['brand']), norm(c['mpn'])))
        if hit:
            rec.update(status='ok', queried=False, icecat_id=hit[0][1], code=c['mpn'], gtins=json.loads(hit[0][2] or '[]'),
                       title=hit[0][3], features=feature_rows(json.loads(hit[0][0])), via='cache')
        else:
            wait = 2.1 - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
            res = query(cfg, brand=c['brand'], mpn=c['mpn'])
            last = time.time()
            n += 1
            rec.update(res)
            rec['queried'] = True
            rec['via'] = 'brand+mpn'
            if res['status'] == 'http_404' and c.get('ean'):
                time.sleep(2.1)
                res2 = query(cfg, gtin=c['ean'][0])
                last = time.time()
                n += 1
                rec['gtin_try'] = {k: v for k, v in res2.items() if k != 'features'}
                if res2['status'] == 'ok':
                    rec.update(res2)
                    rec['via'] = 'gtin'
            if rec['status'] in ('http_401', 'http_429', 'net_error') or rec['status'].startswith('http_5'):
                out.write(json.dumps(rec, ensure_ascii=False) + '\n')
                out.flush()
                print('PARO: ' + rec['status'], file=sys.stderr)
                break
            stats[b][rec['status']] += 1
        out.write(json.dumps(rec, ensure_ascii=False) + '\n')
        out.flush()
        if n and n % 25 == 0:
            print(n, flush=True)
    json.dump({b: dict(s) for b, s in stats.items()}, open(f'{wd}/brand_stats.json', 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({'consultas': n}))


main()
