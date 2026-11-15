// Public read caching. Layers, cheapest first:
//   1. in-isolate memo of the data version (5 s) and of the freshness status (15 s); on Postgres both come from ONE
//      state query memoised 15 s. If the database fails, the last known value is served (stale beats an error page).
//   2. Workers Cache API, keyed by deploy id + version + normalised URL (long TTL; a sync changes the key). Structural
//      responses (filters, brands, categories, shops, sitemap) are keyed by facets_version only, so price refreshes
//      do not evict them. HTML pages are keyed by path only (no query string): /p/* by data version, /c/* by
//      facets_version, '/' and the informational pages by deploy alone, so repeat views skip D1 and HTMLRewriter.
//      Hits are served as stored bytes; JSON only gets its trailing status object refreshed (refreshStatus).
//   3. D1 only: `api_cache` table (structural responses and counts shared by all colos, valid for one facets_version).
//      Not ported to Postgres: Hyperdrive counts every statement and a per-query-string table could be filled with
//      random URLs (see docs/supabase-migration.md).
// Anything that is not a 200 `public` response is never stored. Authenticated/`no-cache` requests bypass layers 2-3 for responses.
import { OFFER_ACTIVE, offerChecked, type ShopMark } from './aggregates';
import { stmt, type Db } from './db';
import { stateRow } from './pg';

export interface CacheEnv { FEATURES_URL?: string; db: Db; DATA_MODE?: string; STALE_AFTER_SECONDS?: string; CF_VERSION_METADATA?: { id?: string } }
export interface DataVersion { key: string; facets: number }

const EDGE_TTL = 3600; // seconds; safe because the key embeds the data version
const HOUR_MS = 3600_000;
const hourBucket = () => Math.floor(Date.now() / HOUR_MS);
const VERSION_MEMO_MS = 5000, STATUS_MEMO_MS = 15000, PG_STATE_MEMO_MS = 15000;
let versionMemo: { at: number; value: DataVersion } | null = null;
let statusMemo: { at: number; value: Status } | null = null;
interface PgState { version: DataVersion; status: Status; categories: Set<string> }
let pgMemo: { at: number; value: PgState } | null = null;
const scopedMemo = new Map<string, { at: number; value: DataVersion }>();
let marksMemo: { at: number; value: ShopMark[] } | null = null;
/** Called by sync() so this isolate sees new data immediately (other isolates catch up within the memo TTL). */
export function resetCaches() { scopedMemo.clear(); versionMemo = null; statusMemo = null; pgMemo = null; marksMemo = null; }

/** Shop pass marks (D1: shop_checks, a handful of rows), memoised like the status. Postgres has none. A failed read serves the last
 * known marks or none (= every offer keeps its own date), never an error page. */
export async function shopMarks(env: CacheEnv): Promise<ShopMark[]> {
  if (env.db.pg) return [];
  if (marksMemo && Date.now() - marksMemo.at < STATUS_MEMO_MS) return marksMemo.value;
  try {
    const value = await env.db.all<ShopMark>('SELECT id,checked_at,unconfirmed FROM shop_checks WHERE deleted_at IS NULL ORDER BY id');
    marksMemo = { at: Date.now(), value };
    return value;
  } catch {
    return marksMemo?.value ?? [];
  }
}

/** Landing payload (categories + price drops) persisted in api_cache under ONE row per hour (`hourly:highlights:24h`, version = hour bucket):
 * the heavy window query runs at most once per hour overall, whatever happens in between. The key deliberately does NOT include
 * facets_version: import bursts (~172 batches of 20 product events) bump it per batch, and a structure-keyed row would be recomputed
 * by every request between two batches. Category counts and price drops can therefore be up to one hour old (accepted); the rest of
 * the site keeps its own, structure-aware caches. Same rules as `hourly:fresh`: the key is a JS string (never a bound number in
 * SQL) and finalizeStatements() evicts the row when the hour rolls over, never on a structural bump. A failed read/compute serves the
 * last payload this isolate saw for up to two hours after calculation. Postgres computes directly. */
const HIGHLIGHTS_KEY = 'hourly:highlights:24h';
let highlightsMemo: { data: unknown; calculatedAt: number } | null = null;
export async function persistedHighlights<T extends object>(env: CacheEnv, compute: () => Promise<T>) {
  if (env.db.pg) return { data: await compute(), stale: false };
  const hour = hourBucket();
  try {
    const hit = await env.db.first<{ body: string }>('SELECT body FROM api_cache WHERE key=? AND version=?', HIGHLIGHTS_KEY, hour);
    if (hit) {
      const { calculated_at_ms, ...data } = JSON.parse(hit.body);
      // Older rows have no timestamp. Use the start of their bucket as the earliest calculation time.
      highlightsMemo = { data, calculatedAt: calculated_at_ms ?? hour * HOUR_MS };
      return { data: data as T, stale: false };
    }
    const data = await compute();
    const calculatedAt = Date.now();
    highlightsMemo = { data, calculatedAt };
    await env.db.run(cachePut(['key', 'version', 'body']), HIGHLIGHTS_KEY, hour,
      JSON.stringify({ ...data, calculated_at_ms: calculatedAt })).catch(() => undefined);
    return { data, stale: false };
  } catch (error) {
    if (highlightsMemo && Date.now() - highlightsMemo.calculatedAt < 2 * HOUR_MS) {
      return { data: highlightsMemo.data as T, stale: true };
    }
    throw error;
  }
}

/** Whole-row replace of an api_cache entry (columns not listed become NULL). D1 only. */
const cachePut = (columns: string[]) => `INSERT OR REPLACE INTO api_cache(${columns}) VALUES(${columns.map(() => '?')})`;

async function pgState(env: CacheEnv): Promise<PgState> {
  if (pgMemo && Date.now() - pgMemo.at < PG_STATE_MEMO_MS) return pgMemo.value;
  try {
    const r = await stateRow(env.db, FRESH_WINDOW_HOURS);
    const value: PgState = {
      version: { key: `${r?.data_version ?? 0}.${r?.facets_version ?? 0}`, facets: Number(r?.facets_version ?? 0) },
      status: buildStatus(env, r ?? undefined, Number(r?.offers_fresh ?? 0)),
      categories: new Set(JSON.parse(String(r?.categories ?? '[]')) as string[])
    };
    pgMemo = { at: Date.now(), value };
    return value;
  } catch (error) {
    if (pgMemo) return pgMemo.value;
    throw error;
  }
}

export async function dataVersion(env: CacheEnv): Promise<DataVersion> {
  if (env.db.pg) return (await pgState(env)).version;
  if (versionMemo && Date.now() - versionMemo.at < VERSION_MEMO_MS) return versionMemo.value;
  try {
    const row = await env.db.first<{ data_version: number; facets_version: number }>('SELECT data_version,facets_version FROM sync_status WHERE id=1');
    const value = { key: `${row?.data_version ?? 0}.${row?.facets_version ?? 0}`, facets: row?.facets_version ?? 0 };
    versionMemo = { at: Date.now(), value };
    return value;
  } catch (error) {
    if (versionMemo) return versionMemo.value;
    throw error;
  }
}

/** Postgres: is `id` a live category (from the memoised state; D1 does not check). */
export async function knownCategory(env: CacheEnv, id: string) {
  return !env.db.pg || (await pgState(env)).categories.has(id);
}

const TRACKING = /^(utm_[a-z]+|fbclid|gclid|msclkid|dclid|mc_[a-z]+|_ga|igshid)$/i;
/** Sorted query string without tracking params and without defaults that cannot change the response. */
export function normalizedSearch(url: URL) {
  const isProducts = url.pathname === '/api/v1/products';
  const pairs = [...url.searchParams].filter(([k, v]) => !TRACKING.test(k) &&
    !(isProducts && ((k === 'page' && v === '1') || (k === 'limit' && v === '24') || (k === 'sort' && v === 'name') || (k === 'q' && !v.trim()) || (k === 'category' && !v))));
  pairs.sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0);
  return new URLSearchParams(pairs).toString();
}

export interface Status { data_updated_at: string | null; source_changed_at: string | null; last_event_id: number; age_seconds: number | null;
  offers_checked_at: string | null; offers_total: number; offers_fresh: number; offers_fresh_hours: number; sync_lag_seconds: number | null; stale: boolean; data_mode: string }
/** Time-dependent fields only; everything else comes from the stored object. */
export function withFreshness<T extends Pick<Status, 'source_changed_at' | 'offers_checked_at' | 'stale' | 'age_seconds'>>(s: T, staleAfter: number): T {
  const since = (iso: string | null) => iso && !Number.isNaN(Date.parse(iso)) ? Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000)) : null;
  const age = since(s.source_changed_at), basis = since(s.offers_checked_at) ?? age;
  return { ...s, age_seconds: age, stale: basis === null || basis > staleAfter };
}

/** Window of the public "Precios comprobados" share. Refreshes run every 1–3 h; an offer's effective freshness is the shop
 * mark of its last confirmed pass (shop_checks, see offerChecked) or, if it was unconfirmed or the shop has no mark, its own
 * last_checked_at (renewed at most every FRESHNESS_SECONDS in nas/catalog.py), so a healthy offer stays well under 12 h. */
export const FRESH_WINDOW_HOURS = 12;
/* The hourly count is cached in api_cache as key `hourly:fresh<H>:<facets_version>`, version = hour. The key MUST be matched with the
 * window interpolated into the SQL text, never a bound number: D1 binds JS numbers as REAL, so `'hourly:fresh'||?` produced 'hourly:fresh12.0'
 * and the lookup never hit (a 5.8k-row scan on every status computation). A shop mark does not bump facets_version, so the share lags a
 * shop pass by up to an hour: that is the documented design (one scan per hour), and offers_checked_at/stale are live. */
const staleAfter = (env: CacheEnv) => Number(env.STALE_AFTER_SECONDS ?? 86400);

type StatusRow = { last_event_id?: unknown; last_sync_at?: unknown; source_changed_at?: unknown; offers_total?: unknown; offers_checked_max?: unknown; shop_checked_max?: unknown };
function buildStatus(env: CacheEnv, r: StatusRow | undefined, fresh: number): Status {
  // Newest check = newest offer date or newest shop mark (ISO strings compare chronologically).
  const own = (r?.offers_checked_max as string | null) ?? null, mark = (r?.shop_checked_max as string | null) ?? null;
  const at = own && mark ? (mark > own ? mark : own) : own ?? mark;
  const total = Number(r?.offers_total ?? 0);
  const lastSync = (r?.last_sync_at as string | null) ?? null, source = (r?.source_changed_at as string | null) ?? null;
  return withFreshness<Status>({ data_updated_at: lastSync, source_changed_at: source, last_event_id: Number(r?.last_event_id ?? 0),
    age_seconds: null, offers_checked_at: at && !Number.isNaN(Date.parse(at)) ? new Date(at).toISOString() : null,
    offers_total: total, offers_fresh: Math.min(total, fresh), offers_fresh_hours: FRESH_WINDOW_HOURS,
    sync_lag_seconds: source && lastSync ? Math.max(0, Math.floor((Date.parse(lastSync) - Date.parse(source)) / 1000)) : null,
    stale: true, data_mode: env.DATA_MODE ?? 'unknown' }, staleAfter(env));
}

/** Freshness status. D1: ~3 row reads (sync_status, hourly-cached fresh count). Postgres: the memoised state query. */
export async function computeStatus(env: CacheEnv): Promise<Status> {
  if (env.db.pg) return withFreshness((await pgState(env)).status, staleAfter(env));
  if (statusMemo && Date.now() - statusMemo.at < STATUS_MEMO_MS) return withFreshness(statusMemo.value, staleAfter(env));
  const hour = Math.floor(Date.now() / 3600_000);
  let row: Record<string, unknown>[], cached: Record<string, unknown>[];
  try {
    [row, cached] = await env.db.reads([
      stmt('SELECT last_event_id,last_sync_at,source_changed_at,offers_total,offers_checked_max,facets_version,(SELECT MAX(checked_at) FROM shop_checks WHERE deleted_at IS NULL) AS shop_checked_max FROM sync_status WHERE id=1'),
      stmt(`SELECT body FROM api_cache WHERE key='hourly:fresh${FRESH_WINDOW_HOURS}:'||(SELECT facets_version FROM sync_status WHERE id=1) AND version=?`, hour)
    ]);
  } catch (error) {
    if (statusMemo) return withFreshness(statusMemo.value, staleAfter(env));
    throw error;
  }
  const r = row[0] as (StatusRow & { facets_version: number }) | undefined;
  // Offers checked in the last FRESH_WINDOW_HOURS: a table scan at most once per hour (no index on last_checked_at, it changes on every refresh).
  let fresh = (cached[0] as { body: string } | undefined)?.body;
  if (fresh === undefined) {
    const eff = offerChecked(await shopMarks(env));
    const n = await env.db.first<{ n: number }>(`SELECT COUNT(*) AS n FROM catalog_offers o WHERE ${eff.sql}>=? AND ${OFFER_ACTIVE}`,
      ...eff.params, new Date(Date.now() - FRESH_WINDOW_HOURS * 3600_000).toISOString());
    fresh = String(n?.n ?? 0);
    await env.db.run(cachePut(['key', 'version', 'body']), `hourly:fresh${FRESH_WINDOW_HOURS}:${r?.facets_version ?? 0}`, hour, fresh).catch(() => undefined);
  }
  const value = buildStatus(env, r, Number(fresh));
  statusMemo = { at: Date.now(), value };
  return value;
}

/** Total for a listing. Free when the page already shows the end of the list; otherwise one COUNT, persisted (D1) when price/stock-independent. */
export async function listingTotal(env: CacheEnv, url: URL, shown: { offset: number; rows: number; limit: number }, count: () => Promise<number>): Promise<number> {
  if (shown.rows <= shown.limit && (shown.rows > 0 || shown.offset === 0)) return shown.offset + shown.rows;
  const params = new URL(url); for (const k of ['page', 'limit', 'sort']) params.searchParams.delete(k);
  const structural = !['q', 'stock', 'min_price', 'max_price'].some(k => params.searchParams.has(k));
  if (!structural || env.db.pg) return count();
  try {
    const version = await dataVersion(env);
    const key = `count:${env.CF_VERSION_METADATA?.id ?? ''}:${normalizedSearch(params)}`;
    const hit = await env.db.first<{ body: string }>('SELECT body FROM api_cache WHERE key=? AND version=?', key, version.facets);
    if (hit) return Number(hit.body);
    const total = await count();
    await env.db.run(cachePut(['key', 'version', 'body']), key, version.facets, String(total));
    return total;
  } catch { return count(); }
}

/** Structural responses persisted in api_cache (D1) and keyed by facets_version. */
const isStructural = (path: string) => path === '/api/v1/catalog/filters' || path === '/api/v1/brands' || path === '/api/v1/categories' || path === '/api/v1/shops' || path === '/sitemap.xml';
/** HTML pages whose rewritten shell does not depend on the catalogue at all (only on the deploy's index.html). */
const STATIC_PAGES = new Set(['/', '/configurador', '/ofertas', '/acerca', '/privacidad']);
const isProductPage = (path: string) => /^\/p\/[^/]*$/.test(path);
const isCategoryPage = (path: string) => /^\/c\/[^/]*$/.test(path);
const isCacheable = (path: string) => (path.startsWith('/api/v1/') && path !== '/api/v1/catalog/status') || path === '/sitemap.xml' ||
  STATIC_PAGES.has(path) || isProductPage(path) || isCategoryPage(path);
/** Cache-key version segment: static pages never change within a deploy; category pages and structural responses
 * only with facets_version; everything else (product pages and listings embed prices) with the data version. */
async function keyVersion(env: CacheEnv, path: string, url?: URL) {
  if (path.startsWith('/api/v1/builder/') || path === '/api/v1/catalog/deals' || (env.FEATURES_URL && path.startsWith('/api/v1/builds/'))) {
    return { segment:`features:${Math.floor(Date.now()/30000)}`,version:null };
  }
  // D1: price changes in another category do not evict this catalogue/product's cache.
  const category = path === '/api/v1/products' ? url?.searchParams.get('category') : null;
  const product = path.match(/^(?:\/api\/v1\/products|\/p)\/([a-zA-Z0-9_-]{1,100})(?:\/offers|\/price-history)?$/)?.[1];
  if (!env.db.pg && (category || product)) {
    const id = category ? `c:${category}` : `p:${product}`;
    let memo = scopedMemo.get(id);
    if (!memo || Date.now() - memo.at >= VERSION_MEMO_MS) {
      const row = await env.db.first<{ version: number; facets_version: number }>(
        `SELECT COALESCE((SELECT version FROM category_cache_epochs WHERE category_id=${category ? '?' : '(SELECT category_id FROM catalog_products WHERE slug=?)'}),0) AS version,
          facets_version FROM sync_status WHERE id=1`, category ?? product);
      const value = { key: `scoped:${row?.facets_version ?? 0}:${row?.version ?? 0}`, facets: row?.facets_version ?? 0 };
      if (scopedMemo.size >= 256) scopedMemo.delete(scopedMemo.keys().next().value!);
      memo = { at: Date.now(), value }; scopedMemo.set(id, memo);
    }
    return { segment: memo.value.key, version: memo.value };
  }
  if (STATIC_PAGES.has(path)) return { segment: 's', version: null };
  const version = await dataVersion(env);
  return { segment: isStructural(path) || isCategoryPage(path) ? `f${version.facets}` : version.key, version };
}

async function persisted(env: CacheEnv, ctx: ExecutionContext, id: string, version: DataVersion, produce: () => Promise<Response>): Promise<Response> {
  if (env.db.pg) return produce();
  const key = `${id}:${env.CF_VERSION_METADATA?.id ?? ''}`;
  try {
    const row = await env.db.first<{ content_type: string; cache_control: string; body: string }>('SELECT content_type,cache_control,body FROM api_cache WHERE key=? AND version=?', key, version.facets);
    if (row) return new Response(row.body, { headers: { 'content-type': row.content_type, 'cache-control': row.cache_control, 'x-content-type-options': 'nosniff' } });
  } catch { return produce(); }
  const response = await produce();
  if (response.status === 200 && response.headers.get('cache-control')?.startsWith('public,')) {
    ctx.waitUntil(response.clone().text().then(body => env.db.run(cachePut(['key', 'version', 'content_type', 'cache_control', 'body']),
      key, version.facets, response.headers.get('content-type'), response.headers.get('cache-control'), body)).then(() => undefined, () => undefined));
  }
  return response;
}

/** Wraps the router: Cache API (version-keyed) -> persisted structural responses (D1) -> route(). */
export async function cachedRoute(request: Request, env: CacheEnv, ctx: ExecutionContext, route: () => Promise<Response>): Promise<Response> {
  const url = new URL(request.url);
  const highlights = url.pathname === '/api/v1/catalog/highlights';
  const cacheable = request.method === 'GET' && isCacheable(url.pathname) && !request.headers.has('authorization') && !request.headers.has('cookie') &&
    !/no-cache|no-store/i.test(request.headers.get('cache-control') ?? '');
  // The sitemap takes no parameters (route() answers 400): never look it up or persist it per query string.
  if (!cacheable || ((highlights || url.pathname === '/sitemap.xml') && url.search)) return route();
  const page = !url.pathname.startsWith('/api/') && url.pathname !== '/sitemap.xml';
  // HTML pages are rendered from the path alone, so the query string (filters, tracking) is not part of their key.
  const search = page ? '' : normalizedSearch(url);
  const hour = highlights ? hourBucket() : null;
  const { segment, version } = hour === null
    ? await keyVersion(env, url.pathname, url)
    : { segment: `h${hour}`, version: null };
  const key = new Request(`${url.origin}/__sf/${encodeURIComponent(env.CF_VERSION_METADATA?.id ?? 'dev')}/${segment}${url.pathname}${search ? `?${search}` : ''}`);
  const cache = await caches.open('stockfinder-public-v2');
  const hit = await cache.match(key);
  const remaining = () => hour === null ? EDGE_TTL : Math.max(0, Math.floor(((hour + 1) * HOUR_MS - Date.now()) / 1000));
  if (hit && (hour === null || hour === hourBucket())) {
    const restored = await restore(hit, env, request.headers.get('x-sf-client-freshness') !== '1' && !highlights && !isStructural(url.pathname), highlights ? remaining() : undefined);
    restored.headers.append('vary', 'X-SF-Client-Freshness');
    return restored;
  }
  // The sitemap embeds the origin, so its persisted copy is per origin (workers.dev, version previews, custom domains).
  const persistId = url.pathname === '/sitemap.xml' ? `${url.origin}${url.pathname}?` : `${url.pathname}?${search}`;
  const response = version && isStructural(url.pathname) ? await persisted(env, ctx, persistId, version, route) : await route();
  const ttl = remaining();
  if (highlights && response.headers.get('cache-control')?.startsWith('public,')) {
    response.headers.set('cache-control', highlightsClientCache(ttl));
  }
  response.headers.set('x-sf-cache', 'miss');
  const clientCache = response.headers.get('cache-control') ?? '';
  if (ttl > 0 && response.status === 200 && clientCache.startsWith('public,')) {
    const stored = new Response(response.clone().body, response);
    stored.headers.set('cache-control', `public, max-age=${ttl}`);
    stored.headers.set('x-sf-cc', clientCache);
    ctx.waitUntil(cache.put(key, stored));
  }
  // Browser variants only. Internal Cache API entries stay header-independent.
  response.headers.append('vary', 'X-SF-Client-Freshness');
  return response;
}

/** Cached JSON embeds a status object computed at generation time: bring its time-dependent fields up to date. */
const highlightsClientCache = (ttl: number) => ttl > 0 ? `public, max-age=${Math.min(300, ttl)}` : 'no-store';

async function restore(hit: Response, env: CacheEnv, refreshJsonStatus: boolean, highlightsTtl?: number): Promise<Response> {
  const headers = new Headers(hit.headers);
  headers.set('cache-control', headers.get('x-sf-cc') ?? 'public, max-age=60'); headers.delete('x-sf-cc'); headers.set('x-sf-cache', 'hit');
  if (highlightsTtl !== undefined) {
    headers.set('cache-control', highlightsClientCache(highlightsTtl));
  }
  if (!refreshJsonStatus || headers.get('x-sf-has-status') !== '1' || !/json/.test(headers.get('content-type') ?? '')) return new Response(hit.body, { status: hit.status, headers });
  const bytes = new Uint8Array(await hit.arrayBuffer());
  const body = refreshStatus(bytes, staleAfter(env));
  if (body !== bytes) headers.delete('content-length');
  return new Response(body, { status: hit.status, headers });
}

const STATUS_MEMBER = new TextEncoder().encode('"status":{');
const TAIL_WINDOW = 2048;
/**
 * Every cached JSON body that carries a status has it as the LAST member of the top-level object, and the status
 * object is flat: `{...,"status":{...}}`. Only that tail is decoded, refreshed (withFreshness) and spliced back onto
 * the untouched leading bytes, instead of parsing and re-serialising the whole body. Anything that does not have
 * exactly that shape is returned as is (same as before for bodies without a status).
 */
export function refreshStatus(bytes: Uint8Array<ArrayBuffer>, staleAfter: number): Uint8Array<ArrayBuffer> {
  const last = bytes.length - STATUS_MEMBER.length, stop = Math.max(0, bytes.length - TAIL_WINDOW);
  let at = -1;
  search: for (let i = last; i >= stop; i--) {
    for (let j = 0; j < STATUS_MEMBER.length; j++) if (bytes[i + j] !== STATUS_MEMBER[j]) continue search;
    at = i; break;
  }
  if (at < 0) return bytes;
  const start = at + STATUS_MEMBER.length - 1; // the status object's `{`
  const tail = new TextDecoder().decode(bytes.subarray(start));
  // `{flat status}` + the top-level `}`; a nested `{` or anything after it means this is not the top-level status.
  if (!tail.endsWith('}}') || tail.indexOf('{', 1) >= 0) return bytes;
  let status: unknown;
  try { status = JSON.parse(tail.slice(0, -1)); } catch { return bytes; }
  if (!status || typeof status !== 'object' || !Object.hasOwn(status, 'offers_checked_at')) return bytes;
  const fresh = new TextEncoder().encode(`${JSON.stringify(withFreshness(status as Status, staleAfter))}}`);
  const out = new Uint8Array(start + fresh.length);
  out.set(bytes.subarray(0, start)); out.set(fresh, start);
  return out;
}
