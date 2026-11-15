# Supabase: configurador, listas compartidas y ranking

El catálogo público general continúa en D1. El configurador y /ofertas usan una réplica independiente del PostgreSQL del NAS en Supabase. Sus consultas, incluida la cabecera y los favoritos, no leen D1. La portada obtiene sus ocho bajadas del mismo ranking.

## Datos y sincronización

El servicio supabase-replica lee catalog_outbox por event_id y aplica versiones idempotentes en Supabase. Mantiene su propio cursor en catalog_replica_checkpoints; no modifica acknowledged_at ni el cursor de D1. Replica categorías, tiendas, productos, precios, disponibilidad y marcas de comprobación por tienda. Sondeos cada 15 minutos; tras alcanzar la cabecera del outbox recalcula el top 20 general y top 10 por categoría.

El ranking contiene hasta 50 productos ?nicos, ordenados por porcentaje de la ?ltima bajada registrada en 24 horas respecto al precio inmediatamente anterior de esa tienda. Exige que la oferta siga en stock, tenga el mejor precio actual del producto y se haya comprobado en 24 horas. Descarta cambios superiores al 70 %. Cada ciclo vuelve a comprobar precio, stock y visibilidad; no conserva un precio rebajado que ya haya cambiado. La cach? puede añadir hasta 60 segundos. Con réplica retrasada más de 45 minutos la página deja de presentar el ranking como actual.

## Acceso y permisos

El Worker llama por HTTPS a la ?nica función expuesta stockfinder_features.rpc. No carga postgres.js ni abre conexiones Postgres para estas funciones. FEATURES_KEY se guarda como secret de Cloudflare, nunca en el navegador. La Data API expone ?nicamente stockfinder_features; anon y authenticated no tienen acceso. service_role solo recibe uso del esquema y ejecución de esa función, sin acceso directo a sus tablas ni al catálogo privado.

La réplica usa stockfinder_replica, con permiso para ejecutar las funciones de sincronización y recálculo, sin lectura directa de tablas. Su cadena est? en .env.supabase-replica.local (0600, ignorado por git). El token de Management API no entra en el contenedor.

## Operación

Desde la raíz del proyecto:

```bash
python3 scripts/setup_supabase_features.py
# Arrancar ?nicamente el servicio de réplica:
docker compose --env-file .env -f infra/compose/compose.yaml --profile supabase up -d --no-deps supabase-replica
# Estado y ?ltimo punto aplicado:
docker compose --env-file .env -f infra/compose/compose.yaml logs --tail 10 supabase-replica
```

El setup aplica migraciones y configura la fachada; guarda la clave de servidor en .env.supabase-features.local sin imprimirla. No ejecutar data-api-off: desactivaría estas funciones. La comprobación genérica de Supabase sigue verificando el esquema privado stockfinder; el esquema expuesto esperado es ahora stockfinder_features.

POST /api/v1/builds y GET /api/v1/builds/:uuid persisten en Supabase. Las listas privadas y capturas del configurador siguen en localStorage; solo se suben al pulsar Compartir. Las listas compartidas validan sus piezas contra la réplica y tienen límite por IP con hash. Antes de un corte desde D1 se copian las listas compartidas existentes sin borrar el origen.

## Validación y recuperación

npm test comprueba también las rutas con una D1 sin tablas: cualquier lectura accidental impediría que funcionasen. npm run test:pg ejecuta las funciones reales contra PostgreSQL desechable. tests/features-browser.cjs bloquea las API respaldadas por D1 mientras navega por ofertas y configurador.

Ante una caída de Supabase se muestra un error o aviso de réplica atrasada; no hay fallback silencioso a D1 para estas páginas. Revisar logs, conectividad y permisos; el cursor se reanuda y los reintentos son idempotentes. No borrar el cursor ni marcar el outbox como confirmado. Para rollback del Worker, anotar primero la versión activa y restaurarla con scripts/cloudflare.py; conservar Supabase y el cursor para recuperar la versión actual. El catálogo D1 y su sincronización continúan independientes.
