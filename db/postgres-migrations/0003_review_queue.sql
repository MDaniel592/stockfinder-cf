CREATE TABLE IF NOT EXISTS retailer_review_queue (
 review_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 shop_id TEXT NOT NULL, retailer_sku TEXT NOT NULL, fingerprint TEXT NOT NULL,
 candidate JSONB NOT NULL, proposed_product_id TEXT,
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
 review_note TEXT, first_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 last_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), reviewed_at TIMESTAMPTZ,
 UNIQUE(shop_id,retailer_sku,fingerprint)
);
CREATE INDEX IF NOT EXISTS review_pending ON retailer_review_queue(status,review_id);
