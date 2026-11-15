"""Apply the PcComponentes generation rules to a Coolmod inventory."""
import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path

from pccomponentes_rules import classify

CATEGORIES = {'cpu': 'procesadores', 'gpu': 'tarjetas-graficas',
              'motherboard': 'placas-base', 'ram': 'memorias-ram',
              'case': 'cajas-pc', 'psu': 'fuentes-alimentacion',
              'storage': 'discos-duros', 'hdd': 'discos-duros', 'cooler': 'ventiladores-cpu'}
EXCLUDE_NAMES = re.compile(r'^(Pack |Portátil |Mini PC |PC Sobremesa |Reacondicionado )', re.I)


def url_key(candidate):
    return (candidate.get('url_public') or '').split('?', 1)[0].rstrip('/')


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('inventory', type=Path)
    parser.add_argument('--output', type=Path, default=Path('artifacts/coolmod-filtered.json'))
    parser.add_argument('--stage-dir', type=Path,
                        help='Write the next batch of candidates here for sync-agent')
    parser.add_argument('--staged-state', type=Path,
                        default=Path('artifacts/coolmod-staged.json'))
    parser.add_argument('--stage-limit', type=int, default=190)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    known_urls = set()
    for path in [Path('data/retailers/approved-coolmod-targets.json'), Path('artifacts/review-queue.json')]:
        if path.exists():
            for row in json.loads(path.read_text()):
                candidate = row.get('candidate', row)
                if candidate.get('shop_id', 'coolmod') == 'coolmod' and candidate.get('url_public'):
                    known_urls.add(candidate['url_public'].split('?', 1)[0].rstrip('/'))
    selected, excluded, known = [], [], []
    for candidate in inventory['candidates']:
        category = CATEGORIES.get(candidate.get('category_hint'))
        if not category:
            raise ValueError('Unknown category')
        name = candidate.get('name') or ''
        # The Coolmod SSD listing carries no 'SSD' word in many titles ("... 2.5 SATA3").
        hint = [{'name': 'Tipo', 'value': 'SSD'}] if candidate.get('category_hint') == 'storage' else None
        verdict = classify(category, name, hint)
        if EXCLUDE_NAMES.match(name):
            verdict = {'modern': False, 'generation': None, 'reason': 'pack-computer-or-refurbished'}
        if not verdict['modern']:
            excluded.append({'candidate': candidate, 'reason': verdict['reason']})
        elif candidate['url_public'].split('?', 1)[0].rstrip('/') in known_urls:
            known.append(candidate)
        else:
            selected.append({**candidate, 'raw_properties': candidate.get('raw_properties', []) + [
                {'key': 'generation', 'value': verdict['generation']},
                {'key': 'generation_rule', 'value': verdict['reason']} ]})
    staged, stage_remaining = [], 0
    if args.stage_dir:
        staged_urls = set()
        if args.staged_state.is_file():
            staged_urls = set(json.loads(args.staged_state.read_text()))
        pending = [c for c in selected if url_key(c) not in staged_urls]
        staged = pending[:max(0, args.stage_limit)]
        stage_remaining = max(0, len(pending) - len(staged))
        if staged:
            write_atomic(args.stage_dir / 'batch.json',
                         json.dumps(staged, ensure_ascii=False, indent=2) + '\n')
            staged_urls |= {url_key(c) for c in staged}
            write_atomic(args.staged_state,
                         json.dumps(sorted(staged_urls), ensure_ascii=False, indent=2) + '\n')
    report = {'category_progress': inventory['categories'], 'scanned': len(inventory['candidates']),
              'selected': len(selected), 'excluded': len(excluded), 'already_known': len(known),
              'staged': len(staged), 'stage_remaining': stage_remaining,
              'selected_by_category': dict(Counter(c['category_hint'] for c in selected)),
              'excluded_by_reason': dict(Counter(c['reason'] for c in excluded)),
              'candidates': selected, 'excluded_candidates': excluded, 'known_candidates': known}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in ('candidates', 'excluded_candidates', 'known_candidates')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
