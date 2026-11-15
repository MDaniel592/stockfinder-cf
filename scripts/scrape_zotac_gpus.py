"""Scrape ZOTAC graphics-card specification pages.

ZOTAC answers direct requests with the SafeLine WAF (HTTP 468 plus a JS challenge), so the
category and product pages are read through the reader fallback by default.  ``--no-jina``
keeps the scraper honest if the WAF policy changes.

Enumeration starts from the public series pages (``/us/product/graphics_card/<series>/all``);
every product slug is then read individually because the listing only carries name + SKU.

Usage:
  python3 scripts/scrape_zotac_gpus.py [--only-current] [--limit N] [--refresh]
  python3 scripts/scrape_zotac_gpus.py --url https://www.zotac.com/us/product/graphics_card/...
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manufacturer_scrape import (  # noqa: E402
    FetchError, ROOT, add_common_args, current_only, fetch, load_catalog, log, make_record,
    match_catalog, normalise_specs, parse_zotac, write_report,
)

BRAND = 'ZOTAC'
BASE = 'https://www.zotac.com/us/product/graphics_card'
SERIES = (
    'GeForce-RTX-5090', 'GeForce-RTX-5080', 'GeForce-RTX-5070-Ti', 'GeForce-RTX-5070',
    'GeForce-RTX-5060-Ti', 'GeForce-RTX-5060', 'GeForce-RTX-5050', 'all',
)
SLUG_RE = re.compile(
    r'href="(?:https?://www\.zotac\.com)?/us/product/graphics_card/([a-z0-9][a-z0-9-]*)"',
    re.I,
)
SERIES_NAMES = {name.casefold() for name in SERIES}


def product_urls(args) -> list[str]:
    """Collect unique product slugs from the series listing pages."""
    found: dict[str, str] = {}
    for series in SERIES:
        if args.limit and len(found) >= args.limit:
            break
        url = f'{BASE}/{series}/all'
        try:
            page = fetch(url, brand='zotac', fmt='html', use_jina=not args.no_jina,
                         delay=args.delay, refresh=args.refresh, max_age=args.max_age)
        except FetchError as error:
            log('series-error', url=url, error=str(error)[:200])
            continue
        slugs = [slug for slug in SLUG_RE.findall(page['text']) if slug.casefold() not in SERIES_NAMES]
        log('series', series=series, slugs=len(set(slugs)), transport=page['transport'])
        for slug in slugs:
            found.setdefault(slug.casefold(), f'{BASE}/{slug}')
    urls = list(found.values())
    if args.only_current:
        urls = [url for url in urls if current_only(url.replace('-', ' '))]
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    output = args.output or ROOT / 'artifacts/manufacturer-zotac-gpus.json'

    urls = args.url or product_urls(args)
    if args.only_current and args.url:
        urls = [url for url in urls if current_only(url.replace('-', ' '))]
    if args.limit:
        urls = urls[:args.limit]
    log('start', brand=BRAND, urls=len(urls), reader_fallback=not args.no_jina)

    catalog = None if args.no_catalog else load_catalog(ROOT)
    records, errors = [], []
    for index, url in enumerate(urls, 1):
        try:
            page = fetch(url, brand='zotac', fmt='html', use_jina=not args.no_jina,
                         delay=args.delay, refresh=args.refresh, max_age=args.max_age)
            parsed = parse_zotac(page['text'])
            if not parsed['pairs']:
                raise FetchError('no col-left/col-right specification pairs found')
            specs = normalise_specs(BRAND, parsed['pairs'], page['text'])
            record = make_record(
                BRAND, url, model=parsed['model'], mpn=parsed['code'], specs=specs,
                raw_specs=parsed['pairs'], transport=page['transport'], status=page['status'],
                notes=['ZOTAC HTML: col-left/col-right table; MPN from "Available SKU".'],
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

    write_report(output, BRAND, records, errors, 'zotac.com product series pages')
    return 0 if records else 1


if __name__ == '__main__':
    raise SystemExit(main())
