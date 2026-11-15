// GET /api/v1/catalog/highlights — data for the landing page in one cacheable request:
// per-category counts and lowest in-stock price, and recent price drops (latest change of an in-stock offer that
// lowered its price and is now the product's best in-stock price). Read-only. Uses the per-product aggregates of
// migration 0007 (min_price_in_stock_cents, in_stock_count, offers_count) so the category
// summary reads one row per product; the drops query only touches history of offers that changed recently.

import { offerChecked, type ShopMark } from './aggregates';
import { stmt, type Db } from './db';
import { highlightRows } from './pg';

const DROP_WINDOW_HOURS = 24;
const MAX_DROPS = 8;

export interface HighlightCategory { id: string; name: string; products: number; in_stock: number; min_price_cents: number | null }
export interface PriceDrop {
  id: string; slug: string; name: string; category_id: string; brand: string; specs: Record<string, string>;
  mpn: string | null; mpn_source: string | null; image_path: string | null; image_scope: string | null;
  retailer_codes: Array<{ shop_id: string; code: string }>;
  min_price_cents: number; previous_price_cents: number; offers_count: number; last_checked_at: string | null;
  shop_name: string; dropped_at: string;
}

// D1 returns JSON columns as text; the Postgres json_agg path embeds them as values already.
const parse = <T,>(value: unknown, fallback: T): T => { if (value && typeof value === 'object') return value as T; try { return value ? JSON.parse(String(value)) as T : fallback; } catch { return fallback; } };

export async function highlights(db: Db, now = Date.now(), marks: ShopMark[] = []): Promise<{ categories: HighlightCategory[]; drops: PriceDrop[] }> {
  const since = new Date(now - DROP_WINDOW_HOURS * 3600_000).toISOString();
  const eff = offerChecked(marks, 'x');
  const [categories, drops] = db.pg ? Object.values(await highlightRows(db, since)) : await db.reads([
    stmt(`SELECT c.id,c.name,COUNT(p.id) AS products,COALESCE(SUM(p.in_stock_count>0),0) AS in_stock,MIN(p.min_price_in_stock_cents) AS min_price_cents
      FROM catalog_categories c LEFT JOIN catalog_products p ON p.category_id=c.id AND p.deleted_at IS NULL
      WHERE c.deleted_at IS NULL GROUP BY c.id,c.name ORDER BY c.name`),
    // Latest observation of each offer that changed recently (rows of the window that have no later row, found through
    // offer_price_history_product + offer_price_history_offer, migration 0010) and the one before it (LIMIT 1 OFFSET 1 on the same
    // ordering). Equivalent to ROW_NUMBER()/LAG() over the whole history, but it never reads the history of unchanged offers.
    // Drops of 1–70 % only (filters data errors).
    stmt(`WITH h AS (SELECT l.offer_id,l.product_id,l.price_cents,l.in_stock,l.observed_at,
        (SELECT q.price_cents FROM offer_price_history q WHERE q.offer_id=l.offer_id ORDER BY q.observed_at DESC,q.id DESC LIMIT 1 OFFSET 1) AS prev_price,
        (SELECT q.in_stock FROM offer_price_history q WHERE q.offer_id=l.offer_id ORDER BY q.observed_at DESC,q.id DESC LIMIT 1 OFFSET 1) AS prev_stock
        FROM offer_price_history l
        WHERE l.observed_at>=?
          AND NOT EXISTS(SELECT 1 FROM offer_price_history n WHERE n.offer_id=l.offer_id AND (n.observed_at,n.id)>(l.observed_at,l.id))
          AND l.in_stock=1)
      SELECT p.id,p.slug,p.name,p.category_id,p.brand,p.specs,p.mpn,p.mpn_source,p.image_path,p.image_scope,p.retailer_codes,
        p.offers_count,(SELECT MAX(${eff.sql}) FROM catalog_offers x WHERE x.product_id=p.id AND x.deleted_at IS NULL) AS last_checked_at,h.price_cents,h.prev_price,h.observed_at,s.name AS shop_name
      FROM h JOIN catalog_offers o ON o.id=h.offer_id AND o.deleted_at IS NULL AND o.in_stock=1 AND o.price_cents=h.price_cents
        JOIN catalog_shops s ON s.id=o.shop_id AND s.deleted_at IS NULL
        JOIN catalog_products p ON p.id=h.product_id AND p.deleted_at IS NULL AND p.min_price_in_stock_cents=h.price_cents
        JOIN catalog_categories c ON c.id=p.category_id AND c.deleted_at IS NULL
      WHERE h.prev_stock=1
        AND h.price_cents*100<=h.prev_price*99 AND h.price_cents*10>=h.prev_price*3
      ORDER BY (h.prev_price-h.price_cents)*1.0/h.prev_price DESC,h.observed_at DESC LIMIT 40`, since, ...eff.params)
  ]);
  const seen = new Set<string>();
  const top = drops.filter(row => { const id = String(row.id); if (seen.has(id)) return false; seen.add(id); return true; }).slice(0, MAX_DROPS);
  return {
    categories: categories.map(r => ({ id: String(r.id), name: String(r.name), products: Number(r.products ?? 0),
      in_stock: Number(r.in_stock ?? 0), min_price_cents: r.min_price_cents === null || r.min_price_cents === undefined ? null : Number(r.min_price_cents) })),
    drops: top.map(p => ({ id: String(p.id), slug: String(p.slug), name: String(p.name), category_id: String(p.category_id), brand: String(p.brand ?? ''),
      specs: parse<Record<string, string>>(p.specs, {}), mpn: (p.mpn as string | null) ?? null, mpn_source: (p.mpn_source as string | null) ?? null,
      image_path: (p.image_path as string | null) ?? null, image_scope: (p.image_scope as string | null) ?? null,
      retailer_codes: parse<Array<{ shop_id: string; code: string }>>(p.retailer_codes, []),
      min_price_cents: Number(p.price_cents), previous_price_cents: Number(p.prev_price), offers_count: Number(p.offers_count ?? 0),
      last_checked_at: (p.last_checked_at as string | null) ?? null, shop_name: String(p.shop_name), dropped_at: String(p.observed_at) }))
  };
}
