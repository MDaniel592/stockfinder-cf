import os,sys,json,psycopg
sys.path.insert(0,'/app/nas')
from catalog import write
from review_queue import approve
APPLY=os.environ.get('APPLY')=='1'
src=psycopg.connect('postgresql://stockfinder@postgres/stockfinder_neobyte_local')
dst=psycopg.connect('postgresql://stockfinder@postgres/stockfinder')
A={'lian-li-lian-li-o11-air-mini-black-g99-011amx-00':'lian-li-o11-air-mini-black-o11amx','lian-li-lian-li-o11-air-mini-white-g99-011amw-00':'lian-li-o11-air-mini-white-o11amw','phanteks-eclipse-g370a-black':'phanteks-ph-ec370a-dbk01','phanteks-glacier-one-360m25-lcd-black':'phanteks-ph-go360m25g2lcd_dbk01','phanteks-glacier-one-360m25-lcd-white':'phanteks-ph-go360m25g2lcd_dwt01','phanteks-glacier-one-360m25g2-black':'phanteks-ph-go360m25g2_dbk02','phanteks-glacier-one-360m25g2-white':'phanteks-ph-go360m25g2_dwt02','zalman-zalman-m2-mini-gray-m2-mini-gray':'zalman-m2-mini-gray'}
B=['intel-core-i5-14400-tray','msi-mag-coreliquid-e240']
def owner(k,s,v):
    r=dst.execute("select product_id from product_identifiers where kind=%s and scope=%s and value=%s",(k,s,v)).fetchone(); return r[0] if r else None
log=[]
with dst.transaction():
  for s in list(A)+B:
    target=A.get(s,s)
    sp=src.execute("select data from catalog_entities where entity='product' and entity_id=%s and deleted_at is null",(s,)).fetchone()[0]
    if s in B:
        if not dst.execute("select 1 from catalog_entities where entity='product' and entity_id=%s",(s,)).fetchone():
            log.append(('new_product',s))
            if APPLY: write(dst,'product',s,sp)
    added=[]
    for k,sc,v in src.execute("select kind,scope,value from product_identifiers where product_id=%s",(s,)).fetchall():
        o=owner(k,sc,v)
        if o is None:
            added.append(f'{k}:{v}')
            if APPLY: dst.execute("insert into product_identifiers(kind,scope,value,product_id) values(%s,%s,%s,%s) on conflict do nothing",(k,sc,v,target))
        elif o!=target: log.append(('ident_owned_by_other',s,k,v,o))
    log.append(('ids_added',target,added))
    for sku,note in src.execute("select retailer_sku,review_note from retailer_classifications where shop_id='neobyte' and product_id=%s and status='approved'",(s,)).fetchall():
        row=dst.execute("select review_id,status from retailer_review_queue where shop_id='neobyte' and retailer_sku=%s order by (status='pending') desc,review_id desc limit 1",(sku,)).fetchone()
        if not row: log.append(('no_review_row',s,sku)); continue
        if not APPLY: log.append(('would_approve',row[0],row[1],target)); continue
        try:
            with dst.transaction(): r=approve(dst,row[0],note or 'Aprobada en local con evidencia oficial (ver stockfinder_neobyte_local)',target)
            log.append(('approved',row[0],target,r))
        except ValueError as e: log.append(('approve_failed',row[0],target,str(e)))
for l in log: print(l)
