"""Compare the pending review queue with the manufacturer reference index.

For every pending retailer row the script looks for the same MPN in the index built by
``scripts/manufacturer_index.py`` and says which official document publishes it.  It never
approves or rejects anything: it is triage, so a reviewer knows where the pending rows already
have a manufacturer code and where the source still has to be found.

Matching rules (in this order):

1. exact MPN (uppercase, without separators) inside the same brand;
2. the same MPN without a regional suffix (``-EU``, ``-BEU``, ``-EEU``, ``_EU``), as allowed by the
   review guide of 14-nov-2026;
3. the same MPN in another brand: reported as a conflict, never used as identity;
4. the same EAN in the index: reported as a contradiction check.

Name similarity is never used to decide identity.  A row of the index whose model text matches is
listed as a lead (``pista``) and has to be checked by hand.

Usage:
  python3 scripts/manufacturer_diff.py
  python3 scripts/manufacturer_diff.py --index artifacts/manufacturer-index/latest.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REGION_SUFFIXES = ('-EU', '-BEU', '-EEU', '_EU', '-EEUEU')

# Words that carry no identity: they are the shop's own description or a brand name.
STOPWORDS = {
    'asus', 'asrock', 'msi', 'gigabyte', 'aorus', 'kingston', 'corsair', 'seagate', 'crucial',
    'lian', 'li', 'thermaltake', 'coolermaster', 'master', 'nzx t', 'antec', 'phanteks', 'deepcool',
    'pc', 'de', 'la', 'el', 'los', 'las', 'un', 'una', 'para', 'con', 'sin', 'y', 'o', 'por',
    'fuente', 'alimentacion', 'caja', 'tarjeta', 'grafica', 'grafico', 'motherboard', 'placa',
    'base', 'cooler', 'disipador', 'refrigeracion', 'memoria', 'ram', 'ssd', 'disco', 'duro',
    'procesador', 'cpu', 'intel', 'amd', 'nvidia', 'negro', 'negra', 'blanca', 'blanco',
    'precio', 'stock', 'envio', 'garantia', 'nuevo', 'atx', 'micro', 'mini', 'itx', 'e',
}


def tokens(value: str) -> set:
    parts = re.findall(r'[a-z0-9]+', str(value or '').casefold())
    return {part for part in parts if len(part) > 1 and part not in STOPWORDS}

# Where the official listing of each brand lives, to help the reviewer.
LISTING_HINTS = {
    'asus': 'https://www.asus.com/sitemap.xml -> /es/sitemap/esN.xml (ficha /techspec/)',
    'asrock': 'https://www.asrock.com/sitemap.xml (mismo HTML en tw.asrock.com)',
    'msi': 'https://es-store.msi.com/products.json?limit=250&page=N (variants[].sku)',
    'seasonic': 'https://rma.seasonic.com/example_import.csv',
    'gigabyte': 'https://www.gigabyte.com/<category>/<model>/ (ficha oficial)',
    'corsair': 'https://www.corsair.com/<locale>/search?q=<model>',
    'coolermaster': 'https://www.coolermaster.com/<model> (ficha oficial)',
    'lianli': 'https://lian-li.com/<model> (ficha oficial)',
    'thermaltake': 'https://www.thermaltake.com/<model> (ficha oficial)',
    'antec': 'https://www.antec.com/product/<model> (ficha oficial)',
    'patriot': 'https://www.patriotmemory.com/products/<model> (ficha oficial)',
    'silverstone': 'https://www.silverstonetek.com/en/product/info/<model>/',
}


def norm_brand(value) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def norm_mpn(value) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def norm_model(value) -> str:
    return ' '.join(re.findall(r'[a-z0-9]+', str(value or '').casefold()))


def strip_region(value: str) -> str:
    """Drop a trailing regional suffix (-EU, -BEU, -EEU, _EU) from a retailer MPN."""
    upper = str(value or '').strip().upper()
    for suffix in REGION_SUFFIXES:
        if upper.endswith(suffix) and len(upper) > len(suffix):
            return upper[: -len(suffix)]
    return upper


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


class Reference:
    """The index, ready to be queried."""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.by_brand_mpn: dict[tuple, list[dict]] = defaultdict(list)
        self.by_mpn: dict[str, list[dict]] = defaultdict(list)
        self.by_ean: dict[str, list[dict]] = defaultdict(list)
        self.by_model: dict[tuple, list[dict]] = defaultdict(list)
        self.by_brand_token: dict[tuple, list[dict]] = defaultdict(list)
        for row in rows:
            brand = norm_brand(row.get('brand'))
            mpn = norm_mpn(row.get('mpn'))
            if mpn:
                self.by_brand_mpn[(brand, mpn)].append(row)
                self.by_mpn[mpn].append(row)
            ean = re.sub(r'\D', '', str(row.get('ean') or ''))
            if ean:
                self.by_ean[ean].append(row)
            model = norm_model(row.get('model'))
            if model:
                self.by_model[(brand, model)].append(row)
            for token in tokens(row.get('model')):
                self.by_brand_token[(brand, token)].append(row)

    def best(self, rows: list[dict]) -> dict:
        def rank(item: dict):
            source = str(item.get('source') or '').split(':', 1)[0]
            return (0 if item.get('mpn_source') == 'manufacturer' else 1,
                    0 if source in ('scraper', 'csv') else 1,
                    0 if item.get('source_url') else 1)
        return sorted(rows, key=rank)[0] if rows else {}

    def find(self, brand: str, mpn: str) -> tuple[str, list[dict]]:
        """Return (status, rows) for a retailer observation."""
        key = norm_mpn(mpn)
        if not key:
            return 'sin_mpn', []
        brand_key = norm_brand(brand)
        exact = self.by_brand_mpn.get((brand_key, key), [])
        if exact:
            return 'mpn', exact
        base = norm_mpn(strip_region(mpn))
        if base and base != key:
            suffixed = self.by_brand_mpn.get((brand_key, base), [])
            if suffixed:
                return 'mpn_sufijo_regional', suffixed
        other = [r for r in self.by_mpn.get(key, []) if norm_brand(r.get('brand')) != brand_key]
        if other:
            return 'mpn_otra_marca', other
        return 'sin_respaldo', []


def load_index(path: Path) -> tuple[list[dict], dict]:
    payload = json.loads(path.read_text(errors='replace'))
    if isinstance(payload, dict):
        rows = payload.get('rows_detail') or payload.get('rows') or []
        meta = {k: v for k, v in payload.items() if k not in ('rows_detail', 'rows')}
    else:
        rows, meta = payload, {}
    return rows, meta


def load_pending(path: Path, shop: str) -> list[dict]:
    try:
        payload = json.loads(path.read_text(errors='replace'))
    except (OSError, ValueError):
        return []
    if isinstance(payload, dict):
        payload = payload.get('pending') or payload.get('items') or []
    out = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        candidate = entry.get('candidate') or {}
        if not isinstance(candidate, dict):
            continue
        out.append({
            'shop': shop,
            'review_id': entry.get('review_id'),
            'brand': candidate.get('brand') or '',
            'mpn': candidate.get('mpn') or '',
            'ean': candidate.get('ean') or '',
            'name': candidate.get('name') or '',
            'category': candidate.get('category_hint') or '',
            'retailer_sku': candidate.get('retailer_sku') or '',
            'url_public': candidate.get('url_public') or '',
            'review_note': entry.get('review_note') or '',
        })
    return out


def leads(reference: Reference, item: dict) -> list[dict]:
    """Index rows of the same brand whose model text is the same; a lead, never an identity."""
    model = norm_model(item['name'])
    if not model:
        return []
    found = reference.by_model.get((norm_brand(item['brand']), model), [])
    return [{'mpn': r.get('mpn'), 'model': r.get('model'), 'source': r.get('source'),
             'source_url': r.get('source_url'), 'mpn_source': r.get('mpn_source')}
            for r in found[:3]]


def model_lead(reference: Reference, item: dict) -> dict | None:
    """The official model behind the retailer's code, when the index publishes it.

    Many shops print an internal code that the manufacturer never publishes ("90-UXC065-GNEAAA"
    for an ASRock CL-650G).  The official model is still in the index, so the row is reported as a
    lead: it says which official page to read, not that the two codes are the same product.
    """
    brand = norm_brand(item['brand'])
    name_tokens = tokens(item['name'])
    if not name_tokens:
        return None
    candidates: dict[int, dict] = {}
    counts: dict[int, int] = {}
    for token in name_tokens:
        for row in reference.by_brand_token.get((brand, token), []):
            key = id(row)
            candidates[key] = row
            counts[key] = counts.get(key, 0) + 1
    best = None
    for key, row in candidates.items():
        model_tokens = tokens(row.get('model'))
        if not model_tokens:
            continue
        shared = model_tokens & name_tokens
        if len(shared) < 2:
            continue
        coverage = len(shared) / len(model_tokens)
        if coverage < 0.6:
            continue
        score = (len(shared), coverage, counts.get(key, 0))
        if best is None or score > best[0]:
            best = (score, row, sorted(shared))
    if best is None:
        return None
    _, row, shared = best
    return {
        'mpn': row.get('mpn'), 'model': row.get('model'), 'source': row.get('source'),
        'source_url': row.get('source_url'), 'mpn_source': row.get('mpn_source'),
        'category': row.get('category'), 'shared_tokens': shared,
    }


def build_item(reference: Reference, item: dict) -> dict:
    status, rows = reference.find(item['brand'], item['mpn'])
    best = reference.best(rows)
    ean_rows = reference.by_ean.get(re.sub(r'\D', '', item['ean']), []) if item['ean'] else []
    out = dict(item)
    out['status'] = status
    out['mpn_source'] = best.get('mpn_source', '')
    out['reference_source'] = best.get('source', '')
    out['reference_url'] = best.get('source_url', '')
    out['reference_local_path'] = best.get('local_path', '')
    out['reference_sha256'] = best.get('sha256', '')
    out['reference_mpn'] = best.get('mpn', '')
    out['reference_model'] = best.get('model', '')
    out['ean_in_index'] = len(ean_rows)
    out['lead'] = leads(reference, item) if status == 'sin_respaldo' else []
    out['model_lead'] = model_lead(reference, item) if status in ('sin_respaldo', 'mpn_otra_marca') else None
    out['listing_hint'] = LISTING_HINTS.get(norm_brand(item['brand']), '')
    return out


def markdown(payload: dict) -> str:
    lines = ['# Índice de fabricantes frente a la cola pendiente', '']
    lines.append(f"Generado: {payload['generated_at']}")
    lines.append(f"Índice: {payload['index_path']} (generado {payload.get('index_generated_at', '?')})")
    lines.append('')
    lines.append('| Tienda | Pendientes | Con MPN de fabricante | Sufijo regional | Otra marca | Sin respaldo |')
    lines.append('|---|---:|---:|---:|---:|---:|')
    for shop, stats in payload['shops'].items():
        lines.append(f"| {shop} | {stats['total']} | {stats['mpn']} | {stats['mpn_sufijo_regional']} "
                     f"| {stats['mpn_otra_marca']} | {stats['sin_respaldo']} |")
    lines.append('')

    for shop, items in payload['items_by_shop'].items():
        pending = [i for i in items if i['status'] in ('sin_respaldo', 'mpn_otra_marca')]
        if not pending:
            continue
        lines.append(f'## {shop}: filas sin respaldo directo ({len(pending)})')
        lines.append('')
        lines.append('| Rev | Marca | MPN observado | EAN | Categoría | Modelo | '
                     'Modelo oficial (pista) | Dónde mirar |')
        lines.append('|---|---|---|---|---|---|---|---|')
        for item in pending:
            lead = item['lead'][0]['model'] if item['lead'] else ''
            model_lead = (item.get('model_lead') or {}).get('model', '')
            if model_lead:
                model_lead += f" ({(item['model_lead'] or {}).get('mpn') or 'sin código'})"
            lines.append(f"| {item['review_id']} | {item['brand']} | `{item['mpn']}` | {item['ean']} "
                         f"| {item['category']} | {item['name'][:44]} | {model_lead[:52] or lead[:52]} "
                         f"| {item['listing_hint'] or 'ficha oficial del fabricante'} |")
        lines.append('')

    conflicts = [i for items in payload['items_by_shop'].values() for i in items
                 if i['status'] == 'mpn_otra_marca']
    if conflicts:
        lines.append('## MPN publicado por otra marca (conflicto)')
        lines.append('')
        for item in conflicts:
            lines.append(f"- #{item['review_id']} {item['brand']} `{item['mpn']}` aparece como "
                         f"{item['reference_source']} ({item['reference_mpn']}).")
        lines.append('')
    return '\n'.join(lines) + '\n'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=ROOT / 'work/manufacturer-index/latest.json')
    parser.add_argument('--pending', type=Path, action='append', default=[],
                        help='Pending queue files (default: the LDLC and Neobyte ones)')
    parser.add_argument('--out-dir', type=Path, default=ROOT / 'work/manufacturer-diff')
    parser.add_argument('--date', default=datetime.now(timezone.utc).strftime('%Y%m%d'))
    args = parser.parse_args(argv)

    pending_paths = list(args.pending)
    if not pending_paths:
        for shop, relative in (('ldlc', 'artifacts/ldlc/pending.json'),
                               ('neobyte', 'artifacts/neobyte/pending.json')):
            candidate = ROOT / relative
            if candidate.is_file():
                pending_paths.append(candidate)

    if not args.index.is_file():
        print(f'Index not found: {args.index}. Run scripts/manufacturer_index.py first.',
              file=sys.stderr)
        return 2
    rows, meta = load_index(args.index)
    reference = Reference(rows)

    items_by_shop: dict[str, list[dict]] = {}
    for path in pending_paths:
        shop = path.parent.name
        pending = load_pending(path, shop)
        if pending:
            items_by_shop[shop] = [build_item(reference, item) for item in pending]

    shops = {}
    for shop, items in items_by_shop.items():
        counter = Counter(item['status'] for item in items)
        shops[shop] = {
            'total': len(items),
            'mpn': counter.get('mpn', 0),
            'mpn_sufijo_regional': counter.get('mpn_sufijo_regional', 0),
            'mpn_otra_marca': counter.get('mpn_otra_marca', 0),
            'sin_respaldo': counter.get('sin_respaldo', 0),
            'sin_mpn': counter.get('sin_mpn', 0),
        }

    payload = {
        'generated_at': now_utc(),
        'index_path': str(args.index),
        'index_generated_at': meta.get('generated_at', ''),
        'index_rows': len(rows),
        'shops': shops,
        'items_by_shop': items_by_shop,
    }

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f'manufacturer-diff-{args.date}'
    json_path = args.out_dir / f'{stem}.json'
    md_path = args.out_dir / f'{stem}.md'
    temporary = json_path.with_name(json_path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, json_path)
    temporary = md_path.with_name(md_path.name + '.tmp')
    temporary.write_text(markdown(payload), encoding='utf-8')
    os.replace(temporary, md_path)
    (args.out_dir / 'latest.json').write_bytes(json_path.read_bytes())
    (args.out_dir / 'latest.md').write_bytes(md_path.read_bytes())

    print(json.dumps({'json': str(json_path), 'markdown': str(md_path), 'shops': shops},
                     ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
