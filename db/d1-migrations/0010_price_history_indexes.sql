-- Read/write optimisations for D1 (additive; apply AFTER 0009 and BEFORE deploying the matching Worker).
-- 1) Landing "price drops" query (highlights.ts): the latest/previous observation of each recently changed offer is found by
--    seeking this index (offer_id, observed_at, id) instead of window functions over the offers' whole history.
--    Cost: +1 index entry written per price-history row (one row per price/stock CHANGE, never for freshness-only refreshes).
--    (An extra (observed_at, offer_id) index was tried and left out: the planner serves the 14-day window with the existing
--    offer_price_history_product index, so it would only add a write per row.)
CREATE INDEX offer_price_history_offer ON offer_price_history(offer_id, observed_at, id);
-- 2) api_cache sweep guard: finalizeStatements() only scans api_cache when "<facets_version>.<hour>" differs from this token.
ALTER TABLE sync_status ADD COLUMN cache_swept TEXT;
