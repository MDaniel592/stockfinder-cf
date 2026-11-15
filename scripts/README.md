# scripts/

Índice generado a partir de la primera línea de cada script (`python3 scripts/make_indexes.py` lo regenera).
Los scripts de rastreo escriben solo en las bases aisladas `stockfinder_*_local` salvo que su cabecera diga otra cosa.

## LDLC y Neobyte

| Script | Qué hace |
|---|---|
| `amd_opn.py` | AMD OPN packaging policy (user policy 2026-11-15) |
| `icecat_identity.py` | Icecat as identity evidence (user policy 2026-11-15) |
| `ldlc_local.py` | LDLC Spain discovery and exact-identity offers, restricted to an isolated local DB |
| `ldlc_local.sh` | Clone the NAS catalogue once and crawl LDLC into the isolated copy only |
| `ldlc_pipeline.py` | Consume incremental LDLC review batches in the isolated local database only |
| `local_retailer_audit.py` | Read-only integrity and review coverage report for the two isolated retailer copies |
| `manufacturer_ean.py` | Manufacturer-published EAN/UPC + model as identity (user policy 2026-11-15) |
| `neobyte_local.py` | Neobyte discovery and exact-identity offers, restricted to an isolated local DB |
| `neobyte_local.sh` | Clone the NAS catalogue once and crawl Neobyte into the isolated copy only |
| `neobyte_pipeline.py` | Consume incremental Neobyte review batches in the isolated local database only |
| `neobyte_shards.py` | Split pending Neobyte review rows (artifacts/neobyte/pending.json) into agent shards |
| `retailer_common.py` | Helpers shared by the LDLC and Neobyte local crawlers (identical in both before extraction) |
| `retailer_listing_refresh.py` | Fast price/stock refresh for LDLC and Neobyte from category listing pages |
| `retailer_listing_refresh.sh` | Fast price/stock refresh from category listings into the isolated local DB |

## Coolmod y PcComponentes

| Script | Qué hace |
|---|---|
| `coolmod_categories.py` | Inventario publico de categorias de Coolmod SIN navegador (curl_cffi). Nunca publica productos ni ofertas |
| `coolmod_enrich.py` | Coolmod PDP enrichment SIN navegador (curl_cffi): rellena MPN / EAN / SKU de Coolmod (+ precio, stock, vendedor) en |
| `coolmod_filter.py` | Apply the PcComponentes generation rules to a Coolmod inventory |
| `coolmod_inventory_auto.py` | Run the Coolmod inventory until complete, then filter and stage candidates |
| `coolmod_refresh.py` | Refresco de ofertas de Coolmod por el endpoint publico de precios (servicio propuesto `coolmod-offers`) |
| `offers_status.py` | Antiguedad de las ofertas publicadas por tienda (p50/p95/max) y cuantas estan desactualizadas |
| `pccomponentes_auto.py` | Automatic, incremental PcComponentes discovery |
| `pccomponentes_batch.py` | Batched PcComponentes observations via its public article API |
| `pccomponentes_discover.py` | Discover PcComponentes candidates by public category inventory |
| `pccomponentes_refresh.py` | Refresco automatico de ofertas de PcComponentes (servicio `pccomponentes-offers`) |
| `pccomponentes_rules.py` | Catalog-coverage policy for PcComponentes and Coolmod discovery |
| `run_coolmod_inventory.sh` | _sin descripción_ |

## Referencias de fabricante

| Script | Qué hace |
|---|---|
| `fetch_official.py` | Fetch one public manufacturer page at a time (curl_cffi browser TLS, like the PcComponentes scripts) |
| `manufacturer_diff.py` | Compare the pending review queue with the manufacturer reference index |
| `manufacturer_index.py` | Build one reference index of manufacturer identifiers from every source already in the project |
| `manufacturer_scrape.py` | Shared helpers for the official manufacturer scrapers (GPUs and the other categories) |
| `scrape_asrock.py` | Scrape ASRock official product pages for every category, not only graphics cards |
| `scrape_asus.py` | Scrape ASUS official product pages for every category, not only GPUs |
| `scrape_powercolor_gpus.py` | Scrape PowerColor graphics-card specification pages |
| `scrape_zotac_gpus.py` | Scrape ZOTAC graphics-card specification pages |

## Imágenes

| Script | Qué hace |
|---|---|
| `catalog_images.py` | Join curated images by stable product ID, retaining asset provenance |
| `fetch_product_images.py` | Fetch official manufacturer product images (one request at a time, >=2 s apart) and store them as local WebP |
| `fetch_retailer_images.py` | Fetch shop product images (PcComponentes, Coolmod) as a FALLBACK for products without a manufacturer image |
| `images_apply_pg_only.py` | Aplica fotos a productos que solo existen en PostgreSQL (no están en data/catalog, así que enrich_images no los ve) |
| `images_fill2_merge.py` | _sin descripción_ |
| `images_fill_validate.py` | _sin descripción_ |
| `make_thumbnails.py` | Small WebP thumbnails for the product images shown in list rows, the comparison bar and the configurator picker |

## Catálogo y specs

| Script | Qué hace |
|---|---|
| `backfill_price_history.py` | Generate D1 SQL that backfills offer_price_history from the PostgreSQL catalog_outbox (read-only) |
| `catalog_flow.py` | Operación del flujo tienda -> catálogo -> web desde el host (ver docs/catalog-flow.md) |
| `generate_catalog_local.py` | Local development projection from curated public records; never a NAS write path |
| `generate_seed.py` | Deterministic synthetic catalogue. Does not contact any retailer |
| `normalize_gpu_chips.py` | Normalize GPU chip spellings through the production outbox. Dry-run unless --apply |
| `spec_gaps.sh` | Herramienta interna de specs incompletas (scripts/spec_gaps_web.py), solo en la red de casa. No es la web pública |
| `spec_gaps_web.py` | Herramienta interna (NO es la web pública): productos con specs clave ausentes y alta manual en data/overrides/ |
| `specs_fill_merge.py` | _sin descripción_ |
| `specs_infer.py` | Rellena huecos de specs por inferencia determinista (NO son datos de ficha). Salida: data/overrides/inferred-<fecha>-<cat>.json |

## Despliegue y entornos

| Script | Qué hace |
|---|---|
| `cloudflare.py` | Load local Cloudflare credentials without shell evaluation or printing them |
| `deploy.sh` | Compila y despliega el Worker (assets incluidos) sin depender de un Node instalado en el host ni de /tmp |
| `lab_end_to_end.py` | Synthetic lab: real PG -> Python agent -> local Worker/D1 -> NAS-off read |
| `local_site.sh` | Vista previa local de StockFinder en la red de casa, con los datos actuales de PostgreSQL |
| `seed_remote.py` | Explicit admin-only synthetic backfill via D1 API (never used by scrapers) |
| `staging_replay.sh` | Carga la base Supabase (modelo de lectura de staging) con el estado actual del outbox del NAS, igual que |
| `supabase.py` | Administración de la base Supabase de StockFinder (modelo de lectura público) vía Management API |
| `supabase_replica.py` | Independent incremental NAS -> Supabase replica. Never acknowledges or rewinds the D1 outbox |
| `test_pg.sh` | Ejecuta la suite del Worker (tests/edge*.test.ts: edge, edge-aggregates, edge-pg) contra un PostgreSQL 17 DESECHABLE |

## Otros

| Script | Qué hace |
|---|---|
| `cf_quota.py` | Comprueba el uso diario de Cloudflare frente a los límites del plan Free y avisa por Healthchecks.io |
| `export_review.py` | Export the private NAS queue for an agent/human review; never publishes candidates |
| `local_replay.py` | Vista previa local: copia el historial del outbox de PostgreSQL a un Worker local |
| `make_indexes.py` | Generate scripts/README.md and docs/README.md from file headers (run from the repo root) |
| `setup_supabase_features.py` | Provision the server-only feature facade and least-privilege replica credentials. No secrets on stdout |
| `stage_store_candidates.py` | Stage source fixtures in the NAS review queue without publishing observations |
| `validate_batch.py` | Validate one or more new catalog batch files before they are merged |
| `worker_metrics.py` | Read Worker CPU percentiles (milliseconds) and errors for a bounded time window. Never prints credentials |
