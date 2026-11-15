"""Normalize GPU chip spellings through the production outbox. Dry-run unless --apply."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nas'))
import psycopg
from catalog import write
from spec_normalize import normalize_gpu_chip

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', default=os.environ.get('DATABASE_URL'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup-dir', type=Path, required=True)
    args = parser.parse_args()
    if not args.database_url: parser.error('--database-url or DATABASE_URL required')
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    changes, counts = [], Counter()
    with psycopg.connect(args.database_url) as conn:
        rows = conn.execute("SELECT entity_id,data,version FROM catalog_entities WHERE entity='product' AND deleted_at IS NULL AND data->>'category_id'='gpu' ORDER BY entity_id FOR UPDATE").fetchall()
        for pid, data, version in rows:
            old = data.get('specs', {}).get('Chip gráfico')
            if not isinstance(old, str): continue
            new = normalize_gpu_chip(old)
            if new == old: continue
            changes.append(dict(id=pid, version=version, before=data, chip_before=old, chip_after=new))
            counts[(old,new)] += 1
        report = dict(applied=args.apply, checked_at=datetime.now(timezone.utc).isoformat(), gpu_products=len(rows), changed_products=len(changes),
                      mapping=[dict(before=a,after=b,count=n) for (a,b),n in sorted(counts.items())])
        if args.apply:
            backup = args.backup_dir / 'products-before.json'
            if backup.exists(): raise ValueError('Use a new backup directory; an existing backup is never overwritten')
            backup.write_text(json.dumps(changes,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            for change in changes:
                data = change['before']
                write(conn,'product',change['id'],{**data,'specs':{**data['specs'],'Chip gráfico':change['chip_after']}})
        else:
            conn.rollback()
    source_files = []
    if args.apply:
        for path in sorted((ROOT/'data/catalog').glob('*.json')):
            original=path.read_text(encoding='utf-8'); records=json.loads(original); changed=False
            for product in records:
                if product.get('category_id')!='gpu': continue
                specs=product.get('specs') or {}
                for key in ('Chip gráfico','GPU'):
                    if isinstance(specs.get(key),str):
                        new=normalize_gpu_chip(specs[key])
                        if new!=specs[key]: specs[key]=new;changed=True
            if changed:
                target=args.backup_dir/'source'/path.name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(original,encoding='utf-8')
                temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');os.chmod(temporary,0o644);temporary.replace(path)
                source_files.append(str(path.relative_to(ROOT)))
    report['source_files']=source_files
    (args.backup_dir/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__': main()
