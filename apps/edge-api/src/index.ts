import { featureCall, FeatureError, type FeaturesEnv } from './features';
import type { Entity } from '../../../packages/api-contracts/index';
import { BUILD_SLOTS, SHARED_BUILD_ID } from '../../../packages/api-contracts/builds';
import { isPagePath, robots, servePage, sitemap } from './seo';
import { highlights } from './highlights';
import { aggregateStatements, finalizeStatements, freshnessUpdate, listingIndex, offerChecked, structureBump, type ShopMark } from './aggregates';
import { cachedRoute, computeStatus, knownCategory, listingTotal, resetCaches, shopMarks, persistedHighlights } from './cache';
import { openDb, stmt, type Db } from './db';
import * as pgq from './pg';
import { categorySpecFilters, isMultiValueSpec, multiValueSpecs, specTokens, specValueOrder, compareFacetValues, compareText, type CatalogFilters } from '../../../packages/api-contracts/catalog-filters';

// Database: D1 (`DB`) by default; Supabase Postgres when a `HYPERDRIVE` binding is configured and the build includes it (see ./db.ts).
interface Bindings extends FeaturesEnv { DB?: D1Database; HYPERDRIVE?: Hyperdrive; ASSETS: Fetcher; SYNC_SECRET?: string; REPORT_WEBHOOK_URL?: string; REPORT_SALT?: string;
  DATA_MODE?: string; STALE_AFTER_SECONDS?: string; CF_VERSION_METADATA?: { id?: string }; }
type Env = Bindings & { db: Db };
/** The zod write contracts load only for the POST endpoints: zod's module setup is most of the bundle's startup cost
 * and read-only isolates (nearly all of them) never need it. */
const contracts = () => import('../../../packages/api-contracts/index');
class HttpError extends Error { constructor(public status: number, message: string) { super(message); } }
const tables: Record<Entity, string> = { category: 'catalog_categories', shop: 'catalog_shops', product: 'catalog_products', offer: 'catalog_offers', shop_check: 'shop_checks' };
const headers = { 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'strict-origin-when-cross-origin' };
function json(data: unknown, status = 200, ttl = 0) {
  return Response.json(data, { status, headers: { ...headers, ...(data && typeof data === 'object' && 'status' in data ? { 'X-SF-Has-Status':'1' } : {}),
    'Cache-Control': ttl ? `public, max-age=${ttl}` : 'no-store' } });
}
async function readBody(request: Request, limit = 65536, message = 'Batch exceeds 64 KiB') {
  if (!request.headers.get('content-type')?.startsWith('application/json')) throw new HttpError(415, 'JSON required');
  const reader = request.body?.getReader();
  if (!reader) throw new HttpError(400, 'Body required');
  const chunks: Uint8Array[] = []; let length = 0;
  while (true) {
    const { done, value } = await reader.read(); if (done) break;
    length += value.length;
    if (length > limit) { await reader.cancel(); throw new HttpError(413, message); }
    chunks.push(value);
  }
  const result = new Uint8Array(length); let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}
async function verifySigned(request: Request, env: Env) {
  if (!env.SYNC_SECRET || env.SYNC_SECRET.length < 32) throw new HttpError(503, 'Sync is not configured');
  const timestamp = request.headers.get('x-sync-timestamp') ?? '';
  const nonce = request.headers.get('x-sync-nonce') ?? '';
  const signature = request.headers.get('x-sync-signature') ?? '';
  if (!/^\d{10}$/.test(timestamp) || Math.abs(Date.now() / 1000 - Number(timestamp)) > 300 ||
      !/^[a-zA-Z0-9_-]{16,100}$/.test(nonce) || !/^[0-9a-f]{64}$/.test(signature)) throw new HttpError(401, 'Invalid sync authentication');
  const body = await readBody(request);
  const encoder = new TextEncoder();
  const prefix = encoder.encode(`${timestamp}.${nonce}.`);
  const signed = new Uint8Array(prefix.length + body.length); signed.set(prefix); signed.set(body, prefix.length);
  const key = await crypto.subtle.importKey('raw', encoder.encode(env.SYNC_SECRET), { name: 'HMAC', hash: 'SHA-256' }, false, ['verify']);
  const signatureBytes = Uint8Array.from(signature.match(/../g)!, v => parseInt(v, 16));
  if (!await crypto.subtle.verify('HMAC', key, signatureBytes, signed)) throw new HttpError(401, 'Invalid sync authentication');
  return { body, nonce };
}
async function sync(request: Request, env: Env) {
  const { body, nonce } = await verifySigned(request, env);
  let input: unknown;
  try { input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); }
  catch { throw new HttpError(400, 'Invalid JSON'); }
  const parsed = (await contracts()).batchSchema.safeParse(input);
  if (!parsed.success) throw new HttpError(400, 'Invalid sync contract');
  const batch = parsed.data;
  const now = new Date().toISOString();
  if (env.db.pg) return syncPg(env, batch, nonce);
  const statements = [stmt('DELETE FROM sync_nonces WHERE expires_at < ?', Math.floor(Date.now() / 1000)),
    stmt('INSERT INTO sync_nonces(nonce, expires_at) VALUES(?, ?)', nonce, Math.floor(Date.now() / 1000) + 600)];
  for (const event of batch.events) {
    const table = tables[event.entity as Entity];
    const data: Record<string, unknown> = event.operation === 'upsert' ? { ...event.data } : {};
    if (typeof data.last_checked_at === 'string') data.last_checked_at = new Date(data.last_checked_at).toISOString();
    if (event.entity === 'shop_check' && event.operation === 'upsert') {   // payload names -> shop_checks columns (ids JSON-encoded, ISO date)
      data.checked_at = new Date(String(data.checked_at)).toISOString();
      data.unconfirmed = JSON.stringify(data.unconfirmed_offer_ids ?? []); delete data.unconfirmed_offer_ids;
    }
    const changedAt = new Date(event.changed_at).toISOString();
    const fields = Object.keys(data);
    const values = Object.values(data).map((v, i) => fields[i] === 'specs' || fields[i] === 'retailer_codes' ? JSON.stringify(v) : typeof v === 'boolean' ? Number(v) : v);
    const columns = ['id', 'version', 'changed_at', 'deleted_at', ...fields];
    const args = [event.entity_id, event.version, changedAt, event.operation === 'delete' ? changedAt : null, ...values];
    if (event.operation === 'upsert' && event.entity === 'offer') {
      // Price history: one row per observed change of price/stock (or first observation), only for versions the
      // upsert below will accept. Runs before the upsert so it compares against the stored row; same atomic batch.
      const o = data as { product_id: string; shop_id: string; price_cents: number; in_stock: boolean; last_checked_at: string };
      statements.push(stmt(`INSERT INTO offer_price_history(offer_id,product_id,shop_id,price_cents,in_stock,observed_at)
        SELECT ?,?,?,?,?,? WHERE NOT EXISTS(SELECT 1 FROM catalog_offers c WHERE c.id=? AND (c.version>=? OR
          (c.deleted_at IS NULL AND c.price_cents=? AND c.in_stock=?)))`,
        event.entity_id, o.product_id, o.shop_id, o.price_cents, Number(o.in_stock), o.last_checked_at,
          event.entity_id, event.version, o.price_cents, Number(o.in_stock)));
    }
    statements.push(...structureBump(event.entity as Entity, event.entity_id, event.operation as 'upsert' | 'delete', data, event.version));
    if (event.operation === 'upsert' && event.entity === 'offer') statements.push(freshnessUpdate(event.entity_id, event.version, changedAt, data));
    // Table/column names originate exclusively from the validated, strict schema.
    statements.push(stmt(`INSERT INTO ${table}(${columns.join(',')}) VALUES(${columns.map(() => '?').join(',')})
      ON CONFLICT(id) DO UPDATE SET ${columns.slice(1).map(c => `${c}=excluded.${c}`).join(',')}
      WHERE excluded.version > ${table}.version`, ...args));
  }
  statements.push(...aggregateStatements(batch.events as never));
  const newest = batch.events.map(e => new Date(e.changed_at).toISOString()).sort().at(-1)!;
  statements.push(stmt(`UPDATE sync_status SET last_event_id=MAX(last_event_id, ?), last_sync_at=?,
    source_changed_at=MAX(COALESCE(source_changed_at, ''), ?) WHERE id=1`, Math.max(...batch.events.map(e => e.event_id)), now, newest));
  statements.push(...finalizeStatements(batch.events as never));
  try {
    const result = await env.db.batch(statements);
    resetCaches();
    console.log(JSON.stringify({ event: 'sync_batch', events: batch.events.length, rows_written: result.written }));
  } catch (error) {
    if (env.db.uniqueViolation(error, 'sync_nonces', 'nonce')) throw new HttpError(409, 'Nonce already used');
    throw error;
  }
  return json({ batch_id: batch.batch_id, acknowledged_event_ids: batch.events.map(e => e.event_id) });
}
/** Postgres: the whole batch is one call to stockfinder.sync_apply() (atomic; same statement sequence as D1 above). */
async function syncPg(env: Env, batch: { batch_id: string; events: Array<{ event_id: number; entity: string; entity_id: string; version: number; operation: string; changed_at: string; data?: Record<string, unknown> }> }, nonce: string) {
  const events = batch.events.map(event => {
    const data: Record<string, unknown> | undefined = event.operation === 'upsert' ? { ...event.data } : undefined;
    if (data) for (const [k, v] of Object.entries(data)) if (typeof v === 'boolean') data[k] = Number(v);
    if (data && typeof data.last_checked_at === 'string') data.last_checked_at = new Date(data.last_checked_at).toISOString();
    return { event_id: event.event_id, entity: event.entity, entity_id: event.entity_id, version: event.version, operation: event.operation,
      changed_at: new Date(event.changed_at).toISOString(), ...(data ? { data } : {}) };
  });
  try {
    const written = await pgq.applySync(env.db, nonce, events);
    resetCaches();
    console.log(JSON.stringify({ event: 'sync_batch', events: events.length, rows_written: written }));
  } catch (error) {
    if (env.db.uniqueViolation(error, 'sync_nonces', 'nonce')) throw new HttpError(409, 'Nonce already used');
    throw error;
  }
  return json({ batch_id: batch.batch_id, acknowledged_event_ids: batch.events.map(e => e.event_id) });
}
const REPORT_LIMIT_PER_HOUR = 5;
async function createReport(request: Request, env: Env, ctx: ExecutionContext) {
  const body = await readBody(request, 4096, 'Report too large');
  let input: unknown;
  try { input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); }
  catch { throw new HttpError(400, 'Invalid JSON'); }
  const parsed = (await contracts()).reportSchema.safeParse(input);
  if (!parsed.success) throw new HttpError(400, 'Invalid report');
  const report = parsed.data;
  if (report.website) return json({ id: 0 }, 201); // honeypot: pretend success, store nothing
  if (env.db.pg) return createReportPg(request, env, ctx, report);
  const product = await env.db.first<{ id: string }>('SELECT p.id FROM catalog_products p WHERE p.slug=? AND p.deleted_at IS NULL', report.product_slug);
  if (!product) throw new HttpError(404, 'Product not found');
  if (report.offer_id && !await env.db.first('SELECT 1 AS ok FROM catalog_offers WHERE id=? AND product_id=? AND deleted_at IS NULL', report.offer_id, product.id))
    throw new HttpError(404, 'Offer not found');
  const ip = request.headers.get('cf-connecting-ip') ?? 'unknown';
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${env.REPORT_SALT ?? env.SYNC_SECRET ?? ''}|${ip}`));
  const ipHash = [...new Uint8Array(digest)].slice(0, 16).map(b => b.toString(16).padStart(2, '0')).join('');
  const since = new Date(Date.now() - 3600_000).toISOString();
  const recent = await env.db.first<{ n: number }>('SELECT COUNT(*) AS n FROM product_reports WHERE ip_hash=? AND created_at>?', ipHash, since);
  if ((recent?.n ?? 0) >= REPORT_LIMIT_PER_HOUR) throw new HttpError(429, 'Too many reports');
  const pageUrl = report.page_url && /^https?:\/\//.test(report.page_url) ? report.page_url : null;
  const inserted = await env.db.first<{ id: number }>('INSERT INTO product_reports(product_slug,offer_id,note,page_url,created_at,ip_hash) VALUES(?,?,?,?,?,?) RETURNING id',
    report.product_slug, report.offer_id ?? null, report.note, pageUrl, new Date().toISOString(), ipHash);
  const id = Number(inserted!.id);
  return reportCreated(env, ctx, report, id);
}
async function ipHashOf(request: Request, env: Env) {
  const ip = request.headers.get('cf-connecting-ip') ?? 'unknown';
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(`${env.REPORT_SALT ?? env.SYNC_SECRET ?? ''}|${ip}`));
  return [...new Uint8Array(digest)].slice(0, 16).map(b => b.toString(16).padStart(2, '0')).join('');
}
/** Postgres: lookup + rate limit + queue cap + insert in one call to stockfinder.report_create(). */
async function createReportPg(request: Request, env: Env, ctx: ExecutionContext, report: { product_slug: string; offer_id?: string; note: string; page_url?: string }) {
  const pageUrl = report.page_url && /^https?:\/\//.test(report.page_url) ? report.page_url : null;
  const r = await pgq.createReport(env.db, { slug: report.product_slug, offer: report.offer_id ?? null, note: report.note, pageUrl,
    ipHash: await ipHashOf(request, env), perHour: REPORT_LIMIT_PER_HOUR });
  if (r.outcome === 'product') throw new HttpError(404, 'Product not found');
  if (r.outcome === 'offer') throw new HttpError(404, 'Offer not found');
  if (r.outcome === 'rate') throw new HttpError(429, 'Too many reports');
  if (r.outcome === 'full') throw new HttpError(503, 'Report queue full');
  return reportCreated(env, ctx, report, Number(r.report_id));
}
function reportCreated(env: Env, ctx: ExecutionContext, report: { product_slug: string; offer_id?: string; note: string }, id: number) {
  if (env.REPORT_WEBHOOK_URL) {
    const text = `Reporte #${id} · ${report.product_slug}${report.offer_id ? ` · ${report.offer_id}` : ''}: ${report.note.slice(0, 200)}`;
    ctx.waitUntil(fetch(env.REPORT_WEBHOOK_URL, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ text }) })
      .then(() => undefined, () => undefined));
  }
  return json({ id }, 201);
}
async function pullReports(request: Request, env: Env) {
  const { nonce } = await verifySigned(request, env);
  if (env.db.pg) {
    try { return json({ data: (await pgq.pullReports(env.db, nonce)).sort((a, b) => Number(a.id) - Number(b.id)) }); }
    catch (error) { if (env.db.uniqueViolation(error, 'sync_nonces', 'nonce')) throw new HttpError(409, 'Nonce already used'); throw error; }
  }
  const now = Math.floor(Date.now() / 1000);
  try {
    await env.db.batch([stmt('DELETE FROM sync_nonces WHERE expires_at < ?', now),
      stmt('INSERT INTO sync_nonces(nonce, expires_at) VALUES(?, ?)', nonce, now + 600)]);
  } catch (error) {
    if (env.db.uniqueViolation(error, 'sync_nonces', 'nonce')) throw new HttpError(409, 'Nonce already used');
    throw error;
  }
  const rows = await env.db.all(`UPDATE product_reports SET status='sent' WHERE id IN
    (SELECT id FROM product_reports WHERE status='new' ORDER BY id LIMIT 50)
    RETURNING id,product_slug,offer_id,note,page_url,created_at`);
  return json({ data: [...rows].sort((a, b) => Number(a.id) - Number(b.id)) });
}
const SHARE_LIMIT_PER_HOUR = 30;
/** POST /api/v1/builds: stores {name, slot -> slug} under a fresh UUID v4. Every slug must be a live product of that slot. */
async function createSharedBuild(request: Request, env: Env) {
  const body = await readBody(request, 4096, 'Build too large');
  let input: unknown;
  try { input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(body)); }
  catch { throw new HttpError(400, 'Invalid JSON'); }
  const parsed = (await contracts()).sharedBuildSchema.safeParse(input);
  if (!parsed.success) throw new HttpError(400, 'Invalid build');
  // Canonical slot order, so equal builds serialise identically.
  const parts = Object.fromEntries(BUILD_SLOTS.filter(slot => parsed.data.parts[slot]).map(slot => [slot, parsed.data.parts[slot]!]));
  const id = crypto.randomUUID();
  const ipHash = await ipHashOf(request, env);
  if (env.FEATURES_URL) {
    const result = await featureCall<{outcome:string;id:string}>(env,'build_create',{id,name:parsed.data.name,parts,ip_hash:ipHash});
    if (result.outcome !== 'ok') throw new HttpError(result.outcome === 'rate' ? 429 : result.outcome === 'product' ? 404 : 503, 'Could not share build');
    return json({id},201);
  }
  if (env.db.pg) {
    const outcome = await pgq.createSharedBuild(env.db, { id, name: parsed.data.name, parts: JSON.stringify(parts), ipHash, perHour: SHARE_LIMIT_PER_HOUR });
    if (outcome === 'rate') throw new HttpError(429, 'Too many shared builds');
    if (outcome === 'product') throw new HttpError(404, 'Product not found');
    if (outcome === 'full') throw new HttpError(503, 'Shared builds full');
    return json({ id }, 201);
  }
  const recent = await env.db.first<{ n: number }>('SELECT COUNT(*) AS n FROM shared_builds WHERE ip_hash=? AND created_at>?', ipHash, new Date(Date.now() - 3600_000).toISOString());
  if ((recent?.n ?? 0) >= SHARE_LIMIT_PER_HOUR) throw new HttpError(429, 'Too many shared builds');
  const slugs = Object.values(parts);
  const found = await env.db.all<{ slug: string; category_id: string }>(`SELECT p.slug,p.category_id FROM catalog_products p WHERE p.slug IN (${slugs.map(() => '?').join(',')}) AND ${visible}`, ...slugs);
  if (Object.entries(parts).some(([slot, slug]) => !found.some(row => row.slug === slug && row.category_id === slot))) throw new HttpError(404, 'Product not found');
  await env.db.run('INSERT INTO shared_builds(id,name,parts,created_at,ip_hash) VALUES(?,?,?,?,?)', id, parsed.data.name, JSON.stringify(parts), new Date().toISOString(), ipHash);
  return json({ id }, 201);
}
/** GET /api/v1/builds/:uuid — immutable, so it can be cached. */
async function sharedBuild(env: Env, id: string) {
  if (!SHARED_BUILD_ID.test(id)) throw new HttpError(404, 'Build not found');
  if (env.FEATURES_URL) return json(await featureCall(env,'build_get',{id}),200,3600);
  const row = env.db.pg ? await pgq.getSharedBuild(env.db, id)
    : await env.db.first<{ id: string; name: string; parts: string; created_at: string }>('SELECT id,name,parts,created_at FROM shared_builds WHERE id=?', id);
  if (!row) throw new HttpError(404, 'Build not found');
  return json({ data: { id: row.id, name: row.name, parts: JSON.parse(String(row.parts)), created_at: row.created_at } }, 200, 3600);
}
const status = (env: Env) => computeStatus(env);
const publicProduct = (row: Record<string, unknown>) => ({ ...row, specs: JSON.parse(String(row.specs ?? '{}')),
  retailer_codes: JSON.parse(String(row.retailer_codes ?? '[]')) });
const visible = `p.deleted_at IS NULL AND p.category_id IN (SELECT id FROM catalog_categories WHERE deleted_at IS NULL)`;
const offerCondition = `o.product_id=p.id AND o.deleted_at IS NULL AND EXISTS(SELECT 1 FROM catalog_shops s WHERE s.id=o.shop_id AND s.deleted_at IS NULL)`;
const projectionBase = `p.id,p.slug,p.name,p.category_id,p.brand,p.specs,p.manufacturer_url,p.specs_verified_at,p.mpn,p.mpn_source,p.image_path,p.image_source_url,p.image_scope,p.retailer_codes,
 p.min_price_in_stock_cents AS min_price_cents,p.offers_count`;
/** Product columns + effective freshness. The shop marks are bound as parameters (see offerChecked): `params` go FIRST in the
 * statement's arguments, the projection being the start of the SELECT. No marks (Postgres, or none published) = the plain MAX. */
const projectionFor = (marks: ShopMark[]) => {
  const eff = offerChecked(marks);
  return { sql: `${projectionBase},(SELECT MAX(${eff.sql}) FROM catalog_offers o WHERE ${offerCondition}) AS last_checked_at`, params: eff.params };
};
/** Deepest row a listing may start at (the PC builder pages through up to 960 rows). */
const MAX_OFFSET = 1200;
/** `category` must appear at most once, be a valid id and (Postgres) name a live category; '' when absent. */
async function categoryParam(url: URL, env: Env) {
  const values = url.searchParams.getAll('category');
  if (values.length > 1) throw new HttpError(400, 'Invalid category');
  const category = values[0] ?? '';
  if (category && (!/^[a-zA-Z0-9_-]{1,100}$/.test(category) || !await knownCategory(env, category))) throw new HttpError(400, 'Invalid category');
  return category;
}
function integer(value: string | null, fallback: number, max: number) {
  if (value === null) return fallback;
  if (!/^[1-9]\d*$/.test(value) || Number(value) > max) throw new HttpError(400, 'Invalid pagination');
  return Number(value);
}
async function route(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
  const url = new URL(request.url); const path = url.pathname;
  if (path === '/internal/sync') {
    if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);
    return sync(request, env);
  }
  if (path === '/internal/reports/pull') {
    if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);
    return pullReports(request, env);
  }
  if (path === '/api/v1/reports') {
    if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);
    return createReport(request, env, ctx);
  }
  if (path === '/api/v1/builds') {
    if (request.method !== 'POST') return json({ error: 'Method not allowed' }, 405);
    return createSharedBuild(request, env);
  }
  if (path === '/sitemap.xml' || path === '/robots.txt') {
    if (request.method !== 'GET' && request.method !== 'HEAD') return json({ error: 'Method not allowed' }, 405);
    if ([...url.searchParams.keys()].length) return json({ error: 'Unknown parameter' }, 400);
    return path === '/robots.txt' ? robots(url) : sitemap(env, url);
  }
  if (isPagePath(path) && (request.method === 'GET' || request.method === 'HEAD')) return servePage(request, env, url);
  if (!path.startsWith('/api/') && !path.startsWith('/internal/')) return env.ASSETS.fetch(request);
  if (request.method !== 'GET') return json({ error: 'Method not allowed' }, 405);
  if (path === '/api/v1/catalog/status') return json(await status(env));
  const shared = path.match(/^\/api\/v1\/builds\/([^/]{1,64})$/);
  if (shared) {
    if ([...url.searchParams.keys()].length) throw new HttpError(400, 'Unknown parameter');
    return sharedBuild(env, shared[1].toLowerCase());
  }
  if (path === '/api/v1/catalog/deals') {
    if ([...url.searchParams.keys()].some(k=>k!=='category') || url.searchParams.getAll('category').length>1) throw new HttpError(400,'Unknown filter');
    const category=url.searchParams.get('category');
    if (category !== null && !(BUILD_SLOTS as readonly string[]).includes(category)) throw new HttpError(400,'Invalid category');
    const data = await featureCall(env,'deals',category ? {category} : {});
    return json({data},200,data.stale ? 0 : 60);
  }
  if (path.startsWith('/api/v1/builder/')) {
    if (path === '/api/v1/builder/state') {
      if (url.search) throw new HttpError(400,'Unknown filter');
      return json(await featureCall(env,'state'),200,30);
    }
    if (path === '/api/v1/builder/options') {
      if ([...url.searchParams.keys()].some(k=>!['category','q','page','limit','sort','specs'].includes(k))) throw new HttpError(400,'Unknown filter');
      const raw = url.searchParams.get('specs') ?? '{}';
      if (raw.length>2000) throw new HttpError(400,'Too many filters');
      let specs:unknown; try { specs=JSON.parse(raw); } catch { throw new HttpError(400,'Invalid filters'); }
      const data=await featureCall(env,'options',{category:url.searchParams.get('category'),q:url.searchParams.get('q') ?? '',page:integer(url.searchParams.get('page'),1,25),specs});
      return json(data,200,data.stale ? 0 : 30);
    }
    if (path === '/api/v1/builder/watchlist') {
      const slugs=url.searchParams.getAll('slug');
      if ([...url.searchParams.keys()].some(k=>k!=='slug') || !slugs.length || slugs.length>20 || slugs.some(s=>!/^[a-zA-Z0-9_-]{1,100}$/.test(s)) || new Set(slugs).size!==slugs.length) throw new HttpError(400,'Invalid watchlist');
      return json(await featureCall(env,'watchlist',{slugs}),200,30);
    }
    const part=path.match(/^\/api\/v1\/builder\/products\/([a-zA-Z0-9_-]{1,100})(\/offers)?$/);
    if (part) {
      if (url.search) throw new HttpError(400,'Unknown filter');
      return json(await featureCall(env,part[2] ? 'offers' : 'product',{slug:part[1]}),200,30);
    }
    throw new HttpError(404,'Not found');
  }
  if (path === '/api/v1/catalog/highlights') {
    if ([...url.searchParams.keys()].length) throw new HttpError(400, 'Unknown filter');
    if (env.FEATURES_URL) { const data=await featureCall<{drops:unknown[];stale?:boolean}>(env,'deals'); return json({data:{...data,drops:data.drops.slice(0,8)}},200,data.stale ? 0 : 60); }
    const { data, stale } = await persistedHighlights(env, async () => highlights(env.db, Date.now(), await shopMarks(env)));
    return json({ data }, 200, stale ? 0 : 300);
  }
  if (path === '/api/v1/catalog/filters') {
    if ([...url.searchParams.keys()].some(key => key !== 'category')) throw new HttpError(400, 'Unknown filter');
    const category = await categoryParam(url, env);
    const where = `${visible}${category ? ' AND p.category_id=?' : ''}`;
    const args = category ? [category] : [];
    const keys = Object.hasOwn(categorySpecFilters, category) ? categorySpecFilters[category] : [];
    let brands: { results: { value: string; count: number }[] }, facets: { results: { key: string; value: string; count: number }[] }, shops: { results: { id: string; name: string; count: number }[] };
    if (env.db.pg) {
      // One UNION ALL statement for the three lists (Hyperdrive counts statements).
      const rows = await pgq.facetRows(env.db, where, args, keys);
      brands = { results: rows.brands }; facets = { results: rows.facets }; shops = { results: rows.shops };
    } else {
      brands = { results: await env.db.all<{ value: string; count: number }>(`SELECT p.brand AS value,COUNT(*) AS count FROM catalog_products p WHERE ${where} GROUP BY p.brand ORDER BY p.brand`, ...args) };
      facets = { results: keys.length ? await env.db.all<{ key: string; value: string; count: number }>(`SELECT j.key,j.value,COUNT(*) AS count FROM catalog_products p,json_each(p.specs) j WHERE ${where} AND j.key IN (${keys.map(() => '?').join(',')}) AND j.value<>'' GROUP BY j.key,j.value ORDER BY j.value`, ...args, ...keys) : [] };
      shops = { results: await env.db.all<{ id: string; name: string; count: number }>(`SELECT s.id,s.name,COUNT(DISTINCT p.id) AS count FROM catalog_products p CROSS JOIN catalog_offers o ON o.product_id=p.id AND o.deleted_at IS NULL JOIN catalog_shops s ON s.id=o.shop_id WHERE s.deleted_at IS NULL AND ${where} GROUP BY s.id,s.name ORDER BY s.name`, ...args) };
    }
    // List-valued keys are exploded into individual tokens (each product counted once per token), ordered by the key's canonical order.
    const options = (key: string) => {
      const rows = facets.results.filter(row => row.key === key);
      if (!isMultiValueSpec(category, key)) {
        const ranking = Object.hasOwn(specValueOrder, category) && Object.hasOwn(specValueOrder[category], key) ? specValueOrder[category][key] : null;
        const singles = rows.map(({ value, count }) => ({ value, count }));
        if (!ranking) return singles.sort((a, b) => compareFacetValues(a.value, b.value));
        const position = (value: string) => { const index = ranking.indexOf(value); return index < 0 ? ranking.length : index; };
        return singles.sort((a, b) => position(a.value) - position(b.value) || compareFacetValues(a.value, b.value));
      }
      const counts = new Map<string, number>();
      for (const row of rows) for (const token of specTokens(row.value)) counts.set(token, (counts.get(token) ?? 0) + row.count);
      const order = multiValueSpecs[category][key];
      const rank = (token: string) => { const index = order.indexOf(token); return index < 0 ? order.length : index; };
      return [...counts].map(([value, count]) => ({ value, count }))
        .sort((a, b) => rank(a.value) - rank(b.value) || compareText(a.value, b.value));
    };
    const data: CatalogFilters = { brands: brands.results, shops: shops.results, specs: keys.map(key => ({ key,
      ...(isMultiValueSpec(category, key) ? { multi: true } : {}), options: options(key) })).filter(facet => facet.options.length > 0) };
    return json({ data }, 200, 60);
  }
  if (path === '/api/v1/brands' || path === '/api/v1/categories' || path === '/api/v1/shops') {
    if ([...url.searchParams.keys()].length) throw new HttpError(400, 'Unknown filter');
  }
  if (path === '/api/v1/brands') {
    return json({ data: (await env.db.all('WITH RECURSIVE b(brand) AS (SELECT MIN(brand) FROM catalog_products UNION ALL SELECT (SELECT MIN(p.brand) FROM catalog_products p WHERE p.brand>b.brand) FROM b WHERE b.brand IS NOT NULL) SELECT brand FROM b WHERE brand IS NOT NULL AND EXISTS(SELECT 1 FROM catalog_products p WHERE p.brand=b.brand AND p.deleted_at IS NULL) ORDER BY brand LIMIT 2000')).map(row => row.brand) }, 200, 60);
  }
  if (path === '/api/v1/categories' || path === '/api/v1/shops') {
    const table = path.endsWith('categories') ? 'catalog_categories' : 'catalog_shops';
    const fields = table === 'catalog_categories' ? 'id,slug,name' : 'id,name,url_public';
    return json({ data: await env.db.all(`SELECT ${fields} FROM ${table} WHERE deleted_at IS NULL ORDER BY name LIMIT 500`) }, 200, 60);
  }
  if (path === '/api/v1/watchlist') {
    if ([...url.searchParams.keys()].some(k => k !== 'slug')) throw new HttpError(400, 'Unknown filter');
    const slugs = url.searchParams.getAll('slug');
    if (!slugs.length || slugs.length > 20 || slugs.some(s => !/^[a-zA-Z0-9_-]{1,100}$/.test(s)) || new Set(slugs).size !== slugs.length) throw new HttpError(400, 'Invalid watchlist');
    const eff = offerChecked(await shopMarks(env));
    const since = new Date(Date.now() - 86400000).toISOString();
    // Lightweight and conservative: stale offers cannot trigger a price or restock alert.
    const rows = await env.db.all(`SELECT p.slug,p.name,
      (SELECT MIN(o.price_cents) FROM catalog_offers o WHERE ${offerCondition} AND o.in_stock=1 AND ${eff.sql}>=?) AS min_price_cents,
      (SELECT MAX(${eff.sql}) FROM catalog_offers o WHERE ${offerCondition}) AS last_checked_at
      FROM catalog_products p WHERE ${visible} AND p.slug IN (${slugs.map(() => '?').join(',')}) ORDER BY p.slug LIMIT 20`,
      ...eff.params, since, ...eff.params, ...slugs);
    return json({ data: rows }, 200, 60);
  }
  if (path === '/api/v1/products') {
    const allowed = new Set(['page', 'limit', 'q', 'category', 'brand', 'sort', 'stock', 'shop', 'min_price', 'max_price', 'specs']);
    if ([...url.searchParams.keys()].some(k => !allowed.has(k))) throw new HttpError(400, 'Unknown filter');
    const page = integer(url.searchParams.get('page'), 1, 1000);
    const limit = integer(url.searchParams.get('limit'), 24, 48);
    if ((page - 1) * limit >= MAX_OFFSET) throw new HttpError(400, 'Invalid pagination');
    const q = (url.searchParams.get('q') ?? '').trim();
    if (q.length > 100) throw new HttpError(400, 'Search too long');
    if (q.length === 1) throw new HttpError(400, 'Search too short');
    await categoryParam(url, env);
    const pg = env.db.pg;
    const clauses = [visible]; const args: (string | number)[] = [];
    const terms = q.split(/\s+/).filter(Boolean);
    if (terms.length > 6 || terms.some(term => term.length > 50)) throw new HttpError(400, 'Search too long');
    // instr() instead of LIKE: no wildcard semantics to escape, and D1 caps LIKE patterns at 50 bytes.
    for (const term of terms) {
      if (pg) {
        // One LIKE over search_text() (name, brand, MPN and retailer codes, newline-separated), served by a pg_trgm GIN index.
        clauses.push(`${pgq.SEARCH_TEXT} LIKE ?`); args.push(pgq.likeTerm(term));
        continue;
      }
      clauses.push(`(instr(LOWER(p.name),LOWER(?))>0 OR instr(LOWER(p.brand),LOWER(?))>0 OR instr(LOWER(p.mpn),LOWER(?))>0 OR
        EXISTS(SELECT 1 FROM json_each(p.retailer_codes) j WHERE instr(LOWER(json_extract(j.value,'$.code')),LOWER(?))>0))`);
      args.push(term, term, term, term);
    }
    for (const [param, field] of [['category', 'category_id']]) {
      const value = url.searchParams.get(param);
      if (value) { if (value.length > 250) throw new HttpError(400, 'Filter too long'); clauses.push(`p.${field}=?`); args.push(value); }
    }
    const brands = url.searchParams.getAll('brand');
    if (brands.length > 24 || brands.some(value => !value.length || value.length > 250)) throw new HttpError(400, 'Invalid brand filter');
    if (brands.length) { clauses.push(`p.brand IN (${brands.map(() => '?').join(',')})`); args.push(...brands); }
    const shopIds = url.searchParams.getAll('shop');
    if (shopIds.length > 24 || shopIds.some(value => !value.length || value.length > 100)) throw new HttpError(400, 'Invalid shop filter');
    if (shopIds.length) { clauses.push(`EXISTS(SELECT 1 FROM catalog_offers o WHERE ${offerCondition} AND o.shop_id IN (${shopIds.map(() => '?').join(',')}))`); args.push(...shopIds); }
    const stock = url.searchParams.get('stock');
    if (stock && stock !== '1') throw new HttpError(400, 'Invalid stock filter');
    if (stock) clauses.push('p.min_price_in_stock_cents IS NOT NULL');
    const prices = ['min_price', 'max_price'].map(param => {
      const value = url.searchParams.get(param);
      if (value === null) return null;
      if (!/^\d{1,9}$/.test(value) || Number(value) > 100000000) throw new HttpError(400, 'Invalid price');
      return Number(value);
    });
    if (prices[0] !== null && prices[1] !== null && prices[0] > prices[1]) throw new HttpError(400, 'Invalid price range');
    prices.forEach((value, index) => {
      if (value === null) return;
      clauses.push(`p.min_price_in_stock_cents ${index === 0 ? '>=' : '<='} ?`);
      args.push(value);
    });
    const rawSpecs = url.searchParams.get('specs');
    if (rawSpecs !== null) {
      if (rawSpecs.length > 12000) throw new HttpError(400, 'Too many specification filters');
      let specs: unknown;
      try { specs = JSON.parse(rawSpecs); } catch { throw new HttpError(400, 'Invalid specification filters'); }
      const category = url.searchParams.get('category') ?? '';
      const keys = Object.hasOwn(categorySpecFilters, category) ? categorySpecFilters[category] : [];
      if (!specs || Array.isArray(specs) || typeof specs !== 'object' || Object.keys(specs).length > 8) throw new HttpError(400, 'Invalid specification filters');
      for (const [key, values] of Object.entries(specs)) {
        if (!keys.includes(key) || !Array.isArray(values) || !values.length || values.length > 24 || values.some(value => typeof value !== 'string' || !value.length || value.length > 250)) throw new HttpError(400, 'Invalid specification filter');
        if (isMultiValueSpec(category, key)) {
          if (values.some(value => /[,;/]/.test(value))) throw new HttpError(400, 'Invalid specification filter');
          // Contains-any (OR) over delimited tokens: ", ATX, " never matches inside ", Micro-ATX, " or ", E-ATX, ". Two binds however many tokens.
          clauses.push(pg
            ? `EXISTS(SELECT 1 FROM json_array_elements_text(?::json) t WHERE strpos(', ' || (p.specs->>?::text) || ', ', ', ' || t.value || ', ')>0)`
            : `EXISTS(SELECT 1 FROM json_each(?) t WHERE instr(', ' || json_extract(p.specs, ?) || ', ', ', ' || t.value || ', ')>0)`);
          args.push(JSON.stringify(values), pg ? key : `$."${key}"`);
        } else {
          clauses.push(pg ? `(p.specs->>?::text) IN (${values.map(() => '?').join(',')})` : `json_extract(p.specs, ?) IN (${values.map(() => '?').join(',')})`);
          args.push(pg ? key : `$."${key}"`, ...values);
        }
      }
    }
    const sort = url.searchParams.get('sort') ?? 'name';
    const sorts: Record<string, string> = { featured: '(p.min_price_in_stock_cents IS NULL),p.id', name: 'p.name,p.id', price: '(p.min_price_in_stock_cents IS NULL),p.min_price_in_stock_cents,p.id', '-price': '(p.min_price_in_stock_cents IS NULL),p.min_price_in_stock_cents DESC,p.id' };
    if (!Object.hasOwn(sorts, sort)) throw new HttpError(400, 'Invalid sort');
    const where = clauses.join(' AND ');
    let order = sorts[sort]; const orderArgs: string[] = [];
    if (terms.length && sort === 'featured') {
      order = pg
        ? `CASE WHEN lower(p.mpn)=? OR EXISTS(SELECT 1 FROM json_array_elements(p.retailer_codes) j WHERE lower((j->>'code') COLLATE "C")=?) THEN 0
        WHEN starts_with(lower(p.name),?) THEN 1 ELSE 2 END,${order}`
        : `CASE WHEN LOWER(p.mpn)=LOWER(?) OR EXISTS(SELECT 1 FROM json_each(p.retailer_codes) j WHERE LOWER(json_extract(j.value,'$.code'))=LOWER(?)) THEN 0
        WHEN instr(LOWER(p.name),LOWER(?))=1 THEN 1 ELSE 2 END,${order}`;
      orderArgs.push(...(pg ? [pgq.asciiLower(q), pgq.asciiLower(q), pgq.asciiLower(terms[0])] : [q, q, terms[0]]));
    }
    const proj = projectionFor(await shopMarks(env));
    // limit+1 rows: a short page already tells the exact total (no COUNT); otherwise one COUNT, persisted per structural filter set.
    const offset = (page - 1) * limit;
    if (pg) {
      // Rows and total in one statement (window count); a page past the end needs one extra COUNT (offset is capped).
      const found = await env.db.all<Record<string, unknown>>(`SELECT ${proj.sql},COUNT(*) OVER() AS total_rows FROM catalog_products p WHERE ${where} ORDER BY ${order} LIMIT ? OFFSET ?`, ...proj.params, ...args, ...orderArgs, limit, offset);
      const total = found.length ? Number(found[0].total_rows) : offset === 0 ? 0
        : Number((await env.db.first<{ total: number }>(`SELECT COUNT(*) AS total FROM catalog_products p WHERE ${where}`, ...args))!.total);
      const data = found.map(({ total_rows: _, ...row }) => publicProduct(row));
      return json({ data, total, page, limit, status: await status(env) }, 200, 60);
    }
    const hint = listingIndex(!!url.searchParams.get('category'), sort, terms.length > 0 || !!stock || prices.some(value => value !== null));
    const rows = await env.db.query<Record<string, unknown>>(`SELECT ${proj.sql} FROM catalog_products p${hint} WHERE ${where} ORDER BY ${order} LIMIT ? OFFSET ?`, ...proj.params, ...args, ...orderArgs, limit + 1, offset);
    const total = await listingTotal(env, url, { offset, rows: rows.results.length, limit },
      async () => Number((await env.db.first<{ total: number }>(`SELECT COUNT(*) AS total FROM catalog_products p WHERE ${where}`, ...args))!.total));
    console.log(JSON.stringify({ event: 'catalog_read', rows_read: rows.rowsRead }));
    return json({ data: rows.results.slice(0, limit).map(publicProduct), total, page, limit, status: await status(env) }, 200, 60);
  }
  const match = path.match(/^\/api\/v1\/products\/([a-zA-Z0-9_-]{1,100})(\/offers|\/price-history)?$/);
  if (match && env.db.pg && match[2]) {
    // Postgres: product lookup and its offers/history in one statement.
    if (match[2] === '/price-history') {
      if ([...url.searchParams.keys()].some(key => key !== 'days')) throw new HttpError(400, 'Unknown filter');
      const rawDays = url.searchParams.get('days');
      if (rawDays !== null && (!/^[1-9]\d{0,2}$/.test(rawDays) || Number(rawDays) > 730)) throw new HttpError(400, 'Invalid days');
      const since = rawDays === null ? null : new Date(Date.now() - Number(rawDays) * 86400_000).toISOString();
      const rows = await pgq.productHistory(env.db, match[1], since, visible);
      if (!rows) throw new HttpError(404, 'Product not found');
      return json({ data: rows, status: await status(env) }, 200, 300);
    }
    const offers = await pgq.productOffers(env.db, match[1], visible);
    if (!offers) throw new HttpError(404, 'Product not found');
    return json({ data: offers, status: await status(env) }, 200, 10);
  }
  if (match) {
    const proj = match[2] ? { sql: 'p.id', params: [] } : projectionFor(await shopMarks(env));
    const product = await env.db.first<Record<string, unknown>>(`SELECT ${proj.sql} FROM catalog_products p WHERE p.slug=? AND ${visible}`, ...proj.params, match[1]);
    if (!product) throw new HttpError(404, 'Product not found');
    if (match[2] === '/price-history') {
      if ([...url.searchParams.keys()].some(key => key !== 'days')) throw new HttpError(400, 'Unknown filter');
      const rawDays = url.searchParams.get('days');
      if (rawDays !== null && (!/^[1-9]\d{0,2}$/.test(rawDays) || Number(rawDays) > 730)) throw new HttpError(400, 'Invalid days');
      const since = rawDays === null ? '' : new Date(Date.now() - Number(rawDays) * 86400_000).toISOString();
      // Newest 500 rows of the window, returned oldest first.
      const rows = await env.db.all(`SELECT offer_id,shop_name,price_cents,in_stock,observed_at FROM (SELECT h.id,h.offer_id,s.name AS shop_name,h.price_cents,h.in_stock,h.observed_at
        FROM offer_price_history h JOIN catalog_shops s ON s.id=h.shop_id AND s.deleted_at IS NULL
        WHERE h.product_id=? AND h.observed_at>=? ORDER BY h.observed_at DESC,h.id DESC LIMIT 500) ORDER BY observed_at,id`, product.id, since);
      return json({ data: rows, status: await status(env) }, 200, 300);
    }
    if (!match[2]) return json({ data: publicProduct(product), status: await status(env) }, 200, 15);
    const eff = offerChecked(await shopMarks(env));
    const offers = await env.db.all(`SELECT o.id,s.name AS shop_name,o.price_cents,o.in_stock,${eff.sql} AS last_checked_at,o.url_public
      FROM catalog_offers o JOIN catalog_shops s ON s.id=o.shop_id AND s.deleted_at IS NULL
      WHERE o.product_id=? AND o.deleted_at IS NULL ORDER BY o.in_stock DESC,o.price_cents,o.id LIMIT 200`, ...eff.params, product.id);
    return json({ data: offers, status: await status(env) }, 200, 10);
  }
  return json({ error: 'Not found' }, 404);
}
export default {
  async fetch(request: Request, bindings: Bindings, ctx: ExecutionContext) {
    const started = performance.now();
    let outcome: Response | undefined;
    let db: Db | undefined;
    // Background work (persisted-cache writes, webhooks) is tracked so the per-request Postgres connection closes after it.
    const pending: Promise<unknown>[] = [];
    const tracked = { waitUntil: (p: Promise<unknown>) => { pending.push(p); ctx.waitUntil(p); },
      passThroughOnException: () => ctx.passThroughOnException() } as ExecutionContext;
    try {
      db = await openDb(bindings);
      const env: Env = { ...bindings, db };
      outcome = await cachedRoute(request, env, tracked, () => route(request, env, tracked));
      return outcome;
    }
    catch (error) {
      if (error instanceof HttpError || error instanceof FeatureError) { outcome = json({ error: error.message }, error.status); return outcome; }
      // Only the error class/SQLSTATE (no message: it could echo data or connection details).
      const code = (error as { code?: unknown } | null)?.code;
      console.error(JSON.stringify({ event: 'request_failed', error: 'database_or_runtime_error', ...(typeof code === 'string' ? { code: code.slice(0, 40) } : {}) }));
      outcome = json({ error: 'Service temporarily unavailable' }, 503);
      return outcome;
    }
    finally {
      // Wall time is diagnostic, not CPU time. Use Workers Analytics/profiling for CPU percentiles.
      if (Math.random() < 0.05) {
      const pathname = new URL(request.url).pathname;
      const routeName = pathname.replace(/(\/products\/)[^/]+/, '$1:slug').replace(/^\/p\/[^/]+$/, '/p/:slug').replace(/(\/builds\/)[^/]+/, '$1:id');
      console.log(JSON.stringify({ event: 'request_sample', route: routeName,
        method: request.method, status: outcome?.status ?? 503, cache: outcome?.headers.get('x-sf-cache') ?? 'bypass', wall_ms: Math.round(performance.now() - started) }));
      }
      const open = db;
      if (open?.pg) ctx.waitUntil(Promise.allSettled(pending).then(() => open.close()).catch(() => undefined));
    }
  }
};
