"""Pull new user reports from the signed private endpoint and append them to a JSONL file."""
import hashlib
import hmac
import json
import os
import time
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlparse

def pull(endpoint, secret, allow_local=False):
    parsed = urlparse(endpoint)
    local = parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', 'edge')
    if parsed.scheme != 'https' and not (allow_local and local):
        raise ValueError('HTTPS required except explicit local development')
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip('/') != '/internal/reports/pull':
        raise ValueError('Expected a direct /internal/reports/pull endpoint without credentials or query')
    if len(secret) < 32: raise ValueError('SYNC_SECRET must contain at least 32 characters')
    body = b'{}'
    timestamp, nonce = str(int(time.time())), uuid.uuid4().hex
    signature = hmac.new(secret.encode(), timestamp.encode()+b'.'+nonce.encode()+b'.'+body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(endpoint, data=body, headers={'Content-Type': 'application/json',
        'User-Agent': 'StockFinderSync/2.0 (+https://github.com/StockFinder)',
        'X-Sync-Timestamp': timestamp, 'X-Sync-Nonce': nonce, 'X-Sync-Signature': signature}, method='POST')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    with urllib.request.build_opener(NoRedirect).open(req, timeout=30) as response:
        return json.load(response)['data']

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=Path('artifacts/user-reports.jsonl'))
    parser.add_argument('--allow-local', action='store_true')
    args = parser.parse_args()
    base = os.environ.get('REPORTS_ENDPOINT') or os.environ['SYNC_ENDPOINT'].replace('/internal/sync', '/internal/reports/pull')
    rows = pull(base, os.environ['SYNC_SECRET'], args.allow_local)
    if rows:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open('a', encoding='utf-8') as f:
            for row in rows: f.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(f'{len(rows)} reportes nuevos -> {args.out}')
    for row in rows:
        print(f"#{row['id']} {row['product_slug']}{' / ' + row['offer_id'] if row.get('offer_id') else ''}: {row['note'][:120]}")

if __name__ == '__main__': main()
