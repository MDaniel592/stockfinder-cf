"""Discover PcComponentes candidates by public category inventory.

Two stages, both optional and independently runnable:

1. Inventory: GET /api/articles/category/{uuid} pages of 40 articles. Each row
   already carries SKU, MPN, brand, name, price, image, seller and normalized
   top attributes. New rows are written in the same shape as
   data/retailers/pccomponentes-candidates.json.
2. --details: one product page per candidate to add EAN, real stock, description
   filters and full images. This is the expensive stage; cap it with --limit.

Candidates are never published. Existing SKUs/MPNs (retailer candidates, pilot
and canonical catalog) are excluded by default so repeated runs only surface
new references.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests

from pccomponentes_batch import (BASE, CODE_NAMES, IN_STOCK_CODES, log, now_utc)
from pccomponentes_rules import PERIPHERALS, classify

ROOT = Path(__file__).resolve().parents[1]
CATEGORY_API = BASE + '/api/articles/category/'
REGISTRY = ROOT / 'data/retailers/pccomponentes-categories.json'
EXCLUDE_NAMES = re.compile(r'^(Pack |Portátil |Mini PC |PC Sobremesa |Reacondicionado )', re.I)


def normalize(value):
    return ' '.join(str(value or '').casefold().split())


def hydration(html):
    marker = 'window.__staticRouterHydrationData = JSON.parse("'
    start = html.find(marker)
    if start < 0:
        raise ValueError('Hydration data absent')
    start += len(marker)
    end = html.find('</script>', start)
    if end < 0:
        raise ValueError('Hydration data unterminated')
    return json.loads(json.loads('"' + html[start:end][:-3] + '"'))


def load_registry(path):
    if not path.is_file():
        return []
    return json.loads(path.read_text())


def save_registry(path, registry):
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + '\n')


def resolve_category(slug, session, registry):
    found = next((c for c in registry if c['slug'] == slug), None)
    if found:
        return found
    response = session.get(BASE + '/' + slug, timeout=60,
                           headers={'Accept': 'text/html,application/xhtml+xml'})
    if response.status_code != 200:
        raise ValueError(f'Category page {slug!r} returned HTTP {response.status_code}')
    category = hydration(response.text)['loaderData']['categoryPage']['productList']['pageContext']['category']
    found = {'slug': category['slug'], 'name': category['name'], 'uuid': category['id']}
    registry.append(found)
    return found


def fetch_page(session, uuid, page, sort='activation_date', page_size=40, attempts=3):
    delay = 2
    for attempt in range(1, attempts + 1):
        response = session.get(CATEGORY_API + uuid, timeout=40,
                               params={'sort': sort, 'page': page, 'pageSize': page_size},
                               headers={'Accept': 'application/json', 'x-selected-language': 'es-ES'})
        if response.status_code == 200:
            return response.json()
        if response.status_code in (403, 429, 500, 502, 503, 504) and attempt < attempts:
            log('retry', status=response.status_code, attempt=attempt)
            time.sleep(delay)
            delay *= 2
            continue
        if 'Just a moment' in response.text:
            raise RuntimeError('Cloudflare challenge returned; refresh curl_cffi/impersonate')
        raise RuntimeError(f'Category API returned HTTP {response.status_code}')
    raise RuntimeError('Category API exhausted retries')


def known_references(paths):
    skus, mpns = set(), set()
    for path in paths:
        if not path.is_file():
            continue
        data = json.loads(path.read_text())
        for item in data:
            if not isinstance(item, dict) or item.get('shop_id') != 'pccomponentes':
                continue
            if item.get('retailer_sku'):
                skus.add(str(item['retailer_sku']))
            if item.get('mpn'):
                mpns.add(normalize(item['mpn']))
    # Canonical catalog MPNs are deliberately NOT excluded: a product that
    # already exists (e.g. via Coolmod) still needs its PcComponentes listing
    # staged so the publisher can auto-approve it by exact MPN. Dedup against
    # the review queue is by (shop, retailer_sku) in stage.
    return skus, mpns


def specs_from(article):
    return [{'name': value.get('name'), 'value': value.get('value')}
            for value in article.get('topAttributes') or [] if value.get('value')]


def make_candidate(article, category, now):
    price = article.get('price')
    return {
        'shop_id': 'pccomponentes',
        'retailer_sku': str(article['id']),
        'url_public': BASE + '/' + str(article['slug']),
        'name': str(article.get('name') or ''),
        'brand': article.get('brandName'),
        'mpn': article.get('mpn'),
        'ean': None,
        'category_hint': category['slug'],
        'price_cents': round(float(price) * 100),
        'in_stock': None,
        'last_checked_at': now,
        'missing_fields': ['ean', 'in_stock', 'verified_specifications', 'manufacturer_url'],
        'specs': specs_from(article),
        'raw_properties': [
            {'key': 'discovery_method', 'value': 'public_category_api'},
            {'key': 'store_category', 'value': category['name']},
            {'key': 'seller', 'value': article.get('sellerName')},
            {'key': 'direct_seller', 'value': bool(article.get('isPCC'))},
            {'key': 'availability_code', 'value': (article.get('delivery') or {}).get('availabilityCode')},
            {'key': 'image_source_url', 'value': (article.get('images') or {}).get('large', {}).get('path')},
            {'key': 'offer_id', 'value': article.get('offerId')},
        ],
    }


def details(session, candidate):
    url = candidate['url_public']
    response = session.get(url, timeout=60,
                           headers={'Accept': 'text/html,application/xhtml+xml'})
    if response.status_code != 200:
        raise ValueError(f'Product page HTTP {response.status_code}')
    data = hydration(response.text)['loaderData']['dynamicView']['dynamicData']
    if str(data.get('productId')) != candidate['retailer_sku']:
        raise ValueError('Product page belongs to a different SKU')
    if normalize(data.get('reference')) != normalize(candidate['mpn']):
        raise ValueError('MPN changed on product page')
    code = data.get('availabilityCode')
    offer = (data.get('offers') or [{}])[0]
    real_stock = (offer.get('availability') or {}).get('realStock')
    main_image = (data.get('mainImage') or {}).get('large')
    if main_image and main_image.startswith('//'):
        main_image = 'https:' + main_image
    gallery = ['https:' + image['large'] if (image.get('large') or '').startswith('//') else image.get('large')
               for image in (data.get('mediaItems') or []) if image.get('large')]
    candidate['ean'] = str(data.get('ean') or '') or None
    candidate['in_stock'] = bool(data.get('purchasable')) and code in IN_STOCK_CODES
    candidate['specs'] = [{'name': group.get('name'), 'value': item.get('name')}
                          for group in data.get('filters') or []
                          for item in group.get('filters') or []]
    candidate['raw_properties'] = [item for item in candidate['raw_properties']
                                   if item['key'] not in ('availability_code', 'real_stock', 'image_source_url')]
    candidate['raw_properties'] += [
        {'key': 'availability_code', 'value': code},
        {'key': 'availability', 'value': CODE_NAMES.get(code, 'UNKNOWN')},
        {'key': 'real_stock', 'value': real_stock},
        {'key': 'image_source_url', 'value': main_image},
        {'key': 'gallery', 'value': gallery[:12]},
    ]
    candidate['missing_fields'] = [field for field in candidate['missing_fields']
                                   if field not in ('ean', 'in_stock')]
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--categories', nargs='+', required=True,
                        help='Category slugs (e.g. tarjetas-graficas) or UUIDs')
    parser.add_argument('--pages', type=int, default=1, help='Pages of 40 per category')
    parser.add_argument('--page-size', type=int, default=40, choices=range(1, 41))
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--details', action='store_true', help='Fetch every product page')
    parser.add_argument('--details-limit', type=int, default=25,
                        help='Maximum product pages fetched (default 25)')
    parser.add_argument('--delay', type=float, default=1.1, help='Seconds between pages')
    parser.add_argument('--sort', default='activation_date',
                        help='Store sort key (default activation_date: newest listings first)')
    parser.add_argument('--all-generations', action='store_true',
                        help='Disable generation rules and accept every matched component')
    parser.add_argument('--include-peripherals', action='store_true',
                        help='Keep monitors, keyboards and mice (excluded by default)')
    parser.add_argument('--include-marketplace', action='store_true')
    parser.add_argument('--include-known', action='store_true',
                        help='Do not exclude SKUs/MPNs already in candidates or catalog')
    parser.add_argument('--keep-all-names', action='store_true',
                        help='Keep packs, laptops and refurbished rows')
    parser.add_argument('--max-candidates', type=int, default=190,
                        help='Output limit (review queue accepts 200)')
    args = parser.parse_args()

    registry = load_registry(REGISTRY)
    session = requests.Session(impersonate='chrome')
    known_skus, known_mpns = known_references(
        [ROOT / 'data/retailers/pccomponentes-candidates.json',
         ROOT / 'data/retailers/pilot.json',
         ROOT / 'data/retailers/approved-coolmod-targets.json'])

    candidates, seen_skus, seen_mpns = [], set(), set()
    stats, excluded, examples = {}, {}, {}
    for requested in args.categories:
        if re.fullmatch(r'[0-9a-f-]{36}', requested):
            category = next((c for c in registry if c['uuid'] == requested),
                            {'slug': requested, 'name': requested, 'uuid': requested})
        else:
            category = resolve_category(requested, session, registry)
        accepted = scanned = known = 0
        category_excluded = {}
        for page in range(1, args.pages + 1):
            body = fetch_page(session, category['uuid'], page, args.sort, args.page_size)
            articles = body.get('articles') or []
            for article in articles:
                scanned += 1
                mpn = normalize(article.get('mpn'))
                sku = str(article.get('id'))
                if not mpn:
                    continue
                if not args.include_marketplace and not article.get('isPCC'):
                    continue
                if article.get('isRefurbished') and not args.keep_all_names:
                    continue
                if EXCLUDE_NAMES.match(article.get('name') or '') and not args.keep_all_names:
                    continue
                price = article.get('price')
                if not isinstance(price, (int, float)) or isinstance(price, bool):
                    continue

                verdict = None
                if not args.all_generations:
                    verdict = classify(category['slug'], article.get('name'), specs_from(article))
                    if category['slug'] in PERIPHERALS and args.include_peripherals:
                        verdict = {'modern': True, 'generation': None,
                                   'reason': 'peripheral-included'}
                    if not verdict['modern']:
                        reason = verdict['reason']
                        category_excluded[reason] = category_excluded.get(reason, 0) + 1
                        excluded[reason] = excluded.get(reason, 0) + 1
                        bucket = examples.setdefault(reason, [])
                        if len(bucket) < 3:
                            bucket.append(article.get('name'))
                        continue

                if not args.include_known and (sku in known_skus or mpn in known_mpns):
                    known += 1
                    continue
                if sku in seen_skus or mpn in seen_mpns:
                    continue
                seen_skus.add(sku)
                seen_mpns.add(mpn)
                candidate = make_candidate(article, category, now_utc())
                if verdict:
                    candidate['raw_properties'] += [
                        {'key': 'generation', 'value': verdict['generation']},
                        {'key': 'generation_rule', 'value': verdict['reason']},
                    ]
                candidates.append(candidate)
                accepted += 1
                if len(candidates) >= args.max_candidates:
                    break
            log('category_page', category=category['slug'], page=page,
                scanned=len(articles), total=body.get('total'), sort=args.sort)
            if len(candidates) >= args.max_candidates or page >= (body.get('totalPages') or 0):
                break
            time.sleep(args.delay)
        stats[category['slug']] = {'scanned': scanned, 'new': accepted,
                                   'known': known, 'excluded': category_excluded}
    save_registry(REGISTRY, registry)

    if args.details:
        enriched = 0
        for candidate in candidates:
            if enriched >= args.details_limit:
                break
            try:
                details(session, candidate)
                enriched += 1
            except (ValueError, RuntimeError, KeyError) as error:
                log('details_failed', sku=candidate['retailer_sku'], detail=str(error))
            time.sleep(args.delay)
        log('details', enriched=enriched, requested=min(len(candidates), args.details_limit))

    report = {
        'generated_at': now_utc(),
        'sort': args.sort,
        'generation_filter': not args.all_generations,
        'include_peripherals': args.include_peripherals,
        'candidates': len(candidates),
        'categories': stats,
        'excluded_reasons': excluded,
        'excluded_examples': examples,
        'output': str(args.output),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + '\n')
    report_path = args.output.with_suffix('.report.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    log('summary', candidates=len(candidates), excluded=excluded,
        categories=stats, output=str(args.output), report=str(report_path))


if __name__ == '__main__':
    main()
