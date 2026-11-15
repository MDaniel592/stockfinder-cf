import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { createHmac, randomUUID } from 'node:crypto';
import { testWorker } from './helpers/worker-db';

// Per-shop freshness mark (shop_check events / shop_checks table): effective offer freshness, exceptions, ingestion cost.
const secret = 'synthetic-test-secret-not-for-deployment-1234';
const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString();
const assets = { ASSETS: async () => new Response('<!doctype html><title>t</title><noscript></noscript>', { headers: { 'content-type': 'text/html' } }) };

function harness() {
  const worker = testWorker({ serviceBindings: assets, bindings: { SYNC_SECRET: secret, DATA_MODE: 'synthetic', STALE_AFTER_SECONDS: '3600' } });
  let eventId = 0; const versions = new Map<string, number>();
  const ev = (entity: string, id: string, data: unknown, version?: number) => {
    const key = `${entity}:${id}`, v = version ?? (versions.get(key) ?? 0) + 1; versions.set(key, Math.max(v, versions.get(key) ?? 0));
    return { event_id: ++eventId, entity, entity_id: id, version: v, operation: 'upsert', changed_at: new Date().toISOString(), data };
  };
  const send = async (events: unknown[]) => {
    const body = JSON.stringify({ batch_id: randomUUID(), schema_version: 1, events }), nonce = randomUUID(), ts = String(Math.floor(Date.now() / 1000));
    return worker.mf.dispatchFetch('https://test/internal/sync', { method: 'POST', body, headers: { 'content-type': 'application/json', 'x-sync-timestamp': ts, 'x-sync-nonce': nonce,
      'x-sync-signature': createHmac('sha256', secret).update(`${ts}.${nonce}.${body}`).digest('hex') } });
  };
  // A freshness-only offer refresh does not move data_version (existing design), so cached bodies may lag: bypass with no-cache where it matters.
  const api = async (path: string, fresh = false) => { const response = await worker.mf.dispatchFetch(`https://test/api/v1/${path}`, fresh ? { headers: { 'cache-control': 'no-cache' } } : {}); return { response, body: await response.json() as any }; };
  const offer = (product_id: string, shop_id: string, last_checked_at: string, price_cents = 50000) =>
    ({ product_id, shop_id, price_cents, in_stock: true, last_checked_at, url_public: 'https://example.com/o' });
  const mark = (checked_at: string, ...unconfirmed_offer_ids: string[]) => ({ checked_at, unconfirmed_offer_ids });
  const gpu = (slug: string) => ({ slug, name: slug, category_id: 'gpu', brand: 'Acme', specs: { Memoria: '16 GB' } });
  const versionsRow = async () => (await worker.query('SELECT data_version,facets_version FROM sync_status WHERE id=1'))[0] as { data_version: number; facets_version: number };
  const checked = async (path: string) => Object.fromEntries(((await api(path, true)).body.data as any[]).map(o => [o.id, o.last_checked_at]));
  return { worker, ev, send, api, offer, mark, gpu, versionsRow, checked };
}

describe.sequential('shop freshness mark', () => {
  const h = harness();
  const own = hoursAgo(20), markAt = hoursAgo(0.2);
  beforeAll(async () => {
    await h.worker.migrate();
    await h.send([h.ev('category', 'gpu', { slug: 'gpu', name: 'GPU' }), h.ev('shop', 'sa', { name: 'A', url_public: 'https://a.example' }),
      h.ev('shop', 'sb', { name: 'B', url_public: 'https://b.example' }), h.ev('product', 'p1', h.gpu('p1')), h.ev('product', 'p2', h.gpu('p2'))]);
    await h.send([h.ev('offer', 'o1', h.offer('p1', 'sa', own)), h.ev('offer', 'o2', h.offer('p1', 'sb', own)), h.ev('offer', 'o3', h.offer('p2', 'sa', own))]);
  }, 30000);
  afterAll(() => h.worker.dispose());

  test('without a shop mark every offer keeps its own date (old behaviour)', async () => {
    expect(await h.checked('products/p1/offers')).toEqual({ o1: own, o2: own });
    expect((await h.api('products/p2')).body.data.last_checked_at).toBe(own);
  });

  test('the mark freshens the shop offers except the unconfirmed ones; other shops are unaffected', async () => {
    const before = await h.versionsRow();
    const rows = (await h.worker.query('SELECT id,version,last_checked_at FROM catalog_offers ORDER BY id')) as any[];
    expect((await h.send([h.ev('shop_check', 'sa', h.mark(markAt, 'o3'))])).status).toBe(200);
    expect(await h.checked('products/p1/offers')).toEqual({ o1: markAt, o2: own });         // o1 marked, o2 shop has no mark
    expect((await h.api('products/p1')).body.data.last_checked_at).toBe(markAt);             // MAX over the product effective dates
    expect((await h.api('products/p2')).body.data.last_checked_at).toBe(own);                // its only offer is unconfirmed
    expect((await h.worker.query('SELECT unconfirmed FROM shop_checks WHERE id=?', 'sa'))[0]).toEqual({ unconfirmed: '["o3"]' });
    // Ingestion: neither cache key moves (freshness-only, no eviction) and no offer row is touched.
    const after = await h.versionsRow();
    expect(after.data_version).toBe(before.data_version);   // freshness-only: never evicts caches
    expect(after.facets_version).toBe(before.facets_version);
    expect(await h.worker.query('SELECT id,version,last_checked_at FROM catalog_offers ORDER BY id')).toEqual(rows);
  });

  test('status: newest mark drives offers_checked_at / stale; the hourly fresh count uses effective dates', async () => {
    // The share is cached for an hour (by design, one scan per hour) and a shop mark does not bump facets_version: drop the cached row and
    // reset the isolate memo with a replayed (ignored) event to force one recount that applies the effective dates.
    await h.worker.query("DELETE FROM api_cache WHERE key LIKE 'hourly:fresh%'");
    await h.send([h.ev('shop_check', 'sa', h.mark(markAt, 'o3'), 1)]);
    const s = (await h.api('catalog/status')).body;
    expect(s.offers_checked_at).toBe(markAt); expect(s.stale).toBe(false);
    expect(s.offers_total).toBe(3); expect(s.offers_fresh).toBe(1);                           // only o1 is inside the 12 h window
  });

  test('own newer date beats an older mark; a newer mark replaces the exceptions; stale replays are ignored', async () => {
    const newer = hoursAgo(0.05);
    await h.send([h.ev('offer', 'o3', h.offer('p2', 'sa', newer))]);                         // excepted offer is re-checked individually
    expect((await h.api('products/p2', true)).body.data.last_checked_at).toBe(newer);
    const second = hoursAgo(0.01);
    await h.send([h.ev('shop_check', 'sa', h.mark(second))]);                                // later pass confirms everything
    expect((await h.checked('products/p1/offers')).o1).toBe(second);
    expect((await h.send([h.ev('shop_check', 'sa', h.mark(hoursAgo(10), 'o1'), 1)])).status).toBe(200);   // replay of version 1
    expect((await h.checked('products/p1/offers')).o1).toBe(second);
  });

  test('contract: strict payload and id/size limits', async () => {
    expect((await h.send([h.ev('shop_check', 'sa', { checked_at: markAt })])).status).toBe(400);
    expect((await h.send([h.ev('shop_check', 'sa', { ...h.mark(markAt), extra: 1 })])).status).toBe(400);
    expect((await h.send([h.ev('shop_check', 'sa', h.mark(markAt, 'bad"id'))])).status).toBe(400);
    expect((await h.send([h.ev('shop_check', 'sa', h.mark('yesterday'))])).status).toBe(400);
    expect((await h.send([h.ev('shop_check', 'sa', { checked_at: markAt, unconfirmed_offer_ids: Array.from({ length: 1001 }, (_, i) => `o${i}`) })])).status).toBe(400);
  });

  test('highlights query still runs with the effective freshness', async () => {
    const { response, body } = await h.api('catalog/highlights');
    expect(response.status).toBe(200); expect(body).toHaveProperty('data');
  });
});

describe.sequential('stale follows the shop mark', () => {
  const h = harness();
  beforeAll(async () => {
    await h.worker.migrate();
    await h.send([h.ev('category', 'gpu', { slug: 'gpu', name: 'GPU' }), h.ev('shop', 'sa', { name: 'A', url_public: 'https://a.example' }), h.ev('product', 'p1', h.gpu('p1'))]);
    await h.send([h.ev('offer', 'o1', h.offer('p1', 'sa', hoursAgo(5)))]);
  }, 30000);
  afterAll(() => h.worker.dispose());
  test('old offers are stale until the shop is confirmed again, without any offer write', async () => {
    const own = (await h.worker.query('SELECT last_checked_at FROM catalog_offers WHERE id=?', 'o1'))[0];
    const old = (await h.api('catalog/status')).body;
    expect(old.stale).toBe(true); expect(old.offers_checked_at).not.toBeNull();
    await h.send([h.ev('shop_check', 'sa', h.mark(new Date().toISOString()))]);
    expect((await h.api('catalog/status')).body.stale).toBe(false);
    expect((await h.worker.query('SELECT last_checked_at FROM catalog_offers WHERE id=?', 'o1'))[0]).toEqual(own);
  });
});

describe.sequential('hourly fresh count cache', () => {
  const h = harness();
  const hour = () => Math.floor(Date.now() / 3600_000);
  beforeAll(async () => {
    await h.worker.migrate();
    await h.send([h.ev('category', 'gpu', { slug: 'gpu', name: 'GPU' }), h.ev('shop', 'sa', { name: 'A', url_public: 'https://a.example' }),
      ...['p1', 'p2', 'p3'].map(p => h.ev('product', p, h.gpu(p)))]);
    await h.send(['p1', 'p2', 'p3'].map((p, i) => h.ev('offer', `o${i + 1}`, h.offer(p, 'sa', hoursAgo(1)))));
  }, 30000);
  afterAll(() => h.worker.dispose());
  // Regression: the lookup used 'hourly:fresh'||?||... and D1 binds the number 12 as REAL ('hourly:fresh12.0'), so it never matched
  // the key written from JS and the whole-table scan ran on every status computation. The key is now built in JS (hour only).
  test('the stored count is read back (a sentinel row wins over the scan) and the scan result is stored under the same key', async () => {
    const fv = ((await h.worker.query('SELECT facets_version FROM sync_status WHERE id=1'))[0] as { facets_version: number }).facets_version, key = `hourly:fresh12:${fv}`;
    await h.worker.query('INSERT OR REPLACE INTO api_cache(key,version,body) VALUES(?,?,?)', key, hour(), '1');
    await h.send([h.ev('shop_check', 'sa', h.mark(new Date().toISOString()))]);           // sync() resets the status memo
    expect((await h.api('catalog/status')).body.offers_fresh).toBe(1);
    await h.worker.query('DELETE FROM api_cache WHERE key=?', key);
    await h.send([h.ev('shop_check', 'sa', h.mark(new Date().toISOString(), 'o3'))]);
    expect((await h.api('catalog/status')).body.offers_fresh).toBe(3);
    expect(await h.worker.query('SELECT version,body FROM api_cache WHERE key=?', key)).toEqual([{ version: hour(), body: '3' }]);
  });
});
