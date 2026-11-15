"""Scrape ASUS official product pages for every category, not only GPUs.

Two public sources are combined:

* ``www.asus.com/es/motherboards-components/<section>/<product>/techspec/`` - public HTML,
  enumerated from the Spanish sitemap files (``/sitemap.xml`` -> ``/es/sitemap/esN.xml``).  This is
  the identity/spec source: the table is ``rowTableTitle`` / ``rowTableItems`` and the commercial
  code is the "Model" row.  Sections per category:

      gpu        -> graphics-cards        motherboard -> motherboards
      psu        -> power-supply-units    case       -> cases
      cooler     -> cooling               storage    -> data-storage

* ``estore.asus.com/<locale>/<product>.html`` - DataDome protected, read through the reader
  fallback.  It only enriches a techspec record with price, EAN and availability, and the match is
  by exact MPN.  It is enabled by default for the GPU category only, because every store page is
  one more request; use ``--store-categories`` to widen it.

``--mode techspec`` skips the store, ``--mode store`` skips the sitemap and emits only store rows.

Usage:
  python3 scripts/scrape_asus.py --category gpu --only-current
  python3 scripts/scrape_asus.py --category psu --limit 50
  python3 scripts/scrape_asus.py --url https://www.asus.com/es/.../techspec/
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manufacturer_scrape import (  # noqa: E402
    FetchError, ROOT, add_common_args, category_slug, current_only, detect_stock, fetch,
    load_catalog, log, make_record, match_catalog, normalise_specs, norm_mpn, parse_asus_html,
    parse_asus_store_markdown, parse_price_cents, write_report,
)

BRAND = 'ASUS'
SITEMAP_INDEX = 'https://www.asus.com/sitemap.xml'
ES_SITEMAP_RE = re.compile(r'<loc>\s*(https://www\.asus\.com/es/sitemap/es\d+\.xml)\s*</loc>', re.I)
STORE_LINK_RE = re.compile(r'\((https://estore\.asus\.com/([a-z]{2})/[^)]+\.html)\)', re.I)

# category -> section folder under /motherboards-components/
SECTIONS = {
    'gpu': 'graphics-cards',
    'motherboard': 'motherboards',
    'psu': 'power-supply-units',
    'case': 'cases',
    'cooler': 'cooling',
    'storage': 'data-storage',
}
CATEGORIES = tuple(SECTIONS)
TECHSPEC_RE = re.compile(
    r'<loc>\s*(https://www\.asus\.com/es/motherboards-components/[^<\s]+/techspec/)\s*</loc>', re.I)


def section_of(url: str) -> str:
    """The folder under /motherboards-components/ that a product URL belongs to."""
    parts = url.split('/')
    if 'motherboards-components' in parts:
        index = parts.index('motherboards-components')
        if index + 1 < len(parts):
            return parts[index + 1]
    return ''


def techspec_urls(category: str, args, verbose: bool = False) -> list[str]:
    """Every Spanish techspec page of one category, from the public sitemaps."""
    section = SECTIONS[category]
    index = fetch(SITEMAP_INDEX, brand='asus', use_jina=False, delay=args.delay,
                  refresh=args.refresh, max_age=args.max_age)
    sitemaps = list(dict.fromkeys(ES_SITEMAP_RE.findall(index['text'])))
    log('sitemap-index', sitemaps=len(sitemaps))
    urls: dict[str, str] = {}
    for sitemap in sitemaps:
        try:
            page = fetch(sitemap, brand='asus', use_jina=False, delay=args.delay,
                         refresh=args.refresh, max_age=args.max_age)
        except FetchError as error:
            log('sitemap-error', url=sitemap, error=str(error)[:200])
            continue
        for url in TECHSPEC_RE.findall(page['text']):
            if section_of(url).casefold() != section:
                continue
            urls.setdefault(url.casefold(), url)
    log('sitemap', category=category, section=section, techspec=len(urls))
    return list(urls.values())


def store_urls(locales: list[str], args) -> list[str]:
    found: dict[str, str] = {}
    for locale in locales:
        sitemap = f'https://estore.asus.com/{locale}/sitemap.xml'
        try:
            page = fetch(sitemap, brand='asus', fmt='markdown', use_jina=not args.no_jina,
                         direct=False, delay=args.delay, refresh=args.refresh, max_age=args.max_age)
        except FetchError as error:
            log('store-sitemap-error', url=sitemap, error=str(error)[:200])
            continue
        for url, loc in STORE_LINK_RE.findall(page['text']):
            if loc.casefold() == locale:
                found.setdefault(url.casefold(), url)
        log('store-sitemap', locale=locale, product_links=len(found))
    return list(found.values())


def store_index(locales: list[str], args) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for url in store_urls(locales, args):
        try:
            page = fetch(url, brand='asus', fmt='markdown', use_jina=not args.no_jina,
                         direct=False, delay=args.delay, refresh=args.refresh, max_age=args.max_age)
            parsed = parse_asus_store_markdown(page['text'])
            mpn = parsed['code']
            if not mpn:
                raise FetchError('store page has no part number')
            key = norm_mpn(mpn)
            if key in index:
                continue
            index[key] = {
                'url': url, 'mpn': mpn, 'ean': parsed['pairs'].get('EAN'),
                'price_cents': parse_price_cents(parsed['pairs'].get('Price', '')),
                'in_stock': detect_stock(page['text']), 'transport': page['transport'],
                'model': parsed['pairs'].get('Model') or parsed['model'],
                'pairs': parsed['pairs'],
            }
            log('store-product', mpn=mpn, price=index[key]['price_cents'],
                in_stock=index[key]['in_stock'], url=url)
        except Exception as error:
            log('store-error', url=url, error=str(error)[:200])
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, CATEGORIES)
    parser.add_argument('--mode', choices=('all', 'techspec', 'store'), default='all')
    parser.add_argument('--store-locales', default='es,fr,it',
                        help='Comma-separated eShop locales (default: es,fr,it)')
    parser.add_argument('--store-categories', default='gpu',
                        help='Comma-separated categories enriched from the eShop (default: gpu)')
    parser.add_argument('--no-store', action='store_true', help='Do not enrich with eShop price/stock')
    args = parser.parse_args(argv)
    category = args.category
    locales = [loc.strip().lower() for loc in args.store_locales.split(',') if loc.strip()]
    store_categories = [c.strip() for c in args.store_categories.split(',') if c.strip()]

    output = args.output or ROOT / f'work/manufacturer/manufacturer-asus-{category_slug(category)}.json'

    urls: list[str] = []
    if args.mode != 'store':
        urls = args.url or techspec_urls(category, args, args.verbose)
        if args.only_current and category == 'gpu':
            urls = [url for url in urls if current_only(url.replace('-', ' '))]
        if args.limit:
            urls = urls[:args.limit]
    log('start', brand=BRAND, category=category, mode=args.mode, techspec=len(urls),
        store_locales=locales)

    catalog = None if args.no_catalog else load_catalog(ROOT)
    records, errors = [], []

    for index, url in enumerate(urls, 1):
        try:
            page = fetch(url, brand='asus', fmt='html', use_jina=not args.no_jina,
                         delay=args.delay, refresh=args.refresh, max_age=args.max_age)
            parsed = parse_asus_html(page['text'])
            if not parsed['pairs']:
                raise FetchError('no rowTableTitle/rowTableItems specification pairs found')
            specs = normalise_specs(BRAND, parsed['pairs'], page['text'], category=category)
            notes = ['ASUS techspec HTML: rowTableTitle/rowTableItems table; MPN from "Model".']
            if not parsed['code']:
                notes.append('The page publishes no "Model" row: no manufacturer code, only the '
                             'marketing name in the URL.')
            record = make_record(
                BRAND, url, model=parsed['model'], mpn=parsed['code'], specs=specs,
                raw_specs=parsed['pairs'], transport=page['transport'], status=page['status'],
                notes=notes, category=category,
            )
            if catalog:
                match_catalog(record, catalog)
            records.append(record)
            if args.verbose:
                log('ok', n=index, url=url, model=record['model'], mpn=record['mpn'],
                    specs=len(specs), transport=page['transport'])
        except Exception as error:
            errors.append({'url': url, 'error': str(error)[:300]})
            log('error', n=index, url=url, error=str(error)[:200])

    if args.mode != 'techspec' and not args.no_store and category in store_categories:
        index = store_index(locales, args)
        by_mpn = {norm_mpn(record['mpn']): record for record in records if record['mpn']}
        for key, store in index.items():
            record = by_mpn.get(key)
            if record is None:
                specs = normalise_specs(BRAND, store['pairs'], '', category=category)
                record = make_record(
                    BRAND, store['url'], model=store['model'], mpn=store['mpn'],
                    specs=specs, raw_specs=store['pairs'], transport=store['transport'],
                    status=200, price_cents=store['price_cents'], currency='EUR',
                    in_stock=store['in_stock'], ean=store['ean'], category=category,
                    notes=['ASUS eShop only: no matching www.asus.com techspec page.'],
                )
                if catalog:
                    match_catalog(record, catalog)
                records.append(record)
                continue
            record['price_cents'] = store['price_cents']
            record['currency'] = 'EUR'
            record['in_stock'] = store['in_stock']
            record['ean'] = store['ean']
            record['notes'].append(
                f'eShop {store["url"]} ({store["transport"]}); matched by exact MPN.')

    write_report(output, BRAND, records, errors, 'asus.com/es sitemap + estore.asus.com',
                 category=category)
    return 0 if records else 1


if __name__ == '__main__':
    raise SystemExit(main())
