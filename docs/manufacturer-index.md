# Referencias de fabricante: MPN, listados y CSV

Cómo se consigue un código de fabricante fiable y dónde queda guardada cada prueba. Complementa
[retailer-review-playbook.md](retailer-review-playbook.md) (reglas de revisión) y
[catalog.md](catalog.md) (vocabulario del catálogo). No repetir aquí ninguna de las dos.

## 1. Qué hay ahora

| Pieza | Ruta | Qué hace |
|---|---|---|
| Índice unificado | `scripts/manufacturer_index.py` | Junta en un CSV+JSON todos los MPN/EAN/modelos publicados por los fabricantes que ya están en el proyecto, con su URL, su fichero y su hash |
| Diferencia con la cola | `scripts/manufacturer_diff.py` | Compara el índice con las pendientes de LDLC y Neobyte y dice qué filas ya tienen respaldo oficial y dónde falta buscarlo |
| Rastreo ASUS | `scripts/scrape_asus.py` | Listado por sitemap español y ficha `/techspec/` de GPU, placas, fuentes, cajas, refrigeración y discos |
| Rastreo ASRock | `scripts/scrape_asrock.py` | Listado por `sitemap.xml` y ficha de producto (espejo `tw.asrock.com`) de GPU, placas, fuentes, refrigeración y mini PC |
| Motor común | `scripts/manufacturer_scrape.py` | Transporte, caché, parsers y vocabulario de especificaciones por categoría |
| Corredor en Docker | `infra/compose/manufacturer-scrape.yaml` | Ejecuta los rastreadores con `curl_cffi` (solo existe en la imagen) guardando el resultado en `work/` |

Salida generada (no versionada, vive en `work/`):

- `work/manufacturer-index/manufacturer-index-<fecha>.csv|.json` (+ `latest.*`)
- `work/manufacturer-diff/manufacturer-diff-<fecha>.json|.md` (+ `latest.*`)
- `work/manufacturer/manufacturer-<marca>-<categoría>.json` (salida de cada rastreo)
- `work/manufacturer-cache/` (caché de páginas, 24 h por defecto)

## 2. Cómo se ejecuta

```bash
# Índice (solo biblioteca estándar; no necesita la imagen Docker)
python3 scripts/manufacturer_index.py \
  --csv artifacts/ldlc/audit-20261115/seasonic-rma-example-import.csv

# Diferencia contra la cola pendiente
python3 scripts/manufacturer_diff.py

# Rastreo (con curl_cffi, dentro de la imagen)
docker compose -f infra/compose/manufacturer-scrape.yaml run --rm scraper \
  python /work/scripts/scrape_asus.py --category psu
docker compose -f infra/compose/manufacturer-scrape.yaml run --rm scraper \
  python /work/scripts/scrape_asrock.py --category motherboard

# Rastreo en el host (sin curl_cffi: el registro dice transport=urllib)
python3 scripts/scrape_asus.py --category gpu --no-store
```

Sin `curl_cffi` el módulo cae a `urllib` con las mismas cabeceras de navegador y **anota el
transporte usado en cada registro**; nunca se presenta una lectura de `urllib` como si fuera de
huella de navegador.

## 3. Dónde está el listado de cada marca (verificado el 15-nov-2026)

| Marca | Listado | Qué da |
|---|---|---|
| ASUS | `https://www.asus.com/sitemap.xml` → `https://www.asus.com/es/sitemap/es1.xml`…`es14.xml` | ~480 URLs por sitemap; las fichas de producto tienen su `/techspec/`. Secciones: `graphics-cards`, `motherboards`, `power-supply-units`, `cases`, `cooling`, `data-storage` |
| ASUS eShop | `https://estore.asus.com/es/sitemap.xml` (DataDome, hay que usar el lector) | Precio, EAN y disponibilidad; se cruza con la ficha por MPN exacto |
| ASRock | `https://www.asrock.com/sitemap.xml` | 647 fichas: MB 373, GPU 144, Nettop 43, PSU 41, CPU-Coolers 14, Monitors 7. El HTML es el mismo en `tw.asrock.com` (www está con Incapsula) |
| ASRock fichas | `download.asrock.com/Manual/...` | El índice de carpeta responde 403, pero las rutas concretas funcionan (QIG por serie, declaraciones CE, datasheets) |
| MSI | `https://es-store.msi.com/products.json?limit=250&page=N` (JSON de Shopify) | `variants[].sku` = MPN oficial. También `pl-store`, `fr-store`, `de-store` |
| Seasonic | `https://rma.seasonic.com/example_import.csv` | CSV oficial: serie, nombre de modelo y **número de modelo** de toda la gama |

Objetivo del listado: enumerar la gama completa del fabricante. Es lo que permite saber que una
variante **no existe** (y por tanto no puede aprobarse) y no solo que falta la página de una que sí
existe.

## 4. CSV de fabricantes

El único CSV real de fabricante que hay en el proyecto es el de Seasonic
(`artifacts/ldlc/audit-20261115/seasonic-rma-example-import.csv`, 19.634 bytes, sha256
`f161e6ffbdb6f242cc7eac9d4124d1da59aea732e0c6ed68a9b70f75d0672823`). Son 228 filas de producto
(`RECOGNIZED RETAIL MODELS`: serie, modelo y número de modelo) más el bloque de compañía de la
plantilla, que el indexador marca para revisión manual.

El indexador admite dos formatos de CSV:

1. **Plantilla RMA de Seasonic**: se detecta la cabecera `RECOGNIZED RETAIL MODELS` y se leen
   serie, modelo y número de modelo.
2. **CSV genérico con cabecera**: se decide cada columna por su nombre (`brand`, `category`, `mpn` /
   `part number` / `model number`, `ean`, `model`, `series`, `url`, `notes`). Si no hay columna de
   MPN, el fichero se ignora.

Para añadir el CSV de otro fabricante:

```bash
python3 scripts/manufacturer_index.py --csv ruta/al/listing.csv --csv-brand Marca
```

## 5. Cómo se lee el índice

Columnas: `brand`, `category`, `mpn`, `ean`, `model`, `series`, `mpn_source`, `source`,
`source_url`, `local_path`, `sha256`, `transport`, `checked_at`, `notes`.

- `mpn_source`: `manufacturer` (lo publica el fabricante), `store` (tienda oficial del fabricante),
  `retailer` (código comercial de una tienda). Solo el primero vale como identidad.
- `source`: `catalog`, `catalog-alias`, `scraper:<fichero>`, `store:<tienda>`, `evidence`,
  `csv:<fichero>`.
- `sha256`: hash de la captura oficial que respalda la fila, cuando existe.

El índice **no decide identidad**. Solo dice qué documento publica qué código. La coincidencia por
nombre no sirve para fusionar productos (ver `catalog.md`).

## 6. Diferencia con la cola pendiente

`scripts/manufacturer_diff.py` clasifica cada fila pendiente:

| Estado | Significado |
|---|---|
| `mpn` | El MPN exacto de la tienda está en el índice, con la misma marca |
| `mpn_sufijo_regional` | Coincide tras quitar el sufijo `-EU`/`-BEU`/`-EEU`/`_EU` (política del 14-nov-2026) |
| `mpn_otra_marca` | El mismo MPN lo publica otra marca: conflicto, nunca identidad |
| `sin_mpn` | La tienda no imprime ningún código |
| `sin_respaldo` | No hay ninguna fuente oficial para ese código en el índice |

El informe Markdown (`work/manufacturer-diff/latest.md`) lista las filas sin respaldo con la marca,
el código observado, el EAN y **dónde mirar** (el listado oficial de esa marca), más una *pista*
cuando el índice tiene una fila con el mismo nombre de modelo. La pista es un aviso para buscar, no
una prueba.

## 7. Bloqueos conocidos

- `curl_cffi` solo está en la imagen Docker; en el host el rastreo usa `urllib` (sirve para
  listados y fichas simples, no para WAF).
- `seasonic.com` y `corsair.com` responden 403 a una petición normal: hay que usar el lector o el
  navegador.
- `gskill.com` prohíbe agentes en `robots.txt`: no rastrear en masa.
- ASRock responde con reto de Incapsula en `www`; el espejo `tw.asrock.com` sirve la misma página.
- Varias marcas devuelven 404 en la ficha que la tienda anuncia (Cooler Master, Lian Li,
  Thermaltake, Antec, Zalman, Chieftec…): es un bloqueo de identidad, no un fallo de red.
- Los fabricantes que solo publican el **nombre de modelo** y ningún código no sirven como MPN:
  el registro queda con `mpn` vacío y el nombre en `model`.

## 8. Reglas de higiene

- ≥ 2 s entre peticiones al mismo host; la caché evita repetir la misma página 24 h.
- Nada de CAPTCHA, ni sesión falsificada, ni respuestas inventadas: lo que no se lee queda como error
  en el informe, nunca como dato.
- Guardar documento, hash y metadatos cuando el documento lo acredita (ver `catalog.md`).
- Los rastreadores no escriben en el catálogo ni en la cola: solo producen referencias.
