-- Commercial cache versions by category. Structural changes still use facets_version.
CREATE TABLE category_cache_epochs (
  category_id TEXT PRIMARY KEY,
  version INTEGER NOT NULL DEFAULT 0
);
