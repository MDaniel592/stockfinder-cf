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

EXACT_CATEGORIES = ('psu', 'cpu', 'ram', 'storage')


def _digits(value):
    return re.sub(r'\D', '', str(value or '')).lstrip('0')


def verified_icecat_identity(candidate, product, decision, evidence_dir, normalize, canonical_brand):
    claim = decision.get('icecat_evidence')
    if not isinstance(claim, dict):
        return None
    if candidate.get('category_hint') not in EXACT_CATEGORIES or product.get('category_id') != candidate.get('category_hint'):
        raise ValueError('Icecat identity is only accepted for exact-MPN categories (psu, cpu, ram, storage)')
    gtin = _digits(claim.get('gtin'))
    if not gtin or gtin != _digits(candidate.get('ean')):
        raise ValueError('Icecat GTIN must equal the retailer EAN')
    path = Path(evidence_dir) / (gtin + '.json')
    if not path.is_file():
        raise ValueError('Icecat evidence file missing for GTIN ' + gtin)
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('source') != 'Icecat JSON API' or gtin not in {_digits(g) for g in record.get('returned_gtins', [])}:
        raise ValueError('Icecat evidence does not return the retailer GTIN')
    checked = datetime.fromisoformat(record['checked_at'])
    if checked.tzinfo is None or (checked - datetime.now(timezone.utc)).total_seconds() > 60:
        raise ValueError('Icecat evidence has an invalid timestamp')
    brands = {canonical_brand(record.get('brand')), canonical_brand(candidate.get('brand')), canonical_brand(product.get('brand'))}
    if len(brands) != 1:
        raise ValueError('Icecat, retailer and canonical brands differ')
    code = normalize(record.get('brand_part_code') or '')
    if claim.get('match') == 'ean_model':
        # Retailer prints a regional/SKU code (e.g. MSI 306-xxxx) that the brand part
        # code does not carry. EAN is in Icecat's GTIN list for the same brand and the
        # Icecat part code is the canonical product's model name.
        model = normalize(re.sub(r'^' + re.escape(str(product.get('brand') or '')) + r'\s+', '', str(product.get('name') or ''), flags=re.I))
        if not code or code != model:
            raise ValueError('Icecat part code must equal the canonical model name for ean_model identity')
    else:
        if not code or code != normalize(product.get('mpn') or '') or code != normalize(candidate.get('mpn') or ''):
            raise ValueError('Icecat brand part code must equal the retailer MPN and the canonical MPN literally')
        if product.get('mpn_source') != 'manufacturer':
            raise ValueError('Canonical product must carry mpn_source manufacturer')
    if 'icecat' not in decision.get('note', '').lower():
        raise ValueError('Decision note must state that identity comes from Icecat')
    return record
