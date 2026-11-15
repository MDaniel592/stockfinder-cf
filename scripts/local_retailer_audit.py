"""Read-only integrity and review coverage report for the two isolated retailer copies."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def checkpoint_coverage(shop):
    directory = ROOT / 'artifacts' / shop / 'checkpoints'
    categories = {}
    snapshots = set()
    invalid_files = []
    for path in directory.rglob('*.json'):
        if path.name in ('run-state.json', 'summary.json'):
            continue
        try:
            value = json.loads(path.read_text())
        except (OSError, ValueError):
            invalid_files.append(str(path.relative_to(ROOT)))
            continue
        if path.name.startswith('inventory-'):
            category = path.stem.removeprefix('inventory-')
            categories.setdefault(category, set()).update(value.get('urls', []))
        elif isinstance(value, dict) and value.get('shop_id') == shop and value.get('url_public'):
            snapshots.add(value['url_public'])
    all_urls = set().union(*categories.values()) if categories else set()
    return dict(categories=[dict(category=category, discovered=len(urls),
                                saved_observations=len(urls & snapshots),
                                without_saved_observation=len(urls - snapshots))
                            for category, urls in sorted(categories.items())],
                unique_discovered=len(all_urls), saved_observations=len(all_urls & snapshots),
                invalid_files=invalid_files)


def audit(shop):
    # Database names and SQL parameters come only from this fixed allowlist.
    if shop not in ('ldlc', 'neobyte'):
        raise ValueError('Only isolated LDLC/Neobyte copies are supported')
    query = f"""
    BEGIN READ ONLY;
    WITH offers AS (
      SELECT entity_id,data FROM catalog_entities WHERE entity='offer'
      AND deleted_at IS NULL AND data->>'shop_id'='{shop}'
    ), products AS (
      SELECT entity_id,data FROM catalog_entities WHERE entity='product' AND deleted_at IS NULL
    ), reviews AS (
      SELECT * FROM retailer_review_queue WHERE shop_id='{shop}'
    )
    SELECT json_build_object(
      'database',current_database(),
      'queue',(SELECT json_object_agg(status,n) FROM (SELECT status,count(*) n FROM reviews GROUP BY status) s),
      'categories',(SELECT json_agg(row_to_json(s)) FROM (
        SELECT candidate->>'category_hint' category,count(*) total,
          count(*) FILTER (WHERE status='approved') approved,
          count(*) FILTER (WHERE status='pending') pending,
          count(*) FILTER (WHERE status='pending' AND review_note LIKE 'Pendiente:%') retained,
          count(*) FILTER (WHERE status='pending' AND coalesce(review_note,'') NOT LIKE 'Pendiente:%') unreviewed
        FROM reviews GROUP BY 1 ORDER BY 1) s),
      'offers',(SELECT count(*) FROM offers),
      'orphan_offers',(SELECT count(*) FROM offers o WHERE NOT EXISTS
        (SELECT 1 FROM products p WHERE p.entity_id=o.data->>'product_id')),
      'duplicate_offer_urls',(SELECT count(*) FROM (
        SELECT data->>'url_public' FROM offers GROUP BY 1 HAVING count(*)>1) s),
      'duplicate_listing_skus',(SELECT count(*) FROM (
        SELECT retailer_sku FROM retailer_listings WHERE shop_id='{shop}' GROUP BY 1 HAVING count(*)>1) s),
      'oldest_observation',(SELECT min(data->>'last_checked_at') FROM offers),
      'newest_observation',(SELECT max(data->>'last_checked_at') FROM offers),
      'pending_brands',(SELECT json_agg(row_to_json(s)) FROM (
        SELECT candidate->>'category_hint' category,candidate->>'brand' brand,count(*) n
        FROM reviews WHERE status='pending' GROUP BY 1,2 ORDER BY count(*) DESC,1,2) s)
    );
    ROLLBACK;
    """
    output = subprocess.check_output([
        'docker', 'exec', 'stockfinder-v2-postgres-1', 'psql', '-X', '-qAt',
        '-v', 'ON_ERROR_STOP=1', '-U', 'stockfinder', '-d', f'stockfinder_{shop}_local', '-c', query,
    ], text=True)
    report = json.loads(output)
    report['checked_at'] = datetime.now(timezone.utc).isoformat()
    report['checkpoint_coverage'] = checkpoint_coverage(shop)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shop', choices=('ldlc', 'neobyte', 'all'), default='all')
    args = parser.parse_args()
    shops = ('ldlc', 'neobyte') if args.shop == 'all' else (args.shop,)
    for shop in shops:
        report = audit(shop)
        target = ROOT / 'artifacts' / shop / 'integrity-audit.json'
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(target)
        print(json.dumps(dict(shop=shop, **report), ensure_ascii=False))


if __name__ == '__main__':
    main()
