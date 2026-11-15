/**
 * Coolmod PDP enrichment: fills MPN / EAN / Coolmod SKU (+ price, stock, seller) on PENDING review-queue rows that the
 * inventory crawl staged from listing pages only (mpn = ean = null), so verification agents and the publisher can
 * match them to official pages / verified products.
 *
 * Input : ENRICH_TARGETS_FILE (default /flow/enrich/coolmod.json), written read-only from PostgreSQL by
 *         nas/publisher.py: [{review_id, url_public, retailer_sku, name, brand, category_hint}] (pending, null MPN).
 * Output: candidate batches `enriched-<run>-<n>.json` in ENRICH_OUTPUT_DIR (the directory sync-agent already stages with
 *         review_queue.stage_candidates / _merge_pending_candidate). Nothing is approved here.
 * State : ENRICH_STATE_FILE, written after every page: resumable, no page is fetched twice inside its cooldown.
 * Rules : one normal headless page at a time, >= 2 s between pages (same as coolmod.cjs): a
 *         403/429/503, a challenge page or 5 consecutive technical failures STOP the run (exit 3), progress is kept.
 * CLI   : node coolmod-enrich.cjs [--limit N] [--dry-run] [--watch] [--input file]
 */
const fs=require('node:fs/promises');
const path=require('node:path');
const coolmod=require('./coolmod.cjs');
const {extractIdentity,readPage,samePath,readJson,writeAtomic,SHOP_HOST,MIN_PAGE_DELAY_MS}=coolmod;
const log=(event,data={})=>console.log(JSON.stringify({event,job:'coolmod-enrich',ts:new Date().toISOString(),...data}));
const warn=(event,data={})=>console.error(JSON.stringify({event,job:'coolmod-enrich',ts:new Date().toISOString(),...data}));
const HOUR=3600*1000;
const COOLDOWN_MS={ok:6*HOUR,gone:7*24*HOUR,no_identity:7*24*HOUR,mismatch:24*HOUR,failed:HOUR};
const BLOCK_STATUS=[403,429,503];
const CHALLENGE=/just a moment|attention required|captcha|access denied|are you a robot|verifica que eres/i;
const FLUSH_EVERY=25,MAX_STAGE=190,MAX_CONSECUTIVE_FAILURES=5;

class BlockedError extends Error {constructor(reason){super(reason);this.name='BlockedError';this.reason=reason}}

function configFromEnv(env=process.env) {
  const number=(value,fallback)=>Number.isFinite(Number(value))&&Number(value)>0?Number(value):fallback;
  return {
    targetsFile:env.ENRICH_TARGETS_FILE||'/flow/enrich/coolmod.json',
    outputDir:env.ENRICH_OUTPUT_DIR||'/output/coolmod',
    stateFile:env.ENRICH_STATE_FILE||'/output/state/coolmod-enrich-state.json',
    pageDelayMs:Math.max(MIN_PAGE_DELAY_MS,number(env.ENRICH_PAGE_DELAY_MS,MIN_PAGE_DELAY_MS)),
    batchSize:number(env.ENRICH_BATCH_SIZE,300),
    idleSeconds:number(env.ENRICH_IDLE_SECONDS,900),
    blockedSeconds:number(env.ENRICH_BLOCKED_SECONDS,6*3600),
  };
}
const urlKey=url=>String(url||'').split('?')[0].split('#')[0].replace(/\/+$/,'').toLowerCase();
function validTarget(target) {
  if(!target||typeof target!=='object'||typeof target.url_public!=='string')return false;
  try{const url=new URL(target.url_public);return url.protocol==='https:'&&url.hostname===SHOP_HOST}catch{return false}
}
async function loadState(file) {
  const state=await readJson(file,{});
  return {attempts:state&&typeof state.attempts==='object'&&state.attempts||{},runs:Number(state&&state.runs)||0,
    totals:{ok:0,gone:0,no_identity:0,mismatch:0,failed:0,blocked:0,...(state&&state.totals||{})},last_run:state&&state.last_run||null};
}
/** Targets not attempted inside the cooldown of their last outcome; never-attempted first, then oldest attempt. */
function selectTargets(targets,state,now=Date.now()) {
  const seen=new Set(),due=[];
  for(const target of targets) {
    if(!validTarget(target))continue;
    const key=urlKey(target.url_public);
    if(seen.has(key))continue;seen.add(key);
    const last=state.attempts[key];
    if(last&&now-last.at<(COOLDOWN_MS[last.outcome]||HOUR))continue;
    due.push({target,at:last?last.at:0});
  }
  return due.sort((a,b)=>a.at-b.at||(a.target.review_id||0)-(b.target.review_id||0)).map(item=>item.target);
}
/** Candidate for review_queue.stage_candidates; _merge_pending_candidate keeps the existing name/evidence of the row. */
function buildCandidate(target,identity,now) {
  const missing=['manufacturer_url','verified_specifications'];
  if(!identity.ean)missing.push('ean');
  if(identity.price_cents===null)missing.push('price_cents');
  if(identity.in_stock===null)missing.push('in_stock');
  if(!identity.seller_direct)missing.push('direct_seller_verification');
  const candidate={shop_id:'coolmod',url_public:target.url_public,retailer_sku:identity.retailer_sku,mpn:identity.mpn,
    ...(identity.ean?{ean:identity.ean}:{}),name:target.name,brand:target.brand||identity.brand,
    ...(target.category_hint?{category_hint:target.category_hint}:{}),
    price_cents:identity.price_cents,in_stock:identity.seller_direct?identity.in_stock:null,last_checked_at:now,
    commercial_observed_at:now,commercial_observation_source:'Coolmod product page JSON-LD Product/Offer (enrichment)',
    missing_fields:missing,
    raw_properties:[{key:'enrichment',value:'coolmod-pdp'},{key:'seller',value:identity.seller||'Coolmod'},{key:'direct_seller',value:identity.seller_direct}]};
  if(!candidate.name)delete candidate.name;
  if(!candidate.brand)delete candidate.brand;
  return candidate;
}
/** Read one product page. Returns {kind:'ok',identity} | {kind:'gone'|'no_identity'|'mismatch',...}; throws BlockedError / technical errors. */
async function enrichTarget(target,deps,browserState) {
  const page=await browserState.newPage();
  try {
    const result=await deps.readPage(page,target);
    if(BLOCK_STATUS.includes(result.status))throw new BlockedError(`HTTP ${result.status}`);
    if(result.status===404||result.status===410)return {kind:'gone',reason:`HTTP ${result.status}`};
    if(result.status!==200)throw new Error('Store request unavailable');
    if(new URL(result.finalUrl).hostname!==SHOP_HOST)throw new Error('Store request unavailable');
    if(!result.documents.length&&CHALLENGE.test(result.title||''))throw new BlockedError('challenge page');
    if(!samePath(result.finalUrl,target.url_public))return {kind:'gone',reason:'redirected away'};
    let identity;
    try{identity=extractIdentity(result.documents,{url_public:target.url_public,flix:result.flix||{}},deps.now())}
    catch(error){return {kind:/disagree|ambiguous/.test(error.message)?'mismatch':'no_identity',reason:error.message}}
    if(!identity.mpn)return {kind:'no_identity',reason:'page has no MPN'};
    return {kind:'ok',identity};
  } finally {await Promise.resolve(page.close()).catch(()=>{})}
}
/** Browser page reader: coolmod.readPage (JSON-LD) + the data-flix-* attributes that carry the EAN. */
async function readEnrichPage(page,target) {
  const base=await readPage(page,target);
  if(base.status!==200)return base;
  let flix={},title='';
  try {
    await page.locator('[data-flix-ean],[data-flix-mpn]').first().waitFor({state:'attached',timeout:4000}).catch(()=>{});
    flix=await page.evaluate(()=>{const node=document.querySelector('[data-flix-ean],[data-flix-mpn]');return node?{ean:node.getAttribute('data-flix-ean'),mpn:node.getAttribute('data-flix-mpn')}:{}});
    title=await page.title();
  } catch(error) {if(!coolmod.isTransient(error))throw error}
  return {...base,flix,title};
}

async function acquireLock(file,now=Date.now()) {
  const lock=`${file}.lock`;
  await fs.mkdir(path.dirname(lock),{recursive:true});
  try{const stat=await fs.stat(lock);if(now-stat.mtimeMs<10*60*1000)return null}catch(error){if(error.code!=='ENOENT')throw error}
  await fs.writeFile(lock,String(process.pid));
  return {touch:()=>fs.utimes(lock,new Date(),new Date()).catch(()=>{}),release:()=>fs.rm(lock,{force:true})};
}

/** One pass over the due targets. `deps` lets tests run without a browser or network. */
async function runEnrich(config,deps,{limit=Infinity,dryRun=false,input}={}) {
  const lock=dryRun?{touch:async()=>{},release:async()=>{}}:await acquireLock(config.stateFile);
  if(!lock)return {skipped:'locked'};
  const started=Date.now(),runId=new Date(started).toISOString().replace(/[-:.TZ]/g,"").slice(0,17);
  try {
    const targets=await readJson(input||config.targetsFile,[]);
    if(!Array.isArray(targets))throw new Error('Targets file is not a list');
    const state=await loadState(config.stateFile);
    const due=selectTargets(targets,state).slice(0,Math.min(limit,config.batchSize));
    const summary={targets:targets.length,due:due.length,ok:0,gone:0,no_identity:0,mismatch:0,failed:0,blocked:null,files:0,with_ean:0,records:[]};
    log('enrich_start',{targets:targets.length,due:due.length,dry_run:dryRun});
    if(!due.length)return summary;
    let browser=await deps.launch(),pending=[],consecutive=0,fileNo=0;
    const flush=async()=>{
      if(!pending.length||dryRun){pending=[];return}
      fileNo+=1;summary.files+=1;
      await writeAtomic(path.join(config.outputDir,`enriched-${runId}-${String(fileNo).padStart(3,'0')}.json`),JSON.stringify(pending,null,2));
      pending=[];
    };
    const saveState=async()=>{
      if(dryRun)return;
      state.last_run=new Date().toISOString();
      await writeAtomic(config.stateFile,JSON.stringify(state));await lock.touch();
    };
    const browserState={newPage:async()=>{if(!browser.isConnected())browser=await deps.launch();return browser.newPage()}};
    try {
      for(const [index,target] of due.entries()) {
        if(index>0)await deps.sleep(config.pageDelayMs+Math.floor(Math.random()*1000));
        let outcome=null,error=null;
        for(let attempt=0;attempt<2&&!outcome;attempt++) {
          try{outcome=await enrichTarget(target,deps,browserState)}
          catch(failure) {
            error=failure;
            if(failure instanceof BlockedError)break;
            if(attempt===0&&coolmod.isTransient(failure)){await deps.sleep(config.pageDelayMs);continue}
            break;
          }
        }
        const key=urlKey(target.url_public);
        if(!outcome&&error instanceof BlockedError) {
          summary.blocked=error.reason;state.totals.blocked+=1;
          warn('enrich_blocked',{reason:error.reason,processed:index});break;
        }
        if(!outcome) {
          consecutive+=1;summary.failed+=1;state.totals.failed+=1;
          state.attempts[key]={at:Date.now(),outcome:'failed',code:coolmod.isTransient(error)?'transient':'extract'};
          warn('enrich_failed',{review_id:target.review_id,code:state.attempts[key].code});
          if(consecutive>=MAX_CONSECUTIVE_FAILURES){summary.blocked='consecutive failures';warn('enrich_blocked',{reason:summary.blocked});break}
        } else {
          consecutive=0;summary[outcome.kind]+=1;state.totals[outcome.kind]+=1;
          state.attempts[key]={at:Date.now(),outcome:outcome.kind,...(outcome.reason?{reason:outcome.reason}:{}),...(outcome.kind==='ok'?{mpn:outcome.identity.mpn}:{})};
          if(outcome.kind==='ok') {
            const candidate=buildCandidate(target,outcome.identity,deps.now());
            summary.with_ean+=candidate.ean?1:0;summary.records.push(candidate);pending.push(candidate);
            if(pending.length>=Math.min(FLUSH_EVERY,MAX_STAGE))await flush();
          }
        }
        if(!dryRun)await saveState();
      }
    } finally {await flush().catch(error=>warn('enrich_flush_failed',{type:error.constructor.name}));await Promise.resolve(browser.close()).catch(()=>{})}
    state.runs+=1;await saveState();
    if(!dryRun)await prune(config.outputDir,started);
    const {records,...rest}=summary;
    log('enrich_done',{...rest,dry_run:dryRun,duration_seconds:Math.round((Date.now()-started)/1000)});
    return summary;
  } finally {await lock.release()}
}
/** Staged batch files are idempotent; keep two days so a restart of sync-agent can re-read them, then drop them. */
async function prune(dir,now) {
  try{for(const name of await fs.readdir(dir))if(/^enriched-.*\.json$/.test(name)){
    const file=path.join(dir,name);if(now-(await fs.stat(file)).mtimeMs>2*24*HOUR)await fs.rm(file,{force:true})}}catch{}
}

async function main() {
  const argv=process.argv.slice(2);
  const option=name=>{const index=argv.indexOf(name);return index>=0?argv[index+1]:undefined};
  const config=configFromEnv(),watch=argv.includes('--watch'),dryRun=argv.includes('--dry-run');
  const limit=option('--limit')?Math.max(1,Number(option('--limit'))):Infinity;
  const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const {chromium}=require('playwright');
  const deps={launch:()=>chromium.launch({headless:true}),readPage:readEnrichPage,sleep,now:()=>new Date().toISOString()};
  do {
    let wait=config.idleSeconds;
    try {
      const result=await runEnrich(config,deps,{limit,dryRun,input:option('--input')});
      if(dryRun)console.log(JSON.stringify(result,null,2));
      if(result.blocked){process.exitCode=3;wait=config.blockedSeconds}
      else if(result.due&&result.due>=Math.min(limit,config.batchSize))wait=5;
    } catch(error) {warn('enrich_failure',{type:error.constructor.name,message:String(error.message).slice(0,160)});if(!watch)process.exitCode=1}
    if(!watch)break;
    log('enrich_sleep',{seconds:wait});await sleep(wait*1000);
  } while(true);
}
if(require.main===module)main().catch(()=>process.exit(1));
module.exports={runEnrich,selectTargets,buildCandidate,enrichTarget,configFromEnv,BlockedError,urlKey};
