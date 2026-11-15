ALTER TABLE catalog_products ADD COLUMN manufacturer_url TEXT;
ALTER TABLE catalog_products ADD COLUMN specs_verified_at TEXT;
ALTER TABLE catalog_products ADD COLUMN mpn TEXT;
CREATE UNIQUE INDEX products_verified_mpn ON catalog_products(brand,mpn) WHERE mpn IS NOT NULL;
