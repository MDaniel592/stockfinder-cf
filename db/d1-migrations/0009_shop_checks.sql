-- Per-shop freshness mark (replaces one last_checked_at write per unchanged offer). Additive; apply BEFORE deploying the matching Worker.
-- checked_at = end of the shop's last confirmed pass. unconfirmed = JSON array of offer ids that pass could not confirm
-- (ids match ^[a-zA-Z0-9_-]+$, so they are searched as "<id>" with instr()); those offers keep their own last_checked_at.
-- Effective offer freshness = MAX(offer.last_checked_at, shop_checks.checked_at) unless the offer is unconfirmed (see OFFER_CHECKED in aggregates.ts).
CREATE TABLE shop_checks (
 id TEXT PRIMARY KEY, version INTEGER NOT NULL, changed_at TEXT NOT NULL, deleted_at TEXT,
 checked_at TEXT NOT NULL, unconfirmed TEXT NOT NULL DEFAULT '[]'
);
