"""AMD OPN packaging policy (user policy 2026-11-15).

AMD retail OPNs are <root><packaging suffix> (e.g. 100-100000592 + BOX/SBX/WOF/WOZ).
The root identifies the processor; the suffix is packaging/region. A retailer OPN is
accepted when its root equals the root of an OPN published on an amd.com page for the
exact model, the decision documents the packaging implied by the store name (cooler
included or not) and the EAN does not contradict. The canonical keeps the retailer's
exact OPN; it is never rewritten to the official suffix.
"""
import re

ROOT = re.compile(r'^(100-\d{9})([A-Z0-9]{2,5})$')


def verified_amd_opn(candidate, product, decision, normalize):
    claim = decision.get('amd_opn')
    if not isinstance(claim, dict):
        return None
    if candidate.get('category_hint') != 'cpu' or product.get('category_id') != 'cpu':
        raise ValueError('AMD OPN policy only applies to cpu')
    if normalize(candidate.get('brand') or '') != 'amd' or normalize(product.get('brand') or '') != 'amd':
        raise ValueError('AMD OPN policy only applies to AMD')
    retailer = ROOT.match(str(candidate.get('mpn') or '').strip().upper())
    official = ROOT.match(str(claim.get('official_opn') or '').strip().upper())
    if not retailer or not official or retailer.group(1) != official.group(1):
        raise ValueError('AMD OPN roots differ between retailer and official page')
    if normalize(product.get('mpn') or '') != normalize(candidate['mpn']):
        raise ValueError('Canonical AMD product must keep the retailer OPN exactly')
    if not str(claim.get('packaging', '')).strip() or len(str(claim.get('packaging')).strip()) < 10:
        raise ValueError('AMD OPN decision must document the packaging (cooler included or not)')
    note = decision.get('note', '')
    if 'amd.com' not in note.lower() or claim['official_opn'].upper() not in note.upper():
        raise ValueError('AMD OPN decision note must cite the amd.com page and the official OPN')
    return claim
