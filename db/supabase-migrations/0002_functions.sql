-- Escrituras del Worker como funciones SECURITY DEFINER: una llamada (= una consulta de Hyperdrive) por lote de
-- sincronización, por reporte y por recogida de reportes. El rol del Worker solo puede leer el catálogo y ejecutar
-- estas funciones; no tiene INSERT/UPDATE/DELETE directos sobre ninguna tabla.
-- sync_apply reproduce, en el mismo orden y con la misma semántica, la secuencia de sentencias que el Worker envía a
-- D1 en un batch (apps/edge-api/src/index.ts sync() + aggregates.ts).
SET search_path = stockfinder;

CREATE FUNCTION sync_apply(p_nonce text, p_events json) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = stockfinder, pg_temp AS $$
DECLARE
  e json; d json; ent text; eid text; op text; ver bigint; chg timestamptz; tbl text; checked timestamptz;
  keys text[]; bad text; n integer; written integer := 0;
  now_s bigint := floor(extract(epoch FROM clock_timestamp()))::bigint;
  touched text[] := '{}'; offer_ids text[] := '{}'; shop_ids text[] := '{}';
  newest_check timestamptz; newest_change timestamptz; last_id bigint := 0; rescan boolean := false;
BEGIN
  -- Nonce de un solo uso (mismo error de clave única que en D1 -> 409 en el Worker).
  DELETE FROM sync_nonces WHERE expires_at < now_s;
  INSERT INTO sync_nonces(nonce, expires_at) VALUES (p_nonce, now_s + 600);

  FOR e IN SELECT value FROM json_array_elements(p_events) LOOP
    ent := e->>'entity'; eid := e->>'entity_id'; op := e->>'operation';
    ver := (e->>'version')::bigint; chg := (e->>'changed_at')::timestamptz; checked := NULL;
    last_id := GREATEST(last_id, (e->>'event_id')::bigint);
    newest_change := GREATEST(newest_change, chg);
    d := CASE WHEN op = 'upsert' THEN e->'data' ELSE '{}'::json END;
    tbl := CASE ent WHEN 'category' THEN 'catalog_categories' WHEN 'shop' THEN 'catalog_shops'
                    WHEN 'product' THEN 'catalog_products' WHEN 'offer' THEN 'catalog_offers' END;
    IF tbl IS NULL OR op NOT IN ('upsert', 'delete') OR eid IS NULL OR ver IS NULL OR chg IS NULL OR json_typeof(d) <> 'object' THEN
      RAISE EXCEPTION 'invalid sync event' USING ERRCODE = '22023';
    END IF;

    IF ent = 'offer' AND op = 'upsert' THEN
      checked := (d->>'last_checked_at')::timestamptz;
      -- Historial: una fila por cambio de precio/stock (o primera observación), solo para versiones que el upsert aceptará.
      INSERT INTO offer_price_history(offer_id, product_id, shop_id, price_cents, in_stock, observed_at)
      SELECT eid, d->>'product_id', d->>'shop_id', (d->>'price_cents')::bigint, (d->>'in_stock')::smallint, checked
      WHERE NOT EXISTS (SELECT 1 FROM catalog_offers c WHERE c.id = eid AND (c.version >= ver OR
        (c.deleted_at IS NULL AND c.price_cents = (d->>'price_cents')::bigint AND c.in_stock = (d->>'in_stock')::smallint)));
      GET DIAGNOSTICS n = ROW_COUNT; written := written + n;
    END IF;

    -- Versiones de caché (structureBump): data_version salvo refresco solo-frescura; facets_version solo en cambios estructurales.
    IF ent <> 'offer' THEN
      UPDATE sync_status SET facets_version = facets_version + 1, data_version = data_version + 1 WHERE id = 1;
    ELSIF op = 'delete' THEN
      UPDATE sync_status SET facets_version = facets_version + 1, data_version = data_version + 1
      WHERE id = 1 AND EXISTS (SELECT 1 FROM catalog_offers c WHERE c.id = eid AND c.deleted_at IS NULL);
    ELSE
      UPDATE sync_status SET facets_version = facets_version + 1 WHERE id = 1 AND NOT EXISTS (
        SELECT 1 FROM catalog_offers c WHERE c.id = eid AND c.deleted_at IS NULL AND c.shop_id = d->>'shop_id' AND c.product_id = d->>'product_id');
      UPDATE sync_status SET data_version = data_version + 1 WHERE id = 1 AND NOT EXISTS (
        SELECT 1 FROM catalog_offers c WHERE c.id = eid AND c.deleted_at IS NULL AND c.product_id = d->>'product_id' AND c.shop_id = d->>'shop_id'
          AND c.price_cents = (d->>'price_cents')::bigint AND c.in_stock = (d->>'in_stock')::smallint AND c.url_public = d->>'url_public');
      -- Refresco solo-frescura (freshnessUpdate): toca columnas sin índice (update HOT) y deja el upsert genérico sin efecto.
      UPDATE catalog_offers SET version = ver, changed_at = chg, last_checked_at = checked
      WHERE id = eid AND version < ver AND deleted_at IS NULL AND product_id = d->>'product_id' AND shop_id = d->>'shop_id'
        AND price_cents = (d->>'price_cents')::bigint AND in_stock = (d->>'in_stock')::smallint AND url_public = d->>'url_public';
      GET DIAGNOSTICS n = ROW_COUNT; written := written + n;
    END IF;

    -- Upsert genérico: solo las columnas presentes en el evento (las omitidas se conservan), si la versión es más nueva.
    SELECT coalesce(array_agg(k), '{}') INTO keys FROM json_object_keys(d) AS k;
    SELECT k INTO bad FROM unnest(keys) AS k
    WHERE k IN ('id', 'version', 'changed_at', 'deleted_at', 'min_price_cents', 'min_price_in_stock_cents', 'max_price_in_stock_cents',
                'offers_count', 'in_stock_count')
       OR NOT EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = format('stockfinder.%I', tbl)::regclass
                      AND a.attname = k AND a.attnum > 0 AND NOT a.attisdropped)
    LIMIT 1;
    IF bad IS NOT NULL THEN RAISE EXCEPTION 'invalid sync field %', bad USING ERRCODE = '22023'; END IF;
    EXECUTE format(
      'INSERT INTO %1$I (id, version, changed_at, deleted_at%2$s) SELECT $1, $2, $3, $4%3$s FROM json_populate_record(NULL::%1$I, $5) AS r '
      'ON CONFLICT (id) DO UPDATE SET version = excluded.version, changed_at = excluded.changed_at, deleted_at = excluded.deleted_at%4$s '
      'WHERE excluded.version > %1$I.version',
      tbl,
      (SELECT string_agg(format(', %I', k), '') FROM unnest(keys) AS k),
      (SELECT string_agg(format(', r.%I', k), '') FROM unnest(keys) AS k),
      (SELECT string_agg(format(', %1$I = excluded.%1$I', k), '') FROM unnest(keys) AS k))
    USING eid, ver, chg, CASE WHEN op = 'delete' THEN chg END, d;
    GET DIAGNOSTICS n = ROW_COUNT; written := written + n;

    -- Productos cuyos agregados hay que recalcular (aggregateStatements).
    IF ent = 'product' THEN
      touched := touched || eid;
    ELSIF ent = 'offer' THEN
      offer_ids := offer_ids || eid;
      IF d->>'product_id' IS NOT NULL THEN touched := touched || (d->>'product_id'); END IF;
      IF checked IS NOT NULL THEN newest_check := GREATEST(newest_check, checked); END IF;
      IF op = 'delete' THEN rescan := true; END IF;
    ELSE
      IF ent = 'shop' THEN shop_ids := shop_ids || eid; rescan := true; END IF;
    END IF;
  END LOOP;

  IF newest_check IS NOT NULL THEN
    UPDATE sync_status SET offers_checked_max = newest_check WHERE id = 1 AND (offers_checked_max IS NULL OR offers_checked_max < newest_check);
  END IF;
  IF rescan THEN
    UPDATE sync_status SET offers_checked_max = (SELECT MAX(o.last_checked_at) FROM catalog_offers o
      WHERE o.deleted_at IS NULL AND EXISTS (SELECT 1 FROM catalog_shops s WHERE s.id = o.shop_id AND s.deleted_at IS NULL)) WHERE id = 1;
  END IF;
  -- Las lápidas no traen datos: el producto se resuelve desde la fila guardada de la oferta (product_id se conserva).
  touched := touched
    || ARRAY(SELECT o.product_id FROM catalog_offers o WHERE o.id = ANY (offer_ids) AND o.product_id IS NOT NULL)
    || ARRAY(SELECT o.product_id FROM catalog_offers o WHERE o.shop_id = ANY (shop_ids) AND o.product_id IS NOT NULL);
  IF cardinality(touched) > 0 THEN
    WITH agg AS (
      SELECT p.id, x.a, x.b, x.c, x.d, x.e FROM catalog_products p CROSS JOIN LATERAL (
        SELECT MIN(o.price_cents) AS a, MIN(o.price_cents) FILTER (WHERE o.in_stock = 1) AS b, MAX(o.price_cents) FILTER (WHERE o.in_stock = 1) AS c,
               COUNT(*) AS d, COUNT(*) FILTER (WHERE o.in_stock = 1) AS e
        FROM catalog_offers o WHERE o.product_id = p.id AND o.deleted_at IS NULL
          AND EXISTS (SELECT 1 FROM catalog_shops s WHERE s.id = o.shop_id AND s.deleted_at IS NULL)) x
      WHERE p.id = ANY (touched))
    UPDATE catalog_products AS p SET min_price_cents = agg.a, min_price_in_stock_cents = agg.b, max_price_in_stock_cents = agg.c,
      offers_count = agg.d, in_stock_count = agg.e
    FROM agg WHERE p.id = agg.id
      AND (p.min_price_cents, p.min_price_in_stock_cents, p.max_price_in_stock_cents, p.offers_count, p.in_stock_count)
          IS DISTINCT FROM (agg.a, agg.b, agg.c, agg.d, agg.e);
    GET DIAGNOSTICS n = ROW_COUNT; written := written + n;
  END IF;

  UPDATE sync_status SET last_event_id = GREATEST(last_event_id, last_id), last_sync_at = clock_timestamp(),
    source_changed_at = GREATEST(source_changed_at, newest_change) WHERE id = 1;
  -- finalizeStatements: total público de ofertas solo cuando cambió la estructura.
  UPDATE sync_status SET offers_total = (SELECT COUNT(*) FROM catalog_offers o WHERE o.deleted_at IS NULL
      AND EXISTS (SELECT 1 FROM catalog_shops s WHERE s.id = o.shop_id AND s.deleted_at IS NULL)), totals_version = facets_version
    WHERE id = 1 AND totals_version <> facets_version;
  RETURN written;
END $$;

-- Reportes de usuarios: búsqueda del producto/oferta, límite por IP (hash), tope de cola y alta, en una llamada.
CREATE FUNCTION report_create(p_slug text, p_offer text, p_note text, p_page_url text, p_ip_hash text, p_per_hour integer)
RETURNS TABLE(outcome text, report_id bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = stockfinder, pg_temp AS $$
#variable_conflict use_column
DECLARE pid text; nid bigint;
BEGIN
  SELECT p.id INTO pid FROM catalog_products p WHERE p.slug = p_slug AND p.deleted_at IS NULL;
  IF pid IS NULL THEN RETURN QUERY SELECT 'product'::text, NULL::bigint; RETURN; END IF;
  IF p_offer IS NOT NULL AND NOT EXISTS (SELECT 1 FROM catalog_offers o WHERE o.id = p_offer AND o.product_id = pid AND o.deleted_at IS NULL) THEN
    RETURN QUERY SELECT 'offer'::text, NULL::bigint; RETURN;
  END IF;
  IF (SELECT COUNT(*) FROM product_reports r WHERE r.ip_hash = p_ip_hash AND r.created_at > now() - interval '1 hour') >= p_per_hour THEN
    RETURN QUERY SELECT 'rate'::text, NULL::bigint; RETURN;
  END IF;
  -- Tope de la cola sin recoger (protege el límite de 500 MB del plan gratuito).
  IF (SELECT COUNT(*) FROM product_reports r WHERE r.status = 'new') >= 1000 THEN
    RETURN QUERY SELECT 'full'::text, NULL::bigint; RETURN;
  END IF;
  INSERT INTO product_reports(product_slug, offer_id, note, page_url, created_at, ip_hash)
  VALUES (p_slug, p_offer, p_note, p_page_url, clock_timestamp(), p_ip_hash) RETURNING product_reports.id INTO nid;
  RETURN QUERY SELECT 'ok'::text, nid;
END $$;

-- Recogida firmada de reportes nuevos por el NAS (máx. 50), con nonce de un solo uso; purga los enviados hace > 90 días.
CREATE FUNCTION reports_pull(p_nonce text)
RETURNS TABLE(id bigint, product_slug text, offer_id text, note text, page_url text, created_at timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = stockfinder, pg_temp AS $$
#variable_conflict use_column
DECLARE now_s bigint := floor(extract(epoch FROM clock_timestamp()))::bigint;
BEGIN
  DELETE FROM sync_nonces WHERE expires_at < now_s;
  INSERT INTO sync_nonces(nonce, expires_at) VALUES (p_nonce, now_s + 600);
  DELETE FROM product_reports r WHERE r.status = 'sent' AND r.created_at < now() - interval '90 days';
  RETURN QUERY UPDATE product_reports r SET status = 'sent'
    WHERE r.id IN (SELECT q.id FROM product_reports q WHERE q.status = 'new' ORDER BY q.id LIMIT 50)
    RETURNING r.id, r.product_slug, r.offer_id, r.note, r.page_url, r.created_at;
END $$;
