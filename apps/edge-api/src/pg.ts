// Postgres (Supabase via Hyperdrive) variants of the public read model.
// Hyperdrive Free counts every statement (100,000/day, cached or not), so each request issues ONE statement:
// rows + total, product + offers, product + history, and the three facet lists are merged with window functions,
// json_agg or UNION ALL; writes go through SECURITY DEFINER functions (one call per sync batch / report / pull).
// Timestamps inside json_agg are formatted by stockfinder.iso() so the public JSON matches D1 byte for byte.
import type { Db } from './db';
import { OFFER_ACTIVE } from './aggregates';

export const ACTIVE_SHOP = `EXISTS(SELECT 1 FROM catalog_shops s WHERE s.id=o.shop_id AND s.deleted_at IS NULL)`;

/** ASCII-only lower case: same folding as SQLite LOWER() and Postgres lower() under COLLATE "C". */
export const asciiLower = (value: string) => value.replace(/[A-Z]/g, c => c.toLowerCase());
/** `%term%` for LIKE over search_text() (lower-cased, `\`, `%` and `_` escaped). Served by the pg_trgm GIN index. */
export const likeTerm = (term: string) => `%${asciiLower(term).replace(/[\\%_]/g, m => `\\${m}`)}%`;
export const SEARCH_TEXT = `search_text(p.name,p.brand,p.mpn,p.retailer_codes)`;

/** One sync batch = one statement. The function applies exactly the D1 statement sequence (see 0002_functions.sql). */
export async function applySync(db: Db, nonce: string, events: unknown[]) {
  const row = await db.first<{ written: number }>('SELECT sync_apply(?, ?::json) AS written', nonce, JSON.stringify(events));
  return Number(row?.written ?? 0);
}

export const pullReports = (db: Db, nonce: string) =>
  db.all<Record<string, unknown>>('SELECT id,product_slug,offer_id,note,page_url,created_at FROM reports_pull(?)', nonce);

export async function createReport(db: Db, r: { slug: string; offer: string | null; note: string; pageUrl: string | null; ipHash: string; perHour: number }) {
  return (await db.first<{ outcome: 'ok' | 'product' | 'offer' | 'rate' | 'full'; report_id: number | null }>(
    'SELECT outcome,report_id FROM report_create(?,?,?,?,?,?::int)', r.slug, r.offer, r.note, r.pageUrl, r.ipHash, r.perHour))!;
}

/** Shared builds: rate limit, slug checks and insert in one call to build_share_create(). */
export async function createSharedBuild(db: Db, b: { id: string; name: string; parts: string; ipHash: string; perHour: number }) {
  const row = await db.first<{ outcome: 'ok' | 'product' | 'rate' | 'full' }>(
    'SELECT build_share_create(?::uuid,?,?::json,?,?::int) AS outcome', b.id, b.name, b.parts, b.ipHash, b.perHour);
  return row!.outcome;
}
export const getSharedBuild = (db: Db, id: string) =>
  db.first<{ id: string; name: string; parts: string; created_at: string }>('SELECT id,name,parts,created_at FROM build_share_get(?::uuid)', id);

/** Brands, spec facets and shops of a filtered product set in one UNION ALL. */
export async function facetRows(db: Db, where: string, args: unknown[], keys: readonly string[]) {
  const parts = [`SELECT 'b' AS kind,p.brand AS a,NULL::text AS b,COUNT(*) AS count FROM catalog_products p WHERE ${where} GROUP BY p.brand`];
  const params: unknown[] = [...args];
  if (keys.length) {
    parts.push(`SELECT 'f',j.key COLLATE "C",j.value COLLATE "C",COUNT(*) FROM catalog_products p CROSS JOIN LATERAL json_each_text(p.specs) j
      WHERE ${where} AND j.key IN (${keys.map(() => '?').join(',')}) AND j.value<>'' GROUP BY j.key,j.value`);
    params.push(...args, ...keys);
  }
  parts.push(`SELECT 's',s.id,s.name,COUNT(DISTINCT p.id) FROM catalog_products p JOIN catalog_offers o ON o.product_id=p.id AND o.deleted_at IS NULL
    JOIN catalog_shops s ON s.id=o.shop_id WHERE s.deleted_at IS NULL AND ${where} GROUP BY s.id,s.name`);
  params.push(...args);
  const rows = await db.all<{ kind: string; a: string; b: string | null; count: number }>(
    `SELECT * FROM (${parts.join(' UNION ALL ')}) u ORDER BY kind,CASE WHEN kind='b' THEN a ELSE b END COLLATE "C",a COLLATE "C" LIMIT 5000`, ...params);
  return {
    brands: rows.filter(r => r.kind === 'b').map(r => ({ value: r.a, count: r.count })),
    facets: rows.filter(r => r.kind === 'f').map(r => ({ key: r.a, value: r.b as string, count: r.count })),
    shops: rows.filter(r => r.kind === 's').map(r => ({ id: r.a, name: r.b as string, count: r.count }))
  };
}

/** Product (visible) + its offers, or null. */
export async function productOffers(db: Db, slug: string, visible: string) {
  const row = await db.first<{ offers: string }>(`SELECT (SELECT COALESCE(json_agg(x ORDER BY x.in_stock DESC,x.price_cents,x.id),'[]') FROM (
      SELECT o.id,s.name AS shop_name,o.price_cents,o.in_stock,iso(o.last_checked_at) AS last_checked_at,o.url_public
      FROM catalog_offers o JOIN catalog_shops s ON s.id=o.shop_id AND s.deleted_at IS NULL
      WHERE o.product_id=p.id AND o.deleted_at IS NULL ORDER BY o.in_stock DESC,o.price_cents,o.id LIMIT 200) x) AS offers
    FROM catalog_products p WHERE p.slug=? AND ${visible}`, slug);
  return row ? JSON.parse(row.offers) as unknown[] : null;
}

/** Product (visible) + newest 500 history rows of the window, oldest first; or null. */
export async function productHistory(db: Db, slug: string, since: string | null, visible: string) {
  const row = await db.first<{ history: string }>(`SELECT (SELECT COALESCE(json_agg(json_build_object('offer_id',w.offer_id,'shop_name',w.shop_name,
      'price_cents',w.price_cents,'in_stock',w.in_stock,'observed_at',iso(w.observed_at)) ORDER BY w.observed_at,w.id),'[]') FROM (
      SELECT h.id,h.offer_id,s.name AS shop_name,h.price_cents,h.in_stock,h.observed_at FROM offer_price_history h
      JOIN catalog_shops s ON s.id=h.shop_id AND s.deleted_at IS NULL
      WHERE h.product_id=p.id AND h.observed_at>=?::timestamptz ORDER BY h.observed_at DESC,h.id DESC LIMIT 500) w) AS history
    FROM catalog_products p WHERE p.slug=? AND ${visible}`, since ?? '-infinity', slug);
  return row ? JSON.parse(row.history) as unknown[] : null;
}

/** Landing data (category summary + recent drops) in one statement; same rules as the D1 queries in highlights.ts. */
export async function highlightRows(db: Db, since: string) {
  const row = await db.first<{ categories: string; drops: string }>(`SELECT
    (SELECT COALESCE(json_agg(json_build_object('id',c.id,'name',c.name,'products',c.products,'in_stock',c.in_stock,'min_price_cents',c.min_price_cents) ORDER BY c.name),'[]')
     FROM (SELECT c.id,c.name,COUNT(p.id) AS products,COUNT(p.id) FILTER (WHERE p.in_stock_count>0) AS in_stock,MIN(p.min_price_in_stock_cents) AS min_price_cents
       FROM catalog_categories c LEFT JOIN catalog_products p ON p.category_id=c.id AND p.deleted_at IS NULL
       WHERE c.deleted_at IS NULL GROUP BY c.id,c.name) c) AS categories,
    (SELECT COALESCE(json_agg(d ORDER BY d.ord),'[]') FROM (
      WITH h AS (SELECT h.id,h.offer_id,h.product_id,h.price_cents,h.in_stock,h.observed_at,
          LAG(h.price_cents) OVER w AS prev_price, LAG(h.in_stock) OVER w AS prev_stock,
          ROW_NUMBER() OVER (PARTITION BY h.offer_id ORDER BY h.observed_at DESC,h.id DESC) AS rn
        FROM offer_price_history h
        WHERE h.offer_id IN (SELECT DISTINCT offer_id FROM offer_price_history WHERE observed_at>=?::timestamptz)
        WINDOW w AS (PARTITION BY h.offer_id ORDER BY h.observed_at,h.id))
      SELECT p.id,p.slug,p.name,p.category_id,p.brand,p.specs,p.mpn,p.mpn_source,p.image_path,p.image_scope,p.retailer_codes,p.offers_count,
        (SELECT iso(MAX(x.last_checked_at)) FROM catalog_offers x WHERE x.product_id=p.id AND x.deleted_at IS NULL) AS last_checked_at,
        h.price_cents,h.prev_price,iso(h.observed_at) AS observed_at,s.name AS shop_name,
        ROW_NUMBER() OVER (ORDER BY (h.prev_price-h.price_cents)*1.0/h.prev_price DESC,h.observed_at DESC) AS ord
      FROM h JOIN catalog_offers o ON o.id=h.offer_id AND o.deleted_at IS NULL AND o.in_stock=1 AND o.price_cents=h.price_cents
        JOIN catalog_shops s ON s.id=o.shop_id AND s.deleted_at IS NULL
        JOIN catalog_products p ON p.id=h.product_id AND p.deleted_at IS NULL AND p.min_price_in_stock_cents=h.price_cents
        JOIN catalog_categories c ON c.id=p.category_id AND c.deleted_at IS NULL
      WHERE h.rn=1 AND h.observed_at>=?::timestamptz AND h.in_stock=1 AND h.prev_stock=1
        AND h.price_cents*100<=h.prev_price*99 AND h.price_cents*10>=h.prev_price*3
      ORDER BY (h.prev_price-h.price_cents)*1.0/h.prev_price DESC,h.observed_at DESC LIMIT 40) d) AS drops`, since, since);
  return { categories: JSON.parse(row!.categories) as Record<string, unknown>[], drops: JSON.parse(row!.drops) as Record<string, unknown>[] };
}

/** Product page metadata + in-stock offer range in one statement. */
export const productMeta = (db: Db, slug: string, activeCategory: string) => db.first<Record<string, string | number | null>>(
  `SELECT p.id,p.name,p.brand,p.mpn,p.specs,p.image_path,x.low,x.high,x.n FROM catalog_products p CROSS JOIN LATERAL (
     SELECT MIN(o.price_cents) AS low,MAX(o.price_cents) AS high,COUNT(*) AS n FROM catalog_offers o
     WHERE o.product_id=p.id AND o.deleted_at IS NULL AND o.in_stock=1 AND ${ACTIVE_SHOP}) x
   WHERE p.slug=? AND p.deleted_at IS NULL AND ${activeCategory}`, slug);

/** Sitemap entries (categories, then products) in one statement, capped at the sitemap protocol's 50,000 URLs. */
export const sitemapRows = (db: Db, activeCategory: string) => db.all<{ kind: string; slug: string; changed_at: string | null }>(
  `SELECT * FROM (SELECT 'c' AS kind,slug,changed_at FROM catalog_categories WHERE deleted_at IS NULL AND slug IS NOT NULL
     UNION ALL SELECT 'p',p.slug,p.changed_at FROM catalog_products p WHERE p.deleted_at IS NULL AND p.slug IS NOT NULL AND ${activeCategory}) u
   ORDER BY kind,slug LIMIT 50000`);

/** Version + public status + live category ids in one statement (memoised per isolate in cache.ts). */
export const stateRow = (db: Db, freshHours: number) => db.first<Record<string, unknown>>(
  `SELECT st.last_event_id,st.last_sync_at,st.source_changed_at,st.offers_total,st.offers_checked_max,st.facets_version,st.data_version,
     (SELECT COUNT(*) FROM catalog_offers o WHERE o.last_checked_at>=now()-make_interval(hours=>?::int) AND ${OFFER_ACTIVE}) AS offers_fresh,
     (SELECT COALESCE(json_agg(c.id),'[]') FROM catalog_categories c WHERE c.deleted_at IS NULL) AS categories
   FROM sync_status st WHERE st.id=1`, freshHours);
