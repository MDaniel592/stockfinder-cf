import { describe, expect, test } from 'vitest';
import { purchasePlans, landedTotal, enteredCents, type BuyOffer } from '../apps/web/src/purchase';
import { watchNotice, readWatchlist, type WatchedProduct } from '../apps/web/src/watchlist';
import { builderSpecFilters } from '../apps/web/src/pc-builder';
const offer = (shop_name:string, price_cents:number, in_stock=true):BuyOffer => ({id:shop_name,shop_name,price_cents,in_stock,url_public:'https://example.com',last_checked_at:new Date().toISOString()});
describe('purchase planning', () => {
  test('mixes cheapest in-stock parts and charges shipping once per shop', () => {
    const plans = purchasePlans([{id:'a',name:'CPU',offers:[offer('A',100),offer('B',90)]},{id:'b',name:'RAM',offers:[offer('A',100),offer('B',200),offer('C',1,false)]}]);
    expect(plans[0].subtotal).toBe(190);
    expect(plans[0].shops.sort()).toEqual(['A','B']);
    expect(landedTotal(plans[0], {A:{shipping:'5',adjustment:'0'},B:{shipping:'7',adjustment:'0'}})).toBe(1390);
    expect(landedTotal(plans[1], {A:{shipping:'5',adjustment:'0'}})).toBe(700);
    expect(landedTotal(plans[0], {A:{shipping:'0',adjustment:'0'}})).toBeNull();
  });
  test('unknown or missing components never yield a complete total', () => {
    const p = purchasePlans([{id:'a',name:'CPU',offers:[offer('A',100)]},{id:'b',name:'GPU',offers:[]}])[0];
    expect(p.missing).toEqual(['GPU']); expect(landedTotal(p,{A:{shipping:'0',adjustment:'0'}})).toBeNull();
    expect(enteredCents('')).toBeNull(); expect(enteredCents('-2')).toBeNull(); expect(enteredCents('1e2')).toBeNull();
    expect(enteredCents('5,25')).toBe(525); expect(enteredCents('0')).toBe(0);
    const old={...offer('A',100),last_checked_at:new Date(Date.now()-3*86400000).toISOString()};
    expect(purchasePlans([{id:'old',name:'Outdated price',offers:[old]}])[0].missing).toEqual(['Outdated price']);
  });
});
describe('on-open alerts', () => {
  const w:WatchedProduct = {slug:'gpu',name:'GPU',baseline:10000,target:8000,backInStock:true,initialStock:false};
  test('does not treat missing offers as zero; detects target, stock and price drops', () => {
    expect(watchNotice(w,null)).toBeNull(); expect(watchNotice(w,8000)).toBe('Precio objetivo alcanzado');
    expect(watchNotice(w,12000)).toBe('Vuelve a haber una oferta en stock');
    expect(watchNotice({...w,initialStock:true},9000)).toBe('Ha bajado de precio');
    expect(watchNotice({...w,initialStock:true},11000)).toBeNull();
  });
  test('rejects corrupt storage rather than silently discarding it', () => {
    expect(readWatchlist(JSON.stringify([w]))).toEqual([w]); expect(readWatchlist(null)).toEqual([]);
    expect(() => readWatchlist(JSON.stringify([w,w]))).toThrow();
    expect(() => readWatchlist(JSON.stringify([{...w,target:-1}]))).toThrow();
    expect(() => readWatchlist('broken')).toThrow();
  });
  test('server-side picker constraints follow board memory before CPU and clear without dependencies', () => {
    const p = (specs:Record<string,string>) => ({specs}) as any;
    expect(builderSpecFilters('motherboard',{cpu:p({Socket:'AM5'})})).toEqual({Socket:['AM5']});
    expect(builderSpecFilters('ram',{cpu:p({'Memoria compatible':'DDR4, DDR5'}),motherboard:p({'Tipo de memoria':'DDR5'})})).toEqual({'Tipo de memoria':['DDR5']});
    expect(builderSpecFilters('ram',{})).toEqual({});
  });
});
