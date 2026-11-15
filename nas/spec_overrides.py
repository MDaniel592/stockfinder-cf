"""Correcciones manuales de especificaciones (data/overrides/*.json), aplicadas por import_catalog DESPUÉS de normalizar.

Cada fichero es una lista de registros con procedencia:
  {"product_id": "...", "category_id": "ram", "key": "Latencia", "value": "CL36",
   "source": "manual", "date": "2026-11-09", "source_url": "https://<ficha oficial consultada>", "note": "opcional"}
o, para «el fabricante no lo publica» (deja de contar como hueco; no toca las specs):
  {..., "key": "GPU integrada", "status": "not_published", ...}   (sin "value")

Claves y valores se validan contra las claves canónicas y formatos de nas/spec_normalize.py (el valor se guarda ya
normalizado). Gana el último registro de cada (producto, clave) en orden de fichero y de posición. Solo stdlib: lo usan
también la herramienta interna scripts/spec_gaps_web.py y las pruebas. Nunca inventa valores: solo aplica lo escrito.
"""
import json
import os
import re
import tempfile
from pathlib import Path

from spec_normalize import KEY_ALIASES, LIST_ORDER, normalize_value

NOT_PUBLISHED = 'not_published'
SOURCE = 'manual'

_INT = r'\d+'
_MM = r'\d+(?:\.\d+)? mm'
_W = r'(?:Hasta )?\d+ W'
_YES_NO = r'Sí|No'
_SOCKET = r'AM\d|LGA\d{4}|sTR\d|sTRX\d|sWRX\d|FM\d\+?'
_FREE = None  # texto libre (no vacío, una línea, <=120 caracteres)


def _list(category, key):
    """Lista canónica: todos los tokens deben estar en LIST_ORDER (mismo orden que los filtros de la web)."""
    return ('list', LIST_ORDER[(category, key)])


# Specs clave por categoría: las que usan la ficha (KEY_SPECS de apps/web/src/display.ts), los filtros
# (packages/api-contracts/catalog-filters.ts) y el configurador (apps/web/src/pc-builder.ts).
# key -> formato (regex que debe cumplir el valor YA normalizado, o _FREE, o ('list', tokens), o ('enum', valores)).
FORMATS = {
    'cpu': {'Socket': _SOCKET, 'Núcleos': _INT, 'Hilos': _INT, 'Frecuencia turbo': r'(?:Hasta )?\d+(?:\.\d+)? GHz',
            'TDP': _W, 'Gráficos integrados': r'Sí|No|No \(requiere GPU dedicada\)', 'GPU integrada': _FREE,
            'Disipador incluido': _YES_NO,
            'Memoria compatible': r'DDR\d(?:-\d{4,5})?(?: RDIMM| UDIMM)?(?:, DDR\d(?:-\d{4,5})?(?: RDIMM| UDIMM)?)*'},
    'gpu': {'Chip gráfico': _FREE, 'Memoria': r'\d+ GB', 'Tipo de memoria': r'GDDR\d\w?|HBM\d\w?', 'Arquitectura': _FREE,
            'Interfaz': r'PCIe \d\.\d', 'Consumo (TBP)': _W, 'Consumo (TGP)': _W, 'Longitud': _MM,
            'Fuente recomendada': r'\d+ W(?: \(.+\))?'},
    'motherboard': {'Socket': _SOCKET, 'Chipset': r'(?:AMD|Intel) [A-Z]\d{3}[A-Z]?', 'Formato': ('enum', ['E-ATX', 'ATX', 'Micro-ATX', 'Mini-ITX']),
                    'Tipo de memoria': r'DDR\d', 'Ranuras de memoria': _INT},
    'ram': {'Tipo de memoria': r'DDR\d', 'Capacidad': r'\d+ GB', 'Velocidad': r'\d{3,5} MT/s', 'Latencia': r'CL\d{1,2}',
            'Formato': ('enum', ['UDIMM', 'CUDIMM', 'SODIMM', 'CSODIMM', 'RDIMM']), 'Perfil': _list('ram', 'Perfil')},
    'storage': {'Tipo': ('enum', ['SSD', 'HDD']), 'Capacidad': r'\d+(?:\.\d+)? (?:GB|TB)', 'Interfaz': _FREE,
                'Formato': r'M\.2 22(?:30|42|60|80|110)|2\.5"|3\.5"|mSATA|U\.2.*', 'Lectura secuencial': r'\d+ MB/s',
                'Disipador integrado': _YES_NO},
    'psu': {'Potencia': r'\d+ W', 'Formato': ('enum', ['ATX', 'SFX', 'SFX-L', 'TFX', 'Flex ATX']),
            'Eficiencia 80 PLUS': ('enum', ['80 PLUS', '80 PLUS Bronze', '80 PLUS Silver', '80 PLUS Gold', '80 PLUS Platinum', '80 PLUS Titanium']),
            'Modular': ('enum', ['Cables fijos', 'Semimodular', 'Totalmente modular']), 'Estándar ATX': ('enum', ['3.1', '3.0', '2.x']),
            'Conector GPU': _FREE},
    'case': {'Formatos compatibles': _list('case', 'Formatos compatibles'), 'Longitud GPU máxima': _MM, 'Altura disipador máxima': _MM,
             'Panel lateral': _FREE, 'Formato fuente': _list('case', 'Formato fuente')},
    'cooler': {'Tipo': ('enum', ['Aire', 'Líquida AIO']), 'Sockets compatibles': _list('cooler', 'Sockets compatibles'),
               'Radiador': r'\d{2,3} mm', 'Altura': _MM, 'Ventiladores': r'\d × \d{2,3} mm'},
}
EXAMPLES = {'Latencia': 'CL36', 'Velocidad': '6000 MT/s', 'Capacidad': '32 GB', 'Socket': 'AM5 / LGA1851', 'Chipset': 'AMD B650 / Intel B760',
            'Frecuencia turbo': 'Hasta 5.4 GHz', 'TDP': '65 W', 'Longitud': '320 mm', 'Ventiladores': '2 × 120 mm', 'Radiador': '360 mm',
            'Memoria compatible': 'DDR5-5600, DDR4-3200', 'Fuente recomendada': '750 W', 'Lectura secuencial': '7000 MB/s',
            'Interfaz': 'PCIe 5.0', 'Memoria': '16 GB', 'Tipo de memoria': 'DDR5 / GDDR7'}


def _always(specs):
    return True


# Huecos: (clave, claves alternativas que también cubren el hueco, condición sobre las specs efectivas).
REQUIRED = {
    'cpu': [('Socket',), ('Núcleos',), ('Hilos',), ('Frecuencia turbo',), ('TDP',), ('Gráficos integrados',),
            ('GPU integrada', (), lambda s: s.get('Gráficos integrados') == 'Sí'), ('Disipador incluido',), ('Memoria compatible',)],
    'gpu': [('Chip gráfico',), ('Memoria',), ('Tipo de memoria',), ('Arquitectura',), ('Interfaz',),
            ('Consumo (TBP)', ('Consumo (TGP)',)), ('Longitud',), ('Fuente recomendada',)],
    'motherboard': [('Socket',), ('Chipset',), ('Formato',), ('Tipo de memoria',), ('Ranuras de memoria',)],
    'ram': [('Tipo de memoria',), ('Capacidad',), ('Velocidad',), ('Latencia',), ('Formato',), ('Perfil',)],
    'storage': [('Tipo',), ('Capacidad',), ('Interfaz',), ('Formato',),
                ('Lectura secuencial', (), lambda s: s.get('Tipo') == 'SSD'),
                ('Disipador integrado', (), lambda s: str(s.get('Formato', '')).startswith('M.2'))],
    'psu': [('Potencia',), ('Formato',), ('Eficiencia 80 PLUS',), ('Modular',), ('Estándar ATX',), ('Conector GPU',)],
    'case': [('Formatos compatibles',), ('Longitud GPU máxima',), ('Altura disipador máxima',), ('Panel lateral',), ('Formato fuente',)],
    'cooler': [('Tipo',), ('Sockets compatibles',), ('Ventiladores',),
               ('Altura', (), lambda s: s.get('Tipo') == 'Aire'), ('Radiador', (), lambda s: s.get('Tipo') == 'Líquida AIO')],
}


def _requirements(category):
    for item in REQUIRED.get(category, []):
        yield item[0], (item[1] if len(item) > 1 else ()), (item[2] if len(item) > 2 else _always)


def allowed_keys(category):
    return list(FORMATS.get(category, {}))


def format_hint(category, key):
    fmt = FORMATS.get(category, {}).get(key)
    if isinstance(fmt, tuple):
        return ' | '.join(fmt[1]) if fmt[0] == 'enum' else 'lista separada por comas de: ' + ', '.join(fmt[1])
    return EXAMPLES.get(key, 'texto libre' if fmt is None else fmt)


def check_value(category, key, value):
    """(valor normalizado, None) o (None, motivo). Clave canónica obligatoria; el valor debe quedar en el formato canónico."""
    formats = FORMATS.get(category)
    if formats is None: return None, f'categoría desconocida: {category}'
    if key in KEY_ALIASES.get(category, {}): return None, f'«{key}» no es canónica; usa «{KEY_ALIASES[category][key]}»'
    if key not in formats: return None, f'clave no admitida en {category}: «{key}» (admitidas: {", ".join(formats)})'
    if not isinstance(value, str) or not value.strip(): return None, 'valor vacío'
    if '\n' in value or len(value) > 120: return None, 'el valor debe ser una línea de 120 caracteres como máximo'
    norm = normalize_value(category, key, value)
    fmt = formats[key]
    if isinstance(fmt, tuple):
        if fmt[0] == 'enum': ok = norm in fmt[1]
        else: ok = all(token in fmt[1] for token in norm.split(', '))
    else:
        ok = fmt is None or re.fullmatch(fmt, norm) is not None
    if not ok: return None, f'«{value}» no tiene el formato canónico de {key} ({format_hint(category, key)})'
    return norm, None


def validate_record(record):
    """Lista de errores de un registro (vacía si es válido)."""
    if not isinstance(record, dict): return ['el registro no es un objeto']
    errors = []
    for field in ('product_id', 'category_id', 'key', 'date', 'source_url'):
        if not isinstance(record.get(field), str) or not record[field].strip(): errors.append(f'falta {field}')
    if record.get('source') != SOURCE: errors.append('source debe ser "manual"')
    if isinstance(record.get('date'), str) and not re.fullmatch(r'\d{4}-\d{2}-\d{2}', record['date']): errors.append('date debe ser AAAA-MM-DD')
    if isinstance(record.get('source_url'), str) and not record['source_url'].startswith('https://'): errors.append('source_url debe ser https://')
    if 'note' in record and not isinstance(record['note'], str): errors.append('note debe ser texto')
    extra = set(record) - {'product_id', 'category_id', 'key', 'value', 'status', 'source', 'date', 'source_url', 'note'}
    if extra: errors.append(f'campos desconocidos: {", ".join(sorted(extra))}')
    if errors: return errors
    category, key = record['category_id'], record['key']
    if record.get('status') == NOT_PUBLISHED:
        if 'value' in record: return ['un registro not_published no lleva value']
        if key in KEY_ALIASES.get(category, {}) or key not in FORMATS.get(category, {}): return [f'clave no admitida en {category}: «{key}»']
        return []
    if 'status' in record: return [f'status desconocido: {record["status"]}']
    norm, error = check_value(category, key, record.get('value'))
    if error: return [error]
    if norm != record['value']: return [f'valor no normalizado: «{record["value"]}» se guarda como «{norm}»']
    return []


def load_overrides(root):
    """(registros válidos en orden, {fichero: [errores]}). Un fichero con algún registro inválido se omite entero."""
    records, errors = [], {}
    for path in sorted((Path(root) / 'data/overrides').glob('*.json')):
        try:
            items = json.loads(path.read_text())
            if not isinstance(items, list): raise ValueError('se esperaba una lista')
        except (OSError, ValueError) as error:
            errors[path.name] = [f'ilegible: {error}']
            continue
        bad = [f'#{i} {e}' for i, item in enumerate(items) for e in validate_record(item)]
        if bad: errors[path.name] = bad
        else: records.extend(items)
    return records, errors


def index(records):
    """{product_id: {key: registro}}; gana el último."""
    out = {}
    for record in records: out.setdefault(record['product_id'], {})[record['key']] = record
    return out


def apply(product, by_product):
    """Specs con las correcciones manuales aplicadas (valores ganan; not_published no toca nada). Ignora registros de otra categoría."""
    specs = product.get('specs')
    entries = by_product.get(product.get('id'), {})
    if not entries or not isinstance(specs, dict): return specs
    specs = dict(specs)
    for key, record in entries.items():
        if record.get('category_id') == product.get('category_id') and record.get('status') != NOT_PUBLISHED:
            specs[key] = record['value']
    return specs


def gaps(product, by_product=None):
    """Claves clave ausentes en las specs efectivas, salvo las marcadas como «el fabricante no lo publica»."""
    category = product.get('category_id')
    specs = product.get('specs') or {}
    marked = {k for k, r in (by_product or {}).get(product.get('id'), {}).items() if r.get('status') == NOT_PUBLISHED}
    missing = []
    for key, alternates, condition in _requirements(category):
        if any(isinstance(specs.get(k), str) and specs[k].strip() for k in (key, *alternates)): continue
        if not condition(specs) or key in marked: continue
        missing.append({'key': key, 'alternates': list(alternates)})
    return missing


def save_record(root, record):
    """Valida y guarda en data/overrides/manual-<categoría>.json sustituyendo el registro previo de (producto, clave)."""
    problems = validate_record(record)
    if problems: raise ValueError('; '.join(problems))
    path = Path(root) / 'data/overrides' / f"manual-{record['category_id']}.json"
    items = json.loads(path.read_text()) if path.is_file() else []
    items = [r for r in items if (r.get('product_id'), r.get('key')) != (record['product_id'], record['key'])] + [record]
    _atomic_write(path, items)
    return path


def delete_record(root, category, product_id, key):
    path = Path(root) / 'data/overrides' / f'manual-{category}.json'
    if not path.is_file(): return False
    items = json.loads(path.read_text())
    kept = [r for r in items if (r.get('product_id'), r.get('key')) != (product_id, key)]
    if len(kept) == len(items): return False
    _atomic_write(path, kept)
    return True


def _atomic_write(path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-', suffix='.json')
    with os.fdopen(fd, 'w') as handle:
        handle.write(json.dumps(items, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
