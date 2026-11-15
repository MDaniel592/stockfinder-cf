"""Manufacturer-published EAN/UPC + model as identity (user policy 2026-11-15).

For exact-MPN categories where the manufacturer page publishes the barcode and
a model name that differs from the retailer MPN (e.g. Antec CSK850 PRO).
The retailer EAN must equal the barcode printed on the manufacturer-domain page
(UPC = EAN without the leading 0). The decision must quote the URL, barcode and model.
"""
import re
from urllib.parse import urlparse

EXACT_CATEGORIES = ('psu', 'cpu', 'ram', 'storage')


def _digits(value):
    return re.sub(r'\D', '', str(value or '')).lstrip('0')


def verified_manufacturer_ean(candidate, product, decision, registrable):
    claim = decision.get('manufacturer_ean')
    if not isinstance(claim, dict):
        return None
    if candidate.get('category_hint') not in EXACT_CATEGORIES or product.get('category_id') != candidate.get('category_hint'):
        raise ValueError('manufacturer_ean is only accepted for psu, cpu, ram, storage')
    url = urlparse(str(claim.get('source_url', '')))
    if url.scheme != 'https' or not url.hostname:
        raise ValueError('manufacturer_ean requires an https source_url')
    if registrable(url.hostname)[0] != registrable(urlparse(product['manufacturer_url']).hostname)[0]:
        raise ValueError('manufacturer_ean source must be on the canonical manufacturer domain')
    if not _digits(candidate.get('ean')) or _digits(claim.get('ean_on_page')) != _digits(candidate.get('ean')):
        raise ValueError('Barcode on the manufacturer page must equal the retailer EAN')
    model = str(claim.get('model_on_page') or '').strip()
    note = decision.get('note', '').lower()
    if len(model) < 3 or model.lower() not in note or claim['source_url'].lower() not in note or str(claim['ean_on_page']).lower() not in note:
        raise ValueError('Decision note must quote the manufacturer URL, barcode and model')
    return claim
