# Estado actual de StockFinder

Documento vigente; sustituir el contenido al revisar, sin acumular historial.

Versión activa comprobada: 01696486-4429-495a-a973-28fcf7e93691. Validación: 120 pruebas correctas y 4 omitidas en la suite general; 50 correctas y 1 omitida contra PostgreSQL desechable. Typecheck, build y navegación móvil correctos.

- Catálogo general: React/Vite + Worker/D1. PostgreSQL del NAS sigue siendo la fuente de verdad.
- Configurador y /ofertas: réplica en Supabase, RPC por HTTPS y cero consultas D1. La réplica se actualiza cada 15 minutos, con cursor independiente. Ver [Supabase](supabase-migration.md).
- /ofertas muestra hasta 20 bajadas porcentuales verificadas de 24 horas, con precios actuales. La portada muestra ocho y enlaza ?Mostrar más ofertas?.
- Selector del configurador: una página de 48 piezas por apertura, búsqueda progresiva y compatibilidad aplicada antes de paginar. Plan de compra por tiendas y destino, con gastos desconocidos explícitos.
- Favoritos y avisos en la cabecera, con desplegable y opción de ocultar desde Apariencia. Se conservan localmente y se comprueban al abrir la web; ocultarlos detiene sus consultas.
- Caché comercial por categoría; el cliente evita reescribir el JSON cacheado para calcular antigüedad. La caché frontal global permanece desactivada para no convertir las imágenes estáticas en peticiones facturables de Worker.
- Duplicados de las 20 imágenes ASRock resueltos conservando las versiones oficiales y sus hashes verificados.

- PDF del configurador con presupuesto destacado, tarjetas con fotos y revisión técnica paginada. Plan de compra compacto con opciones seleccionables, piezas y gastos desplegables.

## Comprobar antes de continuar

python3 scripts/catalog_flow.py status muestra cola, reintentos y backlog vivos. python3 scripts/worker_metrics.py --minutes 60 muestra CPU real en Workers Analytics; los logs request_sample muestran ruta normalizada, caché y tiempo de pared, que no es CPU.

Quedan decisiones retenidas por identidad, especificaciones pendientes de evidencia y seis ofertas antiguas que requieren revisión exacta. No forzar alias ni actualizar fechas sin comprobar. Las estimaciones de almacenamiento no son correcciones aplicadas. Backups externos y ensayo de restauración pendientes de verificar.

Preservar cambios de datos ajenos a esta revisión. Solo servicios StockFinder y credenciales ignoradas con permisos 0600. Reglas: [catálogo](catalog.md), [flujo](catalog-flow.md), [operación](runbooks/operations.md).

## LDLC: trabajo local aislado

La rama `codex/ldlc-local` añade descubrimiento por categorías y ofertas LDLC en una copia completa `stockfinder_ldlc_local`. No conectar esta base al sync-agent ni a Supabase. Ejecución y validación: [LDLC local](ldlc-local.md). Los checkpoints están en `artifacts/ldlc/checkpoints/`; las referencias sin identidad exacta quedan en la cola local.

## Neobyte: trabajo local aislado

Copia completa `stockfinder_neobyte_local`, independiente de la de LDLC. No conectar al sync-agent ni a Supabase; el publicador no exporta Neobyte. Ejecución y reglas: [Neobyte local](neobyte-local.md). Checkpoints en `artifacts/neobyte/checkpoints/`.

Revisión local LDLC/Neobyte del 14 de noviembre: pipelines aislados activos; no publicar ni conectar a producción. Estado operativo, retenciones con motivo y conteos repetibles en [LDLC local](ldlc-local.md) y [Neobyte local](neobyte-local.md); los archivos vivos son `artifacts/{ldlc,neobyte}/pipeline-status.json`. Pruebas consolidadas: 63 correctas contra PostgreSQL 17 temporal, eliminado al terminar. `git diff --check` limpio. Comprueba que una pendiente con nota `Pendiente:` es retención revisada y una pendiente sin nota aún no ha sido revisada; no borres `historical_errors` ni las evidencias.
