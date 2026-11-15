# Flujo de catálogo automatizado (tienda → catálogo → web)

Del descubrimiento de una referencia en Coolmod/PcComponentes a su aparición en la web, sin pasos manuales salvo la verificación con IA de lo nuevo. Las reglas de identidad no cambian (`docs/catalog.md`): MPN/EAN exacto o alias revisado; nunca por parecido de nombre.

```
 Coolmod / PcComponentes (páginas públicas)
   │ descubrimiento automático (pccomponentes-discovery, coolmod-inventory; backfill: catalog_flow.py discover)
   ▼
 COLA DE REVISIÓN (PostgreSQL, retailer_review_queue)  ◄── NO publica nada
   │                         │
   │ MPN/EAN ya verificado   │ referencia nueva o dudosa
   ▼                         ▼
 AUTO-APROBACIÓN         catalog_flow.py shards ──► agentes de IA (1 por shard, docs/catalog-shard-protocol.md)
 (catalog-publisher)       verifican ficha oficial y escriben:
   ▲                         data/catalog/reviewed-batch-*.json   (productos)
   │                         data/aliases/reviewed-aliases-*.json (códigos de tienda)
   │                         artifacts/decisions-*.json           (approve/reject)
   │                         │
   └─────────────────────────┘
 CATALOG-PUBLISHER (cada 5 min): validar → productos → alias → decisiones → auto-aprobar → targets
   │ escribe /flow/targets/{coolmod,pccomponentes}.json (volumen flow-data, solo lectura para scrapers)
   ▼
 SCRAPERS / REFRESCO DE OFERTAS leen los targets ──► precio y stock reales ──► import_offer (PostgreSQL + outbox)
   ▼
 SYNC-AGENT (HMAC, ACK por evento) ──► Worker Cloudflare / D1 ──► WEB
```

## Qué es automático y qué requiere agentes de IA

| Paso | Quién | Notas |
|---|---|---|
| Descubrir y poner en cola | Automático | `pccomponentes-discovery` (12 h), `coolmod-inventory` (24 h). Backfill profundo: `catalog_flow.py discover --backfill …` |
| Auto-aprobar MPN ya verificado | Automático (publicador) | Solo MPN normalizado idéntico a un identificador verificado del producto propuesto, sin nota «Pendiente: …», sin conflicto de identidad y con vendedor directo |
| Importar lotes, alias y decisiones | Automático (publicador) | Idempotente; un fichero inválido o a medio escribir se omite y se reintenta |
| Exportar targets y refrescar ofertas | Automático | El publicador escribe `/flow/targets`; los scrapers los leen |
| Sync a D1 y web | Automático | Sin cambios |
| Verificar referencias NUEVAS contra la ficha oficial | **Agentes de IA** | `/catalog-flow` (comando de Claude Code) o `catalog_flow.py shards` + un agente por shard |

Lo que ve el usuario: una referencia nueva sin producto conocido espera en la cola hasta que un agente la verifique; todo lo demás fluye solo.

## El publicador (`nas/publisher.py`, servicio `catalog-publisher`)

Misma imagen que `sync-agent`; `read_only`, `cap_drop: ALL`, `no-new-privileges`, 256 MB / 0,5 CPU. Monta `data/` y `artifacts/` en solo lectura (los lotes los escriben otros procesos en el host y se ven sin reconstruir) y el volumen `flow-data` en `/flow` (única escritura, solo del publicador). Lock advisory propio (`73610942`; el sync-agent usa `73610941`) por ciclo, de modo que `publish --once` convive con el servicio.

Orden de cada ciclo (intervalo `PUBLISH_INTERVAL_SECONDS`, 300 por defecto):

1. **Validar** con `scripts/validate_batch.py` (importado, sin modificarlo; ver `nas/flow_validation.py`): `data/catalog/*.json` y `data/aliases/*.json`. Cambios por hash. Inválido o ilegible → se registra (`batch_invalid`, `alias_file_invalid`) y se omite; no bloquea al resto.
2. **Productos**: `import_catalog.import_products` por fichero válido y cambiado (más categorías/tiendas/imágenes). Con `PUBLISH_RETAILER_CODES=1` incluye el campo opcional `retailer_codes` (déjalo apagado hasta desplegar el Worker que lo acepta).
3. **Alias**: `import_catalog.import_aliases` de `data/aliases/*.json` y `data/product-identifiers.json`, alias a alias; los que fallan (producto aún ausente) se reintentan.
4. **Decisiones** `artifacts/decisions-*.json` con `review_queue.bulk`. Solo se envían las filas aún `pending`. Las que fallan por `Canonical product absent` o `Verified identifier does not match` (producto/alias todavía no importado) se reintentan cada ciclo hasta `DECISION_MAX_ATTEMPTS` (2016 ≈ 7 días) mientras el fichero no cambie; el resto de errores se registran como permanentes.
5. **Propuestas + auto-aprobación**: a las pendientes sin producto propuesto se les asigna uno solo si su MPN (o EAN global) coincide con exactamente un identificador verificado. Se auto-aprueban (máx. `AUTO_APPROVE_MAX`, 200) las que además pasan: MPN idéntico a un identificador `mpn` del producto en el ámbito de su marca (lo reconfirma `approve()`), sin nota «Pendiente…», sin `manufacturer_variant_conflict`/`identity_status` de conflicto, sin `direct_seller_verification` pendiente y sin EAN que contradiga. Nota: «Auto-aprobado: MPN exacto coincide con el identificador verificado de <product_id> (fecha)». `AUTO_APPROVE_KNOWN=0` lo desactiva.
6. **Targets**: `/flow/targets/coolmod.json` y `pccomponentes.json` (`[{product_id, retailer_sku, mpn, url_public}]`, ordenados), escritura atómica y solo si cambian.
7. **Estado**: `/flow/state/publisher.json` (último ciclo, contadores, ficheros pendientes/inválidos, errores recientes) y logs JSON sin secretos.

Pausa: `touch /flow/PAUSE` (p. ej. `dc exec catalog-publisher touch /flow/PAUSE` o desde un contenedor con el volumen) detiene todo trabajo; borrar el fichero reanuda.

## Alias y criterio B

Cada producto puede tener dos referencias: la del **fabricante** (`mpn`, `mpn_source: manufacturer`) y la de **tienda**, registrada como alias en `data/aliases/*.json` (mismo formato que `data/product-identifiers.json`: `product_id`, `kind`, `value`, `source_url` de la tienda y `note` con la evidencia ≥40 caracteres). El criterio B (placas base y gráficas: ficha oficial coincidente en modelo y todos los atributos de variante, EAN sin contradicción) aprueba con alias; si la ficha oficial no publica código, el producto lleva el de la tienda con `mpn_source: retailer` (la web muestra «≈»). RAM, CPU boxed/tray, regiones, OEM/bulk y kits nunca por B. Política: `docs/catalog.md` («Referencias: fabricante y tienda»); instrucciones para los agentes: `docs/catalog-shard-protocol.md`.

Orden en el publicador: productos → alias → decisiones. Una decisión cuyo alias aún no está importado falla con «Verified identifier does not match…» y se reintenta, de modo que los agentes pueden escribir lote, alias y decisiones en cualquier orden. `retailer_codes` hacia el Worker solo se envía con `PUBLISH_RETAILER_CODES=1`.

## Operación (`scripts/catalog_flow.py`, desde la raíz del repo)

```bash
python3 scripts/catalog_flow.py status                 # cola, publicado, antigüedad de ofertas, publicador, sync, servicios
python3 scripts/catalog_flow.py shards --dry-run       # plan de shards (≤60) de las pendientes SIN revisar
python3 scripts/catalog_flow.py shards --prefix q2     # escribe artifacts/shards/q2-<categoría>-<n>.json
python3 scripts/catalog_flow.py validate               # valida todos los lotes y alias (solo lectura)
python3 scripts/catalog_flow.py publish --once         # un ciclo ahora; --dry-run lo revierte
python3 scripts/catalog_flow.py discover --backfill tarjetas-graficas placas-base --pages 10
```

`shards` selecciona filas `pending` sin `review_note` (o con la nota automática «Observación de oferta…»), sin producto propuesto (las auto-aprueba el publicador; `--include-known` las incluye) y excluye las retenidas «Pendiente: …» (`--include-retained` para re-revisarlas). Agrupa por categoría y marca, ≤60 filas por shard, y no sobrescribe shards existentes sin `--force`. `discover --backfill` usa `scripts/pccomponentes_discover.py` (orden `relevance`, excluye lo ya conocido), parte en lotes de ≤190 y los pone en la cola con `review_queue.py stage`; `--no-stage` solo genera los ficheros. Antes de reconstruir la imagen, añade `--mount-code` (`catalog_flow.py --mount-code publish --once`) para usar el código del host.

Verificación asistida: comando de Claude Code `/catalog-flow` (`.claude/commands/catalog-flow.md`).

## Enriquecimiento de fichas Coolmod (`coolmod-enrich`)

El inventario de Coolmod solo lee listados, así que sus filas llegan a la cola con `mpn`/`ean` nulos y los agentes no pueden cruzarlas con la ficha oficial. `nas/scrapers/coolmod-enrich.cjs` (servicio `coolmod-enrich`, misma imagen y endurecimiento que `coolmod-scraper`, sin acceso a PostgreSQL) cierra ese hueco:

1. El publicador exporta, solo lectura, `/flow/enrich/coolmod.json` (filas `pending` de coolmod sin MPN) en cada ciclo.
2. El servicio abre cada ficha, una cada vez, con ≥ 2 s de pausa y reutilizando `coolmod.cjs` (JSON-LD Product/Offer, `extractIdentity`): SKU de Coolmod, MPN, precio, stock, vendedor; el EAN sale de `data-flix-ean` (o `gtin*`) y solo se acepta con dígito de control GS1 válido. Si dos fuentes discrepan en MPN/EAN, o la ficha no es el producto pedido, no se escribe nada.
3. Escribe `enriched-*.json` en `/output/coolmod` (lotes de 25) que `sync-agent` ya pone en la cola con `stage_candidates`/`_merge_pending_candidate`: la fila queda con MPN/EAN/SKU y `proposed_product_id` si el MPN/EAN coincide exactamente con un único identificador verificado; el publicador la auto-aprueba con las reglas de siempre. Este servicio nunca aprueba.
4. Estado reanudable en `/output/state/coolmod-enrich-state.json` (reintento: ok 6 h, 404 y sin MPN 7 d, fallo 1 h). Un 403/429/503, una página de verificación o 5 fallos técnicos seguidos paran la pasada (código 3) conservando el progreso y duermen `ENRICH_BLOCKED_SECONDS` (6 h); nunca se esquiva la protección anti-bot.

Automático: tras cada lote del inventario y el siguiente ciclo del publicador (≤ 5 min), `coolmod-enrich` (sondeo cada 15 min) recoge las filas nuevas sin MPN. Manual: `dc run --rm -T --no-deps coolmod-enrich node /work/scrapers/coolmod-enrich.cjs --dry-run --limit 10` (`--limit N`, `--dry-run` no escribe nada, `--input fichero`). Pruebas: `tests/coolmod-enrich.test.ts`, `tests/test_nas.py` (`test_enrichment_fills_mpn_ean_sku…`).

## Marca de pasada por tienda (`shop_check`)

Tras importar los snapshots de una tienda (`sync_agent.ingest_snapshots`), `catalog.publish_shop_check()` publica **un** evento
`shop_check` por tienda (`entity_id` = `coolmod` | `pccomponentes`, operación `upsert`):

```json
{"checked_at": "2026-11-09T21:13:01Z", "unconfirmed_offer_ids": ["pccomponentes-ab12…", "..."]}
```

- `checked_at`: inicio de la pasada (la marca más antigua de los ficheros del snapshot; nunca en el futuro).
- `unconfirmed_offer_ids`: ofertas vivas de la tienda que **no** se confirmaron en la pasada (ausentes, marketplace pendiente de confirmar, rechazadas, lotes fallidos, targets retirados). Conservan su propia fecha.
- No avanza si la pasada confirma < 80 % de las ofertas vivas, si no es posterior a la ya publicada o si han pasado < 30 min desde ella (`SHOP_CHECK_MIN_*` en `nas/catalog.py`).
- El Worker toma como fecha efectiva de una oferta `max(su last_checked_at, checked_at de su tienda)` salvo que esté en `unconfirmed_offer_ids`. Por eso `OFFER_FRESHNESS_SECONDS` baja a 24 h.
- Requiere ampliar el CHECK de `catalog_entities.entity` (`db/postgres-migrations/0005_shop_check.sql`); `publish_shop_check` lo aplica solo (idempotente). **Despliegue: Worker + migración D1 antes de que la NAS emita `shop_check`**; si no, el Worker rechaza el lote (400) y el outbox se acumula.

## Depuración

- `docker compose … logs --tail 50 catalog-publisher` (JSON: `cycle`, `batch_imported`, `batch_invalid`, `decisions_applied`, `auto_approved`, `targets_exported`, `step_failed`).
- `catalog_flow.py status` lista los ficheros no procesados (`PENDIENTE catalog/…`, `decisions/…` con sus errores) y los últimos errores de ciclo.
- Lote «inválido»: `python3 scripts/validate_batch.py <fichero>` muestra el motivo exacto. Si un lote en curso hace que otro parezca duplicado, ambos se omiten hasta resolverlo.
- Una decisión que no avanza: `Verified identifier does not match…` = falta el alias/MPN verificado; `Canonical product absent` = el producto aún no está en ningún lote válido.
- Oferta PcComponentes que no se refresca: `docker logs … pccomponentes-offers | grep refresh_done` muestra `skipped` (`marketplace_seller`, `marketplace_gone_pending_confirmation`, `mpn_mismatch`, `absent_pending_confirmation`, `unavailable_unpublished`), `failed` y `cooldowns`; los SKU fallidos quedan en `retry_first` de `/output/pccomponentes-offers-state/state.json`. Una oferta publicada cuyo SKU no está en `/flow/targets/pccomponentes.json` ni en `data/retailers/pilot.json` nunca se refresca.
- Reimportar a ciegas es seguro: todo es idempotente (borrar `/flow/state/publisher.json` fuerza a reevaluar sin duplicar eventos).
- Prueba aislada: `dc run --rm -T -v "$PWD/nas:/app/nas:ro" -v "$PWD/scripts:/app/scripts:ro" -v "$PWD/tests:/app/tests:ro" -v "$PWD/data:/app/data:ro" -v "$PWD/db:/app/db:ro" sync-agent python tests/test_publisher.py` (esquema PostgreSQL aleatorio; necesita un directorio temporal escribible, p. ej. `-v /tmp/sf-flow-test:/testtmp -e TMPDIR=/testtmp`).
