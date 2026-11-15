/**
 * Coolmod: una pagina cada vez, navegador normal, solo fichas verificadas.
 * Rotacion por tandas: cada ciclo procesa hasta COOLMOD_BATCH_SIZE (max. 200) targets, los de comprobacion mas
 * antigua primero (estado persistente), con pausas >= 2 s. Un fallo de producto nunca aborta el ciclo y un fallo
 * tecnico nunca borra ni modifica la oferta publicada; solo una ficha 404/410 (o redirigida sin producto) o un
 * stock explicito AGOTADO publican in_stock=false.
 */
const fs=require('node:fs/promises');
const path=require('node:path');
const SHOP_HOST='www.coolmod.com';
const MAX_BATCH=200;
const MIN_PAGE_DELAY_MS=2000;
const log=(event,data={})=>console.log(JSON.stringify({event,shop:'coolmod',ts:new Date().toISOString(),...data}));
const warn=(event,data={})=>console.error(JSON.stringify({event,shop:'coolmod',ts:new Date().toISOString(),...data}));

function flattenDocuments(documents) {
  const flatten=value=>Array.isArray(value)?value.flatMap(flatten):value&&typeof value==='object'?[value,...flatten(value['@graph']||[])]:[];
  return documents.flatMap(flatten).filter(value=>[].concat(value['@type']||[]).includes('Product'));
}
function extractOffer(documents,target,now=new Date().toISOString()) {
  const products=flattenDocuments(documents);
  const product=products.find(value=>String(value.mpn||'').toLowerCase()===target.mpn.toLowerCase());
  if(!product)throw new Error('Exact MPN absent');
  const offer=[].concat(product.offers||[]).find(value=>value.priceCurrency==='EUR'&&typeof value.price!=='undefined');
  if(!offer||!/^\d+(\.\d{1,2})?$/.test(String(offer.price)))throw new Error('Explicit EUR offer absent');
  const availability=String(offer.availability||'');
  if(!/^https?:\/\/schema.org\/(InStock|OutOfStock|SoldOut|Discontinued)$/.test(availability))throw new Error('Stock not explicit');
  const price=Math.round(Number(offer.price)*100);
  if(!Number.isSafeInteger(price)||price<0||price>100000000)throw new Error('Invalid price');
  if(String(product.sku)!==target.retailer_sku)throw new Error('Retailer SKU changed');
  return {product_id:target.product_id,shop_id:'coolmod',retailer_sku:target.retailer_sku,mpn:target.mpn,
    url_public:target.url_public,price_cents:price,in_stock:availability.endsWith('/InStock'),last_checked_at:now,
    match_evidence:'Normal browser JSON-LD Product/Offer; exact curated MPN or documented store alias and retailer SKU; EUR price and explicit schema availability.'};
}
/** EAN-8/12/13/14 with a valid GS1 check digit, else null. */
function validEan(value) {
  const text=String(value||'').trim();
  if(!/^(\d{8}|\d{12}|\d{13}|\d{14})$/.test(text))return null;
  const digits=[...text].map(Number),check=digits.pop();
  const sum=digits.reverse().reduce((total,digit,index)=>total+digit*(index%2===0?3:1),0);
  return (10-sum%10)%10===check?text:null;
}
const normalizeCode=value=>String(value||'').toUpperCase().replace(/[^A-Z0-9]/g,'');
/**
 * Identity + commercial snapshot of a product page (used by coolmod-enrich.cjs). Same JSON-LD Product/Offer rules as
 * extractOffer, but the MPN is read instead of being the key. `flix` = {mpn, ean} from the data-flix-* attributes of the
 * page (Flixmedia widget); the EAN is only taken from there or from JSON-LD gtin*, and must pass the GS1 checksum.
 * Throws when the page is not the requested product or two sources disagree; never guesses.
 */
function extractIdentity(documents,{url_public,flix={}},now=new Date().toISOString()) {
  const products=flattenDocuments(documents).filter(value=>/^[A-Za-z0-9._-]{3,40}$/.test(String(value.sku||'')));
  const wanted=samePathKey(url_public);
  const byUrl=products.filter(value=>value.url&&samePathKey(value.url)===wanted);
  const candidates=byUrl.length?byUrl:products;
  if(candidates.length!==1)throw new Error(candidates.length?'Product ambiguous':'Product JSON-LD absent');
  const product=candidates[0];
  const clean=value=>{const text=String(value||'').trim();return text.length>=2&&text.length<=64&&!/[\u0000-\u001f]/.test(text)?text:null};
  const mpn=clean(product.mpn)||clean(product.productID);
  const flixMpn=clean(flix.mpn);
  if(mpn&&flixMpn&&normalizeCode(mpn)!==normalizeCode(flixMpn))throw new Error('MPN sources disagree');
  const ldEan=validEan(product.gtin13||product.gtin14||product.gtin12||product.gtin8||product.gtin);
  const flixEan=validEan(flix.ean);
  if(ldEan&&flixEan&&ldEan!==flixEan)throw new Error('EAN sources disagree');
  const offer=[].concat(product.offers||[]).find(value=>value&&value.priceCurrency==='EUR'&&typeof value.price!=='undefined');
  let price_cents=null,in_stock=null,seller=null;
  if(offer) {
    if(/^\d+(\.\d{1,2})?$/.test(String(offer.price))){const cents=Math.round(Number(offer.price)*100);if(Number.isSafeInteger(cents)&&cents>=0&&cents<=100000000)price_cents=cents}
    const availability=String(offer.availability||'');
    if(/^https?:\/\/schema.org\/(InStock|OutOfStock|SoldOut|Discontinued)$/.test(availability))in_stock=availability.endsWith('/InStock');
    seller=offer.seller&&typeof offer.seller==='object'?String(offer.seller.name||'').trim()||null:null;
  }
  const brand=product.brand&&typeof product.brand==='object'?String(product.brand.name||'').trim():String(product.brand||'').trim();
  return {retailer_sku:String(product.sku),mpn:mpn||flixMpn,ean:ldEan||flixEan,brand:brand||null,price_cents,in_stock,
    seller,seller_direct:!seller||/coolmod/i.test(seller)};
}
function samePathKey(url) {
  try{return new URL(url,'https://'+SHOP_HOST).pathname.replace(/\/+$/,'').toLowerCase()}catch{return ''}
}
function unavailableRecord(target,reason,now=new Date().toISOString()) {
  return {product_id:target.product_id,shop_id:'coolmod',retailer_sku:target.retailer_sku,mpn:target.mpn,
    url_public:target.url_public,unavailable:true,in_stock:false,last_checked_at:now,
    match_evidence:`Normal browser request: the store explicitly reports the listing as gone (${reason}); previously verified exact MPN and retailer SKU; last published price kept.`};
}
function verifiedTarget(target,products,aliases=[]) {
  const canonical=products.find(product=>product.id===target.product_id);
  if(!canonical)return false;
  const normalize=value=>String(value||'').trim().toLowerCase();
  return Boolean(canonical.mpn&&normalize(canonical.mpn)===normalize(target.mpn))||aliases.some(alias=>
    alias.product_id===target.product_id&&alias.kind==='mpn'&&normalize(alias.value)===normalize(target.mpn)&&
    alias.source_url===target.url_public&&Boolean(alias.note));
}

async function readJson(file,fallback) {
  try{return JSON.parse(await fs.readFile(file,'utf8'))}
  catch(error){if(error.code==='ENOENT'||error instanceof SyntaxError){if(error instanceof SyntaxError)warn('scraper_input_invalid',{file:path.basename(file)});return fallback}throw error}
}
function validTarget(target) {
  return Boolean(target&&typeof target==='object'&&['product_id','retailer_sku','mpn','url_public'].every(key=>typeof target[key]==='string'&&target[key].trim()));
}
/** /flow/targets/coolmod.json (publisher) + pilot.json; fallback to approved-coolmod-targets.json when the flow file is absent/invalid. */
async function loadTargets({flowFile,dataDir}) {
  const flow=await readJson(flowFile,null);
  const pilot=(await readJson(path.join(dataDir,'retailers/pilot.json'),[])).filter(offer=>offer.shop_id==='coolmod');
  let source='flow',list=flow;
  if(!Array.isArray(list)||!list.length){source='approved';list=await readJson(path.join(dataDir,'retailers/approved-coolmod-targets.json'),[]);if(!Array.isArray(list))list=[]}
  const merged=[...pilot,...list].filter(validTarget);
  const dropped=pilot.length+list.length-merged.length;
  return {targets:[...new Map(merged.map(target=>[target.retailer_sku,target])).values()],source,dropped};
}
/** Aliases = data/product-identifiers.json + every data/aliases/*.json (the directory may not exist). */
async function loadAliases(dataDir) {
  const aliases=[];
  const base=await readJson(path.join(dataDir,'product-identifiers.json'),[]);
  if(Array.isArray(base))aliases.push(...base);
  let files=[];
  try{files=(await fs.readdir(path.join(dataDir,'aliases'))).filter(file=>file.endsWith('.json')).sort()}catch(error){if(error.code!=='ENOENT'&&error.code!=='ENOTDIR')throw error}
  for(const file of files){const list=await readJson(path.join(dataDir,'aliases',file),[]);if(Array.isArray(list))aliases.push(...list)}
  return aliases;
}
async function loadProducts(dataDir) {
  const catalogDir=path.join(dataDir,'catalog');
  return (await Promise.all((await fs.readdir(catalogDir)).filter(file=>file.endsWith('.json')).map(async file=>
    JSON.parse(await fs.readFile(path.join(catalogDir,file),'utf8'))))).flat();
}
async function writeAtomic(file,text) {
  await fs.mkdir(path.dirname(file),{recursive:true});
  const temporary=`${file}.${process.pid}.tmp`;
  await fs.writeFile(temporary,text,{mode:0o644});await fs.rename(temporary,file);
}
async function loadState(file) {
  const state=await readJson(file,{});
  return {attempted:state&&typeof state.attempted==='object'&&state.attempted||{},succeeded:state&&typeof state.succeeded==='object'&&state.succeeded||{},cycles:Number(state&&state.cycles)||0};
}
/** Oldest attempt first; never-attempted targets first; sku as stable tie-break. */
function selectBatch(targets,state,size) {
  const last=target=>Number(state.attempted[target.retailer_sku])||0;
  return [...targets].sort((a,b)=>last(a)-last(b)||(a.retailer_sku<b.retailer_sku?-1:a.retailer_sku>b.retailer_sku?1:0)).slice(0,size);
}
/** Sleep after a cycle so a full pass over every target takes <= passTargetSeconds (see docs/runbooks/operations.md). */
function nextDelaySeconds(total,{batchSize,intervalSeconds,passTargetSeconds,minSeconds=900,lastDurationSeconds=0}) {
  const batches=Math.max(1,Math.ceil(total/batchSize));
  const cap=Math.floor(passTargetSeconds/batches)-Math.ceil(lastDurationSeconds);
  return Math.max(minSeconds,Math.min(intervalSeconds,cap));
}
function isTransient(error) {
  return ['TimeoutError','TargetClosedError'].includes(error&&error.name)||/Target (page, context or browser has been )?closed|Timeout \d+ms|net::ERR_(CONNECTION|TIMED_OUT|NETWORK)/i.test(String(error&&error.message));
}
function failureCode(error) {
  if(isTransient(error))return 'transient';
  const message=String(error&&error.message);
  if(/Curated identifier|Unconfigured domain/.test(message))return 'identity';
  if(/Store request unavailable/.test(message))return 'unavailable_request';
  return 'extract';
}
function samePath(a,b) {
  const normalize=url=>{const parsed=new URL(url);return parsed.pathname.replace(/\/+$/,'').toLowerCase()};
  return normalize(a)===normalize(b);
}

const LD_JSON='script[type="application/ld+json"]';
async function readPage(page,target) {
  const response=await page.goto(target.url_public,{waitUntil:'domcontentloaded',timeout:45000});
  const status=response?response.status():0;
  const finalUrl=page.url();
  if(status!==200)return {status,finalUrl,documents:[]};
  let documents=[];
  try {
    await page.locator(LD_JSON).first().waitFor({state:'attached',timeout:15000});
    documents=await page.locator(LD_JSON).evaluateAll(nodes=>nodes.flatMap(node=>{try{return [JSON.parse(node.textContent)]}catch{return []}}));
  } catch(error) {if(!isTransient(error))throw error}
  return {status,finalUrl,documents};
}
/** Returns {outcome:'ok'|'gone',record}. Throws on technical failures (the previous data is then kept). */
async function checkTarget(target,context) {
  const {products,aliases,deps}=context;
  if(new URL(target.url_public).hostname!==SHOP_HOST)throw new Error('Unconfigured domain');
  if(!verifiedTarget(target,products,aliases))throw new Error('Curated identifier not verified');
  const page=await deps.newPage();
  try {
    const {status,finalUrl,documents}=await deps.readPage(page,target);
    const now=deps.now();
    if(status===404||status===410)return {outcome:'gone',record:unavailableRecord(target,`HTTP ${status}`,now)};
    if(status!==200||new URL(finalUrl).hostname!==SHOP_HOST)throw new Error('Store request unavailable');
    const listed=flattenDocuments(documents).some(product=>String(product.sku)===target.retailer_sku);
    if(!samePath(finalUrl,target.url_public)&&!listed)return {outcome:'gone',record:unavailableRecord(target,'redirected away without the product',now)};
    return {outcome:'ok',record:extractOffer(documents,target,now)};
  } finally {await Promise.resolve(page.close()).catch(()=>{})}
}

function configFromEnv(env=process.env) {
  const number=(value,fallback)=>Number.isFinite(Number(value))&&Number(value)>0?Number(value):fallback;
  return {
    flowFile:env.COOLMOD_TARGETS_FILE||'/flow/targets/coolmod.json',
    dataDir:env.DATA_DIR||'/work/data',
    output:env.OUTPUT_FILE||'/output/offers/coolmod.json',
    stateFile:env.COOLMOD_STATE_FILE||'/output/state/coolmod-state.json',
    batchSize:Math.min(MAX_BATCH,Math.floor(number(env.COOLMOD_BATCH_SIZE,MAX_BATCH))),
    pageDelayMs:Math.max(MIN_PAGE_DELAY_MS,number(env.COOLMOD_PAGE_DELAY_MS,MIN_PAGE_DELAY_MS)),
    intervalSeconds:Math.max(900,number(env.SCRAPE_INTERVAL_SECONDS,5400)),
    passTargetSeconds:number(env.COOLMOD_PASS_TARGET_SECONDS,43200),
  };
}
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));

/** One rotation batch. `deps` allows tests to run it without a browser or network. */
async function runCycle(config,deps) {
  const started=Date.now();
  const {targets,source,dropped}=await loadTargets(config);
  if(!targets.length)throw new Error('No targets');
  const [products,aliases]=await Promise.all([deps.loadProducts(config.dataDir),loadAliases(config.dataDir)]);
  const state=await loadState(config.stateFile);
  const batch=selectBatch(targets,state,config.batchSize);
  log('scraper_cycle_start',{targets:targets.length,batch:batch.length,source,invalid_targets:dropped,aliases:aliases.length});
  let browser=await deps.launch();
  const records=[],counts={ok:0,gone:0,failed:0};
  const runtime={products,aliases,deps:{...deps,newPage:()=>browser.newPage()}};
  try {
    for(const [index,target] of batch.entries()) {
      if(index>0)await deps.sleep(config.pageDelayMs+Math.floor(Math.random()*1000));
      let result=null,error=null;
      for(let attempt=0;attempt<2&&!result;attempt++) {
        try {
          if(!browser.isConnected()){browser=await deps.launch()}
          result=await checkTarget(target,runtime);
        } catch(failure) {
          error=failure;
          if(attempt===0&&isTransient(failure)){await deps.sleep(config.pageDelayMs);continue}
          break;
        }
      }
      state.attempted[target.retailer_sku]=Date.now();
      if(result) {
        records.push(result.record);counts[result.outcome]+=1;
        state.succeeded[target.retailer_sku]=Date.now();
      } else {
        counts.failed+=1;
        warn('scraper_product_failed',{sku:target.retailer_sku,type:error&&error.constructor.name,code:failureCode(error)});
      }
    }
  } finally {await Promise.resolve(browser.close()).catch(()=>{})}
  const keep=new Set(targets.map(target=>target.retailer_sku));
  for(const map of [state.attempted,state.succeeded])for(const sku of Object.keys(map))if(!keep.has(sku))delete map[sku];
  state.cycles+=1;state.last_run=new Date().toISOString();
  if(records.length)await writeAtomic(config.output,JSON.stringify(records,null,2));
  await writeAtomic(config.stateFile,JSON.stringify(state));
  const ages=targets.map(target=>state.succeeded[target.retailer_sku]).map(time=>time?(Date.now()-time)/3600000:null);
  const neverChecked=ages.filter(age=>age===null).length;
  const maxAge=ages.filter(age=>age!==null).reduce((max,age)=>Math.max(max,age),0);
  const summary={targets:targets.length,batch:batch.length,ok:counts.ok,gone:counts.gone,failed:counts.failed,
    never_succeeded:neverChecked,max_age_hours:Number(maxAge.toFixed(2)),duration_seconds:Math.round((Date.now()-started)/1000)};
  if(records.length)log('scraper_success',{...summary,offers:records.length});
  else warn('scraper_failure',{...summary,type:'NoRecords'});
  return {summary,records,total:targets.length};
}

/**
 * Loggable fields of an unexpected error: type, errno code (EACCES, ENOENT...) and a short message. URL credentials
 * and secret-looking query parameters are masked; file paths and errno text are kept (they are what makes an EACCES
 * on /output visible).
 */
function errorFields(error) {
  const message=String(error&&error.message||'')
    .replace(/\/\/[^\s/@]+@/g,'//***@')
    .replace(/([?&][^=&\s]*(?:token|key|secret|pass|sig|auth)[^=&\s]*=)[^&\s]+/gi,'$1***')
    .slice(0,300);
  return {type:error&&error.constructor?error.constructor.name:typeof error,
    code:error&&typeof error.code==='string'?error.code:undefined,message:message||undefined};
}

async function main() {
  const config=configFromEnv();
  const watch=process.argv.includes('--watch');
  const {chromium}=require('playwright');
  const deps={launch:()=>chromium.launch({headless:true}),loadProducts,readPage,sleep,now:()=>new Date().toISOString()};
  let lastDuration=0,total=0;
  do {
    const started=Date.now();
    try{const result=await runCycle(config,deps);total=result.total;if(!result.records.length&&!watch)process.exitCode=1}
    catch(error){warn('scraper_failure',errorFields(error));if(!watch)process.exitCode=1}
    lastDuration=(Date.now()-started)/1000;
    if(!watch)break;
    const delay=nextDelaySeconds(total||config.batchSize,{...config,batchSize:config.batchSize,lastDurationSeconds:lastDuration});
    log('scraper_sleep',{seconds:delay});
    await sleep(delay*1000);
  } while(true);
}
if(require.main===module)main().catch(()=>process.exit(1));
module.exports={extractIdentity,validEan,flattenDocuments,readPage,samePath,readJson,writeAtomic,SHOP_HOST,MIN_PAGE_DELAY_MS,extractOffer,verifiedTarget,unavailableRecord,loadTargets,loadAliases,selectBatch,nextDelaySeconds,configFromEnv,runCycle,checkTarget,isTransient,errorFields};
