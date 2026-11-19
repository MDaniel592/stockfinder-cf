"""Genera el dry-run de specs de Icecat: identidad estricta + mapeo (icecat_spec_map) + normalize_specs. No toca la BD.
Uso (host VM):  python3 scripts/specs_icecat_dryrun.py <dir work>
Lee <dir>/candidates.jsonl y <dir>/results.jsonl; escribe <dir>/dryrun.json (lista de {product_id,key,value,...}) y
<dir>/summary.json (cobertura por categoria/marca y claves). Solo propone claves AUSENTES; nunca sobrescribe."""
import collections, json, os, re, sys
here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
sys.path.insert(0, os.path.join(here, '..', 'nas'))
import spec_normalize as sn
from icecat_spec_map import map_features

norm = lambda s: re.sub(r'\s+', '', s or '').upper()
digits = lambda s: re.sub(r'\D', '', str(s or ''))


def identity_ok(result, cand):
    """Codigo de producto devuelto == MPN del catalogo (sin espacios/mayusculas) o GTIN devuelto contiene el EAN."""
    if result.get('status') != 'ok':
        return None
    if norm(result.get('code')) and norm(result['code']) == norm(cand['mpn']):
        return 'mpn'
    eans = {digits(e).lstrip('0') for e in (cand.get('ean') or []) if digits(e)}
    got = {digits(g).lstrip('0') for g in (result.get('gtins') or []) if digits(g)}
    if eans and eans & got:
        return 'gtin'
    return None


def main():
    wd = sys.argv[1]
    cands = {}
    for l in open(f'{wd}/candidates.jsonl', encoding='utf-8'):
        if l.strip():
            c = json.loads(l)
            cands[c['id']] = c
    dry, cover, skipped = [], collections.defaultdict(collections.Counter), collections.Counter()
    for l in open(f'{wd}/results.jsonl', encoding='utf-8'):
        r = json.loads(l)
        c = cands.get(r['product_id'])
        if not c:
            continue
        label = r['status'] if r['status'] != 'ok' else 'ok'
        if r['status'] == 'ok':
            how = identity_ok(r, c)
            if not how:
                label = 'ficha_otro_codigo'
        cover[(c['cat'], c['brand'])][label if r['status'] != 'http_403' else 'http_403'] += 1
        if r['status'] != 'ok' or label != 'ok':
            continue
        specs = c['specs']
        mapped = map_features(c['cat'], r['features'])
        room = 24 - len(specs)
        added = 0
        for key, (val, src) in mapped.items():
            if key in specs:
                continue
            nv = sn.normalize_specs(c['cat'], {**specs, key: val}).get(key)
            if not isinstance(nv, str) or not nv or len(key) > 80 or len(nv) > 250:
                skipped['no normalizable'] += 1
                continue
            if added >= room:
                skipped['limite 24 claves'] += 1
                continue
            added += 1
            dry.append({'product_id': c['id'], 'name': c['name'], 'category': c['cat'], 'brand': c['brand'], 'mpn': c['mpn'],
                        'key': key, 'value': nv, 'mapped_value': val, 'icecat_feature': src, 'icecat_id': r.get('icecat_id'),
                        'icecat_code': r.get('code'), 'identity': how, 'via': r.get('via'),
                        'source': 'Icecat live API ' + ('GTIN=' + str((c.get('ean') or [''])[0]) if how == 'gtin' and r.get('via') == 'gtin'
                                                         else f"Brand={c['brand']}&ProductCode={c['mpn']}"),
                        'checked_at': r.get('at')})
    json.dump(dry, open(f'{wd}/dryrun.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    per_cat = collections.defaultdict(lambda: {'productos': set(), 'claves': collections.Counter()})
    for d in dry:
        per_cat[d['category']]['productos'].add(d['product_id'])
        per_cat[d['category']]['claves'][d['key']] += 1
    cov_cat = collections.defaultdict(collections.Counter)
    for (cat, brand), cn in cover.items():
        cov_cat[cat].update(cn)
    summary = {'dryrun_entradas': len(dry), 'productos': len({d['product_id'] for d in dry}), 'omitidas': dict(skipped),
               'por_categoria': {k: {'productos': len(v['productos']), 'claves': dict(v['claves'])} for k, v in per_cat.items()},
               'cobertura_categoria': {k: dict(v) for k, v in cov_cat.items()},
               'cobertura_marca': {f'{c}|{b}': dict(v) for (c, b), v in sorted(cover.items())}}
    json.dump(summary, open(f'{wd}/summary.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({k: summary[k] for k in ('dryrun_entradas', 'productos', 'omitidas', 'por_categoria', 'cobertura_categoria')}, ensure_ascii=False, indent=1))


main()
