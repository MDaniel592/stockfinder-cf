"""Validación tolerante de lotes de catálogo y alias (solo biblioteca estándar).

Reutiliza scripts/validate_batch.py (el CLI que usan los agentes de verificación) sin modificarlo:
lo importa como módulo, le apunta a la raíz de datos pedida y envuelve cada fichero para que uno
ilegible, a medio escribir o inválido se registre y se omita sin bloquear al resto.
Lo usan nas/publisher.py (contenedor) y scripts/catalog_flow.py (host).
"""
import hashlib
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import validate_batch as vb  # noqa: E402

LEGACY_IDENTIFIERS = 'product-identifiers.json'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _info(path, kind):
    info = {'name': path.name, 'path': path, 'kind': kind, 'hash': None, 'items': None,
            'errors': [], 'warnings': 0, 'valid': False}
    try:
        raw = path.read_bytes()
        info['hash'] = sha256(raw)
        items = json.loads(raw)
    except (OSError, ValueError) as exc:  # fichero a medio escribir o ilegible: se reintenta en el siguiente ciclo
        info['errors'].append(f'{path.name}: ilegible o JSON incompleto ({type(exc).__name__})')
        return info
    if not isinstance(items, list):
        info['errors'].append(f'{path.name}: el nivel superior debe ser un array JSON')
        return info
    info['items'] = items
    return info


def _changed(info):
    try:
        return sha256(info['path'].read_bytes()) != info['hash']
    except OSError:
        return True


def _keys(product):
    out = {'id': product['id'], 'slug': product.get('slug', product['id']),
           'name': f"{vb.alnum(product['brand'])}:{product['name'].lower()}"}
    if product.get('mpn'):
        out['mpn'] = f"{vb.alnum(product['brand'])}:{str(product['mpn']).lower()}"
    return out


def _seen_without(infos, exclude):
    """Equivalente a validate_batch.load_existing excluyendo un fichero, sobre contenido ya leído."""
    seen = {'id': {}, 'slug': {}, 'name': {}, 'mpn': {}}
    for other in infos:
        if other['name'] == exclude or other['items'] is None:
            continue
        for p in other['items']:
            try:
                for kind, value in _keys(p).items():
                    seen[kind].setdefault(value, other['name'])
            except Exception:
                continue
    return seen


def scan(data_root):
    """Lee y valida data/catalog/*.json y data/aliases/*.json (+ product-identifiers.json, sin validar).
    Devuelve {'catalog': {nombre: info}, 'aliases': {...}, 'legacy': info|None, 'images_hash': str}.
    info['valid'] indica que el fichero se puede importar en este ciclo."""
    data_root = Path(data_root)
    saved = (vb.ROOT, vb.CATALOG)
    vb.ROOT, vb.CATALOG = data_root, data_root / 'data/catalog'
    try:
        brand_file = data_root / 'data/brand-domains.json'
        try:
            extra = {k: v for k, v in json.loads(brand_file.read_text()).items() if not k.startswith('_')}
        except (OSError, ValueError):
            extra = {}
        catalog = [_info(p, 'catalog') for p in sorted(vb.CATALOG.glob('*.json'))]
        for info in catalog:
            if info['items'] is None:
                continue
            try:
                errors, warnings = vb.validate(info['path'], extra, _seen_without(catalog, info['name']))
            except Exception as exc:
                errors, warnings = [f"{info['name']}: validador falló ({type(exc).__name__})"], []
            if _changed(info):
                errors = errors + [f"{info['name']}: el fichero cambió durante la validación"]
            info['errors'], info['warnings'] = errors, len(warnings)
            info['valid'] = not errors
        aliases = [_info(p, 'aliases') for p in sorted((data_root / 'data/aliases').glob('*.json'))]
        for info in aliases:
            if info['items'] is None:
                continue
            try:
                errors, warnings = vb.validate_aliases(info['path'])
            except Exception as exc:  # p. ej. un lote de catálogo a medio escribir
                errors, warnings = [f"{info['name']}: no se pudo validar ({type(exc).__name__})"], []
            if _changed(info):
                errors = errors + [f"{info['name']}: el fichero cambió durante la validación"]
            info['errors'], info['warnings'] = errors, len(warnings)
            info['valid'] = not errors
        legacy = None
        legacy_path = data_root / 'data' / LEGACY_IDENTIFIERS
        if legacy_path.is_file():
            legacy = _info(legacy_path, 'legacy')
            legacy['valid'] = legacy['items'] is not None
        images = hashlib.sha256()
        for p in sorted((data_root / 'data/images').glob('*.json')):
            try:
                images.update(p.name.encode() + p.read_bytes())
            except OSError:
                pass
        return {'catalog': {i['name']: i for i in catalog}, 'aliases': {i['name']: i for i in aliases},
                'legacy': legacy, 'images_hash': images.hexdigest()}
    finally:
        vb.ROOT, vb.CATALOG = saved
