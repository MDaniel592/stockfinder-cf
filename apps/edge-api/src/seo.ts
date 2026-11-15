// Server-side SEO: HTML shell rewriting, dynamic sitemap.xml and robots.txt.
import { stmt, type Db } from './db';
import { productMeta, sitemapRows } from './pg';

interface SeoEnv { db: Db; ASSETS: Fetcher; }

const SITE = 'StockFinder';
const CSP = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'";
const STATIC_PAGES: Record<string, { title: string; description: string }> = {
  '/ofertas': { title:'Las 20 mayores bajadas de precio | StockFinder', description:'Hasta 20 componentes con las mayores bajadas porcentuales verificadas en las últimas 24 horas y sus precios actuales.' },
  '/configurador': { title: 'Configurador de PC | StockFinder', description: 'Monta tu PC pieza a pieza: comprueba la compatibilidad y compara el precio total en tiendas españolas.' },
  '/acerca': { title: 'Cómo funciona StockFinder', description: 'Qué es StockFinder, de dónde salen las especificaciones y los precios, y cómo se mantienen actualizados.' },
  '/privacidad': { title: 'Privacidad | StockFinder', description: 'Política de privacidad de StockFinder: qué datos se tratan y cuáles no.' }
};
export const PAGE_PATHS = new Set(['/', '/configurador', '/ofertas', '/acerca', '/privacidad']);
export const isPagePath = (path: string) => PAGE_PATHS.has(path) || /^\/(p|c)\/[^/]*$/.test(path);

export const escapeHtml = (value: string) => value.replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
export const escapeXml = escapeHtml;
/** JSON safe to embed inside a <script> element: never contains `<`, `>`, `&`, U+2028 or U+2029 literally. */
export const safeJson = (value: unknown) => JSON.stringify(value)
  .replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
const truncate = (text: string, max: number) => text.length <= max ? text : `${text.slice(0, max - 1).trimEnd()}…`;
const euros = (cents: number) => (cents / 100).toFixed(2);
const activeShop = `EXISTS(SELECT 1 FROM catalog_shops s WHERE s.id=o.shop_id AND s.deleted_at IS NULL)`;
const activeCategory = `p.category_id IN (SELECT id FROM catalog_categories WHERE deleted_at IS NULL)`;

interface Meta { title: string; description: string; path: string; ogType: string; image?: string; jsonLd?: unknown; noscript?: string; notFound?: boolean; }

async function describe(path: string, env: SeoEnv, origin: string): Promise<Meta> {
  const notFound: Meta = { title: `Página no encontrada | ${SITE}`, description: 'La página que buscas no existe.', path, ogType: 'website', notFound: true };
  if (path === '/') return { title: '', description: '', path, ogType: 'website' };
  if (STATIC_PAGES[path]) return { ...STATIC_PAGES[path], path, ogType: 'website' };
  const [, kind, rawSlug] = path.split('/');
  let slug: string;
  try { slug = decodeURIComponent(rawSlug ?? ''); } catch { return notFound; }
  if (!/^[a-zA-Z0-9_-]{1,100}$/.test(slug)) return notFound;
  if (kind === 'c') {
    const category = await env.db.first<{ name: string }>('SELECT name FROM catalog_categories WHERE (slug=? OR id=?) AND deleted_at IS NULL', slug, slug);
    if (!category?.name) return notFound;
    const name = category.name;
    return { title: `${name} · Compara precios y especificaciones | ${SITE}`, path, ogType: 'website',
      description: `Compara precios y especificaciones de ${name.toLowerCase()} en tiendas españolas. Filtra por marca, tienda y características.`,
      noscript: `<h1>${escapeHtml(name)}</h1><p>Compara precios y especificaciones de ${escapeHtml(name.toLowerCase())} en tiendas españolas. Activa JavaScript para usar el catálogo.</p>` };
  }
  // Postgres: product + offer range in one statement (Hyperdrive counts statements).
  const meta = env.db.pg ? await productMeta(env.db, slug, activeCategory) : null;
  const product = env.db.pg ? meta as Record<string, string | null> | null : await env.db.first<Record<string, string | null>>(`SELECT p.id,p.name,p.brand,p.mpn,p.specs,p.image_path FROM catalog_products p
    WHERE p.slug=? AND p.deleted_at IS NULL AND ${activeCategory}`, slug);
  if (!product?.name) return notFound;
  const offers = env.db.pg ? meta as unknown as { low: number | null; high: number | null; n: number } : await env.db.first<{ low: number | null; high: number | null; n: number }>(`SELECT MIN(o.price_cents) AS low,MAX(o.price_cents) AS high,COUNT(*) AS n FROM catalog_offers o
    WHERE o.product_id=? AND o.deleted_at IS NULL AND o.in_stock=1 AND ${activeShop}`, product.id);
  let specs: Record<string, string> = {};
  try { const parsed = JSON.parse(product.specs ?? '{}'); if (parsed && typeof parsed === 'object') specs = parsed; } catch { /* keep empty */ }
  const hasOffers = !!offers && offers.n > 0 && offers.low !== null && offers.high !== null;
  const image = product.image_path && product.image_path.startsWith('/') ? `${origin}${product.image_path}` : undefined;
  const ld: Record<string, unknown> = { '@context': 'https://schema.org', '@type': 'Product', name: product.name,
    ...(product.brand ? { brand: { '@type': 'Brand', name: product.brand } } : {}),
    ...(product.mpn ? { mpn: product.mpn } : {}), ...(image ? { image } : {}), url: `${origin}${path}` };
  if (hasOffers) ld.offers = { '@type': 'AggregateOffer', lowPrice: euros(offers!.low!), highPrice: euros(offers!.high!),
    priceCurrency: 'EUR', offerCount: offers!.n, availability: 'https://schema.org/InStock' };
  const price = hasOffers ? ` Desde ${euros(offers!.low!).replace('.', ',')} € en ${offers!.n} ${offers!.n === 1 ? 'tienda' : 'tiendas'}.` : '';
  const specRows = Object.entries(specs).map(([k, v]) => `<li>${escapeHtml(k)}: ${escapeHtml(String(v))}</li>`).join('');
  return { title: `${product.name} · Precios y especificaciones | ${SITE}`, path, ogType: 'product', image, jsonLd: ld,
    description: truncate(`${product.name}${product.brand ? ` de ${product.brand}` : ''}: especificaciones y comparación de precios en tiendas españolas.${price}`, 200),
    noscript: `<h1>${escapeHtml(product.name)}</h1>${product.brand ? `<p>Marca: ${escapeHtml(product.brand)}</p>` : ''}${product.mpn ? `<p>Referencia (MPN): ${escapeHtml(product.mpn)}</p>` : ''}${specRows ? `<h2>Especificaciones</h2><ul>${specRows}</ul>` : ''}<p>Activa JavaScript para ver los precios actualizados.</p>` };
}

async function shell(request: Request, env: SeoEnv, origin: string) {
  let response = await env.ASSETS.fetch(new Request(`${origin}/index.html`, { headers: request.headers, redirect: 'manual' }));
  if (response.status >= 300 && response.status < 400) {
    const location = response.headers.get('location');
    response = await env.ASSETS.fetch(new Request(new URL(location ?? '/', origin).toString(), { headers: request.headers }));
  }
  return response;
}

export async function servePage(request: Request, env: SeoEnv, url: URL): Promise<Response> {
  const origin = url.origin;
  const meta = await describe(url.pathname, env, origin);
  const asset = await shell(request, env, origin);
  if (!asset.ok) return asset;
  const out = new Headers(asset.headers);
  out.delete('content-length'); out.delete('etag'); out.delete('content-encoding');
  out.set('Content-Type', 'text/html; charset=utf-8');
  out.set('Cache-Control', meta.notFound ? 'public, max-age=60' : 'public, max-age=300');
  if (!out.has('Content-Security-Policy')) out.set('Content-Security-Policy', CSP);
  if (!out.has('X-Content-Type-Options')) out.set('X-Content-Type-Options', 'nosniff');
  if (!out.has('Referrer-Policy')) out.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  if (!out.has('X-Frame-Options')) out.set('X-Frame-Options', 'DENY');
  const canonical = `${origin}${url.pathname}`;
  const home = url.pathname === '/';
  // HTMLRewriter does not escape `&`, `<` or `>` inside attribute values, so escape them here.
  const attr = (value: string) => ({ element(el: Element) { el.setAttribute('content', escapeHtml(value)); } });
  let rewriter = new HTMLRewriter();
  if (!home) {
    rewriter = rewriter
      .on('title', { element(el) { el.setInnerContent(meta.title); } })
      .on('meta[name="description"]', attr(meta.description))
      .on('meta[property="og:title"]', attr(meta.title))
      .on('meta[property="og:description"]', attr(meta.description))
      .on('meta[property="og:type"]', attr(meta.ogType))
      .on('meta[name="twitter:title"]', attr(meta.title))
      .on('meta[name="twitter:description"]', attr(meta.description));
  }
  rewriter = rewriter
    .on('link[rel="canonical"]', { element(el) { if (!meta.notFound) el.setAttribute('href', escapeHtml(canonical)); else el.remove(); } })
    .on('meta[property="og:url"]', { element(el) { el.setAttribute('content', escapeHtml(canonical)); } });
  for (const selector of ['meta[property="og:image"]', 'meta[name="twitter:image"]']) {
    rewriter = rewriter.on(selector, { element(el) {
      const current = el.getAttribute('content') ?? '';
      el.setAttribute('content', escapeHtml(meta.image ?? current.replace(/^https?:\/\/[^/]+/, origin)));
    } });
  }
  rewriter = rewriter.on('script[type="application/ld+json"]', { element(el) {
    // Existing WebSite JSON-LD: keep it but point it at the serving origin.
    el.setInnerContent(safeJson({ '@context': 'https://schema.org', '@type': 'WebSite', name: SITE, url: `${origin}/`, inLanguage: 'es',
      description: 'Comparador de componentes de PC y precios en tiendas.' }), { html: true });
  } });
  if (meta.notFound) rewriter = rewriter.on('meta[name="robots"]', { element(el) { el.setAttribute('content', 'noindex'); } });
  if (meta.jsonLd) rewriter = rewriter.on('head', { element(el) {
    el.append(`<script type="application/ld+json">${safeJson(meta.jsonLd)}</script>`, { html: true }); } });
  if (meta.noscript) rewriter = rewriter.on('noscript', { element(el) { el.setInnerContent(meta.noscript!, { html: true }); } });
  return rewriter.transform(new Response(request.method === 'HEAD' ? null : asset.body, { status: meta.notFound ? 404 : 200, headers: out }));
}

const xmlHeaders = (type: string) => ({ 'Content-Type': type, 'Cache-Control': 'public, max-age=3600', 'X-Content-Type-Options': 'nosniff' });
export function robots(url: URL) {
  return new Response(`User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /internal/\n\nSitemap: ${url.origin}/sitemap.xml\n`, { headers: xmlHeaders('text/plain; charset=utf-8') });
}
type Slug = { slug: string; changed_at: string | null };
const PLAIN = /^[A-Za-z0-9_-]*$/;
const SQL_LASTMOD = `CASE WHEN changed_at IS NULL OR changed_at='' THEN '' ELSE '<lastmod>'||substr(changed_at,1,10)||'</lastmod>' END`;
const SQL_ODD = `(slug GLOB '*[^A-Za-z0-9_-]*' OR COALESCE(substr(changed_at,1,10),'') GLOB '*[^A-Za-z0-9_-]*')`;
/** D1: the <url> entries concatenated by SQLite (two string rows instead of thousands of row objects to deserialise
 * and format in the isolate). null when a slug/date would need escaping (the contract forbids it) or on any error. */
async function sitemapEntriesD1(env: SeoEnv, origin: string): Promise<string | null> {
  const part = (kind: 'c' | 'p', from: string) => stmt(`SELECT group_concat(e,'') AS body,SUM(odd) AS odd FROM (SELECT
    '<url><loc>'||?||'/${kind}/'||slug||'</loc>'||${SQL_LASTMOD}||'</url>' AS e,${SQL_ODD} AS odd FROM ${from} ORDER BY slug LIMIT 50000)`, origin);
  try {
    const parts = await env.db.reads([part('c', 'catalog_categories WHERE deleted_at IS NULL AND slug IS NOT NULL'),
      part('p', `catalog_products p WHERE p.deleted_at IS NULL AND p.slug IS NOT NULL AND ${activeCategory}`)]);
    const rows = parts.map(r => r[0] as { body: string | null; odd: number | null } | undefined);
    if (rows.some(r => !r || Number(r.odd ?? 0) > 0)) return null;
    return rows.map(r => r!.body ?? '').join('');
  } catch { return null; }
}
export async function sitemap(env: SeoEnv, url: URL) {
  const head = `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">`;
  const fixed = [entryXml(escapeXml(url.origin), '/'), ...Object.keys(STATIC_PAGES).map(p => entryXml(escapeXml(url.origin), p))].join('');
  const built = env.db.pg ? null : await sitemapEntriesD1(env, escapeXml(url.origin));
  if (built !== null) return new Response(`${head}${fixed}${built}</urlset>\n`, { headers: xmlHeaders('application/xml; charset=utf-8') });
  const pgRows = env.db.pg ? await sitemapRows(env.db, activeCategory) : null;
  const [categories, products] = pgRows ? [pgRows.filter(r => r.kind === 'c'), pgRows.filter(r => r.kind === 'p')] : await env.db.reads([
    stmt('SELECT slug,changed_at FROM catalog_categories WHERE deleted_at IS NULL AND slug IS NOT NULL ORDER BY slug LIMIT 50000'),
    stmt(`SELECT p.slug,p.changed_at FROM catalog_products p WHERE p.deleted_at IS NULL AND p.slug IS NOT NULL AND ${activeCategory} ORDER BY p.slug LIMIT 50000`)
  ]);
  // Slugs and ISO dates are nearly always plain [A-Za-z0-9_-], for which encodeURIComponent and XML escaping are no-ops.
  const origin = escapeXml(url.origin);
  const segment = (slug: string) => PLAIN.test(slug) ? slug : escapeXml(encodeURIComponent(slug));
  let body = fixed;
  for (const c of categories as Slug[]) body += entryXml(origin, `/c/${segment(c.slug)}`, c.changed_at);
  for (const p of products as Slug[]) body += entryXml(origin, `/p/${segment(p.slug)}`, p.changed_at);
  return new Response(`${head}${body}</urlset>\n`, { headers: xmlHeaders('application/xml; charset=utf-8') });
}
/** One <url> entry; `origin` and `path` already escaped. */
function entryXml(origin: string, path: string, lastmod?: string | null) {
  const day = lastmod ? lastmod.slice(0, 10) : '';
  return `<url><loc>${origin}${path}</loc>${day ? `<lastmod>${PLAIN.test(day) ? day : escapeXml(day)}</lastmod>` : ''}</url>`;
}
