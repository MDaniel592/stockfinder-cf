// Denormalised per-product offer aggregates + cache-version bookkeeping, maintained inside the sync() batch.
// Listing/sort/price/stock filters read only catalog_products; nothing here runs on public GET requests.
// D1 only: on Postgres the same logic runs inside stockfinder.sync_apply() (db/supabase-migrations/0002_functions.sql).
import type { Entity } from '../../../packages/api-contracts/index';
import { stmt, type Stmt } from './db';

/** Offer visibility rule shared with the public API: not deleted and its shop not deleted. */
export const OFFER_ACTIVE = `o.deleted_at IS NULL AND EXISTS(SELECT 1 FROM catalog_shops s WHERE s.id=o.shop_id AND s.deleted_at IS NULL)`;

/** One shop's pass mark as stored in shop_checks. Held in memory per isolate (cache.shopMarks), a handful of rows. */
export interface ShopMark { id: string; checked_at: string; unconfirmed: string }
/**
 * Effective freshness of offer `o`: the shop's mark (end of its last pass) freshens every offer of the shop EXCEPT those listed in
 * the mark's `unconfirmed` JSON array (ids the pass could not confirm), which keep their own last_checked_at. A shop without a
 * mark keeps the offer's own date. Ids match ^[a-zA-Z0-9_-]+$, so the quoted instr() test is exact (no json_each per offer).
 * Returned as SQL plus bound params IN TEXT ORDER (put them where the SQL goes): the marks are bound as parameters instead of
 * joining shop_checks, because the join costs one row read per offer (measured on D1, 2k products / 3.9k offers: a 50-row
 * listing reads 305 rows without marks, 405 with the join, 305 with bound params). 4 params per shop (D1 allows 100 per statement).
 */
export function offerChecked(marks: ShopMark[], o = 'o'): { sql: string; params: unknown[] } {
  if (!marks.length) return { sql: `${o}.last_checked_at`, params: [] };
  const arm = `WHEN ? THEN (CASE WHEN ?<=${o}.last_checked_at OR instr(?,'"'||${o}.id||'"')>0 THEN ${o}.last_checked_at ELSE ? END)`;
  return { sql: `CASE ${o}.shop_id ${marks.map(() => arm).join(' ')} ELSE ${o}.last_checked_at END`,
    params: marks.flatMap(m => [m.id, m.checked_at, m.unconfirmed, m.checked_at]) };
}

/** Recompute aggregates for the products selected by `where` (a predicate over catalog_products AS p). */
const STATS = `(SELECT MIN(o.price_cents),MIN(CASE WHEN o.in_stock=1 THEN o.price_cents END),MAX(CASE WHEN o.in_stock=1 THEN o.price_cents END),
  COUNT(*),COALESCE(SUM(o.in_stock=1),0) FROM catalog_offers o WHERE o.product_id=p.id AND ${OFFER_ACTIVE})`;
const STAT_COLUMNS = `min_price_cents,min_price_in_stock_cents,max_price_in_stock_cents,offers_count,in_stock_count`;
/** One statement per selection, writing only rows whose values changed (the price columns are indexed). */
export const recomputeSqls = (where: string) => [
  `UPDATE catalog_products AS p SET (${STAT_COLUMNS})=${STATS} WHERE ${where} AND (${STAT_COLUMNS.replace(/(\w+)/g, 'p.$1')}) IS NOT ${STATS}`
];
/** True when the stored live offer already has exactly these values: the event is a freshness-only refresh (only last_checked_at moves). */
const SAME_OFFER = `EXISTS(SELECT 1 FROM catalog_offers c WHERE c.id=? AND c.deleted_at IS NULL AND c.product_id=? AND c.shop_id=? AND c.price_cents=? AND c.in_stock=? AND c.url_public=?)`;
const sameArgs = (id: string, d: Record<string, unknown>) => [id, d.product_id, d.shop_id, d.price_cents, Number(d.in_stock), d.url_public];

/**
 * Statements to run BEFORE the entity's own upsert/delete (they compare against the stored row).
 * - data_version (the public cache key) moves for every change except a freshness-only offer refresh.
 * - facets_version moves only when facets, counts, brand/category/shop lists or the sitemap can change: any
 *   product/category/shop event, or an offer that appears, disappears or moves (price/stock changes do not).
 */
export function structureBump(entity: Entity, id: string, operation: 'upsert' | 'delete', data: Record<string, unknown>): Stmt[] {
  const head = `UPDATE sync_status SET`;
  // A shop mark only moves freshness, like a freshness-only offer refresh: NO version bump, so it never evicts the edge/D1
  // caches (that would re-read D1 on every pass). Cached bodies may lag by at most EDGE_TTL; the status object (offers_checked_at,
  // stale) is refreshed live on every cache hit, and the 15 s status memo is reset by sync().
  if (entity === 'shop_check') return [];
  if (entity !== 'offer') return [stmt(`${head} facets_version=facets_version+1,data_version=data_version+1 WHERE id=1`)];
  if (operation === 'delete')
    return [stmt(`${head} facets_version=facets_version+1,data_version=data_version+1 WHERE id=1 AND EXISTS(SELECT 1 FROM catalog_offers c WHERE c.id=? AND c.deleted_at IS NULL)`, id)];
  return [
    // Freshness-only refreshes never evict commercial responses. This runs before the offer upsert.
    stmt(`INSERT INTO category_cache_epochs(category_id,version)
      SELECT DISTINCT category_id,1 FROM catalog_products WHERE id IN (?,(SELECT product_id FROM catalog_offers WHERE id=?))
        AND NOT ${SAME_OFFER}
      ON CONFLICT(category_id) DO UPDATE SET version=category_cache_epochs.version+1`,
      data.product_id, id, ...sameArgs(id, data)),
    stmt(`${head} facets_version=facets_version+1 WHERE id=1 AND NOT EXISTS(SELECT 1 FROM catalog_offers c WHERE c.id=? AND c.deleted_at IS NULL AND c.shop_id=? AND c.product_id=?)`,
      id, data.shop_id, data.product_id),
    stmt(`${head} data_version=data_version+1 WHERE id=1 AND NOT ${SAME_OFFER}`, ...sameArgs(id, data))
  ];
}

/**
 * Freshness-only refresh: touches only non-indexed columns, so D1 writes 1 row and no index entries (Postgres: a HOT
 * update). It also stores the event version, which makes the generic upsert that follows a no-op. Anything that is
 * not freshness-only falls through to it.
 */
export function freshnessUpdate(id: string, version: number, changedAt: string, data: Record<string, unknown>): Stmt {
  return stmt(`UPDATE catalog_offers SET version=?,changed_at=?,last_checked_at=? WHERE id=? AND version<? AND deleted_at IS NULL AND product_id=? AND shop_id=? AND price_cents=? AND in_stock=? AND url_public=?`,
    version, changedAt, data.last_checked_at, id, version, data.product_id, data.shop_id, data.price_cents, Number(data.in_stock), data.url_public);
}

interface SyncEvent { entity: string; entity_id: string; operation: string; data?: Record<string, unknown> }

/** Statements to run AFTER all upserts of the batch: refresh the aggregates of every product the batch may have touched. */
export function aggregateStatements(events: SyncEvent[]): Stmt[] {
  const products = new Set<string>(), offers: string[] = [], shops: string[] = [];
  let newestCheck = '', rescan = false;
  for (const event of events) {
    if (event.entity === 'product') products.add(event.entity_id);
    else if (event.entity === 'offer') { offers.push(event.entity_id); if (typeof event.data?.last_checked_at === 'string' && event.data.last_checked_at > newestCheck) newestCheck = event.data.last_checked_at; if (typeof event.data?.product_id === 'string') products.add(event.data.product_id); }
    else if (event.entity === 'shop') shops.push(event.entity_id);
    if (event.entity === 'shop' || (event.entity === 'offer' && event.operation === 'delete')) rescan = true;
  }
  const statements: Stmt[] = [];
  if (newestCheck) statements.push(stmt(`UPDATE sync_status SET offers_checked_max=? WHERE id=1 AND COALESCE(offers_checked_max,'')<?`, newestCheck, newestCheck));
  // The newest-check marker only grows on its own; deletions/shop changes can lower it, so recompute it (rare events).
  if (rescan) statements.push(stmt(`UPDATE sync_status SET offers_checked_max=(SELECT MAX(o.last_checked_at) FROM catalog_offers o WHERE ${OFFER_ACTIVE}) WHERE id=1`));
  const ids = `SELECT value FROM json_each(?)`;
  if (products.size) statements.push(...recomputeSqls(`p.id IN (${ids})`).map(sql => stmt(sql, JSON.stringify([...products]))));
  // Tombstones carry no payload: resolve the product from the stored offer row (product_id is kept on delete).
  if (offers.length) statements.push(...recomputeSqls(`p.id IN (SELECT product_id FROM catalog_offers WHERE id IN (${ids}))`).map(sql => stmt(sql, JSON.stringify(offers))));
  if (shops.length) statements.push(...recomputeSqls(`p.id IN (SELECT product_id FROM catalog_offers WHERE shop_id IN (${ids}))`).map(sql => stmt(sql, JSON.stringify(shops))));
  return statements;
}

/**
 * Without table statistics SQLite prefers the global sort index and filters the category row by row (hundreds of rows read).
 * Pin the index that serves category + ORDER BY so the scan stops after LIMIT rows. Empty when the ORDER BY is not index-shaped.
 */
export function listingIndex(hasCategory: boolean, sort: string, ranked: boolean) {
  if (ranked) return '';
  const name = ({ name: hasCategory ? 'products_category' : 'products_all_name', featured: hasCategory ? 'products_cat_featured' : 'products_all_featured',
    price: hasCategory ? 'products_cat_price' : 'products_all_price', '-price': hasCategory ? 'products_cat_price_desc' : 'products_all_price_desc' } as Record<string, string>)[sort];
  return name ? ` INDEXED BY ${name}` : '';
}

const SWEEP_TOKEN = `((SELECT facets_version FROM sync_status WHERE id=1)||'.'||(CAST(strftime('%s','now') AS INTEGER)/3600))`;
/**
 * offers_total counts active offers (OFFER_ACTIVE: offer and its shop not deleted), so only shop events and offer events can change
 * it; a batch made of products/categories never needs the recount (a full scan of catalog_offers + a shop lookup per offer, ~7.4k rows
 * read for 3.9k offers). Without `events` the recount is always emitted.
 */
const touchesActiveOffers = (events?: SyncEvent[]) => !events || events.some(e => e.entity === 'shop' || e.entity === 'offer');

/**
 * Statements to run LAST: public offer total and eviction of stale persisted cache rows.
 * The recount keeps the `totals_version<>facets_version` guard, so a batch that does not touch offers can bump facets_version
 * (a product-only batch) without recounting: totals_version then stays behind and the next batch that does contain an offer or shop
 * event recounts, converging to the exact total.
 */
export function finalizeStatements(events?: SyncEvent[]): Stmt[] {
  return [
    ...(touchesActiveOffers(events) ? [stmt(`UPDATE sync_status SET offers_total=(SELECT COUNT(*) FROM catalog_offers o WHERE ${OFFER_ACTIVE}), totals_version=facets_version
      WHERE id=1 AND totals_version<>facets_version`)] : []),
    // Sweep only when the structure version or the hour changed since the last sweep (token in sync_status.cache_swept, migration 0010).
    // The statement is driven from sync_status (CROSS JOIN fixes the loop order) so that a skipped sweep never starts the api_cache
    // loop: ~1 row read instead of every cached row on every sync batch.
    stmt(`DELETE FROM api_cache WHERE rowid IN (SELECT a.rowid FROM sync_status s CROSS JOIN api_cache a
      WHERE s.id=1 AND COALESCE(s.cache_swept,'')<>(s.facets_version||'.'||(CAST(strftime('%s','now') AS INTEGER)/3600))
        AND a.version<>CASE WHEN a.key LIKE 'hourly:%' THEN CAST(strftime('%s','now') AS INTEGER)/3600 ELSE s.facets_version END)`),
    stmt(`UPDATE sync_status SET cache_swept=${SWEEP_TOKEN} WHERE id=1 AND COALESCE(cache_swept,'')<>${SWEEP_TOKEN}`)
  ];
}
