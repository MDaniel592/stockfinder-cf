import { afterAll, beforeAll, expect, test } from 'vitest';
import { createHmac, randomUUID } from 'node:crypto';
import { testWorker, dbMode } from './helpers/worker-db';
const secret='synthetic-scope-test-secret-1234567890-not-for-deployment';
const worker=testWorker({bindings:{SYNC_SECRET:secret}});
beforeAll(() => worker.migrate(),30000); afterAll(() => worker.dispose());
let eventId=0;
const event=(entity:string,entity_id:string,data:unknown,version=1) => ({event_id:++eventId,entity,entity_id,data,version,operation:'upsert',changed_at:new Date().toISOString()});
async function send(events:unknown[]) {
 const timestamp=String(Math.floor(Date.now()/1000)),nonce=randomUUID();
 const body=JSON.stringify({batch_id:randomUUID(),schema_version:1,events});
 const signature=createHmac('sha256',secret).update(`${timestamp}.${nonce}.${body}`).digest('hex');
 const res=await worker.mf.dispatchFetch('https://scopes/internal/sync',{method:'POST',body,headers:{'content-type':'application/json','x-sync-timestamp':timestamp,'x-sync-nonce':nonce,'x-sync-signature':signature}});
 expect(res.status,await res.text()).toBe(200);
}
async function get(path:string,headers:Record<string,string>={}) { const res=await worker.mf.dispatchFetch(`https://scopes/api/v1/${path}`,{headers}); return {res,body:await res.json() as any}; }
const offer=(product_id:string,price_cents:number) => ({product_id,price_cents,shop_id:'shop',in_stock:true,last_checked_at:new Date().toISOString(),url_public:'https://example.com/item'});
test.skipIf(dbMode==='pg')('category cache survives unrelated price changes; own prices and structural changes invalidate',async () => {
 await send([
  event('category','gpu',{slug:'gpu',name:'GPU'}),event('category','cpu',{slug:'cpu',name:'CPU'}),event('shop','shop',{name:'Shop',url_public:'https://example.com'}),
  event('product','g',{slug:'gpu',name:'GPU',brand:'Test',category_id:'gpu',specs:{}}),event('product','c',{slug:'cpu',name:'CPU',brand:'Test',category_id:'cpu',specs:{}}),
  event('offer','og',offer('g',10000)),event('offer','oc',offer('c',20000))
 ]);
 const paths=['products?category=gpu','products/gpu','products/gpu/offers'];
 for(const path of paths) { await get(path); await new Promise(r=>setTimeout(r,50)); expect((await get(path)).res.headers.get('x-sf-cache')).toBe('hit'); }
 await send([event('offer','oc',offer('c',19000),2)]);
 for(const path of paths) expect((await get(path)).res.headers.get('x-sf-cache')).toBe('hit');
 await send([event('offer','og',offer('g',9000),2)]);
 const changed=await get('products/gpu'); expect(changed.res.headers.get('x-sf-cache')).toBe('miss'); expect(changed.body.data.min_price_cents).toBe(9000);
 const listing=await get('products?category=gpu'); expect(listing.body.data[0].min_price_cents).toBe(9000);
 await new Promise(r=>setTimeout(r,50));
 await send([event('offer','og',offer('g',9000),3)]);
 expect((await get('products?category=gpu')).res.headers.get('x-sf-cache')).toBe('hit');
 await send([event('product','g',{slug:'gpu',name:'GPU revised',brand:'Test',category_id:'gpu',specs:{}},2)]);
 expect((await get('products/gpu')).body.data.name).toBe('GPU revised');
},30000);
test('watchlist validates bounded input and only returns published matching products',async () => {
 expect((await get('watchlist')).res.status).toBe(400);
 expect((await get('watchlist?slug=a&slug=a')).res.status).toBe(400);
 expect((await get('watchlist?slug=../a')).res.status).toBe(400);
 expect((await get('watchlist?'+Array.from({length:21},(_,i)=>`slug=p${i}`).join('&'))).res.status).toBe(400);
 const result=await get('watchlist?slug=not-present'); expect(result.res.status).toBe(200); expect(result.body.data).toEqual([]);
 await send([event('category','gpu',{slug:'gpu',name:'GPU'}), event('shop','shop',{name:'Shop',url_public:'https://example.com'}),
  event('product','watch-g',{slug:'watch-g',name:'Watched GPU',brand:'Test',category_id:'gpu',specs:{}}),
  event('offer','watch-o',{...offer('watch-g',10000),last_checked_at:new Date(Date.now()-3*86400000).toISOString()})]);
 const stale=await get('watchlist?slug=watch-g'); expect(stale.body.data[0].min_price_cents).toBeNull();
 expect(stale.body.data[0]).not.toHaveProperty('specs');
 await send([event('offer','watch-o',offer('watch-g',9000),2)]);
 const fresh=await get('watchlist?slug=watch-g',{'cache-control':'no-cache'}); expect(fresh.body.data[0].min_price_cents).toBe(9000);
});
