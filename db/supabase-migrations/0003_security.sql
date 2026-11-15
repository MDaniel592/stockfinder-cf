-- Acceso mínimo. El Worker entra como `stockfinder_worker`: lee el catálogo y ejecuta las funciones de 0002; nada más.
-- RLS activado en todas las tablas y SIN políticas: anon/authenticated/service_role (Data API) no ven ninguna fila
-- aunque alguien expusiera el esquema. El rol del Worker lleva BYPASSRLS porque lee directamente (privilegios = solo
-- SELECT); las funciones SECURITY DEFINER son del propietario de las tablas.
-- La contraseña del rol NO está aquí: la fija scripts/supabase_setup.py (Management API) y vive en .env.supabase.local
-- y en la configuración de Hyperdrive. En la base de pruebas desechable la fija tests/helpers/worker-db.ts.
SET search_path = stockfinder;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stockfinder_worker') THEN
    CREATE ROLE stockfinder_worker NOLOGIN NOINHERIT BYPASSRLS;
  ELSE
    ALTER ROLE stockfinder_worker NOINHERIT BYPASSRLS;
  END IF;
END $$;
-- Una sola conexión/rol para lecturas y sync: sync_apply de 20 eventos tarda decenas de ms, muy por debajo de 2 s.
ALTER ROLE stockfinder_worker SET search_path = stockfinder;
ALTER ROLE stockfinder_worker SET statement_timeout = '2s';
ALTER ROLE stockfinder_worker SET idle_in_transaction_session_timeout = '5s';

REVOKE ALL ON SCHEMA stockfinder FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA stockfinder FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA stockfinder FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA stockfinder FROM PUBLIC;
DO $$
DECLARE r text;
BEGIN
  -- Roles de la Data API de Supabase (solo existen en Supabase).
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON SCHEMA stockfinder FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA stockfinder FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA stockfinder FROM %I', r);
      EXECUTE format('REVOKE ALL ON ALL FUNCTIONS IN SCHEMA stockfinder FROM %I', r);
    END IF;
  END LOOP;
END $$;

GRANT USAGE ON SCHEMA stockfinder TO stockfinder_worker;
GRANT SELECT ON catalog_categories, catalog_shops, catalog_products, catalog_offers, offer_price_history, sync_status TO stockfinder_worker;
GRANT EXECUTE ON FUNCTION iso(timestamptz), search_text(text, text, text, json), sync_apply(text, json),
  report_create(text, text, text, text, text, integer), reports_pull(text) TO stockfinder_worker;

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'stockfinder' LOOP
    EXECUTE format('ALTER TABLE stockfinder.%I ENABLE ROW LEVEL SECURITY', t);
  END LOOP;
END $$;
