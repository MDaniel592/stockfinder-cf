import { createRequire } from 'node:module';
import { expect, test } from 'vitest';
const {extractOffer,verifiedTarget}=createRequire(import.meta.url)('../nas/scrapers/coolmod.cjs');
const target={product_id:'amd-example',mpn:'BOXED-123',retailer_sku:'SKU-1',url_public:'https://www.coolmod.com/example'};
const document={ '@type':'Product',mpn:'BOXED-123',sku:'SKU-1',offers:{price:'409.95',priceCurrency:'EUR',availability:'https://schema.org/InStock'} };
test('parses explicit current price and stock only after exact SKU/MPN match',()=>{
  expect(extractOffer([{'@graph':[document]}],target)).toMatchObject({price_cents:40995,in_stock:true,shop_id:'coolmod'});
  expect(()=>extractOffer([{...document,mpn:'OTHER'}],target)).toThrow();
  expect(()=>extractOffer([{...document,sku:'OTHER'}],target)).toThrow();
  expect(()=>extractOffer([{...document,offers:{...document.offers,priceCurrency:'USD'}}],target)).toThrow();
  expect(()=>extractOffer([{...document,offers:{...document.offers,availability:'https://schema.org/PreOrder'}}],target)).toThrow();
});

test('requires a canonical identity or a documented alias for the exact store URL across categories',()=>{
  const gpu={...target,product_id:'gpu-example',mpn:'STORE-CODE'};
  const products=[{id:'gpu-example',category_id:'gpu',mpn:'OFFICIAL-CODE'}];
  const alias={product_id:'gpu-example',kind:'mpn',value:'STORE-CODE',source_url:gpu.url_public,note:'Reviewed model'};
  expect(verifiedTarget(gpu,products,[alias])).toBe(true);
  expect(verifiedTarget(gpu,products,[])).toBe(false);
  expect(verifiedTarget(gpu,products,[{...alias,source_url:'https://www.coolmod.com/other'}])).toBe(false);
  expect(verifiedTarget({...gpu,product_id:'other'},products,[alias])).toBe(false);
  expect(verifiedTarget({...gpu,mpn:'OFFICIAL-CODE'},products,[])).toBe(true);
});
