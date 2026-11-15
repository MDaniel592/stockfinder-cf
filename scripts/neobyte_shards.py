"""Split pending Neobyte review rows (artifacts/neobyte/pending.json) into agent shards.

Groups by category and brand (a brand stays together when it fits) and never overwrites
an existing shard. Reads and writes only artifacts/neobyte/.
"""
import argparse
import json
from collections import defaultdict
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def slug(value):
    return re.sub(r'[^a-z0-9]+', '-', (value or 'unknown').lower()).strip('-') or 'unknown'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size', type=int, default=40)
    parser.add_argument('--directory', type=Path, default=ROOT / 'artifacts/neobyte')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    rows = json.loads((args.directory / 'pending.json').read_text())
    # Skip rows an agent already retained with a reason; they need new evidence, not another pass.
    rows = [r for r in rows if not (r.get('review_note') or '').startswith('Pendiente:')]
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows:
        c = row['candidate']
        groups[c['category_hint']][slug(c.get('brand'))].append(row)
    shards = []
    for category, brands in sorted(groups.items()):
        current = []
        for brand, items in sorted(brands.items(), key=lambda b: -len(b[1])):
            while len(items) > args.size:
                shards.append((category, items[:args.size])); items = items[args.size:]
            if len(current) + len(items) > args.size:
                shards.append((category, current)); current = []
            current += items
        if current:
            shards.append((category, current))
    out = args.directory / 'shards'
    out.mkdir(exist_ok=True)
    counters = defaultdict(int)
    for category, items in shards:
        counters[category] += 1
        name = f'{category}-{counters[category]:02d}'
        if not args.dry_run and not (out / f'{name}.json').exists():
            (out / f'{name}.json').write_text(json.dumps(items, ensure_ascii=False, indent=1) + '\n')
        print(name, len(items), sorted({slug(i['candidate'].get('brand')) for i in items})[:6])
    print('rows', len(rows), 'shards', len(shards))


if __name__ == '__main__':
    main()
