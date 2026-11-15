-- shop_check: marca de pasada por tienda (nas/catalog.py publish_shop_check). Idempotente: se puede ejecutar en cada arranque.
DO $$
DECLARE c RECORD;
BEGIN
 IF EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='catalog_entities'::regclass AND contype='c'
            AND pg_get_constraintdef(oid) LIKE '%shop_check%') THEN RETURN; END IF;
 FOR c IN SELECT conname FROM pg_constraint WHERE conrelid='catalog_entities'::regclass AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%''category''%' LOOP
  EXECUTE format('ALTER TABLE catalog_entities DROP CONSTRAINT %I', c.conname);
 END LOOP;
 ALTER TABLE catalog_entities ADD CONSTRAINT catalog_entities_entity_check
  CHECK(entity IN ('category','shop','product','offer','shop_check'));
END $$;
