"""Herramienta interna (NO es la web pública): productos con specs clave ausentes y alta manual en data/overrides/.

Lee los lotes data/catalog/*.json (el mismo origen que import_catalog), normaliza con nas/spec_normalize.py, aplica las
correcciones de data/overrides/*.json y lista por categoría los huecos de las specs que usan la ficha, los filtros y el
configurador. El formulario escribe registros validados (source "manual", fecha, URL oficial consultada, nota) en
data/overrides/manual-<categoría>.json; el publicador los aplica en su siguiente ciclo. Solo stdlib.

  python3 scripts/spec_gaps_web.py --host 192.168.1.50 --port 8801     (o bash scripts/spec_gaps.sh)
"""
import argparse
from datetime import date
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from urllib.parse import parse_qs, quote, urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nas'))
from spec_normalize import normalize_specs  # noqa: E402
import spec_overrides as so  # noqa: E402

CATEGORY_NAMES = {'cpu': 'Procesadores', 'gpu': 'Tarjetas gráficas', 'motherboard': 'Placas base', 'ram': 'Memoria RAM',
                  'storage': 'Almacenamiento', 'psu': 'Fuentes', 'case': 'Cajas', 'cooler': 'Refrigeración'}
PAGE_SIZE = 50
_lock = threading.Lock()
_cache = {'sig': None, 'data': None}


def _signature(root):
    files = sorted((root / 'data/catalog').glob('*.json')) + sorted((root / 'data/overrides').glob('*.json'))
    sig = []
    for path in files:
        try:
            st = path.stat()
            sig.append((str(path), st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    return tuple(sig)


def load(root=ROOT):
    """{'products': {id: producto con specs efectivas}, 'overrides': índice, 'override_errors': {...}, 'skipped': [...]}"""
    with _lock:
        sig = _signature(root)
        if _cache['sig'] == sig: return _cache['data']
        products, skipped = {}, []
        for path in sorted((root / 'data/catalog').glob('*.json')):
            try:
                items = json.loads(path.read_text())
            except (OSError, ValueError):
                skipped.append(path.name)  # lote a medio escribir: se reintenta en la siguiente carga
                continue
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and item.get('id') and item.get('category_id') in so.FORMATS:
                    products[item['id']] = item  # el último lote gana, como en el import
        records, errors = so.load_overrides(root)
        by_product = so.index(records)
        effective = {}
        for pid, item in products.items():
            specs = normalize_specs(item['category_id'], item.get('specs') or {})
            product = {**item, 'specs': specs}
            product['specs'] = so.apply(product, by_product) or {}
            product['gaps'] = so.gaps(product, by_product)
            effective[pid] = product
        data = {'products': effective, 'overrides': by_product, 'override_errors': errors, 'skipped': skipped}
        _cache.update(sig=sig, data=data)
        return data


# ------------------------------------------------------------------ HTML
CSS = """body{font:14px/1.4 system-ui,sans-serif;margin:16px;color:#1d2228;background:#f6f7f9}a{color:#0b5cad}
table{border-collapse:collapse;background:#fff}td,th{border:1px solid #d7dbe0;padding:4px 8px;text-align:left;vertical-align:top}
.p{background:#fff;border:1px solid #d7dbe0;border-radius:6px;padding:10px 12px;margin:10px 0}.muted{color:#68707a;font-size:12px}
.row{display:flex;gap:8px;align-items:center;margin:4px 0;flex-wrap:wrap}.row label.k{min-width:170px;font-weight:600}
input[type=text],input[type=url]{padding:3px 6px;border:1px solid #b9c0c8;border-radius:4px}input.v{width:260px}input.u{width:520px}
.tag{display:inline-block;background:#e8f1fb;border-radius:4px;padding:1px 6px;margin:1px 4px 1px 0;font-size:12px}
.np{background:#f1ece2}.msg{padding:8px 12px;border-radius:6px;margin:8px 0}.ok{background:#e3f4e6}.err{background:#fbe5e5}
button{cursor:pointer}form.inline{display:inline}nav a{margin-right:10px}"""


def esc(value):
    return html.escape(str(value), quote=True)


def page(title, body, msg=None, err=None):
    notes = ''.join(f'<div class="msg ok">{esc(m)}</div>' for m in msg or []) + ''.join(f'<div class="msg err">{esc(e)}</div>' for e in err or [])
    nav = '<nav><a href="/">Resumen</a>' + ''.join(f'<a href="/c/{c}">{esc(n)}</a>' for c, n in CATEGORY_NAMES.items()) + '<a href="/overrides">Correcciones</a></nav>'
    return (f'<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
            f'<title>{esc(title)} · specs incompletas</title><style>{CSS}</style></head><body>{nav}<h1>{esc(title)}</h1>{notes}{body}</body></html>')


def summary_page(data, msg, err):
    rows = []
    for cat, name in CATEGORY_NAMES.items():
        items = [p for p in data['products'].values() if p['category_id'] == cat]
        per_key = {}
        for p in items:
            for gap in p['gaps']: per_key[gap['key']] = per_key.get(gap['key'], 0) + 1
        keys = ' '.join(f'<a class="tag" href="/c/{cat}?key={quote(k)}">{esc(k)}: {n}</a>' for k, n in sorted(per_key.items(), key=lambda kv: -kv[1]))
        rows.append(f'<tr><td><a href="/c/{cat}">{esc(name)}</a></td><td>{len(items)}</td>'
                    f'<td>{sum(1 for p in items if p["gaps"])}</td><td>{keys or "—"}</td></tr>')
    warn = []
    if data['skipped']: warn.append('Lotes ilegibles ahora mismo (¿a medio escribir?): ' + ', '.join(data['skipped']))
    for name, errors in data['override_errors'].items(): warn.append(f'data/overrides/{name} inválido (se ignora entero): ' + '; '.join(errors[:3]))
    body = ('<p class="muted">Herramienta interna. Solo valores que figuren en la ficha oficial; nunca suposiciones. '
            'Las correcciones se guardan en <code>data/overrides/</code> y el publicador las aplica en su siguiente ciclo.</p>'
            '<table><tr><th>Categoría</th><th>Productos</th><th>Con huecos</th><th>Huecos por spec</th></tr>' + ''.join(rows) + '</table>')
    return page('Specs clave incompletas', body, msg, (err or []) + warn)


def product_block(p, data, back):
    cat = p['category_id']
    manual = data['overrides'].get(p['id'], {})
    url = p.get('manufacturer_url') or ''
    tags = []
    for key, rec in manual.items():
        label = 'el fabricante no lo publica' if rec.get('status') == so.NOT_PUBLISHED else rec.get('value')
        tags.append(f'<span class="tag {"np" if rec.get("status") == so.NOT_PUBLISHED else ""}" title="{esc(rec.get("date"))} · {esc(rec.get("source_url"))}">'
                    f'{esc(key)}: {esc(label)} (manual) <form class="inline" method="post" action="/delete">'
                    f'<input type="hidden" name="product_id" value="{esc(p["id"])}"><input type="hidden" name="category_id" value="{esc(cat)}">'
                    f'<input type="hidden" name="key" value="{esc(key)}"><input type="hidden" name="back" value="{esc(back)}">'
                    f'<button title="Quitar corrección">×</button></form></span>')
    rows = []
    for gap in p['gaps']:
        key = gap['key']
        keys = [key, *gap['alternates']]
        name = f'k_{key}'
        selector = (f'<select name="{esc(name)}">' + ''.join(f'<option>{esc(k)}</option>' for k in keys) + '</select>'
                    if len(keys) > 1 else f'<input type="hidden" name="{esc(name)}" value="{esc(key)}">')
        rows.append(f'<div class="row"><label class="k">{esc(key)}</label>{selector}'
                    f'<input class="v" type="text" name="v_{esc(key)}" placeholder="{esc(so.format_hint(cat, key))}">'
                    f'<label><input type="checkbox" name="np_{esc(key)}" value="1"> el fabricante no lo publica</label></div>')
    specs = ', '.join(f'{k}: {v}' for k, v in p['specs'].items() if isinstance(v, str))[:400]
    link = f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">ficha oficial ↗</a>' if url else '<em>sin manufacturer_url</em>'
    form = ''
    if rows:
        form = (f'<form method="post" action="/save"><input type="hidden" name="product_id" value="{esc(p["id"])}">'
                f'<input type="hidden" name="category_id" value="{esc(cat)}"><input type="hidden" name="back" value="{esc(back)}">'
                + ''.join(rows) +
                f'<div class="row"><label class="k">URL consultada</label><input class="u" type="url" name="source_url" required value="{esc(url)}"></div>'
                f'<div class="row"><label class="k">Nota (opcional)</label><input class="u" type="text" name="note" maxlength="300"></div>'
                f'<div class="row"><button>Guardar</button><span class="muted">Solo los campos rellenos o marcados.</span></div></form>')
    return (f'<div class="p" id="{esc(p["id"])}"><b>{esc(p.get("name"))}</b> <span class="muted">{esc(p["id"])} · {esc(p.get("brand"))}'
            f' · MPN {esc(p.get("mpn") or "—")}</span> · {link}<div>{"".join(tags)}</div>'
            f'<div class="muted">{esc(specs)}</div>{form}</div>')


def category_page(data, cat, query, msg, err):
    key = query.get('key', [''])[0]
    text = query.get('q', [''])[0].strip().casefold()
    raw = query.get('page', ['1'])[0]
    pageno = int(raw) if raw.isdigit() and int(raw) > 0 else 1
    items = [p for p in data['products'].values() if p['category_id'] == cat and p['gaps']]
    if key: items = [p for p in items if any(g['key'] == key for g in p['gaps'])]
    if text: items = [p for p in items if text in f"{p.get('name')} {p['id']} {p.get('brand')} {p.get('mpn')}".casefold()]
    items.sort(key=lambda p: (str(p.get('brand')).casefold(), str(p.get('name')).casefold()))
    total = len(items)
    shown = items[(pageno - 1) * PAGE_SIZE: pageno * PAGE_SIZE]
    keys = [r[0] for r in so.REQUIRED[cat]]
    options = '<option value="">todas las specs</option>' + ''.join(f'<option{" selected" if k == key else ""}>{esc(k)}</option>' for k in keys)
    back = f'/c/{cat}?' + urlencode({k: v for k, v in (('key', key), ('q', text), ('page', pageno)) if v})
    nav = []
    if pageno > 1: nav.append(f'<a href="/c/{cat}?{urlencode(dict(key=key, q=text, page=pageno - 1))}">← anterior</a>')
    if pageno * PAGE_SIZE < total: nav.append(f'<a href="/c/{cat}?{urlencode(dict(key=key, q=text, page=pageno + 1))}">siguiente →</a>')
    body = (f'<form method="get" class="row"><select name="key">{options}</select><input type="text" name="q" value="{esc(text)}" placeholder="buscar nombre, id, MPN">'
            f'<button>Filtrar</button></form><p class="muted">{total} productos con huecos · página {pageno} · '
            'Escribe el valor tal como figura en la ficha oficial; se valida contra el formato canónico de nas/spec_normalize.py.</p>'
            + ''.join(product_block(p, data, back) for p in shown) + '<p>' + ' '.join(nav) + '</p>')
    return page(CATEGORY_NAMES[cat], body, msg, err)


def overrides_page(data, msg, err):
    rows = []
    for pid, entries in sorted(data['overrides'].items()):
        product = data['products'].get(pid)
        for key, rec in entries.items():
            value = 'el fabricante no lo publica' if rec.get('status') == so.NOT_PUBLISHED else rec.get('value')
            rows.append(f'<tr><td>{esc(rec["category_id"])}</td><td>{esc(product.get("name") if product else pid + " (no está en el catálogo)")}</td>'
                        f'<td>{esc(key)}</td><td>{esc(value)}</td><td>{esc(rec.get("date"))}</td>'
                        f'<td><a href="{esc(rec.get("source_url"))}" target="_blank" rel="noopener noreferrer">fuente</a></td><td>{esc(rec.get("note", ""))}</td>'
                        f'<td><form class="inline" method="post" action="/delete"><input type="hidden" name="product_id" value="{esc(pid)}">'
                        f'<input type="hidden" name="category_id" value="{esc(rec["category_id"])}"><input type="hidden" name="key" value="{esc(key)}">'
                        f'<input type="hidden" name="back" value="/overrides"><button>Quitar</button></form></td></tr>')
    body = ('<table><tr><th>Cat.</th><th>Producto</th><th>Spec</th><th>Valor</th><th>Fecha</th><th>Fuente</th><th>Nota</th><th></th></tr>'
            + ''.join(rows) + '</table>') if rows else '<p>Sin correcciones manuales.</p>'
    return page('Correcciones manuales', body, msg, err)


def save_form(form, root=ROOT, today=None):
    """Guarda los campos rellenos/marcados de un producto. Devuelve (guardados, errores); nada se guarda si hay errores."""
    get = lambda name: (form.get(name) or [''])[0].strip()
    pid, cat, url, note = get('product_id'), get('category_id'), get('source_url'), get('note')
    data = load(root)
    product = data['products'].get(pid)
    if not product or product['category_id'] != cat: return [], ['producto desconocido']
    records, errors = [], []
    for gap in product['gaps']:
        key = gap['key']
        target = get(f'k_{key}') or key
        if target not in (key, *gap['alternates']): errors.append(f'{key}: clave no válida'); continue
        value, missing = get(f'v_{key}'), get(f'np_{key}') == '1'
        if not value and not missing: continue
        if value and missing: errors.append(f'{key}: o valor o «no lo publica», no ambos'); continue
        record = {'product_id': pid, 'category_id': cat, 'key': target, 'source': so.SOURCE, 'date': today or date.today().isoformat(), 'source_url': url}
        if missing: record['status'] = so.NOT_PUBLISHED
        else:
            norm, error = so.check_value(cat, target, value)
            if error: errors.append(f'{target}: {error}'); continue
            record['value'] = norm
        if note: record['note'] = note
        problems = so.validate_record(record)
        if problems: errors.append(f'{target}: ' + '; '.join(problems)); continue
        records.append(record)
    if errors: return [], errors
    if not records: return [], ['no había ningún campo relleno ni marcado']
    for record in records: so.save_record(root, record)
    return [f"{r['key']} = {r.get('value', 'no lo publica')}" for r in records], []


class Handler(BaseHTTPRequestHandler):
    server_version = 'spec-gaps'

    def _send(self, status, body=b'', headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items(): self.send_header(k, v)
        if body: self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        if body: self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        msg, err = query.get('msg'), query.get('err')
        data = load()
        if url.path == '/': html_out = summary_page(data, msg, err)
        elif url.path == '/overrides': html_out = overrides_page(data, msg, err)
        elif url.path.startswith('/c/') and url.path[3:] in CATEGORY_NAMES: html_out = category_page(data, url.path[3:], query, msg, err)
        else: return self._send(404, b'No encontrado')
        self._send(200, html_out.encode())

    def do_POST(self):
        origin = self.headers.get('Origin')
        if origin and urlparse(origin).netloc != self.headers.get('Host'): return self._send(403, b'Origen no permitido')
        length = int(self.headers.get('Content-Length') or 0)
        if length > 100_000: return self._send(413, b'Demasiado grande')
        form = parse_qs(self.rfile.read(length).decode('utf-8'), keep_blank_values=True)
        back = (form.get('back') or ['/'])[0]
        if not back.startswith('/') or back.startswith('//'): back = '/'
        path = urlparse(self.path).path
        if path == '/save':
            saved, errors = save_form(form)
            params = [('err', e) for e in errors] + ([('msg', 'Guardado: ' + ', '.join(saved))] if saved else [])
        elif path == '/delete':
            get = lambda name: (form.get(name) or [''])[0]
            done = so.delete_record(ROOT, get('category_id'), get('product_id'), get('key')) if get('category_id') in CATEGORY_NAMES else False
            params = [('msg', f"Quitada la corrección {get('key')} de {get('product_id')}")] if done else [('err', 'No existía esa corrección')]
        else:
            return self._send(404, b'No encontrado')
        sep = '&' if '?' in back else '?'
        self._send(303, headers={'Location': back + sep + urlencode(params)})

    def log_message(self, fmt, *args):
        sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--host', default='127.0.0.1', help='IP de la LAN (nunca 0.0.0.0 hacia Internet)')
    parser.add_argument('--port', type=int, default=8801)
    args = parser.parse_args()
    load()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(json.dumps({'event': 'spec_gaps_web', 'url': f'http://{args.host}:{args.port}/'}), flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
