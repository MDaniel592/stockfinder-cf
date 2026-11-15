import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { createHmac, randomUUID } from 'node:crypto';
import { testWorker } from './helpers/worker-db';

// Offer aggregates denormalised into catalog_products, and version-keyed response caching (separate worker + D1 from edge.test.ts).
const secret = 'synthetic-test-secret-not-for-deployment-1234';
// D1 by default; Postgres when TEST_PG_URL is set (tests/helpers/worker-db.ts).
const worker = testWorker({ serviceBindings: { ASSETS: async () => new Response('<!doctype html><title>t</title><noscript></noscript>', { headers: { 'content-type': 'text/html' } }) },
  bindings: { SYNC_SECRET: secret, DATA_MODE: 'synthetic' } });
const mf = worker.mf;
let eventId = 0, versions = new Map<string, number>();
const now = new Date().toISOString();
const ev = (entity: string, id: string, data: unknown, version?: number, operation = 'upsert') => {
  const v = version ?? (versions.get(id) ?? 0) + 1; versions.set(id, Math.max(v, versions.get(id) ?? 0));
  return { event_id: ++eventId, entity, entity_id: id, version: v, operation, changed_at: now, ...(operation === 'upsert' ? { data } : {}) };
};
const send = (events: unknown[]) => {
  const body = JSON.stringify({ batch_id: randomUUID(), schema_version: 1, events }), nonce = randomUUID(), ts = String(Math.floor(Date.now() / 1000));
  return mf.dispatchFetch('https://test/internal/sync', { method: 'POST', body, headers: { 'content-type': 'application/json', 'x-sync-timestamp': ts, 'x-sync-nonce': nonce,
    'x-sync-signature': createHmac('sha256', secret).update(`${ts}.${nonce}.${body}`).digest('hex') } });
};
const api = async (path: string, headers: Record<string, string> = {}) => {
  const response = await mf.dispatchFetch(`https://test/api/v1/${path}`, { headers }); return { response, body: await response.json() as any };
};
const offer = (product_id: string, shop_id: string, price_cents: number, in_stock = true) =>
  ({ product_id, shop_id, price_cents, in_stock, last_checked_at: now, url_public: 'https://example.com/o' });
const gpu = (slug: string) => ({ slug, name: slug, category_id: 'gpu', brand: 'Acme', specs: { Memoria: '16 GB' } });
const agg = async (id: string) => (await worker.query(
  'SELECT min_price_cents,min_price_in_stock_cents,max_price_in_stock_cents,offers_count,in_stock_count FROM catalog_products WHERE id=?', id))[0] as any;

beforeAll(() => worker.migrate(), 30000);
afterAll(() => worker.dispose());

describe.sequential('offer aggregates stay consistent with catalog_offers', () => {
  test('insert, price/stock update, delete', async () => {
    expect((await send([ev('category', 'gpu', { slug: 'gpu', name: 'GPU' }), ev('shop', 'sa', { name: 'A', url_public: 'https://a.example' }),
      ev('shop', 'sb', { name: 'B', url_public: 'https://b.example' }), ev('product', 'p1', gpu('p1')), ev('product', 'p2', gpu('p2'))])).status).toBe(200);
    expect(await agg('p1')).toMatchObject({ min_price_cents: null, offers_count: 0, in_stock_count: 0 });
    await send([ev('offer', 'o1', offer('p1', 'sa', 50000)), ev('offer', 'o2', offer('p1', 'sb', 40000, false)), ev('offer', 'o3', offer('p1', 'sb', 70000))]);
    expect(await agg('p1')).toMatchObject({ min_price_cents: 40000, min_price_in_stock_cents: 50000, max_price_in_stock_cents: 70000, offers_count: 3, in_stock_count: 2 });
    await send([ev('offer', 'o1', offer('p1', 'sa', 30000))]);               // price drop
    expect(await agg('p1')).toMatchObject({ min_price_cents: 30000, min_price_in_stock_cents: 30000 });
    await send([ev('offer', 'o1', offer('p1', 'sa', 30000, false))]);        // out of stock
    expect(await agg('p1')).toMatchObject({ min_price_cents: 30000, min_price_in_stock_cents: 70000, in_stock_count: 1 });
    await send([ev('offer', 'o3', null, undefined, 'delete')]);              // tombstone carries no payload
    expect(await agg('p1')).toMatchObject({ min_price_in_stock_cents: null, max_price_in_stock_cents: null, offers_count: 2, in_stock_count: 0 });
    const { body } = await api('products/p1');
    expect(body.data).toMatchObject({ min_price_cents: null, offers_count: 2 });
  });
  test('stale versions are ignored and do not disturb aggregates', async () => {
    await send([ev('offer', 'o4', offer('p2', 'sa', 10000))]);
    expect(await agg('p2')).toMatchObject({ min_price_in_stock_cents: 10000, offers_count: 1 });
    expect((await send([ev('offer', 'o4', offer('p2', 'sa', 99), 1), ev('offer', 'o4', null, 1, 'delete')])).status).toBe(200); // replay of version 1
    expect(await agg('p2')).toMatchObject({ min_price_in_stock_cents: 10000, offers_count: 1 });
  });
  test('shop deletion hides its offers; product tombstone hides the product; offers may precede their product', async () => {
    await send([ev('shop', 'sa', null, undefined, 'delete')]);
    expect(await agg('p2')).toMatchObject({ min_price_in_stock_cents: null, offers_count: 0 });
    await send([ev('shop', 'sa', { name: 'A', url_public: 'https://a.example' })]);
    expect(await agg('p2')).toMatchObject({ min_price_in_stock_cents: 10000, offers_count: 1 });
    await send([ev('offer', 'o5', offer('late', 'sb', 12300))]);
    await send([ev('product', 'late', gpu('late'))]);
    expect(await agg('late')).toMatchObject({ min_price_in_stock_cents: 12300, offers_count: 1 });
    await send([ev('product', 'late', null, undefined, 'delete')]);
    expect((await api('products/late')).response.status).toBe(404);
  });
  test('listing sorts and filters use the aggregates; totals are exact', async () => {
    expect((await api('products?category=gpu&sort=price&limit=1')).body).toMatchObject({ total: 2, data: [{ id: 'p2' }] });   // p2 10000 in stock, p1 has none in stock
    expect((await api('products?category=gpu&stock=1')).body.data.map((p: any) => p.id)).toEqual(['p2']);
    expect((await api('products?category=gpu&min_price=10001')).body.total).toBe(0);
    expect((await api('products?category=gpu&sort=-price')).body.data.map((p: any) => p.id)).toEqual(['p2', 'p1']);          // priced first, unpriced last
    expect((await api('products?category=gpu&limit=1&page=2')).body).toMatchObject({ total: 2, data: [{ id: 'p2' }] });
    expect((await api('products?category=gpu&limit=1&page=3')).body).toMatchObject({ total: 2, data: [] });
  });
});

describe.sequential('version-keyed response cache', () => {
  const warm = async (path: string) => { await api(path); await new Promise(r => setTimeout(r, 50)); return api(path); };   // cache.put runs in waitUntil
  test('repeat requests are served from cache with normalised keys and refreshed status', async () => {
    const { response, body } = await warm('products?category=gpu&sort=price');
    expect(response.headers.get('x-sf-cache')).toBe('hit');
    expect(response.headers.get('cache-control')).toMatch(/^public, max-age=\d{1,3}$/);   // browsers never see the long edge TTL
    expect(body.status.stale).toBe(false);
    const alias = await api('products?sort=price&category=gpu&page=1&utm_source=x');
    expect(alias.response.headers.get('x-sf-cache')).toBe('hit');
    expect((await api('products?category=gpu&bogus=1')).response.status).toBe(400);      // unknown params are never normalised away
    expect((await api('catalog/status')).response.headers.get('cache-control')).toBe('no-store');
    // The hit is the stored body with only the status' time-dependent fields refreshed.
    const fresh = await mf.dispatchFetch('https://test/api/v1/products?category=gpu&sort=price', { headers: { 'cache-control': 'no-cache' } });
    const cached = await mf.dispatchFetch('https://test/api/v1/products?category=gpu&sort=price');
    expect(cached.headers.get('x-sf-cache')).toBe('hit');
    const [a, b] = [await fresh.json() as any, await cached.json() as any];
    expect({ ...b, status: { ...b.status, age_seconds: 0 } }).toEqual({ ...a, status: { ...a.status, age_seconds: 0 } });
  });
  test('HTML pages are cached by path alone', async () => {
    const page = (path: string) => mf.dispatchFetch(`https://test${path}`);
    for (const path of ['/', '/acerca', '/c/gpu']) {
      const first = await page(path); expect(first.status, path).toBe(200); await first.text();
      await new Promise(r => setTimeout(r, 50));
      const again = await page(`${path}?utm_source=x&brand=Acme`);
      expect(again.headers.get('x-sf-cache'), path).toBe('hit');
      expect(again.headers.get('cache-control'), path).toBe('public, max-age=300');
      expect(await again.text(), path).toContain('<noscript>');
    }
  });
  test('a sync invalidates listings immediately; structural caches only on structural change', async () => {
    const before = (await warm('products/p2')).body.data.min_price_cents;
    expect(before).toBe(10000);
    const filters = (await warm('catalog/filters?category=gpu')).body.data;
    await send([ev('offer', 'o4', offer('p2', 'sa', 9000))]);                          // price only
    const after = await api('products/p2');
    expect(after.response.headers.get('x-sf-cache')).toBe('miss'); expect(after.body.data.min_price_cents).toBe(9000);
    expect((await api('catalog/filters?category=gpu')).body.data).toEqual(filters);    // unchanged, still correct
    await send([ev('product', 'p3', gpu('p3'))]);                                       // structural: counts must move
    const next = (await api('catalog/filters?category=gpu')).body.data;
    expect(next.brands).toEqual([{ value: 'Acme', count: 3 }]);
    expect((await warm('products?category=gpu&limit=1')).body.total).toBe(3);
    await send([ev('product', 'p3', null, undefined, 'delete')]);
    expect((await api('products?category=gpu&limit=1')).body.total).toBe(2);
    expect((await api('brands')).body.data).toEqual(['Acme']);
  });
  test('public offer total follows structure', async () => {
    const status = (await api('catalog/status')).body;
    expect(status.offers_total).toBe(4); expect(status.offers_fresh).toBe(4);
  });
});
