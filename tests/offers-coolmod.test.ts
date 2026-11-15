import { createRequire } from 'node:module';
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test } from 'vitest';
const scraper = createRequire(import.meta.url)('../nas/scrapers/coolmod.cjs');
const { verifiedTarget, loadAliases, loadTargets, selectBatch, nextDelaySeconds, runCycle, configFromEnv, errorFields } = scraper;

const sku = (n: number) => `SKU-${String(n).padStart(3, '0')}`;
const url = (n: number) => `https://www.coolmod.com/item-${n}/`;
const target = (n: number) => ({ product_id: `p${n}`, retailer_sku: sku(n), mpn: `MPN-${n}`, url_public: url(n) });
const products = (count: number) => Array.from({ length: count }, (_, n) => ({ id: `p${n}`, mpn: `MPN-${n}` }));
const document = (n: number, availability = 'InStock') => ({
  '@type': 'Product', mpn: `MPN-${n}`, sku: sku(n),
  offers: { price: '10.50', priceCurrency: 'EUR', availability: `https://schema.org/${availability}` },
});

async function workspace(count: number) {
  const root = await mkdtemp(path.join(tmpdir(), 'coolmod-'));
  const dataDir = path.join(root, 'data');
  await mkdir(path.join(dataDir, 'retailers'), { recursive: true });
  await writeFile(path.join(dataDir, 'retailers/pilot.json'), '[]');
  await writeFile(path.join(dataDir, 'retailers/approved-coolmod-targets.json'), JSON.stringify(Array.from({ length: count }, (_, n) => target(n))));
  await writeFile(path.join(dataDir, 'product-identifiers.json'), '[]');
  const config = { flowFile: path.join(root, 'flow/targets/coolmod.json'), dataDir, output: path.join(root, 'out/offers/coolmod.json'),
    stateFile: path.join(root, 'out/state/state.json'), batchSize: 200, pageDelayMs: 2000, intervalSeconds: 5400, passTargetSeconds: 43200 };
  return { root, dataDir, config };
}
const sleeps: number[] = [];
function fakeDeps(count: number, pages: Record<string, any> = {}) {
  const launches = { count: 0 };
  const visited: string[] = [];
  const deps = {
    launch: async () => { launches.count++; return { isConnected: () => true, newPage: async () => ({ close: async () => {} }), close: async () => {} }; },
    loadProducts: async () => products(count),
    sleep: async (ms: number) => { sleeps.push(ms); },
    now: () => new Date().toISOString(),
    readPage: async (_page: unknown, t: any) => {
      visited.push(t.retailer_sku);
      const n = Number(t.retailer_sku.split('-')[1]);
      const behaviour = pages[t.retailer_sku];
      if (typeof behaviour === 'function') return behaviour();
      return { status: 200, finalUrl: t.url_public, documents: [document(n)] };
    },
  };
  return { deps, visited, launches };
}

test('aliases come from product-identifiers.json and every data/aliases/*.json, tolerating a missing directory', async () => {
  const { dataDir } = await workspace(1);
  const alias = { product_id: 'gpu', kind: 'mpn', value: 'STORE-CODE', source_url: 'https://www.coolmod.com/gpu/', note: 'Reviewed' };
  expect(await loadAliases(dataDir)).toEqual([]);
  await mkdir(path.join(dataDir, 'aliases'));
  expect(await loadAliases(dataDir)).toEqual([]);
  await writeFile(path.join(dataDir, 'aliases/batch-1.json'), JSON.stringify([alias]));
  await writeFile(path.join(dataDir, 'aliases/broken.json'), '{nope');
  await writeFile(path.join(dataDir, 'product-identifiers.json'), JSON.stringify([{ ...alias, value: 'OTHER' }]));
  const aliases = await loadAliases(dataDir);
  expect(aliases.map((a: any) => a.value).sort()).toEqual(['OTHER', 'STORE-CODE']);
  const gpu = { product_id: 'gpu', mpn: 'STORE-CODE', retailer_sku: 'S', url_public: alias.source_url };
  expect(verifiedTarget(gpu, [{ id: 'gpu', mpn: 'OFFICIAL' }], aliases)).toBe(true);
  expect(verifiedTarget(gpu, [{ id: 'gpu', mpn: 'OFFICIAL' }], [])).toBe(false);
});

test('targets prefer the flow file and fall back to the approved list', async () => {
  const { config } = await workspace(3);
  expect((await loadTargets(config)).source).toBe('approved');
  await mkdir(path.dirname(config.flowFile), { recursive: true });
  await writeFile(config.flowFile, JSON.stringify([target(7), { product_id: 'x' }]));
  const flow = await loadTargets(config);
  expect(flow.source).toBe('flow');
  expect(flow.targets.map((t: any) => t.retailer_sku)).toEqual(['SKU-007']);
  expect(flow.dropped).toBe(1);
  await writeFile(config.flowFile, '{truncated');
  expect((await loadTargets(config)).targets).toHaveLength(3);
});

test('rotation picks the oldest checks first and the delay keeps a full pass within the target', () => {
  const targets = [target(1), target(2), target(3)];
  const state = { attempted: { 'SKU-001': 300, 'SKU-002': 100 }, succeeded: {} };
  expect(selectBatch(targets, state, 2).map((t: any) => t.retailer_sku)).toEqual(['SKU-003', 'SKU-002']);
  const params = { batchSize: 200, intervalSeconds: 5400, passTargetSeconds: 43200, minSeconds: 900, lastDurationSeconds: 1200 };
  expect(nextDelaySeconds(802, params)).toBe(5400);
  expect(nextDelaySeconds(2000, params)).toBe(4320 - 1200);
  expect(nextDelaySeconds(100000, params)).toBe(900);
  expect(configFromEnv({ COOLMOD_BATCH_SIZE: '999', COOLMOD_PAGE_DELAY_MS: '10' }).batchSize).toBe(200);
  expect(configFromEnv({ COOLMOD_PAGE_DELAY_MS: '10' }).pageDelayMs).toBe(2000);
});

test('a cycle handles at most 200 targets, rotates across cycles and never aborts on a product failure', async () => {
  const { config } = await workspace(250);
  sleeps.length = 0;
  const pages: Record<string, any> = {
    'SKU-003': () => ({ status: 404, finalUrl: url(3), documents: [] }),
    'SKU-004': () => ({ status: 200, finalUrl: 'https://www.coolmod.com/', documents: [{ '@type': 'ItemList' }] }),
    'SKU-005': () => ({ status: 200, finalUrl: url(5), documents: [document(5, 'PreOrder')] }),
    'SKU-006': () => ({ status: 403, finalUrl: url(6), documents: [] }),
    'SKU-007': () => ({ status: 200, finalUrl: url(7), documents: [document(7, 'OutOfStock')] }),
  };
  const first = fakeDeps(250, pages);
  const one = await runCycle(config, first.deps);
  expect(first.visited).toHaveLength(200);
  expect(one.summary).toMatchObject({ batch: 200, ok: 196, gone: 2, failed: 2 });
  const out = JSON.parse(await readFile(config.output, 'utf8'));
  expect(out).toHaveLength(198);
  const gone = out.filter((r: any) => r.unavailable);
  expect(gone.map((r: any) => r.retailer_sku).sort()).toEqual(['SKU-003', 'SKU-004']);
  expect(gone[0]).toMatchObject({ in_stock: false, shop_id: 'coolmod' });
  expect(gone[0]).not.toHaveProperty('price_cents');
  expect(out.find((r: any) => r.retailer_sku === 'SKU-007')).toMatchObject({ in_stock: false, price_cents: 1050 });
  expect(sleeps.filter((ms) => ms < 2000)).toEqual([]);
  expect(sleeps.length).toBeGreaterThanOrEqual(199);

  const second = fakeDeps(250);
  await runCycle(config, second.deps);
  expect(second.visited).toHaveLength(200);
  const alreadyDone = new Set(first.visited);
  expect(second.visited.filter((sku) => !alreadyDone.has(sku))).toHaveLength(50);
  expect(new Set(second.visited.slice(0, 50)).size).toBe(50);
  expect(second.visited.slice(0, 50).every((sku) => !alreadyDone.has(sku))).toBe(true);
});

test('scraper_failure fields keep the errno message (EACCES visible) and mask secrets', () => {
  const eacces = Object.assign(new Error("EACCES: permission denied, open '/output/offers/coolmod.json.7.tmp'"), { code: 'EACCES' });
  expect(errorFields(eacces)).toEqual({ type: 'Error', code: 'EACCES', message: "EACCES: permission denied, open '/output/offers/coolmod.json.7.tmp'" });
  const leaky = errorFields(new TypeError('fetch https://user:pw@host/x?token=abc&page=2 failed'));
  expect(leaky.message).toBe('fetch https://***@host/x?token=***&page=2 failed');
  expect(leaky.code).toBeUndefined();
  expect(errorFields(new Error('x'.repeat(500))).message).toHaveLength(300);
});

test('transient errors are retried once; technical failures keep the previous data (nothing is written)', async () => {
  const { config } = await workspace(2);
  let calls = 0;
  const flaky = () => { calls++; if (calls === 1) { const e = new Error('Target closed'); e.name = 'TargetClosedError'; throw e; } return { status: 200, finalUrl: url(0), documents: [document(0)] }; };
  const ok = fakeDeps(2, { 'SKU-000': flaky });
  const result = await runCycle(config, ok.deps);
  expect(result.summary).toMatchObject({ ok: 2, failed: 0 });
  expect(calls).toBe(2);

  const { config: blockedConfig } = await workspace(2);
  const blocked = fakeDeps(2, { 'SKU-000': () => ({ status: 503, finalUrl: url(0), documents: [] }), 'SKU-001': () => ({ status: 403, finalUrl: url(1), documents: [] }) });
  const failed = await runCycle(blockedConfig, blocked.deps);
  expect(failed.records).toHaveLength(0);
  await expect(readFile(blockedConfig.output, 'utf8')).rejects.toThrow();
});
