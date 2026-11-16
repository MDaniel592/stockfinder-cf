"""Durable staging queue: collecting a retailer record never publishes a product."""
import argparse
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime,timezone
from urllib.parse import urlparse

import psycopg
from psycopg.types.json import Jsonb
from import_catalog import normalize
from import_offers import import_offer, DOMAINS

ROOT=Path(__file__).resolve().parents[1]

def _checked_at(candidate):
    value=candidate.get('last_checked_at')
    if not value:return None
    try:
        checked=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return checked.replace(tzinfo=timezone.utc) if checked.tzinfo is None else checked
    except ValueError:return None

def _observation_source(candidate,shop):
    source=candidate.get('commercial_observation_source') or candidate.get('source')
    if source:return str(source)
    for item in reversed(candidate.get('raw_properties') or []):
        if isinstance(item,dict) and item.get('key') in ('commercial_observation_source','observation_source','source') and item.get('value'):
            return str(item['value'])
    return f'{shop} retailer observation with structured price and stock'

def _merge_pending_candidate(existing,incoming,shop):
    """Keep stronger observations and human evidence when a listing refresh is sparse."""
    old=dict(existing or {})
    new=dict(incoming)
    merged=dict(old)
    for key,value in new.items():
        if key in ('raw_properties','missing_fields','price_cents','in_stock','last_checked_at'):
            continue
        if value is not None and value!='':merged[key]=value

    # A retailer list refresh may omit identifiers and commercial fields that a PDP
    # observation supplied earlier. Do not erase them with nulls or partial snapshots.
    old_mpn=str(old.get('mpn') or '').strip()
    new_mpn=str(new.get('mpn') or '').strip()
    if new_mpn:
        merged['mpn']=new_mpn
        if old_mpn and normalize(old_mpn)!=normalize(new_mpn) and (old.get('manufacturer_url') or old.get('identity_status')):
            merged['manufacturer_verified_mpn']=old_mpn
            merged['identity_status']='retailer_mpn_changed_requires_review'
            merged['missing_fields']=list(dict.fromkeys([*(old.get('missing_fields') or []),'manufacturer_variant_conflict']))
    elif old_mpn:
        merged['mpn']=old_mpn

    # Keep the last complete price/stock observation unless the new one is complete
    # and at least as recent. A marketplace-only result explicitly clears own stock.
    old_price,old_stock=old.get('price_cents'),old.get('in_stock')
    new_price,new_stock=new.get('price_cents'),new.get('in_stock')
    old_complete=isinstance(old_price,int) and not isinstance(old_price,bool) and isinstance(old_stock,bool)
    new_complete=isinstance(new_price,int) and not isinstance(new_price,bool) and isinstance(new_stock,bool)
    old_checked,new_checked=_checked_at(old),_checked_at(new)
    newer=(not old_checked or not new_checked or new_checked>=old_checked)
    marketplace_only='direct_seller_verification' in (new.get('missing_fields') or [])
    if marketplace_only:
        merged['in_stock']=None
        merged['missing_fields']=list(dict.fromkeys([*(merged.get('missing_fields') or []),'in_stock','direct_seller_verification']))
        if isinstance(new_price,int) and not isinstance(new_price,bool):merged['price_cents']=new_price
        elif old_price is not None:merged['price_cents']=old_price
        if new_checked:
            merged['last_checked_at']=new.get('last_checked_at')
            merged['commercial_observed_at']=new.get('commercial_observed_at') or new.get('last_checked_at')
            merged['commercial_observation_source']=_observation_source(new,shop)
    elif new_complete and newer:
        merged['price_cents']=new_price
        merged['in_stock']=new_stock
        if new_checked:
            merged['last_checked_at']=new.get('last_checked_at')
            merged['commercial_observed_at']=new.get('commercial_observed_at') or new.get('last_checked_at')
            merged['commercial_observation_source']=_observation_source(new,shop)
    elif old_complete:
        merged['price_cents']=old_price
        merged['in_stock']=old_stock
        if old.get('last_checked_at'):merged['last_checked_at']=old.get('last_checked_at')
    else:
        if isinstance(new_price,int) and not isinstance(new_price,bool):merged['price_cents']=new_price
        elif old_price is not None:merged['price_cents']=old_price
        if isinstance(new_stock,bool):merged['in_stock']=new_stock
        elif 'in_stock' in old:merged['in_stock']=old_stock
        if new_checked and not old_checked:merged['last_checked_at']=new.get('last_checked_at')

    # Official evidence is additive and must survive retailer-only refreshes.
    for key in ('manufacturer_url','official_sources','verified_specifications','identity_status',
                'manufacturer_identity_verified_at','official_product_name','manufacturer_verified_mpn'):
        if key=='identity_status' and merged.get(key)=='retailer_mpn_changed_requires_review':continue
        if old.get(key) and not new.get(key):merged[key]=old[key]
    for key in ('ean','name','brand','retailer_sku','url_public'):
        if not merged.get(key) and old.get(key):merged[key]=old[key]

    props=[]
    seen=set()
    for item in [*(old.get('raw_properties') or []),*(new.get('raw_properties') or [])]:
        marker=json.dumps(item,sort_keys=True,ensure_ascii=False,default=str)
        if marker not in seen:seen.add(marker);props.append(item)
    if props:merged['raw_properties']=props

    missing=list(dict.fromkeys([*(old.get('missing_fields') or []),*(new.get('missing_fields') or [])]))
    if merged.get('identity_status')=='retailer_mpn_changed_requires_review':missing.append('manufacturer_variant_conflict')
    if merged.get('ean'):missing=[x for x in missing if x!='ean']
    if merged.get('retailer_sku') and not str(merged['retailer_sku']).startswith('https://'):missing=[x for x in missing if x!='retailer_sku']
    if merged.get('mpn'):missing=[x for x in missing if x!='mpn']
    if merged.get('manufacturer_url'):missing=[x for x in missing if x!='manufacturer_url']
    if merged.get('verified_specifications'):missing=[x for x in missing if x!='verified_specifications']
    if isinstance(merged.get('price_cents'),int) and not isinstance(merged.get('price_cents'),bool):missing=[x for x in missing if x not in ('price_cents','price')]
    if isinstance(merged.get('in_stock'),bool):missing=[x for x in missing if x not in ('in_stock','stock')]
    merged['missing_fields']=missing
    return merged

def migrate(conn):
    conn.execute((ROOT/'db/postgres-migrations/0003_review_queue.sql').read_text())
    conn.execute((ROOT/'db/postgres-migrations/0004_persistent_classification.sql').read_text())

def classify(conn,candidate,status,product_id,note):
    conn.execute('''INSERT INTO retailer_classifications(shop_id,retailer_sku,status,product_id,mpn,ean,review_note)
      VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(shop_id,retailer_sku) DO UPDATE SET
      status=excluded.status,product_id=excluded.product_id,mpn=excluded.mpn,ean=excluded.ean,
      review_note=excluded.review_note,reviewed_at=clock_timestamp()''',
      (candidate['shop_id'],str(candidate.get('retailer_sku') or candidate['url_public']),status,product_id,
       normalize(str(candidate.get('mpn') or '')),str(candidate.get('ean') or ''),note))

def stage_candidates(conn,candidates):
    if not isinstance(candidates,list) or len(candidates)>200: raise ValueError('Candidate batch limit exceeded')
    ids=[]
    for candidate in candidates:
        if not isinstance(candidate,dict) or len(json.dumps(candidate))>50000:raise ValueError('Invalid candidate')
        shop=candidate.get('shop_id')
        if shop not in DOMAINS or urlparse(candidate.get('url_public','')).hostname!=DOMAINS[shop]:raise ValueError('Unconfigured origin')
        if not candidate['url_public'].startswith('https://'):raise ValueError('HTTPS required')
        sku=str(candidate.get('retailer_sku') or candidate['url_public'])
        # Listing pages often omit SKU: reconcile URL-only discoveries with later product detail.
        url_key=candidate['url_public'].split('?',1)[0].split('#',1)[0].rstrip('/')
        if sku.startswith('https://'):
            linked=conn.execute("""SELECT l.retailer_sku FROM retailer_listings l
              JOIN catalog_entities e ON e.entity='offer'
               AND e.entity_id=l.shop_id||'-'||left(encode(sha256(convert_to(l.retailer_sku,'UTF8')),'hex'),24)
              WHERE l.shop_id=%s AND rtrim(split_part(e.data->>'url_public','?',1),'/')=%s LIMIT 1""",(shop,url_key)).fetchone()
            if linked:sku=linked[0];candidate={**candidate,'retailer_sku':sku}
        known=conn.execute("""SELECT review_id,retailer_sku FROM retailer_review_queue
          WHERE shop_id=%s AND rtrim(split_part(split_part(candidate->>'url_public','?',1),'#',1),'/')=%s
          ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,review_id LIMIT 1""",(shop,url_key)).fetchone()
        if known and sku.startswith('https://'):
            sku=known[1];candidate={**candidate,'retailer_sku':sku}
        elif known and known[1].startswith('https://') and not sku.startswith('https://'):
            conn.execute('UPDATE retailer_review_queue SET retailer_sku=%s WHERE review_id=%s',(sku,known[0]))
            conn.execute('''INSERT INTO retailer_classifications(shop_id,retailer_sku,status,product_id,mpn,ean,review_note)
              SELECT shop_id,%s,status,product_id,mpn,ean,review_note FROM retailer_classifications WHERE shop_id=%s AND retailer_sku=%s
              ON CONFLICT(shop_id,retailer_sku) DO NOTHING''',(sku,shop,known[1]))
        decision=conn.execute('SELECT status,product_id,mpn,ean,review_note FROM retailer_classifications WHERE shop_id=%s AND retailer_sku=%s',(shop,sku)).fetchone()
        if decision and decision[0]=='rejected':continue
        if decision and decision[0]=='approved':
            mpn=normalize(str(candidate.get('mpn') or ''))
            if mpn and mpn==decision[2]:
                # Classification is permanent; later observations only refresh the linked offer.
                if isinstance(candidate.get('price_cents'),int) and isinstance(candidate.get('in_stock'),bool) and 'direct_seller_verification' not in candidate.get('missing_fields',[]):
                    current=conn.execute('SELECT checked_at FROM retailer_listings WHERE shop_id=%s AND retailer_sku=%s',(shop,sku)).fetchone()
                    from datetime import datetime
                    observed=datetime.fromisoformat(candidate['last_checked_at'].replace('Z','+00:00'))
                    if not current or observed>=current[0]:
                        try:
                            # Savepoint: a failed refresh must not abort the whole crawl or poison the transaction.
                            with conn.transaction():
                                import_offer(conn,{**candidate,'product_id':decision[1],'match_evidence':decision[4]})
                        except ValueError as error:
                            print(json.dumps(dict(event='offer_refresh_skipped',shop=shop,retailer_sku=sku,url=candidate.get('url_public'),mpn=candidate.get('mpn'),product_id=decision[1],reason=str(error)),ensure_ascii=False),flush=True)
                continue
            if not mpn:continue
            classify(conn,candidate,'pending',None,'La identidad MPN del SKU ha cambiado; requiere nueva revisión.')
        # Repeated checks with the same commercial data do not grow the review queue.
        stable={k:v for k,v in candidate.items() if k not in ('last_checked_at','collected_at','match_evidence')}
        fingerprint=hashlib.sha256(json.dumps(stable,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        proposed=None
        if candidate.get('mpn'):
            matches=conn.execute("SELECT DISTINCT product_id FROM product_identifiers WHERE kind='mpn' AND value=%s",(normalize(candidate['mpn']),)).fetchall()
            if len(matches)==1:proposed=matches[0][0]
        if proposed is None and candidate.get('ean'):
            match=conn.execute("SELECT product_id FROM product_identifiers WHERE kind='ean' AND scope='global' AND value=%s",(normalize(candidate['ean']),)).fetchone()
            if match:proposed=match[0]
        pending=conn.execute("SELECT review_id,candidate,proposed_product_id FROM retailer_review_queue WHERE shop_id=%s AND retailer_sku=%s AND status='pending' ORDER BY review_id LIMIT 1 FOR UPDATE",(shop,sku)).fetchone()
        if pending:
            candidate=_merge_pending_candidate(pending[1],candidate,shop)
            if candidate.get('mpn'):
                matches=conn.execute("SELECT DISTINCT product_id FROM product_identifiers WHERE kind='mpn' AND value=%s",(normalize(candidate['mpn']),)).fetchall()
                proposed=matches[0][0] if len(matches)==1 else None
            if proposed is None and candidate.get('ean'):
                match=conn.execute("SELECT product_id FROM product_identifiers WHERE kind='ean' AND scope='global' AND value=%s",(normalize(candidate['ean']),)).fetchone()
                if match:proposed=match[0]
            if proposed is None and normalize(str(candidate.get('mpn') or ''))==normalize(str(pending[1].get('mpn') or '')):
                proposed=pending[2]
            conn.execute('UPDATE retailer_review_queue SET candidate=%s,proposed_product_id=%s,last_seen_at=clock_timestamp() WHERE review_id=%s',(Jsonb(candidate),proposed,pending[0]))
            ids.append(pending[0]);continue
        row=conn.execute('''INSERT INTO retailer_review_queue(shop_id,retailer_sku,fingerprint,candidate,proposed_product_id)
          VALUES(%s,%s,%s,%s,%s) ON CONFLICT(shop_id,retailer_sku,fingerprint)
          DO UPDATE SET last_seen_at=clock_timestamp(),candidate=excluded.candidate,
          proposed_product_id=COALESCE(retailer_review_queue.proposed_product_id,excluded.proposed_product_id)
          RETURNING review_id''',(shop,sku,fingerprint,Jsonb(candidate),proposed)).fetchone()
        ids.append(row[0])
    return ids

def approve(conn,identity,note,product_id=None):
    if not note.strip():raise ValueError('Review evidence required')
    row=conn.execute('SELECT candidate,proposed_product_id,status FROM retailer_review_queue WHERE review_id=%s FOR UPDATE',(identity,)).fetchone()
    if not row:raise ValueError('Review not found')
    if row[2]=='approved':return None
    candidate_data=row[0]
    if ('manufacturer_variant_conflict' in (candidate_data.get('missing_fields') or []) or
        candidate_data.get('identity_status') in ('retailer_mpn_changed_requires_review','retailer_ean_variant_conflict')):
        raise ValueError('Retailer identity has an unresolved manufacturer variant conflict')
    canonical=product_id or row[1]
    if row[2]!='pending' or not canonical:raise ValueError('No unambiguous verified canonical identity; complete manufacturer data first')
    candidate={**row[0],'product_id':canonical,'match_evidence':note}
    product=conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id=%s AND deleted_at IS NULL",(canonical,)).fetchone()
    if not product:raise ValueError('Canonical product absent')
    mpn=normalize(str(candidate.get('mpn') or ''));ean=normalize(str(candidate.get('ean') or ''))
    if not mpn and not ean:raise ValueError('Observación sin MPN/EAN: no se aprueba por nombre')
    # Store-code aliases (data/aliases) are registered as mpn identifiers whose value is the shop code.
    matches=[]
    if mpn:matches.append(conn.execute("SELECT product_id FROM product_identifiers WHERE kind='mpn' AND scope=%s AND value=%s",(product[0]['brand'],mpn)).fetchone())
    if ean:matches.append(conn.execute("SELECT product_id FROM product_identifiers WHERE kind='ean' AND scope='global' AND value=%s",(ean,)).fetchone())
    if not any(m and m[0]==canonical for m in matches):raise ValueError('Verified identifier does not match the canonical product')
    eligible=isinstance(candidate.get('price_cents'),int) and isinstance(candidate.get('in_stock'),bool) and 'direct_seller_verification' not in candidate.get('missing_fields',[])
    imported=import_offer(conn,candidate) if eligible else None
    classify(conn,candidate,'approved',canonical,note)
    conn.execute("UPDATE retailer_review_queue SET status='approved',proposed_product_id=%s,review_note=%s,reviewed_at=clock_timestamp() WHERE review_id=%s",(canonical,note,identity))
    return imported

def reject(conn,identity,note):
    if not note.strip():raise ValueError('Exclusion reason required')
    row=conn.execute("SELECT candidate FROM retailer_review_queue WHERE review_id=%s AND status='pending' FOR UPDATE",(identity,)).fetchone()
    if not row:return
    classify(conn,row[0],'rejected',None,note)
    conn.execute("UPDATE retailer_review_queue SET status='rejected',review_note=%s,reviewed_at=clock_timestamp() WHERE shop_id=%s AND retailer_sku=%s AND status='pending'",(note,row[0]['shop_id'],str(row[0].get('retailer_sku') or row[0]['url_public'])))

def bulk(conn,decisions,dry_run=False):
    """Apply [{review_id, action: approve|reject, product_id?, note}] in one run.
    Each decision uses a savepoint: a failure is reported and skipped, never aborts the batch."""
    result={'approved':0,'rejected':0,'offers':0,'errors':[]}
    for d in decisions:
        try:
            with conn.transaction():
                if d.get('action','approve')=='reject':
                    reject(conn,int(d['review_id']),d.get('note',''));result['rejected']+=1
                else:
                    offer=approve(conn,int(d['review_id']),d.get('note',''),d.get('product_id'))
                    result['approved']+=1;result['offers']+=bool(offer)
        except Exception as error:result['errors'].append({'review_id':d.get('review_id'),'error':str(error)})
    if dry_run:conn.rollback()
    return result

def export(conn):
    rows=conn.execute('''SELECT review_id,status,proposed_product_id,candidate,review_note,first_seen_at,last_seen_at
      FROM retailer_review_queue ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,review_id''').fetchall()
    output=[]
    for row in rows:
        item=dict(zip(['review_id','status','proposed_product_id','candidate','review_note','first_seen_at','last_seen_at'],row))
        for key in ['first_seen_at','last_seen_at']:item[key]=item[key].isoformat()
        output.append(item)
    return output

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['stage','export','approve','reject','targets','bulk'])
    parser.add_argument('--file',type=Path)
    parser.add_argument('--id',type=int)
    parser.add_argument('--note',default='')
    parser.add_argument('--product-id')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--shop',default='coolmod',choices=['coolmod','pccomponentes','ldlc','neobyte'])
    args=parser.parse_args()
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        migrate(conn)
        if args.action=='stage':
            if not args.file:parser.error('--file required')
            print(json.dumps({'review_ids':stage_candidates(conn,json.loads(args.file.read_text()))}))
        elif args.action=='approve':
            if args.id is None:parser.error('--id required')
            print(json.dumps({'offer_id':approve(conn,args.id,args.note,args.product_id)}))
        elif args.action=='bulk':
            if not args.file:parser.error('--file required')
            print(json.dumps(bulk(conn,json.loads(args.file.read_text()),args.dry_run)))
        elif args.action=='reject':
            if args.id is None or not args.note:parser.error('--id and --note required')
            reject(conn,args.id,args.note)
        elif args.action=='targets':
            rows=conn.execute("SELECT DISTINCT ON(retailer_sku) candidate,proposed_product_id FROM retailer_review_queue WHERE status='approved' AND shop_id=%s ORDER BY retailer_sku,review_id DESC",(args.shop,)).fetchall()
            print(json.dumps([{**{key:row[0][key] for key in ['retailer_sku','mpn','url_public']},'product_id':row[1]} for row in rows],ensure_ascii=False,indent=2))
        else:print(json.dumps(export(conn),ensure_ascii=False,indent=2))

if __name__=='__main__':main()
