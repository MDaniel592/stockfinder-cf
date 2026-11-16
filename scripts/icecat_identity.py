"""Icecat as identity evidence (user policy 2026-11-15).

Icecat is fed by the brands. When the manufacturer site does not publish the
part code, an Icecat record whose GTIN equals the retailer EAN and whose
brand part code equals the retailer MPN identifies the exact product.
Evidence is read from files exported from the research database, never
trusted from the batch itself.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

EXACT_CATEGORIES = ('psu', 'cpu', 'ram', 'storage', 'case', 'cooler')  # case/cooler added 2026-11-15 (Icecat EAN is color/variant specific)


def _digits(value):
    return re.sub(r'\D', '', str(value or '')).lstrip('0')


def verified_icecat_identity(candidate, product, decision, evidence_dir, normalize, canonical_brand):
    claim = decision.get('icecat_evidence')
    if not isinstance(claim, dict):
        return None
    if candidate.get('category_hint') not in EXACT_CATEGORIES or product.get('category_id') != candidate.get('category_hint'):
        raise ValueError('Icecat identity is only accepted for psu, cpu, ram, storage, case, cooler')
    gtin = _digits(claim.get('gtin'))
    if not gtin or gtin != _digits(candidate.get('ean')):
        raise ValueError('Icecat GTIN must equal the retailer EAN')
    path = Path(evidence_dir) / (gtin + '.json')
    if not path.is_file():
        raise ValueError('Icecat evidence file missing for GTIN ' + gtin)
    record = json.loads(path.read_text(encoding='utf-8'))
    mpn_lookup = claim.get('match') == 'mpn_literal' and record.get('lookup') == 'brand_mpn' and not record.get('returned_gtins')
    if record.get('source') != 'Icecat JSON API' or (not mpn_lookup and gtin not in {_digits(g) for g in record.get('returned_gtins', [])}):
        raise ValueError('Icecat evidence does not return the retailer GTIN')
    checked = datetime.fromisoformat(record['checked_at'])
    if checked.tzinfo is None or (checked - datetime.now(timezone.utc)).total_seconds() > 60:
        raise ValueError('Icecat evidence has an invalid timestamp')
    brands = {re.sub(r'[^a-z0-9]', '', str(canonical_brand(b) or '').lower()) for b in (record.get('brand'), candidate.get('brand'), product.get('brand'))}
    bk = lambda b: re.sub(r'[^a-z0-9]', '', str(b or '').lower())
    alias_ok = (decision.get('brand_alias') == 'adata->xpg' and candidate.get('category_hint') in ('case', 'cooler', 'psu')
                and bk(candidate.get('brand')) == 'adata' and bk(product.get('brand')) == 'xpg' and bk(record.get('brand')) == 'xpg')
    if len(brands) != 1 and not alias_ok:
        raise ValueError('Icecat, retailer and canonical brands differ')
    code = normalize(record.get('brand_part_code') or '')
    if claim.get('match') == 'ean_model':
        # Retailer prints a regional/SKU code (e.g. MSI 306-xxxx) that the brand part
        # code does not carry. EAN is in Icecat's GTIN list for the same brand and the
        # Icecat part code is the canonical product's model name.
        model = normalize(re.sub(r'^' + re.escape(str(product.get('brand') or '')) + r'\s+', '', str(product.get('name') or ''), flags=re.I))
        # Equal to the canonical model name, or every digit-bearing token of the Icecat part
        # code (UD850GM, PG5...) appears in the canonical name/MPN (revision words like V2 may differ).
        digit_tokens = [t for t in re.findall(r'[A-Za-z0-9]+', str(record.get('brand_part_code') or '')) if re.search(r'\d', t) and len(t) >= 3]
        haystack = set(re.findall(r'[a-z0-9]+', (str(product.get('name') or '') + ' ' + str(product.get('mpn') or '')).lower()))
        joined = re.sub(r'[^a-z0-9]', '', (str(product.get('name') or '') + str(product.get('mpn') or '')).lower())
        token_match = bool(digit_tokens) and all(t.lower() in haystack or (len(t) >= 4 and t.lower() in joined) for t in digit_tokens)
        if not code or (code != model and not token_match):
            raise ValueError('Icecat part code must match the canonical model for ean_model identity')
    else:
        if not code or code != normalize(product.get('mpn') or '') or code != normalize(candidate.get('mpn') or ''):
            raise ValueError('Icecat brand part code must equal the retailer MPN and the canonical MPN literally')
        if product.get('mpn_source') != 'manufacturer':
            raise ValueError('Canonical product must carry mpn_source manufacturer')
    if 'icecat' not in decision.get('note', '').lower():
        raise ValueError('Decision note must state that identity comes from Icecat')
    return record
