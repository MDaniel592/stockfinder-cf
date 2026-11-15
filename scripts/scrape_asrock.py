"""Scrape ASRock official product pages for every category, not only graphics cards.

ASRock protects every HTML page with an Incapsula JS challenge.  ``sitemap.xml`` is public, so it
is used to enumerate the product URLs of each section; each product page is requested with browser
TLS first and, when Incapsula answers, through the reader fallback (``--no-jina`` disables it).
The ``tw.asrock.com`` mirror serves the same English page and is used when ``www`` is blocked.
The product page carries a structured ``SpecItem``/``SpecData`` list plus the commercial model code
in ``#briefModel``.

Sections per category:

    gpu -> Graphics-Card      motherboard -> MB        psu -> Power-Supply
    cooler -> CPU-Coolers      mini-pc -> Nettop        monitor -> Monitors

Usage:
  python3 scripts/scrape_asrock.py --category gpu --only-current
  python3 scripts/scrape_asrock.py --category psu --limit 50
  python3 scripts/scrape_asrock.py --url https://www.asrock.com/Power-Supply/SteelLegend/SL-850G/
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from manufacturer_scrape import (  # noqa: E402
    FetchError, ROOT, add_common_args, category_slug, current_only, fetch, load_catalog, log,
    make_record, match_catalog, normalise_specs, parse_asrock, write_report,
)

BRAND = 'ASRock'
SITEMAP = 'https://www.asrock.com/sitemap.xml'

SECTIONS = {
    'gpu': 'Graphics-Card',
    'motherboard': 'MB',
    'psu': 'Power-Supply',
    'cooler': 'CPU-Coolers',
    'mini-pc': 'Nettop',
    'monitor': 'Monitors',
}
CATEGORIES = tuple(SECTIONS)


def product_urls(category: str, args, verbose: bool = False) -> list[str]:
    """Every product URL of one section, from the public sitemap."""
    section = SECTIONS[category]
    page = fetch(SITEMAP, brand='asrock', use_jina=False, fmt='html', timeout=45, delay=1.0)
    pattern = re.compile(
        r'<loc>\s*(https://(?:www\.)?asrock\.com/' + re.escape(section) + r'/[^<]*?)\s*</loc>', re.I)
    urls = [url.replace(' ', '%20') for url in pattern.findall(page['text'])]
    seen, unique = set(), []
    for url in urls:
        key = url.rstrip('/').casefold()
        if key not in seen:
            seen.add(key)
            unique.append(url)
    if verbose:
        log('sitemap', section=section, urls=len(unique))
    return unique


def mirror_url(url: str) -> str:
    """www.asrock.com is Incapsula-protected; the Taiwan mirror serves the same English page."""
    return re.sub(r'^https?://(?:www\.)?asrock\.com/', 'https://tw.asrock.com/', url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_common_args(parser, CATEGORIES)
    args = parser.parse_args(argv)
    category = args.category
    output = args.output or ROOT / f'work/manufacturer/manufacturer-asrock-{category_slug(category)}.json'

    urls = args.url or product_urls(category, args, args.verbose)
    if args.only_current and category == 'gpu':
        urls = [url for url in urls if current_only(url.replace('%20', ' '))]
    if args.limit:
        urls = urls[:args.limit]
    log('start', brand=BRAND, category=category, urls=len(urls),
        reader_fallback=not args.no_jina)

    catalog = None if args.no_catalog else load_catalog(ROOT)
    records, errors = [], []
    for index, url in enumerate(urls, 1):
        fetch_url = mirror_url(url)
        try:
            page = fetch(fetch_url, brand='asrock', fmt='html', use_jina=not args.no_jina,
                         delay=args.delay, refresh=args.refresh, max_age=args.max_age)
            parsed = parse_asrock(page['text'])
            if not parsed['pairs']:
                raise FetchError('no SpecItem/SpecData pairs found')
            specs = normalise_specs(BRAND, parsed['pairs'], page['text'], category=category)
            notes = ['ASRock HTML: SpecItem/SpecData table; model code from #briefModel.',
                     'Fetched from tw.asrock.com mirror: same English page, www is '
                     'Incapsula-protected.']
            if not parsed['code']:
                notes.append('The page publishes no commercial code, only the marketing name '
                             f"({parsed['model']}). The MPN stays empty.")
            record = make_record(
                BRAND, fetch_url, model=parsed['model'], mpn=parsed['code'], specs=specs,
                raw_specs=parsed['pairs'], transport=page['transport'], status=page['status'],
                notes=notes, category=category,
            )
            if catalog:
                match_catalog(record, catalog)
            records.append(record)
            if args.verbose:
                log('ok', n=index, url=fetch_url, model=record['model'], mpn=record['mpn'],
                    specs=len(specs), transport=page['transport'])
        except Exception as error:
            errors.append({'url': fetch_url, 'error': str(error)[:300]})
            log('error', n=index, url=fetch_url, error=str(error)[:200])

    write_report(output, BRAND, records, errors, 'asrock.com/sitemap.xml', category=category)
    return 0 if records else 1


if __name__ == '__main__':
    raise SystemExit(main())
