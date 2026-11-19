"""Extractor determinista de specs de cajas/refrigeración desde la ficha oficial (dry-run).
Uso: python specs_official_casecooler_extract.py <dump.json> <salida.json> [--brand X ...] [--limit N]
dump.json: {product_id: data} de productos case/cooler (stdout de una consulta a catalog_entities).
Solo AÑADE claves ausentes; no escribe en la BD. Una petición por URL (caché local), >=1,2 s por dominio."""
import sys, os, re, json, time, hashlib, datetime, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bs4 import BeautifulSoup
except ImportError:
    sys.exit('Falta beautifulsoup4')
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CACHE = os.environ.get('SPECS_CACHE', 'pages')
_last = {}
_robots = {}
X = '×'


def fetch(url, delay=1.2):
    os.makedirs(CACHE, exist_ok=True)
    p = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + '.html')
    if os.path.exists(p):
        return open(p, encoding='utf-8').read()
    host = url.split('/')[2]
    if host not in _robots:
        import urllib.robotparser
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(urllib.request.urlopen(urllib.request.Request(f'https://{host}/robots.txt', headers={'User-Agent': UA}), timeout=20).read().decode('utf-8', 'replace').splitlines())
        except Exception:
            rp = None  # sin robots.txt legible (404): permitido
        _robots[host] = rp
    if _robots[host] and not _robots[host].can_fetch('*', url):
        sys.stderr.write(f'ROBOTS {url}' + chr(10))
        return None
    w = _last.get(host, 0) + delay - time.time()
    if w > 0:
        time.sleep(w)
    err = None
    for _ in range(2):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'en,es;q=0.8'}), timeout=30)
            b = r.read().decode('utf-8', 'replace')
            _last[host] = time.time()
            open(p, 'w', encoding='utf-8').write(b)
            return b
        except Exception as e:
            _last[host] = time.time()
            err = e
            time.sleep(3)
    sys.stderr.write(f'ERR {url} {err}\n')
    return None


def tokens(html):
    s = BeautifulSoup(html, 'html.parser')
    for t in s(['script', 'style', 'nav', 'header', 'footer', 'noscript']):
        t.decompose()
    return [t for t in s.get_text('|', strip=True).split('|') if t]


def after(tok, *names, n=1):
    for i, t in enumerate(tok):
        if t.rstrip('：: ').lower() in names:
            return tok[i + 1:i + 1 + n]
    return None


def mm(v):
    return f'{float(v):g} mm'


def clean(s):
    return re.sub(r'\s+', ' ', s.replace('\xa0', ' ')).strip()


def keyof(s):
    return re.sub(r'[^a-z0-9]', '', s.lower())


def fans_from(text):
    parts = re.findall(r'(\d+)\s*[x×*]\s*(\d{2,3})\s*mm', text, re.I)
    if not parts:
        parts = [(c, s) for s, c in re.findall(r'(\d{2,3})\s*mm\s*[x×*]\s*(\d+)', text, re.I)]
    if not parts:
        return None
    if len({s for c, s in parts}) == 1:
        return f'{sum(int(c) for c, s in parts)} {X} {parts[0][1]} mm'
    return ' + '.join(f'{c} {X} {s} mm' for c, s in parts)


def formats_from(text):
    t = text.lower()
    t = re.sub(r'e[\s-]?atx|eatx', 'EATX', t)
    t = re.sub(r'micro[\s-]?atx|m[\s-]?atx', 'MICROATX', t)
    t = re.sub(r'mini[\s-]?itx|(?<![a-z])itx', 'MINIITX', t)
    out = []
    if 'EATX' in t or 'ssi' in t:
        out.append('E-ATX')
    if re.search(r'(?<![A-Za-z])atx', t):
        out.append('ATX')
    if 'MICROATX' in t:
        out.append('Micro-ATX')
    if 'MINIITX' in t:
        out.append('Mini-ITX')
    return ', '.join(out) or None


# ---------------- extractores por marca: dict clave -> (valor, evidencia) ----------------
def mars(html, cat, p):
    tok = tokens(html)
    out = {}
    ref = after(tok, 'referencia')
    if ref and p.get('mpn') and p['mpn'].upper()[:6] != ref[0].upper()[:6]:
        return {'_skip': f'referencia {ref[0]} != {p.get("mpn")}'}
    if cat == 'case':
        v = after(tok, 'included components')
        if v and (f := fans_from(v[0])):
            out['Ventiladores incluidos'] = (f, v[0])
        v = after(tok, 'cpu cooler height')
        if v and (m := re.fullmatch(r'(\d{2,3})\s*mm', v[0])):
            out['Altura disipador máxima'] = (mm(m[1]), v[0])
        v = after(tok, 'max vga length.', 'max vga length')
        if v and (m := re.fullmatch(r'(\d{2,3})\s*mm', v[0])):
            out['Longitud GPU máxima'] = (mm(m[1]), v[0])
        i = next((k for k, t in enumerate(tok) if t.lower() == 'compatibility with motherboards'), None)
        if i is not None:
            seg = []
            for t in tok[i + 1:i + 6]:
                if re.fullmatch(r'(?i)(e-?atx|atx|micro-?atx|mini-?itx)', t):
                    seg.append(t)
                else:
                    break
            if seg and (f := formats_from(' '.join(seg))):
                out['Formatos compatibles'] = (f, ' | '.join(seg))
        i = next((k for k, t in enumerate(tok) if t.lower() == 'power supply compatibility'), None)
        if i is not None and re.fullmatch(r'(?i)(atx|sfx|sfx-l|tfx)', tok[i + 1]):
            out['Formato fuente'] = (tok[i + 1].upper(), tok[i + 1])
        i = next((k for k, t in enumerate(tok) if t.lower() == 'window'), None)
        if i is not None and re.search('tempered glass', tok[i + 1], re.I):
            out['Panel lateral'] = ('Cristal templado', f'Window: {tok[i+1]}')
    else:
        v = after(tok, 'tdp')
        if v and (m := re.fullmatch(r'(\d{2,3})\s*W', v[0], re.I)):
            out['TDP'] = (f'{m[1]} W', v[0])
        v = after(tok, 'dimensiones radiador')
        if v and (m := re.fullmatch(r'(120|140|240|280|360|420)\s*mm', v[0])):
            out['Radiador'] = (f'{m[1]} mm', 'Dimensiones radiador: ' + v[0])
    return out


def jonsbo(html, cat, p):
    tok = tokens(html)
    txt = ' '.join(tok)
    out = {}
    model = after(tok, 'model')
    if not model:
        mt = next((t for t in tok if re.match(r'Model[：:]', t)), None)
        model = [re.sub(r'^Model[：:]\s*', '', mt)] if mt else None
    if not model:
        return {'_skip': 'sin modelo'}
    if keyof(model[0]) not in keyof(p['name']) and keyof(model[0]) not in keyof(p.get('mpn') or ''):
        return {'_skip': f'modelo {model[0]} no coincide'}
    if cat == 'case':
        if re.search(r'ATX\s*PS2\s*PSU', txt):
            out['Formato fuente'] = ('ATX', 'ATX PS2 PSU Clearance')
        m = re.search(r'CPU Air Cooler Clearance\W+(?:[≤<]|up to)?\s*(\d{2,3}(?:\.\d)?)\s*mm', txt, re.I)
        if m:
            out['Altura disipador máxima'] = (mm(m[1]), m[0])
        m = re.search(r'GPU Clearance\W+(?:[≤<]|up to)?\s*(\d{2,3}(?:\.\d)?)\s*mm', txt, re.I)
        if m:
            out['Longitud GPU máxima'] = (mm(m[1]), m[0])
        i = next((k for k, t in enumerate(tok) if t.startswith('Motherboard Support')), None)
        if i is not None:
            seg = ' '.join(tok[i:i + 3])
            if (f := formats_from(seg)):
                out['Formatos compatibles'] = (f, seg)
    else:
        m = re.search(r'D-TDP\W+(\d{2,3})\s*W', txt)
        if m:
            out['TDP'] = (f'{m[1]} W', m[0])
        m = re.search(r'Dimensions\W+(\d{2,3}(?:\.\d)?)\s*mm\s*\(L\)\s*\*\s*(\d{2,3}(?:\.\d)?)\s*mm\s*\(W\)\s*\*\s*(\d{2,3}(?:\.\d)?)\s*mm\s*\(H\)', txt)
        if m and p['specs'].get('Tipo', '').lower().startswith(('aire', 'air')):
            out['Altura'] = (mm(m[3]), m[0])
    return out


def montech(html, cat, p):
    tok = tokens(html)
    out = {}
    model = after(tok, 'model name')
    if not model:
        return {'_skip': 'sin modelo'}
    if keyof(model[0]) not in keyof(p['name']):
        return {'_skip': f'modelo {model[0]} no coincide'}
    if cat == 'case':
        i = next((k for k, t in enumerate(tok) if t == 'Compatibility / Maximum'), None)
        if i is not None:
            seg = tok[i:i + 12]
            for lab, kk in (('CPU Cooler', 'Altura disipador máxima'), ('GPU', 'Longitud GPU máxima')):
                if lab in seg:
                    m = re.fullmatch(r'(\d{2,3})\s*mm', seg[seg.index(lab) + 1])
                    if m:
                        out[kk] = (mm(m[1]), f'{lab} {seg[seg.index(lab)+1]}')
            if 'PSU' in seg:
                m = re.match(r'(?:\d{2,3}\s*mm\s*)?(ATX|SFX-?L?|TFX)', seg[seg.index('PSU') + 1])
                if m:
                    out['Formato fuente'] = (m[1].upper(), 'PSU ' + seg[seg.index('PSU') + 1])
        v = after(tok, 'mb support')
        if v and (f := formats_from(v[0])):
            out['Formatos compatibles'] = (f, v[0])
        i = next((k for k, t in enumerate(tok) if t.startswith('Pre-installed Fan')), None)
        if i is not None:
            seg = tok[i + 1:i + 7]
            fs = []
            for lab, val in zip(seg[::2], seg[1::2]):
                if lab in ('Front', 'Rear', 'Top', 'Side', 'Bottom'):
                    m = re.match(r'(\d{2,3})\s*mm\s*\*\s*(\d+)', val)
                    if m:
                        fs.append((int(m[2]), int(m[1]), lab, val))
                else:
                    break
            if fs and len({f[1] for f in fs}) == 1:
                out['Ventiladores incluidos'] = (f'{sum(f[0] for f in fs)} {X} {fs[0][1]} mm', '; '.join(f'{f[2]} {f[3]}' for f in fs))
    return out


def thermalright(html, cat, p):
    s = BeautifulSoup(html, 'html.parser')
    out = {}
    tabs = []
    for t in s.find_all('table', class_='specs-table'):
        tabs.append({clean(r.find('th').get_text(' ')).lower(): clean(r.find('td').get_text(' ')) for r in t.find_all('tr') if r.find('th') and r.find('td')})
    if not tabs:
        return {'_skip': 'sin tabla'}
    aio = bool(re.search(r'l[ií]quida|aio|water', p['specs'].get('Tipo', ''), re.I))
    first = tabs[0]
    if not aio:
        m = re.search(r'H\s*(\d{2,3}(?:\.\d+)?)\s*mm', first.get('dimension', ''))
        if m and ('heat pipes' in first or 'copper base' in first):
            out['Altura'] = (mm(m[1]), 'Dimension: ' + first['dimension'])
    else:
        rk = next((k for k in first if k.startswith('radiator') and ('dim' in k or 'fim' in k)), None)
        m = re.search(r'L\s*(\d{3}(?:\.\d+)?)\s*mm', first.get(rk, '')) if rk else None
        size = None
        if m:
            L = float(m[1])
            size = 240 if 270 <= L <= 295 else 360 if 385 <= L <= 410 else None
        nm = re.search(r'(?<!\d)(120|240|280|360|420)(?!\d)', p['name'])
        if size and nm and int(nm[1]) == size:
            out['Radiador'] = (f'{size} mm', f'{rk}: {first[rk]}')
            if len(tabs) > 1:
                fm = re.fullmatch(r'L\s*(\d{3})\s*mm\s*x\s*W\s*(\d{3})\s*mm\s*x\s*H\s*\d+\s*mm', tabs[1].get('dimension', ''))
                if fm and int(fm[2]) == 120 and int(fm[1]) == size // 120 * 120:
                    out['Ventiladores'] = (f'{size // 120} {X} 120 mm', 'Dimension ventilador: ' + tabs[1]['dimension'])
    return out


def deepcool(html, cat, p):
    tok = tokens(html)
    txt = ' '.join(tok)
    out = {}
    model = after(tok, 'model')
    if not model:
        return {'_skip': 'sin modelo'}
    if keyof(model[0]) not in keyof(p['name']):
        return {'_skip': f'modelo {model[0]} no coincide'}
    if 'Technical Spec' not in tok:
        return {'_skip': 'sin Technical Spec'}
    ts = tok[tok.index('Technical Spec'):]
    if cat == 'case':
        m = re.search(r'GPUs? up to (\d{3})\s*mm', txt)
        if m:
            out['Longitud GPU máxima'] = (mm(m[1]), m[0])
        m = re.search(r'(?:air )?coolers? (?:up )?to (\d{3})\s*mm', txt)
        if m:
            out['Altura disipador máxima'] = (mm(m[1]), m[0])
        v = after(ts, 'pre-installed fans')
        if v and (f := fans_from(v[0].replace('x', ' x ') if False else v[0])):
            out['Ventiladores incluidos'] = (f, v[0])
        v = after(ts, 'motherboard support')
        if v and (f := formats_from(v[0])):
            out['Formatos compatibles'] = (f, v[0])
    else:
        v = after(ts, 'product dimensions')
        tipo = p['specs'].get('Tipo', '')
        if v and re.match(r'(?i)aire|air', tipo):
            m = re.fullmatch(r'[\d.]+\s*[×x]\s*[\d.]+\s*[×x]\s*([\d.]+)\s*mm\s*\(L×W×H\)', v[0])
            if m:
                out['Altura'] = (mm(m[1]), 'Product Dimensions: ' + v[0])
        v = after(ts, 'radiator dimensions')
        if v and re.search(r'(?i)l[ií]quida|aio', tipo):
            m = re.match(r'(\d{3})\s*[×x]\s*120\s*[×x]', v[0])
            nm = re.search(r'(?<!\d)(240|280|360|420)(?!\d)', p['name'])
            if m and nm:
                L = int(m[1])
                size = 240 if 270 <= L <= 295 else 360 if 385 <= L <= 410 else None
                if size and size == int(nm[1]):
                    out['Radiador'] = (f'{size} mm', 'Radiator Dimensions: ' + v[0])
    return out


EXTRACT = {('DeepCool', 'case'): deepcool, ('DeepCool', 'cooler'): deepcool, ('Mars Gaming', 'case'): mars, ('Mars Gaming', 'cooler'): mars, ('Jonsbo', 'case'): jonsbo, ('Jonsbo', 'cooler'): jonsbo,
           ('Montech', 'case'): montech, ('Thermalright', 'cooler'): thermalright}


def run(dump, outp, brands=None, limit=None):
    from spec_normalize import normalize_value
    d = json.load(open(dump, encoding='utf-8'))
    res, skipped, n = [], [], 0
    for pid, p in sorted(d.items()):
        fn = EXTRACT.get((p.get('brand'), p.get('category_id')))
        if not fn or (brands and p['brand'] not in brands):
            continue
        if limit and n >= limit:
            break
        url = p.get('manufacturer_url')
        if not url:
            continue
        n += 1
        sys.stderr.write(f'{n} {url}'+chr(10)); sys.stderr.flush()
        html = fetch(url)
        if not html:
            skipped.append((pid, 'sin respuesta'))
            continue
        try:
            got = fn(html, p['category_id'], p)
        except Exception as e:
            skipped.append((pid, f'error {e!r}'))
            continue
        if '_skip' in got:
            skipped.append((pid, got['_skip']))
            continue
        for k, (v, ev) in got.items():
            if k in p.get('specs', {}):
                continue
            nv = normalize_value(p['category_id'], k, v)
            res.append(dict(product_id=pid, brand=p['brand'], category=p['category_id'], name=p['name'], key=k, new=nv, raw=v, evidence=ev, url=url, fetched=datetime.date.today().isoformat()))
    json.dump(dict(items=res, skipped=skipped), open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    import collections
    c = collections.Counter((r['brand'], r['category'], r['key']) for r in res)
    for k, v in sorted(c.items()):
        print(k, v)
    print('productos', len({r['product_id'] for r in res}), 'omitidos', len(skipped))


if __name__ == '__main__':
    a = sys.argv[1:]
    brands = [a[i + 1] for i, x in enumerate(a) if x == '--brand']
    lim = int(a[a.index('--limit') + 1]) if '--limit' in a else None
    run(a[0], a[1], brands, lim)
