"""Scrape PowerColor graphics-card specification pages.

PowerColor's HTML is public, but the TLS chain is broken (missing intermediate certificate), so
requests must use ``verify=False``.  This is a certificate problem, not an anti-bot measure: the
pages carry no challenge and need no reader fallback.

Enumeration comes from ``sitemap.xml`` (product-detail*.htm).  The specification table uses
``div.th`` / ``div.td`` cells, and the exact commercial model code is the "Model Name" row.

Usage:
  python3 scripts/scrape_powercolor_gpus.py [--only-current] [--limit N] [--refresh]
  python3 scripts/scrape_powercolor_gpus.py --url https://www.powercolor.com/product-detail211.htm
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manufacturer_scrape import (  # noqa: E402
    FetchError, ROOT, add_common_args, current_only, fetch, load_catalog, log, make_record,
    match_catalog, normalise_specs, parse_powercolor, write_report,
)

BRAND = 'PowerColor'
SITEMAP = 'https://www.powercolor.com/sitemap.xml'
DETAIL_RE = re.compile(r'<loc>\s*(https://www\.powercolor\.com/product-detail\d+\.htm)\s*</loc>', re.I)


def gpu_urls(args) -> list[str]:
    page = fetch(SITEMAP, brand='powercolor', use_jina=False, verify=False,
                 timeout=45, delay=1.0, refresh=args.refresh, max_age=args.max_age)
    urls = list(dict.fromkeys(DETAIL_RE.findall(page['text'])))
    log('sitemap', urls=len(urls))
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser)
    args = parser.parse_args()
    output = args.output or ROOT / 'artifacts/manufacturer-powercolor-gpus.json'

    urls = args.url or gpu_urls(args)
    if args.limit:
        urls = urls[:args.limit]
    log('start', brand=BRAND, urls=len(urls), reader_fallback=not args.no_jina)

    catalog = None if args.no_catalog else load_catalog(ROOT)
    records, errors = [], []
    for index, url in enumerate(urls, 1):
        try:
            page = fetch(url, brand='powercolor', fmt='html', use_jina=not args.no_jina,
                         verify=False, delay=args.delay, refresh=args.refresh, max_age=args.max_age)
            parsed = parse_powercolor(page['text'])
            if not parsed['pairs']:
                # The sitemap also lists headsets/accessories; those have no GPU spec table.
                if not re.search(r'(Radeon|GeForce|RTX|RX\s*\d)', parsed['model'], re.I):
                    log('skip', url=url, reason='not a graphics card')
                    continue
                raise FetchError('no th/td specification pairs found')
            if not any(key in parsed['pairs'] for key in ('Graphics Engine', 'GPU', 'Model Name')):
                log('skip', url=url, reason='no GPU specification table')
                continue
            if args.only_current and not current_only(parsed['model']):
                continue
            specs = normalise_specs(BRAND, parsed['pairs'], page['text'])
            record = make_record(
                BRAND, url, model=parsed['model'], mpn=parsed['code'], specs=specs,
                raw_specs=parsed['pairs'], transport=page['transport'], status=page['status'],
                notes=['PowerColor HTML: div.th/div.td table; verify=False (broken TLS chain, not a WAF).'],
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

    write_report(output, BRAND, records, errors, 'powercolor.com/sitemap.xml')
    return 0 if records else 1


if __name__ == '__main__':
    raise SystemExit(main())
