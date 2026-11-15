import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { createHmac, randomUUID } from 'node:crypto';
import { dbMode, testWorker } from './helpers/worker-db';

// Read-model details that matter for the Supabase migration. Runs in both modes; the privilege checks only on Postgres.
const secret = 'synthetic-test-secret-not-for-deployment-1234';
const worker = testWorker({ serviceBindings: { ASSETS: async () => new Response('<!doctype html><title>t</title><noscript></noscript>', { headers: { 'content-type': 'text/html' } }) },
  bindings: { SYNC_SECRET: secret, DATA_MODE: 'synthetic' } });
const mf = worker.mf;
let eventId = 0;
const now = new Date().toISOString();
const ev = (entity: string, id: string, data: unknown, version = 1) => ({ event_id: ++eventId, entity, entity_id: id, version, operation: 'upsert', changed_at: now, data });
const send = (events: unknown[]) => {
  const body = JSON.stringify({ batch_id: randomUUID(), schema_version: 1, events }), nonce = randomUUID(), ts = String(Math.floor(Date.now() / 1000));
  return mf.dispatchFetch('https://test/internal/sync', { method: 'POST', body, headers: { 'content-type': 'application/json', 'x-sync-timestamp': ts, 'x-sync-nonce': nonce,
    'x-sync-signature': createHmac('sha256', secret).update(`${ts}.${nonce}.${body}`).digest('hex') } });
};
const api = async (path: string) => (await mf.dispatchFetch(`https://test/api/v1/${path}`, { headers: { 'cache-control': 'no-cache' } })).json() as Promise<any>;

beforeAll(() => worker.migrate(), 30000);
afterAll(() => worker.dispose());

describe.sequential('read model fidelity', () => {
  test('specs keep the publisher key order and timestamps keep the D1 ISO format', async () => {
    const specs = { 'Zócalo': 'AM5', 'Arquitectura': 'Zen 5', 'Núcleos': '8', 'Bus': 'PCIe 5.0' };
    expect((await send([ev('category', 'cpu', { slug: 'cpu', name: 'Procesadores' }), ev('shop', 'sh', { name: 'Shop', url_public: 'https://example.com' }),
      ev('product', 'p1', { slug: 'p1', name: 'Ryzen Ñ 9700X', category_id: 'cpu', brand: 'AMD', specs, retailer_codes: [{ shop_id: 'sh', code: 'RC-9' }] }),
      ev('offer', 'o1', { product_id: 'p1', shop_id: 'sh', price_cents: 33333, in_stock: true, last_checked_at: '2026-11-09T10:00:00+02:00', url_public: 'https://example.com/p1' })])).status).toBe(200);
    const product = (await api('products/p1')).data;
    expect(Object.keys(product.specs)).toEqual(Object.keys(specs));
    expect(product.last_checked_at).toBe('2026-11-09T08:00:00.000Z');
    const offers = (await api('products/p1/offers')).data;
    expect(offers).toEqual([{ id: 'o1', shop_name: 'Shop', price_cents: 33333, in_stock: 1, last_checked_at: '2026-11-09T08:00:00.000Z', url_public: 'https://example.com/p1' }]);
    expect((await api('products/p1/price-history')).data[0].observed_at).toBe('2026-11-09T08:00:00.000Z');
    expect((await api('products?q=rc-9')).data.map((p: any) => p.id)).toEqual(['p1']);
    expect((await api('products?q=9700x+amd')).data.map((p: any) => p.id)).toEqual(['p1']);
  });
});

describe.runIf(dbMode === 'pg')('Postgres privileges', () => {
  test('the Worker role can only read the catalogue and call the sync/report functions', async () => {
    const [row] = await worker.query<Record<string, boolean>>(`SELECT
      has_table_privilege('stockfinder_worker','stockfinder.catalog_products','SELECT') AS read_products,
      has_table_privilege('stockfinder_worker','stockfinder.catalog_products','INSERT') AS write_products,
      has_table_privilege('stockfinder_worker','stockfinder.sync_status','UPDATE') AS write_status,
      has_table_privilege('stockfinder_worker','stockfinder.product_reports','SELECT') AS read_reports,
      has_table_privilege('stockfinder_worker','stockfinder.sync_nonces','SELECT') AS read_nonces,
      has_function_privilege('stockfinder_worker','stockfinder.sync_apply(text,json)','EXECUTE') AS run_sync,
      has_function_privilege('public','stockfinder.sync_apply(text,json)','EXECUTE') AS public_sync,
      (SELECT bool_and(relrowsecurity) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='stockfinder' AND c.relkind='r') AS rls_everywhere,
      (SELECT COUNT(*)=0 FROM pg_policies WHERE schemaname='stockfinder') AS no_policies`);
    expect(row).toEqual({ read_products: true, write_products: false, write_status: false, read_reports: false, read_nonces: false,
      run_sync: true, public_sync: false, rls_everywhere: true, no_policies: true });
  });
  test('search uses the trigram index', async () => {
    const plan = await worker.query<{ 'QUERY PLAN': string }>(`EXPLAIN SELECT id FROM stockfinder.catalog_products p
      WHERE stockfinder.search_text(p.name,p.brand,p.mpn,p.retailer_codes) LIKE '%ryzen%'`);
    await worker.query('SET enable_seqscan = off');
    const forced = (await worker.query<{ 'QUERY PLAN': string }>(`EXPLAIN SELECT id FROM stockfinder.catalog_products p
      WHERE stockfinder.search_text(p.name,p.brand,p.mpn,p.retailer_codes) LIKE '%ryzen%'`)).map(r => r['QUERY PLAN']).join('\n');
    expect(plan.length).toBeGreaterThan(0);
    expect(forced).toContain('products_search');
  });
});
