"""Utilidades comunes para rellenar specs desde fichas oficiales (solo lectura de red; no escribe en la BD).
Caché en disco, robots.txt, >=1,2 s entre peticiones por dominio, sin saltarse WAF: 403/429 => se anota y se omite."""
import os, re, time, json, html, hashlib, urllib.request, urllib.error, urllib.robotparser, urllib.parse

UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
CACHE = os.environ.get('SPECS_CACHE', '/opt/stockfinder/work/specs-official-cache')
os.makedirs(CACHE, exist_ok=True)
_last = {}
_robots = {}
BLOCKED = {}


def allowed(url):
    p = urllib.parse.urlparse(url)
    base = f'{p.scheme}://{p.netloc}'
    rp = _robots.get(base)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            req = urllib.request.Request(base + '/robots.txt', headers={'User-Agent': UA})
            rp.parse(urllib.request.urlopen(req, timeout=20).read().decode('utf8', 'replace').splitlines())
        except Exception:
            rp.parse([])  # sin robots accesible: se asume permitido
        _robots[base] = rp
    return rp.can_fetch(UA, url) and rp.can_fetch('*', url)


def fetch(url, max_age=86400 * 3):
    """Devuelve (texto|None, estado). estado: 'ok', 'cache', 'robots', 'http NNN', 'error ...'"""
    host = urllib.parse.urlparse(url).netloc
    f = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest() + '.html')
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < max_age:
        return open(f, encoding='utf8').read(), 'cache'
    if BLOCKED.get(host, 0) >= 3:
        return None, 'http 403 (dominio bloqueado, omitido)'
    if not allowed(url):
        return None, 'robots'
    wait = 1.3 - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.time()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml', 'Accept-Language': 'en,es;q=0.8'})
        body = urllib.request.urlopen(req, timeout=40).read().decode('utf8', 'replace')
    except urllib.error.HTTPError as e:
        if e.code in (403, 429, 503):
            BLOCKED[host] = BLOCKED.get(host, 0) + 1
        return None, f'http {e.code}'
    except Exception as e:
        return None, f'error {type(e).__name__}'
    open(f, 'w', encoding='utf8').write(body)
    return body, 'ok'


def text_lines(page):
    t = re.sub(r'<(script|style|noscript).*?</\1>', '', page, flags=re.S | re.I)
    t = re.sub(r'<(br|/p|/div|/li|/tr|/h\d|/td|/th|/dt|/dd)[^>]*>', '\n', t, flags=re.I)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', t))
    return [re.sub(r'\s+', ' ', l).strip() for l in t.split('\n') if l.strip()]
