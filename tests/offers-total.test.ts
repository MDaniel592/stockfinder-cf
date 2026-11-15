import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { createHmac, randomUUID } from 'node:crypto';
import { testWorker } from './helpers/worker-db';
import { finalizeStatements } from '../apps/edge-api/src/aggregates';

// sync_status.offers_total (public "offers" total) is recounted only by batches that contain a shop or offer event; a product/category-only
// batch bumps facets_version without recounting, and totals_version stays behind until the next offer/shop batch converges it.
const secret = 'synthetic-test-secret-not-for-deployment-1234';
const worker = testWorker({ serviceBindings: { ASSETS: async () => new Response('<!doctype html><title>t</title><noscript></noscript>', { headers: { 'content-type': 'text/html' } }) },
  bindings: { SYNC_SECRET: secret, DATA_MODE: 'synthetic' } });
const mf = worker.mf;
let eventId = 0; const versions = new Map<string, number>();
const now = new Date().toISOString();
const ev = (entity: string, id: string, data: unknown, operation = 'upsert') => {
  const v = (versions.get(id) ?? 0) + 1; versions.set(id, v);
  return { event_id: ++eventId, entity, entity_id: id, version: v, operation, changed_at: now, ...(operation === 'upsert' ? { data } : {}) };
};
const send = async (events: unknown[]) => {
  const body = JSON.stringify({ batch_id: randomUUID(), schema_version: 1, events }), nonce = randomUUID(), ts = String(Math.floor(Date.now() / 1000));
  const response = await mf.dispatchFetch('https://test/internal/sync', { method: 'POST', body, headers: { 'content-type': 'application/json', 'x-sync-timestamp': ts, 'x-sync-nonce': nonce,
    'x-sync-signature': createHmac('sha256', secret).update(`${ts}.${nonce}.${body}`).digest('hex') } });
  expect(response.status).toBe(200);
};
const offer = (product_id: string, shop_id: string, price_cents = 50000) => ({ product_id, shop_id, price_cents, in_stock: true, last_checked_at: now, url_public: 'https://example.com/o' });
const gpu = (slug: string) => ({ slug, name: slug, category_id: 'gpu', brand: 'Acme', specs: { Memoria: '16 GB' } });
const state = async () => (await worker.query<{ offers_total: number; totals_version: number; facets_version: number }>('SELECT offers_total,totals_version,facets_version FROM sync_status WHERE id=1'))[0];
const active = async () => (await worker.query<{ n: number }>(`SELECT COUNT(*) AS n FROM catalog_offers o WHERE o.deleted_at IS NULL
  AND EXISTS(SELECT 1 FROM catalog_shops s WHERE s.id=o.shop_id AND s.deleted_at IS NULL)`))[0].n;

describe.sequential('offers_total: recount only for shop/offer batches, converges after product-only bursts', () => {
  beforeAll(async () => {
    await worker.migrate();
    await send([ev('category', 'gpu', { slug: 'gpu', name: 'GPU' }), ev('shop', 'sa', { name: 'A', url_public: 'https://a.example' }), ev('shop', 'sb', { name: 'B', url_public: 'https://b.example' }),
      ev('product', 'p1', gpu('p1')), ev('product', 'p2', gpu('p2')), ev('product', 'p3', gpu('p3'))]);
    await send([ev('offer', 'o1', offer('p1', 'sa')), ev('offer', 'o2', offer('p1', 'sb')), ev('offer', 'o3', offer('p2', 'sa'))]);
  }, 30000);
  afterAll(() => worker.dispose());

  test('the recount statement is emitted only when the batch has a shop or offer event (always without events)', () => {
    const has = (events?: Array<{ entity: string; entity_id: string; operation: string }>) => finalizeStatements(events as never).some(s => s.sql.includes('offers_total'));
    expect(has()).toBe(true);
    expect(has([{ entity: 'product', entity_id: 'p', operation: 'upsert' }, { entity: 'category', entity_id: 'c', operation: 'delete' }])).toBe(false);
    expect(has([{ entity: 'product', entity_id: 'p', operation: 'upsert' }, { entity: 'offer', entity_id: 'o', operation: 'upsert' }])).toBe(true);
    expect(has([{ entity: 'shop', entity_id: 's', operation: 'delete' }])).toBe(true);
    expect(has([{ entity: 'offer', entity_id: 'o', operation: 'delete' }])).toBe(true);
  });

  test('after the first sync the total is exact and converged', async () => {
    const s = await state();
    expect(s.offers_total).toBe(3); expect(await active()).toBe(3); expect(s.totals_version).toBe(s.facets_version);
  });

  test('a burst of 50 product-only batches bumps facets_version, does not recount, and leaves the total correct', async () => {
    const before = await state();
    for (let i = 0; i < 50; i++) await send([ev('product', `burst-${i}`, gpu(`burst-${i}`))]);
    const after = await state();
    expect(after.facets_version).toBe(before.facets_version + 50);
    expect(after.totals_version).toBe(before.totals_version);                                   // not recounted: stays behind
    expect(after.offers_total).toBe(3); expect(await active()).toBe(3);
  });

  test('the next offer batch recounts and converges totals_version', async () => {
    await send([ev('offer', 'o4', offer('p3', 'sa'))]);
    const s = await state();
    expect(s.offers_total).toBe(4); expect(await active()).toBe(4); expect(s.totals_version).toBe(s.facets_version);
  });

  test('a price-only refresh of an existing offer keeps the total (no facets bump, no recount needed)', async () => {
    const before = await state();
    await send([ev('offer', 'o4', offer('p3', 'sa', 45000))]);
    const after = await state();
    expect(after.offers_total).toBe(4); expect(after.facets_version).toBe(before.facets_version); expect(after.totals_version).toBe(after.facets_version);
  });

  test('shop soft-delete drops its offers from the total; restoring it brings them back, even after product-only batches in between', async () => {
    await send([ev('shop', 'sb', undefined, 'delete')]);
    let s = await state();
    expect(s.offers_total).toBe(3); expect(await active()).toBe(3); expect(s.totals_version).toBe(s.facets_version);   // o2 belonged to sb
    for (let i = 0; i < 5; i++) await send([ev('product', `after-delete-${i}`, gpu(`after-delete-${i}`))]);
    expect((await state()).offers_total).toBe(3);                                                // unchanged: nothing recounted, nothing changed
    await send([ev('shop', 'sb', { name: 'B', url_public: 'https://b.example' })]);               // restore
    s = await state();
    expect(s.offers_total).toBe(4); expect(await active()).toBe(4); expect(s.totals_version).toBe(s.facets_version);
  });

  test('offer delete and insert are reflected exactly', async () => {
    await send([ev('offer', 'o1', undefined, 'delete')]);
    expect((await state()).offers_total).toBe(3); expect(await active()).toBe(3);
    await send([ev('product', 'between', gpu('between'))]);                                      // product-only batch between the two offer batches
    await send([ev('offer', 'o5', offer('p2', 'sb')), ev('offer', 'o6', offer('p3', 'sb'))]);
    const s = await state();
    expect(s.offers_total).toBe(5); expect(await active()).toBe(5); expect(s.totals_version).toBe(s.facets_version);
  });
});
