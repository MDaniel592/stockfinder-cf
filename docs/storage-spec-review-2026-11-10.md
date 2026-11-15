# Auditoría de correcciones de almacenamiento — 2026-11-10

La revisión deja 42 productos con huecos efectivos. Las fuentes y notas de cada corrección aplicada constan en `data/overrides/manual-storage.json`. Las estimaciones están aparte y no se aplican: `data/storage-candidates-2026-11-10.json`.

## Auditoría de velocidad por variante

Se comprobaron las 63 correcciones con valor numérico de `Lectura secuencial` frente a la ficha o tabla oficial enlazada: capacidad, número de parte y valor corresponden a esa variante. No encontré diferencias adicionales. La corrección detectada en Samsung 980 500 GB era 3 500 MB/s; el dato ahora coincide con la ficha Samsung: 3 100 MB/s.

| Modelo | Lectura secuencial |
|---|---|
| Corsair MP600 MINI 1 TB (CSSD-F1000GBMP600MNR2) (`corsair-mp600-mini-1tb-cssd-f1000gbmp600mnr2`) | 7000 MB/s |
| Corsair MP700 PRO 1 TB con refrigerador de aire (CSSD-F1000GBMP700PRO) (`corsair-mp700-pro-1tb-air-cooler-cssd-f1000gbmp700pro`) | 11700 MB/s |
| Corsair MP700 PRO 1 TB (CSSD-F1000GBMP700PNH) (`corsair-mp700-pro-1tb-cssd-f1000gbmp700pnh`) | 11700 MB/s |
| Crucial BX500 1 TB (CT1000BX500SSD1) (`crucial-ct1000bx500ssd1`) | 540 MB/s |
| Crucial P510 1 TB con disipador (CT1000P510SSD5) (`crucial-ct1000p510ssd5`) | 11000 MB/s |
| Crucial BX500 2 TB (CT2000BX500SSD1) (`crucial-ct2000bx500ssd1`) | 540 MB/s |
| Crucial T500 2 TB (CT2000T500SSD8) (`crucial-ct2000t500ssd8`) | 7400 MB/s |
| Crucial BX500 240 GB (CT240BX500SSD1) (`crucial-ct240bx500ssd1`) | 540 MB/s |
| Crucial BX500 500 GB (CT500BX500SSD1) (`crucial-ct500bx500ssd1`) | 540 MB/s |
| Dell 960GB SSD SATA Read Intensive 6Gbps 512e 2.5in con portador hibrido 3.5in, S4520 (345-BDQM) (`dell-345-bdqm`) | 550 MB/s |
| Dell 960GB SSD SATA Read Intensive 6Gbps 512e 2.5in 1DWPD (345-BJPR) (`dell-345-bjpr`) | 550 MB/s |
| GOODRAM PX700 1 TB (SSDPR-PX700-01T-80) (`goodram-px700-1tb-ssdpr-px700-01t-80`) | 7400 MB/s |
| Kioxia EXCERIA BASIC 1 TB (LSF10Z001TG8) (`kioxia-exceria-basic-1-tb-lsf10z001tg8`) | 7200 MB/s |
| Kioxia EXCERIA BASIC 2 TB (LSF10Z002TG8) (`kioxia-exceria-basic-2-tb-lsf10z002tg8`) | 7300 MB/s |
| Kioxia EXCERIA G3 1 TB (`kioxia-exceria-g3-1tb`) | 10000 MB/s |
| Kioxia EXCERIA G3 2 TB (`kioxia-exceria-g3-2tb`) | 10000 MB/s |
| Kioxia EXCERIA PLUS G3 1 TB (`kioxia-exceria-plus-g3-1tb`) | 5000 MB/s |
| Kioxia EXCERIA PLUS G3 2 TB (`kioxia-exceria-plus-g3-2tb`) | 5000 MB/s |
| Kioxia EXCERIA PLUS G4 1 TB (`kioxia-exceria-plus-g4-1tb`) | 10000 MB/s |
| Kioxia EXCERIA PRO G2 1 TB (`kioxia-exceria-pro-g2-1tb`) | 14400 MB/s |
| Kioxia EXCERIA PRO G2 4 TB (`kioxia-exceria-pro-g2-4tb`) | 14900 MB/s |
| Kioxia EXCERIA SATA SSD 480 GB (LTC10Z480GG8) (`kioxia-exceria-sata-480-gb-ltc10z480gg8`) | 555 MB/s |
| Kioxia EXCERIA SATA SSD 960 GB (LTC10Z960GG8) (`kioxia-exceria-sata-960-gb-ltc10z960gg8`) | 555 MB/s |
| MSI SPATIUM M461 1 TB (S78-440L1D0-P83) (`msi-spatium-m461-1-tb-s78-440l1d0-p83`) | 5000 MB/s |
| MSI SPATIUM M461 2 TB (S78-440Q550-P83) (`msi-spatium-m461-2-tb-s78-440q550-p83`) | 5000 MB/s |
| MSI SPATIUM S270 480 GB (S78-440E350-P83) (`msi-spatium-s270-480-gb-s78-440e350-p83`) | 500 MB/s |
| PNY CS1030 1 TB (M280CS1030-1TB-RB) (`pny-cs1030-1-tb-m280cs1030-1tb-rb`) | 2100 MB/s |
| PNY CS1030 250 GB (M280CS1030-250-RB) (`pny-cs1030-250-gb-m280cs1030-250-rb`) | 2500 MB/s |
| PNY CS1030 500 GB (M280CS1030-500-RB) (`pny-cs1030-500-gb-m280cs1030-500-rb`) | 2000 MB/s |
| PNY CS2140 1 TB (M280CS2140-1TB-RB) (`pny-cs2140-1tb-m280cs2140-1tb-rb`) | 3600 MB/s |
| PNY CS2230 500 GB (M280CS2230-500-RB) (`pny-cs2230-500gb-m280cs2230-500-rb`) | 3300 MB/s |
| Samsung 980 500 GB (MZ-V8V500BW) (`samsung-980-500gb-mz-v8v500bw`) | 3100 MB/s |
| SanDisk 520 SATA SSD 500 GB (SDSS51500GAB-000G0) (`sandisk-520-sata-ssd-500-gb-sdss51500gab-000g0`) | 560 MB/s |
| SanDisk Optimus 5100 1 TB (SDSP51100TAN-000E0) (`sandisk-optimus-5100-1-tb-sdsp51100tan-000e0`) | 7100 MB/s |
| SanDisk Optimus 5100 2 TB (SDSP51200TAN-000E0) (`sandisk-optimus-5100-2-tb-sdsp51200tan-000e0`) | 7100 MB/s |
| SanDisk Optimus 5100 4 TB (`sandisk-optimus-5100-4tb`) | 6900 MB/s |
| SanDisk Optimus 5100 500 GB (`sandisk-optimus-5100-500gb`) | 6600 MB/s |
| SanDisk Optimus GX 7100 1 TB (SDSP71100TAN-000E0) (`sandisk-optimus-gx-7100-1-tb-sdsp71100tan-000e0`) | 7250 MB/s |
| SanDisk Optimus GX 7100 2 TB (SDSP71200TAN-000E0) (`sandisk-optimus-gx-7100-2-tb-sdsp71200tan-000e0`) | 7250 MB/s |
| SanDisk Optimus GX 7100 4 TB (SDSP71400TAN-000E0) (`sandisk-optimus-gx-7100-4-tb-sdsp71400tan-000e0`) | 7000 MB/s |
| SanDisk Optimus GX 7100 500 GB (SDSP71500GAN-000E0) (`sandisk-optimus-gx-7100-500-gb-sdsp71500gan-000e0`) | 6800 MB/s |
| SanDisk Optimus GX PRO 8100 1 TB sin disipador (`sandisk-optimus-gx-pro-8100-1tb`) | 14900 MB/s |
| SanDisk Optimus GX PRO 850X 1 TB (SDSP81100TAN-000E0) (`sandisk-optimus-gx-pro-850x-1-tb-sdsp81100tan-000e0`) | 7300 MB/s |
| SanDisk Optimus GX PRO 850X 2 TB (SDSP81200TAN-000E0) (`sandisk-optimus-gx-pro-850x-2-tb-sdsp81200tan-000e0`) | 7300 MB/s |
| SanDisk SSD Plus 2 TB (SDSSDA-2T00-G28) (`sandisk-ssd-plus-2-tb-sdssda-2t00-g28`) | 545 MB/s |
| SanDisk WD Blue SN5100 4 TB (`sandisk-wd-blue-sn5100-4tb`) | 6900 MB/s |
| TEAMGROUP CX2 256 GB (T253X6256G0C101) (`teamgroup-cx2-256gb-t253x6256g0c101`) | 520 MB/s |
| TEAMGROUP MP33 256 GB (TM8FP6256G0C101) (`teamgroup-mp33-256gb-tm8fp6256g0c101`) | 1600 MB/s |
| TEAMGROUP MS30 512 GB (TM8PS7512G0C101) (`teamgroup-ms30-512gb-tm8ps7512g0c101`) | 530 MB/s |
| TEAMGROUP NV5000 1 TB (TM8FGM001T0C101) (`teamgroup-nv5000-1tb-tm8fgm001t0c101`) | 4500 MB/s |
| WD_BLACK SN7100 4 TB (WDS400T4X0E) (`wd-black-sn7100-4-tb-wds400t4x0e`) | 6800 MB/s |
| WD_BLACK SN8100 1 TB (WDS100T1X0M) (`wd-black-sn8100-1-tb-wds100t1x0m`) | 14900 MB/s |
| WD_BLACK SN8100 4 TB (WDS400T1X0M) (`wd-black-sn8100-4-tb-wds400t1x0m`) | 14900 MB/s |
| WD Blue SA510 500 GB M.2 (WDS500G3B0B) (`wd-blue-sa510-500-gb-m-2-wds500g3b0b`) | 560 MB/s |
| WD Blue SN5000 1 TB (WDS100T4B0E) (`wd-blue-sn5000-1-tb-wds100t4b0e`) | 5150 MB/s |
| WD Blue SN5000 500 GB (WDS500G4B0E) (`wd-blue-sn5000-500-gb-wds500g4b0e`) | 5000 MB/s |
| WD Blue SN5100 2 TB (WDS200T5B0E-00CPE0) (`wd-blue-sn5100-2-tb-wds200t5b0e-00cpe0`) | 7100 MB/s |
| WD Blue SN5100 500 GB (WDS500G5B0E-00CPE0) (`wd-blue-sn5100-500-gb-wds500g5b0e-00cpe0`) | 6600 MB/s |
| WD Green SN3000 1 TB (WDS100T4G0E) (`wd-green-sn3000-1-tb-wds100t4g0e`) | 5000 MB/s |
| WD Green SN3000 2 TB (WDS200T4G0E) (`wd-green-sn3000-2-tb-wds200t4g0e`) | 5000 MB/s |
| WD Green SN350 500 GB (WDS500G2G0C-00CDH0) (`wd-green-sn350-500-gb-wds500g2g0c-00cdh0`) | 2400 MB/s |
| WD Red SA500 NAS 1 TB M.2 (WDS100T1R0B) (`wd-red-sa500-nas-1-tb-m-2-wds100t1r0b`) | 560 MB/s |
| WD Red SA500 NAS 2 TB 2.5" (WDS200T2R0A) (`wd-red-sa500-nas-2-tb-2-5-wds200t2r0a`) | 560 MB/s |

## Disipadores registrados como No

Se conservaron los 20 valores `No` con evidencia de SKU exacto y condición de disipador explícita (ausente, N/A, independiente o accesorio opcional). Las fuentes y notas enumeran MPN y capacidad de cada variante.

| Modelo | MPN | Evidencia |
|---|---|---|
| ADATA LEGEND 860 1 TB (`adata-legend-860-1tb`) | SLEG-860-1000GCS | La tabla oficial de pedido confirma exactamente SLEG-860-1000GCS (1TB); ficha técnica oficial y compatibilidad PS5 de la serie indican disipador opcional, no integrado. Opcional: https://www.adata.com/storage/downloadfile/LEGEND-860-Compatible-with-PS5-Illustration_20230613.pdf |
| ADATA LEGEND 860 500 GB (`adata-legend-860-500gb`) | SLEG-860-500GCS | La tabla oficial de pedido confirma exactamente SLEG-860-500GCS (500GB); ficha técnica oficial y compatibilidad PS5 de la serie indican disipador opcional, no integrado. Opcional: https://www.adata.com/storage/downloadfile/LEGEND-860-Compatible-with-PS5-Illustration_20230613.pdf |
| ADATA LEGEND 900 1 TB (`adata-legend-900-1tb`) | SLEG-900-1TCS | La tabla oficial confirma exactamente SLEG-900-1TCS (1TB); el documento oficial de compatibilidad PS5 de LEGEND 900 indica disipador opcional. https://www.adata.com/storage/downloadfile/LEGEND-900-Compatible-with-PS5-Illustration_20230613.pdf |
| ADATA LEGEND 900 2 TB (`adata-legend-900-2tb`) | SLEG-900-2TCS | La tabla oficial confirma exactamente SLEG-900-2TCS (2TB); el documento oficial de compatibilidad PS5 de LEGEND 900 indica disipador opcional. https://www.adata.com/storage/downloadfile/LEGEND-900-Compatible-with-PS5-Illustration_20230613.pdf |
| ADATA LEGEND 900 512 GB (`adata-legend-900-512gb`) | SLEG-900-512GCS | La tabla oficial confirma exactamente SLEG-900-512GCS (512GB); el documento oficial de compatibilidad PS5 de LEGEND 900 indica disipador opcional. https://www.adata.com/storage/downloadfile/LEGEND-900-Compatible-with-PS5-Illustration_20230613.pdf |
| ADATA XPG GAMMIX S60 2 TB (`adata-xpg-gammix-s60-2tb`) | AGAMMIXS60-2T-CS | La tabla oficial confirma exactamente AGAMMIXS60-2T-CS (2TB); el documento oficial de GAMMIX S60 indica disipador opcional. https://www.adata.com/storage/downloadfile/GAMMIX-S60-Compatible-with-PS5-Illustration_20231128.pdf |
| ADATA XPG GAMMIX S60 512 GB (`adata-xpg-gammix-s60-512gb`) | AGAMMIXS60-512G-CS | La tabla oficial confirma exactamente AGAMMIXS60-512G-CS (512GB); el documento oficial de GAMMIX S60 indica disipador opcional. https://www.adata.com/storage/downloadfile/GAMMIX-S60-Compatible-with-PS5-Illustration_20231128.pdf |
| ADATA XPG MARS 980 BLADE 2 TB (SMAR-980B-2TCS) (`adata-xpg-mars-980-blade-2tb`) | SMAR-980B-2TCS | Ficha oficial para SMAR-980B-2TCS: versión BLADE para laptop, dimensiones de unidad sin disipador y heatsink descrito como opcional. |
| Apacer AS2280Q4U 1 TB (AP1TBAS2280Q4U-1) (`apacer-as2280q4u-1tb`) | AP1TBAS2280Q4U-1 | La hoja oficial lista el P/N exacto AP1TBAS2280Q4U-1 y distingue dimensiones del SSD y “With heat sink”. La guía oficial https://www.apacer.com/upload/media/download/Personal-biz/User%20Manual/AS2280Q4U_Quick%20Guide_220224.pdf especifica que el disipador va incluido como pieza separada que debe ensamblarse/desensamblarse; por eso no está integrado en el SSD. |
| Corsair MP600 MINI 1 TB (CSSD-F1000GBMP600MNR2) (`corsair-mp600-mini-1tb-cssd-f1000gbmp600mnr2`) | CSSD-F1000GBMP600MNR2 | En la ficha del SKU CSSD-F1000GBMP600MNR2, las especificaciones técnicas declaran explícitamente “Heatsink: N/A” y “Heatsink Color: N/A”. |
| Corsair MP600 PRO NH 2 TB (CSSD-F2000GBMP600PNH) (`corsair-mp600-pro-nh-2tb-cssd-f2000gbmp600pnh`) | CSSD-F2000GBMP600PNH | La guía oficial de CORSAIR indica expresamente que el MP600 PRO NH no se envía con heatspreader; la página de producto enlazada por la familia identifica el SKU CSSD-F2000GBMP600PNH. |
| Kioxia EXCERIA BASIC 1 TB (LSF10Z001TG8) (`kioxia-exceria-basic-1-tb-lsf10z001tg8`) | LSF10Z001TG8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |
| Kioxia EXCERIA BASIC 2 TB (LSF10Z002TG8) (`kioxia-exceria-basic-2-tb-lsf10z002tg8`) | LSF10Z002TG8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |
| Kioxia EXCERIA G3 1 TB (`kioxia-exceria-g3-1tb`) | LVC10Z001TG8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |
| Kioxia EXCERIA G3 2 TB (`kioxia-exceria-g3-2tb`) | LVC10Z002TG8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |
| Kioxia EXCERIA PLUS G3 1 TB (`kioxia-exceria-plus-g3-1tb`) | LSD10Z001TG8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |
| Kioxia EXCERIA PLUS G3 2 TB (`kioxia-exceria-plus-g3-2tb`) | LSD10Z002TG8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |
| Kioxia EXCERIA PLUS G4 1 TB (`kioxia-exceria-plus-g4-1tb`) | LVD10Z001TG8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |
| Kioxia EXCERIA PRO G2 1 TB (`kioxia-exceria-pro-g2-1tb`) | LVE10Z1T02G8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |
| Kioxia EXCERIA PRO G2 4 TB (`kioxia-exceria-pro-g2-4tb`) | LVE10Z4T09G8 | KIOXIA identifica el SKU dentro de una gama sin disipador: distingue expresamente «EXCERIA with Heatsink» como línea aparte, primera de la marca con heatsink; este modelo pertenece a otra serie. |

## Valores No retirados

Quité 19 valores `No` cuando las fuentes solo mostraban dimensiones, fotografías o falta de mención, insuficientes para confirmar que el SKU exacto carece de disipador: Apacer AS2280P4 1 TB y AS2280Q4L 512 GB; Corsair MP600 CORE XT 1 TB, 2 TB (dos SKU) y 4 TB; Gigabyte Gen4 4000E 500 GB y 1 TB; Kingston NV3 500 GB/1/2/4 TB 2280 y 500 GB/1/2 TB 2230; MSI SPATIUM M461 1 TB y 2 TB; SanDisk Optimus 5100 500 GB y 4 TB. Sus huecos quedan restaurados.

## Productos pendientes

| Modelo | Huecos efectivos |
|---|---|
| Apacer AS2280P4 1 TB (AP1TBAS2280P4-1) (`apacer-as2280p4-1tb`) | Disipador integrado |
| Apacer AS2280Q4L 512 GB (`apacer-as2280q4l-512gb`) | Disipador integrado |
| Corsair MP600 CORE XT 1 TB (CSSD-F1000GBMP600CXTR2) (`corsair-mp600-core-xt-1tb-cssd-f1000gbmp600cxtr2`) | Disipador integrado |
| Corsair MP600 CORE XT 2 TB (CSSD-F2000GBMP600CXT) (`corsair-mp600-core-xt-2tb-cssd-f2000gbmp600cxt`) | Disipador integrado |
| Corsair MP600 CORE XT 2 TB (CSSD-F2000GBMP600CXTR2) (`corsair-mp600-core-xt-2tb-cssd-f2000gbmp600cxtr2`) | Disipador integrado |
| Corsair MP600 CORE XT 4 TB (CSSD-F4000GBMP600CXT) (`corsair-mp600-core-xt-4tb-cssd-f4000gbmp600cxt`) | Disipador integrado |
| Crucial E100 1 TB (CT1000E100SSD8) (`crucial-ct1000e100ssd8`) | Disipador integrado |
| Crucial E100 2 TB (CT2000E100SSD8) (`crucial-ct2000e100ssd8`) | Disipador integrado |
| Crucial P3 Plus 2 TB M.2 2280 (CT2000P3PSSD8) (`crucial-ct2000p3pssd8`) | Disipador integrado |
| Crucial E100 480 GB (CT480E100SSD8) (`crucial-ct480e100ssd8`) | Disipador integrado |
| Dell 480GB M.2 SSD SATA 512n 6Gbps S3520 (400-AVSS) (`dell-400-avss`) | Formato, Lectura secuencial |
| Forgeon Nimbus 1 TB (FO-SSD-N-1TB-6) (`forgeon-nimbus-1tb-fo-ssd-n-1tb-6`) | Disipador integrado |
| GIGABYTE Gen4 4000E SSD 1 TB (G440E1TB) (`gigabyte-gen4-4000e-1tb-g440e1tb`) | Disipador integrado |
| GIGABYTE Gen4 4000E SSD 500 GB (G440E500G) (`gigabyte-gen4-4000e-500gb-g440e500g`) | Disipador integrado |
| GOODRAM PX600 1 TB (SSDPR-PX600-1K0-80) (`goodram-px600-1tb-ssdpr-px600-1k0-80`) | Disipador integrado |
| GOODRAM PX600 500 GB (SSDPR-PX600-500-80) (`goodram-px600-500gb-ssdpr-px600-500-80`) | Disipador integrado |
| Intenso MI500 2 TB (3836470) (`intenso-mi500-2tb-3836470`) | Disipador integrado |
| Intenso MI500 500 GB (3836450) (`intenso-mi500-500gb-3836450`) | Disipador integrado |
| Intenso Top SSD M.2 SATA III 1 TB (3832460) (`intenso-top-m2-sata-ssd-1-tb-3832460`) | Disipador integrado |
| Intenso Top SSD M.2 SATA III 256 GB (3832440) (`intenso-top-m2-sata-ssd-256-gb-3832440`) | Disipador integrado |
| Intenso Top SSD M.2 SATA III 512 GB (3832450) (`intenso-top-m2-sata-ssd-512-gb-3832450`) | Disipador integrado |
| Kingston NV3 1 TB (`kingston-nv3-1tb`) | Disipador integrado |
| Kingston NV3 M.2 2230 1 TB (`kingston-nv3-2230-1tb`) | Disipador integrado |
| Kingston NV3 M.2 2230 2 TB (`kingston-nv3-2230-2tb`) | Disipador integrado |
| Kingston NV3 M.2 2230 500 GB (`kingston-nv3-2230-500gb`) | Disipador integrado |
| Kingston NV3 2 TB (`kingston-nv3-2tb`) | Disipador integrado |
| Kingston NV3 4 TB (`kingston-nv3-4tb`) | Disipador integrado |
| Kingston NV3 500 GB (`kingston-nv3-500gb`) | Disipador integrado |
| Lexar NM990 1 TB (`lexar-nm990-1tb`) | Disipador integrado |
| Lexar NQ790 512 GB (`lexar-nq790-512gb`) | Disipador integrado |
| MSI SPATIUM M461 1 TB (S78-440L1D0-P83) (`msi-spatium-m461-1-tb-s78-440l1d0-p83`) | Disipador integrado |
| MSI SPATIUM M461 2 TB (S78-440Q550-P83) (`msi-spatium-m461-2-tb-s78-440q550-p83`) | Disipador integrado |
| PNY CS1030 1 TB (M280CS1030-1TB-RB) (`pny-cs1030-1-tb-m280cs1030-1tb-rb`) | Disipador integrado |
| PNY CS1030 250 GB (M280CS1030-250-RB) (`pny-cs1030-250-gb-m280cs1030-250-rb`) | Disipador integrado |
| PNY CS1030 500 GB (M280CS1030-500-RB) (`pny-cs1030-500-gb-m280cs1030-500-rb`) | Disipador integrado |
| PNY CS2140 1 TB (M280CS2140-1TB-RB) (`pny-cs2140-1tb-m280cs2140-1tb-rb`) | Disipador integrado |
| PNY CS2230 500 GB (M280CS2230-500-RB) (`pny-cs2230-500gb-m280cs2230-500-rb`) | Disipador integrado |
| SanDisk NAS 800 NVMe SSD 3,84 TB (`sandisk-nas-800-3840gb`) | Disipador integrado |
| SanDisk Optimus 5100 4 TB (`sandisk-optimus-5100-4tb`) | Disipador integrado |
| SanDisk Optimus 5100 500 GB (`sandisk-optimus-5100-500gb`) | Disipador integrado |
| TEAMGROUP MP33 256 GB (TM8FP6256G0C101) (`teamgroup-mp33-256gb-tm8fp6256g0c101`) | Disipador integrado |
| TEAMGROUP MS30 512 GB (TM8PS7512G0C101) (`teamgroup-ms30-512gb-tm8ps7512g0c101`) | Disipador integrado |

No se conserva ningún `not_published` en estas filas. Se retiraron las tres marcas Dell de lectura secuencial: las páginas oficiales exactas 345-BDQM, 345-BJPR y 400-AVSS se revisaron y publican interfaz/capacidad, pero eso solo prueba que esos datos no aparecen en esas páginas; no confirma que Dell no publique la velocidad en otra ficha. Esos campos quedan pendientes.
