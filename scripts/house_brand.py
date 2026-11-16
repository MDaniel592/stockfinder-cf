"""House brand identity (user policy 2026-11-15, precedent: Forgeon).

Textorm is a brand of the LDLC group (next.ink 2687; LDLC sells it with Textorm as the
responsible person). For a house brand, the owner's own listing (MPN, EAN, specs on the
LDLC product page) is the primary source. Only LDLC reviews of brand Textorm; the
retailer MPN/EAN must be present and equal the canonical ones; no name-only matches.
"""
HOUSE_BRANDS = {'textorm': ('ldlc',)}


def verified_house_brand(candidate, product, decision, normalize, shop):
    claim = decision.get('house_brand')
    if not isinstance(claim, dict):
        return None
    key = normalize(candidate.get('brand') or '')
    if shop not in HOUSE_BRANDS.get(key, ()) or normalize(product.get('brand') or '') != key:
        raise ValueError('house_brand only applies to Textorm reviews in the LDLC database')
    if candidate.get('category_hint') not in ('psu', 'ram', 'case', 'cooler') or product.get('category_id') != candidate.get('category_hint'):
        raise ValueError('house_brand category mismatch')
    if not candidate.get('mpn') or not candidate.get('ean'):
        raise ValueError('house_brand requires the listing to print both MPN and EAN')
    if normalize(candidate['mpn']) != normalize(product.get('mpn') or ''):
        raise ValueError('house_brand canonical MPN must equal the listing MPN')
    if claim.get('listing_url') != candidate.get('url_public'):
        raise ValueError('house_brand listing_url must be the reviewed LDLC listing')
    note = decision.get('note', '').lower()
    if 'next.ink' not in note and 'ldlc group' not in note:
        raise ValueError('house_brand note must state that Textorm is an LDLC group brand')
    return claim
