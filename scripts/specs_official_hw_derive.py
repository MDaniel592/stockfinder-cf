"""Rellena specs faltantes (placas base, CPU, GPU) leyendo la ficha oficial del fabricante. Solo lectura.

  python3 scripts/specs_official_hw_derive.py urls   <catalog-dump.json> <urls.json> <prod-urls.json>
  python3 scripts/specs_official_hw_derive.py derive <catalog-dump.json> <pages-dir> <outdir>

1. `urls` fija la URL de ficha por producto (la propia manufacturer_url del catálogo, con la ruta de especificaciones).
2. scripts/specs_official_hw_fetch.py (corredor Docker con curl_cffi) descarga las fichas.
3. `derive` parsea cada ficha, EXIGE identidad inequívoca de modelo (tokens exactos, sin variantes), convierte a las
   convenciones del catálogo, pasa por spec_normalize.normalize_specs y escribe <outdir>/dryrun-<categoría>.json.
   Solo propone claves ausentes. Nada se escribe en el catálogo desde aquí (ver specs_official_hw_apply.py).
catalog-dump.json: json_agg de {id, d} de los productos motherboard/cpu/gpu del catálogo."""
import sys, os, re, json, html, collections
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, '..', 'nas'))
sys.path.insert(0, '/app/nas')
import spec_normalize as sn  # noqa: E402
import specs_official_hw_parse as P  # noqa: E402


def page_url(cat, d):
    u = (d.get('manufacturer_url') or '').strip().split('?')[0]
    if not u: return None
    h = urlparse(u).netloc
    if h == 'www.gigabyte.com' and cat in ('motherboard', 'gpu'):
        return re.sub(r'/(sp|ov|kf|support)?$', '', u.rstrip('/')) + '/sp'
    if h in ('www.msi.com', 'us.msi.com') and cat in ('motherboard', 'gpu'):
        return re.sub(r'/(Specification|Overview)$', '', u.rstrip('/')) + '/Specification'
    if h == 'www.asus.com' and cat in ('motherboard', 'gpu'):
        return re.sub(r'/(techspec|spec|overview)?/?$', '', u.rstrip('/')) + '/techspec/'
    if h in ('www.asrock.com', 'tw.asrock.com', 'asrock.com', 'pg.asrock.com') and cat in ('motherboard', 'gpu'):
        return u
    if cat == 'cpu' and h == 'www.intel.com':
        return re.sub(r'/(ordering|specifications|overview|compatibility|downloads|documentation)\.html$', '/specifications.html', u)
    if cat == 'cpu' and h == 'www.amd.com':
        return u
    return None


def asrock_candidates(url):
    """Misma ficha en la maquetación nueva (Specification.asp) y en el espejo oficial tw.asrock.com."""
    base = re.sub(r'(index(?:\.us)?\.asp)?(#.*)?$', '', url).rstrip('/')
    tw = re.sub(r'^https://(?:www\.|pg\.)?asrock\.com', 'https://tw.asrock.com', base)
    return [base + '/Specification.asp', tw + '/', tw + '/Specification.asp', tw + '/index.asp']


def site_of(url):
    h = urlparse(url).netloc
    for s in ('gigabyte', 'msi', 'asus', 'asrock', 'intel', 'amd'):
        if s in h: return s


# ------------------------------------------------------------------ helpers de valores
def num(x):
    x = float(x)
    return str(int(x)) if x == int(x) else ('%g' % round(x, 3))


def ghz(v):
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(GHz|MHz)', v or '', re.I)
    if not m: return None
    x = float(m.group(1).replace(',', '.'))
    if m.group(2).lower() == 'mhz': x /= 1000
    return num(x) + ' GHz'


def size(v, units=('MB', 'GB', 'TB')):
    m = re.search(r'(\d+(?:[.,]\d+)?)\s*(' + '|'.join(units) + r')\b', v or '', re.I)
    return (num(float(m.group(1).replace(',', '.'))) + ' ' + m.group(2).upper()) if m else None


def watts(v):
    m = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*W\s*', v or '', re.I)
    return (num(float(m.group(1))) + ' W') if m else None


def toks(s, drop=()):
    s = html.unescape(s or '').lower().replace('™', '').replace('®', '')
    s = re.sub(r'\(.*?\)', ' ', s)
    s = re.sub(r'\brev\.?\s*\d+(?:\.[\dx]+)?', ' ', s)  # revisión de placa: no distingue modelo
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return [t for t in s.split() if t not in drop]


# ------------------------------------------------------------------ identidad (placas y GPU)
BRANDS = {'asus', 'msi', 'gigabyte', 'asrock', 'intel', 'amd', 'nvidia'}
SOCKET_NUMS = {'1700', '1851', '1200', '1151', '1155', '1150'}
GPU_NOISE = {'geforce', 'radeon', 'gddr6', 'gddr6x', 'gddr7', 'gddr5', 'pcie', 'edition', 'g', 'gb'}


def canon_tokens(s, brand, gpu=False):
    """Firma del modelo: tokens sin marca, sin sufijo de memoria ni de socket; 'o16g' (ASUS) == 'oc 16g'."""
    s = html.unescape(s or '').lower()
    if gpu:
        s = re.sub(r'\bdlss\d?\b', ' ', s)
        s = re.sub(r'\bo(\d+g)\b', r'oc \1', s)
    t = toks(s, drop=BRANDS | {brand.lower()} | {'socket', 'am4', 'am5', 'rev', 'motherboard', 'placa', 'base', 'graphics', 'card'} | SOCKET_NUMS)
    out = []
    for x in t:
        for y in (re.findall(r'[a-z]+|\d+', x) if gpu else [x]):
            if gpu and y in GPU_NOISE: continue
            out.append(y)
    return out


def page_model(site, h, pairs, url):
    t = re.search(r'<title>(.*?)</title>', h, re.S)
    t = html.unescape(t.group(1)).strip() if t else ''
    slug = urlparse(url).path.rstrip('/').split('/')
    if site == 'gigabyte':
        m = re.sub(r'\s+(Motherboard|Graphics Card).*$', '', t)
        return re.sub(r'\s*-\s*GIGABYTE.*$', '', m)
    if site == 'msi':
        mn = pairs.get('Marketing Name')
        if mn: return mn
        generic = re.search(r'Motherboards?\s*[｜|]|Best Motherboard|Graphics Cards?\s*[｜|]', t)
        if t and not generic and not t.startswith('MSI'): return t.split('|')[0].strip()
        s = slug[-2] if slug[-1].lower() == 'specification' else slug[-1]
        return s.replace('-', ' ')
    if site == 'asus':
        m = re.sub(r'\s*-\s*(Tech Specs|Especificaciones).*$', '', t)
        m = re.sub(r'\s*[｜|/].*$', '', m)
        return pairs.get('Model') or m
    if site == 'asrock':
        return re.sub(r'^\s*ASRock\s*>\s*', '', t)
    return t


def identity_ok(site, cat, d, pm, url):
    """True si la ficha es exactamente el modelo del catálogo (mismos tokens, sin variantes)."""
    brand = d.get('brand', '')
    gpu = cat == 'gpu'
    a, b = canon_tokens(d.get('name', ''), brand, gpu), canon_tokens(pm, brand, gpu)
    if a and sorted(a) == sorted(b): return True, 'tokens'
    if site == 'gigabyte' and gpu:
        slug = urlparse(url).path.rstrip('/').split('/')[-2]
        mpn = re.sub(r'[^a-z0-9]', '', (d.get('mpn') or '').lower())
        if mpn and mpn == re.sub(r'[^a-z0-9]', '', slug.lower()): return True, 'mpn=slug'
    if site == 'msi' and gpu:
        mpn = re.sub(r'[^a-z0-9]', '', (d.get('mpn') or '').lower())
        mn = re.sub(r'[^a-z0-9]', '', (pm or '').lower())
        if mpn and mpn == mn: return True, 'mpn=modelo'
    return False, f'{a} != {b}'


# ------------------------------------------------------------------ placas base
WIFI_NAME = re.compile(r'wi-?fi|wireless|\bwf\d?e?\b|\bax\b|\bbe\b', re.I)


def mb_wifi(site, pairs):
    key = {'gigabyte': 'Wireless Communication module', 'msi': 'WIRELESS LAN & BLUETOOTH', 'asrock': 'Wireless LAN',
           'asus': 'Wireless & Bluetooth'}[site]
    v = pairs.get(key)
    if not v: return None
    low = v.lower()
    m = re.findall(r'wi-?fi\s*(7|6e|6|5)\b', low)
    ranks = {'7': 7, '6e': 6.5, '6': 6, '5': 5}
    if m:
        best = max(m, key=lambda x: ranks[x])
        return 'Wi-Fi ' + best.upper()
    if re.search(r'802\.11[a-z/ ]*be', low): return 'Wi-Fi 7'
    if re.search(r'802\.11[a-z/ ]*ax', low): return 'Wi-Fi 6E' if re.search(r'6\s*ghz|6e', low) else 'Wi-Fi 6'
    if re.search(r'802\.11[a-z/ ]*ac', low): return 'Wi-Fi 5'
    return None


LAN_SKIP = re.compile(r'software|support|wake|pxe|bandwidth|priority|statistic|optimi[sz]ed|languard|visual|smart auto|bmc|ipmi|manage|dedicated|wi-?fi|bluetooth|wlan|wireless', re.I)


def mb_lan(site, pairs):
    """Controladores de red cableada: [(puertos, velocidad)] -> '2.5 GbE', '2 × 2.5 GbE', '10 GbE + 2.5 GbE'.
    Solo cuenta líneas que describen un controlador; una línea repetida con la misma velocidad y sin cantidad explícita
    no suma puertos (evita doble recuento del mismo chip)."""
    v = pairs.get('LAN') or pairs.get('Ethernet')
    if not v: return None
    ports = {}
    for seg in re.split(r'[;\n]', v):
        seg = seg.strip()
        if not seg or LAN_SKIP.search(seg): continue
        s = re.sub(r'\([^)]*\)', ' ', seg)
        m = re.search(r'(?<![\d.])(\d+(?:\.\d+)?)\s*G(?:b|bE|bit|igabit)?(?:\s*(?:E|LAN|Ethernet|BASE))?(?![a-z])', s)
        sp = float(m.group(1)) if m and float(m.group(1)) in (1, 2.5, 5, 10, 25) else (1.0 if re.search(r'gigabit|\b1\s*Gb', s, re.I) else None)
        if not sp: continue
        e = re.match(r'\s*(\d)\s*[x×]\s', s)
        n = int(e.group(1)) if e else (2 if re.search(r'\bdual\b', s, re.I) else None)
        if n: ports[sp] = ports.get(sp, 0) + n
        else: ports.setdefault(sp, 1)
    if not ports: return None
    return ' + '.join((f'{n} × ' if n > 1 else '') + f'{num(sp)} GbE' for sp, n in sorted(ports.items(), reverse=True))


def mb_memmax(pairs):
    v = pairs.get('Memory') or ''
    cands = set()
    pats = (r'(?:max(?:imum|\.)?(?:\s+(?:system\s+)?memory)?(?:\s+capacity)?|up to|hasta)\s*(?:of\s+system\s+memory)?\s*:?\s*(\d+)\s*(GB|TB)\b',
            r'capacity of system memory\s*:?\s*(\d+)\s*(GB|TB)\b')
    for pat in pats:
        for m in re.finditer(pat, v, re.I):
            tail = v[m.end():m.end() + 25].lower()
            if re.match(r'\s*(?:per|each|single)\b', tail): continue  # capacidad por módulo, no total
            cands.add(f'{m.group(1)} {m.group(2).upper()}')
    return cands.pop() if len(cands) == 1 else None


def mb_slots(pairs):
    v = pairs.get('Memory') or ''
    c = {m.group(1) for m in re.finditer(r'(\d)\s*[x×*]\s*(?:DDR\d\s*)?(?:U-?|SO-?)?DIMM', v, re.I)}
    c |= {m.group(1) for m in re.finditer(r'(\d)\s*(?:x\s*)?(?:DDR\d\s+)?DIMM(?:\s+slots?|\s+sockets?)', v, re.I)}
    return c.pop() if len(c) == 1 else None


def derive_mb(site, d, pairs, name_has_wifi):
    out = {}
    s = d['specs']
    if 'Memoria máxima' not in s:
        v = mb_memmax(pairs)
        if v: out['Memoria máxima'] = v
    if 'Ranuras de memoria' not in s:
        v = mb_slots(pairs)
        if v: out['Ranuras de memoria'] = v
    wf = mb_wifi(site, pairs)
    if not any(k in s for k in ('Wi-Fi', 'WiFi', 'Red inalámbrica', 'Conectividad inalámbrica')):
        if wf: out['Wi-Fi'] = wf
        elif not name_has_wifi and ('LAN' in pairs or 'Ethernet' in pairs) and not any(
                'wireless' in k.lower() or 'wi-fi' in k.lower() for k in pairs):
            out['Wi-Fi'] = 'No integrado'
    if not any(k in s for k in ('LAN', 'Red', 'Red cableada')):
        v = mb_lan(site, pairs)
        if v: out['LAN'] = v
    if 'Formato' not in s and site in ('gigabyte', 'asus', 'asrock'):
        ff = pairs.get('Form Factor')
        if ff:
            nv = sn.normalize_value('motherboard', 'Formato', ff.split(';')[0])
            if nv in ('ATX', 'Micro-ATX', 'Mini-ITX', 'E-ATX', 'Extended ATX'): out['Formato'] = nv
    return out


# ------------------------------------------------------------------ GPU
def gpu_conn(v):
    v = re.sub(r'\([^)]*\)', ' ', v or '')
    m12 = re.search(r'(?:(\d)\s*[x×*]\s*)?(12V[\s-]?2x6|12VHPWR)', v, re.I)
    if m12 and not re.search(r'[68][\s-]*pin', re.sub(r'12V[\s-]?2x6[\s-]*pin|12VHPWR', '', v, flags=re.I), re.I):
        return f"{m12.group(1) or 1} × " + ('12V-2x6' if '2x6' in m12.group(2).lower() else '12VHPWR')
    v = re.sub(r'12\s*\+\s*4', '16', v)
    parts = {}
    for m in re.finditer(r'(\d)\s*[x×*]\s*(\d{1,2})[\s-]*pin', v, re.I):
        parts[int(m.group(2))] = parts.get(int(m.group(2)), 0) + int(m.group(1))
    if not parts:
        for m in re.finditer(r'(\d{1,2})[\s-]*pin\s*(?:\*|x|×)\s*(\d)', v, re.I):
            parts[int(m.group(1))] = parts.get(int(m.group(1)), 0) + int(m.group(2))
    if not parts:
        for m in re.finditer(r'(?<![\d])(\d{1,2})[\s-]*pin(?!\s*[x×*]\s*\d)', v, re.I):
            parts[int(m.group(1))] = parts.get(int(m.group(1)), 0) + 1
    if not parts or any(k not in (6, 8, 16) for k in parts): return None
    return ' + '.join(f'{c} × {k} pines' for k, c in sorted(parts.items(), reverse=True))


def gpu_len(pairs):
    for k in ('Card Size', 'Card Dimension (mm)', 'Dimensions', 'Card Length'):
        v = pairs.get(k)
        if not v: continue
        v = re.sub(r'\s+', ' ', v)
        m = re.search(r'L\s*=\s*(\d+(?:\.\d+)?)', v)
        if m: return num(float(m.group(1))) + ' mm'
        m = re.search(r'(\d+(?:\.\d+)?)\s*[x×*]\s*\d+(?:\.\d+)?\s*[x×*]\s*\d+(?:\.\d+)?\s*(mm|cm)', v, re.I)
        if m: return num(float(m.group(1)) * (10 if m.group(2).lower() == 'cm' else 1)) + ' mm'
    return None


def gpu_pcie(pairs):
    for k in ('Card Bus', 'Interface', 'Bus Standard'):
        v = pairs.get(k)
        if not v: continue
        m = re.search(r'(?:PCI[\s-]*E(?:xpress)?|PCIe)\s*(?:Gen\.?\s*)?(\d)(?:\.(\d))?', v, re.I)
        if m: return f'PCIe {m.group(1)}.{m.group(2) or 0}'
    return None


def derive_gpu(d, pairs):
    out = {}
    s = d['specs']
    bus = pairs.get('Memory Bus') or pairs.get('Memory Interface')
    if 'Bus de memoria' not in s and bus:
        m = re.fullmatch(r'\s*(\d{2,4})[\s-]*bits?\s*', bus, re.I)
        if m: out['Bus de memoria'] = f'{m.group(1)} bits'
    psu = pairs.get('Recommended PSU')
    if 'Fuente recomendada' not in s and psu:
        m = re.fullmatch(r'\s*(\d{3,4})\s*W\s*', re.sub(r'\([^)]*\)', '', psu), re.I)
        if m: out['Fuente recomendada'] = f'{m.group(1)} W'
    if 'Longitud' not in s:
        v = gpu_len(pairs)
        if v: out['Longitud'] = v
    if 'Interfaz' not in s:
        v = gpu_pcie(pairs)
        if v: out['Interfaz'] = v
    pc = pairs.get('Power Connectors') or pairs.get('Power connectors') or pairs.get('Power Connector')
    if 'Conectores de alimentación' not in s and pc:
        v = gpu_conn(pc)
        if v: out['Conectores de alimentación'] = sn.normalize_value('gpu', 'Conectores de alimentación', v)
    return out


# ------------------------------------------------------------------ CPU
def cpu_match(name, ref):
    ref = re.sub(r'\b(Intel|AMD|Core|Ryzen|Processor|Procesador)\b', ' ', ref, flags=re.I)
    ref = re.sub(r'\s+', ' ', ref).strip()
    if not ref: return False
    pat = r'(?<![A-Za-z0-9])' + r'[\s-]*'.join(re.escape(t) for t in re.split(r'[\s-]+', ref)) + r'(?![A-Za-z0-9])'
    return re.search(pat, html.unescape(name), re.I) is not None


def derive_cpu(site, d, pairs):
    s = d['specs']; out = {}
    name = d['name']
    p = pairs.get
    if site == 'intel':
        if not p('Sockets Supported'): return out, 'ficha incompleta'
        ref = p('Processor Number') or ''
        if not cpu_match(name, ref): return out, f'identidad: {ref!r} no está en {name!r}'
        sockets = re.findall(r'(?:FC)?(LGA\s?\d{3,4})', p('Sockets Supported') or '')
        if len(set(sockets)) == 1: out['Socket'] = sockets[0].replace(' ', '')
        if (p('Total Cores') or '').isdigit(): out['Núcleos'] = p('Total Cores')
        if (p('Total Threads') or '').isdigit(): out['Hilos'] = p('Total Threads')
        pc, ec = p('# of Performance-cores'), p('# of Efficient-cores')
        if pc and ec and ec != '0' and pc.isdigit() and ec.isdigit(): out['Distribución de núcleos'] = f'{pc} P + {ec} E'
        pb, eb = ghz(p('Performance-core Base Frequency')), ghz(p('Efficient-core Base Frequency'))
        pbase = ghz(p('Processor Base Frequency'))
        if pb and eb: out['Frecuencia base'] = f'P: {pb} / E: {eb}'
        elif pbase: out['Frecuencia base'] = pbase
        elif pb and ec == '0': out['Frecuencia base'] = pb
        t = ghz(p('Max Turbo Frequency'))
        if t: out['Frecuencia turbo'] = 'Hasta ' + t
        bp = watts(p('Processor Base Power')); tdp = watts(p('TDP')) or bp
        if tdp: out['TDP'] = tdp
        if bp: out['Potencia base'] = bp
        mt = watts(p('Maximum Turbo Power'))
        if mt: out['Potencia máxima turbo'] = mt
        l3 = p('Cache')
        if l3 and 'Smart Cache' in l3 and size(l3) and 'Caché' not in s: out['Caché L3'] = size(l3)
        l2 = size(p('Total L2 Cache'))
        if l2: out['Caché L2'] = l2
        mm = size(p('Max Memory Size (dependent on memory type)'), ('GB', 'TB'))
        if mm: out['Memoria máxima'] = mm
        mem = p('Memory Types')
        if mem:
            mt_ = re.findall(r'(DDR[345]|LPDDR5X?)[\s-]*(\d{4,5})\s*MT/s', mem)
            if mt_ and len(mt_) == len({a for a, _ in mt_}): out['Memoria compatible'] = ', '.join(f'{a}-{b}' for a, b in mt_)
        g = p('Processor Graphics')
        if g or p('Graphics Output') or p('Graphics Max Dynamic Frequency'): out['Gráficos integrados'] = 'Sí'
        elif re.search(r'\d[A-Z]*F[A-Z]*$', re.sub(r'\s*\(.*$', '', name).strip().split(' ')[-1]): out['Gráficos integrados'] = 'No'  # sufijo F: sin gráficos
        if g and len(g) <= 60 and ';' not in g: out['GPU integrada'] = g
        return out, None
    if site == 'amd':
        ref = p('Name') or ''
        if not p('CPU Socket') or not cpu_match(name, ref): return out, f'identidad: {ref!r} vs {name!r}'
        if re.fullmatch(r'AM[345]|SP[35]|sTR[X]?\d|TRX?\d+|sWRX8|FP\d', p('CPU Socket')): out['Socket'] = p('CPU Socket')
        for k, lab in (('Núcleos', '# of CPU Cores'), ('Hilos', '# of Threads')):
            if (p(lab) or '').isdigit(): out[k] = p(lab)
        m = re.fullmatch(r'Up to (\d+(?:\.\d+)?) GHz', p('Max. Boost Clock') or '')
        if m: out['Frecuencia turbo'] = 'Hasta ' + num(float(m.group(1))) + ' GHz'
        if re.fullmatch(r'\d+(?:\.\d+)? GHz', p('Base Clock') or ''): out['Frecuencia base'] = ghz(p('Base Clock'))
        for k, lab in (('Caché L2', 'L2 Cache'), ('Caché L3', 'L3 Cache')):
            if re.fullmatch(r'\d+(?:\.\d+)? (MB|KB)', p(lab) or ''): out[k] = size(p(lab), ('MB', 'KB'))
        t = watts(p('Default TDP'))
        if t: out['TDP'] = t
        if re.fullmatch(r'\d+ (GB|TB)', p('Max. Memory') or ''): out['Memoria máxima'] = p('Max. Memory')
        mt = p('System Memory Type')
        if mt and re.fullmatch(r'DDR[45]|LPDDR5X?', mt):
            out['Memoria compatible'] = mt + (' RDIMM' if (p('System Memory Subtype') or '').strip() == 'RDIMM' else '')
        g = p('Graphics Model')
        if g:
            if re.search(r'discrete graphics card required|^none$|^n/a$', g, re.I): out['Gráficos integrados'] = 'No'
            else:
                out['Gráficos integrados'] = 'Sí'
                gg = re.sub(r'^AMD\s+', '', g).strip()
                gg = re.sub(r'^(Radeon \d+M?) Graphics$', r'\1', gg)
                if re.fullmatch(r'Radeon[\w ]{0,25}', gg): out['GPU integrada'] = gg
        return out, None
    return out, 'sitio no soportado'


# ------------------------------------------------------------------ main
def load_pages(pages_dir):
    man = {}
    for l in open(os.path.join(pages_dir, 'manifest.jsonl')):
        r = json.loads(l)
        if r.get('status') == 200: man[r['url']] = r
    return man


def main():
    cmd = sys.argv[1]
    dump = json.load(open(sys.argv[2]))
    if cmd == 'urls':
        urls = collections.OrderedDict(); src = {}
        for x in dump:
            u = page_url(x['d']['category_id'], x['d'])
            if u: urls[u] = 1; src[x['id']] = u
        json.dump(list(urls), open(sys.argv[3], 'w')); json.dump(src, open(sys.argv[4], 'w'))
        print(len(urls), 'urls', dict(collections.Counter(urlparse(u).netloc for u in urls))); return
    pages, outdir = sys.argv[3], sys.argv[4]
    os.makedirs(outdir, exist_ok=True)
    man = load_pages(pages)
    rows = []; rej = []; stats = collections.Counter()
    for x in dump:
        pid, d = x['id'], x['d']; cat = d['category_id']
        url = page_url(cat, d)
        if not url or url not in man:
            stats[(cat, 'sin_ficha')] += 1; continue
        r = man[url]; site = site_of(url)
        h = open(os.path.join(pages, r['sha1'] + '.html'), encoding='utf8', errors='replace').read()
        pairs = P.parse_site(site, h)
        if not pairs and site == 'asrock':
            for cand in asrock_candidates(url):
                if cand in man:
                    h2 = open(os.path.join(pages, man[cand]['sha1'] + '.html'), encoding='utf8', errors='replace').read()
                    pairs = P.parse_site(site, h2)
                    if pairs: url, r, h = cand, man[cand], h2; break
        if not pairs: stats[(cat, 'sin_parseo')] += 1; rej.append((pid, url, 'sin parseo')); continue
        if cat == 'cpu':
            new, why = derive_cpu(site, d, pairs)
            if why: stats[(cat, 'rechazo')] += 1; rej.append((pid, url, why)); continue
        else:
            pm = page_model(site, h, pairs, url)
            ok, why = identity_ok(site, cat, d, pm, url)
            if not ok: stats[(cat, 'identidad')] += 1; rej.append((pid, url, why)); continue
            if cat == 'motherboard':
                nw = bool(WIFI_NAME.search(d.get('name', '') + ' ' + (d.get('mpn') or '')))
                new = derive_mb(site, d, pairs, nw)
            else:
                new = derive_gpu(d, pairs)
        new = {k: v for k, v in new.items() if k not in d['specs'] and v}
        if not new: stats[(cat, 'sin_cambios')] += 1; continue
        norm = sn.normalize_specs(cat, dict(new))
        new = {k: str(norm[k]) for k in new if k in norm and norm[k]}  # un alias renombrado se descarta
        new = {k: v for k, v in new.items() if len(k) <= 80 and len(v) <= 250 and k not in d['specs']}
        if not new: continue
        if len(d['specs']) + len(new) > 24:
            stats[(cat, 'excede24')] += 1; continue
        stats[(cat, 'ok')] += 1
        for k, v in new.items():
            rows.append(dict(product_id=pid, category=cat, brand=d['brand'], name=d['name'], key=k, new=v, url=url,
                             sha1=r['sha1'], fetched_at=r['fetched_at'], site=site))
    for cat in ('motherboard', 'cpu', 'gpu'):
        json.dump([r for r in rows if r['category'] == cat], open(os.path.join(outdir, f'dryrun-{cat}.json'), 'w'), ensure_ascii=False, indent=1)
    json.dump(rej, open(os.path.join(outdir, 'rechazados.json'), 'w'), ensure_ascii=False, indent=1)
    print(json.dumps({f'{a}:{b}': n for (a, b), n in sorted(stats.items())}, ensure_ascii=False))
    c = collections.Counter((r['category'], r['site'], r['key']) for r in rows)
    for k, n in sorted(c.items()): print(k, n)


if __name__ == '__main__':
    main()
