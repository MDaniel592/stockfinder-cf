---
description: Verifica la cola de revisión con agentes de IA (un agente por shard) y publica el resultado
argument-hint: "[--category psu storage ...] [--shop coolmod|pccomponentes] [--prefix NOMBRE]"
---

Ejecuta la verificación asistida por IA del flujo de catálogo de StockFinder. Contexto completo: `docs/catalog-flow.md`.

1. Estado de partida: `python3 scripts/catalog_flow.py status`. Si el publicador está pausado o con errores, díselo al usuario antes de seguir.
2. Genera los shards (observaciones pendientes sin revisar, ≤60 por shard, agrupadas por categoría y marca) con los argumentos recibidos (`$ARGUMENTS`):
   `python3 scripts/catalog_flow.py shards $ARGUMENTS`
   Usa `--dry-run` primero si dudas del volumen. Se escribe `artifacts/shards/<prefijo>-<categoría>-<n>.json` y `<prefijo>-index.json`. Para re-revisar retenidas («Pendiente: …») añade `--include-retained`.
3. Lanza UN agente por shard (en paralelo, en tandas de 4-6 si son muchos) con este encargo, sustituyendo `<shard>`:
   «Sigue `docs/catalog-shard-protocol.md` para el shard `<shard>` (`artifacts/shards/<shard>.json`). Fecha de hoy: <AAAA-MM-DD>. Entrega lote de productos, alias y decisiones con los nombres del protocolo y valida con `scripts/validate_batch.py`.»
   Los agentes NO usan docker ni la base de datos: solo escriben `data/catalog/reviewed-batch-*`, `data/aliases/reviewed-aliases-*` y `artifacts/decisions-*`.
4. Espera a que terminen. El publicador (`catalog-publisher`) ya va importando lo que escriben cada pocos minutos; para no esperar al siguiente ciclo y ver el resultado:
   `python3 scripts/catalog_flow.py validate` (todos los lotes, 0 inválidos) y `python3 scripts/catalog_flow.py publish --once`.
5. Revisa el informe del ciclo: productos importados, alias, decisiones aprobadas/rechazadas/reintentos, auto-aprobadas y targets exportados. Las decisiones con «reintento» se resuelven solas cuando aparezca el producto o alias que falta; si persisten, revisa `python3 scripts/catalog_flow.py status`.
6. Resume al usuario: shards lanzados, informes de los agentes (nuevos, alias, aprobadas, rechazadas, pendientes y motivos) y el estado final de la cola.

No uses `docker compose up/restart/build`, no edites `data/catalog/` a mano ni apruebes nada por parecido de nombre.
