"""Consume incremental Neobyte review batches in the isolated local database only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'nas'), str(ROOT / 'scripts')]
from import_catalog import import_aliases, import_products, normalize, canonical_brand
from review_queue import approve
from validate_batch import registrable, validate, alnum
from generate_seed import CATEGORIES
from icecat_identity import verified_icecat_identity
from amd_opn import verified_amd_opn
from manufacturer_ean import verified_manufacturer_ean
import psycopg


ICECAT_EVIDENCE_DIR = ROOT / 'artifacts/neobyte/icecat-evidence'


def region_equal(retailer, canonical):
    """Policy 2026-11-14: a final regional suffix on the retailer code does not change the product.
    Also covers a base code whose last letter is part of the suffix (canonical ...-3B, retailer ...-3BEU)."""
    a, b = normalize(retailer or ''), normalize(canonical or '')
    if not a or not b:
        return False
    return strip_region(a) == strip_region(b) or a in (b + 'eu', b + '-eu', b + '_eu', b + 'beu', b + '-beu')


def strip_region(value):
    """User policy 2026-11-14: a trailing regional suffix (-EU, -BEU, -EEU, _EU) does not change the product."""
    return re.sub(r'[-_ ]?(?:b|e)?eu$', '', normalize(value or '')).strip()


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')
    temporary.replace(path)


def validate_review(conn, decision, aliases):
    review = conn.execute("SELECT candidate,status FROM retailer_review_queue WHERE review_id=%s AND shop_id='neobyte'", (decision['review_id'],)).fetchone()
    if not review:
        raise ValueError('Not a Neobyte review')
    if review[1] == 'approved':
        return None, []
    if review[1] != 'pending' or decision.get('action') != 'approve':
        raise ValueError('Only pending approvals are accepted')
    candidate = review[0]
    if ('manufacturer_variant_conflict' in (candidate.get('missing_fields') or []) or
        candidate.get('identity_status') in ('retailer_mpn_changed_requires_review', 'retailer_ean_variant_conflict')):
        raise ValueError('Retailer identity has an unresolved manufacturer variant conflict')
    pid = decision['product_id']
    result = conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id=%s AND deleted_at IS NULL", (pid,)).fetchone()
    if not result:
        raise ValueError('Canonical product absent')
    product = dict(id=pid, **result[0])
    icecat_identity = verified_icecat_identity(candidate, product, decision, ICECAT_EVIDENCE_DIR, normalize, canonical_brand)
    amd_identity = verified_amd_opn(candidate, product, decision, normalize)
    mfr_ean_identity = verified_manufacturer_ean(candidate, product, decision, registrable)
    # User policy 2026-11-15: criterion B for PSUs whose brand never prints the store code.
    # Needs the same full variant evidence as cases/coolers with >=4 attributes (watts, ATX standard, modular, color/80 PLUS...).
    psu_b = (candidate.get('category_hint') == 'psu' and decision.get('psu_criterion_b') is True
             and len((decision.get('variant_evidence') or {}).get('attributes') or {}) >= 4)
    if not (icecat_identity and decision.get('brand_alias') == 'adata->xpg') and canonical_brand(candidate.get('brand')) != canonical_brand(product['brand']):
        raise ValueError('Retailer and manufacturer brands differ')
    if product['category_id'] != candidate['category_hint']:
        raise ValueError('Product and retailer categories differ')
    chosen = [a for a in aliases if a.get('product_id') == pid and a.get('source_url') == candidate['url_public']]
    evidence = decision.get('note', '') + ' ' + ' '.join(a.get('note', '') for a in chosen)
    if candidate['category_hint'] not in ('gpu', 'motherboard', 'case', 'cooler') and not (icecat_identity or amd_identity or mfr_ean_identity or psu_b):
        if chosen or product.get('mpn_source') != 'manufacturer' or not candidate.get('mpn') or not region_equal(candidate['mpn'], product.get('mpn', '')):
            raise ValueError('Other component categories require an exact manufacturer MPN, never criterion B aliases')
        if strip_region(product['mpn']) not in normalize(evidence):
            raise ValueError('Review must explicitly document the exact manufacturer MPN')
    official_domain = registrable(urlparse(product['manufacturer_url']).hostname)[0]
    # User-authorized extension (2026-11-14): exact full variants for cases/coolers.
    # This remains an explicit human review, never an automatic name match.
    if candidate['category_hint'] in ('case', 'cooler') and icecat_identity:
        pass  # Icecat EAN+brand+part code identifies the exact variant; no separate variant_evidence needed
    elif candidate['category_hint'] in ('case', 'cooler') or psu_b:
        exact_mpn = (not chosen and product.get('mpn_source') == 'manufacturer'
                     and bool(candidate.get('mpn'))
                     and normalize(candidate['mpn']) == normalize(product.get('mpn', '')))
        if not exact_mpn:
            variant = decision.get('variant_evidence') or {}
            attrs = variant.get('attributes')
            if (not variant.get('official_model') or not variant.get('retailer_model')
                    or variant.get('ean_checked') is not True
                    or not isinstance(attrs, dict) or len(attrs) < 3
                    or not all(isinstance(k, str) and k.strip() and isinstance(v, str) and v.strip()
                               for k, v in attrs.items())):
                raise ValueError('Case/cooler criterion B requires full variant evidence and explicit EAN check')
            source = urlparse(str(variant.get('source_url', '')))
            if (source.scheme != 'https' or not source.hostname
                    or registrable(source.hostname)[0] != official_domain):
                raise ValueError('Variant evidence must cite the canonical manufacturer domain')
    sources = re.findall(r'https://[^\s<>"\)]+', evidence)
    if not icecat_identity and not any(urlparse(url).hostname and registrable(urlparse(url).hostname)[0] == official_domain for url in sources):
        raise ValueError('Review must cite the canonical manufacturer domain')
    if len(decision.get('note', '').strip()) < 40:
        raise ValueError('Detailed decision evidence required')
    for alias in chosen:
        if alias.get('kind') != 'mpn' or normalize(alias.get('value', '')) != normalize(candidate.get('mpn') or ''):
            raise ValueError('Only the observed exact retailer MPN can become an alias')
        if alias.get('scope') or len(alias.get('note', '').strip()) < 40:
            raise ValueError('Alias scope cannot override the manufacturer brand; evidence required')
    if candidate.get('ean'):
        conflict = conn.execute("SELECT product_id FROM product_identifiers WHERE kind='ean' AND scope='global' AND value=%s", (normalize(candidate['ean']),)).fetchone()
        if conflict and conflict[0] != pid:
            raise ValueError('EAN contradicts the reviewed identity')
    if mfr_ean_identity and candidate.get('ean'):
        chosen.append(dict(product_id=pid, kind='ean', scope='global', value=candidate['ean'], source_url=candidate['url_public'],
                           note='EAN published by the manufacturer on ' + decision['manufacturer_ean']['source_url'] + ' for model ' + decision['manufacturer_ean']['model_on_page'] + '. ' + decision['note']))
    if icecat_identity and decision['icecat_evidence'].get('match') == 'ean_model' and candidate.get('ean'):
        chosen.append(dict(product_id=pid, kind='ean', scope='global', value=candidate['ean'], source_url=candidate['url_public'],
                           note='EAN linked through Icecat record ' + str(icecat_identity.get('icecat_id')) + ' (brand part code ' + str(icecat_identity.get('brand_part_code')) + '). ' + decision['note']))
    return product, chosen


def import_reviewed_products(conn, products, decisions):
    if not products:
        return 0
    referenced = {d.get('product_id') for d in decisions}
    existing = {pid: data for pid, data in conn.execute("SELECT entity_id,data FROM catalog_entities WHERE entity='product' AND deleted_at IS NULL").fetchall()}
    new = []
    for raw in products:
        product = dict(raw)
        if product.get('category_id') not in {slug for slug, _ in CATEGORIES} or product.get('id') not in referenced:
            raise ValueError('New products must belong to supported categories and reviewed decisions')
        amd_new = (product['category_id'] == 'cpu' and product.get('mpn_source') == 'retailer'
                   and any(d.get('product_id') == product.get('id') and isinstance(d.get('amd_opn'), dict) for d in decisions))
        psu_new = (product['category_id'] == 'psu' and product.get('mpn_source') == 'retailer'
                   and any(d.get('product_id') == product.get('id') and d.get('psu_criterion_b') is True for d in decisions))
        if product['category_id'] not in ('gpu', 'motherboard', 'case', 'cooler') and product.get('mpn_source') != 'manufacturer' and not amd_new and not psu_new:
            raise ValueError('Other new component categories require a verified manufacturer MPN')
        if not product.get('manufacturer_evidence'):
            raise ValueError('New products require explicit official evidence')
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', product.get('specs_verified_at', '')):
            product['specs_verified_at'] += 'T00:00:00Z'
        if product.get('specs_verified_at'):
            verified_at = datetime.fromisoformat(product['specs_verified_at'].replace('Z', '+00:00'))
            if verified_at.tzinfo is None:
                raise ValueError('Specification verification timestamp requires a timezone')
            if (verified_at - datetime.now(timezone.utc)).total_seconds() > 60:
                raise ValueError('Specification verification timestamp cannot be in the future')
        current = existing.get(product['id'])
        if current:
            if any(current.get(key) != product.get(key) for key in ('name', 'brand', 'mpn', 'manufacturer_url')):
                raise ValueError('New product ID collides with an existing canonical identity')
            continue
        new.append(product)
    if not new:
        return 0
    seen = {key: {} for key in ('id', 'slug', 'name', 'mpn')}
    for pid, product in existing.items():
        seen['id'][pid] = 'local database'
        seen['slug'][product['slug']] = 'local database'
        seen['name'][f"{alnum(product['brand'])}:{product['name'].lower()}"] = 'local database'
        if product.get('mpn'):
            seen['mpn'][f"{alnum(product['brand'])}:{product['mpn'].lower()}"] = 'local database'
    extra = json.loads((ROOT / 'data/brand-domains.json').read_text())
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'reviewed-products.json'
        path.write_text(json.dumps(new))
        errors, warnings = validate(path, extra, seen)
    if errors:
        raise ValueError('; '.join(errors))
    return import_products(conn, new)


def classify_errors(conn, errors):
    """Expose current blockers and preserve resolved review failures as history."""
    review_ids = sorted({rid for error in errors
                         for rid in ([error['review_id']] if error.get('review_id') is not None
                                     else error.get('review_ids', []))})
    statuses = {}
    if review_ids:
        statuses = {row[0]: (row[1], row[2]) for row in conn.execute(
            "SELECT review_id,status,review_note FROM retailer_review_queue WHERE shop_id='neobyte' AND review_id=ANY(%s)",
            (review_ids,)).fetchall()}

    def resolved(review_id):
        row = statuses.get(review_id)
        if not row:
            return False
        status, note = row
        return status in ('approved', 'rejected') or (
            status == 'pending' and str(note or '').startswith('Pendiente:'))

    active, historical = [], []
    for error in errors:
        ids = ([error['review_id']] if error.get('review_id') is not None
               else error.get('review_ids', []))
        (historical if ids and all(resolved(rid) for rid in ids) else active).append(error)
    return active, historical


def cycle(conn, directory):
    global ICECAT_EVIDENCE_DIR
    ICECAT_EVIDENCE_DIR = directory / 'icecat-evidence'
    state_file = directory / 'pipeline-state.json'
    state = json.loads(state_file.read_text()) if state_file.exists() else {'files': {}}
    state['files'] = {key: value for key, value in state['files'].items() if '-batch-' in key}
    state.setdefault('superseded_errors', [])
    for path in sorted((directory / 'reviews').glob('*-batch-*.json')):
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        if state['files'].get(path.name, {}).get('sha256') == digest:
            continue
        batch = json.loads(payload)
        report = dict(sha256=digest, products=0, approved=0, retained=0, errors=[])
        previous = state['files'].get(path.name)
        if previous and previous.get('sha256') != digest:
            for old_error in previous.get('errors', []):
                archived = dict(old_error, batch=path.name, sha256=previous['sha256'])
                if archived not in state['superseded_errors']:
                    state['superseded_errors'].append(archived)
        try:
            with conn.transaction():
                report['products'] = import_reviewed_products(conn, batch.get('products', []), batch.get('decisions', []))
        except Exception as error:
            report['errors'].append(dict(stage='products', error=str(error),
                                         review_ids=sorted({d.get('review_id') for d in batch.get('decisions', [])
                                                            if d.get('review_id') is not None})))
        for decision in batch.get('decisions', []):
            try:
                with conn.transaction():
                    product, aliases = validate_review(conn, decision, batch.get('aliases', []))
                    if product:
                        import_aliases(conn, [product], aliases)
                        approve(conn, decision['review_id'], decision['note'], product['id'])
                        report['approved'] += 1
            except Exception as error:
                report['errors'].append(dict(review_id=decision.get('review_id'), error=str(error)))
        for retained in batch.get('retained', []):
            reason = str(retained.get('reason', '')).strip()
            if not reason:
                continue
            conn.execute("UPDATE retailer_review_queue SET review_note=%s WHERE review_id=%s AND shop_id='neobyte' AND status='pending'", ('Pendiente: ' + reason, retained['review_id']))
            report['retained'] += 1
        conn.commit()
        state['files'][path.name] = report
        print(json.dumps(dict(event='neobyte_review_batch', file=path.name, **report)), flush=True)
        atomic_json(state_file, state)
    rows = conn.execute("SELECT review_id,candidate,proposed_product_id,review_note FROM retailer_review_queue WHERE shop_id='neobyte' AND status='pending' ORDER BY review_id").fetchall()
    atomic_json(directory / 'pending.json', [dict(zip(('review_id', 'candidate', 'proposed_product_id', 'review_note'), row)) for row in rows])
    products = conn.execute("SELECT entity_id,data FROM catalog_entities WHERE entity='product' AND deleted_at IS NULL ORDER BY entity_id").fetchall()
    atomic_json(directory / 'catalog.json', [dict(id=pid, data=data) for pid, data in products])
    identifiers = conn.execute('SELECT kind,scope,value,product_id FROM product_identifiers').fetchall()
    atomic_json(directory / 'identifiers.json', [dict(zip(('kind', 'scope', 'value', 'product_id'), row)) for row in identifiers])
    # Count approved review IDs, not write deltas: rewritten or renamed batches
    # must not erase the work already applied or double count the same decision.
    approved_review_ids = set()
    for filename, report in state['files'].items():
        reviewed = json.loads((directory / 'reviews' / filename).read_text())
        ids = [d['review_id'] for d in reviewed.get('decisions', [])]
        if ids:
            approved_review_ids.update(row[0] for row in conn.execute("SELECT review_id FROM retailer_review_queue WHERE shop_id='neobyte' AND status='approved' AND review_id=ANY(%s)", (ids,)).fetchall())
    counts = dict(conn.execute("SELECT status,count(*) FROM retailer_review_queue WHERE shop_id='neobyte' GROUP BY status").fetchall())
    all_errors = list(state['superseded_errors'])
    for filename, report in state['files'].items():
        review_path = directory / 'reviews' / filename
        try:
            batch_ids = sorted({d.get('review_id') for d in json.loads(review_path.read_text()).get('decisions', [])
                                if d.get('review_id') is not None})
        except (OSError, json.JSONDecodeError):
            batch_ids = []
        for error in report['errors']:
            if error.get('stage') == 'products' and not error.get('review_ids'):
                error['review_ids'] = batch_ids
            all_errors.append(error)
    active_errors, historical_errors = classify_errors(conn, all_errors)
    atomic_json(directory / 'pipeline-status.json', dict(updated_at=datetime.now(timezone.utc).isoformat(), queue=counts,
                 review_batches=len(state['files']), reviews_approved=len(approved_review_ids),
                 errors=active_errors, historical_errors=historical_errors))
    conn.commit()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    if urlparse(args.database_url).path not in ('/stockfinder_neobyte_local', '/stockfinder'):  # production allowed since 2026-11-16
        parser.error('Only stockfinder or stockfinder_neobyte_local is permitted')
    args.directory.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(args.database_url) as conn:
        while True:
            cycle(conn, args.directory)
            if not args.watch:
                break
            time.sleep(30)
