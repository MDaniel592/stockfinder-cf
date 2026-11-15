"""Fetch one public manufacturer page at a time (curl_cffi browser TLS, like the PcComponentes scripts).
Usage: fetch_official.py URL [--grep REGEX]. Prints status and text (or matching lines). Stops on a challenge page."""
import os, re, sys, time
# The host has no pip: dependencies copied from the NAS image live in a temp dir when present.
_DEPS = os.environ.get('STOCKFINDER_PYDEPS', '/tmp/stockfinder-pydeps')
if os.path.isdir(_DEPS) and _DEPS not in sys.path: sys.path.append(_DEPS)
from curl_cffi import requests

def main():
    url = sys.argv[1]; pattern = sys.argv[sys.argv.index('--grep')+1] if '--grep' in sys.argv else None
    r = requests.Session(impersonate='chrome').get(url, timeout=30)
    head = r.text[:5000].lower()
    if any(k in head for k in ('incapsula', 'captcha', 'just a moment')) and len(r.text) < 5000:
        print(f'CHALLENGE {r.status_code} {url}: leave pending'); return 2
    print(f'STATUS {r.status_code} {len(r.text)} bytes {url}')
    text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', r.text, flags=re.S)
    text = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '\n', text))
    if pattern:
        for m in re.finditer(r'.{0,80}(?:%s).{0,120}' % pattern, r.text if pattern.startswith('RAW:') else text, flags=re.I): print(m.group(0))
    else: print(text[:6000])
    time.sleep(2)
if __name__ == '__main__': sys.exit(main())
