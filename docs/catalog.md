# Catálogo: datos y revisión

> Identificadores, políticas de aprobación, cómo consultar fabricantes y flujo de lotes: ver [retailer-review-playbook.md](retailer-review-playbook.md) (guía única, no duplicar aquí).

## Dónde está cada dato

- `data/catalog/*.json`: productos canónicos, especificaciones, `manufacturer_url` y fecha de verificación. No acredita venta ni stock.
- `data/product-identifiers.json`: alias comerciales con fuente y explicación; no atribuirlos al fabricante.
- `data/retailers/*candidates.json`: observaciones externas sin aprobación implícita.
- `artifacts/review-queue.json`: cola completa, decisiones y evidencia; `docs/review-queue.md` es solo el resumen.
- `data/images/*.json`: procedencia y alcance de imágenes; `docs/image-assets.json`: ilustraciones generadas. Conservar atribuciones, alcance modelo/familia y fallback; no asumir licencia abierta.
- Tamaños de imagen en la web: los originales generados están en `apps/web/image-src/` (no se publican); `apps/web/public/images/placeholders/<categoría>-{160,400,640}.{avif,webp}` son los sustitutos 4:3 que se sirven y `images/og-card.jpg` (1200×630) la tarjeta social. Tras añadir o borrar fotos en `images/products/` ejecutar `python3 scripts/make_thumbnails.py` (miniaturas WebP de 160 px en `images/thumbs/` para filas de listas; si falta una, la web usa la foto completa). Fotos nuevas: máximo 800 px y ≲150 KB.
- Imágenes de tienda (alternativa, 2026-11-08): `scripts/fetch_retailer_images.py` solo para productos sin imagen de fabricante y con oferta aprobada; registros `data/images/retailer-<fecha>.json` con `image_origin: "retailer"` y `attribution` (PcComponentes/Coolmod); `catalog_images.enrich_images` da siempre prioridad a la imagen del fabricante. La web deriva el nombre de la tienda del host de `image_source_url` (`pccomponentes.com`, `coolmod.com`). Para retirarlas todas, borrar ese JSON y los `.webp` listados en él.
- `artifacts/batch-2026-11-02-*.json`: comprobaciones públicas y recogida Coolmod del último lote.

## Reglas

Identificar por MPN/EAN exacto o alias revisado; nunca fusionar por parecido de nombre. Conservar boxed/tray, región, revisión, capacidad, latencia y variante de ensamblador. Un chip GPU genérico no identifica una tarjeta comercial. Ausencias quedan desconocidas.

La clasificación persiste por tienda/SKU: aprobados actualizan su oferta sin nueva revisión; descartados no reaparecen; un MPN cambiado reabre revisión. Las URLs provisionales se reconcilian con el SKU detallado. Código: `nas/review_queue.py`.

Publicar ofertas solo con precio EUR, disponibilidad comprobada, fecha y vendedor directo verificado. No usar precios de marketplace como venta de la tienda. La entrega publicada no valida checkout, impuestos ni envío a Canarias. Ante bloqueo, conservar el dato fechado y dejar la nueva observación pendiente.

Compatibilidad: socket/DDR son filtros básicos; BIOS, QVL, revisiones, canales/ranks, grosor de radiador, espacio RAM/GPU y cables requieren comprobación específica. XMP/EXPO son perfiles OC. TDP/TBP/TGP y potencia de fuente son magnitudes distintas.

## Referencias: fabricante y tienda (política vigente desde 2026-11-08)

Un producto puede llevar **dos referencias**, cada una con su procedencia:

- **Fabricante**: el código o modelo que publica la ficha oficial (`mpn`, `mpn_source: "manufacturer"`).
- **Tienda**: el código comercial que imprime Coolmod/PcComponentes cuando difiere. Se registra como alias en `data/aliases/*.json` (mismo formato que `data/product-identifiers.json`: `product_id`, `kind`, `value`, `source_url` de la ficha de la tienda y `note` con la evidencia). Nunca se presenta como dato del fabricante. La oferta se enlaza con el producto a través del alias y la web puede mostrar ambos códigos (`retailer_codes`).

**Criterio B (aprobar con alias), placas base, tarjetas gráficas, cajas y refrigeración.** La ficha oficial coincide con el modelo exacto y con todos los atributos de variante que declara la tienda (memoria, edición/OC, color, socket/chipset, formato) y el EAN de la tienda, si lo hay, no lo contradice. Si la ficha oficial no publica ningún código, `mpn` es el de la tienda con `mpn_source: "retailer"` (la web muestra «≈»).

**Siguen pendientes, nunca por criterio B:** RAM (composición del kit, latencia, prefijos), CPU boxed/tray, código raíz distinto, región (-NA; el sufijo -EU/-BEU final se tolera desde 2026-11-14 en las bases aisladas, ver la guía), sufijo bulk/OEM, kits/packs y cualquier variante ambigua. El publicador automático solo auto-aprueba MPN/EAN exactos ya verificados; el criterio B lo aplica una verificación con evidencia.

## Excepciones que conservar

- Forgeon: PcComponentes declara ser titular de la marca; su ficha de marca propia es fuente primaria. Threadripper sTR5 requiere DDR5 RDIMM, no kits UDIMM de consumo.

- MSI X870E TOMAHAWK es distinta de X870, MAX y PZ; ASUS B650/B650E y sufijos WiFi también se separan.
- ASUS TUF B650E-PLUS: la fuente consultada indica chipset B650. ROG B650E-E omite chipset por ambigüedad; ROG B650-A omite memoria máxima por bloques contradictorios.
- Intel 270K Plus boxed `BX80768270K` y 285K boxed `BX80768285K`: códigos globales contrastados en [Intel 270K Plus](https://www.intel.com/content/www/us/en/products/sku/245692/intel-core-ultra-7-processor-270k-plus-36m-cache-up-to-5-50-ghz/ordering.html) e [Intel 285K](https://www.intel.com/content/www/us/en/products/sku/241060/intel-core-ultra-9-processor-285k-36m-cache-up-to-5-70-ghz/ordering.html).
- WaterForce AIO lleva radiador propio de 360 mm; no confundir con WaterForce WB. ASUS Prime OC no es Prime sin OC.
- Kingston `KF564C32BBEK2-32` no es `KF564C32BBK2-32`. Corsair PSU `-NA` no identifica automáticamente `-EU`.
- North/North XL son TG Dark, no Mesh/RC. Radiador frontal reduce longitud GPU; elevar ventilador del NH-D15 G2 para RAM alta aumenta la altura necesaria. SSD catalogados sin disipador.
- RX 9060 XT LP no implica formato físico low-profile universal. B570 omite fuente recomendada por falta de dato oficial. CPU T/OEM no implica venta minorista.
- Imágenes de familia no prueban SKU. La URL Fractal con sufijo `tg-light` se validó por SKU `FD-C-NOR1C-02` (TG Dark), no por el texto de la ruta.

## Descubrimiento

Coolmod: primer lote de 87 candidatos, primeros 25 resultados de cuatro categorías; MPN/SKU/stock ausentes no se infieren de URL, imagen o frecuencia RAM. PcComponentes: 91 candidatos leídos en páginas públicas; vendedor tercero/desconocido conserva `direct_seller_verification`. La revisión posterior debe actualizar evidencia antes de publicar.

`catalog-discovery` recoge candidatos uno a uno y conserva progreso; no publica ni revisa con IA cuando el chat está cerrado. Coolmod periódico usa piloto y `approved-coolmod-targets.json`, una página a la vez con pausas; mantener seis horas.


### Ampliación autorizada el 14 de noviembre de 2026

El usuario autoriza criterio B en cajas y refrigeración únicamente cuando la ficha oficial acredita la variante completa y no hay contradicciones de EAN. El código comercial conserva procedencia de tienda; no se convierte en MPN de fabricante. Cada decisión no basada en MPN oficial exacto requiere `variant_evidence` con `official_model`, `retailer_model`, `source_url` oficial, `ean_checked: true` y `attributes` (al menos tres atributos de variante contrastados, incluidos color/edición y las diferencias relevantes). No se admite coincidencia de familia ni se amplía el criterio a RAM, CPU, almacenamiento o fuentes. Los conflictos de identidad existentes siguen bloqueando la aprobación hasta resolverse con evidencia. Esta ampliación afecta a las bases aisladas; no autoriza publicar.


### Copias de documentación y marcas temporales

Una copia auténtica de un documento creado por el fabricante puede acreditar identidad aunque la aloje un distribuidor fiable. Es preciso distinguir autoría de alojamiento: guardar documento, hash y metadatos, citar el espejo real y la ficha oficial del modelo, y no presentar tablas elaboradas por tiendas como fuente del fabricante. `manufacturer_url` sigue siendo la página del fabricante; no se amplían dominios globalmente por un espejo. La coincidencia de SKU/UPC y todos los atributos decisivos debe ser literal en el documento y no tener contradicciones.

Las fechas de verificación se generan con reloj UTC real; los importadores aislados rechazan fechas futuras (tolerancia de 60 segundos para desajuste de reloj). Corregir una fecha de verificación nunca cambia `last_checked_at` ni las observaciones de precio/stock.


### Nombres de chip GPU y filtros (15-nov-2026)

`Chip gráfico` usa nombres sin prefijo AMD/NVIDIA/Intel: GeForce RTX 5070, Radeon RX 9070 XT, Arc B580. Se conservan Ti/SUPER/XT/XTX/GRE y las variantes profesionales; Graphics se omite solo en Arc cuando es un sufijo descriptivo. Se normaliza en `nas/spec_normalize.py`; `scripts/normalize_gpu_chips.py` permite dry-run y aplicacion con backup mediante el outbox. Los filtros GPU son Chip grafico (etiqueta web Chip), Memoria y Tipo de memoria; Arquitectura e Interfaz no se ofrecen como filtros GPU.
