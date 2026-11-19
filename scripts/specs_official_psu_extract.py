"""Extrae specs desde la ficha OFICIAL enlazada en `manufacturer_url` de cada producto con huecos. Solo lectura (red + volcado de la BD ya hecho).
Entrada: artifacts/neobyte/gap-targets.json (volcado de productos psu/ram/storage con claves ausentes).
Salida: <out>/raw-<marca>.json con propuestas {product_id, key, raw, source_url, fetched_at, evidence}.
Uso (host VM): python3 scripts/specs_official_psu_extract.py --brand asus [--out work/specs-official-psu-20261119]
Exige identidad inequÃ­voca (vatios/capacidad coinciden) y nunca infiere por parecido. Se normaliza despuÃ©s con specs_official_finalize.py."""
import sys, os, re, json, argparse, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psu_official_http as C

NOW = datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')


def watts(x):
    s = x['specs'].get('Potencia')
    m = re.search(r'(\d{3,4})\s*W', s or '') or re.search(r'(\d{3,4})\s*W\b', x['name'])
    return int(m.group(1)) if m else None


def label_map(lines, labels):
    """valor = lÃ­neas siguientes a una etiqueta exacta, hasta la siguiente etiqueta conocida."""
    out = {}
    for i, l in enumerate(lines):
        if l in labels:
            j = i + 1; vals = []
            while j < len(lines) and lines[j] not in labels and len(vals) < 12:
                vals.append(lines[j]); j += 1
            out[l] = vals
    return out


ASUS_LABELS = {'Model', 'Intel Form Factor', 'Form Factor', 'PFC Type', 'ATX Standard', '80 PLUS Efficiency', 'Cybenetics Efficiency', 'Protection Features',
               'Hazardous Materials', 'AC Input Range', 'DC Output Voltage', 'Maximum Load', 'Combined Load', 'Total Output', 'Cable Connectors', 'Package Contents',
               'Dimensions', 'Weight', 'Cybenetics Noise Level Certification', 'AURA SYNC', 'MTBF', '0dB Fan Button', 'Warranty', 'Efficiency'}


def asus(x):
    u = x['url']
    if not re.match(r'https://(www\.asus\.com|rog\.asus\.com)/.+(techspec|spec)/?$', u):
        return None, 'url no es una ficha techspec'
    page, st = C.fetch(u)
    if not page:
        return None, st
    L = C.text_lines(page)
    m = label_map(L, ASUS_LABELS)
    tot = re.search(r'(\d{3,4})\s*W', ' '.join(m.get('Total Output', [])))
    w = watts(x)
    if not tot or not w or int(tot.group(1)) != w:
        return None, f'vatios no coinciden/ausentes (ficha {tot and tot.group(1)}, catÃ¡logo {w})'
    mdl = ' '.join(m.get('Model', [''])[:1])
    props = []
    atx = ' '.join(m.get('ATX Standard', []))
    am = re.search(r'ATX\s*(?:12V\s*)?(?:v)?(\d\.\d)', atx)
    if am: props.append(('EstÃ¡ndar ATX', am.group(1), f'ATX Standard: {atx}'))
    ff = ' '.join(m.get('Intel Form Factor', m.get('Form Factor', [])))
    if re.fullmatch(r'ATX12V', ff.strip()): props.append(('Formato', 'ATX', f'Form Factor: {ff}'))
    elif re.fullmatch(r'SFX-?L(12V)?', ff.strip(), re.I) or ('SFX-L' in ff): props.append(('Formato', 'SFX-L', f'Form Factor: {ff}'))
    elif re.fullmatch(r'SFX(12V)?', ff.strip()): props.append(('Formato', 'SFX', f'Form Factor: {ff}'))
    cc = m.get('Cable Connectors', [])
    if cc:
        j = ' | '.join(cc)
        if re.search(r'PCI-E\s*16-pin', j):
            std = am.group(1) if am else None
            conn = '12V-2x6' if std == '3.1' else '12VHPWR' if std == '3.0' else None
            if conn: props.append(('Conector GPU', conn, f'Cable Connectors: {j}'))
        else:
            n = sum(int(k.group(1)) for l in cc for k in [re.match(r'PCI-E\s*(?:6\+2|8)-pin\s*x\s*(\d+)', l)] if k)
            if n: props.append(('Conector GPU', f'{n} Ã— PCIe 6+2 pines', f'Cable Connectors: {j}'))
    e = ' '.join(m.get('80 PLUS Efficiency', m.get('Efficiency', [])))
    em = re.search(r'80\s*Plus\s*(Bronze|Silver|Gold|Platinum|Titanium)?', e, re.I)
    if em: props.append(('Eficiencia 80 PLUS', '80 PLUS' + (f' {em.group(1).title()}' if em.group(1) else ''), f'80 PLUS Efficiency: {e}'))
    cy = ' '.join(m.get('Cybenetics Efficiency', []))
    cm = re.match(r'(Bronze|Silver|Gold|Platinum|Titanium|Diamond)', cy)
    if cm: props.append(('Cybenetics', f'Cybenetics {cm.group(1)}', f'Cybenetics Efficiency: {cy}'))
    return [dict(key=k, raw=v, evidence=ev, model_on_page=mdl) for k, v, ev in props], 'ok'


MSI_LABELS = {'Modular', 'Fan Bearing', 'Power Watt', 'Power Excursion', 'Input Voltage', 'Input Frequency', 'Efficiency', 'Fan Size', 'Dimension', 'PFC Type', 'Protection'}


def msi_psu(x):
    u = x['url']
    if not re.match(r'https://(es|pl|fr|de|it|pt|uk|nl)-store\.msi\.com/products/[a-z0-9-]+$', u):
        return None, 'url no es una ficha de tienda oficial MSI'
    page, st = C.fetch(u)
    if not page:
        return None, st
    L = C.text_lines(page)
    title = re.sub(r'[^a-z0-9]+', ' ', L[0].lower()) if L else ''
    code = re.search(r'\b([A-Z]{1,3}\d{3,4}[A-Z]{0,3}\d?)\b', x['name'].replace('MSI MAG ', '').replace('MSI MPG ', '').replace('MSI MEG ', ''))
    m = {}
    for i, l in enumerate(L):
        if l in MSI_LABELS and i + 1 < len(L): m.setdefault(l, L[i + 1])
    w = watts(x)
    pw = re.search(r'(\d{3,4})\s*W', m.get('Power Watt', ''))
    if not pw or not w or int(pw.group(1)) != w:
        return None, f'vatios no coinciden (ficha {pw and pw.group(1)}, catálogo {w})'
    if not code or code.group(1).lower() not in title.split():
        return None, f'código de modelo {code and code.group(1)} no está en el título de la ficha'
    if ('white' in x['name'].lower()) != ('white' in title.split()):
        return None, 'color/edición (white) no coincide con el título'
    props = []
    mod = m.get('Modular', '')
    if re.match(r'Yes \(full', mod, re.I): props.append(('Modular', 'Totalmente modular', f'Modular: {mod}'))
    elif re.match(r'Yes \(semi', mod, re.I): props.append(('Modular', 'Semimodular', f'Modular: {mod}'))
    elif re.fullmatch(r'No', mod, re.I): props.append(('Modular', 'Cables fijos', f'Modular: {mod}'))
    ef = m.get('Efficiency', '')
    em = re.match(r'80 PLUS (Bronze|Silver|Gold|Platinum|Titanium)', ef, re.I)
    if em: props.append(('Eficiencia 80 PLUS', f'80 PLUS {em.group(1).title()}', f'Efficiency: {ef}'))
    full = ' '.join(L)
    atx = {v for v in re.findall(r'ATX\s*(3\.[01])b', ' '.join(L[:L.index('Descargar Especificaciones') if 'Descargar Especificaciones' in L else 400]), re.I)}
    if len(atx) == 1: props.append(('Estándar ATX', next(iter(atx)), 'texto oficial: ATX ' + next(iter(atx))))
    n16 = None; n8 = 0
    for i, l in enumerate(L):
        if re.match(r'PCI-E 5\.\d \(16 pin\)', l): n16 = L[i + 1]
        if re.match(r'PCI-E \(6\+2 pin\)', l) and L[i + 1].isdigit(): n8 = int(L[i + 1])
    if n16 and n16[0].isdigit() and int(n16[0]) > 0:
        if re.search(r'12V-?2x6', full, re.I) or atx == {'3.1'}: props.append(('Conector GPU', '12V-2x6', f'PCI-E 5.0 (16 pin): {n16}'))
    elif n8: props.append(('Conector GPU', f'{n8} × PCIe 6+2 pines', f'PCI-E (6+2 pin): {n8}'))
    return [dict(key=k, raw=v, evidence=ev, model_on_page=L[0]) for k, v, ev in props], 'ok'


BRANDS = {'msi-psu': (lambda x: x['cat'] == 'psu' and x['brand'] == 'MSI', msi_psu),
          'asus': (lambda x: x['cat'] == 'psu' and x['brand'] == 'ASUS', asus)}

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--brand', required=True)
    ap.add_argument('--targets', default='/opt/stockfinder/artifacts/neobyte/gap-targets.json')
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    sel, fn = BRANDS[a.brand]
    os.makedirs(a.out, exist_ok=True)
    out, skipped = [], []
    for x in json.load(open(a.targets)):
        if not sel(x): continue
        try:
            r, st = fn(x)
        except Exception as e:
            r, st = None, f'excepciÃ³n {type(e).__name__}: {e}'
        if not r:
            skipped.append(dict(product_id=x['id'], name=x['name'], url=x['url'], reason=st)); continue
        for p in r:
            if p['key'] in x['specs']: continue
            out.append(dict(product_id=x['id'], name=x['name'], brand=x['brand'], category=x['cat'], source_url=x['url'], fetched_at=NOW, **p))
    json.dump(out, open(f'{a.out}/raw-{a.brand}.json', 'w'), ensure_ascii=False, indent=1)
    json.dump(skipped, open(f'{a.out}/skipped-{a.brand}.json', 'w'), ensure_ascii=False, indent=1)
    print(a.brand, 'propuestas', len(out), 'productos', len({p['product_id'] for p in out}), 'omitidos', len(skipped))
