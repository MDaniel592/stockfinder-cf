# Operación

Desde la raíz. Solo servicios de StockFinder; nunca `down -v` para un reinicio normal.

```bash
dc() { docker compose --env-file .env -f infra/compose/compose.yaml "$@"; }
```

## Preparación e importación

Copiar `.env.example` a `.env`, completar y proteger con `chmod 600 .env`. `SYNC_SECRET` debe coincidir con Cloudflare; el token Cloudflare no va al agente NAS.

```bash
dc up -d postgres
dc build sync-agent
dc run --rm -T sync-agent python nas/catalog.py migrate
dc run --rm -T sync-agent python nas/import_catalog.py
dc up -d sync-agent
```

Reconstruir imagen tras cambiar codigo NAS incrustado. El publicador monta data/ y artifacts/: sus cambios no requieren reconstruccion. El catálogo llega por sincronización sin desplegar frontend. No reimportar snapshots piloto antiguos sobre observaciones más recientes.

## Lote de tiendas

```bash
python3 scripts/export_review.py
python3 scripts/stage_store_candidates.py
dc run --rm -T sync-agent python nas/review_queue.py approve --id ID --product-id PRODUCT_ID --note 'Evidencia'
dc run --rm -T sync-agent python nas/review_queue.py reject --id ID --note 'Motivo'
dc run --rm -T sync-agent python nas/review_queue.py targets > data/retailers/approved-coolmod-targets.json
# Los targets se exportan y releen automaticamente.
dc up -d coolmod-offers pccomponentes-offers
```

Importar catálogo antes de aprobar. Revisar JSON de targets antes de activar. Tras el lote, regenerar resumen y comprobar API, logs y backlog. No crear informes Markdown por tanda.

## Diagnóstico y recuperación

```bash
dc logs --tail 30 sync-agent catalog-publisher coolmod-offers pccomponentes-offers
dc run --rm -T sync-agent python nas/catalog.py status
# Reencolar catálogo hacia una D1 vacía/compatible:
dc run --rm -T sync-agent python nas/catalog.py backfill
# Pruebas PostgreSQL en esquema aislado:
dc run --rm -T -v "$PWD/tests:/app/tests:ro" sync-agent python tests/test_nas.py
```

Ante fallo: revisar conectividad/TLS, contrato o secreto y reiniciar solo el servicio afectado. No borrar ni marcar ACK manualmente. D1 sirve el catálogo aunque el NAS esté apagado. No reconstruir sobre datos divergentes sin respaldo/base nueva.

PostgreSQL usa outbox transaccional, versiones y tombstones; el agente tiene lock y confirma cada evento. D1 aplica lotes de hasta 20 y rechaza versiones antiguas. Sync usa HMAC-SHA256 sobre `timestamp.nonce.raw_body`, con headers `X-Sync-Timestamp`, `X-Sync-Nonce`, `X-Sync-Signature`. Reintentar con nonce nuevo. `last_event_id` no es checkpoint; `sync_lag_seconds` no mide backlog ni éxito de scraping. Cambiar solo la hora del sondeo solo emite evento si la última marca publicada tiene ≥6 h (ver «Refresco automático de ofertas»). Caché pública: 10–60 s.

## Cloudflare

Credenciales en `.env.cloudflare.local` (0600), nunca evaluarlo como shell.

```bash
python3 scripts/cloudflare.py d1 migrations apply DB --remote
npm run build
python3 scripts/cloudflare.py deploy
python3 scripts/cloudflare.py secret put SYNC_SECRET
# Volver a código compatible; no revierte D1:
python3 scripts/cloudflare.py rollback VERSION_ID
```

**Migración D1 0009 (marca por tienda, `shop_checks`)**: aditiva, aplicar *antes* de desplegar el Worker nuevo y antes de que el `sync-agent` envíe el primer evento `shop_check` (un Worker anterior lo rechaza con 400 y el outbox quedaría bloqueado). La fecha efectiva de una oferta es `MAX(last_checked_at, marca de su tienda)` salvo que su id figure en `unconfirmed` (`offerChecked()` en `apps/edge-api/src/aggregates.ts`); sin fila en `shop_checks` rige la fecha propia. Un evento `shop_check` mueve `data_version` (no `facets_version`) y no escribe ninguna fila de oferta. Rollback: volver al Worker anterior; la tabla sobra pero no estorba.

**Migración D1 0010 (optimización de lecturas/escrituras, `price_history_indexes`)**: aditiva, aplicar tras la 0009 y *antes* del Worker que la usa (el `DELETE` de `api_cache` lee la columna nueva `sync_status.cache_swept`). Crea `offer_price_history_offer(offer_id, observed_at, id)` (+1 fila escrita por cada fila de histórico, es decir, por cada cambio de precio o stock, nunca por un refresco de frescura) y la columna `cache_swept`. Con ella: (1) la consulta de bajadas de `/api/v1/catalog/highlights` busca la última y la anterior observación de cada oferta por índice, sin funciones de ventana sobre todo el histórico (≈7× menos filas leídas con 27.000 filas de histórico); (2) el payload de `highlights` se guarda en `api_cache` en una sola fila por hora (`hourly:highlights:24h`, versión = hora, **sin** `facets_version` para que una ráfaga de ~172 lotes de producto no la recalcule en cada petición): la consulta pesada corre como máximo una vez por hora; bajadas de precio y contadores de categoría pueden tener hasta 1 h de retraso (aceptado); (3) `finalizeStatements()` solo recorre `api_cache` si cambió `facets_version` o la hora (token en `cache_swept`), en vez de en cada lote de sync.

**Orden de despliegue (0009 + 0010)**: `python3 scripts/cloudflare.py d1 migrations apply DB --remote` (aplica 0009 y 0010) → `npm run build` → `python3 scripts/cloudflare.py deploy` → solo entonces reconstruir y recrear `sync-agent` (`docker compose build sync-agent && docker compose up -d --no-deps sync-agent`). Rollback: el Worker anterior sigue funcionando con las tablas y la columna nuevas (son aditivas); no hace falta revertir las migraciones.

Backups externos y restauración están pendientes. Preparar `pg_dump -Fc` cifrado fuera del NAS, verificar ambos códigos con `pipefail` y ensayar restauración aislada. Revisar cuotas de cuenta y medir crecimiento antes de ampliar carga; alertas/retención automáticas pendientes.

## Reportes de usuarios

`POST /api/v1/reports` es público (máx. 5/hora por IP con hash; sin datos personales). Recoger los nuevos (quedan `sent`):
`SYNC_ENDPOINT=https://.../internal/sync SYNC_SECRET=... python3 nas/reports_pull.py` añade a `artifacts/user-reports.jsonl`.
Aviso opcional: `python3 scripts/cloudflare.py secret put REPORT_WEBHOOK_URL` (ntfy/Discord/Slack, `{"text": ...}`).
Opcional `REPORT_SALT` (secret) para el hash; por defecto usa `SYNC_SECRET`. Migraciones 0004 y 0005 antes de desplegar.

## Refresco automático de ofertas

Flujo: `coolmod-offers` y `pccomponentes-offers` escriben ofertas en `retailer-data:/offers/*.json`; `sync-agent` (`RETAILER_SNAPSHOT_DIR`) importa cada fichero cambiado (un hash por fichero, ≤500 ofertas) con un savepoint por oferta y las publica por el outbox. Un fichero inválido (`snapshot_invalid`) o una oferta rechazada (`retailer_snapshot_imported` con `accepted`/`rejected`) nunca bloquea el resto ni el envío a Cloudflare. Targets: `/flow/targets/{coolmod,pccomponentes}.json` (volumen `flow-data`, solo lectura aquí); fallback a `data/retailers/approved-*.json` (+ `pilot.json`) o a PostgreSQL.

- **Coolmod antiguo, solo rollback** (`nas/scrapers/coolmod.cjs`, perfil `legacy-coolmod`): tandas de ≤200 targets, los de comprobación más antigua primero (estado `state/coolmod-state.json`), una página cada vez con pausa ≥2 s, un reintento ante `TargetClosedError`/timeout. Espera entre tandas = `min(SCRAPE_INTERVAL_SECONDS, COOLMOD_PASS_TARGET_SECONDS/ceil(N/200) − duración)`, mínimo 900 s. Con N=802: 5 tandas × (≈20 min de scraping + 90 min de espera) ≈ 9 h por pasada (objetivo ≤12 h), ≈2.600 páginas/día (1 página cada ~33 s). Con la espera mínima el límite está en ≈4.000 targets; por encima, subir `COOLMOD_BATCH_SIZE` no es posible (máx. 200): añadir una segunda instancia o aceptar >12 h. Un fallo de ciclo se registra como `scraper_failure` con `type`, `code` (p. ej. `EACCES`) y `message` (≤300 caracteres, credenciales de URL enmascaradas).
- **Coolmod por endpoint de precios** (`scripts/coolmod_refresh.py`, servicio activo `coolmod-offers`, imagen `sync-agent`, curl_cffi; **sustituye** a `coolmod-scraper`, nunca los dos a la vez). Bandera `COOLMOD_AJAX_REFRESH=1` (activada en Compose; sin ella no hace ninguna petición).
  - Fuente: `GET /view_v2/ajax/category/newAjaxPricesForProductsWithCode.php?productCodes=["PROD-…",…]&DayId=PM&ZoneCode=PENINSULA&VatId=NV&TarId=1`, el mismo JSON que piden los listados de la web (cabeceras `Referer` de un listado y `X-Requested-With`). robots.txt no lo excluye (`Allow: /`; solo se excluyen filtros, ordenación, `/consultas_ajax.php?cat=*`, etc.). Por código: `ProducCode` (= `retailer_sku`), `Price` "1.094,95" con IVA (coincide con el JSON-LD de la ficha pese a `VatId=NV`), `OldPrice`, `Stock` entero con tope 50, `TextDelivered` ("Agotado" ⇔ Stock 0), `Slug`, `LifeCycleCode` (P/V vistos; V con stock y fecha de entrega normal), `BecomeBackinStockDate`. Un código desconocido devuelve todo `null`. Admite al menos 100 códigos por llamada; Compose configura 100 (`COOLMOD_AJAX_CHUNK`).
  - Registro `source: coolmod-ajax` (renueva `last_checked_at`: precio y stock explícitos de la tienda) solo si el target está verificado en el catálogo, `Slug` = ruta de `url_public`, precio limpio > 0 y `Stock` entero coherente con `TextDelivered`; `in_stock = Stock > 0`. Cualquier anomalía → ficha.
  - Ficha (curl_cffi; el JSON-LD sale en el HTML): reverifica identidad (MPN exacto + SKU) cada `COOLMOD_PAGE_VERIFY_DAYS` (7), máx. `COOLMOD_PAGE_CHECKS_PER_PASS` (20) por pasada: primero anomalías (como mucho una vez cada 24 h), luego nunca verificadas, luego las más antiguas. 404/410 o redirección sin producto → `unavailable`. MPN/SKU distinto → ese target no publica nada (tampoco por AJAX) hasta que otra ficha lo confirme (reintento 24 h). Precio de ficha ≠ AJAX → no se publica y se registra `price_conflict`.
  - **Ojo**: el JSON-LD de Coolmod dice `InStock` también en productos agotados (p. ej. NH-U12S: AJAX `Stock 0`/"Agotado", `data-itemavailability="OutOfStock"` en la misma ficha). Por eso una ficha solo publica stock propio si JSON-LD y `data-itemavailability` coinciden; el stock fiable es el del AJAX. `coolmod-scraper` (solo JSON-LD) publica todo como disponible.
  - 403/429/503 o página de verificación: se para la pasada (sin fichas), se escribe lo obtenido y se duerme `COOLMOD_BLOCKED_SECONDS` (6 h). 3 llamadas seguidas con fallo técnico abortan la pasada.
  - Coste medido (09/11/2026, 1.779 targets verificados): 72 llamadas AJAX + 5 fichas en 215 s, 0 anomalías, 314 agotados. Por pasada ≈ 72 llamadas + ≤20 fichas ≈ 4–5 min; con `COOLMOD_REFRESH_INTERVAL_SECONDS=3600`: ≈1.700 llamadas ligeras + ≤480 fichas al día, frente a ≈4.000 cargas de página Playwright/día del scraper anterior (≈9 h por pasada). La verificación inicial de identidad de todos los targets tarda ≈ 1.800/20 pasadas ≈ 3,7 días (mientras tanto ya se publica por AJAX, porque la identidad se estableció al aprobar el target).
  - Salida `COOLMOD_OUTPUT_FILE` (`/output/offers/coolmod.json`, troceado en `coolmod.N.json` de ≤400 ofertas), estado `COOLMOD_REFRESH_STATE_FILE` (`/output/state/coolmod-refresh-state.json`). Prueba en seco: `dc run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/data:/app/data:ro" -v stockfinder-v2_flow-data:/flow:ro -v /tmp/cm-dry:/dry -e COOLMOD_AJAX_REFRESH=1 -e COOLMOD_DATA_DIR=/app/data -e COOLMOD_OUTPUT_FILE=/dry/offers/coolmod.json -e COOLMOD_REFRESH_STATE_FILE=/dry/state.json -e COOLMOD_TARGET_LIMIT=50 sync-agent python scripts/coolmod_refresh.py --once` (`/tmp/cm-dry` con `chmod 777`). Pruebas: `tests/test_coolmod_refresh.py`.
  - Otras fuentes vistas: listados HTML (`/<categoría>/?pagina=N`, 25 tarjetas `a[data-itemname]` con `data-itemid`/`data-itemprice`, sin stock ni MPN; renderizado en servidor), `sitemap_products.xml` (≈12.900 URLs con `lastmod`, sin precio: útil para descubrimiento), Doofinder (buscador de la web, zona `eu1`, hashid público en `eu1-config.doofinder.com/2.x/<store>.js`; no evaluado).
- **PcComponentes** (`scripts/pccomponentes_refresh.py`): cada `PCC_REFRESH_INTERVAL_SECONDS` (3 h), `GET /api/articles/list-by-ids?productIds=…&pageSize=N` con curl_cffi (huella TLS de Chrome, sin cookies ni retos). 40 IDs por llamada es el máximo del API (41 → HTTP 400) y `PCC_REQUEST_DELAY_SECONDS` (`2,3`) entre llamadas: ≈2.140 targets = 54 llamadas ≈ 2,5 min por pasada. Solo vendedor directo, MPN exacto, stock = `availabilityCode` 1/3 (el API no da unidades: `stock`/`initialStock` vienen a `null`). Targets: `/flow/targets` + ofertas PcComponentes de `data/retailers/pilot.json` cuyo SKU no esté (`PCC_PILOT_TARGETS_FILE`, `''` lo desactiva).
  - **HTTP 429**: no se reintenta en caliente; se espera `max(Retry-After, enfriamiento)` de `PCC_RATE_LIMIT_COOLDOWNS` (`60,180` s; evento `cooldown`) y se sigue. 3 lotes seguidos fallidos también consumen un enfriamiento; sin enfriamientos restantes abortan la pasada (`PCC_RATE_LIMIT_COOLDOWNS=''` = comportamiento anterior). Los lotes fallidos se reintentan una vez al final de la pasada (`PCC_RETRY_FAILED_CHUNKS=1`, `retried_chunks` en `refresh_done`) y los SKU que sigan fallando se guardan en `retry_first` del estado y van primero en la pasada siguiente.
  - Alternativas evaluadas (2026-11-09): `/api/articles/category/{uuid}` (la usa el descubrimiento) también va de 40 en 40 y las 12 categorías suman ≈20.000 artículos (≈500 páginas; ≈6.600 de venta directa): más peticiones que `list-by-ids` para los targets. `/api/articles/search` y `/api/articles/*/buybox` están vetados en robots.txt y no se usan.
- **Ofertas que desaparecen**: Coolmod 404/410, redirección sin el producto o stock `OutOfStock/SoldOut/Discontinued`; PcComponentes ficha ausente en 2 sondeos seguidos con respuesta sana, o `availabilityCode` 7/9999 sin precio, o (con `PCC_MARKETPLACE_GONE_AS_UNAVAILABLE=1`) ficha vendida solo por un marketplace con `availabilityCode` 7/9999 en 2 sondeos seguidos → `in_stock=false` conservando el último precio (`unavailable: true`). Solo se emite `unavailable` para ofertas ya publicadas (`unavailable_unpublished` en el resumen), así el sync-agent no registra rechazos «No published offer to mark unavailable». Un fallo técnico (bloqueo, timeout, marketplace con otro código) conserva el dato anterior y solo se registra. Nunca se borran ofertas.
- **Frescura**: la fecha pública de «comprobado» la lleva la marca por tienda `shop_check` (ver `docs/catalog-flow.md`): una fila por tienda y pasada (≈2 eventos por hora de Coolmod y por 3 h de PcComponentes), en lugar de un evento por oferta. Además `catalog.write()` republica el `last_checked_at` de una oferta sin cambios como máximo cada `OFFER_FRESHNESS_SECONDS` (24 h, red de seguridad): ≈1 evento/día/oferta (antes ≈4 con 6 h). Comprobar la cuota con `scripts/cf_quota.py`.
- **Red hacia Cloudflare**: `push` prueba el DNS del sistema (conexión 8 s) y después `SYNC_FALLBACK_IPS` conectando por IP con SNI, `Host` y verificación TLS completos y HMAC nuevo en cada intento. Una IP de respaldo que funciona se prueba primero 10 min. Eventos `sync_network_error` / `sync_fallback_ip_used`.

```bash
# Antigüedad p50/p95/máx por tienda y ofertas >24 h / >48 h (solo lectura)
dc run --rm -T sync-agent python scripts/offers_status.py
dc logs --tail 20 coolmod-scraper pccomponentes-offers sync-agent
# Pruebas (esquema aislado + lógica sin red):
dc run --rm -T --tmpfs /tmp -v "$PWD/tests:/app/tests:ro" sync-agent python tests/test_nas.py
dc run --rm -T -v "$PWD/tests:/app/tests:ro" sync-agent python tests/test_offers_refresh.py
# Pasada de prueba de PcComponentes contra la tienda, con rutas temporales (nunca /output/offers ni el estado real):
mkdir -p /tmp/pcc-dry && chmod 777 /tmp/pcc-dry && python3 -c "import json,sys; json.dump(json.load(open(sys.argv[1]))[:400], open('/tmp/pcc-dry/targets.json','w'))" <targets.json>
dc run --rm -T --no-deps -v "$PWD/scripts:/app/scripts:ro" -v /tmp/pcc-dry:/dry pccomponentes-offers python scripts/pccomponentes_refresh.py --once --targets /dry/targets.json --output /dry/offers/pccomponentes.json --state /dry/state.json
node node_modules/vitest/vitest.mjs run tests/offers-coolmod.test.ts
```

## Publicador automático del catálogo

El servicio `catalog-publisher` (mismo código que `sync-agent`) valida e importa cada pocos minutos lo que escriben los agentes de verificación (`data/catalog`, `data/aliases`, `artifacts/decisions-*.json`), auto-aprueba MPN ya verificados y exporta los targets de los scrapers a `/flow/targets` (volumen `flow-data`). Flujo completo y depuración: `docs/catalog-flow.md`.

```bash
dc build sync-agent && dc up -d catalog-publisher   # tras cambiar nas/ o scripts/
python3 scripts/catalog_flow.py status               # estado de cola, publicado y publicador
python3 scripts/catalog_flow.py publish --once       # ciclo inmediato (--dry-run: se revierte)
dc logs --tail 30 catalog-publisher
# Pausar / reanudar (no hace nada mientras exista /flow/PAUSE):
dc run --rm -T catalog-publisher touch /flow/PAUSE ; dc run --rm -T catalog-publisher rm -f /flow/PAUSE
```

`AUTO_APPROVE_KNOWN=0` desactiva la auto-aprobación; `PUBLISH_RETAILER_CODES` queda apagado hasta desplegar el Worker que acepta `retailer_codes`. Los pasos manuales de «Lote de tiendas» siguen siendo válidos, pero ya no son necesarios.

## Vista previa local (evaluar sin gastar D1 de producción)

`bash scripts/local_site.sh` arranca el Worker y la web en el NAS con una D1 local propia (`/tmp/sf-local`), cargada con el estado actual de PostgreSQL (último evento de cada entidad del outbox, solo lectura) y el histórico de precios. Abrir desde la red de casa: `http://192.168.1.50:8800/` (`PORT=` para otro puerto).

- `--no-build`: usa `apps/web/dist` tal cual. Sin opción: recompila la web antes.
- `--refresh`: recarga los datos en el servidor ya arrancado.
- `--stop`: lo detiene.

Usa un secreto de sincronización propio (`/tmp/sf-local/.dev.vars`) y `scripts/local_replay.py` solo acepta destinos `127.0.0.1`: no puede escribir en producción. Todas las pruebas, capturas y escaneos del catálogo deben ir contra esta vista previa o contra PostgreSQL, nunca contra `workers.dev` (cada página del catálogo consume entre cientos y miles de filas leídas de la cuota diaria de D1).

## Specs incompletas y correcciones manuales (herramienta interna)

`bash scripts/spec_gaps.sh` arranca `scripts/spec_gaps_web.py` (solo stdlib, proceso del host, sin contenedor) en la IP de la LAN: `http://192.168.1.50:8801/` (`PORT=` para otro puerto; `--stop` la detiene; registro en `/tmp/sf-spec-gaps/server.log`). No es la web pública ni se despliega en Cloudflare; no tiene usuarios, así que no exponerla fuera de casa.

- **Qué lista**: por categoría, los productos de `data/catalog/*.json` (normalizados con `nas/spec_normalize.py` y con las correcciones ya aplicadas) a los que les falta alguna spec clave de la ficha, los filtros o el configurador (`REQUIRED` en `nas/spec_overrides.py`; algunas son condicionales, p. ej. `Altura` solo en disipadores de aire, `Lectura secuencial` solo en SSD). Filtro por spec y búsqueda; enlace a `manufacturer_url`.
- **Alta manual**: escribir el valor tal como figura en la ficha oficial, con la clave y el formato canónicos (`6000 MT/s`, `CL36`, `AMD B650`, `2 × 120 mm`…); se normaliza y se valida antes de guardar. O marcar «el fabricante no lo publica» para que deje de figurar como hueco (no toca las specs). Obligatorio: URL consultada (https). Nunca rellenar por suposición.
- **Dónde queda**: `data/overrides/manual-<categoría>.json`, registros `{product_id, category_id, key, value | status: "not_published", source: "manual", date, source_url, note?}`. Gana el último registro de cada producto y clave. Se pueden quitar desde la página «Correcciones».
- **Cómo llega a la web**: `import_catalog` (y el publicador) aplican las correcciones DESPUÉS de normalizar, así que ganan al dato del lote y sobreviven a los reimports. El publicador solo reimporta los lotes cuyos productos tienen correcciones nuevas o cambiadas (en ≤5 min). Un fichero de correcciones inválido se omite entero y aparece como error `overrides` en `catalog_flow.py status`/logs; la herramienta lo avisa en el resumen.

## Supabase: configurador y ofertas

La réplica del NAS sirve el configurador, las listas compartidas y el ranking sin consultas D1. Arranque: `dc --profile supabase up -d --no-deps supabase-replica`. Diagnóstico: `dc logs --tail 10 supabase-replica`. El cursor vive en `catalog_replica_checkpoints` y no altera los ACK de D1.

Migraciones y permisos: `python3 scripts/setup_supabase_features.py`. No ejecutar `data-api-off`: la fachada privada necesita que `stockfinder_features` sea el único esquema expuesto. Las tablas del catálogo y las claves de servidor no se exponen al navegador. Detalles en [Supabase](../supabase-migration.md).

CPU real del Worker: `python3 scripts/worker_metrics.py --minutes 60`. Los logs `request_sample` distinguen ruta y caché, pero su `wall_ms` mide tiempo de pared, no CPU. La caché frontal global se deja desactivada para conservar el tratamiento gratuito de los assets estáticos; se usa caché interna por categoría y respuestas en flujo cuando no requieren actualizar estado.
