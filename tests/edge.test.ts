import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { createHmac, randomUUID } from 'node:crypto';
import { dbMode, testWorker } from './helpers/worker-db';

const secret = 'synthetic-test-secret-not-for-deployment-1234';
// Stand-in for the static assets binding: the SPA shell, as built by Vite (same head tags as apps/web/index.html).
const shell = `<!doctype html>
<html lang="es"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>StockFinder — Compara componentes de PC y precios en tiendas</title>
<meta name="description" content="Compara especificaciones y precios."/>
<meta name="robots" content="index,follow,max-image-preview:large"/>
<link rel="canonical" href="https://www.stockfinder.workers.dev/"/>
<meta property="og:type" content="website"/><meta property="og:title" content="StockFinder"/>
<meta property="og:description" content="Especificaciones y precios."/>
<meta property="og:url" content="https://www.stockfinder.workers.dev/"/><meta property="og:image" content="https://www.stockfinder.workers.dev/images/pc-hero.png"/>
<meta name="twitter:title" content="StockFinder"/><meta name="twitter:description" content="Especificaciones y precios."/>
<meta name="twitter:image" content="https://www.stockfinder.workers.dev/images/pc-hero.png"/>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebSite","name":"StockFinder","url":"https://www.stockfinder.workers.dev/"}</script>
<script type="module" crossorigin src="/assets/index.js"></script>
</head><body><noscript><h1>StockFinder</h1><p>Activa JavaScript.</p></noscript><div id="root"></div></body></html>`;
const assets = async (request: Request) => new URL(request.url).pathname === '/index.html'
  ? new Response(shell, { headers: { 'content-type': 'text/html' } }) : new Response('asset', { status: 200 });
// D1 by default; Postgres (fresh database, Hyperdrive binding) when TEST_PG_URL is set — see tests/helpers/worker-db.ts.
const main = testWorker({ serviceBindings: { ASSETS: assets }, bindings: { SYNC_SECRET: secret, DATA_MODE: 'synthetic' } });
const mf = main.mf;
const now = new Date().toISOString();
function event(entity: string, entity_id: string, data: unknown, version = 1, operation = 'upsert') {
  return { event_id: ++eventId, entity, entity_id, version, operation, changed_at: now, ...(operation === 'upsert' ? { data } : {}) };
}
let eventId = 0;
function signed(events: unknown[], nonce = randomUUID(), timestamp = String(Math.floor(Date.now()/1000))) {
  const body = JSON.stringify({ batch_id: randomUUID(), schema_version: 1, events });
  const signature = createHmac('sha256', secret).update(`${timestamp}.${nonce}.${body}`).digest('hex');
  return { method: 'POST', body, headers: { 'content-type': 'application/json', 'x-sync-timestamp': timestamp,
    'x-sync-nonce': nonce, 'x-sync-signature': signature } };
}
const send = (events: unknown[]) => mf.dispatchFetch('https://test/internal/sync', signed(events));
const get = async (path: string) => { const response = await mf.dispatchFetch(`https://test/api/v1/${path}`, { headers: { 'cache-control': 'no-cache' } }); return { response, body: await response.json() as any }; };
const product = { slug: 'test-gpu', name: 'GPU Test', category_id: 'gpu', brand: 'Test', specs: { VRAM: '16 GB' } };
beforeAll(() => main.migrate(), 30000);
afterAll(() => main.dispose());
describe.sequential('Edge read model and authenticated sync', () => {
  test('rejects unauthenticated writes and stale signatures', async () => {
    expect((await mf.dispatchFetch('https://test/internal/sync', { method: 'POST' })).status).toBe(401);
    expect((await mf.dispatchFetch('https://test/internal/sync', signed([], randomUUID(), '1000000000'))).status).toBe(401);
  });
  test('initial catalogue, pagination, filters and detail', async () => {
    expect((await send([
      event('category', 'gpu', { slug: 'gpu', name: 'Gráficas' }),
      event('shop', 'shop', { name: 'Synthetic Shop', url_public: 'https://example.com' }),
      event('product', 'gpu-1', product),
      event('offer', 'offer-1', { product_id: 'gpu-1', shop_id: 'shop', price_cents: 50000, in_stock: true, last_checked_at: now, url_public: 'https://example.com/gpu' })
    ])).status).toBe(200);
    const { body } = await get('products?category=gpu&stock=1&sort=price&limit=1');
    expect(body.total).toBe(1); expect(body.data[0].min_price_cents).toBe(50000);
    expect((await get('products/test-gpu')).body.data.specs).toEqual({ VRAM: '16 GB' });
    expect((await get('products/test-gpu/offers')).body.data[0].shop_name).toBe('Synthetic Shop');
    expect((await get('products?q=missing')).body.total).toBe(0);
    for (const query of ['page=0','limit=49','sort=toString','sort=__proto__','unknown=1']) expect((await get(`products?${query}`)).response.status).toBe(400);
    expect((await get('products?q=%25%25')).body.total).toBe(0);
    // Abuse limits: one-character search, repeated/invalid category, offset past 1,200 rows, parameters where none exist.
    for (const query of ['q=%25', 'q=x', 'category=gpu&category=gpu', 'category=bad%20id', 'page=26&limit=48', 'page=51'])
      expect((await get(`products?${query}`)).response.status, query).toBe(400);
    expect((await get('products?page=25&limit=48')).response.status).toBe(200);
    for (const path of ['brands?x=1', 'categories?x=1', 'shops?x=1', 'catalog/filters?category=gpu&category=gpu'])
      expect((await get(path)).response.status, path).toBe(400);
    for (const path of ['/sitemap.xml?x=1', '/robots.txt?x=1']) expect((await mf.dispatchFetch(`https://test${path}`)).status, path).toBe(400);
    // Postgres also rejects categories that do not exist (they could only fill the edge cache).
    if (dbMode === 'pg') for (const path of ['products?category=nope', 'catalog/filters?category=nope'])
      expect((await get(path)).response.status, path).toBe(400);
  });
  test('GPU chip facets and exact variant filtering', async () => {
    const chips: Record<string, string> = { 'chip-base': 'GeForce RTX 5060', 'chip-ti-a': 'GeForce RTX 5060 Ti',
      'chip-ti-b': 'GeForce RTX 5060 Ti', 'chip-amd': 'Radeon RX 9070 XT' };
    expect((await send(Object.entries(chips).map(([id, chip]) => event('product', id, {
      ...product, slug: id, name: id, specs: { 'Chip gráfico': chip, Memoria: '16 GB', Arquitectura: 'Test', Interfaz: 'PCIe 4.0' }
    })))).status).toBe(200);
    try {
      const facets = (await get('catalog/filters?category=gpu')).body.data.specs;
      expect(facets.some((f: any) => ['Arquitectura', 'Interfaz'].includes(f.key))).toBe(false);
      const facet = facets.find((f: any) => f.key === 'Chip gráfico');
      expect(facet.options).toEqual(expect.arrayContaining([{ value: 'GeForce RTX 5060 Ti', count: 2 }, { value: 'GeForce RTX 5060', count: 1 }]));
      const query = (values: string[]) => 'products?category=gpu&specs=' + encodeURIComponent(JSON.stringify({ 'Chip gráfico': values }));
      const selected = (await get(query(['GeForce RTX 5060 Ti']))).body;
      expect(selected.total).toBe(2);
      expect(selected.data.every((p: any) => p.specs['Chip gráfico'] === 'GeForce RTX 5060 Ti')).toBe(true);
      expect((await get(query(['GeForce RTX 5060 Ti', 'Radeon RX 9070 XT']))).body.total).toBe(3);
    } finally {
      expect((await send(Object.keys(chips).map(id => event('product', id, undefined, 2, 'delete')))).status).toBe(200);
    }
  });
  test('list-valued specs: token facets and delimited, contains-any matching', async () => {
    const lists: Record<string, string> = { 'list-itx': 'Micro-ATX, Mini-ITX', 'list-atx': 'ATX, Micro-ATX, Mini-ITX', 'list-eatx': 'E-ATX, ATX, Micro-ATX, Mini-ITX', 'list-micro': 'Micro-ATX', 'list-eatx-only': 'E-ATX' };
    expect((await send([
      event('category', 'case', { slug: 'case', name: 'Cajas' }),
      ...Object.entries(lists).map(([id, formats]) => event('product', id, { ...product, slug: id, name: id, category_id: 'case', specs: { 'Formatos compatibles': formats, 'Panel lateral': 'Cristal' } })),
      event('product', 'list-none', { ...product, slug: 'list-none', name: 'list-none', category_id: 'case', specs: { 'Panel lateral': 'Cristal' } }),
    ])).status).toBe(200);
    const facets = (await get('catalog/filters?category=case')).body.data.specs;
    const formats = facets.find((facet: any) => facet.key === 'Formatos compatibles');
    expect(formats.multi).toBe(true);
    expect(formats.options).toEqual([{ value: 'E-ATX', count: 2 }, { value: 'ATX', count: 2 }, { value: 'Micro-ATX', count: 4 }, { value: 'Mini-ITX', count: 3 }]);
    expect(facets.find((facet: any) => facet.key === 'Panel lateral').multi).toBeUndefined();
    const ids = async (selected: unknown) => (await get(`products?category=case&sort=name&specs=${encodeURIComponent(JSON.stringify({ 'Formatos compatibles': selected }))}`)).body.data.map((p: any) => p.id);
    expect(await ids(['ATX'])).toEqual(['list-atx', 'list-eatx']);                       // not Micro-ATX, not E-ATX
    expect(await ids(['E-ATX'])).toEqual(['list-eatx', 'list-eatx-only']);
    expect(await ids(['Mini-ITX'])).toEqual(['list-atx', 'list-eatx', 'list-itx']);
    expect(await ids(['E-ATX', 'Micro-ATX'])).toEqual(['list-atx', 'list-eatx', 'list-eatx-only', 'list-itx', 'list-micro']); // OR
    expect(await ids(["' OR 1=1 --"])).toEqual([]);
    expect((await get(`products?category=case&specs=${encodeURIComponent(JSON.stringify({ 'Formatos compatibles': ['ATX, Micro-ATX'] }))}`)).response.status).toBe(400);
    const both = encodeURIComponent(JSON.stringify({ 'Formatos compatibles': ['Micro-ATX'], 'Panel lateral': ['Cristal'] }));
    expect((await get(`products?category=case&specs=${both}`)).body.total).toBe(4);
    await send(Object.keys(lists).concat('list-none').map(id => event('product', id, null, 2, 'delete')));
  });
  test('category facets, combined filters, price ranges and pagination cover the full catalogue', async () => {
    const ids = ['facet-12', 'facet-16', 'facet-unpriced', 'facet-cpu'];
    expect((await send([
      event('category', 'cpu', { slug: 'cpu', name: 'Procesadores' }),
      ...ids.map((id, index) => event('product', id, { ...product, slug: id, name: id,
        brand: index === 1 ? 'Other' : 'Facet', category_id: index === 3 ? 'cpu' : 'gpu',
        specs: index === 3 ? { Socket: 'AM5' } : { Memoria: index === 0 ? '12 GB' : '16 GB', 'Chip gráfico': 'Test GPU' } })),
      event('offer', 'facet-offer-12', { product_id: 'facet-12', shop_id: 'shop', price_cents: 30000, in_stock: true, last_checked_at: now, url_public: 'https://example.com/12' }),
      event('offer', 'facet-offer-16', { product_id: 'facet-16', shop_id: 'shop', price_cents: 60000, in_stock: true, last_checked_at: now, url_public: 'https://example.com/16' }),
      event('offer', 'facet-offer-cheaper', { product_id: 'facet-16', shop_id: 'shop', price_cents: 40000, in_stock: true, last_checked_at: now, url_public: 'https://example.com/16-cheaper' }),
    ])).status).toBe(200);
    const facets = (await get('catalog/filters?category=gpu')).body.data;
    expect(facets.brands).toContainEqual({ value: 'Facet', count: 2 });
    expect(facets.specs.find((facet: any) => facet.key === 'Memoria').options).toEqual([{ value: '12 GB', count: 1 }, { value: '16 GB', count: 2 }]);
    expect(facets.specs.some((facet: any) => facet.key === 'Socket')).toBe(false);
    expect((await get('catalog/filters?category=cpu')).body.data.specs).toEqual([{ key: 'Socket', options: [{ value: 'AM5', count: 1 }] }]);
    const specs = encodeURIComponent(JSON.stringify({ Memoria: ['12 GB', '16 GB'], 'Chip gráfico': ['Test GPU'] }));
    const query = `products?category=gpu&brand=Facet&brand=Other&specs=${specs}&stock=1&min_price=30000&max_price=40000&sort=price&limit=1`;
    const first = (await get(query)).body;
    expect(first.total).toBe(2); expect(first.data.map((p: any) => p.id)).toEqual(['facet-12']);
    expect((await get(`${query}&page=2`)).body.data.map((p: any) => p.id)).toEqual(['facet-16']);
    expect((await get('products?category=gpu&brand=Facet&min_price=0')).body.data.map((p: any) => p.id)).toEqual(['facet-12']);
    expect((await get('products?category=gpu&min_price=45000&max_price=65000')).body.data.map((p: any) => p.id)).toEqual(['gpu-1']);
    expect((await get(`products?category=gpu&specs=${encodeURIComponent(JSON.stringify({ Memoria: ["' OR 1=1 --"] }))}`)).body.total).toBe(0);
    for (const query of ['min_price=-1', 'min_price=1.5', 'min_price=600&max_price=100', 'max_price=100000001', 'specs=null', 'specs=[]', 'specs={', `specs=${encodeURIComponent('{"Socket":["AM5"]}')}`, `category=gpu&specs=${encodeURIComponent('{"Memoria":[]}')}`]) {
      expect((await get(`products?${query}`)).response.status, query).toBe(400);
    }
    await send(ids.map(id => event('product', id, null, 2, 'delete')));
    expect((await get('catalog/filters?category=gpu')).body.data.brands).toEqual([{ value: 'Test', count: 1 }]);
  });
  test('nonce cannot replay; same event with a fresh signature is idempotent', async () => {
    const update = event('product', 'gpu-1', { ...product, name: 'Newer GPU' }, 3);
    const request = signed([update]);
    expect((await mf.dispatchFetch('https://test/internal/sync', request)).status).toBe(200);
    expect((await mf.dispatchFetch('https://test/internal/sync', request)).status).toBe(409);
    expect((await send([update])).status).toBe(200);
    await send([event('product', 'gpu-1', { ...product, name: 'Old GPU' }, 2)]);
    expect((await get('products/test-gpu')).body.data.name).toBe('Newer GPU');
    expect((await get('products')).body.total).toBe(1);
  });
  test('tombstone prevents resurrection, including deletion before first upsert', async () => {
    await send([event('product', 'gpu-1', null, 4, 'delete')]);
    await send([event('product', 'gpu-1', product, 3)]);
    expect((await get('products/test-gpu')).response.status).toBe(404);
    await send([event('product', 'never-seen', null, 8, 'delete')]);
    await send([event('product', 'never-seen', { ...product, slug: 'never-seen' }, 7)]);
    expect((await get('products/never-seen')).response.status).toBe(404);
    await send([event('product', 'gpu-1', product, 5)]);
    expect((await get('products/test-gpu')).response.status).toBe(200);
  });
  test('private/unknown fields, oversized batches and tampering rejected', async () => {
    expect((await send([event('product', 'private', { ...product, email: 'private@example.com' })])).status).toBe(400);
    expect((await send(Array.from({ length: 21 }, () => event('product', 'x', product)))).status).toBe(400);
    const request = signed([event('product', 'x', product)]); request.body += ' ';
    expect((await mf.dispatchFetch('https://test/internal/sync', request)).status).toBe(401);
    const large = signed([event('product', 'x', { ...product, name: 'x'.repeat(70000) })]);
    expect((await mf.dispatchFetch('https://test/internal/sync', large)).status).toBe(413);
  });
  test('a conflicting batch rolls back all writes and the nonce', async () => {
    const request = signed([event('product', 'valid', { ...product, slug: 'valid' }), event('product', 'conflict', product)]);
    expect((await mf.dispatchFetch('https://test/internal/sync', request)).status).toBe(503);
    expect((await get('products/valid')).response.status).toBe(404);
    expect((await mf.dispatchFetch('https://test/internal/sync', request)).status).toBe(503);
  });
  test('deleted shops hide offers; deleted categories hide products', async () => {
    await send([event('shop', 'shop', null, 2, 'delete')]);
    expect((await get('products/test-gpu/offers')).body.data).toHaveLength(0);
    expect((await get('products/test-gpu')).body.data.min_price_cents).toBeNull();
    await send([event('category', 'gpu', null, 2, 'delete')]);
    expect((await get('products')).body.total).toBe(0);
  });
  test('catalogue works with no NAS; freshness is visible and status uncached', async () => {
    const { body, response } = await get('catalog/status');
    expect(body.data_mode).toBe('synthetic'); expect(body.data_updated_at).toBeTruthy();
    expect(response.headers.get('cache-control')).toBe('no-store');
    expect(body.sync_lag_seconds).toBeGreaterThanOrEqual(0);
  });
});

describe.sequential('User reports', () => {
  const report = (body: unknown, ip = '203.0.113.1', type = 'application/json') => mf.dispatchFetch('https://test/api/v1/reports', {
    method: 'POST', body: typeof body === 'string' ? body : JSON.stringify(body), headers: { 'content-type': type, 'cf-connecting-ip': ip } });
  const pull = (sign = true) => {
    const body = '{}', nonce = randomUUID(), timestamp = String(Math.floor(Date.now() / 1000));
    const signature = createHmac('sha256', secret).update(`${timestamp}.${nonce}.${body}`).digest('hex');
    return mf.dispatchFetch('https://test/internal/reports/pull', { method: 'POST', body, headers: { 'content-type': 'application/json',
      ...(sign ? { 'x-sync-timestamp': timestamp, 'x-sync-nonce': nonce, 'x-sync-signature': signature } : {}) } });
  };
  test('setup catalogue', async () => {
    await send([event('category', 'gpu', { slug: 'gpu', name: 'Gráficas' }, 5), event('product', 'rep-1', { ...product, slug: 'rep-gpu', mpn_source: 'retailer' }, 5)]);
    expect((await get('products/rep-gpu')).body.data.mpn_source).toBe('retailer');
  });
  test('valid report returns 201, uncached; invalid ones are rejected', async () => {
    const ok = await report({ product_slug: 'rep-gpu', note: 'Precio incorrecto' });
    expect(ok.status).toBe(201); expect(ok.headers.get('cache-control')).toBe('no-store');
    expect(typeof ((await ok.json()) as any).id).toBe('number');
    expect((await report({ product_slug: 'rep-gpu', note: '   ' })).status).toBe(400);
    expect((await report({ product_slug: 'rep-gpu', note: 'x'.repeat(501) })).status).toBe(400);
    expect((await report({ product_slug: 'nope', note: 'hola' })).status).toBe(404);
    expect((await report({ product_slug: 'rep-gpu', offer_id: 'nope', note: 'hola' })).status).toBe(404);
    expect((await report('x', undefined, 'text/plain')).status).toBe(415);
    expect((await report({ product_slug: 'rep-gpu', note: 'x'.repeat(5000) })).status).toBe(413);
  });
  test('rate limit per hashed IP', async () => {
    const statuses = [];
    for (let i = 0; i < 6; i++) statuses.push((await report({ product_slug: 'rep-gpu', note: `n${i}` }, '198.51.100.7')).status);
    expect(statuses).toEqual([201, 201, 201, 201, 201, 429]);
  });
  test('pull requires a signature, returns new reports once and marks them sent', async () => {
    expect((await pull(false)).status).toBe(401);
    const first = await (await pull()).json() as any;
    expect(first.data.length).toBeGreaterThan(0); expect(first.data.length).toBeLessThanOrEqual(50);
    expect(first.data[0]).not.toHaveProperty('ip_hash');
    expect((await (await pull()).json() as any).data).toEqual([]);
  });
});

// ---------------------------------------------------------------------------------------------------------------------
// Price history, search, retailer codes, SEO shell, sitemap/robots and freshness.
const iso = (offsetMs: number) => new Date(Date.now() + offsetMs).toISOString();
const offerData = (productId: string, price: number, inStock: boolean, checked: string, shop = 'hs') =>
  ({ product_id: productId, shop_id: shop, price_cents: price, in_stock: inStock, last_checked_at: checked, url_public: `https://example.com/${productId}` });
const historyRows = (offerId: string) => main.query('SELECT price_cents,in_stock,observed_at FROM offer_price_history WHERE offer_id=? ORDER BY id', offerId);

describe.sequential('Price history', () => {
  const t = [iso(-5 * 3600_000), iso(-4 * 3600_000), iso(-3 * 3600_000), iso(-2 * 3600_000), iso(-3600_000)];
  test('setup', async () => {
    expect((await send([
      event('category', 'cat-h', { slug: 'cat-h', name: 'Gráficas H' }),
      event('shop', 'hs', { name: 'Hist Shop', url_public: 'https://example.com' }),
      event('product', 'hp', { ...product, slug: 'hist-gpu', name: 'Hist GPU', category_id: 'cat-h' }),
    ])).status).toBe(200);
  });
  test('first observation, unchanged checks, price and stock changes, stale versions', async () => {
    await send([event('offer', 'h-offer', offerData('hp', 1000, true, t[0]), 1)]);
    expect(await historyRows('h-offer')).toEqual([{ price_cents: 1000, in_stock: 1, observed_at: new Date(t[0]).toISOString() }]);
    await send([event('offer', 'h-offer', offerData('hp', 1000, true, t[1]), 2)]);
    expect(await historyRows('h-offer')).toHaveLength(1);
    await send([event('offer', 'h-offer', offerData('hp', 900, true, t[2]), 3)]);
    await send([event('offer', 'h-offer', offerData('hp', 900, false, t[3]), 4)]);
    expect((await historyRows('h-offer')).map((r: any) => [r.price_cents, r.in_stock])).toEqual([[1000, 1], [900, 1], [900, 0]]);
    await send([event('offer', 'h-offer', offerData('hp', 5, true, t[4]), 2)]); // old version: ignored
    await send([event('offer', 'h-offer', offerData('hp', 900, false, t[4]), 4)]); // same version replay: ignored
    expect(await historyRows('h-offer')).toHaveLength(3);
    expect((await get('products/hist-gpu/offers')).body.data[0].price_cents).toBe(900);
  });
  test('a conflicting batch rolls back its history rows', async () => {
    const request = signed([event('offer', 'h-offer', offerData('hp', 800, true, t[4]), 5),
      event('product', 'hp-dup', { ...product, slug: 'hist-gpu', category_id: 'cat-h' })]);
    expect((await mf.dispatchFetch('https://test/internal/sync', request)).status).toBe(503);
    expect(await historyRows('h-offer')).toHaveLength(3);
  });
  test('endpoint returns ascending rows, honours days and validates input', async () => {
    await send([event('offer', 'h-old', offerData('hp', 2000, true, iso(-10 * 86400_000)), 1)]);
    const all = await get('products/hist-gpu/price-history');
    expect(all.response.status).toBe(200);
    expect(all.response.headers.get('cache-control')).toBe('public, max-age=300');
    expect(all.body.data.map((r: any) => r.price_cents)).toEqual([2000, 1000, 900, 900]);
    expect(all.body.data[0]).toEqual({ offer_id: 'h-old', shop_name: 'Hist Shop', price_cents: 2000, in_stock: 1, observed_at: expect.any(String) });
    expect(all.body.status).toHaveProperty('stale');
    expect((await get('products/hist-gpu/price-history?days=1')).body.data).toHaveLength(3);
    expect((await get('products/hist-gpu/price-history?days=30')).body.data).toHaveLength(4);
    for (const q of ['days=0', 'days=731', 'days=x', 'days=1.5', 'foo=1']) expect((await get(`products/hist-gpu/price-history?${q}`)).response.status, q).toBe(400);
    expect((await get('products/nope-gpu/price-history')).response.status).toBe(404);
    await send([event('product', 'hp2', { ...product, slug: 'hist-empty', category_id: 'cat-h' })]);
    expect((await get('products/hist-empty/price-history')).body.data).toEqual([]);
  });
});

describe.sequential('Search and retailer codes', () => {
  const p = (slug: string, name: string, brand: string, mpn: string | undefined, extra: object = {}) =>
    ({ ...product, slug, name, brand, category_id: 'cat-h', ...(mpn ? { mpn } : {}), ...extra });
  test('setup and stored shape', async () => {
    expect((await send([
      event('product', 'wid-cable', p('wid-cable', 'WID-1 compatible cable', 'Cablex', undefined)),
      event('product', 'wid-main', p('wid-main', 'Alpha Widget', 'Zotac', 'WID-1', { retailer_codes: [{ shop_id: 'hs', code: 'ABC-123' }] })),
      event('offer', 'wid-o1', offerData('wid-main', 5000, true, iso(-1000))),
      event('offer', 'wid-o2', offerData('wid-cable', 5000, true, iso(-1000))),
    ])).status).toBe(200);
    expect((await get('products/wid-main')).body.data.retailer_codes).toEqual([{ shop_id: 'hs', code: 'ABC-123' }]);
    expect((await get('products/wid-cable')).body.data.retailer_codes).toEqual([]);
  });
  test('matches name, brand, MPN and retailer codes, case-insensitively, with AND over terms', async () => {
    const ids = async (q: string) => (await get(`products?q=${encodeURIComponent(q)}&sort=name`)).body.data.map((r: any) => r.slug);
    expect(await ids('ZOTAC')).toEqual(['wid-main']);
    expect(await ids('wid-1')).toEqual(['wid-main', 'wid-cable']);
    expect(await ids('abc-123')).toEqual(['wid-main']);
    expect(await ids('alpha zotac')).toEqual(['wid-main']);
    expect(await ids('alpha cablex')).toEqual([]);
    expect(await ids('  alpha   widget ')).toEqual(['wid-main']);
    expect(await ids('%%')).toEqual([]);
    expect(await ids('__')).toEqual([]);
    expect(await ids('\\%')).toEqual([]);
    expect(await ids("'; DROP TABLE catalog_products; --")).toEqual([]);
  });
  test('term limits', async () => {
    expect((await get('products?q=a+b+c+d+e+f')).response.status).toBe(200);
    expect((await get('products?q=a+b+c+d+e+f+g')).response.status).toBe(400);
    expect((await get(`products?q=${'x'.repeat(51)}`)).response.status).toBe(400);
    expect((await get(`products?q=${'x'.repeat(50)}`)).response.status).toBe(200);
  });
  test('featured sort ranks exact MPN, then name prefix, ahead of the default order', async () => {
    const order = async (q: string) => (await get(`products?q=${encodeURIComponent(q)}&sort=featured`)).body.data.map((r: any) => r.slug);
    expect(await order('WID-1')).toEqual(['wid-main', 'wid-cable']);
    expect(await order('abc-123')).toEqual(['wid-main']);
    expect(await order('wid')).toEqual(['wid-cable', 'wid-main']);
  });
  test('retailer_codes are preserved by updates that omit them and cleared only explicitly', async () => {
    await send([event('product', 'wid-main', p('wid-main', 'Alpha Widget 2', 'Zotac', 'WID-1'), 2)]);
    const kept = (await get('products/wid-main')).body.data;
    expect(kept.name).toBe('Alpha Widget 2'); expect(kept.retailer_codes).toEqual([{ shop_id: 'hs', code: 'ABC-123' }]);
    await send([event('product', 'wid-main', p('wid-main', 'Alpha Widget 2', 'Zotac', 'WID-1', { retailer_codes: [] }), 3)]);
    expect((await get('products/wid-main')).body.data.retailer_codes).toEqual([]);
    expect((await get('products?q=abc-123')).body.total).toBe(0);
    const list = (await get('products?q=Alpha+Widget+2')).body;
    expect(list.data[0].retailer_codes).toEqual([]);
  });
  test('contract rejects more than 8 codes and invalid shapes', async () => {
    const code = (n: number) => ({ shop_id: 'hs', code: `C${n}` });
    const status = async (retailer_codes: unknown) => (await send([event('product', 'rc-x', p('rc-x', 'RC', 'B', undefined, { retailer_codes }))])).status;
    expect(await status(Array.from({ length: 8 }, (_, i) => code(i)))).toBe(200);
    expect(await status(Array.from({ length: 9 }, (_, i) => code(i)))).toBe(400);
    for (const bad of ['x', [{ shop_id: 'hs' }], [{ code: 'a' }], [{ shop_id: 'hs', code: '' }], [{ shop_id: 'hs', code: 'x'.repeat(101) }],
      [{ shop_id: 'bad id!', code: 'a' }], [{ shop_id: 'hs', code: 'a', extra: 1 }], [['hs', 'a']], [{ shop_id: 'hs', code: 5 }]]) {
      expect(await status(bad), JSON.stringify(bad)).toBe(400);
    }
    expect((await send([event('product', 'rc-y', p('rc-y', 'No codes', 'B', undefined))])).status).toBe(200);
  });
});

describe.sequential('SEO shell, sitemap and robots', () => {
  const evil = 'Evil "GPU" </script><img src=x onerror=alert(1)> & co \u2028x';
  const page = async (path: string, init?: Parameters<typeof mf.dispatchFetch>[1]) => { const response = await mf.dispatchFetch(`https://seo.example${path}`, init); return { response, html: await response.text() }; };
  const ldBlocks = (html: string) => [...html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g)].map(m => m[1]);
  test('setup', async () => {
    expect((await send([
      event('product', 'seo-1', { ...product, slug: 'seo-evil', name: evil, brand: 'Br<and>', mpn: 'M&P-1', category_id: 'cat-h',
        image_path: '/images/products/evil.png', specs: { 'K<ey>': 'V"al' } }),
      event('product', 'seo-2', { ...product, slug: 'seo-nooffer', name: 'No Offer', category_id: 'cat-h' }),
      event('offer', 'seo-o1', offerData('seo-1', 12345, true, iso(-1000))),
      event('offer', 'seo-o2', offerData('seo-1', 20000, true, iso(-1000))),
      event('offer', 'seo-o3', offerData('seo-1', 100, false, iso(-1000))),
      event('offer', 'seo-o4', offerData('seo-2', 100, false, iso(-1000))),
    ])).status).toBe(200);
  });
  test('product page: exact title, canonical from the request origin, escaped content, JSON-LD', async () => {
    const { response, html } = await page('/p/seo-evil');
    expect(response.status).toBe(200);
    expect(response.headers.get('cache-control')).toBe('public, max-age=300');
    expect(html).toContain(`<title>${evil.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')} · Precios y especificaciones | StockFinder</title>`);
    expect(html).toContain('<link rel="canonical" href="https://seo.example/p/seo-evil"');
    expect(html).not.toContain('www.stockfinder.workers.dev');
    expect(html).not.toContain('<img src=x');
    expect(html).not.toMatch(/<\/script><img/);
    expect(html).toContain('property="og:image" content="https://seo.example/images/products/evil.png"');
    expect(html).toContain('property="og:type" content="product"');
    expect(html).toContain('K&lt;ey&gt;: V&quot;al');
    const blocks = ldBlocks(html);
    expect(blocks).toHaveLength(2);
    for (const block of blocks) { expect(block).not.toMatch(/[<>&\u2028\u2029]/); JSON.parse(block); }
    const ld = JSON.parse(blocks.find(b => b.includes('"Product"'))!);
    expect(ld.name).toBe(evil); expect(ld.brand.name).toBe('Br<and>'); expect(ld.mpn).toBe('M&P-1');
    expect(ld.image).toBe('https://seo.example/images/products/evil.png');
    expect(ld.offers).toEqual({ '@type': 'AggregateOffer', lowPrice: '123.45', highPrice: '200.00', priceCurrency: 'EUR', offerCount: 2, availability: 'https://schema.org/InStock' });
  });
  test('product without in-stock offers omits offers; unknown pages are 404 noindex', async () => {
    const { html } = await page('/p/seo-nooffer');
    const ld = JSON.parse(ldBlocks(html).find(b => b.includes('"Product"'))!);
    expect(ld.name).toBe('No Offer'); expect(ld).not.toHaveProperty('offers');
    for (const path of ['/p/does-not-exist', '/c/does-not-exist', '/p/bad%20slug', '/p/%E0%A4%A']) {
      const missing = await page(path);
      expect(missing.response.status, path).toBe(404);
      expect(missing.html, path).toContain('<meta name="robots" content="noindex"');
      expect(missing.html, path).toContain('<div id="root">');
      expect(missing.html, path).not.toContain('"Product"');
    }
  });
  test('category, informational and home pages', async () => {
    expect((await page('/c/cat-h')).html).toContain('<title>Gráficas H · Compara precios y especificaciones | StockFinder</title>');
    expect((await page('/configurador')).html).toContain('<title>Configurador de PC | StockFinder</title>');
    expect((await page('/acerca')).html).toContain('<title>Cómo funciona StockFinder</title>');
    expect((await page('/privacidad')).html).toContain('<title>Privacidad | StockFinder</title>');
    const home = await page('/');
    expect(home.html).toContain('<title>StockFinder — Compara componentes de PC y precios en tiendas</title>');
    expect(home.html).toContain('<link rel="canonical" href="https://seo.example/"');
    expect(home.response.status).toBe(200);
    expect((await page('/p/seo-evil', { method: 'HEAD' })).response.status).toBe(200);
  });
  test('sitemap.xml and robots.txt are dynamic and origin-aware', async () => {
    await send([event('product', 'seo-2', null, 9, 'delete')]);
    const sitemap = await page('/sitemap.xml');
    expect(sitemap.response.headers.get('content-type')).toContain('application/xml');
    expect(sitemap.html).toContain('<loc>https://seo.example/</loc>');
    expect(sitemap.html).toContain('<loc>https://seo.example/c/cat-h</loc>');
    expect(sitemap.html).toContain('<loc>https://seo.example/acerca</loc>');
    expect(sitemap.html).toMatch(/<loc>https:\/\/seo\.example\/p\/seo-evil<\/loc><lastmod>\d{4}-\d{2}-\d{2}<\/lastmod>/);
    expect(sitemap.html).not.toContain('seo-nooffer');
    const robots = await page('/robots.txt');
    expect(robots.html).toContain('Sitemap: https://seo.example/sitemap.xml');
    expect(robots.html).toContain('Disallow: /api/');
    // Stored copies (Cache API and D1 api_cache) are per origin: another host never gets seo.example URLs.
    await new Promise(r => setTimeout(r, 50));
    const other = await (await mf.dispatchFetch('https://other.example/sitemap.xml')).text();
    expect(other).toContain('<loc>https://other.example/p/seo-evil</loc>');
    expect(other).not.toContain('seo.example');
    expect((await page('/sitemap.xml')).html).toBe(sitemap.html);
  });
});

describe.sequential('Freshness', () => {
  const freshWorker = testWorker({ serviceBindings: { ASSETS: async () => new Response(shell, { headers: { 'content-type': 'text/html' } }) },
    bindings: { SYNC_SECRET: secret, DATA_MODE: 'synthetic', STALE_AFTER_SECONDS: '3600' } });
  const fresh = freshWorker.mf;
  const push = (events: unknown[]) => fresh.dispatchFetch('https://test/internal/sync', signed(events));
  const status = async () => ((await (await fresh.dispatchFetch('https://test/api/v1/catalog/status')).json()) as any);
  const clearFreshShare = () => freshWorker.query("DELETE FROM api_cache WHERE key LIKE 'hourly:fresh%'");
  beforeAll(() => freshWorker.migrate(), 30000);
  afterAll(() => freshWorker.dispose());
  test('without offers it falls back to source_changed_at', async () => {
    await push([event('category', 'c', { slug: 'c', name: 'C' })]);
    const s = await status();
    expect(s.offers_checked_at).toBeNull(); expect(s.stale).toBe(false);
    expect(s.offers_total).toBe(0); expect(s.offers_fresh).toBe(0);
  });
  test('stale follows the latest offer check, not the last price change', async () => {
    await push([event('shop', 's', { name: 'S', url_public: 'https://example.com' }), event('product', 'p', { ...product, slug: 'p', category_id: 'c' }),
      event('offer', 'o', offerData('p', 100, true, iso(-2 * 3600_000), 's'))]);
    const old = await status();
    expect(old.stale).toBe(true); expect(old.offers_checked_at).toBe(new Date(Date.parse(old.offers_checked_at)).toISOString());
    const checked = iso(-60_000);
    await push([{ ...event('offer', 'o', offerData('p', 100, true, checked, 's'), 2), changed_at: iso(-3 * 3600_000) }]);
    const s = await status();
    expect(s.offers_checked_at).toBe(checked); expect(s.stale).toBe(false);
    expect(Date.parse(s.source_changed_at)).toBeGreaterThan(Date.now() - 60_000);
  });
  test('reports the share of offers checked in the last 12 hours', async () => {
    await push([event('offer', 'o2', offerData('p', 110, true, iso(-6 * 86400_000), 's'), 1),
      event('offer', 'o3', offerData('p', 120, true, iso(-11 * 3600_000), 's'), 1),
      event('offer', 'o4', offerData('p', 130, true, iso(-13 * 3600_000), 's'), 1)]);
    await clearFreshShare();                                                                  // cached for an hour; offer events do not evict it
    const s = await status();
    expect(s.offers_total).toBe(4); expect(s.offers_fresh).toBe(2); expect(s.offers_fresh_hours).toBe(12);
  });
  test('deleted shops do not count towards freshness', async () => {
    await push([event('shop', 's', null, 2, 'delete')]);
    const s = await status();
    expect(s.offers_checked_at).toBeNull(); expect(s.offers_total).toBe(0); expect(s.offers_fresh).toBe(0);
  });
});

describe.sequential('Landing highlights', () => {
  const hlWorker = testWorker({ serviceBindings: { ASSETS: async () => new Response(shell, { headers: { 'content-type': 'text/html' } }) },
    bindings: { SYNC_SECRET: secret, DATA_MODE: 'synthetic' } });
  const hl = hlWorker.mf;
  const push = (events: unknown[]) => hl.dispatchFetch('https://test/internal/sync', signed(events));
  const highlights = async (query = '') => { const r = await hl.dispatchFetch(`https://test/api/v1/catalog/highlights${query}`, { headers: { 'cache-control': 'no-cache' } }); return { status: r.status, body: await r.json() as any }; };
  beforeAll(() => hlWorker.migrate(), 30000);
  afterAll(() => hlWorker.dispose());
  test('category summary and price drops within 24 hours (latest change, in stock, now the best price)', async () => {
    const offer = (product: string, price: number, inStock: boolean, checked: string) => ({ product_id: product, shop_id: 'ds', price_cents: price, in_stock: inStock, last_checked_at: checked, url_public: `https://example.com/${product}` });
    expect((await push([
      event('category', 'gpu', { slug: 'gpu', name: 'Gráficas' }), event('category', 'cpu', { slug: 'cpu', name: 'Procesadores' }),
      event('shop', 'ds', { name: 'Drop Shop', url_public: 'https://example.com' }),
      event('product', 'p-drop', { ...product, slug: 'p-drop', name: 'Drop GPU (DG-1)', mpn: 'DG-1' }),
      event('product', 'p-flat', { ...product, slug: 'p-flat', name: 'Older Drop GPU' }),
      event('product', 'p-gone', { ...product, slug: 'p-gone', name: 'Gone GPU' }),
      event('offer', 'o-drop', offer('p-drop', 50000, true, iso(-3 * 86400_000)), 1),
      event('offer', 'o-flat', offer('p-flat', 35000, true, iso(-3 * 86400_000)), 1),
      event('offer', 'o-gone', offer('p-gone', 40000, true, iso(-3 * 86400_000)), 1),
    ])).status).toBe(200);
    expect((await push([
      event('offer', 'o-drop', offer('p-drop', 45000, true, iso(-23 * 3600_000)), 2),
      event('offer', 'o-flat', offer('p-flat', 30000, true, iso(-25 * 3600_000)), 2),
      event('offer', 'o-gone', offer('p-gone', 20000, false, iso(-23 * 3600_000)), 2),
    ])).status).toBe(200);
    const { status, body } = await highlights();
    expect(status).toBe(200);
    expect(body.data.drops.map((d: any) => d.slug)).toEqual(['p-drop']);
    expect(body.data.drops[0]).toMatchObject({ min_price_cents: 45000, previous_price_cents: 50000, shop_name: 'Drop Shop', mpn: 'DG-1', category_id: 'gpu' });
    const gpu = body.data.categories.find((c: any) => c.id === 'gpu');
    expect(gpu).toEqual({ id: 'gpu', name: 'Gráficas', products: 3, in_stock: 2, min_price_cents: 30000 });
    expect(body.data.categories.find((c: any) => c.id === 'cpu')).toEqual({ id: 'cpu', name: 'Procesadores', products: 0, in_stock: 0, min_price_cents: null });
    // A cheaper offer elsewhere means the drop is no longer the best price. The landing payload is persisted for the hour (one row, no
    // structure version in its key; up to 1 h of lag is accepted): it is still served until the hour rolls over.
    expect((await push([event('offer', 'o-drop-2', { ...offer('p-drop', 44000, true, iso(-3600_000)), shop_id: 'ds' }, 1)])).status).toBe(200);
    if (dbMode !== 'pg') {
      expect((await highlights()).body.data.drops.map((d: any) => d.slug)).toEqual(['p-drop']);
      await hlWorker.query("UPDATE api_cache SET version=version-1 WHERE key='hourly:highlights:24h'");
    }   // the hour rolls over
    expect((await highlights()).body.data.drops).toEqual([]);
    expect((await highlights('?limit=3')).status).toBe(400);
  });
  test.skipIf(dbMode === 'pg')('import bursts: 50 structural batches (facets_version bumps) with a request after each recompute nothing within the hour', async () => {
    await hlWorker.query("UPDATE api_cache SET version=version-1 WHERE key='hourly:highlights:24h'");
    const base = (await highlights()).body.data;                                                 // recomputed once: new hour row
    const facets = async () => (await hlWorker.query<{ f: number }>('SELECT facets_version AS f FROM sync_status WHERE id=1'))[0].f;
    const f0 = await facets(), gpu0 = base.categories.find((c: any) => c.id === 'gpu').products;
    for (let i = 0; i < 50; i++) {
      expect((await push([event('product', `p-burst-${i}`, { ...product, slug: `p-burst-${i}`, name: `Burst GPU ${i}` })])).status).toBe(200);
      expect((await highlights()).body.data).toEqual(base);                                      // served from the persisted hour row
    }
    expect(await facets()).toBeGreaterThanOrEqual(f0 + 50);
    expect(await hlWorker.query("SELECT count(*) AS n FROM api_cache WHERE key LIKE 'hourly:highlights:24h%'")).toEqual([{ n: 1 }]);
    await hlWorker.query("UPDATE api_cache SET version=version-1 WHERE key='hourly:highlights:24h'");   // next hour: the changes appear
    expect((await highlights()).body.data.categories.find((c: any) => c.id === 'gpu').products).toBe(gpu0 + 50);
  });
});

describe.sequential('Shared PC builds', () => {
  const share = (body: unknown, ip = '198.51.100.7') => mf.dispatchFetch('https://test/api/v1/builds', { method: 'POST', body: JSON.stringify(body),
    headers: { 'content-type': 'application/json', 'cf-connecting-ip': ip } });
  test('stores slugs under a UUID and returns them; rejects unknown or misplaced products', async () => {
    expect((await send([
      event('category', 'cpu', { slug: 'cpu', name: 'Procesadores' }),
      event('product', 'share-cpu', { slug: 'share-cpu', name: 'CPU Share', category_id: 'cpu', brand: 'Test', specs: { Socket: 'AM5' } })
    ])).status).toBe(200);
    const created = await share({ name: 'Mi PC', parts: { cpu: 'share-cpu' } });
    expect(created.status).toBe(201);
    const { id } = await created.json() as { id: string };
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
    const { response, body } = await get(`builds/${id}`);
    expect(response.status).toBe(200);
    expect(body.data).toMatchObject({ id, name: 'Mi PC', parts: { cpu: 'share-cpu' } });
    expect(JSON.stringify(body)).not.toContain('ip_hash');
    expect((await share({ name: 'X', parts: { gpu: 'share-cpu' } })).status).toBe(404);
    expect((await share({ name: 'X', parts: { cpu: 'missing' } })).status).toBe(404);
    for (const bad of [{ name: 'X', parts: {} }, { name: '', parts: { cpu: 'share-cpu' } }, { name: 'X', parts: { fan: 'share-cpu' } }, { name: 'X', parts: { cpu: 'share-cpu' }, extra: 1 }])
      expect((await share(bad)).status).toBe(400);
    expect((await get('builds/00000000-0000-4000-8000-000000000000')).response.status).toBe(404);
    expect((await get('builds/not-a-uuid')).response.status).toBe(404);
    expect((await mf.dispatchFetch('https://test/api/v1/builds', { method: 'GET' })).status).toBe(405);
  });
  test('rate limited per IP', async () => {
    const statuses: number[] = [];
    for (let i = 0; i < 31; i++) statuses.push((await share({ name: `L${i}`, parts: { cpu: 'share-cpu' } }, '198.51.100.99')).status);
    expect(statuses.slice(0, 30).every(s => s === 201)).toBe(true);
    expect(statuses[30]).toBe(429);
  });
});
