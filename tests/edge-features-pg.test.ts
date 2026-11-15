import { afterAll,beforeAll,describe,expect,test } from 'vitest';
import { randomUUID } from 'node:crypto';
import { dbMode,testWorker } from './helpers/worker-db';
describe.skipIf(dbMode!=='pg')('Supabase feature replica and private RPC',()=>{
 let w:ReturnType<typeof testWorker>;
 beforeAll(async()=>{w=testWorker({});await w.migrate();},30000);afterAll(async()=>{await w?.dispose();});
 let eventId=0;
 const ev=(entity:string,entity_id:string,data:unknown,version=1)=>({event_id:++eventId,entity,entity_id,data,version,operation:'upsert',changed_at:new Date().toISOString()});
 const rpc=async(op:string,payload:unknown={})=>JSON.parse(String((await w.query('SELECT stockfinder_features.rpc(?,?::jsonb) AS body',op,JSON.stringify(payload)))[0].body));
 const apply=async(events:unknown[])=>w.query('SELECT stockfinder.feature_replica_apply(?,?::json)',randomUUID(),JSON.stringify(events));
 const offer=(price:number)=>({product_id:'gpu',shop_id:'shop',price_cents:price,in_stock:1,last_checked_at:new Date().toISOString(),url_public:'https://example.com/gpu'});
 test('replicates products and prices, filters before pagination and refreshes a deduplicated current-price ranking',async()=>{
  await apply([ev('category','gpu',{slug:'gpu',name:'GPU'}),ev('shop','shop',{name:'Shop',url_public:'https://example.com'}),
   ev('product','gpu',{slug:'smart-gpu',name:'Smart GPU',brand:'Test',category_id:'gpu',specs:{Socket:'AM5'}}),
   ev('offer','o',{...offer(20000),last_checked_at:new Date(Date.now()-7200000).toISOString()})]);
  await apply([ev('offer','o',offer(10000),2)]);
  const list=await rpc('options',{category:'gpu',page:1,q:'Smart GPU',specs:{Socket:['AM5']}});
  expect(list.total).toBe(1);expect(list.data[0]).toMatchObject({slug:'smart-gpu',min_price_cents:10000});
  expect((await rpc('options',{category:'gpu',specs:{Socket:['LGA1700']}})).total).toBe(0);
  await w.query('SELECT stockfinder.feature_refresh()');
  const ranking=await rpc('deals');expect(ranking.stale).toBe(false);expect(ranking.drops).toHaveLength(1);
  expect((await rpc('deals',{category:'gpu'})).drops).toHaveLength(1);
  expect((await rpc('deals',{category:'cpu'})).drops).toHaveLength(0);
  expect(ranking).not.toHaveProperty('by_category');
  expect(ranking.drops[0]).toMatchObject({slug:'smart-gpu',min_price_cents:10000,previous_price_cents:20000});
  await apply([ev('offer','o',offer(9000),3)]);await w.query('SELECT stockfinder.feature_refresh()');
  expect((await rpc('deals')).drops[0]).toMatchObject({min_price_cents:9000,previous_price_cents:10000});
  expect((await rpc('offers',{slug:'smart-gpu'})).data[0].price_cents).toBe(9000);
  expect((await rpc('product',{slug:'smart-gpu'})).data.slug).toBe('smart-gpu');
 });
 test('shared builds validate against replica, reject invalid filters, and keep private tables inaccessible to the replica role',async()=>{
  const id=randomUUID();expect((await rpc('build_create',{id,name:'Saved PC',parts:{gpu:'smart-gpu'},ip_hash:'synthetic-test-ip-hash-1234567890'})).outcome).toBe('ok');
  expect((await rpc('build_get',{id})).data).toMatchObject({id,name:'Saved PC',parts:{gpu:'smart-gpu'}});
  expect((await rpc('build_create',{id:randomUUID(),name:'Wrong slot',parts:{cpu:'smart-gpu'},ip_hash:'synthetic-test-ip-hash-1234567890'})).outcome).toBe('product');
  await expect(rpc('options',{category:'gpu',specs:{unexpected:['x']}})).rejects.toThrow();
  const permissions=(await w.query("SELECT has_table_privilege('stockfinder_replica','stockfinder.catalog_products','SELECT') AS direct_read,has_function_privilege('stockfinder_replica','stockfinder.feature_replica_apply(text,json)','EXECUTE') AS apply,has_function_privilege('stockfinder_replica','stockfinder_features.rpc(text,jsonb)','EXECUTE') AS rpc"))[0];
  expect(permissions).toEqual({direct_read:false,apply:true,rpc:false});
 });
});
