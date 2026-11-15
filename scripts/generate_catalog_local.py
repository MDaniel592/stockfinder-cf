"""Local development projection from curated public records; never a NAS write path."""
import hashlib
import json
import re
from pathlib import Path
from datetime import datetime,timezone
from generate_seed import CATEGORIES, sql
from catalog_images import enrich_images

now=datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
events=[]
def add(entity,identity,data):
    events.append(dict(event_id=len(events)+1,entity=entity,entity_id=identity,version=1,operation='upsert',changed_at=now,data=data))
for slug,name in CATEGORIES:add('category',slug,dict(slug=slug,name=name))
for shop,name,url in [('pccomponentes','PcComponentes','https://www.pccomponentes.com'),('coolmod','Coolmod','https://www.coolmod.com')]:
    add('shop',shop,dict(name=name,url_public=url))
products=[]
for path in sorted(Path('data/catalog').glob('*.json')):products.extend(json.loads(path.read_text()))
for product in enrich_images(products,Path('.')):add('product',product['id'],{k:v for k,v in product.items() if k!='id'})
for offer in json.loads(Path('data/retailers/pilot.json').read_text()):
    identity=offer['shop_id']+'-'+hashlib.sha256(str(offer['retailer_sku']).encode()).hexdigest()[:24]
    add('offer',identity,{k:offer[k] for k in ['product_id','shop_id','price_cents','in_stock','last_checked_at','url_public']})
output=Path('artifacts/catalog-local.sql')
output.parent.mkdir(exist_ok=True)
retire="""UPDATE catalog_products SET deleted_at=CURRENT_TIMESTAMP WHERE id GLOB 'part-[0-9][0-9][0-9][0-9]';
UPDATE catalog_offers SET deleted_at=CURRENT_TIMESTAMP WHERE id GLOB 'offer-[0-9][0-9][0-9][0-9]-[01]';
UPDATE catalog_shops SET deleted_at=CURRENT_TIMESTAMP WHERE id IN ('shop-0','shop-1');
"""
output.write_text(retire+re.sub(r' WHERE excluded.version>catalog_[a-z_]+\.version', '', sql(events)))
print(f'{len(events)} local public projection records prepared')
