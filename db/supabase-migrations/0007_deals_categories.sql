-- Top 20 overall and top 10 per category, compact payloads and a freshness window for 15-minute replication.
CREATE OR REPLACE FUNCTION stockfinder.feature_refresh() RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE result jsonb;
BEGIN
 WITH recent AS (
  SELECT DISTINCT ON(h.offer_id) h.* FROM stockfinder.offer_price_history h
  WHERE h.observed_at>=now()-interval '24 hours' ORDER BY h.offer_id,h.observed_at DESC,h.id DESC
 ), candidates AS (
  SELECT DISTINCT ON(p.id) stockfinder.feature_product(p) || jsonb_build_object('previous_price_cents',prev.price_cents,
   'shop_name',s.name,'dropped_at',stockfinder.iso(h.observed_at)) AS body,
   (prev.price_cents-h.price_cents)*1.0/prev.price_cents AS ratio,h.observed_at,p.id,p.category_id
  FROM recent h
  JOIN LATERAL (SELECT q.price_cents,q.in_stock FROM stockfinder.offer_price_history q WHERE q.offer_id=h.offer_id
   AND (q.observed_at,q.id)<(h.observed_at,h.id) ORDER BY q.observed_at DESC,q.id DESC LIMIT 1) prev ON true
  JOIN stockfinder.catalog_offers o ON o.id=h.offer_id AND o.deleted_at IS NULL AND o.in_stock=1 AND o.price_cents=h.price_cents
  JOIN stockfinder.catalog_products p ON p.id=o.product_id AND p.deleted_at IS NULL AND p.min_price_in_stock_cents=o.price_cents
  JOIN stockfinder.catalog_shops s ON s.id=o.shop_id AND s.deleted_at IS NULL
  JOIN stockfinder.catalog_categories c ON c.id=p.category_id AND c.deleted_at IS NULL
  WHERE h.in_stock=1 AND prev.in_stock=1 AND prev.price_cents>0 AND stockfinder.feature_checked(o)>=now()-interval '24 hours'
   AND h.price_cents*100<=prev.price_cents*99 AND h.price_cents*10>=prev.price_cents*3
  ORDER BY p.id,(prev.price_cents-h.price_cents)*1.0/prev.price_cents DESC,h.observed_at DESC,o.id
 ), ranked AS (
  SELECT *,row_number() OVER(PARTITION BY category_id ORDER BY ratio DESC,observed_at DESC,id) AS category_rank FROM candidates
 ), top AS (SELECT * FROM candidates ORDER BY ratio DESC,observed_at DESC,id LIMIT 20)
 SELECT jsonb_build_object('drops',(SELECT coalesce(jsonb_agg(body ORDER BY ratio DESC,observed_at DESC,id),'[]'::jsonb) FROM top),
  'by_category',(SELECT coalesce(jsonb_object_agg(category_id,items),'{}'::jsonb) FROM (
    SELECT category_id,jsonb_agg(body ORDER BY ratio DESC,observed_at DESC,id) AS items FROM ranked WHERE category_rank<=10 GROUP BY category_id
  ) grouped),
  'calculated_at',stockfinder.iso(clock_timestamp()),'window_hours',24,'sort','percentage','stale',false) INTO result;
 result:=result || jsonb_build_object('categories',(SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.name),'[]'::jsonb) FROM (
  SELECT c.id,c.name,count(p.id) AS products,count(p.id) FILTER(WHERE p.in_stock_count>0) AS in_stock,min(p.min_price_in_stock_cents) AS min_price_cents
  FROM stockfinder.catalog_categories c LEFT JOIN stockfinder.catalog_products p ON p.category_id=c.id AND p.deleted_at IS NULL
  WHERE c.deleted_at IS NULL GROUP BY c.id,c.name) x));
 INSERT INTO stockfinder_features.snapshot VALUES(1,result,clock_timestamp()) ON CONFLICT(id) DO UPDATE SET body=excluded.body,updated_at=excluded.updated_at;
END $$;


CREATE OR REPLACE FUNCTION stockfinder_features.rpc(op text,payload jsonb DEFAULT '{}'::jsonb) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path='' AS $$
DECLARE result jsonb; part_row stockfinder.catalog_products; cat text:=payload->>'category'; q text:=trim(coalesce(payload->>'q',''));
 page integer:=coalesce((payload->>'page')::integer,1); filter_specs jsonb:=coalesce(payload->'specs','{}'::jsonb); total integer; outcome text; mark timestamptz;
BEGIN
 SELECT updated_at INTO mark FROM stockfinder_features.snapshot WHERE id=1;
 IF op='deals' THEN
  IF cat IS NOT NULL AND cat NOT IN('cpu','gpu','ram','motherboard','storage','case','psu','cooler') THEN RAISE EXCEPTION 'invalid category' USING ERRCODE='22023'; END IF;
  SELECT (body-'by_category') || jsonb_build_object('stale',updated_at<now()-interval '45 minutes',
    'drops',CASE WHEN cat IS NULL THEN body->'drops' ELSE coalesce(body->'by_category'->cat,'[]'::jsonb) END)
    INTO result FROM stockfinder_features.snapshot WHERE id=1;
  RETURN coalesce(result,jsonb_build_object('drops','[]'::jsonb,'categories','[]'::jsonb,'stale',true));
 ELSIF op='state' THEN
  RETURN jsonb_build_object('replica_updated_at',stockfinder.iso(mark),'stale',mark IS NULL OR mark<now()-interval '45 minutes',
   'categories',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',id,'name',name)),'[]'::jsonb) FROM stockfinder.catalog_categories WHERE deleted_at IS NULL),
   'shops',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',id,'name',name,'url_public',url_public)),'[]'::jsonb) FROM stockfinder.catalog_shops WHERE deleted_at IS NULL));
 ELSIF op='options' THEN
  IF cat NOT IN ('cpu','gpu','ram','motherboard','storage','case','psu','cooler') OR cat IS NULL OR page NOT BETWEEN 1 AND 25 OR length(q)>100 OR length(q)=1
    OR jsonb_typeof(filter_specs)<>'object' OR EXISTS(SELECT 1 FROM jsonb_object_keys(filter_specs) k WHERE k NOT IN ('Socket','Tipo de memoria'))
    OR EXISTS(SELECT 1 FROM jsonb_each(filter_specs) j WHERE jsonb_typeof(j.value)<>'array' OR jsonb_array_length(j.value)>8)
  THEN RAISE EXCEPTION 'invalid options' USING ERRCODE='22023'; END IF;
  WITH filtered AS (
   SELECT p.* FROM stockfinder.catalog_products p JOIN stockfinder.catalog_categories c ON c.id=p.category_id AND c.deleted_at IS NULL
   WHERE p.deleted_at IS NULL AND p.category_id=cat
    AND NOT EXISTS(SELECT 1 FROM regexp_split_to_table(lower(q),E'\\s+') t WHERE t<>'' AND position(t IN stockfinder.search_text(p.name,p.brand,p.mpn,p.retailer_codes))=0)
    AND NOT EXISTS(SELECT 1 FROM jsonb_each(filter_specs) s WHERE NOT coalesce((p.specs->>s.key IN(SELECT jsonb_array_elements_text(s.value))),false))
  ), selected AS (SELECT * FROM filtered ORDER BY (min_price_in_stock_cents IS NULL),id LIMIT 48 OFFSET (page-1)*48)
  SELECT jsonb_build_object('data',coalesce((SELECT jsonb_agg(stockfinder.feature_product(s::stockfinder.catalog_products) ORDER BY (s.min_price_in_stock_cents IS NULL),s.id) FROM selected s),'[]'::jsonb),
   'total',(SELECT count(*) FROM filtered),'page',page,'limit',48,'replica_updated_at',stockfinder.iso(mark),'stale',mark IS NULL OR mark<now()-interval '45 minutes') INTO result;
  RETURN result;
 ELSIF op IN('product','offers') THEN
  IF coalesce(payload->>'slug','') !~ '^[a-zA-Z0-9_-]{1,100}$' THEN RAISE EXCEPTION 'invalid slug' USING ERRCODE='22023'; END IF;
  SELECT x.* INTO part_row FROM stockfinder.catalog_products x JOIN stockfinder.catalog_categories c ON c.id=x.category_id AND c.deleted_at IS NULL
   WHERE x.slug=payload->>'slug' AND x.deleted_at IS NULL;
  IF part_row.id IS NULL THEN RAISE EXCEPTION 'product absent' USING ERRCODE='P0002'; END IF;
  IF op='product' THEN RETURN jsonb_build_object('data',stockfinder.feature_product(part_row),'replica_updated_at',stockfinder.iso(mark)); END IF;
  SELECT coalesce(jsonb_agg(jsonb_build_object('id',o.id,'shop_name',s.name,'price_cents',o.price_cents,'in_stock',o.in_stock,
   'last_checked_at',stockfinder.iso(stockfinder.feature_checked(o)),'url_public',o.url_public) ORDER BY o.in_stock DESC,o.price_cents,o.id),'[]'::jsonb)
  INTO result FROM stockfinder.catalog_offers o JOIN stockfinder.catalog_shops s ON s.id=o.shop_id AND s.deleted_at IS NULL WHERE o.product_id=part_row.id AND o.deleted_at IS NULL;
  RETURN jsonb_build_object('data',result,'replica_updated_at',stockfinder.iso(mark));
 ELSIF op='watchlist' THEN
  IF jsonb_typeof(payload->'slugs')<>'array' OR jsonb_array_length(payload->'slugs') NOT BETWEEN 1 AND 20 THEN RAISE EXCEPTION 'invalid watchlist' USING ERRCODE='22023'; END IF;
  SELECT coalesce(jsonb_agg(jsonb_build_object('slug',p.slug,'name',p.name,
   'min_price_cents',(SELECT min(o.price_cents) FROM stockfinder.catalog_offers o JOIN stockfinder.catalog_shops s ON s.id=o.shop_id AND s.deleted_at IS NULL
     WHERE o.product_id=p.id AND o.deleted_at IS NULL AND o.in_stock=1 AND stockfinder.feature_checked(o)>=now()-interval '24 hours'),
   'last_checked_at',(SELECT stockfinder.iso(max(stockfinder.feature_checked(o))) FROM stockfinder.catalog_offers o WHERE o.product_id=p.id AND o.deleted_at IS NULL))), '[]'::jsonb)
  INTO result FROM stockfinder.catalog_products p JOIN stockfinder.catalog_categories c ON c.id=p.category_id AND c.deleted_at IS NULL
   WHERE p.deleted_at IS NULL AND p.slug IN (SELECT jsonb_array_elements_text(payload->'slugs'));
  RETURN jsonb_build_object('data',result);
 ELSIF op='build_create' THEN
  IF length(coalesce(payload->>'name','')) NOT BETWEEN 1 AND 80 OR jsonb_typeof(payload->'parts')<>'object'
   OR length(coalesce(payload->>'ip_hash','')) NOT BETWEEN 16 AND 128 OR (SELECT count(*) FROM jsonb_each(payload->'parts')) NOT BETWEEN 1 AND 8
   OR EXISTS(SELECT 1 FROM jsonb_each_text(payload->'parts') j WHERE j.key NOT IN('cpu','gpu','ram','motherboard','storage','case','psu','cooler') OR j.value !~ '^[a-zA-Z0-9_-]{1,100}$')
  THEN RAISE EXCEPTION 'invalid build' USING ERRCODE='22023'; END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(payload->>'ip_hash',0));
  outcome:=stockfinder.build_share_create((payload->>'id')::uuid,payload->>'name',(payload->'parts')::json,payload->>'ip_hash',30);
  RETURN jsonb_build_object('outcome',outcome,'id',payload->>'id');
 ELSIF op='build_get' THEN
  SELECT jsonb_build_object('id',b.id,'name',b.name,'parts',b.parts,'created_at',stockfinder.iso(b.created_at)) INTO result FROM stockfinder.shared_builds b WHERE b.id=(payload->>'id')::uuid;
  IF result IS NULL THEN RAISE EXCEPTION 'build absent' USING ERRCODE='P0002'; END IF;
  RETURN jsonb_build_object('data',result);
 END IF;
 RAISE EXCEPTION 'invalid operation' USING ERRCODE='22023';
END $$;

