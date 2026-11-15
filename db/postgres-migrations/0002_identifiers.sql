CREATE TABLE IF NOT EXISTS product_identifiers (
 kind TEXT NOT NULL CHECK(kind IN ('mpn','ean','model')),
 scope TEXT NOT NULL,
 value TEXT NOT NULL,
 product_id TEXT NOT NULL,
 PRIMARY KEY(kind,scope,value)
);
CREATE INDEX IF NOT EXISTS identifiers_product ON product_identifiers(product_id);
CREATE TABLE IF NOT EXISTS retailer_listings (
 shop_id TEXT NOT NULL,
 retailer_sku TEXT NOT NULL,
 product_id TEXT NOT NULL,
 match_kind TEXT NOT NULL CHECK(match_kind IN ('mpn','ean')),
 match_value TEXT NOT NULL,
 match_evidence TEXT NOT NULL,
 checked_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(shop_id,retailer_sku)
);
