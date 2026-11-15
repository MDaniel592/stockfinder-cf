-- Nullable public fields permit tombstones arriving before the original row.
CREATE TABLE catalog_categories (
 id TEXT PRIMARY KEY, slug TEXT, name TEXT, version INTEGER NOT NULL,
 changed_at TEXT NOT NULL, deleted_at TEXT
);
CREATE UNIQUE INDEX categories_slug ON catalog_categories(slug);
CREATE TABLE catalog_shops (
 id TEXT PRIMARY KEY, name TEXT, url_public TEXT, version INTEGER NOT NULL,
 changed_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE catalog_products (
 id TEXT PRIMARY KEY, slug TEXT, name TEXT, category_id TEXT, brand TEXT, specs TEXT,
 version INTEGER NOT NULL, changed_at TEXT NOT NULL, deleted_at TEXT
);
CREATE UNIQUE INDEX products_slug ON catalog_products(slug);
CREATE INDEX products_category ON catalog_products(category_id, deleted_at, name, id);
CREATE INDEX products_brand ON catalog_products(brand, deleted_at, id);
CREATE TABLE catalog_offers (
 id TEXT PRIMARY KEY, product_id TEXT, shop_id TEXT, price_cents INTEGER,
 in_stock INTEGER, last_checked_at TEXT, url_public TEXT,
 version INTEGER NOT NULL, changed_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX offers_product_price ON catalog_offers(product_id, deleted_at, in_stock, price_cents);
CREATE TABLE sync_status (
 id INTEGER PRIMARY KEY CHECK(id=1), last_event_id INTEGER NOT NULL DEFAULT 0,
 last_sync_at TEXT, source_changed_at TEXT
);
INSERT INTO sync_status(id) VALUES(1);
CREATE TABLE sync_nonces (nonce TEXT PRIMARY KEY, expires_at INTEGER NOT NULL);
CREATE INDEX nonces_expiry ON sync_nonces(expires_at);
