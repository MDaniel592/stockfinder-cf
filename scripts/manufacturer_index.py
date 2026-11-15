"""Build one reference index of manufacturer identifiers from every source already in the project.

The index answers a single question: "for this MPN, EAN or model name, which official source
publishes it, and where?".  It never decides identity on its own: it only says which document
publishes which code, with the URL, the file and the hash of the evidence.

Sources read (all of them optional, all of them local):

* ``data/catalog/*.json``            - canonical products (``mpn_source: manufacturer`` or ``retailer``).
* ``data/product-identifiers.json``  - retailer aliases observed in shops.
* ``data/aliases/*.json``            - the same, in batches.
* ``artifacts/manufacturer-*.json``  - output of the official manufacturer scrapers.
* ``artifacts/msi-official-store/``  - MSI shop catalogue (Shopify JSON, ``variants[].sku``).
* ``artifacts/msi-regional-stores/`` - the same catalogue for the PL/FR/DE shops.
* ``artifacts/official-browser-evidence/`` - official HTML/PDF already captured, with their sha256.
* any CSV given with ``--csv``        - manufacturer listings (the Seasonic RMA template, or a
  generic table with a header row).

Output: ``artifacts/manufacturer-index/<date>.csv`` and ``.json`` (plus a ``latest`` copy).  Only
the standard library is used, so it runs on the host without the NAS image.

Usage:
  python3 scripts/manufacturer_index.py
  python3 scripts/manufacturer_index.py --csv artifacts/ldlc/audit-20261115/seasonic-rma-example-import.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]

FIELDS = ['brand', 'category', 'mpn', 'ean', 'model', 'series', 'mpn_source', 'source',
          'source_url', 'local_path', 'sha256', 'transport', 'checked_at', 'notes']

# Provenance priority: an official document beats a shop, a shop beats a local alias.
SOURCE_RANK = {
    'catalog': 3,
    'catalog-alias': 6,
    'store': 4,
    'evidence': 5,
    'csv': 2,
    'scraper': 0,
}
MPN_SOURCE_RANK = {'manufacturer': 0, 'store': 1, 'retailer': 2}

BRAND_HOST_HINTS = {
    'asus.com': 'ASUS', 'rog.asus.com': 'ASUS', 'estore.asus.com': 'ASUS',
    'asrock.com': 'ASRock', 'msi.com': 'MSI', 'seasonic.com': 'Seasonic',
    'gigabyte.com': 'Gigabyte', 'aorus.com': 'Gigabyte', 'crucial.com': 'Crucial',
    'micron.com': 'Micron', 'kingston.com': 'Kingston', 'gskill.com': 'G.SKILL',
    'corsair.com': 'Corsair', 'thermaltake.com': 'Thermaltake', 'lian-li.com': 'Lian Li',
    'lianli.com': 'Lian Li', 'phanteks.com': 'Phanteks', 'deepcool.com': 'DeepCool',
    'antec.com': 'Antec', 'bequiet.com': 'be quiet!', 'noctua.at': 'Noctua',
    'silverstonetek.com': 'SilverStone', 'fractal-design.com': 'Fractal Design',
    'coolermaster.com': 'Cooler Master', 'adata.com': 'ADATA', 'xpg.com': 'XPG',
    'lexar.com': 'Lexar', 'kioxia.com': 'Kioxia', 'sandisk.com': 'SanDisk',
    'westerndigital.com': 'Western Digital', 'wd.com': 'Western Digital',
    'seagate.com': 'Seagate', 'samsung.com': 'Samsung', 'intel.com': 'Intel',
    'amd.com': 'AMD', 'nvidia.com': 'NVIDIA', 'coolbox.es': 'CoolBox', 'textorm.com': 'Textorm',
    'fox-spirit.com': 'Fox Spirit', 'patriotmemory.com': 'Patriot', 'havn.com': 'HAVN',
    'hiditec.com': 'Hiditec', 'marsgaming.eu': 'Mars Gaming', 'nox.com': 'NOX',
    'tacens.com': 'Tacens', 'cougargaming.com': 'Cougar', 'nzxt.com': 'NZXT',
}


def norm_brand(value) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def norm_mpn(value) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def norm_model(value) -> str:
    return ' '.join(re.findall(r'[a-z0-9]+', str(value or '').casefold()))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def brand_from_url(url: str, brand_domains: dict) -> str:
    host = (urlparse(url).hostname or '').lower()
    if not host:
        return ''
    if host in BRAND_HOST_HINTS:
        return BRAND_HOST_HINTS[host]
    for brand, domains in brand_domains.items():
        if brand.startswith('_'):
            continue
        if any(host == domain or host.endswith('.' + domain) for domain in domains):
            return brand
    label = host.split('.')[-2] if host.count('.') >= 1 else host
    return label


def row(brand='', category='', mpn='', ean='', model='', series='', mpn_source='', source='',
        source_url='', local_path='', sha256='', transport='', checked_at='', notes='') -> dict:
    return {
        'brand': (brand or '').strip(), 'category': (category or '').strip(),
        'mpn': (mpn or '').strip(), 'ean': (ean or '').strip(), 'model': (model or '').strip(),
        'series': (series or '').strip(), 'mpn_source': (mpn_source or '').strip(),
        'source': (source or '').strip(), 'source_url': (source_url or '').strip(),
        'local_path': (local_path or '').strip(), 'sha256': (sha256 or '').strip(),
        'transport': (transport or '').strip(), 'checked_at': (checked_at or '').strip(),
        'notes': (notes or '').strip(),
    }


def merge_rows(rows: list[dict]) -> dict:
    """Keep the most authoritative row and complete the empty fields from the others."""
    def rank(item: dict):
        source = item['source'].split(':', 1)[0]
        return (MPN_SOURCE_RANK.get(item['mpn_source'], 3), SOURCE_RANK.get(source, 7),
                0 if item['source_url'] else 1, 0 if item['sha256'] else 1)

    ordered = sorted(rows, key=rank)
    best = dict(ordered[0])
    for other in ordered[1:]:
        for field in FIELDS:
            if not best.get(field) and other.get(field):
                best[field] = other[field]
        notes = [n for n in (best.get('notes'), other.get('notes')) if n]
        seen, unique = set(), []
        for note in notes:
            for part in note.split(' | '):
                if part and part not in seen:
                    seen.add(part)
                    unique.append(part)
        best['notes'] = ' | '.join(unique)
    return best


class Index:
    def __init__(self) -> None:
        self.rows: dict[tuple, list[dict]] = {}
        self.evidence: dict[str, dict] = {}
        self.sources: dict[str, int] = {}
        self.brand_domains: dict = {}

    def add(self, item: dict) -> None:
        if not item.get('mpn') and not item.get('ean') and not item.get('source_url'):
            return
        key = (norm_brand(item['brand']), norm_mpn(item['mpn']))
        self.rows.setdefault(key, []).append(item)
        label = item['source'].split(':', 1)[0]
        self.sources[label] = self.sources.get(label, 0) + 1

    def load_evidence(self, directory: Path) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob('*.json')):
            try:
                payload = json.loads(path.read_text(errors='replace'))
            except (OSError, ValueError):
                continue
            if isinstance(payload, list):
                payload = payload[0] if payload and isinstance(payload[0], dict) else {}
            if not isinstance(payload, dict):
                continue
            url = payload.get('source_url') or payload.get('url_final') or ''
            if not url:
                continue
            self.evidence[url] = {
                'sha256': payload.get('sha256') or '',
                'checked_at': payload.get('checked_at') or '',
                'title': payload.get('title') or '',
                'status': payload.get('status'),
                'challenge': bool(payload.get('challenge')),
                'path': str(path),
            }

    def attach_evidence(self, item: dict) -> dict:
        """Complete a row with the hash and date of the official capture of its URL."""
        found = self.evidence.get(item.get('source_url') or '')
        if not found:
            return item
        if not item.get('sha256'):
            item['sha256'] = found['sha256']
        if not item.get('checked_at'):
            item['checked_at'] = found['checked_at']
        if found['challenge']:
            item['notes'] = ' | '.join(filter(None, [
                item.get('notes'),
                'La captura oficial de esta URL devolvió un reto de WAF (challenge=True).']))
        return item

    def finalise(self) -> list[dict]:
        out = []
        for key, items in self.rows.items():
            merged = merge_rows([self.attach_evidence(i) for i in items])
            merged['key'] = f"{key[0]}:{key[1]}"
            out.append(merged)
        out.sort(key=lambda i: (norm_brand(i['brand']), norm_mpn(i['mpn']), i['source']))
        return out


# --------------------------------------------------------------------------- sources
def read_catalog(index: Index, catalog_dir: Path, include_retailer: bool) -> None:
    for path in sorted(catalog_dir.glob('*.json')):
        try:
            products = json.loads(path.read_text(errors='replace'))
        except (OSError, ValueError):
            continue
        if not isinstance(products, list):
            continue
        for product in products:
            if not isinstance(product, dict):
                continue
            mpn_source = product.get('mpn_source') or ''
            if mpn_source != 'manufacturer' and not include_retailer:
                continue
            index.add(row(
                brand=product.get('brand') or '', category=product.get('category_id') or '',
                mpn=product.get('mpn') or '', model=product.get('name') or '',
                mpn_source=mpn_source or 'retailer', source='catalog',
                source_url=product.get('manufacturer_url') or '',
                local_path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                checked_at=product.get('specs_verified_at') or '',
                notes='Producto canónico del catálogo.'))


def read_aliases(index: Index, paths: list[Path]) -> None:
    for path in paths:
        try:
            entries = json.loads(path.read_text(errors='replace'))
        except (OSError, ValueError):
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = entry.get('kind') or 'mpn'
            value = entry.get('value') or ''
            product_id = entry.get('product_id') or ''
            brand = re.sub(r'[-_].*$', '', product_id).replace('-', ' ').title()
            index.add(row(
                brand=brand, mpn=value if kind == 'mpn' else '', ean=value if kind == 'ean' else '',
                model=product_id, mpn_source='retailer', source='catalog-alias',
                source_url=entry.get('source_url') or '',
                local_path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                notes=(entry.get('note') or '').strip()))


def read_scrapers(index: Index, pattern: str) -> None:
    for path in sorted(ROOT.glob(pattern)):
        try:
            payload = json.loads(path.read_text(errors='replace'))
        except (OSError, ValueError):
            continue
        records = payload.get('records') if isinstance(payload, dict) else payload
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            index.add(row(
                brand=record.get('brand') or '', category=record.get('category') or '',
                mpn=record.get('mpn') or '', ean=record.get('ean') or '',
                model=record.get('model') or '', mpn_source='manufacturer',
                source='scraper:' + path.stem, source_url=record.get('url') or '',
                local_path=str(path.relative_to(ROOT)),
                transport=record.get('transport') or '',
                checked_at=record.get('fetched_at') or '',
                notes=' | '.join(record.get('notes') or [])))


def read_msi_store(index: Index, path: Path, label: str) -> None:
    try:
        payload = json.loads(path.read_text(errors='replace'))
    except (OSError, ValueError):
        return
    if not isinstance(payload, list):
        return
    for product in payload:
        if not isinstance(product, dict):
            continue
        title = product.get('title') or ''
        handle = product.get('handle') or ''
        variants = product.get('variants') or []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            sku = variant.get('sku')
            if not sku:
                continue
            index.add(row(
                brand='MSI', category='', mpn=sku, model=title, mpn_source='manufacturer',
                source='store:' + label,
                source_url=f'https://{label}/products/{handle}' if handle else '',
                local_path=str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path),
                checked_at=(variant.get('updated_at') or '')[:10],
                notes=('Catálogo público de la tienda oficial MSI (Shopify products.json); '
                       'variants[].sku.') + (f' available={variant.get("available")}' if
                                              variant.get('available') is not None else '')))


def read_evidence(index: Index, directory: Path) -> None:
    for url, found in index.evidence.items():
        index.add(row(
            brand=brand_from_url(url, index.brand_domains), mpn='', model=found['title'],
            source='evidence', source_url=url, local_path=found['path'], sha256=found['sha256'],
            checked_at=found['checked_at'],
            notes='Captura oficial ya guardada (HTML o PDF). No se extrajo ningún MPN de ella.'))


def detect_delimiter(sample: str) -> str:
    head = '\n'.join(sample.splitlines()[:5])
    return ';' if head.count(';') > head.count(',') else ','


def parse_seasonic_rma(text: str, brand: str, path: Path, digest: str) -> list[dict]:
    """The Seasonic RMA template: series / model name / model number per line."""
    rows = list(csv.reader(io.StringIO(text), delimiter=';'))
    out: list[dict] = []
    first_data = None
    for number, values in enumerate(rows):
        padded = values + [''] * 17
        if not padded[0].strip() and not padded[1].strip() and padded[5].strip():
            first_data = number
            break
    for number, values in enumerate(rows):
        padded = values + [''] * 17
        series, model, mpn = padded[5].strip(), padded[6].strip(), padded[7].strip()
        if not series or not model or not mpn:
            continue
        if mpn.casefold().startswith('model number'):
            continue
        notes = 'Listado oficial Seasonic (plantilla RMA).'
        if first_data is not None and number < first_data:
            notes += ' Fila dentro del bloque de compañía/ejemplo de la plantilla: verificar.'
        out.append(row(
            brand=brand, mpn=mpn, model=model, series=series, mpn_source='manufacturer',
            source='csv:' + path.name, local_path=str(path), sha256=digest,
            checked_at='', notes=notes))
    return out


GENERIC_COLUMNS = {
    'brand': ('brand', 'marca', 'manufacturer', 'fabricante'),
    'category': ('category', 'categoria', 'categoría', 'type'),
    'mpn': ('mpn', 'part number', 'partnumber', 'model number', 'modelnumber', 'code', 'codigo',
            'código', 'sku', 'p/n'),
    'ean': ('ean', 'ean code', 'upc', 'barcode', 'gtin'),
    'model': ('model', 'model name', 'nombre', 'name', 'product', 'producto'),
    'series': ('series', 'serie', 'family', 'familia', 'line', 'linea'),
    'url': ('url', 'source url', 'source_url', 'link', 'enlace', 'page'),
    'notes': ('notes', 'note', 'nota', 'comment'),
}


def parse_generic_csv(text: str, brand: str, path: Path, digest: str) -> list[dict]:
    """A CSV with a header row; the column names decide what each column is."""
    sample = text[:4096]
    delimiter = detect_delimiter(sample)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if not rows:
        return []
    header = [cell.strip().casefold() for cell in rows[0]]
    mapping: dict[str, int] = {}
    for field, aliases in GENERIC_COLUMNS.items():
        for index, cell in enumerate(header):
            if cell in aliases:
                mapping[field] = index
                break
    if 'mpn' not in mapping:
        return []
    out = []
    for values in rows[1:]:
        if not any(cell.strip() for cell in values):
            continue

        def get(field: str) -> str:
            index = mapping.get(field)
            if index is None or index >= len(values):
                return ''
            return values[index].strip()

        out.append(row(
            brand=get('brand') or brand, category=get('category'), mpn=get('mpn'), ean=get('ean'),
            model=get('model'), series=get('series'), mpn_source='manufacturer',
            source='csv:' + path.name, source_url=get('url'), local_path=str(path),
            sha256=digest, notes=get('notes')))
    return out


def read_csv(index: Index, path: Path, brand: str) -> None:
    text = path.read_text(encoding='utf-8-sig', errors='replace')
    digest = sha256_file(path)
    if 'RECOGNIZED RETAIL MODELS' in text:
        rows = parse_seasonic_rma(text, brand or 'Seasonic', path, digest)
    else:
        rows = parse_generic_csv(text, brand, path, digest)
    for item in rows:
        index.add(item)


# --------------------------------------------------------------------------- output
def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        for item in rows:
            writer.writerow({field: item.get(field, '') for field in FIELDS})
    os.replace(temporary, path)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--catalog-dir', type=Path, default=ROOT / 'data/catalog')
    parser.add_argument('--aliases', type=Path, action='append', default=[],
                        help='Alias files (default: data/product-identifiers.json + data/aliases/*.json)')
    parser.add_argument('--scraper-glob', default='work/manufacturer/manufacturer-*.json')
    parser.add_argument('--msi-store', type=Path, action='append', default=[],
                        help='MSI shop catalogue files (default: the official and regional ones)')
    parser.add_argument('--evidence-dir', type=Path, default=ROOT / 'artifacts/official-browser-evidence')
    parser.add_argument('--csv', type=Path, action='append', default=[],
                        help='Manufacturer listing CSV (repeatable)')
    parser.add_argument('--csv-brand', default='', help='Brand for CSVs without a brand column')
    parser.add_argument('--include-retailer', action='store_true',
                        help='Also index catalogue products whose MPN comes from a shop')
    parser.add_argument('--out-dir', type=Path, default=ROOT / 'work/manufacturer-index')
    parser.add_argument('--date', default=datetime.now(timezone.utc).strftime('%Y%m%d'))
    args = parser.parse_args(argv)

    brand_domains = {}
    domains_path = ROOT / 'data/brand-domains.json'
    if domains_path.is_file():
        try:
            brand_domains = json.loads(domains_path.read_text())
        except (OSError, ValueError):
            brand_domains = {}

    index = Index()
    index.brand_domains = brand_domains
    index.load_evidence(args.evidence_dir)

    read_catalog(index, args.catalog_dir, args.include_retailer)

    alias_paths = list(args.aliases)
    if not alias_paths:
        default_alias = ROOT / 'data/product-identifiers.json'
        if default_alias.is_file():
            alias_paths.append(default_alias)
        alias_paths.extend(sorted((ROOT / 'data/aliases').glob('*.json')))
    read_aliases(index, alias_paths)

    read_scrapers(index, args.scraper_glob)

    stores = list(args.msi_store)
    if not stores:
        official = ROOT / 'artifacts/msi-official-store/all-products.json'
        if official.is_file():
            stores.append(official)
        regional = ROOT / 'artifacts/msi-regional-stores'
        if regional.is_dir():
            for child in sorted(regional.iterdir()):
                candidate = child / 'all-products.json'
                if candidate.is_file():
                    stores.append(candidate)
    for store in stores:
        label = store.parent.name if store.name == 'all-products.json' else store.stem
        read_msi_store(index, store, label)

    read_evidence(index, args.evidence_dir)

    for csv_path in args.csv:
        if csv_path.is_file():
            read_csv(index, csv_path, args.csv_brand)

    rows = index.finalise()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f'manufacturer-index-{args.date}'
    csv_path = args.out_dir / f'{stem}.csv'
    json_path = args.out_dir / f'{stem}.json'
    write_csv(csv_path, rows)
    payload = {
        'generated_at': now_utc(),
        'date': args.date,
        'rows': len(rows),
        'sources': index.sources,
        'evidence_captures': len(index.evidence),
        'fields': FIELDS,
        'rows_detail': rows,
    }
    write_json(json_path, payload)
    for name in ('latest.csv', 'latest.json'):
        source = csv_path if name.endswith('.csv') else json_path
        target = args.out_dir / name
        target.write_bytes(source.read_bytes())

    print(json.dumps({
        'csv': str(csv_path), 'json': str(json_path), 'rows': len(rows),
        'sources': index.sources, 'evidence_captures': len(index.evidence),
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
