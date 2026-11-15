# LDLC en local

> Identificadores, políticas de aprobación, cómo consultar fabricantes y flujo de lotes: ver [retailer-review-playbook.md](retailer-review-playbook.md) (guía única, no duplicar aquí).

Rama: `codex/ldlc-local`, desde `main` consolidada en `4def187`. Sin remoto Git configurado. El checkout antiguo `homepage-cache` y el stash experimental se conservan.

## Base aislada

`stockfinder_ldlc_local`, en el PostgreSQL de StockFinder, es una copia completa del catálogo NAS. Ningún agente de sincronización ni réplica apunta a esta base. Los datos de LDLC se escriben aquí y en `artifacts/ldlc/checkpoints/`, fuera de los directorios vigilados por los servicios activos. No se han modificado servicios ni desplegado a Cloudflare/Supabase.

```bash
bash scripts/ldlc_local.sh --watch
# Una categoría, reutilizando las fichas ya descargadas:
bash scripts/ldlc_local.sh --categories gpu
# Volver a consultar precio, stock e identidad:
bash scripts/ldlc_local.sh --refresh
```

El script crea la copia solo si no existe. No la borra ni reemplaza en ejecuciones posteriores. Requiere Docker, la imagen `stockfinder-v2-sync-agent` y el contenedor PostgreSQL del proyecto. La contraseña se pasa por entorno, sin imprimirla ni guardarla.

## Descubrimiento y ofertas

Ocho grupos: gráficas, placas, procesadores, RAM, almacenamiento (SSD y HDD internos), fuentes, cajas y refrigeración (disipadores y watercooling). Se recorren todas las páginas españolas: `ItemList` JSON-LD en la primera y tarjetas `pdt-item` en las restantes, siguiendo `rel=next`. No se usan carruseles de recomendación para ampliar categorías.

Cada ficha se consulta con `curl_cffi`, dejando al menos dos segundos entre peticiones. Solo se admite un `Product` con una oferta EUR, vendedor LDLC, estado nuevo, URL española exacta y stock explícito. Preorder/backorder no se consideran disponibles. Un bloqueo HTTP conserva el progreso; con `--watch` espera y reanuda automáticamente. Un 404/410 de ficha se registra como ausencia sin inventar stock. Una observación comercial inválida se descarta y registra; nunca se convierte en una baja de stock inventada.

Se comparan MPN por marca y EAN con `product_identifiers`. Si ambos identificadores conducen a productos diferentes, no se aprueba. También debe coincidir la categoría del producto canónico. Las coincidencias exactas pasan por la cola de revisión y el importador habitual; las demás permanecen pendientes. No se crean productos canónicos por parecido de nombre ni se atribuye evidencia del fabricante a una ficha de tienda.

Los checkpoints conservan la fecha real de consulta: reanudar no rejuvenece ofertas. `--refresh` consulta nuevamente las fichas. La identidad estable de tienda usa `PB…` de la URL; el código interno `AR…` se conserva en propiedades.

```bash
# Estado de la carga aislada:
docker exec stockfinder-v2-postgres-1 psql -U stockfinder -d stockfinder_ldlc_local \
  -c "SELECT status,count(*) FROM retailer_review_queue WHERE shop_id='ldlc' GROUP BY status"
# Ofertas aceptadas:
docker exec stockfinder-v2-postgres-1 psql -U stockfinder -d stockfinder_ldlc_local \
  -c "SELECT count(*) FROM catalog_entities WHERE entity='offer' AND deleted_at IS NULL AND data->>'shop_id'='ldlc'"
```

## Validación y producción

Las pruebas del parser están en `tests/test_ldlc.py`; la suite NAS comprueba importación idempotente de LDLC y rechazo de dominios falsos. El registro final de cada carga se guarda en `artifacts/ldlc/checkpoints/summary.json`.

La integración queda deliberadamente local: no hay servicio LDLC en Compose ni exportación de targets LDLC en el publicador. Antes de activar producción habrá que revisar los pendientes, comprobar cobertura y frescura, decidir frecuencia y conectar explícitamente descubrimiento/refresco al flujo operativo. Esta rama no activa ninguna de esas tareas.

## Estado de la carga local (14 de noviembre de 2026, 10:28 UTC)

El descubrimiento original terminó las 2.484 fichas a las 00:56 UTC. Sigue activo un único refresco comercial para GPU y placas, que eran las categorías stale: comenzó a las 09:33 UTC y conserva su `refresh_started_at` para reanudar el mismo corte. El último evento de progreso es 400/509 fichas de placa (393 aprobadas, 7 pendientes); los checkpoints individuales siguen escribiéndose cada ocho segundos. No iniciar otro collector ni refresco mientras esté activo. PID del crawler: 482768; PID del importador: 604801.

A las 10:28 UTC la cola tiene 1.849 aprobadas, 564 pendientes y 73 rechazadas; hay 1.849 ofertas LDLC activas y el pipeline no muestra errores activos. Las 564 pendientes tienen una nota explícita `Pendiente:`; ninguna está sin revisar. Estado de pendientes bajo mi ámbito: GPU 1, placas 12, CPU 6, RAM 39 y almacenamiento 15; las demás categorías las llevan los agentes de case/PSU y cooler.

La auditoría SQL repeatable-read confirma ofertas activas = reviews aprobadas, cero ofertas activas sin una review aprobada de la misma URL, cero reviews pendientes/rechazadas que compartan URL activa y cero URLs duplicadas. Las fallas ya resueltas se conservan como `historical_errors`; `errors` contiene solo bloqueos vigentes. El reintento de un lote no borra su error anterior.

Resoluciones recientes: #6270 RTX 3050 WINDFORCE OC V2 8G, con ficha Gigabyte exacta y `G10` guardado como identificador observado de LDLC bajo criterio B; #8614 TRYX PANORAMA SE White, reutilizando el canónico exacto y normalizando solo la capitalización de marca. #6609 y #8678 también se aprobaron con fuentes oficiales exactas. El brand map compartido está en versión 14 y distingue alias de spelling comprobados de marcas distintas.

Retenciones relevantes que se conservan deliberadamente: #6834 Textorm tiene el MPN exacto en su [tabla oficial](https://www.textorm.com/memoires-udimm/), pero la misma tabla publica ese MPN con dos configuraciones incompatibles; no se elige cuál fila es errónea. #7347 WD Green SN350 conserva la diferencia entre MPN con y sin sufijo. No hay alias global Western Digital/SanDisk; #7341–7346 requieren resolver MPN/sufijo y marca por producto. Las placas Gigabyte sin revisión/EAN oficial inequívoca y los SKU AMD con packaging distinto siguen pendientes; no se decide por parecido de nombre.

En cada tienda, `status='pending'` no significa por sí solo “sin revisar”: un `review_note` que empieza por `Pendiente:` registra retención con motivo; nota vacía indica que todavía falta revisión. `approved` y `rejected` son estados separados. Los motivos completos se consultan en `retailer_review_queue` o en `artifacts/ldlc/pending.json`, y los fallos activos/históricos en `artifacts/ldlc/pipeline-status.json`.

## Pipeline incremental

El recolector y el revisor/importador trabajan de forma independiente. Mientras llegan fichas, las coincidencias exactas se incorporan y la revisión con fuentes oficiales resuelve los pendientes por lotes. Los resultados se escriben atómicamente en `artifacts/ldlc/reviews/*-batch-*.json`; el resto de archivos de evidencia no se importan.

`--refresh --watch` inicia una sesión de refresco con un `refresh_started_at` persistido en `run-state.json`. Si HTTP bloquea la sesión, `--watch` conserva el corte y al reanudarse reutiliza inventarios y observaciones comerciales únicamente cuando sus marcas (`collected_at` y `last_checked_at`) son de esa sesión o posteriores. Al completar el refresco, una nueva invocación explícita de `--refresh` crea un corte nuevo; por ello nunca reutiliza cachés de un refresco anterior. Las fechas de observación conservan el momento real de consulta.

```bash
# En procesos separados; ambos acceden exclusivamente a la copia aislada:
bash scripts/ldlc_local.sh --watch
bash scripts/ldlc_local.sh --pipeline --watch
```

El segundo proceso sondea cada 30 segundos, importa productos verificados → alias revisados → decisiones, conserva motivos de retención y actualiza `pending.json`, `catalog.json`, `identifiers.json` y `pipeline-status.json`. Los archivos se identifican por hash para evitar reaplicaciones. Cada decisión usa una transacción: un EAN contradictorio impide registrar su alias y su oferta. Los retries controlados usan un nuevo archivo con las mismas decisiones verificadas; la cola aprobada, los identificadores únicos y las ofertas con claves estables hacen que el reintento sea idempotente. El estado separa errores activos de históricos. Los productores deben escribir batches atómicamente con permisos de lectura `0644` antes de renombrarlos; `mkstemp` deja archivos `0600` por defecto. Si un batch no se puede leer, el pipeline registra el error en stdout, sigue con los demás y vuelve a intentarlo en el siguiente ciclo.

Las altas requieren contrato válido, URL oficial, evidencia explícita, fecha de verificación y ausencia de duplicados en el catálogo local. Fuera de GPU/placas/cajas/refrigeración, se exige coincidencia literal con MPN del fabricante y nunca alias por criterio B. La ampliación autorizada a cajas/refrigeración el 14 de noviembre exige evidencia de variante completa y EAN sin contradicciones, según docs/catalog.md. Los subdominios oficiales de un mismo dominio registrable son equivalentes a efectos de procedencia; no se admiten dominios imitadores.

La revisión IA es trabajo de agentes de esta conversación; el importador no inventa verificaciones ni genera decisiones por sí solo. Los resultados quedan fuera de `data/` para que el publicador activo no los lleve a producción.

Tras un bloqueo, el recolector aumenta la separación entre peticiones (4, 8 y hasta 12 segundos), conserva la respuesta HTTP fallida para diagnóstico y reanuda tras la espera. La comprobación y la revisión de lo ya descargado continúan mientras la tienda no responde.


Política de revisión vigente: el 14 de noviembre de 2026 el usuario autorizó criterio B para cajas y refrigeración en estas bases aisladas, con evidencia oficial de variante completa, procedencia de códigos de tienda explícita y sin contradicción de EAN. Los batches no basados en MPN exacto necesitan `variant_evidence`; véase [catálogo](catalog.md). CPU, RAM, almacenamiento y fuentes conservan identidad por MPN oficial exacto. No cambia la exclusión de producción.


## Revision del 15 de noviembre de 2026

Tres agentes Luna: aprobadas #7225, #8053 y #8054; cola final 2.193 aprobadas, 225 pendientes, 73 rechazadas. Cero errores activos; 85 pruebas correctas e integridad de ofertas comprobada. Informe y evidencia: `artifacts/ldlc/audit-20261115/report.md` e `integrity-tests.json`. Las fechas comerciales se conservan y no se publica a produccion.


## Cierre integral iniciado el 15 de noviembre de 2026

Objetivo activo: resolver todas las pendientes con evidencia individual y tres agentes Luna; no publicar. Estado inicial: 2.193 aprobadas, 225 pendientes y 73 rechazadas. Backup aislado y seguimiento en `artifacts/ldlc/completion-20261115/`.

El pipeline admite `action: reject` solo con nota detallada y `rejection_evidence` (`reason_code`: `identity_conflict`, `unsupported_bundle` u `out_of_scope`; `source_url` oficial citado en la nota; `observed` identificando MPN/EAN/nombre literal; `official` con hallazgo distinto). La ausencia de una fuente no justifica por si sola un rechazo. Rechazar una aprobada o varios fingerprints pendientes del mismo SKU esta bloqueado.

Un producto existente puede revalidarse con `verification_review_id` de su aprobacion pendiente y evidencia primaria nueva; su nombre, marca, MPN y manufacturer_url no pueden cambiar en ese refresco. La relacion Viper Gaming/Patriot se acredita por revision de RAM con MPN exacto, `brand_evidence` y fuente Patriot; no se crea alias global de marcas.
