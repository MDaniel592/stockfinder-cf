-- Rows-read reduction (Workers free plan: 5M D1 rows read/day). Additive; apply BEFORE deploying the matching Worker.
-- 1) Per-product offer aggregates, maintained by sync(); public listing/sort/filters read only catalog_products.
ALTER TABLE catalog_products ADD COLUMN min_price_cents INTEGER;            -- cheapest offer, any stock
ALTER TABLE catalog_products ADD COLUMN min_price_in_stock_cents INTEGER;   -- cheapest in-stock offer (public min_price_cents)
ALTER TABLE catalog_products ADD COLUMN max_price_in_stock_cents INTEGER;
ALTER TABLE catalog_products ADD COLUMN offers_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE catalog_products ADD COLUMN in_stock_count INTEGER NOT NULL DEFAULT 0;
-- 2) Cache bookkeeping: facets_version moves only on structural changes (not on price/stock refreshes).
ALTER TABLE sync_status ADD COLUMN facets_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_status ADD COLUMN offers_total INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_status ADD COLUMN totals_version INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sync_status ADD COLUMN data_version INTEGER NOT NULL DEFAULT 0;   -- public cache key: moves on every change except freshness-only offer refreshes
ALTER TABLE sync_status ADD COLUMN offers_checked_max TEXT;                    -- newest last_checked_at, replaces the offers_last_checked index
-- last_checked_at changes on every refresh: an index on it would cost 2 extra rows written per offer event.
DROP INDEX IF EXISTS offers_last_checked;
-- Persisted, version-stamped responses/counts shared by all colos (1 row read per hit).
CREATE TABLE api_cache (key TEXT PRIMARY KEY, version INTEGER NOT NULL, content_type TEXT, cache_control TEXT, body TEXT NOT NULL);
-- 3) Backfill (idempotent; re-run it after deploy if an old Worker synced in between).
UPDATE catalog_products AS p SET
  (min_price_cents,min_price_in_stock_cents,max_price_in_stock_cents,offers_count,in_stock_count)=
  (SELECT MIN(o.price_cents),MIN(CASE WHEN o.in_stock=1 THEN o.price_cents END),MAX(CASE WHEN o.in_stock=1 THEN o.price_cents END),
          COUNT(*),COALESCE(SUM(o.in_stock=1),0)
   FROM catalog_offers o WHERE o.product_id=p.id AND o.deleted_at IS NULL AND EXISTS(SELECT 1 FROM catalog_shops s WHERE s.id=o.shop_id AND s.deleted_at IS NULL));
UPDATE sync_status SET offers_checked_max=(SELECT MAX(o.last_checked_at) FROM catalog_offers o WHERE o.deleted_at IS NULL AND EXISTS(SELECT 1 FROM catalog_shops s WHERE s.id=o.shop_id AND s.deleted_at IS NULL)), offers_total=(SELECT COUNT(*) FROM catalog_offers o WHERE o.deleted_at IS NULL AND EXISTS(SELECT 1 FROM catalog_shops s WHERE s.id=o.shop_id AND s.deleted_at IS NULL));
-- 4) Indexes: category/global browse in every sort order terminates after LIMIT rows (expressions must match the ORDER BY text).
CREATE INDEX products_cat_featured ON catalog_products(category_id, deleted_at, (min_price_in_stock_cents IS NULL), id);
CREATE INDEX products_cat_price ON catalog_products(category_id, deleted_at, (min_price_in_stock_cents IS NULL), min_price_in_stock_cents, id);
CREATE INDEX products_cat_price_desc ON catalog_products(category_id, deleted_at, (min_price_in_stock_cents IS NULL), min_price_in_stock_cents DESC, id);
CREATE INDEX products_all_name ON catalog_products(deleted_at, name, id);
CREATE INDEX products_all_featured ON catalog_products(deleted_at, (min_price_in_stock_cents IS NULL), id);
CREATE INDEX products_all_price ON catalog_products(deleted_at, (min_price_in_stock_cents IS NULL), min_price_in_stock_cents, id);
CREATE INDEX products_all_price_desc ON catalog_products(deleted_at, (min_price_in_stock_cents IS NULL), min_price_in_stock_cents DESC, id);
CREATE INDEX offers_shop_product ON catalog_offers(shop_id, deleted_at, product_id);
CREATE INDEX products_cat_brand ON catalog_products(category_id, deleted_at, brand);  -- covering index for the per-category brand facet
CREATE INDEX products_cat_minprice ON catalog_products(category_id, deleted_at, min_price_in_stock_cents);  -- stock/price-range filters and their counts
