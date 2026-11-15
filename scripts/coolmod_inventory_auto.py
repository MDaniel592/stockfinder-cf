"""Run the Coolmod inventory until complete, then filter and stage candidates.

The inventory script (scripts/coolmod_categories.py with curl_cffi, or the legacy
Playwright coolmod-categories.cjs) is resumable and writes progress after every
page. This wrapper keeps calling it while any category is unfinished, then
applies the shared generation rules and writes the next review batch. It is
designed to run as a Docker service, independent of any interactive session.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CATEGORIES = ('cpu', 'gpu', 'motherboard', 'ram', 'case', 'psu', 'storage', 'hdd', 'cooler')


def log(event, **data):
    print(json.dumps({'event': event, **data}, ensure_ascii=False), flush=True)


def write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(text)
    os.replace(temporary, path)


def refresh_due(path, hours):
    if hours <= 0:
        return False
    if not path.is_file():
        write_atomic(path, json.dumps({'last_refresh': int(time.time())}) + '\n')
        return False
    data = json.loads(path.read_text())
    return int(time.time()) - int(data.get('last_refresh', 0)) >= hours * 3600


def reset_inventory_progress(path):
    data = json.loads(path.read_text())
    for progress in (data.get('categories') or {}).values():
        progress['last_url'] = None
        progress['pages'] = 0
        for key in ('complete', 'exhausted', 'end_reason'):
            progress.pop(key, None)
    write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def inventory_complete(path):
    if not path.is_file():
        return False
    data = json.loads(path.read_text())
    categories = data.get('categories') or {}
    return all(categories.get(name, {}).get('complete') or
               categories.get(name, {}).get('exhausted')
               for name in EXPECTED_CATEGORIES)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path,
                        default=Path('artifacts/coolmod-inventory.json'))
    parser.add_argument('--scraper', type=Path,
                        default=ROOT / 'nas/scrapers/coolmod-categories.cjs')
    parser.add_argument('--filtered', type=Path,
                        default=Path('artifacts/coolmod-filtered.json'))
    parser.add_argument('--stage-dir', type=Path, default=Path('/output/coolmod'))
    parser.add_argument('--staged-state', type=Path,
                        default=Path('artifacts/coolmod-staged.json'))
    parser.add_argument('--pages', type=int, default=200,
                        help='Maximum category pages per inventory run')
    parser.add_argument('--stage-limit', type=int, default=190,
                        help='Candidates written per batch (review queue accepts 200)')
    parser.add_argument('--interval', type=int, default=86400,
                        help='Seconds between completed inventory/filter cycles')
    parser.add_argument('--stage-interval', type=int, default=900,
                        help='Seconds between batches while candidates remain')
    parser.add_argument('--refresh-hours', type=int, default=168,
                        help='Full rescan interval after completion (0 disables)')
    parser.add_argument('--refresh-state', type=Path,
                        default=Path('artifacts/coolmod-refresh.json'))
    parser.add_argument('--retry', type=int, default=30,
                        help='Seconds between partial inventory runs')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()

    environment = os.environ.copy()
    environment['COOLMOD_PAGES'] = str(args.pages)
    environment['COOLMOD_INVENTORY'] = str(args.inventory)

    while True:
        try:
            # .py = inventario sin navegador (scripts/coolmod_categories.py); .cjs = Playwright (rollback).
            runner = [sys.executable] if str(args.scraper).endswith('.py') else ['node']
            subprocess.run(runner + [str(args.scraper)], check=True, env=environment)
            if inventory_complete(args.inventory):
                subprocess.run(
                    [sys.executable, str(ROOT / 'scripts/coolmod_filter.py'),
                     str(args.inventory),
                     '--output', str(args.filtered),
                     '--stage-dir', str(args.stage_dir),
                     '--staged-state', str(args.staged_state),
                     '--stage-limit', str(args.stage_limit)],
                    check=True, env=environment)
                log('inventory_complete', inventory=str(args.inventory))
                report = json.loads(args.filtered.read_text())
                remaining = report.get('stage_remaining', 0)
                log('batch', staged=report.get('staged', 0), remaining=remaining)
                if refresh_due(args.refresh_state, args.refresh_hours):
                    reset_inventory_progress(args.inventory)
                    write_atomic(args.refresh_state,
                                 json.dumps({'last_refresh': int(time.time())}) + '\n')
                    log('inventory_refresh', inventory=str(args.inventory))
                    if args.once:
                        return
                    continue
                if args.once:
                    return
                time.sleep(args.stage_interval if remaining else args.interval)
            else:
                log('inventory_pending', inventory=str(args.inventory))
                if args.once:
                    return
                time.sleep(args.retry)
        except Exception as error:
            log('failure', type=type(error).__name__, detail=str(error)[:200])
            if args.once:
                raise SystemExit(1) from None
            time.sleep(max(30, args.retry * 2))


if __name__ == '__main__':
    main()
