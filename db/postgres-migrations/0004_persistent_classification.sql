CREATE TABLE IF NOT EXISTS retailer_classifications (
 shop_id TEXT NOT NULL, retailer_sku TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected')),
 product_id TEXT, mpn TEXT, ean TEXT, review_note TEXT,
 reviewed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(shop_id,retailer_sku)
);
-- Preserve decisions already made before this classification registry existed.
INSERT INTO retailer_classifications(shop_id,retailer_sku,status,product_id,mpn,ean,review_note)
SELECT DISTINCT ON(shop_id,retailer_sku) shop_id,retailer_sku,status,proposed_product_id,
 lower(trim(candidate->>'mpn')),candidate->>'ean',review_note FROM retailer_review_queue
 WHERE status IN ('approved','rejected')
 ORDER BY shop_id,retailer_sku,CASE status WHEN 'approved' THEN 0 ELSE 1 END,review_id DESC
ON CONFLICT(shop_id,retailer_sku) DO NOTHING;
INSERT INTO retailer_classifications(shop_id,retailer_sku,status,product_id,mpn,review_note)
SELECT shop_id,retailer_sku,'approved',product_id,CASE WHEN match_kind='mpn' THEN match_value ELSE NULL END,match_evidence
FROM retailer_listings ON CONFLICT(shop_id,retailer_sku) DO NOTHING;
