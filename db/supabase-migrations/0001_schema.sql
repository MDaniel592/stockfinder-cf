-- Modelo de lectura público de StockFinder en Supabase Postgres: estado final equivalente a db/d1-migrations/0001..0007.
-- Esquema propio `stockfinder` (no expuesto por la Data API de Supabase, que solo publica public y graphql_public).
-- Convenciones (ver docs/supabase-migration.md):
--   * INTEGER de SQLite -> bigint (SQLite usa 64 bits; evita desbordes en aritmética como price_cents*100).
--   * in_stock -> smallint 0/1 (la API pública devuelve 0/1 como hoy).
--   * Fechas -> timestamptz (el Worker las devuelve en ISO-8601 con milisegundos y Z, igual que D1),
--     salvo specs_verified_at, que es un texto de procedencia que se publica tal cual llega.
--   * specs / retailer_codes -> json (no jsonb): conserva el texto y el orden de claves que publica el NAS.
--   * Texto con COLLATE "C": mismo orden por bytes que SQLite (BINARY) en ORDER BY / comparaciones.
--   * Sin claves foráneas: las lápidas y las ofertas pueden llegar antes que su fila padre (igual que en D1).
--   * Sin tabla api_cache: en Postgres no hay caché persistida por URL (Hyperdrive cuenta cada sentencia y la tabla
--     se podría llenar con URLs aleatorias); quedan el memo en memoria y la Cache API.
CREATE SCHEMA IF NOT EXISTS stockfinder;
-- pg_trgm vive en el esquema `extensions` (convención de Supabase; en la base de pruebas se crea aquí).
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
SET search_path = stockfinder;

-- ISO-8601 UTC con milisegundos y Z: el formato que D1 guarda y la API publica (para json_agg).
CREATE FUNCTION iso(t timestamptz) RETURNS text LANGUAGE sql STABLE PARALLEL SAFE
  RETURN to_char(t AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"');

-- Texto de búsqueda: nombre, marca, MPN y códigos de tienda separados por saltos de línea (un término nunca contiene
-- uno, así que no hay coincidencias entre campos), en minúsculas ASCII (COLLATE "C": igual que LOWER() de SQLite).
CREATE FUNCTION search_text(name text, brand text, mpn text, codes json) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
  RETURN lower((coalesce(name, '') || E'\n' || coalesce(brand, '') || E'\n' || coalesce(mpn, '') || E'\n' ||
    coalesce((SELECT string_agg(c->>'code', E'\n') FROM json_array_elements(codes) c), '')) COLLATE "C");

CREATE TABLE catalog_categories (
  id text COLLATE "C" PRIMARY KEY,
  slug text COLLATE "C",
  name text COLLATE "C",
  version bigint NOT NULL,
  changed_at timestamptz NOT NULL,
  deleted_at timestamptz
);
CREATE UNIQUE INDEX categories_slug ON catalog_categories(slug);

CREATE TABLE catalog_shops (
  id text COLLATE "C" PRIMARY KEY,
  name text COLLATE "C",
  url_public text,
  version bigint NOT NULL,
  changed_at timestamptz NOT NULL,
  deleted_at timestamptz
);

CREATE TABLE catalog_products (
  id text COLLATE "C" PRIMARY KEY,
  slug text COLLATE "C",
  name text COLLATE "C",
  category_id text COLLATE "C",
  brand text COLLATE "C",
  specs json,
  version bigint NOT NULL,
  changed_at timestamptz NOT NULL,
  deleted_at timestamptz,
  manufacturer_url text,
  specs_verified_at text,
  mpn text COLLATE "C",
  mpn_source text,
  image_path text,
  image_source_url text,
  image_scope text,
  retailer_codes json,
  -- Agregados por producto (0007), mantenidos por /internal/sync
  min_price_cents bigint,
  min_price_in_stock_cents bigint,
  max_price_in_stock_cents bigint,
  offers_count bigint NOT NULL DEFAULT 0,
  in_stock_count bigint NOT NULL DEFAULT 0
) WITH (fillfactor = 90);
CREATE UNIQUE INDEX products_slug ON catalog_products(slug);
CREATE UNIQUE INDEX products_verified_mpn ON catalog_products(brand, mpn) WHERE mpn IS NOT NULL;
CREATE INDEX products_category ON catalog_products(category_id, deleted_at, name, id);
CREATE INDEX products_brand ON catalog_products(brand, deleted_at, id);
CREATE INDEX products_cat_featured ON catalog_products(category_id, deleted_at, (min_price_in_stock_cents IS NULL), id);
CREATE INDEX products_cat_price ON catalog_products(category_id, deleted_at, (min_price_in_stock_cents IS NULL), min_price_in_stock_cents, id);
CREATE INDEX products_cat_price_desc ON catalog_products(category_id, deleted_at, (min_price_in_stock_cents IS NULL), min_price_in_stock_cents DESC, id);
CREATE INDEX products_all_name ON catalog_products(deleted_at, name, id);
CREATE INDEX products_all_featured ON catalog_products(deleted_at, (min_price_in_stock_cents IS NULL), id);
CREATE INDEX products_all_price ON catalog_products(deleted_at, (min_price_in_stock_cents IS NULL), min_price_in_stock_cents, id);
CREATE INDEX products_all_price_desc ON catalog_products(deleted_at, (min_price_in_stock_cents IS NULL), min_price_in_stock_cents DESC, id);
CREATE INDEX products_cat_brand ON catalog_products(category_id, deleted_at, brand);
CREATE INDEX products_cat_minprice ON catalog_products(category_id, deleted_at, min_price_in_stock_cents);
-- Búsqueda por subcadena (LIKE '%término%') sin recorrer la tabla.
CREATE INDEX products_search ON catalog_products USING gin (search_text(name, brand, mpn, retailer_codes) extensions.gin_trgm_ops);

-- fillfactor 90: los refrescos de frescura (version, changed_at, last_checked_at; columnas sin índice) son updates HOT.
CREATE TABLE catalog_offers (
  id text COLLATE "C" PRIMARY KEY,
  product_id text COLLATE "C",
  shop_id text COLLATE "C",
  price_cents bigint,
  in_stock smallint CHECK (in_stock IN (0, 1)),
  last_checked_at timestamptz,
  url_public text,
  version bigint NOT NULL,
  changed_at timestamptz NOT NULL,
  deleted_at timestamptz
) WITH (fillfactor = 90);
CREATE INDEX offers_product_price ON catalog_offers(product_id, deleted_at, in_stock, price_cents);
CREATE INDEX offers_shop_product ON catalog_offers(shop_id, deleted_at, product_id);

CREATE TABLE offer_price_history (
  id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  offer_id text COLLATE "C" NOT NULL,
  product_id text COLLATE "C" NOT NULL,
  shop_id text COLLATE "C" NOT NULL,
  price_cents bigint NOT NULL,
  in_stock smallint NOT NULL CHECK (in_stock IN (0, 1)),
  observed_at timestamptz NOT NULL
);
CREATE INDEX offer_price_history_product ON offer_price_history(product_id, observed_at);
-- Solo Postgres: sirven a /catalog/highlights (ventana por oferta y ofertas con cambios recientes).
CREATE INDEX offer_price_history_offer ON offer_price_history(offer_id, observed_at, id);
CREATE INDEX offer_price_history_observed ON offer_price_history(observed_at);

CREATE TABLE sync_status (
  id integer PRIMARY KEY CHECK (id = 1),
  last_event_id bigint NOT NULL DEFAULT 0,
  last_sync_at timestamptz,
  source_changed_at timestamptz,
  facets_version bigint NOT NULL DEFAULT 0,
  offers_total bigint NOT NULL DEFAULT 0,
  totals_version bigint NOT NULL DEFAULT 0,
  data_version bigint NOT NULL DEFAULT 0,
  offers_checked_max timestamptz
);
INSERT INTO sync_status(id) VALUES (1);

CREATE TABLE sync_nonces (
  nonce text COLLATE "C" PRIMARY KEY,
  expires_at bigint NOT NULL
);
CREATE INDEX nonces_expiry ON sync_nonces(expires_at);

CREATE TABLE product_reports (
  id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  product_slug text NOT NULL,
  offer_id text,
  note text NOT NULL,
  page_url text,
  status text NOT NULL DEFAULT 'new',
  created_at timestamptz NOT NULL,
  ip_hash text NOT NULL
);
CREATE INDEX product_reports_status ON product_reports(status, id);
CREATE INDEX product_reports_ip ON product_reports(ip_hash, created_at);
