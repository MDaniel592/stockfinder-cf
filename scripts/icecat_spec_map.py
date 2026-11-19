"""Mapeo explicito de caracteristicas de Icecat (EN) a claves canonicas del catalogo, por categoria.

Principios: tablas y reglas exactas, sin heuristicas difusas. Si un valor es ambiguo o contiene algun elemento que no esta
en la tabla, la clave NO se mapea (mejor omitir que inventar). El resultado de cada regla se pasa luego por
nas/spec_normalize.normalize_specs (lo hace specs_icecat_dryrun.py / specs_icecat_apply.py), aqui solo se traduce.

Entrada: lista de caracteristicas resumidas [{'name','pv'(PresentationValue),'raw'(RawValue),'unit','type'}] de
scripts/icecat_specs_fetch.py. Salida: {clave canonica: (valor, nombre de la caracteristica Icecat de origen)}."""
import re

TARGET_KEYS = {
    'case': ['Panel lateral', 'Formato fuente', 'Altura disipador máxima', 'Longitud GPU máxima', 'Formatos compatibles', 'Color', 'Ventiladores incluidos'],
    'psu': ['Estándar ATX', 'Conector GPU', 'Modular', 'Eficiencia 80 PLUS', 'Potencia', 'Formato'],
    'ram': ['Perfil', 'Formato', 'Tipo de memoria', 'Latencia', 'Velocidad', 'Módulos', 'Capacidad'],
    'storage': ['Tipo', 'Interfaz', 'Capacidad', 'Lectura secuencial', 'Escritura secuencial', 'Formato'],
    'cooler': ['Sockets compatibles', 'Tipo', 'TDP', 'Altura', 'Radiador', 'Ventiladores'],
    'motherboard': ['Wi-Fi', 'Memoria máxima', 'Ranuras de memoria', 'Formato', 'Socket', 'Chipset'],
}

COLOR = {'Black': 'Negro', 'White': 'Blanco', 'Grey': 'Gris', 'Gray': 'Gris', 'Red': 'Rojo', 'Blue': 'Azul', 'Green': 'Verde'}
BOARD_FORMS = {'ATX': 'ATX', 'Micro-ATX': 'Micro-ATX', 'microATX': 'Micro-ATX', 'Micro ATX': 'Micro-ATX', 'mATX': 'Micro-ATX',
               'Mini-ITX': 'Mini-ITX', 'Extended ATX': 'E-ATX', 'E-ATX': 'E-ATX', 'EATX': 'E-ATX'}  # 'Mini-ATX' es ambiguo: no se mapea
PSU_FORMS = {'ATX': 'ATX', 'SFX': 'SFX', 'SFX-L': 'SFX-L', 'TFX': 'TFX', 'Flex ATX': 'Flex ATX'}
SOCKETS = {'Socket AM4': 'AM4', 'Socket AM5': 'AM5', 'Socket AM3+': 'AM3+', 'Socket AM3': 'AM3', 'Socket AM2+': 'AM2+',
           'Socket AM2': 'AM2', 'LGA 1851': 'LGA1851', 'LGA 1700': 'LGA1700', 'LGA 1200': 'LGA1200',
           'LGA 1200 (Socket H5)': 'LGA1200', 'LGA 1151': 'LGA1151', 'LGA 1151 (Socket H4)': 'LGA1151',
           'LGA 1150 (Socket H3)': 'LGA1150', 'LGA 1155 (Socket H2)': 'LGA1155', 'LGA 1156 (Socket H)': 'LGA1156',
           'LGA 1366 (Socket B)': 'LGA1366', 'LGA 2066': 'LGA2066', 'LGA 2011 (Socket R)': 'LGA2011',
           'LGA 2011-v3 (Socket R3)': 'LGA2011-v3', 'LGA 2011-3': 'LGA2011-v3'}
COOLER_TYPES = {'All-in-one liquid cooler': 'Líquida AIO', 'Processor cooler': 'Aire'}
WIFI = {'Wi-Fi 7 (802.11be)': 'Wi-Fi 7', 'Wi-Fi 6E (802.11ax)': 'Wi-Fi 6E', 'Wi-Fi 6 (802.11ax)': 'Wi-Fi 6',
        'Wi-Fi 5 (802.11ac)': 'Wi-Fi 5'}
PSU_CABLING = {'Fully modular': 'Totalmente modular', 'Semi-modular': 'Semimodular', 'Non-Modular': 'Cables fijos'}
STORAGE_IF = {'Serial ATA III': 'SATA III'}
FAN_LOCATIONS = {'Front': ('Front fans installed', 'Number of front fans supported (max)'),
                 'Rear': ('Rear fans installed', 'Number of rear fans supported (max)'),
                 'Top': ('Top fans installed', 'Number of top fans supported (max)'),
                 'Side': ('Side fans installed', 'Number of side fans supported (max)'),
                 'Bottom': ('Bottom fans installed', 'Number of bottom fans supported (max)')}


def _idx(features):
    out = {}
    for f in features or []:
        if f.get('name') and f['name'] not in out and f.get('pv') not in (None, ''):
            out[f['name']] = f
    return out


def _num(f):
    """RawValue numerico (float) de una caracteristica, o None."""
    try:
        return float(str(f['raw']).replace(',', '.'))
    except (TypeError, ValueError, KeyError):
        return None


def _fmt(x):
    return str(int(x)) if float(x).is_integer() else ('%g' % x)


def _mm(f):
    if f.get('unit') != 'mm':
        return None
    n = _num(f)
    return f'{_fmt(n)} mm' if n and n > 0 else None


def _split(f):
    return [p.strip() for p in str(f['raw']).split(',') if p.strip()]


def _int(f):
    n = _num(f)
    return int(n) if n is not None and n == int(n) and n >= 0 else None


def _capacity(f):
    n, unit = _num(f), f.get('unit')
    if not n or n <= 0 or unit not in ('GB', 'TB', 'MB'):
        return None
    gb = n * {'GB': 1, 'TB': 1000, 'MB': 0.001}[unit]
    if gb >= 1000 and (gb / 1000).is_integer():
        return f'{_fmt(gb / 1000)} TB'
    return f'{_fmt(gb)} GB' if float(gb).is_integer() else None


def _speed(f):
    n, unit = _num(f), f.get('unit')
    if not n or n <= 0 or unit not in ('MB/s', 'GB/s'):
        return None
    mb = n * (1000 if unit == 'GB/s' else 1)
    return f'{_fmt(mb)} MB/s' if float(mb).is_integer() else None


def _fans(ix, count_key, diam_key):
    """'N × D mm' si hay numero y diametro unicos."""
    if count_key not in ix or diam_key not in ix:
        return None
    n, d = _int(ix[count_key]), ix[diam_key]
    if not n or d.get('unit') != 'mm' or _num(d) is None or len(_split(d)) != 1:
        return None
    return f'{n} × {_fmt(_num(d))} mm'


def map_case(ix):
    out = {}
    f = ix.get('Tempered glass panel(s)')
    if f and f['raw'] == 'Y':
        out['Panel lateral'] = ('Cristal templado', 'Tempered glass panel(s)')
    f = ix.get('Supported power supply form factors')
    if f and f['raw'] and all(p in PSU_FORMS for p in _split(f)):
        out['Formato fuente'] = (', '.join(PSU_FORMS[p] for p in _split(f)), f['name'])
    f = ix.get('Maximum CPU cooler height')
    if f and _mm(f):
        out['Altura disipador máxima'] = (_mm(f), f['name'])
    f = ix.get('Maximum graphics card length')
    if f and _mm(f):
        out['Longitud GPU máxima'] = (_mm(f), f['name'])
    f = ix.get('Supported motherboard form factors')
    if f and _split(f) and all(p in BOARD_FORMS for p in _split(f)):
        out['Formatos compatibles'] = (', '.join(BOARD_FORMS[p] for p in _split(f)), f['name'])
    f = ix.get('Product colour')
    if f and f['raw'] in COLOR:  # un solo color; las combinaciones ("Black, Transparent") son ambiguas
        out['Color'] = (COLOR[f['raw']], f['name'])
    # Ventiladores incluidos: solo si cada ubicacion con soporte >0 declara sus instalados y todos son "Nx D mm"
    total, ok, src = {}, True, []
    for loc, (inst, sup) in FAN_LOCATIONS.items():
        s = ix.get(sup)
        supported = _int(s) if s else 0
        f = ix.get(inst)
        if f:
            m = re.fullmatch(r'(\d+)x (\d+) mm', f['raw'])
            if not m:
                ok = False
                break
            total[int(m.group(2))] = total.get(int(m.group(2)), 0) + int(m.group(1))
            src.append(inst)
        elif supported is None or supported > 0:
            ok = False
            break
    if ok and total:
        out['Ventiladores incluidos'] = (' + '.join(f'{n} × {d} mm' for d, n in sorted(total.items(), reverse=True)), ', '.join(src))
    return out


def map_psu(ix):
    out = {}
    f = ix.get('Total power')
    if f and f.get('unit') == 'W' and _int(f):
        out['Potencia'] = (f"{_int(f)} W", f['name'])
    f = ix.get('ATX version')
    if f and re.fullmatch(r'\d\.\d', f['raw']):
        out['Estándar ATX'] = (f['raw'], f['name'])
    f = ix.get('Cabling type')
    if f and f['raw'] in PSU_CABLING:
        out['Modular'] = (PSU_CABLING[f['raw']], f['name'])
    f = ix.get('80 PLUS certification')
    if f and re.fullmatch(r'80 PLUS( Bronze| Silver| Gold| Platinum| Titanium)?', f['raw']):
        out['Eficiencia 80 PLUS'] = (f['raw'], f['name'])
    f = ix.get('Power supply unit (PSU) form factor')
    if f and f['raw'] in PSU_FORMS:
        out['Formato'] = (PSU_FORMS[f['raw']], f['name'])
    # Conector GPU: solo PCIe 6+2 sin conector de 16 pines ni de 6 pines (12VHPWR/12V-2x6 es ambiguo)
    f = ix.get('PCI Express power connectors (6+2 pin)')
    extra = [ix.get(k) for k in ('PCI Express power connectors (16-pin)', 'PCI Express power connectors (6-pin)', 'PCI Express power connectors (8-pin)')]
    if f and _int(f) and all(e is None or _int(e) == 0 for e in extra):
        out['Conector GPU'] = (f"{_int(f)} × PCIe 6+2 pines", f['name'])
    return out


def map_ram(ix):
    out = {}
    f = ix.get('Internal memory')
    if f and _capacity(f):
        out['Capacidad'] = (_capacity(f), f['name'])
    f = ix.get('Internal memory type')
    if f and re.fullmatch(r'DDR[2-5]', f['raw']):
        out['Tipo de memoria'] = (f['raw'], f['name'])
    f = ix.get('CAS latency')
    if f and _int(f):
        out['Latencia'] = (f"CL{_int(f)}", f['name'])
    f = ix.get('Memory data transfer rate')
    if f and f.get('unit') == 'MT/s' and _int(f):
        out['Velocidad'] = (f"{_int(f)} MT/s", f['name'])
    f = ix.get('Memory layout (modules x size)')
    m = re.fullmatch(r'(\d+) x (\d+)', f['raw']) if f and f.get('unit') == 'GB' else None
    if m:
        out['Módulos'] = (f"{m.group(1)} × {m.group(2)} GB", f['name'])
    ff, buf = ix.get('Memory form factor'), ix.get('Buffered memory type')
    if ff and re.search(r'SO-DIMM', ff['raw']):
        out['Formato'] = ('SODIMM', ff['name'])
    elif ff and re.fullmatch(r'\d+-pin DIMM', ff['raw']) and buf:
        if buf['raw'] == 'Unregistered (unbuffered)':
            out['Formato'] = ('UDIMM', ff['name'] + ' + ' + buf['name'])
        elif buf['raw'] in ('Registered (buffered)', 'Registered'):
            out['Formato'] = ('RDIMM', ff['name'] + ' + ' + buf['name'])
    return out


def map_storage(ix):
    out = {}
    ssd, hdd = ix.get('SSD capacity'), ix.get('HDD capacity')
    if bool(ssd) != bool(hdd):
        out['Tipo'] = ('SSD' if ssd else 'HDD', 'SSD capacity' if ssd else 'HDD capacity')
    cap = ssd or hdd
    if bool(ssd) != bool(hdd) and _capacity(cap):
        out['Capacidad'] = (_capacity(cap), cap['name'])
    f = ix.get('Read speed')
    if f and _speed(f):
        out['Lectura secuencial'] = (_speed(f), f['name'])
    f = ix.get('Write speed')
    if f and _speed(f):
        out['Escritura secuencial'] = (_speed(f), f['name'])
    f = ix.get('Interface')
    if f and f['raw'] in STORAGE_IF:
        out['Interfaz'] = (STORAGE_IF[f['raw']], f['name'])
    elif f:
        m = re.fullmatch(r'PCI Express (\d\.0)', f['raw'])
        lanes, nvme = ix.get('PCI Express interface data lanes'), ix.get('NVMe')
        if m and lanes and re.fullmatch(r'x\d{1,2}', lanes['raw']) and nvme and nvme['raw'] == 'Y':
            ver = ix.get('NVMe version')
            tail = f"NVMe {ver['raw']}" if ver and re.fullmatch(r'\d\.\d', ver['raw']) else 'NVMe'
            out['Interfaz'] = (f"PCIe {m.group(1)} {lanes['raw']}, {tail}", f['name'] + ' + ' + lanes['name'] + ' + ' + nvme['name'])
    f = ix.get('M.2 SSD size')
    m = re.match(r'(22(?:30|42|60|80|110))\b', f['raw']) if f else None
    if m:
        out['Formato'] = (f'M.2 {m.group(1)}', f['name'])
    else:
        for k in ('SSD form factor', 'HDD size'):
            f = ix.get(k)
            if f and f['raw'] in ('2.5"', '3.5"'):
                out['Formato'] = (f['raw'], f['name'])
                break
    return out


def map_cooler(ix):
    out = {}
    f = ix.get('Supported processor sockets')
    if f and _split(f) and all(p in SOCKETS for p in _split(f)):
        out['Sockets compatibles'] = (', '.join(SOCKETS[p] for p in _split(f)), f['name'])
    t = ix.get('Type')
    ctype = COOLER_TYPES.get(t['raw']) if t else None
    if ctype:
        out['Tipo'] = (ctype, t['name'])
    f = ix.get('Thermal Design Power (TDP)')
    if f and f.get('unit') == 'W' and _int(f):
        out['TDP'] = (f"{_int(f)} W", f['name'])
    if ctype == 'Aire':  # en AIO "Height" es el bloque de agua: solo se usa en aire
        f = ix.get('Height')
        if f and _mm(f):
            out['Altura'] = (_mm(f), f['name'])
    fans = _fans(ix, 'Number of fans', 'Fan diameter')
    f2 = ix.get('Fan diameter 2')
    if fans and not (f2 and _num(f2) != _num(ix['Fan diameter'])):
        out['Ventiladores'] = (fans, 'Number of fans + Fan diameter')
        # Radiador: clase = ventiladores x diametro, solo si el ancho real del radiador es coherente (clase + 25..50 mm)
        n, d = _int(ix['Number of fans']), _num(ix['Fan diameter'])
        w = ix.get('Radiator width')
        if ctype == 'Líquida AIO' and n and d and w and w.get('unit') == 'mm' and _num(w) is not None:
            cls = n * d
            if cls in (120, 240, 280, 360, 420) and cls + 25 <= _num(w) <= cls + 50:
                out['Radiador'] = (f'{_fmt(cls)} mm', 'Number of fans + Fan diameter + Radiator width')
    return out


def map_motherboard(ix):
    out = {}
    f = ix.get('Wi-Fi')
    top = ix.get('Top Wi-Fi standard')
    if f and f['raw'] == 'Y' and top and top['raw'] in WIFI:
        out['Wi-Fi'] = (WIFI[top['raw']], top['name'])
    elif f and f['raw'] == 'N':
        out['Wi-Fi'] = ('No integrado', f['name'])
    f = ix.get('Maximum internal memory')
    if f and f.get('unit') == 'GB' and _int(f):
        n = _int(f)
        out['Memoria máxima'] = (f'{n // 1024} TB' if n >= 1024 and n % 1024 == 0 else f'{n} GB', f['name'])
    f = ix.get('Number of memory slots')
    if f and _int(f):
        out['Ranuras de memoria'] = (str(_int(f)), f['name'])
    f = ix.get('Motherboard form factor')
    if f and f['raw'] in BOARD_FORMS:
        out['Formato'] = (BOARD_FORMS[f['raw']], f['name'])
    f = ix.get('Motherboard chipset')
    if f and re.fullmatch(r'(AMD|Intel) [A-Z]\d{3}[A-Z]?E?', f['raw']):
        out['Chipset'] = (f['raw'], f['name'])
    f = ix.get('Processor socket')
    if f and f['raw'] in SOCKETS:
        out['Socket'] = (SOCKETS[f['raw']], f['name'])
    return out


MAPPERS = {'case': map_case, 'psu': map_psu, 'ram': map_ram, 'storage': map_storage, 'cooler': map_cooler,
           'motherboard': map_motherboard}


def map_features(category, features):
    """{clave canonica: (valor, caracteristica Icecat)} restringido a TARGET_KEYS de la categoria."""
    mapper = MAPPERS.get(category)
    if not mapper:
        return {}
    got = mapper(_idx(features))
    return {k: v for k, v in got.items() if k in TARGET_KEYS[category]}
