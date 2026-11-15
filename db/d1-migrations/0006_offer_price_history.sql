-- Price/stock observations per offer (one row per change, plus the first observation).
CREATE TABLE offer_price_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  offer_id TEXT NOT NULL,
  product_id TEXT NOT NULL,
  shop_id TEXT NOT NULL,
  price_cents INTEGER NOT NULL,
  in_stock INTEGER NOT NULL,
  observed_at TEXT NOT NULL
);
CREATE INDEX offer_price_history_product ON offer_price_history(product_id, observed_at);
-- status.stale: latest offer check (MAX over non-deleted offers) is answered from this index.
CREATE INDEX offers_last_checked ON catalog_offers(last_checked_at);
-- Optional public retailer codes (JSON array of {shop_id, code}); NULL when never published.
ALTER TABLE catalog_products ADD COLUMN retailer_codes TEXT;
