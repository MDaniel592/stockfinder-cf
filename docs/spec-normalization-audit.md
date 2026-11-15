# Auditoría de normalización de especificaciones

Fecha: 2026-11-13. Revisión de solo lectura; no se modificaron la normalización, los productos ni las bases de datos.

## Alcance y método

Se siguió la ruta de importación (`nas/import_catalog.py` → `nas/spec_normalize.py` → overrides) y se contrastó con los nombres consumidos por la ficha/listas (`apps/web/src/display.ts`), los facets (`packages/api-contracts/catalog-filters.ts`, `apps/edge-api/src/index.ts`) y el selector/comprobaciones del configurador (`apps/web/src/pc-builder.ts`). Se inspeccionaron las pruebas de normalización en `tests/test_nas.py`; no se ejecutaron.

La consulta a PostgreSQL de producción fue `BEGIN READ ONLY` con `ROLLBACK`. La distribución observada fue:

| Categoría | Productos vivos | Cobertura de campos relevantes observada |
|---|---:|---|
| case | 713 | `Formatos compatibles` 713; `Formato fuente` 701; `Longitud GPU máxima` 709; `Altura disipador máxima` 709; `Panel lateral` 671 |
| cooler | 339 | `Tipo` 339; `Sockets compatibles` 337; `Ventiladores` 331; `Radiador` 192; `Altura` 144 |
| cpu | 96 | Socket, Núcleos, Hilos, Frecuencia turbo, TDP, Gráficos integrados, Disipador incluido y Memoria compatible: 96 cada uno |
| gpu | 257 | Chip gráfico, Memoria, Tipo de memoria, Interfaz y Fuente recomendada: 257, 257, 257, 257 y 251; Longitud 235; TBP 97 / TGP 121 |
| motherboard | 480 | Socket, Chipset, Formato, Tipo de memoria, Ranuras de memoria: 480 cada uno; Memoria máxima 355; Wi-Fi 232 |
| psu | 310 | Potencia 310; Formato 308; Eficiencia 80 PLUS 250; Estándar ATX 248; Modular 280; Conector GPU 290 |
| ram | 603 | Capacidad y Módulos 603; Tipo de memoria 603; Velocidad 601; Latencia 598; Formato 599; Perfil 513 |
| storage | 345 | Tipo, Capacidad e Interfaz 345; Formato 344; Lectura secuencial 233; Disipador integrado 170 |

La categoría `hdd` que aparece en inventarios de tienda no es una categoría canónica aparte: el catálogo/configurador soporta ocho slots y HDD está dentro de `storage`.

El artefacto local `artifacts/ldlc/verified-psu-run/catalog.json` contiene 3.695 filas de productos canónicos y ejemplos de specs ya en español/canonizadas. Los shards de Neobyte inspeccionados son candidatos comerciales y expresamente marcan `verified_specifications` como faltante; por tanto no son una segunda fuente de especificaciones verificadas. No se editó ni se intervino en esos pipelines locales.

## Hallazgos prioritarios

### P1 — La lista de alias no cubre variantes reales de claves

El importador aplica la normalización al importar producto y después los overrides manuales. La normalización solo renombra claves incluidas en `KEY_ALIASES`; las claves desconocidas se conservan literalmente, sin error. Esto es bueno para no perder detalle, pero deja valores fuera de facets y de los lectores por nombre exacto. La base contiene, por ejemplo:

| Categoría | Clave persistida fuera del nombre canónico | Filas | Efecto comprobable |
|---|---|---:|---|
| case | `Form factor` (11), `Fans` (3) | 14 | `Form factor` no se integra a `Formatos compatibles`; el texto tipo `Mid-Tower; ATX / Micro ATX / ITX` no puede alimentar la comprobación si llega solo bajo esa clave. En esas 11 filas la lista canónica también está presente, así que no hay pérdida de cobertura agregada. `Fans` queda como detalle libre y no se clasifica como `Ventiladores incluidos` sin confirmar su significado. |
| cpu | `Gráfica integrada` | 5 | La ficha agrupa la clave, pero el facet y la advertencia de salida de vídeo leen `Gráficos integrados`. 96 CPUs tienen la clave canónica, así que las 5 filas son inconsistencia/posible dato duplicado. |
| cpu | `Refrigeración incluida` | 5 | Solo se muestra como detalle; `checkCompatibility` determina disipador incluido leyendo `Disipador incluido`. Las CPUs observadas también tienen esa clave canónica (96/96), pero el dato divergente no se compara ni se resuelve. |
| motherboard | `Chipset/model family` (35), `Chipset/model` (6) | 41 | `Chipset` canónico existe en 480/480; no afecta cobertura agregada, pero el normalizador solo elimina ambas variantes cuando también existe `Chipset`. Si el valor y el canónico discrepan, conserva la variante bajo su clave original y no valida equivalencia. |
| ram | `Perfiles`, `Latencia probada`, `Velocidad probada` (1 cada una) | 3 | `Perfiles` no aparece en filtro `Perfil`; las claves probadas se muestran como detalles pero no se cuentan en facets `Latencia`/`Velocidad`. La cobertura de filtro queda incompleta en esos productos. |
| cooler | `Radiator`, `Fans` (1 cada una) | 2 | El facet usa `Radiador`/`Ventiladores`; las comprobaciones físicas y de tipo dependen de `Radiador`/`Ventiladores` canónicos. En ambos productos también existe la clave canónica. `Fans` no se renombra cuando es prosa; el alias condicional solo se aplica si el valor comienza por un contador como `2 ×`. |
| storage | `Disipador` | 1 | La ficha lo conserva como detalle; el facet es `Disipador integrado`. No equivale automáticamente a “integrado” cuando falta el booleano. |
| psu | `EstÃ¡ndar ATX` | 7 | Codificación mojibake no está reconocida. Los productos no aparecen en el facet `Estándar ATX` ni quedan normalizados para mostrar el estándar junto a la fuente. |
| psu | `Eficiencia` | 1 | Un texto no analizado quedó bajo la clave general; el facet solo lee `Eficiencia 80 PLUS` y `Cybenetics`. |

No se recomienda renombrar indiscriminadamente `Formato` en cajas: allí significa tipo de torre en algunos productos (`Mid-tower`, `Mini-Torre`) y no factor de forma de placa. La compatibilidad debe seguir en `Formatos compatibles`.

### P1 — El configurador interpreta algunas magnitudes y nombres de forma demasiado estrecha

- `apps/web/src/pc-builder.ts` `memoryTypes()` solo extrae DDR3/4/5. Valores `DDR5 RDIMM` sí producen DDR5, pero `DDR5-5600` no es el formato de memoria de la placa y falta comprobar familia de módulo/RDIMM frente a UDIMM. La guía de catálogo documenta Threadripper sTR5 con DDR5 RDIMM, mientras la comprobación CPU/placa/RAM solo compara generación DDR; puede declarar `memory-match` cuando el tipo de módulo no sirve.
- `memoryTypes()` también omite DDR2, y la expresión de sockets del selector limita AM a AM3/4/5. El set del catálogo incluye cooler antiguos AM2/AM2+ y LGA legacy; el parser de `socketList()` no los considera. Para una CPU/plataforma antigua, el filtro puede devolver `null` (sin filtrar) o la comprobación `socket-unknown` aunque haya datos exactos. Priorizar extensión de regex desde las sockets que el propio catálogo acepta y cubrir con pruebas.
- `formats()` no reconoce SSI-EEB/SSI-CEB/EE-ATX, aunque el normalizador y los facets de cajas sí enumeran esos tokens. Además motherboard permite solo E-ATX/ATX/Micro-ATX/Mini-ITX en el override, lo que impide curar formatos de placas fuera de esa lista. Puede producir `case-format-unknown` para una combinación con datos publicados o falsos desconocidos.
- `dimension()` solo extrae `mm`. Los campos de compatibilidad efectiva exigen mm por esquema, pero specs no curadas en texto libre pueden tener `cm`; la normalización general corrige coma decimal y formatos de dimensiones de placa, no convierte dimensiones universales. No convertir valores ambiguos automáticamente; admitir unidades explicitadas con factor 10.
- La comprobación de potencia toma TGP/TBP/TDP y fuente recomendada por claves exactas; documentación señala correctamente que TDP/TBP/TGP no son lo mismo. El producto con `Consumo (TDP)` (1 fila GPU) no sirve para el presupuesto y queda como desconocido, opción segura. No mapear TDP a TBP/TGP como “arreglo”.

### P2 — La normalización de grafías no es conversión general de unidades

`normalize_specs()` intencionalmente conserva magnitudes y hechos separados. Estandariza algunas grafías (`1,4 V` → `1.4 V`, `3200 MHz` → `3200 MT/s` en RAM, PCIe Gen, sockets, perfiles, formatos) pero no unifica todas las unidades ni estructuras. Eso es apropiado para no inventar hechos, aunque implica que filtros simples por igualdad mantienen grupos distintos. Casos a cubrir en revisión de fuentes: capacidades GB/TB (los facets ordenan numéricamente, pero no los igualan), dimensiones cm/mm, velocidad de ventilador y frecuencias con formatos de unidades mezclados. La normalización de tamaño debe conservar el valor mostrado y solo derivar equivalencia para comparaciones cuando el formato sea inequívoco.

## Cobertura por categoría

### CPU

Los ocho campos básicos principales están completos en las 96 filas. Se normalizan socket, memoria compatible, gráficos integrados y disipador. Hallazgo de alias residual: `Gráfica integrada` y `Refrigeración incluida` (5 c/u). El filtro usa `Gráficos integrados`; el configurador también usa `Gráficos integrados` y `Disipador incluido`, así que solo la grafía residual no llega a esas funciones. Casos de TDP vs potencia base/máxima turbo se mantienen separados de forma correcta.

### GPU

Las cinco claves expuestas como facets tienen cobertura completa. `Fuente recomendada` aparece en 251/257; `Longitud` en 235. Se conservan separadas `Consumo (TBP)` (97) y `Consumo (TGP)` (121), correctamente. Hay tres claves marginales `Consumo` y una `Consumo (TDP)`; no son convertibles de forma segura. `Radiador GPU` existe en un producto y el configurador lo consume explícitamente para advertir sobre radiadores propios.

### Placa base

Los cinco campos de facet/compatibilidad tienen cobertura 480/480. Hay 41 filas con variantes de chipset adicionales; la lógica de eliminación condicional evita la pérdida cuando el canonical existe, pero no resuelve discrepancias. Requiere auditar conflictos en vez de forzar alias. `Tipo de memoria` solo representa DDR y no el formato/ranks/capacidad QVL; el aviso de catálogo ya limita adecuadamente la promesa de compatibilidad.

### RAM

Las claves están mayormente estandarizadas: módulo, capacidad, frecuencia a MT/s, CL separada de timings y perfiles XMP/EXPO en lista OR. Sin embargo la cobertura del perfil es 513/603 y hay restos singulares `Perfiles`, `Latencia probada` y `Velocidad probada`; falta decidir alias semánticos para perfil y si “probada” corresponde a velocidad facetada o a otro concepto. La lista de módulos admite RDIMM en el catálogo pero la verificación del configurador no compara formato, principal limitación real con sTR5.

### Almacenamiento

Tipo/capacidad/interfaz son completos; formato 344/345. Las 79 claves `Caché`, 41 `Controlador`, escrituras y lecturas se conservan como especificaciones. `Disipador integrado` aparece en 170/345 y un registro separado `Disipador`; la normalización reconoce `Sí (descripción)` solo si está bajo `Disipador integrado`. No inferir booleano `Sí` de la mera existencia de un disipador nominal.

### Fuente

Potencia tiene cobertura completa; formato 308; estándar ATX 248; certificación 80 PLUS 250, Cybenetics 94, ruido Cybenetics 21, modularidad 280 y conector GPU 290. La división de certificaciones evita mezclar pruebas distintas. Siete campos mojibake y una eficiencia general sin parsear quedan fuera de los filtros. La compatibilidad del configurador usa potencia pero no estándar ATX, certificación, modularidad ni tipo de conector para aprobar; solo advierte sobre estimación y deja explícito que cables/transitorios/calidad no quedan validados.

### Caja

Los límites físicos y formatos de placa cubren 709–713/713; 11 registros guardan además `Form factor` y texto libre, aunque ya tienen la clave canónica. La normalización reconoce listas de formato y detalles como conectores traseros; no conviene fusionar la clave simple `Formato`, que describe mid-tower/mini-tower en los ejemplos reales. `Radiator support` conserva texto detallado (11 filas); la compatibilidad extrae solo números del valor y compara talla nominal, por lo que posiciones/grosor/espesor se advierten como pendientes, de acuerdo con `docs/catalog.md`.

### Refrigeración

Tipo completo; sockets 337/339; ventiladores 331/339. `Radiador` se aplica principalmente a AIO (192/339). Existe un valor de dimensiones múltiples bajo `Radiador` (p. ej. `395 × 120 × 27 mm` en un AIO) que el normalizador separa como `Detalle radiador` únicamente cuando empieza con el tamaño nominal en el patrón esperado. La comprobación usa solo el tamaño nominal del radiador, una aproximación declarada; no garantiza posición ni montaje. Ampliar la lista de sockets permitidos en overrides después de validar sockets oficiales observados; no desechar tokens desconocidos.

## Flujo de overrides, facets y presentación

- Overrides en `data/overrides/*.json` se aplican **después** de normalizar en `nas/import_catalog.py`; aceptan solo claves y valores canónicos según `nas/spec_overrides.py`. Los registros `not_published` sirven para cerrar huecos, no cambian specs. Se debe priorizar reparar el normalizador antes de generar overrides que repitan hechos de la ficha.
- El normalizador conserva claves no conocidas y solo descarta duplicados de igual valor. Diferencias de valor conservan la clave alternativa (o una clave de origen), lo que preserva evidencia pero deja dos posibles versiones del hecho. El informe de importación debería poder contabilizar de forma rutinaria alias residuales y colisiones, para que este estado no dependa de una auditoría manual de SQL.
- Facets se construyen de una allowlist de claves. Los filtros multivalor separan tokens delimitados y usan OR para opciones de una misma clave; por ello claves residuales no se hacen visibles por sí solas.
- `display.ts` conserva claves desconocidas en “Otras características”, así que la ausencia de un facet no significa borrado. Esto reduce pérdida visual pero no ayuda al selector/compatibilidad, que leen claves concretas.

## Correcciones propuestas, por prioridad

1. **Inventariar y resolver alias residuales**: añadir reporte de importación/consulta que liste claves fuera del contrato por categoría, volumen, y productos que también tienen la canónica. Revisar colisiones antes de mapear. Añadir alias comprobados para `Form factor` de cajas solo si representa expresamente los formatos de placa; `Gráfica integrada` → `Gráficos integrados` con normalización del valor; `Perfiles` con semántica confirmada; claves con mojibake tras corregir/confirmar codificación. No aliasar `Formato` de caja ni `Disipador` de storage a booleanos.
2. **Compatibilidad de RAM/módulos**: comparar RDIMM/UDIMM cuando el socket/plataforma lo requiere (incluido Threadripper sTR5) y emitir error/desconocido, no “match” por DDR5 solamente. Añadir chequeo a filtro server-side y client-side de forma conjunta, ya que hoy `builderSpecFilters()` filtra RAM solo por `Tipo de memoria`.
3. **Unificar el parser de sockets y formatos** entre listas normalizadas y configurador, con cobertura para AM2/AM2+, sTR5/sWRX y sockets LGA observados, además de EE-ATX/SSI-EEB/SSI-CEB. Mantener unknowns visibles y compatibilidad “desconocida” ante parsing incompleto.
4. **Normalización de unidades explícitas**: comparadores de dimensión convertir cm↔mm cuando unidad está escrita y de capacidad GB↔TB solo para orden/filtrado numérico si se quiere; no reescribir la fuente ni equiparar magnitudes eléctricas distintas.
5. **Reprocesar catálogo de forma controlada** tras cerrar alias/reglas: puesto que la normalización corre al importar, los rows existentes solo se corrigen al reimportar los productos. Hacer primero dry-run con diffs y conteo de claves/valores afectados; no actualizar registros de producción con esta auditoría.
6. **Ampliar pruebas focalizadas** en `tests/test_nas.py` (alias residual/conflicto, conversiones sin pérdida, efectividad de facets, DDR5 RDIMM vs UDIMM, legacy socket/form factor, unidades). Las pruebas actuales cubren casos unitarios intencionales, pero no verifican que todas las claves reales del catálogo acaben en campos de contrato ni que los parsers de compatibilidad acepten tokens del `LIST_ORDER`.

## Referencias de implementación

- `nas/spec_normalize.py`: mapas `KEY_ALIASES`, `CONDITIONAL_ALIASES`, `LIST_ORDER`, `_extras()`, `normalize_value()` y `normalize_specs()`.
- `nas/spec_overrides.py`: `FORMATS`, `REQUIRED`, `check_value()`, `apply()` y `gaps()`.
- `nas/import_catalog.py`: llamadas de normalización y overrides en importación.
- `packages/api-contracts/catalog-filters.ts`: allowlist de facets y listas multivalor.
- `apps/edge-api/src/index.ts`: extracción de facets y comparación de specs en API.
- `apps/web/src/pc-builder.ts`: parsers y lógica de compatibilidad.
- `apps/web/src/display.ts`: columnas, grupos y fallback para claves residuales.
- `tests/test_nas.py`: pruebas de normalización, eficiencia, valores de facets y lista de tokens.
- `docs/catalog.md`: límites de compatibilidad y conservación de facts diferenciados.
