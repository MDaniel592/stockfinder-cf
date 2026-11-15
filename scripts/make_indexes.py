"""Generate scripts/README.md and docs/README.md from file headers (run from the repo root)."""
import ast
import re
import subprocess
from pathlib import Path

tracked = subprocess.check_output(['git', 'ls-files'], text=True).split('\n')


def first_line(text):
    for line in text.strip().splitlines():
        line = line.strip()
        if line:
            return line.rstrip('.')
    return ''


def describe(path):
    text = Path(path).read_text(errors='replace')
    if path.endswith('.py'):
        try:
            doc = ast.get_docstring(ast.parse(text))
        except SyntaxError:
            doc = None
        return first_line(doc) if doc else ''
    for line in text.splitlines()[1:6]:
        if line.startswith('#'):
            return first_line(line.lstrip('# '))
    return ''


FAMILIES = [
    ('LDLC y Neobyte', ('ldlc_', 'neobyte_', 'retailer_', 'local_retailer', 'amd_opn', 'manufacturer_ean', 'icecat_')),
    ('Coolmod y PcComponentes', ('coolmod_', 'pccomponentes_', 'run_coolmod', 'offers_')),
    ('Referencias de fabricante', ('manufacturer_', 'scrape_', 'fetch_official')),
    ('Imágenes', ('images_', 'catalog_images', 'fetch_product_images', 'fetch_retailer_images', 'make_thumbnails', 'fetch_')),
    ('Catálogo y specs', ('catalog_', 'generate_', 'import_', 'spec', 'normalize_', 'backfill_')),
    ('Despliegue y entornos', ('deploy', 'local_site', 'staging_', 'cloudflare', 'test_pg', 'lab_', 'seed_', 'supabase', 'railway')),
]


def family(name):
    for title, prefixes in FAMILIES:
        if name.startswith(prefixes):
            return title
    return 'Otros'


scripts = sorted(p for p in tracked if p.startswith('scripts/') and p.count('/') == 1 and not p.endswith('README.md'))
groups = {}
for path in scripts:
    groups.setdefault(family(Path(path).name), []).append(path)
out = ['# scripts/', '',
       'Índice generado a partir de la primera línea de cada script (`python3 scripts/make_indexes.py` lo regenera).',
       'Los scripts de rastreo escriben solo en las bases aisladas `stockfinder_*_local` salvo que su cabecera diga otra cosa.', '']
order = [t for t, _ in FAMILIES] + ['Otros']
for title in order:
    if title not in groups:
        continue
    out += [f'## {title}', '', '| Script | Qué hace |', '|---|---|']
    for path in groups[title]:
        name = Path(path).name
        out.append(f'| `{name}` | {describe(path) or "_sin descripción_"} |')
    out.append('')
Path('scripts/README.md').write_text('\n'.join(out))

docs = sorted(p for p in tracked if p.startswith('docs/') and p.endswith('.md') and p != 'docs/README.md')
out = ['# docs/', '', 'Índice de la documentación. Para operar la pila, empieza por `runbooks/operations.md`.', '',
       '| Documento | Contenido |', '|---|---|']
for path in docs:
    text = Path(path).read_text(errors='replace')
    heading = next((l.lstrip('# ').strip() for l in text.splitlines() if l.startswith('#')), '')
    out.append(f'| [{path[5:]}]({path[5:]}) | {heading} |')
out.append('')
Path('docs/README.md').write_text('\n'.join(out))
print(len(scripts), 'scripts,', len(docs), 'docs')
