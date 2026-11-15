# PcComponentes

## Acceso

La web y su API JSON están detrás de Cloudflare Bot Management. `curl`, `urllib`,
`fetch` y clientes sin huella TLS de navegador reciben `403 Just a moment`.
`curl_cffi` con `impersonate="chrome"` obtiene `200` sin resolver captchas ni
falsificar cookies. Es la única dependencia añadida (`nas/requirements.txt`).

La API es interna y no documentada. `robots.txt` prohíbe expresamente
`/api/articles/search`; los scripts no la usan. `/api/articles/list-by-ids` y
`/api/articles/category/{uuid}` no aparecen en `robots.txt`.

Límites observados: **40 IDs por llamada**, **40 artículos por página** y caché
de borde de **120 s**. Espaciar peticiones 0,6–1 s.

## Endpoints útiles

| Ruta | Uso |
| --- | --- |
| `GET /api/articles/list-by-ids?productIds=...&sort=relevance&page=1&pageSize=40` | Precio, stock, MPN, oferta y vendedor de un conjunto conocido |
| `GET /api/articles/category/{uuid}?sort=relevance&page=N&pageSize=40` | Inventario por categoría; incluye `topAttributes` |
| `GET /api/articles/{id}/prices` | Histórico de precios |
| PDP (`/slug`) | EAN, referencia, `purchasable`, stock real, specs, imágenes y descripción |

Los UUID de categoría están en `data/retailers/pccomponentes-categories.json`.
El script los resuelve y cachea si se pasa un slug nuevo.

## Disponibilidad

`availabilityCode`: 1 `IN_STOCK`, 2 `INMINENT_REPLENISHMENT`, 3
`HIGH_AVAILABILITY`, 4 `OUT_OF_STOCK`, 5 `PRESALE`, 6 `CONSULT`, 7
`DISCONTINUED`, 9876 `ZONE_NOT_AVAILABLE`, 9999 `FORCE_DISCONTINUED`, 10000
`PRE_ORDER`.

Solo 1 y 3 cuentan como stock. El campo `stock` de la API (`null`/`1`) no es un
booleano. `stock: null` no significa agotado. El vendedor se lee de
`sellerName`/`isPCC`; las ofertas marketplace se omiten por defecto.

## Scripts

Actualizar un conjunto ya aprobado:

```bash
python3 scripts/pccomponentes_batch.py \
  --targets data/retailers/pccomponentes-targets.json \
  --output artifacts/pccomponentes-offers.json
```

Descubrir referencias nuevas por categoría:

```bash
python3 scripts/pccomponentes_discover.py \
  --categories procesadores tarjetas-graficas placas-base memorias-ram fuentes-alimentacion \
  --pages 2 --output artifacts/pccomponentes-discovery.json
```

Añadir `--details --details-limit 50` para leer EAN, stock real y specs del PDP
solo en las referencias seleccionadas. `--include-known` desactiva la
deduplicación por SKU/MPN.

Los scripts se ejecutan en el host con `curl_cffi` instalado o dentro de la
imagen NAS tras reconstruirla:

```bash
dc build sync-agent
dc run --rm -T -v "$PWD/artifacts:/out" sync-agent \
  python scripts/pccomponentes_discover.py --categories tarjetas-graficas --pages 1 --output /out/pcc.json
```

## Flujo

1. `pccomponentes_discover.py` genera candidatos (máximo 190 por lote) sin
   publicar nada.
2. `review_queue.py stage --file ...` los encola; la aprobación exige
   `product_id` y evidencia MPN/EAN.
3. Exportar solo los aprobados de esta tienda:
   `review_queue.py targets --shop pccomponentes > data/retailers/approved-pccomponentes-targets.json`.
4. `pccomponentes_batch.py` produce ofertas para `import_offers.py` con MPN
   exacto, vendedor directo y precio EUR.
5. Importar: `python nas/import_offers.py artifacts/pccomponentes-offers.json`
   dentro del contenedor NAS.
6. `--details` solo para el subconjunto que se vaya a aprobar: una petición de
   ~800 KB por ficha.

## Escala

Las 12 categorías principales suman ~20 000 referencias vivas (RAM ~4 000,
monitores ~2 600, cajas ~2 300, discos ~2 000). A 40 por página son ~500
peticiones de inventario; el cuello de botella es el PDP, una petición por
producto. El lote de ejemplo
`artifacts/pccomponentes-discovery-2026-11-02.json` contiene 191 referencias
nuevas con MPN y vendedor directo.

## Política de cobertura (2026-11-08)

Vale para PcComponentes y Coolmod (`scripts/pccomponentes_rules.py::classify`):

| Categoría | Entra | Sale |
|---|---|---|
| CPU | Ryzen 7000/8000/9000 (AM5), Ryzen 1000–5000 de sobremesa (AM4, decisión 2026-11-09), Core 13.ª/14.ª gen (LGA1700), Core Ultra 200S (LGA1851) | LGA1200 y ≤12.ª gen, portátil (H/HS/HX/U), tray, Xeon/EPYC/Threadripper |
| GPU | NVIDIA RTX 50, AMD RX 9000, Intel Arc serie A y B (A desde 2026-11-09) | RTX 40/30, GTX, RX 7000/6000, profesionales |
| Almacenamiento | SSD SATA, SSD NVMe (cualquier PCIe, incluido 3.0/Gen3) y HDD internos | USB/externos, SAS/IDE, cinta, (ninguno por generación) |
| Fuentes | Todas las ATX de escritorio ≥ 500 W (ATX 3.x y anteriores; el filtro web es `Estándar ATX`) | < 500 W, SAI, adaptadores |
| Placas, RAM, cajas, disipadores | sin cambios | sin cambios |

Se mantienen fuera: fuentes < 500 W, Intel Arc serie A y Core 5/7 sin «Ultra» (decisión del propietario, 2026-11-08).

Los productos ya publicados de generaciones anteriores no se tocan: la política solo decide qué se descubre.
Tipos de almacenamiento: `Tipo` (SSD/HDD), `Interfaz`, `Formato`; HDD añade `Velocidad de rotación` y `Caché`.

Carga del descubrimiento completo (servicio `pccomponentes-discovery`: `--pages 40`, `--max-pending 3000`,
`--delay 0.6`):

- Hasta 40 páginas × 40 artículos por categoría y 9 categorías = ≤ 360 peticiones a
  `/api/articles/category/{uuid}` por pasada (las categorías pequeñas terminan antes: procesadores ~9 páginas,
  tarjetas ~12, discos ~50, RAM/cajas/fuentes se cortan a 40). El worker espera `--delay` entre páginas y cada
  petición tarda 0,3–1 s, de modo que la cadencia efectiva es ≤ 1 petición/s; una pasada dura ~6–10 min cada 12 h.
- Cada pasada emite como máximo `--max-candidates` (190) nuevos; las siguientes continúan donde la anterior
  (SKU/MPN vistos en el estado). Para vaciar el listado de una vez usa
  `catalog_flow.py discover --backfill … --pages 40` (≥ 1 s entre páginas, nunca `/api/articles/search`).
- Coolmod: `coolmod-inventory` recorre cada listado en Playwright con 2 s entre páginas (≈ 130 páginas en total;
  discos SSD + discos HDD incluidos) y reparte los candidatos en lotes de 190 cada 15 min.

## Límites de uso

Los datos cambian y la caché de 120 s puede devolver precios anteriores. No
publicar automáticamente: el proyecto exige identidad exacta y revisión. No usar
`/api/articles/search` por `robots.txt`. Mantener una petición por segundo y no
reutilizar la sesión para otros fines.
