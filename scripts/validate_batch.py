"""Validate one or more new catalog batch files before they are merged.

Usage: python3 scripts/validate_batch.py data/catalog/reviewed-batch-2026-11-08-<shard>.json [...]

Checks the public product contract (packages/api-contracts), uniqueness against every other
file in data/catalog, and that manufacturer_url belongs to the brand (same rule as
tests/catalog.test.ts). Exit code 1 on any error; warnings never fail. Read-only.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data/catalog'
CATEGORIES = {'cpu', 'gpu', 'motherboard', 'ram', 'storage', 'psu', 'case', 'cooler'}
ALLOWED_KEYS = {'id', 'slug', 'name', 'category_id', 'brand', 'specs', 'manufacturer_url', 'specs_verified_at',
                'mpn', 'mpn_source', 'image_path', 'image_source_url', 'image_scope',
                # Review evidence kept in local batches; nas/import_catalog.py drops it before the API.
                'manufacturer_sources', 'manufacturer_evidence', 'verification_review_id'}
ID_RE = re.compile(r'^[a-zA-Z0-9_-]{1,100}$')
TWO_LEVEL = ('com.tw', 'com.tr', 'co.uk', 'com.cn', 'co.jp', 'com.br', 'com.au')
# Spec keys the public filters read (packages/api-contracts/catalog-filters.ts): warn when absent.
FILTER_KEYS = {
    'cpu': ['Socket', 'Núcleos', 'Memoria compatible'], 'gpu': ['Memoria', 'Tipo de memoria', 'Interfaz'],
    'motherboard': ['Socket', 'Chipset', 'Tipo de memoria', 'Formato'],
    'ram': ['Tipo de memoria', 'Capacidad', 'Velocidad', 'Latencia'],
    'storage': ['Capacidad', 'Interfaz', 'Formato'], 'psu': ['Potencia', 'Formato', 'Eficiencia 80 PLUS', 'Modular', 'Estándar ATX'],
    'case': ['Formatos compatibles', 'Panel lateral', 'Formato fuente'], 'cooler': ['Tipo', 'Sockets compatibles'],
}


def alnum(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def registrable(host):
    labels = host.lower().split('.')
    if len(labels) >= 3 and '.'.join(labels[-2:]) in TWO_LEVEL:
        return '.'.join(labels[-3:]), labels[-3]
    return '.'.join(labels[-2:]), labels[-2] if len(labels) >= 2 else labels[0]


def host_matches_brand(brand, host, extra):
    key = alnum(brand)
    domain, label = registrable(host)
    if any(domain == d or domain.endswith('.' + d) for d in extra.get(key, [])):
        return True
    label = alnum(label)
    return len(label) >= 3 and len(key) >= 2 and (key in label or label in key)


def load_existing(skip):
    seen = {'id': {}, 'slug': {}, 'name': {}, 'mpn': {}}
    for path in sorted(CATALOG.glob('*.json')):
        if path.resolve() in skip:
            continue
        for p in json.loads(path.read_text()):
            seen['id'].setdefault(p['id'], path.name)
            seen['slug'].setdefault(p.get('slug', p['id']), path.name)
            seen['name'].setdefault(f"{alnum(p['brand'])}:{p['name'].lower()}", path.name)
            if p.get('mpn'):
                seen['mpn'].setdefault(f"{alnum(p['brand'])}:{str(p['mpn']).lower()}", path.name)
    return seen


def validate(path, extra, seen):
    errors, warnings = [], []
    try:
        products = json.loads(path.read_text())
    except Exception as exc:
        return [f'{path.name}: invalid JSON ({exc})'], []
    if not isinstance(products, list):
        return [f'{path.name}: top level must be a JSON array'], []
    local = {'id': set(), 'slug': set(), 'name': set(), 'mpn': set()}
    for index, p in enumerate(products):
        where = f"{path.name}[{index}] {p.get('id', '?')}"
        if not isinstance(p, dict):
            errors.append(f'{where}: not an object'); continue
        for unknown in set(p) - ALLOWED_KEYS:
            errors.append(f'{where}: unknown key {unknown!r}')
        for required in ('id', 'slug', 'name', 'category_id', 'brand', 'specs', 'manufacturer_url', 'specs_verified_at'):
            if not p.get(required):
                errors.append(f'{where}: missing {required}')
        if errors and errors[-1].startswith(where) and 'missing' in errors[-1]:
            continue
        for key in ('id', 'slug'):
            if not ID_RE.match(str(p.get(key, ''))):
                errors.append(f'{where}: {key} must match [a-zA-Z0-9_-]{{1,100}}')
        if p['id'] != p['slug']:
            warnings.append(f'{where}: slug differs from id (existing batches use identical values)')
        if p['category_id'] not in CATEGORIES:
            errors.append(f"{where}: category_id {p['category_id']!r} not in {sorted(CATEGORIES)}")
        for key in ('name', 'brand', 'mpn'):
            if key in p and not (isinstance(p[key], str) and 1 <= len(p[key]) <= 250):
                errors.append(f'{where}: {key} must be a 1-250 char string')
        if p.get('mpn') and p.get('mpn_source') not in (None, 'manufacturer', 'retailer'):
            errors.append(f"{where}: mpn_source must be manufacturer|retailer")
        if p.get('mpn') and p.get('mpn_source') is None:
            warnings.append(f'{where}: mpn without mpn_source (use manufacturer or retailer)')
        specs = p['specs']
        if not isinstance(specs, dict) or not specs or len(specs) > 24 or not all(
                isinstance(k, str) and isinstance(v, str) and 0 < len(k) <= 80 and 0 < len(v) <= 250 for k, v in specs.items()):
            errors.append(f'{where}: specs must be 1-24 string pairs (key<=80, value<=250 chars)')
        else:
            missing = [k for k in FILTER_KEYS[p['category_id']] if k not in specs] if p['category_id'] in FILTER_KEYS else []
            if missing:
                warnings.append(f"{where}: missing filter specs {missing} (add only if the official page states them)")
        url = urlparse(str(p['manufacturer_url']))
        if url.scheme != 'https' or not url.hostname:
            errors.append(f'{where}: manufacturer_url must be https')
        elif not host_matches_brand(p['brand'], url.hostname, extra):
            errors.append(f"{where}: manufacturer_url host {url.hostname!r} does not belong to brand {p['brand']!r}")
        try:
            datetime.fromisoformat(p['specs_verified_at'].replace('Z', '+00:00'))
            if not re.search(r'(Z|[+-]\d\d:\d\d)$', p['specs_verified_at']):
                raise ValueError('timezone required')
        except Exception:
            errors.append(f"{where}: specs_verified_at must be ISO-8601 with Z or offset")
        keys = {'id': p['id'], 'slug': p['slug'], 'name': f"{alnum(p['brand'])}:{p['name'].lower()}"}
        if p.get('mpn'):
            keys['mpn'] = f"{alnum(p['brand'])}:{str(p['mpn']).lower()}"
        for kind, value in keys.items():
            if value in local[kind]:
                errors.append(f'{where}: duplicate {kind} {value!r} inside this file')
            local[kind].add(value)
            if value in seen[kind]:
                errors.append(f'{where}: {kind} {value!r} already exists in {seen[kind][value]}')
    return errors, warnings


SHOP_HOSTS = {'www.coolmod.com', 'www.pccomponentes.com'}
ALIAS_KEYS = {'product_id', 'kind', 'value', 'source_url', 'note', 'scope'}


def norm(value):
    return ' '.join(str(value).casefold().split())


def validate_aliases(path):
    """Alias files (data/aliases/*.json): store codes linked to verified products with provenance."""
    errors, warnings = [], []
    products = {p['id']: p for f in sorted(CATALOG.glob('*.json')) for p in json.loads(f.read_text())}
    taken = {}  # (kind, brand key, value) -> product_id, from product MPNs and every other alias file
    for p in products.values():
        if p.get('mpn'):
            taken[('mpn', alnum(p['brand']), norm(p['mpn']))] = p['id']
    others = [ROOT / 'data/product-identifiers.json', *sorted((ROOT / 'data/aliases').glob('*.json'))]
    for other in others:
        if other.resolve() == path.resolve() or not other.is_file():
            continue
        for a in json.loads(other.read_text()):
            product = products.get(a.get('product_id'))
            if product:
                taken.setdefault((a['kind'], alnum(a.get('scope') or product['brand']), norm(a['value'])), a['product_id'])
    try:
        aliases = json.loads(path.read_text())
    except Exception as exc:
        return [f'{path.name}: invalid JSON ({exc})'], []
    if not isinstance(aliases, list):
        return [f'{path.name}: top level must be a JSON array'], []
    local = set()
    for index, a in enumerate(aliases):
        where = f"{path.name}[{index}] {a.get('product_id', '?')}"
        if not isinstance(a, dict):
            errors.append(f'{where}: not an object'); continue
        for unknown in set(a) - ALIAS_KEYS:
            errors.append(f'{where}: unknown key {unknown!r}')
        product = products.get(a.get('product_id'))
        if product is None:
            errors.append(f"{where}: product_id not found in data/catalog (write the product batch first)"); continue
        if a.get('kind') not in ('mpn', 'ean'):
            errors.append(f'{where}: kind must be mpn or ean')
        value = a.get('value')
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
            errors.append(f'{where}: value must be a 1-100 char string'); continue
        url = urlparse(str(a.get('source_url', '')))
        if url.scheme != 'https' or url.hostname not in SHOP_HOSTS:
            errors.append(f"{where}: source_url must be the https product page on {sorted(SHOP_HOSTS)}")
        if len(str(a.get('note', '')).strip()) < 40:
            errors.append(f'{where}: note must explain the evidence (official page URL, matched attributes) in >=40 chars')
        key = (a.get('kind'), alnum(a.get('scope') or product['brand']), norm(value))
        if key in local:
            errors.append(f'{where}: duplicate alias {value!r} inside this file')
        local.add(key)
        owner = taken.get(key)
        if owner and owner != a['product_id']:
            errors.append(f'{where}: code {value!r} already identifies {owner}')
        elif owner == a['product_id']:
            warnings.append(f'{where}: code {value!r} already registered for this product')
        if product.get('mpn') and norm(product['mpn']) == norm(value):
            warnings.append(f'{where}: alias equals the product mpn (redundant)')
    return errors, warnings


def main():
    files = [Path(a).resolve() for a in sys.argv[1:]]
    if not files:
        print(__doc__); return 2
    extra = {k: v for k, v in json.loads((ROOT / 'data/brand-domains.json').read_text()).items() if not k.startswith('_')}
    seen = load_existing(set(files))
    failed = False
    for path in files:
        if path.parent.name == 'aliases':
            errors, warnings = validate_aliases(path)
            for line in warnings[:40]:
                print('WARN ', line)
            for line in errors:
                print('ERROR', line)
            total = len(json.loads(path.read_text())) if path.exists() else 0
            print(f"{path.name}: {total} aliases, {len(errors)} errors, {len(warnings)} warnings")
            failed |= bool(errors)
            continue
        errors, warnings = validate(path, extra, seen)
        for line in warnings[:40]:
            print('WARN ', line)
        for line in errors:
            print('ERROR', line)
        total = len(json.loads(path.read_text())) if not errors or path.exists() else 0
        print(f"{path.name}: {total} products, {len(errors)} errors, {len(warnings)} warnings")
        failed |= bool(errors)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
