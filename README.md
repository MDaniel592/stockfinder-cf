# StockFinder V2

Comparador de componentes: React/Vite, API Cloudflare Workers, catálogo público en D1 y configurador/ofertas en Supabase. PostgreSQL del NAS es la fuente de verdad.

[Web](https://www.stockfinder.workers.dev/) · [Continuar trabajo](docs/HANDOFF.md) · [Operación](docs/runbooks/operations.md) · [Catálogo](docs/catalog.md) · [PcComponentes](docs/pccomponentes.md)

## Desarrollo

Node 22+, Python 3.13 y Docker Compose. En el NAS, compilar y desplegar con `bash scripts/deploy.sh` (Node en Docker).

```bash
npm ci
npm run db:local
python3 scripts/generate_catalog_local.py
npx wrangler d1 execute DB --local --file artifacts/catalog-local.sql
npm run build
npm run dev
```

Local: `http://127.0.0.1:8787`. Verificar con `npm test`, `npm run typecheck` y `npm run build`.

## API

Rutas GET bajo `/api/v1/`: `products`, `products/:slug`, `products/:slug/offers`, `categories`, `shops`, `brands`, `catalog/filters`, `catalog/highlights`, `catalog/deals`, `builder/options`, `builder/products/:slug`, `builder/state`, `catalog/status`, `products/:slug/price-history`, `builds/:id`.

El listado admite búsqueda, categoría, marca, especificaciones, stock, precios, orden y paginación (máximo 48). Contratos: `packages/api-contracts`. Ingesta privada: `POST /internal/sync`. Reportes de error: `POST /api/v1/reports` (público, limitado) y `POST /internal/reports/pull` (firmado, recogida por `nas/reports_pull.py`).

Listas y capturas se guardan en el navegador y se exportan/importan como JSON; no hay cuentas. El configurador comprueba reglas básicas, sin certificar BIOS/QVL ni montaje completo. Los precios desconocidos no cuentan como cero.

POST /api/v1/builds guarda configuraciones compartidas en Supabase; D1 0008 se conserva para compatibilidad y rollback. La proyección de desarrollo anterior usa lotes y ofertas piloto; no equivale al estado vivo ni aplica todas las correcciones del publicador. Para revisar el NAS, usar la vista previa local descrita en docs/runbooks/operations.md.

El configurador y el [ranking de 20 bajadas](https://www.stockfinder.workers.dev/ofertas) usan Supabase como réplica del NAS, sin consultas D1. El catálogo general continúa en D1. Arquitectura, permisos y recuperación: [Supabase](docs/supabase-migration.md). Para desarrollar estas funciones se necesita FEATURES_KEY en las variables locales; las pruebas automatizadas usan servicios simulados o PostgreSQL desechable.
