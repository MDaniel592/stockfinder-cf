"""Shared helpers for the official manufacturer scrapers (GPUs and the other categories).

The target sites protect themselves differently:

* ASRock   -> Incapsula JS challenge on every HTML page (sitemap.xml is public).
* ZOTAC    -> SafeLine WAF returning HTTP 468 plus a JS challenge.
* PowerColor -> broken TLS chain (missing intermediate certificate); the HTML itself is public.
* ASUS store -> DataDome on estore/shop; www.asus.com and uk.store.asus.com are public.

Transport order is always:

1. direct request with a real browser TLS fingerprint (``curl_cffi`` ``impersonate="chrome"``).
   When ``curl_cffi`` is not installed the module falls back to the standard library with the same
   browser headers; the transport used is recorded on every result, so a page read with the plain
   fallback is never presented as a browser-fingerprint read.
2. for a challenge/403/468 response, an optional reader fallback (r.jina.ai) that renders the page
   server-side.  This is the same idea as curl_cffi but for JS/WAF-protected pages.  It is opt-in
   through ``--no-jina`` and every record says which transport produced it.

Nothing here solves a CAPTCHA, logs in, or forges a session cookie.  A page that still fails is
reported as ``blocked`` and never guessed.

``normalise_specs`` maps the manufacturer's own labels to the canonical Spanish vocabulary used by
the catalogue, per category (``gpu``, ``motherboard``, ``psu``, ``ram``, ``case``, ``cooler``,
``storage``).  Only stdlib + curl_cffi are used, so the module runs on the host and in the NAS
image.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from manufacturer_common import norm_model, norm_mpn, now_utc  # noqa: E402

_DEPS = os.environ.get('STOCKFINDER_PYDEPS', '/tmp/stockfinder-pydeps')
if os.path.isdir(_DEPS) and _DEPS not in sys.path:
    sys.path.append(_DEPS)

try:
    from curl_cffi import requests
    HAVE_CURL_CFFI = True
except ImportError:  # pragma: no cover - the stdlib fallback covers a host without the dependency
    requests = None
    HAVE_CURL_CFFI = False

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE = ROOT / 'work/manufacturer-cache'
JINA_PREFIX = 'https://r.jina.ai/'
CHALLENGE_MARKERS = (
    '_incapsula_resource', 'incapsula incident', 'request unsuccessful',
    'safelinechallenge', 'security detection powered by', 'slg-title',
    'captcha-delivery', 'please enable js and disable any ad blocker',
    'shopify.exit_interstitial', 'passwordcontroller', 'just a moment',
)
BROWSER_HEADERS = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
    'Cache-Control': 'no-cache',
    'Pragma': 'no-cache',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1',
}


class FetchError(RuntimeError):
    """The page could not be retrieved (network error, HTTP error or a challenge page)."""


def log(event: str, **data) -> None:
    print(json.dumps({'event': event, **data}, ensure_ascii=False), flush=True)


def text(value) -> str:
    """HTML fragment -> clean text, keeping <br> as a newline and collapsing runs of spaces."""
    if value is None:
        return ''
    value = re.sub(r'(?i)<br\s*/?>', '\n', str(value))
    value = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', value)
    value = re.sub(r'<[^>]+>', ' ', value)
    value = html_lib.unescape(value)
    lines = [' '.join(line.split()) for line in value.splitlines()]
    lines = [re.sub(r'^[-•·]\s*', '', line) for line in lines]
    return '\n'.join(line for line in lines if line).strip()


def one_line(value) -> str:
    return ' '.join(text(value).split())


def looks_like_code(value) -> bool:
    """A commercial code: no spaces, at least four characters and at least one digit.

    Marketing names ("Low Profile 4GB", "TUF Gaming 1200W Platinum") are not codes.  When the
    page only publishes one of those, the record keeps the name and leaves the MPN empty.
    """
    text_value = str(value or '').strip()
    if not text_value or len(text_value) < 4 or len(text_value) > 40:
        return False
    if re.search(r'\s', text_value):
        return False
    return bool(re.search(r'\d', text_value))


def is_challenge(status: int, body: str, headers=None) -> bool:
    low = (body or '')[:20000].casefold()
    if any(marker in low for marker in CHALLENGE_MARKERS):
        return True
    if headers and any('datadome' in str(k).casefold() for k in headers):
        return True
    # These statuses are used by the WAFs as a challenge, never by a normal product page.
    return status in (468,) or (status in (403, 429, 503) and len(body or '') < 2000)


def _cache_key(url: str, fmt: str, transport: str) -> str:
    return hashlib.sha256(f'{transport}\0{fmt}\0{url}'.encode()).hexdigest()[:24]


def _read_cache(path: Path, max_age: float | None):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if max_age is not None:
        age = time.time() - float(data.get('fetched_ts', 0))
        if age > max_age:
            return None
    return data


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False))
    os.replace(temporary, path)


def _http_get(url: str, *, timeout: int = 45, verify: bool = True, headers: dict | None = None):
    """GET ``url`` and return (status, text, headers, final_url, transport).

    ``curl_cffi`` with a Chrome TLS fingerprint is used when it is installed; otherwise the standard
    library is used with the same browser headers.  The transport is returned so every record says
    how the page was read.  ``verify=False`` only exists for sites with a broken certificate chain
    (PowerColor); it is never a way past a WAF.
    """
    headers = dict(headers or BROWSER_HEADERS)
    if HAVE_CURL_CFFI:
        session = requests.Session(impersonate='chrome')
        response = session.get(url, timeout=timeout, headers=headers, verify=verify)
        return (int(response.status_code), response.text, dict(response.headers), str(response.url),
                'curl_cffi')

    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            body = response.read().decode('utf-8', 'replace')
            return (int(response.status), body, dict(response.headers), str(response.url), 'urllib')
    except urllib.error.HTTPError as error:
        body = error.read().decode('utf-8', 'replace')
        return (int(error.code), body, dict(error.headers or {}), url, 'urllib')


def fetch(url: str, *, brand: str, cache_dir: Path = DEFAULT_CACHE, fmt: str = 'html',
          use_jina: bool = True, verify: bool = True, timeout: int = 45, delay: float = 2.0,
          refresh: bool = False, max_age: float = 86400, direct: bool = True) -> dict:
    """Fetch ``url`` and return {url, final_url, status, text, transport, fetched_at}.

    ``fmt`` is the format requested from the reader fallback: ``html`` or ``markdown``.
    The direct request always asks for HTML; callers parse the returned ``text`` accordingly.
    """
    key = _cache_key(url, fmt, 'jina' if use_jina else 'direct')
    cache_path = Path(cache_dir) / brand.lower() / f'{key}.json'
    if not refresh:
        cached = _read_cache(cache_path, max_age)
        if cached:
            return cached

    direct_error = None
    if direct:
        try:
            status, body, headers, final_url, transport = _http_get(
                url, timeout=timeout, verify=verify, headers=BROWSER_HEADERS)
            if is_challenge(status, body, headers):
                raise FetchError(f'challenge/HTTP {status}')
            if status >= 400:
                raise FetchError(f'HTTP {status}')
            payload = {
                'url': url, 'final_url': final_url, 'status': status,
                'text': body, 'transport': transport, 'fetched_at': now_utc(),
                'fetched_ts': int(time.time()), 'content_type': headers.get('content-type', ''),
            }
            _write_cache(cache_path, payload)
            time.sleep(max(0.0, delay))
            return payload
        except Exception as error:  # network, TLS, challenge: try the reader fallback
            direct_error = error

    if not use_jina:
        raise FetchError(f'direct fetch failed for {url}: {direct_error}')

    reader_url = JINA_PREFIX + url

    def reader_get(requested_format: str) -> str:
        status, body, headers, _, _ = _http_get(
            reader_url, timeout=max(timeout * 2, 60), verify=verify,
            headers={'Accept': 'text/plain', 'X-Return-Format': requested_format,
                     'X-Timeout': '30'})
        if status >= 400:
            raise FetchError(f'reader HTTP {status}')
        low = body[:4000].casefold()
        if 'markdown content:' not in low and 'title:' not in low and '<html' not in low:
            raise FetchError('reader returned an error page')
        if is_challenge(200, body):
            raise FetchError('reader returned a WAF challenge')
        return body

    last_error = None
    for attempt in range(3):
        try:
            body = reader_get(fmt)
            payload = {
                'url': url, 'final_url': url, 'status': 200, 'text': body,
                'transport': f'jina-reader:{fmt}', 'fetched_at': now_utc(),
                'fetched_ts': int(time.time()), 'content_type': 'text/html' if fmt == 'html' else 'text/markdown',
            }
            _write_cache(cache_path, payload)
            time.sleep(max(0.0, delay))
            return payload
        except Exception as error:
            last_error = error
            # A different reader format often warms the cache / bypasses a transient WAF answer.
            if attempt == 0:
                try:
                    reader_get('markdown' if fmt == 'html' else 'html')
                except Exception:
                    pass
            time.sleep(max(3.0, delay))
    raise FetchError(f'reader fallback failed for {url}: {last_error}')


# --------------------------------------------------------------------------- parsers
def _strip_tags(fragment: str) -> str:
    fragment = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', fragment)
    fragment = re.sub(r'<[^>]+>', ' ', fragment)
    return html_lib.unescape(fragment)


def sequential_pairs(body: str, title_class: str, item_class: str) -> dict:
    """Pair the title cells with the value cells of a specification table, in document order.

    Both manufacturers write the table as alternating cells, but some pages leave the closing tag
    of the value cell out (``<div class="SpecData">- BRONZE</li>``).  A regex that expects a
    complete tag pair then drifts and mixes the value of a row with the title of the next one, so
    the cells are walked in order instead and paired one by one.
    """
    marker = re.compile(r'<div[^>]*class="[^"]*(' + title_class + '|' + item_class + r')[^"]*"[^>]*>',
                        re.I)
    cells = [(match.start(), match.end(), match.group(1).lower()) for match in marker.finditer(body or '')]
    pairs: dict[str, str] = {}
    pending = None
    for index, (_, end, kind) in enumerate(cells):
        stop = cells[index + 1][0] if index + 1 < len(cells) else len(body or '')
        segment = (body or '')[end:stop]
        if kind == title_class.lower():
            pending = one_line(_strip_tags(segment)).rstrip(':').strip()
            continue
        value = text(segment)
        value = re.sub(r'^\s*[-–]\s*', '', value)   # ASRock writes "- value"
        if '</li>' in segment:
            # The row ends at its </li>; without it the last row of the page would collect the
            # text of the support section that follows the table.
            value = text(segment.split('</li>', 1)[0])
        if pending and value:
            pairs.setdefault(pending, value)
        pending = None
    return pairs


def pairs_from_html(body: str, item_pattern: str, value_pattern: str) -> dict:
    """Extract ordered key/value pairs from a repeated two-cell HTML pattern."""
    found = {}
    pattern = re.compile(item_pattern + r'\s*' + value_pattern, re.S | re.I)
    for match in pattern.finditer(body or ''):
        key = one_line(match.group(1)).rstrip(':').strip()
        value = text(match.group(2))
        if key and value and key not in found:
            found[key] = value
    return found


def parse_asrock(body: str) -> dict:
    pairs = sequential_pairs(body, 'SpecItem', 'SpecData')
    model_html = re.search(r'id="briefModel"[^>]*>(.*?)</div>', body or '', re.S | re.I)
    model, code = '', ''
    if model_html:
        lines = [one_line(part) for part in re.split(r'(?i)<br\s*/?>', model_html.group(1))]
        lines = [line for line in lines if line]
        if lines:
            model = lines[0]
            # With two lines the second one is the commercial code (GPU pages); with one line the
            # page only publishes the model, which for this brand is the code itself.
            candidate = lines[1] if len(lines) > 1 else lines[0]
            code = candidate if looks_like_code(candidate) else ''
    if not model:
        title = re.search(r'<title>(.*?)</title>', body or '', re.S | re.I)
        model = one_line(title.group(1)) if title else ''
        model = re.sub(r'^\s*ASRock\s*>\s*', '', model, flags=re.I)
    # The #briefDesc block states the architecture explicitly ("AMD RDNA™ 4 Architecture").
    brief = re.search(r'id="briefDesc"(.*?)</ul>', body or '', re.S | re.I)
    if brief:
        arch = re.search(r'\b(?:AMD\s+)?RDNA[™\s]*(\d)\b', text(brief.group(1)), re.I)
        if arch:
            pairs.setdefault('Architecture', f'RDNA {arch.group(1)}')
    return {'pairs': pairs, 'model': model, 'code': code}


def parse_zotac(body: str) -> dict:
    pairs = pairs_from_html(
        body,
        r'<div[^>]*class="[^"]*col-left[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*col-right[^"]*"[^>]*>(.*?)</div>',
    )
    name = re.search(r'class="pdt-Name"[^>]*>(.*?)</div>', body or '', re.S)
    serial = re.search(r'class="pdt-serial"[^>]*>(.*?)</div>', body or '', re.S)
    title = re.search(r'<title>(.*?)</title>', body or '', re.S | re.I)
    model = one_line(name.group(1)) if name else (one_line(title.group(1)) if title else '')
    model = re.sub(r'\s*\|\s*ZOTAC.*$', '', model, flags=re.I)
    code = one_line(serial.group(1)) if serial else pairs.get('Available SKU', '')
    return {'pairs': pairs, 'model': model, 'code': code}


def parse_powercolor(body: str) -> dict:
    pairs = pairs_from_html(
        body,
        r'<div[^>]*class="[^"]*th[^"]*"[^>]*>(.*?)</div>',
        r'<div[^>]*class="[^"]*td[^"]*"[^>]*>(.*?)</div>',
    )
    title = re.search(r'<title>(.*?)</title>', body or '', re.S | re.I)
    model = one_line(title.group(1)) if title else ''
    model = re.sub(r'∣.*$', '', model).strip()
    return {'pairs': pairs, 'model': model, 'code': pairs.get('Model Name', '')}


def parse_asus_html(body: str) -> dict:
    pairs = sequential_pairs(body, 'rowTableTitle', 'rowTableItems')
    title = re.search(r'<title>(.*?)</title>', body or '', re.S | re.I)
    model = one_line(title.group(1)) if title else ''
    model = re.sub(r'\s*\|.*$', '', model).strip()
    candidate = pairs.get('Model', '')
    code = candidate if looks_like_code(candidate) else ''
    return {'pairs': pairs, 'model': model, 'code': code}


def parse_asus_store_markdown(body: str) -> dict:
    """Parse the reader markdown of an ASUS eShop product page (``### Key`` blocks)."""
    pairs = {}
    for match in re.finditer(r'(?m)^###\s+(.+?)\s*$\n+([^\n#].*?)(?=\n\s*\n###|\n\s*\n##|\Z)', body or '', re.S):
        key = one_line(match.group(1)).rstrip(':').strip()
        value = text(match.group(2))
        if key and value and key not in pairs:
            pairs[key] = value
    # Part Number / EAN / price live outside the ### blocks, and the label is localised.
    part = re.search(
        r'\*\*(?:Part Number|R[ée]f[ée]rence|Referencia|Codice|Artikelnummer|Numero di parte):\*\*'
        r'\s*\n+\s*([A-Z0-9][A-Z0-9._/-]{3,})', body or '', re.I)
    ean = re.search(
        r'\*\*(?:Ean Code|EAN|Code EAN|C[óo]digo EAN|Codice EAN):\*\*\s*\n+\s*(\d{8,14})',
        body or '', re.I)
    name = re.search(
        r'\*\*(?:Nome Modello|Model Name|Nom complet du produit|Nombre del modelo|Modellname)'
        r'\s*-\*\*\s*\n+\s*([^\n]+)', body or '', re.I)
    price = re.search(r'(?<![\d.,])(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|\d+(?:,\d{2}))\s*€', body or '')
    if part:
        pairs.setdefault('Part Number', part.group(1).strip())
    if ean:
        pairs.setdefault('EAN', ean.group(1).strip())
    if name:
        pairs.setdefault('Model', name.group(1).strip())
    if price:
        pairs.setdefault('Price', price.group(0).strip())
    title = re.search(r'(?m)^Title:\s*(.+)$', body or '')
    return {'pairs': pairs, 'model': one_line(title.group(1)) if title else '', 'code': pairs.get('Part Number', '')}


OUT_OF_STOCK_MARKERS = (
    'rupture de stock', 'esaurito', 'agotado', 'out of stock', 'non disponibile',
    'indisponible', 'non disponible', "m'alerter", 'avvisami', 'av[íi]same', 'notify me',
)
IN_STOCK_MARKERS = (
    'ajouter au panier', 'aggiungi al carrello', 'a[ñn]adir al carrito', 'add to cart',
    'in den warenkorb',
)


def detect_stock(body: str):
    """Return True/False when the store page states availability, else None."""
    low = (body or '').casefold()
    if any(marker in low for marker in OUT_OF_STOCK_MARKERS):
        return False
    if any(marker in low for marker in IN_STOCK_MARKERS):
        return True
    return None


def parse_price_cents(value: str):
    """'549,90€' / '1.299,00 €' / '€549.90' -> integer cents, else None."""
    match = re.search(r'(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|\d+(?:[.,]\d{2}))', value or '')
    if not match:
        return None
    raw = match.group(1).replace(' ', '').replace('.', '').replace(',', '.')
    try:
        cents = round(float(raw) * 100)
    except ValueError:
        return None
    return cents if 0 <= cents <= 100_000_000 else None


# --------------------------------------------------------------------------- normalisation
KEY_MAP = {
    'gpu': 'Chip gráfico', 'graphic engine': 'Chip gráfico', 'graphics engine': 'Chip gráfico',
    'chipset': 'Chip gráfico',
    'model': 'Modelo', 'model name': 'Modelo', 'nome modello': 'Modelo',
    'architecture': 'Arquitectura', 'gpu architecture': 'Arquitectura',
    'video memory': 'Memoria', 'memory': 'Memoria', 'memory size': 'Memoria',
    'memory type': 'Tipo de memoria',
    'memory bus': 'Bus de memoria', 'memory interface': 'Bus de memoria',
    'memory speed': 'Velocidad de memoria', 'memory clock': 'Velocidad de memoria',
    'bus standard': 'Interfaz', 'pci express': 'Interfaz', 'interface bus': 'Interfaz',
    'dimensions': 'Dimensiones', 'card length': 'Dimensiones', 'card size': 'Dimensiones',
    'length': 'Dimensiones',
    'slot': 'Ranuras', 'slot size': 'Ranuras', 'grosor': 'Ranuras',
    'recommended power supply': 'Fuente recomendada', 'recommended psu': 'Fuente recomendada',
    'power connectors': 'Conectores de alimentación', 'power connector': 'Conectores de alimentación',
    'power input': 'Conectores de alimentación',
    'engine clock': 'Frecuencia turbo', 'boost clock': 'Frecuencia turbo',
    'game clock': 'Frecuencia de juego',
    'stream processors': 'Procesadores de flujo', 'stream processor': 'Procesadores de flujo',
    'cuda cores': 'Procesadores de flujo', 'cuda core': 'Procesadores de flujo',
    'power consumption': 'Consumo', 'tbp': 'Consumo (TBP)', 'tgp': 'Consumo (TGP)',
    'display outputs': 'Salidas de vídeo', 'interface': 'Salidas de vídeo',
    'resolution': 'Resolución máxima',
    'hdcp support': 'HDCP', 'hdcp': 'HDCP',
    'multi display capability': 'Pantallas simultáneas', 'multi-view': 'Pantallas simultáneas',
    'directx': 'DirectX', 'opengl': 'OpenGL', 'cooling': 'Refrigeración',
    'net weight': 'Peso', 'accessories': 'Accesorios',
    'series': 'Serie', 'edition': 'Edición',
    # ASUS eShop localised (fr/it/es) headings.
    'moteur graphique': 'Chip gráfico', 'moteur grafique': 'Chip gráfico',
    'mémoire vidéo': 'Memoria', 'memoria video': 'Memoria',
    'horloge moteur': 'Frecuencia turbo', 'frequenza motore': 'Frecuencia turbo',
    'cœurs cuda': 'Procesadores de flujo', 'core cuda': 'Procesadores de flujo',
    'vitesse mémoire': 'Velocidad de memoria', 'velocità memoria': 'Velocidad de memoria',
    'interface mémoire': 'Bus de memoria', 'interfaccia memoria': 'Bus de memoria',
    'alimentation recommandée': 'Fuente recomendada', 'alimentatore consigliato': 'Fuente recomendada',
    'connecteurs d\'alimentation': 'Conectores de alimentación',
    'connettori di alimentazione': 'Conectores de alimentación',
    'nombre d\'écrans maximum supportés': 'Pantallas simultáneas',
    'numero massimo di schermi supportati': 'Pantallas simultáneas',
    'logiciel': 'Software', 'remarques': 'Notas',
}
SKIP_KEYS = {'note', 'software', 'sli', 'supported os', 'nvlink/ crossfire support',
             'maximum display support', 'available sku', 'part number', 'ean', 'price'}

# Canonical vocabulary per category.  The labels are the ones the manufacturer uses in its own
# specification table; the values are the Spanish keys already used by the catalogue
# (data/catalog/*.json), so a scraped record can be compared without translating it again.
CATEGORY_KEY_MAP = {
    'motherboard': {
        'socket': 'Socket', 'cpu socket': 'Socket', 'processor socket': 'Socket',
        'chipset': 'Chipset', 'southbridge': 'Chipset',
        'memory type': 'Tipo de memoria', 'memory': 'Tipo de memoria',
        'memory generation': 'Tipo de memoria', 'supported memory': 'Tipo de memoria',
        'memory slots': 'Ranuras de memoria', 'memory slot': 'Ranuras de memoria',
        'number of memory slots': 'Ranuras de memoria', 'ram slots': 'Ranuras de memoria',
        'max memory': 'Memoria máxima', 'maximum memory': 'Memoria máxima',
        'maximum memory size': 'Memoria máxima', 'memory size (max)': 'Memoria máxima',
        'form factor': 'Formato', 'form factor (motherboard)': 'Formato',
        'board form factor': 'Formato',
        'wireless networking': 'Wi-Fi', 'wi-fi': 'Wi-Fi', 'wireless lan': 'Wi-Fi',
        'network': 'Red', 'ethernet': 'Red', 'ethernet (lan)': 'Red',
        'm.2 slots': 'Almacenamiento', 'storage': 'Almacenamiento',
        'sata connectors': 'Almacenamiento', 'onboard graphics': 'Gráficos integrados',
        'audio': 'Audio', 'audio codec': 'Audio',
    },
    'psu': {
        'wattage': 'Potencia', 'power': 'Potencia', 'rated power': 'Potencia',
        'power rating': 'Potencia', 'power (wattage)': 'Potencia',
        'efficiency': 'Eficiencia', '80 plus': 'Eficiencia 80 PLUS',
        '80 plus certification': 'Eficiencia 80 PLUS', 'certification': 'Eficiencia 80 PLUS',
        'modular': 'Modular', 'modularity': 'Modularidad', 'fully modular': 'Modularidad',
        'modular type': 'Modularidad', 'cable type': 'Modularidad',
        'form factor': 'Formato', 'atx version': 'Estándar ATX', 'atx standard': 'Estándar ATX',
        'intel form factor': 'Formato',
        'pcie connector': 'Conector GPU', 'pcie version': 'Estándar PCIe', 'pcie': 'Estándar PCIe',
        'cpu connector': 'Conector CPU', 'main connector': 'Conector principal',
        'connector': 'Conectores', 'connectors': 'Conectores', 'cables': 'Cables',
        'fan size': 'Ventilador', 'fan': 'Ventilador', 'cooling': 'Ventilador',
        'length': 'Longitud', 'dimensions': 'Dimensiones',
        'warranty': 'Garantía', 'color': 'Color', 'cybenetics': 'Cybenetics',
        'fan speed': 'Velocidad de ventiladores', 'noise level': 'Nivel de ruido',
    },
    'ram': {
        'memory type': 'Tipo de memoria', 'type': 'Tipo de memoria',
        'memory configuration': 'Configuración', 'configuration': 'Configuración',
        'capacity': 'Capacidad', 'total capacity': 'Capacidad', 'memory size': 'Capacidad',
        'modules': 'Módulos', 'number of modules': 'Módulos', 'module count': 'Módulos',
        'speed': 'Velocidad', 'memory speed': 'Velocidad', 'speed rating': 'Velocidad',
        'frequency': 'Velocidad', 'rated speed': 'Velocidad',
        'latency': 'Latencia', 'cas latency': 'Latencia CAS', 'cas latency (cl)': 'Latencia CAS',
        'voltage': 'Voltaje', 'voltage (v)': 'Voltaje',
        'profile': 'Perfil', 'xmp profile': 'Perfil', 'intel xmp': 'Perfil', 'expo': 'Perfil',
        'form factor': 'Formato', 'memory form factor': 'Formato',
        'ecc': 'ECC', 'illumination': 'Iluminación', 'color': 'Color', 'series': 'Línea',
        'heat spreader': 'Difusor térmico', 'warranty': 'Garantía',
    },
    'case': {
        'case type': 'Formato', 'type': 'Formato', 'form factor': 'Formato',
        'motherboard support': 'Formatos de placa base',
        'form factor (case)': 'Formatos de placa base', 'supported motherboard': 'Formatos de placa base',
        'expansion slots': 'Ranuras de expansión',
        'maximum gpu length': 'Longitud GPU máxima', 'max gpu length': 'Longitud GPU máxima',
        'maximum gpu card length': 'Longitud GPU máxima',
        'maximum cpu cooler height': 'Altura disipador máxima',
        'max cooler height': 'Altura disipador máxima',
        'maximum radiator size': 'Radiador máximo',
        'front radiator': 'Radiador frontal máximo', 'rear radiator': 'Radiador trasero máximo',
        'top radiator': 'Radiador superior máximo',
        'dimensions': 'Dimensiones', 'dimension': 'Dimensiones',
        'side panel': 'Panel lateral', 'panel': 'Panel lateral',
        'included fans': 'Ventiladores incluidos', 'fans included': 'Ventiladores incluidos',
        'included fan': 'Ventiladores incluidos',
        'drive bays': 'Bahías de unidades', 'psu form factor': 'Formato fuente',
        'power supply': 'Formato fuente', 'color': 'Color', 'material': 'Materiales',
        'materials': 'Materiales', 'weight': 'Peso', 'front i/o': 'Front I/O',
        'warranty': 'Garantía',
    },
    'cooler': {
        'type': 'Tipo', 'cooler type': 'Tipo', 'product type': 'Tipo',
        'socket support': 'Sockets compatibles', 'sockets': 'Sockets compatibles',
        'cpu socket': 'Sockets compatibles', 'supported sockets': 'Sockets compatibles',
        'radiator size': 'Radiador', 'radiator dimension': 'Radiador', 'radiator': 'Radiador',
        'radiator dimensions': 'Dimensiones radiador',
        'fan size': 'Ventilador', 'fans': 'Ventiladores', 'fan': 'Ventilador',
        'fan speed': 'Velocidad de ventiladores', 'speed': 'Velocidad de ventiladores',
        'noise level': 'Nivel de ruido', 'airflow': 'Flujo de aire',
        'height': 'Altura', 'dimensions': 'Dimensiones', 'tdp': 'TDP',
        'cooling capacity': 'TDP', 'pump speed': 'Velocidad de bomba',
        'heatpipes': 'Heatpipes', 'bearing': 'Cojinete', 'color': 'Color',
        'illumination': 'Iluminación', 'display': 'Pantalla', 'warranty': 'Garantía',
        'tube length': 'Longitud de tubos', 'pump speed (rpm)': 'Velocidad de bomba',
    },
    'storage': {
        'capacity': 'Capacidad', 'form factor': 'Formato', 'interface': 'Interfaz',
        'sequential read': 'Lectura secuencial', 'sequential write': 'Escritura secuencial',
        'endurance': 'Resistencia', 'tbw': 'Resistencia', 'nand type': 'Tipo de NAND',
        'nand': 'Tipo de NAND', 'cache': 'Caché', 'cache memory': 'Caché',
        'controller': 'Controlador', 'heatsink': 'Disipador integrado',
        'encryption': 'Cifrado', 'power consumption': 'Consumo',
        'speed': 'Velocidad de rotación', 'rotation speed': 'Velocidad de rotación',
        'rpm': 'Velocidad de rotación', 'interface type': 'Interfaz', 'type': 'Tipo',
        'warranty': 'Garantía',
    },
}

# Category -> canonical keys whose value is a bare number that must keep its unit.
CATEGORY_UNITS: dict[str, dict[str, str]] = {}


def _category_map(category: str) -> dict:
    if category == 'gpu':
        return KEY_MAP
    merged = dict(KEY_MAP)
    merged.update(CATEGORY_KEY_MAP.get(category, {}))
    return merged


def normalise_specs(brand: str, pairs: dict, page_text: str = '', category: str = 'gpu') -> dict:
    key_map = _category_map(category)
    out = {}
    for raw_key, raw_value in pairs.items():
        key = one_line(raw_key).rstrip(':').strip()
        value = text(raw_value)
        if not key or not value:
            continue
        low = key.casefold()
        if low in SKIP_KEYS:
            continue
        canonical = key_map.get(low)
        if not canonical:
            canonical = key
        if canonical in out and out[canonical] != value:
            continue
        out[canonical] = value

    if category != 'gpu':
        return _normalise_non_gpu(out, category)

    # Memory "16GB GDDR6" (most) or "GDDR6 10GB" (ASRock Intel cards) -> capacity + type.
    memory = out.get('Memoria') or ''
    match = re.search(r'(\d+\s*GB)\s+((?:GDDR|HBM)\d\w*)', memory, re.I)
    if match:
        out['Memoria'] = re.sub(r'(?<=\d)\s*(GB|TB|MB)', r' \1', re.sub(r'\s+', ' ', match.group(1)))
        out.setdefault('Tipo de memoria', match.group(2).upper())
    else:
        match = re.search(r'((?:GDDR|HBM)\d\w*)\s+(\d+\s*GB)', memory, re.I)
        if match:
            out['Memoria'] = re.sub(r'(?<=\d)\s*(GB|TB|MB)', r' \1', re.sub(r'\s+', ' ', match.group(2)))
            out.setdefault('Tipo de memoria', match.group(1).upper())

    # PCIe spelling.
    if 'Interfaz' in out:
        out['Interfaz'] = re.sub(r'[®™]', '', out['Interfaz'])
        out['Interfaz'] = re.sub(r'\bPCI\s*Express\s*', 'PCIe ', out['Interfaz'], flags=re.I)
        out['Interfaz'] = re.sub(r'\bPCIE\b', 'PCIe', out['Interfaz'], flags=re.I)
        out['Interfaz'] = re.sub(r'\s+', ' ', out['Interfaz']).strip()

    # Architecture only when a source states it explicitly (ASRock key spec, ASUS spec table);
    # never inferred from unrelated page text.
    if 'Arquitectura' not in out:
        for value in list(out.values()):
            arch = re.search(r'\b(?:AMD\s+)?RDNA[™\s]*(\d)\b', value, re.I)
            if arch:
                out['Arquitectura'] = f'RDNA {arch.group(1)}'
                break
            arch = re.search(r'\b(Blackwell|Ada Lovelace|Ampere)\b', value, re.I)
            if arch:
                out['Arquitectura'] = arch.group(1)
                break

    # Length = first dimension in mm (catalog GPU "Longitud" is the card length).
    dimensions = out.get('Dimensiones') or ''
    if dimensions:
        length = re.search(r'(\d+(?:[.,]\d+)?)\s*[x×]\s*\d+(?:[.,]\d+)?\s*[x×]\s*\d+(?:[.,]\d+)?\s*mm',
                           dimensions, re.I)
        if not length:
            length = re.search(r'(\d+(?:[.,]\d+)?)\s*mm', dimensions, re.I)
        if not length:
            length = re.search(r'^(\d+(?:[.,]\d+)?)\s*[x×]', dimensions, re.I)
        if length:
            out['Longitud'] = length.group(1).replace(',', '.') + ' mm'

    # Slot size from "2.9-slot", "2.5 Slot", "3.125 Slot".
    if 'Ranuras' not in out:
        slot = re.search(r'(\d+(?:[.,]\d+)?)\s*[- ]?slot', dimensions, re.I)
        if slot:
            out['Ranuras'] = slot.group(1).replace(',', '.') + ' slots'
    elif re.search(r'slot', out['Ranuras'], re.I) is None:
        out['Ranuras'] = out['Ranuras'] + ' slots'
    else:
        out['Ranuras'] = re.sub(r'(?i)\s*slots?\b', ' slots', out['Ranuras']).strip()

    # Clean vendor decorations that add no information.
    for key in ('Chip gráfico',):
        if key in out:
            out[key] = re.sub(r'[®™]', '', out[key])
            out[key] = re.sub(r'\s+(?:GPU|Graphics Processor)$', '', out[key], flags=re.I)
            out[key] = re.sub(r'\s+', ' ', out[key]).strip()

    return out


def _normalise_non_gpu(out: dict, category: str) -> dict:
    """Cleanups for every category except GPU.

    Values are kept verbatim: a bare number is never given a unit, because "16" in a capacity
    field is an inference, not a published fact (see docs/catalog.md: "Ausencias quedan
    desconocidas").  Only vendor decorations and repeated whitespace are removed.
    """
    for key, value in list(out.items()):
        cleaned = re.sub(r'[®™]', '', value)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        if cleaned and cleaned != value:
            out[key] = cleaned
    return out


def load_catalog(root: Path = ROOT) -> dict:
    products = []
    for path in sorted((root / 'data/catalog').glob('*.json')):
        try:
            items = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(items, list):
            products.extend(item for item in items if isinstance(item, dict) and item.get('id'))
    by_mpn, by_model = {}, {}
    for product in products:
        if product.get('mpn'):
            by_mpn.setdefault(norm_mpn(product['mpn']), product)
        if product.get('name'):
            by_model.setdefault(norm_model(product['name']), product)
    return {'products': products, 'by_mpn': by_mpn, 'by_model': by_model}


def match_catalog(record: dict, catalog: dict) -> None:
    mpn = record.get('mpn') or ''
    if mpn and norm_mpn(mpn) in catalog['by_mpn']:
        product = catalog['by_mpn'][norm_mpn(mpn)]
        record['catalog_product_id'] = product['id']
        record['match'] = 'mpn'
        return
    model = record.get('model') or ''
    if model and norm_model(model) in catalog['by_model']:
        product = catalog['by_model'][norm_model(model)]
        record['catalog_product_id'] = product['id']
        record['match'] = 'model'
        return
    record['catalog_product_id'] = None
    record['match'] = None


def make_record(brand: str, url: str, *, model: str, mpn: str, specs: dict, raw_specs: dict,
                transport: str, status: int, price_cents=None, currency=None, in_stock=None,
                ean=None, notes=None, category: str = 'gpu') -> dict:
    return {
        'brand': brand, 'source': 'official', 'category': category, 'url': url, 'model': model or None,
        'mpn': mpn or None, 'ean': ean or None, 'specs': specs, 'raw_specs': raw_specs,
        'price_cents': price_cents, 'currency': currency, 'in_stock': in_stock,
        'transport': transport, 'http_status': status, 'fetched_at': now_utc(),
        'catalog_product_id': None, 'match': None, 'notes': notes or [],
    }


def write_report(path: Path, brand: str, records: list, errors: list, source: str,
                 category: str = 'gpu') -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'brand': brand, 'category': category, 'source': source, 'generated_at': now_utc(),
        'count': len(records), 'records': records, 'errors': errors,
    }
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
    os.replace(temporary, path)
    log('report', brand=brand, category=category, records=len(records), errors=len(errors),
        output=str(path))


CATEGORY_SLUG = {'gpu': 'gpus'}


def category_slug(category: str) -> str:
    """File-name slug of a category ('gpu' keeps the historical 'gpus' name)."""
    return CATEGORY_SLUG.get(category, category)


def add_common_args(parser, categories: tuple = ('gpu',)) -> None:
    parser.add_argument('--output', type=Path,
                        help='JSON output (default: artifacts/manufacturer-<brand>-<category>.json)')
    parser.add_argument('--category', choices=categories, default=categories[0],
                        help='Product category to scrape')
    parser.add_argument('--cache-dir', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--delay', type=float, default=2.5, help='Seconds between network requests')
    parser.add_argument('--max-age', type=float, default=86400, help='Cache freshness in seconds (0 always refetch)')
    parser.add_argument('--refresh', action='store_true', help='Ignore the cache')
    parser.add_argument('--no-jina', action='store_true', help='Do not use the reader fallback for blocked pages')
    parser.add_argument('--limit', type=int, default=0, help='Maximum pages to scrape (0 = all)')
    parser.add_argument('--only-current', action='store_true', help='Keep only RTX 50 / RX 9000 / Arc B products')
    parser.add_argument('--no-catalog', action='store_true', help='Do not annotate catalog_product_id')
    parser.add_argument('--url', action='append', default=[], help='Scrape this product URL (repeatable)')
    parser.add_argument('--verbose', action='store_true')


CURRENT_RE = re.compile(r'(RTX\s*50\d0|RX\s*9\d{3}|Arc\s*[BP]\d{3})', re.I)


def current_only(name: str) -> bool:
    return bool(CURRENT_RE.search(name or ''))
