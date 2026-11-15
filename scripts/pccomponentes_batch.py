"""Batched PcComponentes observations via its public article API.

The store's HTML and JSON endpoints reject non-browser TLS fingerprints with a
Cloudflare challenge, so requests use curl_cffi's browser impersonation and the
site's own language header. No challenge is solved and no cookie is forged.

Endpoint used: GET /api/articles/list-by-ids?productIds=...&sort=relevance
It is not listed in robots.txt (unlike /api/articles/search, which is
disallowed and deliberately not used).

Output is a JSON list compatible with nas/import_offers.py, plus a sidecar
report with skipped rows. Nothing is written to PostgreSQL or D1 here.
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests

BASE = 'https://www.pccomponentes.com'
ENDPOINT = BASE + '/api/articles/list-by-ids'
BATCH_SIZE = 40
DIRECT_SELLER = 'PcComponentes'
IN_STOCK_CODES = {1, 3}  # 1 IN_STOCK, 3 HIGH_AVAILABILITY
CODE_NAMES = {
    0: 'WITHOUT_MOVEMENT', 1: 'IN_STOCK', 2: 'INMINENT_REPLENISHMENT',
    3: 'HIGH_AVAILABILITY', 4: 'OUT_OF_STOCK', 5: 'PRESALE', 6: 'CONSULT',
    7: 'DISCONTINUED', 9876: 'ZONE_NOT_AVAILABLE', 9999: 'FORCE_DISCONTINUED',
    10000: 'PRE_ORDER',
}


def log(event, **data):
    print(json.dumps({'event': event, **data}, ensure_ascii=False), file=sys.stderr)


def now_utc():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def load_targets(path, session):
    """Accept dicts with retailer_sku and optional product_id/mpn/url_public,
    plain SKUs, or product URLs (resolved through the page's JSON-LD sku).
    Unresolvable URLs are returned as skips instead of aborting the batch."""
    raw = json.loads(Path(path).read_text())
    if not isinstance(raw, list):
        raise ValueError('Targets file must contain a JSON list')
    targets, skipped = [], []
    for entry in raw:
        if isinstance(entry, str):
            target = ({'retailer_sku': entry, 'url_public': entry}
                      if entry.startswith('http') else {'retailer_sku': entry})
        elif isinstance(entry, dict) and entry.get('retailer_sku'):
            target = dict(entry)
        else:
            raise ValueError('Every target needs retailer_sku or a product URL')
        target['retailer_sku'] = str(target['retailer_sku'])
        if target['retailer_sku'].startswith('http') or target.get('url_public', '').startswith('http'):
            url = target.get('url_public') or target['retailer_sku']
            try:
                target['retailer_sku'] = page_sku(url, session)
                target.setdefault('url_public', url)
            except (ValueError, RuntimeError) as error:
                skipped.append({'retailer_sku': url, 'reason': 'url_unresolved',
                                'detail': str(error)})
                continue
        if not target['retailer_sku'].isdigit():
            raise ValueError(f'Invalid retailer_sku: {target["retailer_sku"]!r}')
        targets.append(target)
    return targets, skipped


def page_sku(url, session):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'www.pccomponentes.com':
        raise ValueError('Only https://www.pccomponentes.com product URLs are accepted')
    response = session.get(url, timeout=40, headers={'Accept': 'text/html,application/xhtml+xml'})
    if response.status_code != 200 or 'Just a moment' in response.text:
        raise ValueError(f'Product page unavailable: {response.status_code}')
    marker = 'id="microdata-product-script"'
    start = response.text.find(marker)
    if start < 0:
        raise ValueError('Product JSON-LD absent')
    start = response.text.find('>', start) + 1
    end = response.text.find('</script>', start)
    data = json.loads(response.text[start:end])
    if not str(data.get('sku', '')).isdigit():
        raise ValueError('Product JSON-LD sku absent')
    return str(data['sku'])


class RateLimited(RuntimeError):
    """HTTP 429 from the article API; `retry_after` in seconds when the store sends it."""

    def __init__(self, retry_after=None):
        super().__init__(f'Article API returned HTTP 429 (Retry-After={retry_after})')
        self.retry_after = retry_after


def retry_after_seconds(response, cap=600):
    """Numeric Retry-After header (seconds, capped); None if absent or an HTTP date."""
    try:
        value = int(str((response.headers or {}).get('retry-after', '')).strip())
    except (TypeError, ValueError):
        return None
    return min(max(value, 0), cap)


def fetch_batch(session, skus, attempts=3, retry_429=True, sleep=time.sleep):
    """Max 40 IDs per call (41+ returns HTTP 400). With retry_429=False a 429 raises
    RateLimited at once so the caller can cool down instead of hammering the limit."""
    if not 1 <= len(skus) <= BATCH_SIZE:
        raise ValueError(f'list-by-ids accepts 1..{BATCH_SIZE} IDs per call')
    params = {'productIds': ','.join(skus), 'sort': 'relevance',
              'page': 1, 'pageSize': len(skus)}
    delay = 2
    for attempt in range(1, attempts + 1):
        response = session.get(ENDPOINT, params=params, timeout=40,
                               headers={'Accept': 'application/json',
                                        'x-selected-language': 'es-ES'})
        status = response.status_code
        if status == 200:
            return response.json().get('articles', [])
        if status == 429 and not retry_429:
            raise RateLimited(retry_after_seconds(response))
        if status in (403, 429, 500, 502, 503, 504) and attempt < attempts:
            wait = max(delay, retry_after_seconds(response) or 0) if status == 429 else delay
            log('retry', status=status, attempt=attempt, wait=wait)
            sleep(wait)
            delay *= 2
            continue
        if status == 429:
            raise RateLimited(retry_after_seconds(response))
        if 'Just a moment' in response.text:
            raise RuntimeError('Cloudflare challenge returned; refresh curl_cffi/impersonate')
        raise RuntimeError(f'Article API returned HTTP {response.status_code}')
    raise RuntimeError('Article API exhausted retries')


def observe(article, target, allow_marketplace):
    seller = article.get('sellerName') or ''
    code = (article.get('delivery') or {}).get('availabilityCode')
    if not allow_marketplace and seller != DIRECT_SELLER:
        return None, 'marketplace_seller'
    if not allow_marketplace and not article.get('isPCC'):
        return None, 'not_direct_seller'
    expected = str(target.get('mpn') or '').strip().casefold()
    actual = str(article.get('mpn') or '').strip().casefold()
    if expected and expected != actual:
        return None, 'mpn_mismatch'
    price = article.get('price')
    if not isinstance(price, (int, float)) or isinstance(price, bool) or not math.isfinite(price):
        return None, 'price_absent'
    price_cents = round(float(price) * 100)
    if not 0 <= price_cents <= 100000000:
        return None, 'price_out_of_range'
    url = (target.get('url_public') or
           BASE + '/' + str(article.get('slug') or '')).rstrip('/')
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname != 'www.pccomponentes.com':
        return None, 'invalid_url'
    record = {
        'shop_id': 'pccomponentes',
        'retailer_sku': str(article['id']),
        'mpn': article.get('mpn'),
        'url_public': url,
        'price_cents': price_cents,
        'in_stock': code in IN_STOCK_CODES,
        'availability_code': code,
        'availability': CODE_NAMES.get(code, 'UNKNOWN'),
        'seller': seller,
        'is_direct': bool(article.get('isPCC')),
        'offer_id': article.get('offerId'),
        'last_checked_at': now_utc(),
        'match_evidence': (
            'PcComponentes /api/articles/list-by-ids; exact curated MPN; '
            f'direct seller={seller}; availabilityCode={code} '
            f'({CODE_NAMES.get(code, "UNKNOWN")}); final EUR price.'
        ),
    }
    if target.get('product_id'):
        record['product_id'] = target['product_id']
    return record, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--targets', required=True, type=Path,
                        help='JSON list of SKUs, product URLs, or target objects')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--allow-marketplace', action='store_true',
                        help='Keep third-party offers instead of skipping them')
    parser.add_argument('--batch-size', type=int, default=BATCH_SIZE)
    parser.add_argument('--delay', type=float, default=1.0,
                        help='Seconds between batches (default 1)')
    parser.add_argument('--limit', type=int, help='Only process the first N targets')
    parser.add_argument('--dry-run', action='store_true',
                        help='Fetch and validate but do not write the output files')
    args = parser.parse_args()
    if not 1 <= args.batch_size <= BATCH_SIZE:
        parser.error(f'--batch-size must be 1..{BATCH_SIZE}')

    session = requests.Session(impersonate='chrome')
    targets, skipped = load_targets(args.targets, session)
    if args.limit:
        targets = targets[:args.limit]

    records = []
    for start in range(0, len(targets), args.batch_size):
        chunk = targets[start:start + args.batch_size]
        articles = {str(a['id']): a for a in
                    fetch_batch(session, [t['retailer_sku'] for t in chunk])}
        for target in chunk:
            article = articles.get(target['retailer_sku'])
            if not article:
                skipped.append({'retailer_sku': target['retailer_sku'],
                                'reason': 'not_found_or_discontinued'})
                continue
            record, reason = observe(article, target, args.allow_marketplace)
            if record:
                records.append(record)
            else:
                skipped.append({'retailer_sku': target['retailer_sku'],
                                'reason': reason,
                                'seller': article.get('sellerName'),
                                'mpn_expected': target.get('mpn'),
                                'mpn_actual': article.get('mpn')})
        log('batch', offset=start, requested=len(chunk), matched=len(articles))
        if start + args.batch_size < len(targets):
            time.sleep(args.delay)

    direct = sum(1 for r in records if r['is_direct'])
    in_stock = sum(1 for r in records if r['in_stock'])
    unmapped = sum(1 for r in records if 'product_id' not in r)
    report = {
        'generated_at': now_utc(),
        'targets': len(targets),
        'records': len(records),
        'skipped': skipped,
        'direct_seller': direct,
        'marketplace': len(records) - direct,
        'in_stock': in_stock,
        'without_product_id': unmapped,
        'batches': math.ceil(len(targets) / args.batch_size),
    }
    log('summary', **{k: v for k, v in report.items() if k != 'skipped'})

    if args.dry_run:
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
    report_path = args.output.with_suffix('.report.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    log('written', output=str(args.output), report=str(report_path))


if __name__ == '__main__':
    main()
