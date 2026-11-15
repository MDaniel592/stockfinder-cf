"""Import checked retailer snapshots with exact verified MPN/EAN identity."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from catalog import write
from import_catalog import normalize

DOMAINS = {'pccomponentes': 'www.pccomponentes.com', 'coolmod':'www.coolmod.com', 'ldlc':'www.ldlc.com', 'neobyte':'www.neobyte.es'}

def import_offer(conn, offer):
    shop = offer['shop_id']
    if shop not in DOMAINS: raise ValueError('Unconfigured source')
    url = urlparse(offer['url_public'])
    if url.scheme != 'https' or url.hostname != DOMAINS[shop] or url.username or url.password:
        raise ValueError('Offer must use the exact configured HTTPS store domain')
    # `unavailable`: la tienda indica de forma EXPLICITA que la ficha ya no existe o esta agotada sin precio.
    # Solo se marca in_stock=false conservando el ultimo precio publicado; nunca se borra la oferta.
    unavailable = offer.get('unavailable') is True
    if unavailable:
        if offer.get('in_stock') is not False: raise ValueError('An unavailable offer must have in_stock=false')
    else:
        if not isinstance(offer['price_cents'],int) or isinstance(offer['price_cents'],bool) or not 0<=offer['price_cents']<=100000000:
            raise ValueError('Price must be integer EUR cents')
        if not isinstance(offer['in_stock'],bool): raise ValueError('Explicit checked stock required')
    checked = datetime.fromisoformat(offer['last_checked_at'].replace('Z','+00:00'))
    if checked.tzinfo is None or checked > datetime.now(timezone.utc)+timedelta(minutes=5):
        raise ValueError('Invalid observation timestamp')
    product = conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id=%s AND deleted_at IS NULL", (offer['product_id'],)).fetchone()
    if not product: raise ValueError('Unknown canonical product')
    matched = None
    for kind, scope in [('mpn',product[0]['brand']),('ean','global')]:
        if not offer.get(kind): continue
        row = conn.execute('SELECT product_id FROM product_identifiers WHERE kind=%s AND scope=%s AND value=%s',
                           (kind,scope,normalize(offer[kind]))).fetchone()
        if row and row[0]!=offer['product_id']: raise ValueError('Conflicting verified product identifier')
        if row and row[0]==offer['product_id']: matched = (kind, normalize(offer[kind])); break
    if matched is None: raise ValueError('No exact verified MPN/EAN match; refusing a fuzzy association')
    sku = str(offer['retailer_sku'])
    current = conn.execute('SELECT product_id,checked_at FROM retailer_listings WHERE shop_id=%s AND retailer_sku=%s', (shop,sku)).fetchone()
    if current and current[0]!=offer['product_id']: raise ValueError('Retailer SKU already belongs to a different product')
    if current and checked < current[1]: raise ValueError('An older observation cannot replace newer retailer data')
    identity = shop+'-'+hashlib.sha256(sku.encode()).hexdigest()[:24]
    previous = None
    if unavailable:
        previous = conn.execute("SELECT data FROM catalog_entities WHERE entity='offer' AND entity_id=%s AND deleted_at IS NULL",(identity,)).fetchone()
        if not previous: raise ValueError('No published offer to mark unavailable')
    evidence = offer.get('match_evidence','').strip()
    if not evidence: raise ValueError('Match evidence required')
    conn.execute('''INSERT INTO retailer_listings(shop_id,retailer_sku,product_id,match_kind,match_value,match_evidence,checked_at)
      VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(shop_id,retailer_sku) DO UPDATE SET
      match_evidence=excluded.match_evidence,checked_at=excluded.checked_at''',
      (shop,sku,offer['product_id'],*matched,evidence,checked))
    keys=['product_id','shop_id','price_cents','in_stock','last_checked_at','url_public']
    if unavailable:
        data={**previous[0],'in_stock':False,'last_checked_at':offer['last_checked_at']}
    else:
        data={key:offer[key] for key in keys}
    write(conn,'offer',identity,data)
    return identity

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('file',type=Path)
    args=parser.parse_args()
    offers=json.loads(args.file.read_text())
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        for offer in offers: import_offer(conn,offer)
    print(json.dumps(dict(offers_imported=len(offers))))

if __name__=='__main__':main()
