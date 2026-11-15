# Neobyte en local

> Identificadores, políticas de aprobación, cómo consultar fabricantes y flujo de lotes: ver [retailer-review-playbook.md](retailer-review-playbook.md) (guía única, no duplicar aquí).

Misma estrategia que [LDLC local](ldlc-local.md), en una base propia para poder trabajar en paralelo sin tocar la de LDLC.

## Base aislada

`stockfinder_neobyte_local`: copia completa del catálogo NAS (el script la crea solo si no existe y nunca la borra). Ningún sync-agent, réplica ni publicador apunta a ella. Checkpoints en `artifacts/neobyte/checkpoints/`. El cargador rechaza cualquier otra base (`tests/test_neobyte.py`). **No se ha desplegado nada a Cloudflare/Supabase ni hay servicio Neobyte en Compose.**

```bash
bash scripts/neobyte_local.sh --watch              # todas las categorías
bash scripts/neobyte_local.sh --categories gpu     # una categoría (reutiliza fichas guardadas)
bash scripts/neobyte_local.sh --refresh            # reconsultar precio y stock
bash scripts/neobyte_local.sh --pipeline --watch   # aplicar lotes revisados y actualizar estado
```

## Fuente de datos

Neobyte (PrestaShop) está tras Cloudflare: `curl` simple recibe el desafío, `curl_cffi` con `impersonate='chrome'` responde 200. Se respeta `robots.txt` (solo URLs de categoría `/<slug>-<id>` paginadas con `?page=N` y fichas `.html`).

- Listados: JSON-LD `ItemList` (32 por página) y `rel=next`. No se usan las tarjetas HTML porque incluyen carruseles/PC montados.
- Ficha: JSON-LD `Product` (dentro de `@graph`) con `mpn`, `gtin13`, `brand` y una `Offer` EUR, vendedor `Neobyte`, estado nuevo, URL idéntica a la de la ficha y disponibilidad explícita. Cualquier otra cosa se descarta y se registra.
- Identidad de tienda (`retailer_sku`): el ID numérico final de la URL (`…-28548.html` → `28548`); la referencia interna (`TG01AS513`) queda en propiedades.
- Categorías (IDs de Neobyte): gpu 111, placas 106, CPU 107, RAM 108, almacenamiento 144/142/143, fuentes 113, cajas 153, refrigeración 138/139.

Mismas reglas de LDLC: ≥2 s entre peticiones (4/8/12 s tras bloqueo), espera mínima de 15 min tras 403/429/503 o desafío, coincidencia solo por MPN por marca o EAN exactos y misma categoría; lo demás queda pendiente en `retailer_review_queue`. No se crean productos por parecido de nombre.

## Cambios compartidos

Una línea cada uno para registrar la tienda: `DOMAINS` en `nas/import_offers.py`, `SHOPS` en `nas/import_catalog.py` y `--shop` en `nas/review_queue.py`. `nas/publisher.py` sigue con `SHOPS = ('coolmod','pccomponentes')`, así que Neobyte no se exporta a producción.

## Estado local (14 de noviembre de 2026, 10:28 UTC)

El pipeline usa exclusivamente `stockfinder_neobyte_local`; un único watcher activo importa los lotes revisados y no hay publicador/sync-agent conectado a esta copia. A las 10:28 UTC el estado registra 2.246 revisiones aprobadas, 1.217 pendientes, 36 rechazadas y `errors: []`; hay 2.246 ofertas activas. Auditoría SQL repeatable-read: cero ofertas sin review aprobada de la misma URL, cero reviews pendientes/rechazadas con URL activa y cero URLs activas duplicadas.

De las pendientes, 45 tienen nota explícita `Pendiente:` y 1.172 no tienen nota/revisión todavía. Las notas y candidatos están en `artifacts/neobyte/pending.json`; el estado más reciente está en `artifacts/neobyte/pipeline-status.json`. Una retención documentada no equivale a una identidad resuelta: se revisa de nuevo cuando aparece una fuente oficial adicional.

La revisión HDD Neobyte asignada a Seagate, Toshiba y Synology ya no tiene referencias HDD pendientes. El lote `resume-hdd-seagate-toshiba-synology-batch-001.json` verificó 20 MPN con fichas oficiales. Las cuatro referencias Synology que aún aparecen bajo `storage` son unidades SSD y pertenecen a la revisión de RAM/SSD del agente core, no a HDD.

El pipeline aprobó #7937 TooQ con MPN/EAN exactos y ficha oficial; la diferencia `Tooq`/`TooQ` se normaliza solo como capitalización. También se aprobaron #9553 Fractal y #9421/#9429/#9705/#9706 TRYX tras verificar fuentes oficiales y reutilizar canónicos exactos; el brand map compartido está en versión 14. La review #6891 Gigabyte H610M D3W DDR4 se reintentó con la URL exacta de Neobyte y quedó aprobada. No se añaden alias WD/SanDisk ni alias difusos.

Para distinguir estados, `status='pending'` con `review_note` que empieza por `Pendiente:` es una retención explícita con motivo; `pending` sin nota aún no se ha revisado. `approved` y `rejected` se cuentan aparte. Los errores antiguos solo pasan a históricos si la revisión terminó aprobada/rechazada o tiene una retención explícita; siguen visibles en el estado histórico.

El crawler original ya terminó. Antes de iniciar otro, confirma que no existe un collector de Neobyte activo y respeta `run-state.json`, cooldown y espera de 15 minutos tras bloqueos/desafíos. No eludir CAPTCHA ni lanzar watchers duplicados.


Política de revisión vigente: el 14 de noviembre de 2026 el usuario autorizó criterio B para cajas y refrigeración en estas bases aisladas, con evidencia oficial de variante completa, procedencia de códigos de tienda explícita y sin contradicción de EAN. Los batches no basados en MPN exacto necesitan `variant_evidence`; véase [catálogo](catalog.md). CPU, RAM, almacenamiento y fuentes conservan identidad por MPN oficial exacto. No cambia la exclusión de producción.
