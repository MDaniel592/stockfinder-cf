# Guía de revisión de tiendas (LDLC, Neobyte)

Documento único. Resume lo aprendido el 14-nov-2026; el detalle de cada tienda está en `ldlc-local.md` y `neobyte-local.md`, y las reglas generales del catálogo en `catalog.md`. No repetir aquí ni allí.

## 1. Identificadores: qué es cada cosa

| Código | Qué es | Fiabilidad |
|---|---|---|
| SKU de tienda | `PB…` (LDLC), id numérico de URL (Neobyte). Identifica la oferta, no el producto | Estable, nunca es MPN |
| MPN de tienda | Lo que imprime la tienda. A menudo truncado, con guion omitido, con sufijo regional (`-EU`, `-BEU`), con color añadido o con nombre+potencia concatenados | Solo es alias, nunca dato del fabricante |
| MPN oficial | El que publica la ficha del fabricante (`mpn_source: manufacturer`) | Única fuente de identidad exacta |
| EAN/UPC | Código de barras. Los fabricantes casi nunca lo publican; MSI/AMD/Phanteks/Antec publican UPC (EAN sin el 0 inicial). Dos EAN distintos para el mismo producto es normal entre regiones | Sirve para **detectar contradicciones**, no para identificar solo |
| `retailer_mpn_observed` | MPN original de la tienda, se conserva siempre | Trazabilidad |

Reglas prácticas: normalizar solo mayúsculas/espacios; un guion omitido o un sufijo regional no cambia el producto; un EAN usado como MPN no es MPN; un EAN idéntico en dos productos canónicos distintos es un conflicto que hay que resolver antes de aprobar.

## 2. Qué se exige por categoría

- **GPU, placas, cajas, refrigeración:** criterio B. Ficha oficial con modelo exacto y todos los atributos de variante; EAN sin contradicción. El código de tienda queda como alias. Cajas y refrigeración llevan `variant_evidence` (`official_model`, `retailer_model`, `ean_checked: true`, ≥3 `attributes`, `source_url` oficial).
- **CPU, RAM, almacenamiento, fuentes:** MPN oficial literal en la ficha, sin alias de criterio B. Boxed/tray y región se distinguen.
- Si la ficha no publica ningún código, el `mpn` es el de tienda con `mpn_source: retailer`.

**Políticas del usuario (14-nov-2026):**
1. Sufijo regional final (`-EU`, `-BEU`, `-EEU`, `_EU`) no cambia el producto si el resto del MPN es literal y el EAN no contradice. Implementado como `strip_region()` en `scripts/{ldlc,neobyte}_pipeline.py`.
2. La web de la propia marca vale como fuente oficial si publica ficha de la variante (Textorm, Fox Spirit, HAVN).
3. Color declarado solo por la tienda se acepta si modelo y dimensiones coinciden y la ficha no publica color; la nota debe decirlo.

Nada de esto autoriza publicar: las bases siguen aisladas y el publicador no exporta LDLC ni Neobyte.

## 3. Cómo obtener información de los fabricantes

El procedimiento completo (listados por marca, CSV de fabricantes, índice unificado y diferencia
contra la cola) está en [manufacturer-index.md](manufacturer-index.md). Reglas de esta sección:

Orden de prueba (de menos a más pesado), siempre ≥2 s entre peticiones por host:
1. **Evidencia ya capturada:** `/opt/stockfinder/artifacts/official-browser-evidence` (HTML/PDF oficiales) y, para MSI, el catálogo público `artifacts/msi-official-store/all-products.json` (`variants.sku` exacto; MSI Store es fuente primaria). El índice unificado (`work/manufacturer-index/latest.csv`) los reúne con su hash.
2. **`curl` con User-Agent de Chrome desde la VM.** Funciona con ASUS ROG, AMD, Crucial, G.SKILL, Lian Li, Thermalright, Phanteks, DeepCool, Antec y la mayoría de cajas. `curl_cffi` no está instalado.
3. **Camoufox** (venv en el PC, ruta en `claude-codex-coordination.json`), un navegador a la vez, para sitios con JS o bloqueo.

Sitios problemáticos: msi.com (403), gigabyte.com (403 a curl, abre en navegador), seasonic.com (HTML sin códigos), ASRock y AMD (JS), xpg.com (403), gskill.com (`robots.txt` prohíbe agentes: no usarlo masivamente), Neobyte (página de bloqueo: usar los datos ya guardados en la cola). Muchos fabricantes publican solo el **nombre de modelo** y no el P/N: eso solo vale como MPN si coincide literal tras normalizar.

Reglas de higiene: **nunca descargar ficheros al PC del usuario** (los PDF dispararon un diálogo de descarga); leer PDF en la VM (`pdftotext`/`pypdf` no están en la VM). No consultar APIs internas de los sitios si el clasificador de permisos lo bloquea.

## 4. Flujo de trabajo y errores típicos

Estados en `retailer_review_queue`: `pending` con nota `Pendiente:` = retenida con motivo; nota vacía = sin revisar; `approved` (con `retailer_listings` y oferta) o `rejected`. Bases: `stockfinder_ldlc_local` y `stockfinder_neobyte_local`.

Las decisiones se escriben **solo por lotes** JSON en `artifacts/<tienda>/reviews/` (escribir a `.tmp`, `chmod 644`, `mv`); el pipeline los importa cada 30 s. Un lote reenviado necesita un **nombre nuevo** (se identifica por hash+nombre). Nunca UPDATE directos de estado, ni rechazos en bloque (se hizo una vez y hubo que reabrir desde `retailer_review_queue_pending_backup_20261114`).

Errores que ya ocurrieron: `specs_verified_at` en el futuro; canónico ausente en la base de la tienda; canónico sin `mpn_source: manufacturer` (la comprobación de MPN literal lo exige); alias omitido; colisión de `mpn` con un canónico existente (reutilizar el producto); `variant_evidence` incompleto; lote guardado en cp1252 en vez de UTF-8 (tumba el pipeline en bucle: escribir siempre UTF-8); MPN de tienda sin el prefijo oficial (p. ej. HVN- en HAVN), que se resuelve con un alias del MPN observado y nota. Antes de escribir un lote, mirar el aviso de colisiones del propio script.

## 5. Trabajo en paralelo

Varios agentes escriben a la vez. Coordinación en `/opt/stockfinder/claude-codex-coordination.json`: ámbitos activos y reglas. Cada agente solo toca sus propios lotes (`claude-*`, `luna-*`, `root-*`) y comprueba `status='pending'` antes de decidir.

## 6. Revisión cruzada: qué falló más

De 257 decisiones comprobadas contra su ficha, ninguna resultó incorrecta; las dudosas fueron siempre de: color solo en la tienda, EAN no verificable ("sin contradicción" suele significar "la ficha no publica EAN"), ids de producto distintos entre tiendas para el mismo MPN, y variantes V1/V2 sin distinguir en el título.

## 7. Icecat como evidencia de identidad (política del usuario, 15-nov-2026)

Icecat está alimentado por las marcas. Cuando la web del fabricante no publica el código, un registro Icecat cuyo GTIN es el EAN de la tienda y cuyo `brand_part_code` es el MPN de la tienda **y** el del canónico (literal, sin tolerancias de región ni guiones) identifica el producto. Solo psu/cpu/ram/storage; la marca de Icecat, tienda y canónico debe coincidir.

- Evidencia: un archivo `artifacts/ldlc/icecat-evidence/<GTIN>.json` exportado de `work/research-evidence-20261115/evidence.sqlite3` (el validador lo lee; no se fía del lote). Lo escribe quien revisa, con permisos 644.
- Lote: la decisión lleva `icecat_evidence: {gtin, icecat_id}` y la nota debe decir que la identidad viene de Icecat. `manufacturer_url` sigue siendo una página real de la marca. Implementado en `scripts/icecat_identity.py` y `validate_review` de `ldlc_pipeline.py` (copia: `.bak-20261115-icecat`). Neobyte aún no lo usa.
- No sirve si Icecat devuelve otro código (MSI MAG A850GL: `MAG A850GL PCIE5`, no `306-…`) ni sin revisión de placa (Gigabyte B550M).
- Ejemplo: `claude-icecat-fsp-twins-pro-batch-001.json` (FSP Twins PRO 500/700/900 W).

## 8. Vías de identidad añadidas el 15-nov-2026 (decididas por el usuario; validadores LDLC y Neobyte)

Código en `scripts/{icecat_identity,amd_opn,manufacturer_ean}.py` y `validate_review` de los dos pipelines (copias `*.bak-20261115-*`). Ninguna autoriza publicar.

| Campo de la decisión | Cuándo vale | Qué comprueba el validador |
|---|---|---|
| `icecat_evidence {gtin, icecat_id}` | psu, cpu, ram, storage, case, cooler | GTIN de Icecat = EAN de la tienda, misma marca (sin distinguir mayúsculas), código de marca de Icecat = MPN de tienda = MPN canónico literal. Archivo `artifacts/<tienda>/icecat-evidence/<GTIN>.json` |
| `icecat_evidence.match = 'ean_model'` | La tienda imprime un código regional (MSI 306-…, Gigabyte 28xxx-…) | Código de Icecat = nombre de modelo canónico, o todos sus tokens con dígitos aparecen en nombre/MPN canónico (subcadena si ≥4 caracteres). Registra el EAN como alias |
| `icecat_evidence.match = 'mpn_literal'` | Registro obtenido por marca+MPN sin lista de GTIN | Archivo con `"lookup": "brand_mpn"`; el resto de comprobaciones literales siguen |
| `brand_alias = 'adata->xpg'` | case, cooler, psu | Solo con Icecat y marca XPG en el registro |
| `manufacturer_ean {source_url, ean_on_page, model_on_page}` | psu, cpu, ram, storage | La página del fabricante imprime el código de barras (UPC = EAN sin 0 inicial); la nota cita URL, código y modelo |
| `amd_opn {official_opn, packaging}` | cpu AMD | Raíz `100-<9 dígitos>` igual a la de un OPN en amd.com; el canónico conserva el OPN de la tienda (`mpn_source: retailer`) |
| `psu_criterion_b: true` + `variant_evidence` | psu cuya marca no imprime el código (Seasonic, Lian Li) | ≥4 atributos, ficha oficial exacta, EAN comprobado; `mpn_source: retailer` |
| Sufijo regional | psu/cpu/ram/storage | `region_equal`: `-3BEU` coincide con `-3B` |

Vías sin resolver (política o acceso): Full Icecat (403 código 9: Lian Li, Seasonic, Cooler Master, AMD, G.Skill), msi.com sin `306-…`, gigabyte.com en mantenimiento, Corsair sin slug de URL, Textorm sin ficha con esos MPN.
