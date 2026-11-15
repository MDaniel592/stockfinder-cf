# Revisión de huecos CPU — 2026-11-10

Campo examinado en los 19 modelos con huecos efectivos: `Disipador incluido`. Solo se guardaron valores cuando la información oficial permite aplicarlos a la variante identificada. Las posibilidades para SKU tray quedan separadas y no están publicadas como correcciones.

## Corregidos en `manual-cpu.json`

| Modelo | Valor | Resolución y evidencia oficial |
|---|---|---|
| Intel Core Ultra 5 245T | Sí | Intel indica que el procesador desktop boxed trae solución térmica, salvo sufijos K/KF/KS/XE/X. El sufijo T no es excepción. Ficha SKU enlazada en el propio registro. [Política boxed/tray](https://www.intel.com/content/www/us/en/support/articles/000029613/processors.html) |
| Intel Core Ultra 5 245 | Sí | Misma política boxed de Intel; no tiene sufijo de excepción. |
| Intel Core Ultra 5 230F | Sí | Misma política boxed; F no figura entre las excepciones oficiales. |
| Intel Core Ultra 5 235T | Sí | Misma política boxed; T no figura entre las excepciones. |
| Intel Core Ultra 5 225T | Sí | Misma política boxed; T no figura entre las excepciones. |
| Intel Core Ultra 9 285T | Sí | Misma política boxed; T no figura entre las excepciones. |
| Intel Core Ultra 7 265T | Sí | Misma política boxed; T no figura entre las excepciones. |
| Intel Core Ultra 7 265K | No | Intel declara que los desktop K se venden sin fan heatsink. [Artículo Intel](https://www.intel.com/content/www/us/en/support/articles/000005940/processors.html) |
| Intel Core Ultra 5 245KF (Tray/OEM) | No | Sufijo KF: Intel declara que los desktop KF se venden sin fan heatsink. [Artículo Intel](https://www.intel.com/content/www/us/en/support/articles/000005940/processors.html) |
| Intel Core Ultra 5 250KF Plus (Tray/OEM) | No | Sufijo KF; el modelo está incluido en la regla oficial para K/KF. [Artículo Intel](https://www.intel.com/content/www/us/en/support/articles/000005940/processors.html) |
| Intel Core Ultra 9 285K (Tray) | No | Sufijo K; regla oficial Intel para procesadores desktop K. [Artículo Intel](https://www.intel.com/content/www/us/en/support/articles/000005940/processors.html) |
| Intel Core Ultra 5 250K Plus (Tray) | No | Intel cita expresamente 250K Plus entre los modelos sin fan heatsink. [Artículo Intel](https://www.intel.com/content/www/us/en/support/articles/000005940/processors.html) |
| AMD Ryzen 9 7900 | No | El MPN catalogado es `100-100000590WOF`; la ficha AMD identifica `Thermal Solution (PIB): Not Included`. [Ficha AMD](https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-9-7900.html) |

Las URL de la ficha de cada producto se guardaron como `source_url` de cada registro. Para Intel, la política enlazada en la nota resuelve la inclusión en variante boxed; las fichas individuales identifican el SKU.

## Examinados; sin corrección por ahora

| Modelo | Resultado y motivo |
|---|---|
| Intel Core i7-13700T (MPN tray) | No resuelto para el SKU catalogado: Intel confirma que boxed incluye disipador, pero esta ficha es tray y la documentación consultada no dice expresamente qué accesorio acompaña a ese MPN. Posibilidad por formato tray: No; no confirmado. |
| Intel Core i7-13700 (Tray) | No resuelto: Intel confirma RM1 para la variante boxed i7-13700, pero no para este SKU tray. [Lista boxed RM1](https://www.intel.com/content/www/us/en/support/articles/000089190/processors/intel-core-processors.html). Posibilidad tray: No; no confirmado. |
| AMD Ryzen 5 5500F (Tray, MPN `100-000001905`) | La ficha del modelo publica Wraith Stealth para PIB/boxed; no confirma que el MPN tray lo incluya. Posibilidad tray: No; no confirmado. [Ficha AMD](https://www.amd.com/en/support/downloads/drivers.html/processors/ryzen/ryzen-5000-series/amd-ryzen-5-5500f.html) |
| AMD Ryzen 5 7600 (Tray, MPN `100-000001015`) | La ficha del modelo publica Wraith Stealth para PIB/boxed; no confirma que el MPN tray lo incluya. Posibilidad tray: No; no confirmado. [Ficha AMD](https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-5-7600.html) |
| AMD Ryzen 5 7500F (Tray, MPN `100-000000597`) | La ficha del modelo publica Wraith Stealth para PIB/boxed; no confirma que el MPN tray lo incluya. Posibilidad tray: No; no confirmado. [Ficha AMD](https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-5-7500f.html) |
| AMD Ryzen 7 8700G (MPN catalogado `100-000001236`, variante tray) | AMD documenta Wraith Spire para la SKU BOX `100-100001236BOX`; no confirma cooler para el MPN tray del catálogo. Posibilidad tray: No; no confirmado. [Referencia AMD 8000G](https://www.amd.com/content/dam/amd/en/documents/partner-hub/ryzen/amd-ryzen-8000g-quick-reference-guide-competitive.pdf) |

No se marcó “no publicado” en los seis pendientes: hay información de cooler para las versiones boxed, pero falta confirmación para el SKU tray examinado. Valores candidatos `No` arriba son hipótesis por tipo de empaquetado, no hechos confirmados.
