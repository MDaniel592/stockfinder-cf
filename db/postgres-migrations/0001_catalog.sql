CREATE TABLE IF NOT EXISTS catalog_entities (
 entity TEXT NOT NULL CHECK(entity IN ('category','shop','product','offer','shop_check')),
 entity_id TEXT NOT NULL, data JSONB NOT NULL DEFAULT '{}',
 version BIGINT NOT NULL DEFAULT 1, deleted_at TIMESTAMPTZ,
 changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(entity, entity_id)
);
CREATE TABLE IF NOT EXISTS catalog_outbox (
 event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 entity TEXT NOT NULL, entity_id TEXT NOT NULL, version BIGINT NOT NULL,
 operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
 changed_at TIMESTAMPTZ NOT NULL, data JSONB,
 acknowledged_at TIMESTAMPTZ, attempts INTEGER NOT NULL DEFAULT 0,
 UNIQUE(entity,entity_id,version)
);
CREATE INDEX IF NOT EXISTS outbox_pending ON catalog_outbox(event_id) WHERE acknowledged_at IS NULL;
CREATE OR REPLACE FUNCTION emit_catalog_change() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND NEW.version=OLD.version THEN RETURN NEW; END IF;
 INSERT INTO catalog_outbox(entity,entity_id,version,operation,changed_at,data)
 VALUES(NEW.entity,NEW.entity_id,NEW.version,CASE WHEN NEW.deleted_at IS NULL THEN 'upsert' ELSE 'delete' END,
 NEW.changed_at,CASE WHEN NEW.deleted_at IS NULL THEN NEW.data ELSE NULL END);
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS catalog_change ON catalog_entities;
CREATE TRIGGER catalog_change AFTER INSERT OR UPDATE ON catalog_entities FOR EACH ROW EXECUTE FUNCTION emit_catalog_change();
-- Rows are never physically removed: monotonically versioned tombstones survive retries.
