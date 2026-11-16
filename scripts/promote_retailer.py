"""Promote one retailer from its isolated review database into the production catalog.

Read-only unless --apply. Copies only what the retailer's APPROVED, active offers need:
new canonical products (never overwrites existing ones), their identifiers, the offers,
and the approved/rejected queue + classification + listing rows. Pending rows stay behind.
Everything goes through catalog.write so the outbox/sync-agent publish it as usual.
"""
import argparse, json, os, sys
from pathlib import Path
import psycopg
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'nas'))
from catalog import write  # noqa: E402


def load(src, shop):
    offers = {r[0]: r[1] for r in src.execute(
        "select entity_id, data from catalog_entities where entity='offer' and deleted_at is null and data->>'shop_id'=%s", (shop,))}
    pids = {o['product_id'] for o in offers.values()}
    products = {r[0]: r[1] for r in src.execute(
        "select entity_id, data from catalog_entities where entity='product' and deleted_at is null")}
    idents = src.execute("select kind, scope, value, product_id from product_identifiers").fetchall()
    return offers, pids, products, idents


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--shop', required=True)
    ap.add_argument('--source-url', required=True)
    ap.add_argument('--dest-url', required=True)
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--report', type=Path)
    a = ap.parse_args()
    src = psycopg.connect(a.source_url, autocommit=False)
    dst = psycopg.connect(a.dest_url, autocommit=False)
    src.read_only = True
    offers, pids, sproducts, sidents = load(src, a.shop)
    dprod = {r[0]: r[1] for r in dst.execute("select entity_id, data from catalog_entities where entity='product' and deleted_at is null")}
    didents = {(k, s, v): p for k, s, v, p in dst.execute("select kind, scope, value, product_id from product_identifiers")}
    doffer_urls = {r[0] for r in dst.execute("select data->>'url_public' from catalog_entities where entity='offer' and deleted_at is null and data->>'shop_id'=%s", (a.shop,))}
    missing = sorted(p for p in pids if p not in sproducts)
    tombstoned = {r[0] for r in dst.execute("select entity_id from catalog_entities where entity='product' and deleted_at is not null")}
    # Products deliberately removed from production stay removed (and their offers are not promoted).
    pids = {p for p in pids if p not in tombstoned}
    offers = {e: o for e, o in offers.items() if o['product_id'] not in tombstoned}
    new = sorted(p for p in pids if p in sproducts and p not in dprod)
    existing = sorted(p for p in pids if p in dprod)
    changed_existing = [p for p in existing if sproducts[p] != dprod[p]]
    new_set = set(new)
    ident_rows = [(k, s, v, p) for k, s, v, p in sidents if p in new_set or p in set(existing)]
    ident_new, ident_conflict = [], []
    for k, s, v, p in ident_rows:
        cur = didents.get((k, s, v))
        if cur is None: ident_new.append((k, s, v, p))
        elif cur != p: ident_conflict.append(dict(kind=k, scope=s, value=v, source_product=p, prod_product=cur))
    # EAN/MPN of a NEW product that belongs to another prod product under a different kind of key
    ean_dups = []
    for p in new:
        ean = sproducts[p].get('ean')
        if ean and didents.get(('ean', 'global', str(ean))) not in (None, p): ean_dups.append(p)
    url_dup = [e for e, o in offers.items() if o.get('url_public') in doffer_urls]
    bad_price = [e for e, o in offers.items() if not isinstance(o.get('price_cents'), int) or o['price_cents'] <= 0 or not isinstance(o.get('in_stock'), bool)]
    report = dict(shop=a.shop, offers=len(offers), distinct_products=len(pids), new_products=len(new), existing_products=len(existing),
                  existing_products_that_differ_locally=len(changed_existing), offers_with_missing_product=len(missing),
                  identifiers_to_add=len(ident_new), identifier_conflicts=ident_conflict[:50], identifier_conflict_count=len(ident_conflict),
                  new_products_ean_owned_by_other=ean_dups, offers_already_in_prod=len(url_dup), offers_bad_price_or_stock=len(bad_price))
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if a.report: a.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    if not a.apply:
        return
    if ident_conflict or ean_dups or missing or bad_price:
        raise SystemExit('Refusing to apply: resolve conflicts first')
    with dst.transaction():
        for p in new: write(dst, 'product', p, sproducts[p])
        for k, s, v, p in ident_new:
            dst.execute("insert into product_identifiers(kind,scope,value,product_id) values(%s,%s,%s,%s) on conflict do nothing", (k, s, v, p))
        for eid, data in offers.items(): write(dst, 'offer', eid, data)
        for r in src.execute("select shop_id,retailer_sku,product_id,match_kind,match_value,match_evidence,checked_at from retailer_listings where shop_id=%s", (a.shop,)):
            dst.execute("insert into retailer_listings values(%s,%s,%s,%s,%s,%s,%s) on conflict do nothing", r)
        for r in src.execute("select shop_id,retailer_sku,status,product_id,mpn,ean,review_note,reviewed_at from retailer_classifications where shop_id=%s and status in ('approved','rejected')", (a.shop,)):
            dst.execute("insert into retailer_classifications values(%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing", r)
        for r in src.execute("select shop_id,retailer_sku,fingerprint,candidate,proposed_product_id,status,review_note,first_seen_at,last_seen_at,reviewed_at from retailer_review_queue where shop_id=%s and status in ('approved','rejected')", (a.shop,)):
            exists = dst.execute("select 1 from retailer_review_queue where shop_id=%s and retailer_sku=%s and fingerprint=%s", r[:3]).fetchone()
            if not exists:
                dst.execute("insert into retailer_review_queue(shop_id,retailer_sku,fingerprint,candidate,proposed_product_id,status,review_note,first_seen_at,last_seen_at,reviewed_at) values(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            (*r[:3], psycopg.types.json.Jsonb(r[3]), *r[4:]))
    dst.commit()
    print('APPLIED')


if __name__ == '__main__':
    main()
