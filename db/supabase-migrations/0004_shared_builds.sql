-- Configuraciones compartidas por enlace (equivale a db/d1-migrations/0008_shared_builds.sql).
-- El Worker no lee ni escribe la tabla: solo ejecuta build_share_create / build_share_get (SECURITY DEFINER),
-- así ip_hash nunca sale de la base. RLS activado y sin políticas, como el resto del esquema (0003_security.sql).
SET search_path = stockfinder;

CREATE TABLE shared_builds (
  id uuid PRIMARY KEY,
  name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
  parts json NOT NULL,
  created_at timestamptz NOT NULL,
  ip_hash text NOT NULL
);
CREATE INDEX shared_builds_ip ON shared_builds(ip_hash, created_at);

-- Límite por IP (hash), cada slug debe existir en su categoría, tope total (plan gratuito de 500 MB) y alta.
CREATE FUNCTION build_share_create(p_id uuid, p_name text, p_parts json, p_ip_hash text, p_per_hour integer)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = stockfinder, pg_temp AS $$
BEGIN
  IF (SELECT COUNT(*) FROM shared_builds b WHERE b.ip_hash = p_ip_hash AND b.created_at > now() - interval '1 hour') >= p_per_hour THEN
    RETURN 'rate';
  END IF;
  IF EXISTS (SELECT 1 FROM json_each_text(p_parts) j WHERE NOT EXISTS (SELECT 1 FROM catalog_products p
      WHERE p.slug = j.value AND p.category_id = j.key AND p.deleted_at IS NULL)) THEN
    RETURN 'product';
  END IF;
  IF (SELECT COUNT(*) FROM shared_builds) >= 200000 THEN RETURN 'full'; END IF;
  INSERT INTO shared_builds(id, name, parts, created_at, ip_hash) VALUES (p_id, p_name, p_parts, clock_timestamp(), p_ip_hash);
  RETURN 'ok';
END $$;

CREATE FUNCTION build_share_get(p_id uuid)
RETURNS TABLE(id text, name text, parts json, created_at timestamptz)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = stockfinder, pg_temp AS $$
  SELECT b.id::text, b.name, b.parts, b.created_at FROM shared_builds b WHERE b.id = p_id
$$;

REVOKE ALL ON shared_builds FROM PUBLIC;
REVOKE ALL ON FUNCTION build_share_create(uuid, text, json, text, integer), build_share_get(uuid) FROM PUBLIC;
DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['anon', 'authenticated', 'service_role'] LOOP
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('REVOKE ALL ON stockfinder.shared_builds FROM %I', r);
      EXECUTE format('REVOKE ALL ON FUNCTION stockfinder.build_share_create(uuid, text, json, text, integer), stockfinder.build_share_get(uuid) FROM %I', r);
    END IF;
  END LOOP;
END $$;
GRANT EXECUTE ON FUNCTION build_share_create(uuid, text, json, text, integer), build_share_get(uuid) TO stockfinder_worker;
ALTER TABLE shared_builds ENABLE ROW LEVEL SECURITY;
