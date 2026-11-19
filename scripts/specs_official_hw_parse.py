"""Parsers de fichas oficiales (Gigabyte, MSI, ASUS, ASRock, Intel ARK, AMD) -> dict etiqueta -> texto.
Usado por scripts/specs_official_hw_derive.py. ASUS y ASRock reutilizan los parsers de manufacturer_scrape.py."""
import re, html, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _txt(s, sep='; '):
    s = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', s)
    s = re.sub(r'(?i)<br\s*/?>|</li>|</p>|</tr>', sep, s)
    s = re.sub(r'<[^>]+>', ' ', s)
    s = html.unescape(s).replace('\xa0', ' ')
    s = re.sub(r'[®™]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'(?:;\s*)+', '; ', s).strip(' ;')
    return s


def parse_gigabyte(h):
    out = {}
    for m in re.finditer(r'<li class="spec-title"><div>(.*?)</div></li>\s*<li class="spec-desc"><div>(.*?)</div></li>', h, re.S):
        k = _txt(m.group(1)); v = _txt(m.group(2))
        if k and k not in out: out[k] = v
    return out


def parse_msi(h):
    out = {}
    for m in re.finditer(r'<th scope="row"><span class="h2">(.*?)</span>\s*</th>\s*<td>(.*?)</td>', h, re.S):
        k = _txt(m.group(1)); v = _txt(m.group(2))
        if k and k not in out: out[k] = v
    for m in re.finditer(r'<li class="specName">(.*?)</li>\s*</ul>(.*?)</div>', h, re.S):
        k = _txt(m.group(1)); v = _txt(m.group(2))
        if k and k not in out: out[k] = v
    return out


def parse_intel(h):
    out = {}
    for r in re.findall(r'<tr[^>]*>(.*?)</tr>', h, re.S):
        th = re.search(r'<th[^>]*class="key"[^>]*>(.*?)</th>', r, re.S)
        td = re.search(r'<td[^>]*class="value"[^>]*>(.*?)</td>', r, re.S)
        if not (th and td): continue
        k = re.search(r'<div class="row-key">(.*?)<button|<div class="row-key">(.*?)</div>', th.group(1), re.S)
        k = _txt((k.group(1) or k.group(2)) if k else th.group(1))
        k = re.sub(r'\s*‡.*$', '', k).strip()  # marca de nota al pie de ARK
        v = re.split(r'\s*"\s*tabindex', _txt(td.group(1)))[0]
        v = re.sub(r'\s*‡\s*$', '', v).strip()
        if k and k not in out: out[k] = v
    if not out:  # maquetación nueva de ARK (tech-label / tech-data)
        for m in re.finditer(r'tech-label">\s*<span>(.*?)</span>.*?tech-data">(.*?)</div>', h, re.S):
            k = _txt(m.group(1)); v = re.sub(r'\s*‡\s*$', '', _txt(m.group(2))).strip()
            if k and k not in out: out[k] = v
    return out


def parse_amd(h):
    out = {}
    for m in re.finditer(r'<dt>(.*?)</dt>.*?<dd>(.*?)</dd>', h, re.S):
        k = _txt(m.group(1)); v = _txt(m.group(2))
        k = re.split(r'\s+(?:Max boost|Represents|AMD`s|AMD’s)\b', k)[0]
        k = re.sub(r'\s*<.*$', '', k).strip()
        if k and k not in out: out[k] = v
    return out


def parse_asus(h):
    import manufacturer_scrape as ms
    return ms.parse_asus_html(h)['pairs']


def parse_asrock(h):
    import manufacturer_scrape as ms
    return ms.parse_asrock(h)['pairs']


def parse_site(site, h):
    f = {'gigabyte': parse_gigabyte, 'msi': parse_msi, 'intel': parse_intel, 'amd': parse_amd,
         'asus': parse_asus, 'asrock': parse_asrock}.get(site)
    return f(h) if f else None
