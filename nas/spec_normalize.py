"""Canonical spec keys and value spellings, applied on every catalog import (old and new batches alike).

Only spelling is normalized: units, case, spacing, decimal separator, vendor-prefix duplicates and English/Spanish
duplicate keys. Facts are never merged (TDP vs TBP/TGP stay distinct) and nothing is invented: an alias key is
renamed only when the canonical key is absent, and dropped only when it carries the same value or is pure placeholder junk."""
import re

# category -> {alias key: canonical key}
KEY_ALIASES = {
    'motherboard': {'socket': 'Socket', 'chipset': 'Chipset', 'Form factor': 'Formato', 'form_factor': 'Formato',
                    'Memory generation': 'Tipo de memoria', 'memory': 'Tipo de memoria', 'model': 'Modelo',
                    'network': 'Wi-Fi'},
    'gpu': {'Boost clock': 'Frecuencia turbo', 'Frecuencia boost': 'Frecuencia turbo', 'Game clock': 'Frecuencia de juego',
            'Stream processors': 'Procesadores de flujo', 'GPU': 'Chip gráfico'},
    'psu': {'Modularidad': 'Modular', 'Estándar': 'Estándar ATX', 'Norma ATX': 'Estándar ATX', 'ATX': 'Estándar ATX',
            'Form factor': 'Formato', 'Factor de forma': 'Formato'},
    'storage': {'Tipo de unidad': 'Tipo', 'Tipo de disco': 'Tipo', 'Velocidad de rotacion': 'Velocidad de rotación',
                'RPM': 'Velocidad de rotación', 'Cache': 'Caché', 'Cache de disco': 'Caché'},
    'case': {'Formatos de placa base': 'Formatos compatibles', 'Placas base compatibles': 'Formatos compatibles',
             'Model': 'Modelo', 'Edition': 'Edición', 'Materials': 'Materiales', 'Front I/O': 'Panel frontal',
             'Clearance': 'Espacio libre', 'Chassis dimensions': 'Dimensiones', 'Fan capacity': 'Capacidad de ventiladores',
             # Claves que traen Neobyte (inglés) y Abysm; la tabla y el configurador solo leen las canónicas de arriba.
             **{k: 'Longitud GPU máxima' for k in ('GPU máxima', 'Longitud maxima GPU', 'Longitud máxima GPU', 'Maximum GPU length',
                                                    'Maximum graphics card length')},
             **{k: 'Altura disipador máxima' for k in ('Altura maxima disipador CPU', 'Maximum CPU cooler height')},
             **{k: 'Formatos compatibles' for k in ('Placas base', 'Motherboard support')},
             'Dimensions': 'Dimensiones'},
    'cooler': {'Cooling type': 'Tipo', 'TDP máximo': 'TDP', 'Sockets': 'Sockets compatibles'},
    'ram': {'color': 'Color', 'capacity': 'Capacidad', 'speed': 'Velocidad', 'latency': 'Latencia', 'model': 'Línea',
            'Latencia CAS': 'Latencia', 'Perfil de memoria': 'Perfil', 'Memory type': 'Tipo de memoria', 'Capacity': 'Capacidad',
            'Tested speed': 'Velocidad', 'Velocidad probada': 'Velocidad', 'Velocidad probada (XMP/EXPO)': 'Velocidad',
            'Configuración': 'Módulos', 'Profile': 'Perfil', 'Serie': 'Línea', 'Color del disipador': 'Color',
            'Factor de forma': 'Formato', 'Module type': 'Formato',
            **{k: 'Timings' for k in ('Tested timings', 'Tiempos probados', 'Tiempos probados (XMP)', 'Tiempos probados (XMP/EXPO)')},
            **{k: 'Voltaje' for k in ('Tested voltage', 'Voltaje probado', 'Voltaje probados (XMP/EXPO)', 'Voltaje (XMP)', 'Voltaje (XMP/EXPO)')}},
}
# Aliases only renamed when the value looks like the canonical one (a fan count, not a fan model).
CONDITIONAL_ALIASES = {('cooler', 'Fans'): ('Ventiladores', r'^\d+\s*[×x]'), ('cooler', 'Ventilador'): ('Ventiladores', r'^\d+\s*[×x]')}
# Drop when the canonical key is present (family/duplicate of it) or the value is a placeholder.
DROP_IF_CANONICAL = {'motherboard': {'Chipset/model family': 'Chipset', 'Chipset/model': 'Chipset'}}
JUNK_KEYS = re.compile(r'^(Detail \d+|Dato oficial \d+)$')
JUNK_VALUES = {('motherboard', 'Wireless networking'): {'Wi-Fi variant'},
               ('motherboard', 'Conectividad inalámbrica'): {'wireless networking is part of the named model'}}

COLORS = {'black': 'Negro', 'grey': 'Gris', 'gray': 'Gris', 'white': 'Blanco', 'red': 'Rojo', 'blue': 'Azul', 'green': 'Verde'}
FORMATS = [(re.compile(r'\bmicro[\s-]?atx\b|\bm[\s-]?atx\b', re.I), 'Micro-ATX'),
           (re.compile(r'\bmini[\s-]?itx\b', re.I), 'Mini-ITX'),
           (re.compile(r'(?<![A-Za-z-])e[\s-]?atx\b', re.I), 'E-ATX')]

# --- List-valued specs --------------------------------------------------------------------------------------------
# Stored as a canonical ", "-separated list: canonical tokens, no duplicates, fixed order. The edge API lists each token as
# a facet and matches on delimited tokens, so ORDER lists below must mirror multiValueSpecs in
# packages/api-contracts/catalog-filters.ts (tests/test_nas.py checks that they agree).
LIST_ORDER = {
    ('case', 'Formatos compatibles'): ['EE-ATX', 'SSI-EEB', 'SSI-CEB', 'E-ATX', 'ATX', 'Micro-ATX', 'Mini-ITX'],
    ('case', 'Formato fuente'): ['ATX', 'SFX-L', 'SFX'],
    ('cooler', 'Sockets compatibles'): ['AM5', 'AM4', 'AM3+', 'AM3', 'AM2+', 'AM2', 'FM2+', 'FM2', 'FM1', 'TR5', 'TR4',
        'LGA1851', 'LGA1700', 'LGA1200', 'LGA2066', 'LGA1151', 'LGA2011-v3', 'LGA1150', 'LGA2011', 'LGA1155', 'LGA1156',
        'LGA1366', 'LGA115x'],
    ('ram', 'Perfil'): ['AMD EXPO', 'AMD EXPO 1.1', 'Intel XMP', 'Intel XMP 3.0', 'Intel XMP 2.0'],
}
LIST_SPLIT = re.compile(r'\s*[,;/]\s*')
FORM_FACTORS = {'atx': 'ATX', 'matx': 'Micro-ATX', 'microatx': 'Micro-ATX', 'itx': 'Mini-ITX', 'miniitx': 'Mini-ITX',
                'eatx': 'E-ATX', 'eeatx': 'EE-ATX', 'ssieeb': 'SSI-EEB', 'ssiceb': 'SSI-CEB',
                'sfx': 'SFX', 'sfxl': 'SFX-L', 'tfx': 'TFX', 'flexatx': 'Flex ATX'}
# Words that describe something other than a form factor (e.g. "reverse connector"): moved out of the format list.
FORMAT_EXTRA = re.compile(r'reverse|connector|conector|trasero|back[\s-]?connect|\bBTF\b', re.I)
EXTRA_KEY = 'Compatibilidad conectores traseros'

def _ordered(category, key, tokens):
    order = LIST_ORDER[(category, key)]
    seen = []
    for token in tokens:
        if token and token not in seen: seen.append(token)
    # known tokens by canonical rank; unknown ones keep their relative order after them
    return sorted(seen, key=lambda t: order.index(t) if t in order else len(order))

def _form_factor_tokens(value):
    """(format tokens, non-format words) of a form-factor list."""
    formats, extras = [], []
    for piece in LIST_SPLIT.split(value):
        piece = piece.strip()
        if not piece: continue
        canon = FORM_FACTORS.get(re.sub(r'[\s_-]', '', piece).casefold())
        if canon: formats.append(canon)
        elif FORMAT_EXTRA.search(piece): extras.append(piece)
        else: formats.append(piece)
    return formats, extras

def _form_factor_list(category, key, value):
    return ', '.join(_ordered(category, key, _form_factor_tokens(value)[0]))

def _socket_list(category, key, value):
    value = _sockets(re.sub(r'\b(?:Intel|AMD)\s+', '', value))
    tokens, prefix = [], None
    for piece in LIST_SPLIT.split(value):
        piece = piece.strip()
        if not piece: continue
        named = re.fullmatch(r'(LGA|AM|FM|TR)\s*(\d[0-9a-z+-]*)', piece, re.I)
        if named:
            prefix = named.group(1).upper()
            piece = prefix + named.group(2).replace('X', 'x').replace('-V', '-v')
        elif prefix and re.fullmatch(r'\d{3,4}[xX]?(?:-[vV]\d)?', piece):
            piece = prefix + piece.replace('X', 'x').replace('-V', '-v')  # "LGA1150/1151": the prefix is shared
        tokens.append(piece)
    return ', '.join(_ordered(category, key, tokens))

def _memory_profiles(category, key, value):
    tokens = []
    for piece in re.split(r'\s*[,;/]\s*|\s+(?:y|e|and|&)\s+', value):
        m = re.fullmatch(r'(?:Intel\s+)?XMP(?:\s+(\d+(?:\.\d+)?))?', piece.strip(), re.I)
        e = re.fullmatch(r'(?:AMD\s+)?EXPO(?:\s+(\d+(?:\.\d+)?))?', piece.strip(), re.I)
        found = m or e
        if not found:
            tokens.append(piece.strip()); continue
        base = 'Intel XMP' if m else 'AMD EXPO'
        tokens.append(base)  # a versioned profile is also a profile of that family
        if found.group(1): tokens.append(f'{base} {found.group(1)}')
    return ', '.join(_ordered(category, key, tokens))

LIST_HANDLERS = {('case', 'Formatos compatibles'): _form_factor_list, ('case', 'Formato fuente'): _form_factor_list,
                 ('cooler', 'Sockets compatibles'): _socket_list, ('ram', 'Perfil'): _memory_profiles}

def _decimal(value):
    return re.sub(r'(?<=\d),(?=\d{1,2}(?!\d))', '.', value)

def _upper_first(value):
    return value[:1].upper() + value[1:]

def _sockets(value):
    value = re.sub(r'\bLGA\s*(\d+)\s*x\b', lambda m: f'LGA{m.group(1)}x', value, flags=re.I)
    value = re.sub(r'\bLGA\s*(\d{3,4})\b', r'LGA\1', value, flags=re.I)
    return re.sub(r'\bAM\s*(\d)\b', r'AM\1', value)

def _voltage(value):
    return re.sub(r'(\d+\.\d*?)0+(?=\s*V\b)', lambda m: m.group(1).rstrip('.') if m.group(1).endswith('.') else m.group(1), _decimal(value))

def _formats(value):
    for pattern, name in FORMATS: value = pattern.sub(name, value)
    return value


def normalize_gpu_chip(value):
    """Canonical chip label; preserve model and Ti/SUPER/XT/professional variants."""
    value = ' '.join(value.split())
    value = re.sub(r'^(?:AMD|NVIDIA|Intel)\s+(?=(?:GeForce|Radeon|Arc|RTX|Quadro|Tesla)\b)', '', value, flags=re.I)
    for pattern, label in [(r'^geforce\b', 'GeForce'), (r'^radeon\b', 'Radeon'), (r'^arc\b', 'Arc')]:
        value = re.sub(pattern, label, value, flags=re.I)
    value = re.sub(r'\b(?:RTX|GTX|GT|RX|XTX|XT|GRE|SUPER)\b', lambda m: m.group().upper(), value, flags=re.I)
    value = re.sub(r'\bti\b', 'Ti', value, flags=re.I)
    value = re.sub(r'^RTX\s+pro\b', 'RTX PRO', value, flags=re.I)
    arc = re.fullmatch(r'Arc\s+(?:(Pro)\s+)?([A-Z]\d{2,3})(?:\s+Graphics)?', value, flags=re.I)
    if arc:
        value = 'Arc ' + ('Pro ' if arc.group(1) else '') + arc.group(2).upper()
    return value


def _gpu_memory(value):
    return re.sub(r'(\d)\s*GB\b', r'\1 GB', value)

# --- PSU -----------------------------------------------------------------------------------------------------------
# `Eficiencia` used to mix two certifications. It is split into `Eficiencia 80 PLUS` ("80 PLUS", "80 PLUS Gold"...),
# `Cybenetics` ("Cybenetics Gold"...) and, only when stated, `Cybenetics ruido` ("A-"). A tier is never invented.
EFF_KEYS = {'Eficiencia': None, 'Eficiencia 80 PLUS': '80', 'Certificación 80 PLUS': '80', '80 PLUS': '80',
            'Cybenetics': 'cyb', 'Certificación Cybenetics': 'cyb', 'Cybenetics ruido': 'noise'}
EFF_ORDER = ('Eficiencia 80 PLUS', 'Cybenetics', 'Cybenetics ruido')
TIERS = {'bronze': 'Bronze', 'bronce': 'Bronze', 'silver': 'Silver', 'plata': 'Silver', 'gold': 'Gold', 'oro': 'Gold',
         'platinum': 'Platinum', 'platino': 'Platinum', 'titanium': 'Titanium', 'titanio': 'Titanium',
         'diamond': 'Diamond', 'diamante': 'Diamond'}
_TIER = re.compile(r'\b(' + '|'.join(TIERS) + r')\b', re.I)
_NOISE = re.compile(r'(?i:ruido|noise|lambda)\W*(A\+\+|A\+|A-|A|B|C|D|E|F)(?![A-Za-z0-9])')
_80PLUS = re.compile(r'80\s*(?:plus|\+)', re.I)
_EFF_SPLIT = re.compile(r'\s*[/;,]\s*|\s+\+\s+|\s+y\s+')
EFF_MARK = '\0eff'

def _efficiency(context, value):
    """Split a (possibly mixed) efficiency string into ({canonical key: value}, [unparsed pieces])."""
    found, rest = {}, []
    for piece in _EFF_SPLIT.split(value):
        piece = piece.strip()
        if not piece: continue
        if context == 'noise' and re.fullmatch(r'A\+\+|A\+|A-|A|B|C|D|E|F', piece): found.setdefault('Cybenetics ruido', piece); continue
        noise = _NOISE.search(piece)
        if noise:
            found.setdefault('Cybenetics ruido', noise.group(1))
            piece = _NOISE.sub('', piece).strip(' -:()')
            if not piece: continue
        tier = _TIER.search(piece)
        tier = TIERS[tier.group(1).casefold()] if tier else None
        kind = '80' if _80PLUS.search(piece) else 'cyb' if re.search('cybenetics', piece, re.I) else context if tier else None
        if kind == '80':
            new = '80 PLUS' + (f' {tier}' if tier else '')
            if found.get('Eficiencia 80 PLUS', '80 PLUS') == '80 PLUS': found['Eficiencia 80 PLUS'] = new  # a stated tier beats a bare "80 PLUS"
        elif kind == 'cyb':
            if tier: found.setdefault('Cybenetics', f'Cybenetics {tier}')
        else: rest.append(piece)
    return found, rest

def _atx_standard(value):
    """ATX power-supply standard: "3.1", "3.0" or "2.x" (any ATX12V 2.xx); other text is left untouched."""
    m = re.match(r'(?i)(?:ATX12V|ATX)?\s*v?\s*(\d)\.(\d+|x)\b', value)
    if not m: return value
    if m.group(1) == '2': return '2.x'
    if m.group(1) == '3' and m.group(2) != 'x': return f'3.{m.group(2)[0]}'
    return value

_PSU_FORMAT = re.compile(r'(?i)(ATX12V|ATX|SFX[\s-]?L|SFX|TFX|Flex\s*ATX)(?:\s+v?(\d\.(?:\d+|x)))?')
_PSU_FORMS = {'atx12v': 'ATX', 'atx': 'ATX', 'sfxl': 'SFX-L', 'sfx': 'SFX', 'tfx': 'TFX', 'flexatx': 'Flex ATX'}

def _psu_modular(value):
    low = value.casefold()
    if re.search(r'semi[\s-]?modular|parcialmente modular', low): return 'Semimodular'
    if re.search(r'\bno[\s-]?modular|non[\s-]?modular|cables? fijos?|cableado fijo|hardwired', low): return 'Cables fijos'
    if re.search(r'totalmente modular|full(?:y)?[\s-]*modular|completamente modular|modular completo', low): return 'Totalmente modular'
    return value

def _gpu_connector(value):
    """12VHPWR (PCIe CEM 5.0) and 12V-2x6 (PCIe CEM 5.1) are different connectors: only the spelling is unified."""
    if re.search(r'[,;]|\by\b|\d\s*[x×]\s*(?:PCIe|12V|16)', value, re.I): return value  # several connectors: left untouched
    low = value.casefold()
    if re.search(r'12v[\s-]?2x6', low): return '12V-2x6'
    if re.search(r'12v[\s-]?hpwr', low): return '12VHPWR'
    if re.search(r'16\s*pin|12\s*\+\s*4', low):
        if re.search(r'\b5\.1\b', low): return '12V-2x6'
        if re.search(r'\b5\.0\b', low): return '12VHPWR'
    return value

def _psu_post(out, eff, eff_rest):
    """Expand the efficiency placeholder and split a standard glued to the form factor ("ATX 3.1")."""
    if EFF_MARK in out:
        new = {}
        for k, v in out.items():
            if k != EFF_MARK: new[k] = v; continue
            for ek in EFF_ORDER:
                if ek in eff: new[ek] = eff[ek]
            if eff_rest: new['Eficiencia'] = ', '.join(eff_rest)  # unparseable text is kept, never dropped
        out = new
    m = _PSU_FORMAT.fullmatch(out['Formato']) if isinstance(out.get('Formato'), str) else None
    if m:
        new = {}
        for k, v in out.items():
            new[k] = _PSU_FORMS[re.sub(r'[\s_-]', '', m.group(1)).casefold()] if k == 'Formato' else v
            if k == 'Formato' and m.group(2) and 'Estándar ATX' not in out: new['Estándar ATX'] = _atx_standard(m.group(2))
        out = new
    return out

# --- Filter hygiene: one clean value per facet, extras moved to their own detail key ------------------------------
def _insert_after(out, after, key, value):
    """Add key=value right after `after` unless the key already exists (an explicit fact always wins)."""
    if key in out or not value: return out
    new = {}
    for k, v in out.items():
        new[k] = v
        if k == after: new[key] = value
    return new

def _cpu_memory(value):
    """"Hasta DDR5 5600 MT/s / DDR4 3200 MT/s" -> "DDR5-5600, DDR4-3200"; anything unparseable is left as written."""
    tokens = []
    for piece in re.split(r'\s*[,;]\s*|\s*/(?!s)\s*|\s+y\s+', value):
        m = re.fullmatch(r'(?i)(?:hasta\s+)?(DDR\d)(?:[\s-]+(\d{3,5}))?(?:\s*MT/s)?(\s+(?:RDIMM|UDIMM|ECC.*))?', piece.strip())
        if not m: return value
        tokens.append(m.group(1).upper() + (f'-{m.group(2)}' if m.group(2) else '') + (m.group(3).upper() if m.group(3) else ''))
    return ', '.join(dict.fromkeys(tokens))

RAM_SPEED = re.compile(r'(?i)(?:(?:up to|hasta)\s+)?(?:(DDR\d)[\s-]+)?(\d{3,5})(?:\s*(?:MT/s|MHz))?(?:\s+with\s+(?:XMP|EXPO)\S*)?')

def _ram_speed(value):
    """"DDR4-3200", "3200 MHz", "Up to 3200 MT/s with XMP" -> ("DDR4" or None, "3200 MT/s"); None when not a plain speed."""
    m = RAM_SPEED.fullmatch(value.strip())
    if not m or not (m.group(1) or re.search(r'(?i)MT/s|MHz', value)): return None
    return (m.group(1).upper() if m.group(1) else None), f'{m.group(2)} MT/s'

def _ram_details(specs):
    """Anonymous "Detail N" lines of a RAM dump -> the facts they state (timings, profile, module format)."""
    specs = dict(specs)
    for key in [k for k in specs if re.fullmatch(r'Detail \d+', k)]:
        value = specs[key]
        if not isinstance(value, str): continue
        value = ' '.join(value.split())
        m = re.fullmatch(r'CL\s*(\d+)((?:-\d+)+)', value)
        if m:
            specs.setdefault('Latencia', f'CL{m.group(1)}'); specs.setdefault('Timings', m.group(1) + m.group(2))
        elif re.fullmatch(r'(?i)(?:Intel\s+)?XMP(?:\s+\d\.\d)?|AMD\s+EXPO', value): specs.setdefault('Perfil', value)
        elif re.fullmatch(r'(?i)[RCSU]*DIMM', value): specs.setdefault('Formato', value.upper())
    return specs

def _ram_memory_string(out):
    """Spec dumps with one free-text "Memory" line ("16GB kit (2×8GB) DDR5-5200") -> separate type/speed keys; type is
    also taken from a "DDRn-speed" Velocidad. Only what the text states is used."""
    out = dict(out)
    text = out.get('Memory')
    if isinstance(text, str):
        m = re.search(r'\b(DDR\d)-(\d{3,5})\b', text)
        if m:
            del out['Memory']
            out.setdefault('Tipo de memoria', m.group(1).upper())
            out.setdefault('Velocidad', f'{m.group(2)} MT/s')
            cap = re.search(r'(\d+)\s*GB\b', text)
            kit = re.search(r'\((\d)\s*[×x]\s*(\d+)\s*GB\)', text)
            if cap: out.setdefault('Capacidad', f'{cap.group(1)} GB')
            if kit: out.setdefault('Módulos', f'{kit.group(1)} × {kit.group(2)} GB')
            elif cap and re.search(r'(?i)\bsingle\b', text): out.setdefault('Módulos', f'1 × {cap.group(1)} GB')
    return out

def _yes_no(out, key, detail_key, strip_vendor=False):
    """Boolean facet: "Sí"/"No", the stated detail (a model name, never invented) moves to detail_key."""
    value = out.get(key)
    if not isinstance(value, str): return out
    detail = None
    if re.match(r'(?i)no\b', value): answer = 'No'
    else:
        answer = 'Sí'
        m = re.fullmatch(r'(?i)s[ií]\s*[(:,-]\s*(.+?)\)?', value)
        if m: detail = m.group(1)
        elif not re.fullmatch(r'(?i)s[ií]', value): detail = value
    if detail and strip_vendor: detail = re.sub(r'^AMD\s+', '', detail)
    out = {**out, key: answer}
    return _insert_after(out, key, detail_key, detail)

def _extras(category, out):
    if category == 'cpu':
        out = _yes_no(out, 'Gráficos integrados', 'GPU integrada', strip_vendor=True)
        out = _yes_no(out, 'Disipador incluido', 'Modelo disipador')
        if isinstance(out.get('Memoria compatible'), str): out['Memoria compatible'] = _cpu_memory(out['Memoria compatible'])
    elif category == 'motherboard':
        m = re.fullmatch(r'(DDR\d),\s*(\d+)\s*(?:ranuras\s+)?DIMM(?:\s+ranuras)?,?\s*(?:m[aá]ximo|hasta)\s*(\d+)\s*GB', out.get('Memoria') or '', re.I)
        if m:  # one free-text line ("DDR5, 4 DIMM, máximo 256 GB") -> the three facets it states
            out = {k: v for k, v in out.items() if k != 'Memoria'}
            out.setdefault('Tipo de memoria', m.group(1).upper())
            out.setdefault('Ranuras de memoria', m.group(2))
            out.setdefault('Memoria máxima', f'{m.group(3)} GB')
        m = re.fullmatch(r'(.+?)\s*\((\d+(?:\.\d+)?\s*[×x]\s*\d+(?:\.\d+)?(?:\s*[×x]\s*\d+(?:\.\d+)?)?\s*(?:cm|mm))\)', out.get('Formato') or '')
        if m:
            out['Formato'] = _formats(m.group(1))
            out = _insert_after(out, 'Formato', 'Dimensiones', m.group(2).replace(' x ', ' × '))
    elif category == 'gpu':
        m = re.fullmatch(r'(\d+ GB)\s+((?:GDDR|HBM)\d\w*)', out.get('Memoria') or '')
        if m:
            out['Memoria'] = m.group(1)
            out = _insert_after(out, 'Memoria', 'Tipo de memoria', m.group(2))
        m = re.fullmatch(r'(PCIe \d\.\d)\s+(x\d+)', out.get('Interfaz') or '')
        if m:
            out['Interfaz'] = m.group(1)
            out = _insert_after(out, 'Interfaz', 'Enlace PCIe', m.group(2))
    elif category == 'ram':
        out = _ram_memory_string(out)
        m = re.fullmatch(r'(\d+) × (\d+) GB', out.get('Módulos') or '')
        if m and 'Capacidad' not in out:  # kit total is arithmetic on the stated modules
            out = {**out, 'Capacidad': f'{int(m.group(1)) * int(m.group(2))} GB'}
        m = re.fullmatch(r'(\d+ GB)\s*\((.+)\)', out.get('Capacidad') or '')
        if m:
            out['Capacidad'] = m.group(1)
            out = _insert_after(out, 'Capacidad', 'Módulos', re.sub(r'\s*[×x]\s*', ' × ', m.group(2)))
        form = out.get('Formato')
        if isinstance(form, str) and re.search(r'(?i)dimm', form):
            low = form.casefold()
            out['Formato'] = ('CUDIMM' if 'cudimm' in low or 'clocked' in low else 'CSODIMM' if 'csodimm' in low
                              else 'SODIMM' if re.search(r'so-?dimm', low) else 'RDIMM' if re.search(r'rdimm|registered', low) else 'UDIMM')
        kind = out.get('Tipo de memoria')
        if isinstance(kind, str) and re.fullmatch(r'DDR\d CUDIMM', kind):
            out['Tipo de memoria'] = kind.split()[0]
            out.setdefault('Formato', 'CUDIMM')
        lat = out.get('Latencia')
        if isinstance(lat, str):
            m = re.fullmatch(r'CL\s*(\d+)\s*\((\d+(?:-\d+)+)\)', lat)
            timings = m.group(2) if m else None
            if not m:
                m = re.fullmatch(r'(?:CL\s*)?(\d+)-(\d+(?:-\d+)+)', lat)
                timings = f'{m.group(1)}-{m.group(2)}' if m else None
            if m:
                out['Latencia'] = f'CL{m.group(1)}'
                out = _insert_after(out, 'Latencia', 'Timings', timings)
    elif category == 'storage':
        m = re.fullmatch(r'(Sí|No)\s*\((.+)\)', out.get('Disipador integrado') or '')
        if m:
            out['Disipador integrado'] = m.group(1)
            if m.group(1) == 'Sí': out = _insert_after(out, 'Disipador integrado', 'Disipador', _upper_first(m.group(2)))
    elif category == 'cooler':
        m = re.fullmatch(r'(Aire|Líquida AIO),\s*(.+)', out.get('Tipo') or '')
        if m:
            out['Tipo'] = m.group(1)
            out = _insert_after(out, 'Tipo', 'Diseño', _upper_first(m.group(2)))
        m = re.fullmatch(r'(\d{2,3}) mm\b[\s,;(]*(.+?)\)?', out.get('Radiador') or '')
        if m:
            out['Radiador'] = f'{m.group(1)} mm'
            out = _insert_after(out, 'Radiador', 'Detalle radiador', m.group(2))
        m = re.fullmatch(r'(\d) × (\d{2,3}) mm\b[\s,;(]*(.+?)\)?', out.get('Ventiladores') or '')
        if m:
            out['Ventiladores'] = f'{m.group(1)} × {m.group(2)} mm'
            out = _insert_after(out, 'Ventiladores', 'Detalle ventiladores', m.group(3))
    return out

def normalize_value(category, key, value):
    if not isinstance(value, str): return value
    value = ' '.join(value.split())
    handler = LIST_HANDLERS.get((category, key))
    if handler: return handler(category, key, value)
    value = _decimal(value)
    if key == 'Bus de memoria': value = re.sub(r'(\d+)[\s-]*bits?\b', r'\1 bits', value, flags=re.I)
    elif key == 'Socket': value = _sockets(value)
    elif key == 'Chipset': value = re.sub(r'^intel\b', 'Intel', re.sub(r'^amd\b', 'AMD', value, flags=re.I), flags=re.I)
    elif key == 'Formato' and category == 'motherboard': value = _formats(value)
    elif key == 'Voltaje': value = _voltage(value)
    elif category == 'case' and key in ('Longitud GPU máxima', 'Altura disipador máxima'):
        m = re.fullmatch(r'(?i)(?:up to|hasta)?\s*(\d+(?:\.\d+)?)\s*mm', value)
        if m: value = f'{m.group(1)} mm'
    elif key == 'Color':
        value = COLORS.get(value.casefold(), value)
        if value == value.lower(): value = _upper_first(value)
    elif key == 'Controlador': value = re.sub(r'^phison\b', 'Phison', value, flags=re.I)
    elif key.startswith('Frecuencia'): value = _upper_first(value)
    elif key == 'Chip gráfico' and category == 'gpu': value = normalize_gpu_chip(value)
    elif key == 'Memoria' and category == 'gpu': value = _gpu_memory(value)
    elif key == 'Arquitectura' and category == 'gpu': value = re.sub(r'^(AMD|NVIDIA|Intel)\s+', '', value)
    elif key == 'Interfaz' and category == 'gpu': value = re.sub(r'^PCI Express\b', 'PCIe', value)
    elif key == 'Interfaz' and category == 'storage': value = re.sub(r'\bPCIe Gen(\d)\b', r'PCIe \1.0', value)
    elif key == 'Tipo' and category == 'storage':
        if re.fullmatch(r'(?i)(disco\s+)?ssd|unidad de estado s[oó]lido|solid state( drive)?', value): value = 'SSD'
        elif re.fullmatch(r'(?i)disco duro|hdd|disco mec[aá]nico|hdd \(mec[aá]nico\)|mec[aá]nico', value): value = 'HDD'
    elif key == 'Velocidad de rotación' and category == 'storage':
        m = re.fullmatch(r'(?i)(\d{1,2})[.,\s]?(\d{3})\s*(?:rpm|r/min)', value)
        if m: value = f'{m.group(1)}{m.group(2)} rpm'
    elif key == 'Estándar ATX': value = _atx_standard(value)
    elif key == 'Modular' and category == 'psu': value = _psu_modular(value)
    elif key == 'Conector GPU' and category == 'psu': value = _gpu_connector(value)
    elif key == 'Gráficos integrados': value = re.sub(r'^No[;,] requiere GPU dedicada$|^No \(requiere GPU dedicada\)$|^Requiere GPU discreta$', 'No (requiere GPU dedicada)', value)
    elif key == 'Tipo' and category == 'cooler':
        if re.fullmatch(r'AIO líquida|Refrigeración líquida AIO', value): value = 'Líquida AIO'
        elif value == 'Disipador de CPU por aire': value = 'Aire'
        elif re.search(r'\bAIO\b', value) and re.search(r'liquid', value, re.I): value = 'Líquida AIO'
    elif key == 'Velocidad' and category == 'ram':
        parsed = _ram_speed(value)
        if parsed: value = parsed[1]
    return value

def normalize_specs(category, specs):
    """Return a new specs dict with canonical keys and values (order of first appearance kept)."""
    if not isinstance(specs, dict): return specs
    if category == 'ram': specs = _ram_details(specs)
    aliases = KEY_ALIASES.get(category, {})
    present = set(specs) | {aliases[k] for k in specs if k in aliases}
    out, eff, eff_rest = {}, {}, []
    for key, value in specs.items():
        if JUNK_KEYS.match(key): continue
        if category == 'psu' and key in EFF_KEYS and isinstance(value, str):
            found, rest = _efficiency(EFF_KEYS[key], ' '.join(value.split()))
            for ek, ev in found.items():
                if ek not in eff or (ek == 'Eficiencia 80 PLUS' and eff[ek] == '80 PLUS'): eff[ek] = ev
            if rest: eff_rest.append(', '.join(rest))
            out.setdefault(EFF_MARK, None)
            continue
        if isinstance(value, str) and value.strip() in JUNK_VALUES.get((category, key), ()): continue
        target = aliases.get(key)
        cond = CONDITIONAL_ALIASES.get((category, key))
        if cond and isinstance(value, str) and re.search(cond[1], value): target = cond[0]
        if key in DROP_IF_CANONICAL.get(category, {}) and DROP_IF_CANONICAL[category][key] in present: continue
        name = target or key
        new = normalize_value(category, name, value)
        if name in out:
            if out[name] == new: continue  # same fact under two names
            if target: name = key; new = normalize_value(category, name, value)  # different value: keep the alias untouched
            if name in out: continue
        elif target and target in specs and target != key:
            # canonical key is also in the source; let it win and drop an identical alias, keep a different one
            canon = normalize_value(category, target, specs[target])
            if canon == new: continue
            name = key; new = normalize_value(category, name, value)
        out[name] = new
    if category == 'psu': out = _psu_post(out, eff, eff_rest)
    out = _extras(category, out)
    if category == 'ram' and 'Latencia' not in out and re.fullmatch(r'\d+(?:-\d+)+', out.get('Timings') or ''):
        out = _insert_after(out, 'Timings', 'Latencia', f"CL{out['Timings'].split('-')[0]}")  # CAS is the first timing
        out = {k: out[k] for k in sorted(out, key=lambda k: k == 'Timings')}  # keep Latencia before Timings
    if category == 'ram' and 'Módulos' in out:  # dual/single channel is already implied by the module count
        out = {k: v for k, v in out.items() if k not in ('Canales', 'Channel configuration', 'Kit')}
    if category == 'ram' and 'Tipo de memoria' not in out:
        # Type only stated as the prefix of the speed ("DDR4-3200"): it was dropped when the speed was reduced to MT/s.
        for key, value in specs.items():
            if aliases.get(key, key) == 'Velocidad' and isinstance(value, str) and _ram_speed(value) and _ram_speed(value)[0]:
                out = {'Tipo de memoria': _ram_speed(value)[0], **out}
                break
    if category == 'case':
        # Non-format words dropped from the format list are kept under their own key (verbatim), never invented.
        for key, value in specs.items():
            if aliases.get(key, key) == 'Formatos compatibles' and isinstance(value, str) and out.get('Formatos compatibles') is not None:
                extras = _form_factor_tokens(' '.join(value.split()))[1]
                if extras and EXTRA_KEY not in out: out[EXTRA_KEY] = _upper_first(', '.join(extras))
    return out
