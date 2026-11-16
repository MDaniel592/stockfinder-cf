"""Import curated manufacturer records; upsert stable identities, never fuzzy-merge offers."""
import argparse
import json
import os
from pathlib import Path
import re

from urllib.parse import urlparse

import psycopg
from catalog import write
from generate_seed import CATEGORIES
from catalog_images import enrich_images
from spec_normalize import normalize_specs
import spec_overrides

ROOT = Path(__file__).resolve().parents[1]
SHOPS = [('pccomponentes', 'PcComponentes', 'https://www.pccomponentes.com'),
         ('coolmod', 'Coolmod', 'https://www.coolmod.com'),
         ('ldlc', 'LDLC', 'https://www.ldlc.com/es-es/'),
         ('neobyte', 'Neobyte', 'https://www.neobyte.es/')]
SHOP_BY_HOST = {urlparse(url).hostname: shop_id for shop_id, _, url in SHOPS}

def normalize(value):
    return ' '.join(value.casefold().split())

# Canonical brand spellings (artifacts/shards/PROTOCOL-w8.md, data/brand-domains.json). Keys are the
# casefolded brand without non-alphanumerics; anything not listed keeps its own spelling.
# TRYX's official store/about pages style the manufacturer name as TRYX; the old
# local spelling "Tryx" is a capitalization variant only (not a separate brand).
BRAND_CANON = ['ASUS', 'ASRock', 'MSI', 'Gigabyte', 'G.SKILL', 'Corsair', 'Kingston', 'ADATA', 'TEAMGROUP', 'Patriot',
               'PowerColor', 'ZOTAC', 'Sapphire', 'XFX', 'PNY', 'Crucial', 'Lexar', 'Samsung', 'SanDisk', 'Kioxia',
               'Micron', 'Intel', 'AMD', 'NVIDIA', 'be quiet!', 'Seasonic', 'Fractal Design', 'NZXT', 'Cooler Master',
               'Thermaltake', 'DeepCool', 'ARCTIC', 'Noctua', 'Lian Li', 'darkFlash', 'NOX', 'Mars Gaming', 'Alphacool', 'TRYX',
               'ENDORFY', 'Phanteks', 'Antec', 'FSP', 'Abysm', 'XPG', 'INNO3D', 'Forgeon', 'Tempest', 'HYTE', 'Seagate',
               'CoolBox', 'UNYKAch', 'WD_BLACK']
BRAND_ALIASES = {'adatatechnology': 'ADATA', 'adatatechnologyco': 'ADATA', 'abysmgaming': 'Abysm',
                 'patriotmemory': 'Patriot', 'teamgroup': 'TEAMGROUP', 'teamgroupinc': 'TEAMGROUP',
                 'asustek': 'ASUS', 'asustekcomputer': 'ASUS', 'gskill': 'G.SKILL', 'msistar': 'MSI',
                 'seagatetechnology': 'Seagate',
                 # Official TooQ product pages use this brand capitalization; some
                 # retailer records title-case it as Tooq (e.g. TQC-3005U3).
                 'tooq': 'TooQ',
                 # Fractal's own site presents products as "Fractal" and identifies
                 # Fractal Design as the PC gaming brand; this is an exact spelling
                 # alias, not a fuzzy prefix rule (see fractal-design.com/about).
                 'fractal': 'Fractal Design',
                 # Neobyte title-cases the official GOODRAM brand; spelling-only alias.
                 'goodram': 'GOODRAM'}
BRAND_MAP = {**{re.sub(r'[^a-z0-9]', '', b.casefold()): b for b in BRAND_CANON}, **BRAND_ALIASES}
BRAND_MAP_VERSION = '15'  # bump when the brand map or spec_normalize rules change: forces re-import of unchanged batches

def canonical_brand(value):
    """Canonical spelling for a brand; unknown brands are only whitespace-normalized."""
    if not isinstance(value, str): return value
    cleaned = ' '.join(value.split())
    return BRAND_MAP.get(re.sub(r'[^a-z0-9]', '', cleaned.casefold()), cleaned)

def identify(conn, kind, scope, value, product_id):
    current = conn.execute('SELECT product_id FROM product_identifiers WHERE kind=%s AND scope=%s AND value=%s',
                           (kind, scope, value)).fetchone()
    if current and current[0] != product_id:
        raise ValueError(f'Identity conflict for {kind}: {product_id} differs from {current[0]}')
    conn.execute('INSERT INTO product_identifiers(kind,scope,value,product_id) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                 (kind,scope,value,product_id))

def load_aliases(root=ROOT):
    """Reviewed identifiers: the legacy data/product-identifiers.json plus one file per verification
    batch in data/aliases/*.json (same record format: product_id, kind, value, source_url, note)."""
    paths = [root/'data/product-identifiers.json', *sorted((root/'data/aliases').glob('*.json'))]
    aliases = []
    for path in paths:
        if path.is_file(): aliases.extend(json.loads(path.read_text()))
    return aliases

def import_aliases(conn, products, aliases):
    """Register store codes as aliases of verified products; never attributed to the manufacturer."""
    by_id = {product['id']: product for product in products}
    for alias in aliases:
        if not alias.get('source_url','').startswith('https://') or not alias.get('note'): raise ValueError('Alias provenance required')
        product = by_id.get(alias['product_id'])
        if product is None: raise ValueError(f"Alias for unknown product {alias['product_id']}")
        scope = alias.get('scope')
        scope = canonical_brand(scope) if scope and scope != 'global' else (scope or canonical_brand(product['brand']))
        identify(conn, alias['kind'], scope, normalize(alias['value']), product['id'])
    return len(aliases)

def retailer_codes(aliases, products):
    """Store codes per product ({product_id: [{shop_id, code}]}) for the optional public `retailer_codes` field.
    Only MPN aliases whose source is a configured shop and that differ from the product's own mpn."""
    by_id = {product['id']: product for product in products}
    codes = {}
    for alias in aliases:
        product = by_id.get(alias.get('product_id'))
        shop_id = SHOP_BY_HOST.get(urlparse(alias.get('source_url','')).hostname)
        if alias.get('kind') != 'mpn' or product is None or shop_id is None: continue
        if normalize(alias['value']) == normalize(product.get('mpn') or ''): continue
        entry = {'shop_id': shop_id, 'code': alias['value'].strip()}
        bucket = codes.setdefault(product['id'], [])
        if entry not in bucket: bucket.append(entry)
    return {product_id: sorted(items, key=lambda e: (e['shop_id'], e['code']))[:8] for product_id, items in codes.items()}

def import_products(conn, products, codes=None, overrides=None):
    """`overrides`: {product_id: {key: registro}} de spec_overrides.index(); se aplican tras normalizar y ganan."""
    ids, models = set(), set()
    for product in products:
        product = {**product, 'brand': canonical_brand(product['brand'])}
        if isinstance(product.get('specs'), dict): product['specs'] = normalize_specs(product.get('category_id'), product['specs'])
        if overrides: product['specs'] = spec_overrides.apply(product, overrides)
        product_id = product['id']
        model = (product['brand'], normalize(product['name']))
        if product_id in ids or model in models: raise ValueError('Duplicate catalog identity')
        if not re.fullmatch('[a-zA-Z0-9_-]{1,100}', product_id): raise ValueError('Invalid product ID')
        if product['category_id'] not in {slug for slug,_ in CATEGORIES}: raise ValueError('Unexpected curated category')
        if not product.get('manufacturer_url','').startswith('https://') or not product.get('specs_verified_at'):
            raise ValueError('Manufacturer provenance required')
        ids.add(product_id); models.add(model)
        identify(conn,'model', product['brand'], model[1], product_id)
        if product.get('mpn'): identify(conn, 'mpn', product['brand'], normalize(product['mpn']), product_id)
        # The public catalog accepts only contract fields. Keep review evidence such as
        # manufacturer_sources in the local batch, but do not send it to the strict API.
        # Also omit absent optional fields instead of serializing JSON null.
        api_fields = {'slug', 'name', 'category_id', 'brand', 'specs', 'manufacturer_url',
                      'specs_verified_at', 'mpn', 'mpn_source', 'image_path', 'image_source_url', 'image_scope'}
        payload = {key:value for key,value in product.items()
                   if key in api_fields and value is not None}
        verified_at = payload.get('specs_verified_at')
        if isinstance(verified_at, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', verified_at):
            payload['specs_verified_at'] = verified_at + 'T00:00:00Z'
        # The Worker contract (z.string().datetime({offset:true})) rejects the WHOLE sync batch on a malformed timestamp.
        verified_at = payload.get('specs_verified_at')
        if isinstance(verified_at, str) and not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})', verified_at):
            raise ValueError(f'specs_verified_at is not an ISO-8601 timestamp with offset: {verified_at!r}')
        # Optional public field; only sent once the deployed Worker contract accepts it (PUBLISH_RETAILER_CODES=1).
        if codes and codes.get(product_id): payload['retailer_codes'] = codes[product_id]
        # Identifiers registered under an older spelling of this brand move to the canonical scope.
        stale = [row for row in conn.execute("SELECT DISTINCT scope FROM product_identifiers WHERE product_id=%s AND scope<>'global' AND scope<>%s",
                                             (product_id, product['brand'])).fetchall() if canonical_brand(row[0]) == product['brand']]
        for (old_scope,) in stale:
            conn.execute('DELETE FROM product_identifiers WHERE product_id=%s AND scope=%s', (product_id, old_scope))
        write(conn,'product',product_id,payload)
    return len(ids)

def retire_demo(conn):
    # Explicitly bounded to the fixture IDs this project created; retain their tombstones.
    rows = conn.execute("""SELECT entity,entity_id,data FROM catalog_entities
      WHERE deleted_at IS NULL AND (
       (entity='product' AND entity_id ~ '^part-[0-9]{4}$') OR
       (entity='offer' AND entity_id ~ '^offer-[0-9]{4}-[01]$') OR
       (entity='shop' AND entity_id IN ('shop-0','shop-1')))""").fetchall()
    for entity, entity_id, data in rows: write(conn,entity,entity_id,data,deleted=True)
    return len(rows)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--retire-demo', action='store_true')
    args = parser.parse_args()
    products = []
    for filename in sorted((ROOT/'data/catalog').glob('*.json')):
        products.extend(json.loads(filename.read_text()))
    products = enrich_images(products,ROOT)
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        conn.execute((ROOT/'db/postgres-migrations/0002_identifiers.sql').read_text())
        for slug,name in CATEGORIES: write(conn,'category',slug,dict(slug=slug,name=name))
        for shop_id,name,url in SHOPS: write(conn,'shop',shop_id,dict(name=name,url_public=url))
        retired = retire_demo(conn) if args.retire_demo else 0
        aliases = load_aliases()
        codes = retailer_codes(aliases, products) if os.environ.get('PUBLISH_RETAILER_CODES') == '1' else None
        overrides, override_errors = spec_overrides.load_overrides(ROOT)
        for name, errors in override_errors.items():  # fichero inválido: se omite entero, no bloquea el resto
            print(json.dumps(dict(event='overrides_invalid', file=name, errors=errors[:5]), ensure_ascii=False))
        count = import_products(conn,products,codes,spec_overrides.index(overrides))
        import_aliases(conn,products,aliases)
    print(json.dumps(dict(products=count,aliases=len(aliases),overrides=len(overrides),retired_fixture_records=retired,shops=len(SHOPS))))

if __name__ == '__main__':main()
