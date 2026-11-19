"""Rellena specs faltantes por derivación determinista (nombre/MPN, otras claves del mismo producto o gemelo exacto Tray/Boxed).
Solo lectura: escribe un dry-run JSON. Aplicar con scripts/specs_fill_apply.py.
Uso (en la VM): docker exec -i stockfinder-v2-neobyte-pipeline-1 python - < scripts/specs_fill_derive.py > work/specs-fill-<fecha>/dryrun-derive.json"""
import sys, re, json, collections, os, psycopg
sys.path.insert(0, '/app/nas')
import spec_normalize as sn
conn = psycopg.connect('postgresql://stockfinder@postgres/stockfinder')
rows = conn.execute("select entity_id,data from catalog_entities where entity='product' and deleted_at is null").fetchall()
P = {i: d for i, d in rows}
props = []


def add(pid, key, new, src, remove=()):
    s = P[pid]['specs']
    if key in s and not remove:
        return
    if any(p['product_id'] == pid and p['key'] == key for p in props):
        return
    props.append(dict(product_id=pid, category=P[pid]['category_id'], key=key, old=s.get(key), new=new, source=src, remove=list(remove)))


CAT = collections.defaultdict(list)
for i, d in P.items():
    CAT[d['category_id']].append(i)


def nm(i):
    return P[i]['name']


# ---------- STORAGE
for i in CAT['storage']:
    s = P[i]['specs']; n = nm(i)
    if 'Tipo' not in s:
        rot = s.get('Velocidad de rotación') or (s.get('Velocidad') if re.search(r'rpm', str(s.get('Velocidad', '')), re.I) else None)
        if rot or (re.search(r'\b(HDD|hard drive|disco duro)\b', n, re.I) and not re.search(r'\bSSD\b', n)):
            add(i, 'Tipo', 'HDD', 'nombre del producto o velocidad de rotación del mismo producto')
        elif re.search(r'\bSSD\b|NVMe', n, re.I) or str(s.get('Formato', '')).startswith('M.2') or 'NVMe' in str(s.get('Interfaz', '')):
            add(i, 'Tipo', 'SSD', 'nombre/formato M.2/interfaz NVMe del mismo producto')
    if 'Velocidad de rotación' not in s:
        m = re.match(r'^(\d{4,5})\s*rpm$', str(s.get('Velocidad', '')), re.I)
        if m:
            add(i, 'Velocidad de rotación', m.group(1) + ' rpm', 'clave «Velocidad» del mismo producto', remove=['Velocidad'])
        else:
            m = re.search(r'\b(5400|5640|5900|7200|10000)\s*rpm\b', n, re.I)
            if m and (s.get('Tipo') == 'HDD' or 'Tipo' not in s):
                add(i, 'Velocidad de rotación', m.group(1) + ' rpm', 'nombre del producto')


# ---------- CPU: gemelo con el mismo nombre (Tray/Boxed)
def cpukey(n):
    return re.sub(r'\s+', ' ', re.sub(r'\((?:tray|boxed|box|mpk|bulk|multipack)[^)]*\)|\b(tray|boxed|mpk)\b', '', n, flags=re.I)).strip().lower()


tw = collections.defaultdict(list)
for i in CAT['cpu']:
    tw[(P[i]['brand'], cpukey(nm(i)))].append(i)
COPY = ['Socket', 'Núcleos', 'Hilos', 'Frecuencia base', 'Frecuencia turbo', 'Memoria compatible', 'TDP', 'Gráficos integrados',
        'Caché L3', 'Caché L2', 'Memoria máxima', 'Distribución de núcleos', 'Potencia base', 'Potencia máxima turbo', 'Arquitectura']
for i in CAT['cpu']:
    s = P[i]['specs']
    if len(s) >= 8:
        continue
    sib = [j for j in tw[(P[i]['brand'], cpukey(nm(i)))] if j != i and len(P[j]['specs']) >= 8]
    if not sib:
        continue
    for k in COPY:
        vals = {json.dumps(P[j]['specs'].get(k)) for j in sib if k in P[j]['specs']}
        if len(vals) == 1 and k not in s:
            add(i, k, json.loads(vals.pop()), 'gemelo exacto (mismo modelo, otra presentación): ' + ', '.join(sib[:2]))
    if str(s.get('Presentación', '')).lower().startswith(('tray', 'bulk', 'mpk')) and 'Disipador incluido' not in s:
        add(i, 'Disipador incluido', 'No', 'presentación Tray/Bulk del mismo producto (sin disipador)')

for i in CAT['cpu']:
    s = P[i]['specs']
    if 'Gráficos integrados' not in s and P[i]['brand'] == 'Intel' and re.search(r'Core.*i[3579]-\d{4,5}K?F', nm(i)):
        add(i, 'Gráficos integrados', 'No', 'sufijo F del modelo Intel Core (sin gráficos integrados, nomenclatura oficial de Intel)')

# ---------- PSU
for i in CAT['psu']:
    s = P[i]['specs']; n = nm(i); low = n.lower()
    if 'Potencia' not in s:
        m = re.findall(r'(?<![\d.])(\d{3,4})\s*W\b', n)
        if len(set(m)) == 1:
            add(i, 'Potencia', m[0] + ' W', 'nombre del producto')
    if not any(k in s for k in ('Estándar ATX', 'Estandar ATX', 'EstÃ¡ndar ATX')):
        m = re.search(r'ATX\s*(?:12V\s*)?v?(3\.[01])', n, re.I)
        if m:
            add(i, 'Estándar ATX', m.group(1), 'nombre del producto')
    if 'Modular' not in s:
        if re.search(r'full[- ]?modular|totalmente modular', low):
            add(i, 'Modular', 'Totalmente modular', 'nombre del producto')
        elif re.search(r'semi[- ]?modular', low):
            add(i, 'Modular', 'Semimodular', 'nombre del producto')
    if 'Eficiencia 80 PLUS' not in s and 'Eficiencia' not in s:
        m = re.search(r'80\s*\+?\s*plus\s*(bronze|silver|gold|platinum|titanium)', low)
        if m:
            add(i, 'Eficiencia 80 PLUS', '80 PLUS ' + m.group(1).capitalize(), 'nombre del producto')
    if 'Formato' not in s:
        m = re.search(r'\b(SFX-L|SFX|TFX)\b', n)
        if m:
            add(i, 'Formato', m.group(1), 'nombre del producto')

# ---------- COOLER
for i in CAT['cooler']:
    s = P[i]['specs']; n = nm(i)
    if 'Tipo' not in s and re.search(r'\b(AIO|liquid|líquid|loop|kraken|nautilus)', n, re.I):
        add(i, 'Tipo', 'Líquida AIO', 'nombre del producto')
    if str(s.get('Tipo', '')).startswith('Líquida') and 'Radiador' not in s:
        m = re.findall(r'(?<!\d)(120|140|240|280|360|420)\s*(?:mm)?\b', n)
        if len(set(m)) == 1:
            add(i, 'Radiador', m[0] + ' mm', 'nombre del producto')

# ---------- RAM
for i in CAT['ram']:
    s = P[i]['specs']; n = nm(i); t = str(s.get('Tipo', ''))
    if 'Formato' not in s:
        src = t + ' ' + n
        if re.search(r'so-?dimm', src, re.I) and not re.search(r'c-?so-?dimm', src, re.I):
            add(i, 'Formato', 'SODIMM', 'tipo/nombre del producto')
        elif re.search(r'\bu-?dimm\b', src, re.I):
            add(i, 'Formato', 'UDIMM', 'tipo/nombre del producto')
        elif re.search(r'\brdimm\b', src, re.I):
            add(i, 'Formato', 'RDIMM', 'tipo/nombre del producto')
    if 'Tipo de memoria' not in s:
        m = re.search(r'\b(DDR[345]L?)\b', t + ' ' + n)
        if m:
            add(i, 'Tipo de memoria', m.group(1), 'tipo/nombre del producto')
    if 'Latencia' not in s:
        m = re.findall(r'\bCL\s?(\d{2})\b', n, re.I)
        if len(set(m)) == 1:
            add(i, 'Latencia', 'CL' + m[0], 'nombre del producto')
    if 'Módulos' not in s and 'Capacidad' in s:
        m = re.search(r'\b(\d)\s*[x×]\s*(\d{1,3})\s*GB\b', n, re.I)
        cap = re.match(r'^(\d+)\s*GB$', str(s.get('Capacidad', '')))
        if m and cap and int(m.group(1)) * int(m.group(2)) == int(cap.group(1)):
            add(i, 'Módulos', f'{m.group(1)} × {m.group(2)} GB', 'nombre del producto (coherente con la capacidad total)')

# ---------- MOTHERBOARD
SOCK = {**{c: 'AM4' for c in 'A320 B350 X370 B450 X470 A520 B550 X570'.split()},
        **{c: 'AM5' for c in 'A620 B650 B650E X670 X670E B840 B850 X870 X870E'.split()},
        **{c: 'LGA1700' for c in 'H610 B660 H670 Z690 B760 H770 Z790'.split()},
        **{c: 'LGA1851' for c in 'H810 B860 H870 Z890'.split()},
        **{c: 'LGA1200' for c in 'H410 B460 H470 Z490 H510 B560 H570 Z590'.split()}}
CHIP_RE = re.compile(r'\b(' + '|'.join(sorted(SOCK, key=len, reverse=True)) + r')\b')
for i in CAT['motherboard']:
    s = P[i]['specs']; n = nm(i)
    chip = s.get('Chipset')
    if not chip:
        m = CHIP_RE.search(n)
        if m:
            c = m.group(1)
            chip = ('AMD ' if SOCK[c].startswith('AM') else 'Intel ') + c
            add(i, 'Chipset', chip, 'nombre del producto')
    if 'Socket' not in s and chip and chip.split()[-1] in SOCK:
        add(i, 'Socket', SOCK[chip.split()[-1]], 'el socket queda fijado por el chipset ' + chip + ' (misma ficha)')
    if 'Wi-Fi' not in s:
        m = re.search(r'wi-?fi\s?(7|6E|6|5)\b', n, re.I)
        if m:
            add(i, 'Wi-Fi', 'Wi-Fi ' + m.group(1).upper(), 'nombre del producto')
    if 'Formato' not in s:
        if re.search(r'micro-?atx|\bm-?atx\b', n, re.I):
            add(i, 'Formato', 'Micro-ATX', 'nombre del producto')
        elif re.search(r'mini-?itx', n, re.I):
            add(i, 'Formato', 'Mini-ITX', 'nombre del producto')
        elif re.search(r'e-?atx', n, re.I):
            add(i, 'Formato', 'E-ATX', 'nombre del producto')

# ---------- GPU
G5 = re.compile(r'^(GeForce RTX 50\d0( Ti)?|Radeon RX 90\d0( XT| GRE)?)$')
for i in CAT['gpu']:
    s = P[i]['specs']; n = nm(i)
    if 'Chip gráfico' not in s:
        m = re.search(r'(GeForce RTX\s?\d{4}(?:\s?Ti)?(?:\s?SUPER)?|GeForce GTX\s?\d{3,4}(?:\s?Ti)?|Radeon RX\s?\d{4}(?:\s?(?:XTX|XT|GRE))?|Arc [AB]\d{3})', n, re.I)
        if m:
            raw = re.sub(r'\s+', ' ', m.group(1))
            raw = re.sub(r'(RTX|GTX|RX)(\d)', r'\1 \2', raw)
            add(i, 'Chip gráfico', sn.normalize_gpu_chip(raw), 'nombre del producto')
    chip = s.get('Chip gráfico')
    if chip and 'Interfaz' not in s and G5.match(chip):
        add(i, 'Interfaz', 'PCIe 5.0', 'especificación oficial del chip (RTX 50 / RX 9000 son PCIe 5.0)')

# ---------- validar con normalize_specs: solo se acepta el valor normalizado de las claves nuevas
by = collections.defaultdict(list)
for p in props:
    by[p['product_id']].append(p)
out = []; bad = 0
for pid, ps in by.items():
    cat = P[pid]['category_id']
    t = dict(P[pid]['specs'])
    for p in ps:
        for r in p['remove']:
            t.pop(r, None)
        t[p['key']] = p['new']
    n = sn.normalize_specs(cat, t)
    for p in ps:
        v = n.get(p['key'])
        if not isinstance(v, str) or not v or len(v) > 250 or len(p['key']) > 80:
            bad += 1
            continue
        p['new'] = v
        out.append(p)
_p = lambda *a: print(*a, file=sys.stderr)
_p('propuestas', len(out), 'inválidas', bad, 'productos', len({p['product_id'] for p in out}))
for k, v in collections.Counter((p['category'], p['key']) for p in out).most_common():
    _p(' ', k, v)
json.dump(out, sys.stdout, ensure_ascii=False, indent=1)  # redirigir a work/specs-fill-<fecha>/dryrun-derive.json (el contenedor no monta work/)
