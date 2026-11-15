/** Discovery only: raw candidates stay in the review queue, never auto-create products. */
const fs=require('node:fs/promises');
const {chromium}=require('playwright');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const flatten=value=>Array.isArray(value)?value.flatMap(flatten):value&&typeof value==='object'?[value,...flatten(value['@graph']||[])]:[];
async function main(){
  const queries=JSON.parse(await fs.readFile('/work/data/retailers/discovery-queries.json','utf8'));
  const output=process.env.DISCOVERY_OUTPUT||'/output/candidates.json';
  let candidates=[];try{candidates=JSON.parse(await fs.readFile(output,'utf8'))}catch{}
  candidates=[...new Map(candidates.map(item=>[item.shop_id+':'+item.retailer_sku,item])).values()];
  const seen=new Set(candidates.map(item=>item.url_public));
  const seenSkus=new Set(candidates.map(item=>String(item.retailer_sku)));
  const limit=Math.min(50,Math.max(1,Number(process.env.DISCOVERY_LIMIT)||30));
  const browser=await chromium.launch();let added=0;
  try{
    for(const {query,hint} of queries){
      if(added>=limit)break;
      const page=await browser.newPage();let links=[];
      try{
        const response=await page.goto('https://www.coolmod.com/#01cc/fullscreen/m=and&q='+encodeURIComponent(query),{waitUntil:'networkidle',timeout:45000});
        if(!response||response.status()!==200)throw new Error('Search unavailable');
        await page.waitForFunction(()=>document.body.innerText.includes('resultados'),{},{timeout:15000});
        const tokens=query.toLowerCase().split(/\s+/).filter(token=>token.length>=3);
        links=await page.locator('a[href]').evaluateAll((anchors,tokens)=>[...new Set(anchors.map(a=>a.href).filter(href=>{
          const url=new URL(href);return url.hostname==='www.coolmod.com'&&url.pathname.split('/').filter(Boolean).length===1&&
           !/contenido|configurador|portatil|mini-pc|coolpc|alienware|pc-sobremesa/.test(url.pathname)&&tokens.every(token=>decodeURIComponent(url.pathname).toLowerCase().includes(token));
        }))],tokens);
      }catch(error){console.log(JSON.stringify({event:'discovery_search_failed',query,type:error.constructor.name}))}
      finally{await page.close()}
      // Limit breadth per search; packs/tray/assembler variants are kept as separate candidates.
      for(const url of links.slice(0,4)){
        if(added>=limit)break;if(seen.has(url))continue;
        const detail=await browser.newPage();
        try{
          const response=await detail.goto(url,{waitUntil:'networkidle',timeout:45000});
          if(!response||response.status()!==200||new URL(detail.url()).hostname!=='www.coolmod.com')throw new Error('Product unavailable');
          const documents=await detail.locator('script[type="application/ld+json"]').evaluateAll(nodes=>nodes.flatMap(n=>{try{return [JSON.parse(n.textContent)]}catch{return []}}));
          const product=documents.flatMap(flatten).find(item=>[].concat(item['@type']||[]).includes('Product'));
          if(!product)throw new Error('Product schema absent');
          if(seenSkus.has(String(product.sku))){seen.add(url);continue}
          if(/^(Pack |Portátil |Mini PC |PC Sobremesa )/i.test(String(product.name))){seen.add(url);continue}
          const offer=[].concat(product.offers||[]).find(offer=>offer.priceCurrency==='EUR');
          const rawPrice=offer&&/^\d+(\.\d{1,2})?$/.test(String(offer.price))?Math.round(Number(offer.price)*100):null;
          const availability=String(offer?.availability||'');
          const stock=/\/InStock$/.test(availability)?true:/\/(OutOfStock|SoldOut)$/.test(availability)?false:null;
          const candidate={shop_id:'coolmod',retailer_sku:String(product.sku||url),url_public:url,
            name:String(product.name||''),brand:typeof product.brand==='string'?product.brand:product.brand?.name||null,
            mpn:product.mpn?String(product.mpn):null,ean:product.gtin13||product.gtin||null,
            price_cents:rawPrice,in_stock:stock,last_checked_at:new Date().toISOString(),category_hint:hint,
            raw_properties:product.additionalProperty||[],image_source_url:product.image||null,
            missing_fields:['manufacturer_url','verified_specifications',...(!product.mpn?['mpn']:[]),...(rawPrice===null?['price']:[]),...(stock===null?['stock']:[])]};
          candidates.push(candidate);seen.add(url);seenSkus.add(String(product.sku));added++;
          await fs.writeFile(output+'.tmp',JSON.stringify(candidates,null,2));await fs.rename(output+'.tmp',output);
          console.log(JSON.stringify({event:'candidate_collected',sku:candidate.retailer_sku,query,added}));
        }catch(error){console.log(JSON.stringify({event:'discovery_product_failed',url,type:error.constructor.name}))}
        finally{await detail.close()}
        await wait(2000);
      }
      await wait(2000);
    }
  }finally{await browser.close()}
  console.log(JSON.stringify({event:'discovery_complete',new_candidates:added,total_candidates:candidates.length}));
}
main().catch(error=>{console.error(error.message);process.exit(1)});
