import { afterAll,expect,test } from 'vitest';
import { testWorker } from './helpers/worker-db';
const calls:string[]=[];
const id='bd03eaa8-aed8-44bb-8840-3f216b6e70c6';
const w=testWorker({bindings:{FEATURES_URL:'https://features.example',FEATURES_KEY:'synthetic-server-key',SYNC_SECRET:'synthetic-ip-hash-secret-not-for-deployment-123'},serviceBindings:{
 FEATURES_SERVICE:async(request:Request)=>{
  expect(request.headers.get('content-profile')).toBe('stockfinder_features');
  const {op,payload}=await request.json() as {op:string;payload:any};calls.push(op);
  const values:Record<string,unknown>={deals:{drops:[],categories:[],stale:false},state:{categories:[],shops:[],stale:false},
   options:{data:[],total:0,page:1,limit:48},product:{data:{id:'p',slug:'p',specs:{}}},offers:{data:[]},watchlist:{data:[]},
   build_create:{id:payload.id,outcome:'ok'},build_get:{data:{id,name:'Shared',parts:{cpu:'cpu'},created_at:new Date().toISOString()}}};
  return Response.json(values[op]);
 },
 ASSETS:async()=>new Response('<html><head><title>StockFinder</title></head><body><div id="root"></div></body></html>',{headers:{'content-type':'text/html'}})
}});
// Deliberately do NOT apply any D1 migrations. Every accidental D1 read would fail the requests below.
afterAll(()=>w.dispose());
test('feature APIs, shared builds and HTML shells work with an empty D1 database',async()=>{
 for(const path of ['/ofertas','/configurador','/api/v1/catalog/deals','/api/v1/catalog/highlights','/api/v1/builder/state',
  '/api/v1/builder/options?category=cpu','/api/v1/builder/products/p','/api/v1/builder/products/p/offers',
  '/api/v1/builder/watchlist?slug=p',`/api/v1/builds/${id}`]) {
  const response=await w.mf.dispatchFetch('https://features-test'+path);
  expect(response.status,`${path}: ${await response.text()}`).toBe(200);
 }
 const shared=await w.mf.dispatchFetch('https://features-test/api/v1/builds',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({name:'My PC',parts:{cpu:'cpu'}})});
 expect(shared.status,await shared.text()).toBe(201);
 expect(calls).toEqual(expect.arrayContaining(['deals','state','options','product','offers','watchlist','build_create','build_get']));
},30000);
