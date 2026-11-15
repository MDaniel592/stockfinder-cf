import { createRequire } from 'node:module';
import { mkdir, mkdtemp, readFile, readdir, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { expect, test } from 'vitest';
const require_ = createRequire(import.meta.url);
const { extractIdentity, validEan } = require_('../nas/scrapers/coolmod.cjs');
const { runEnrich, selectTargets, buildCandidate } = require_('../nas/scrapers/coolmod-enrich.cjs');

const url = (n: number) => `https://www.coolmod.com/item-${n}/`;
const product = (n: number, extra: Record<string, unknown> = {}) => ({
  '@type': 'Product', sku: `PROD-${n}`, mpn: `MPN-${n}`, url: url(n).replace(/\/$/, ''), brand: { name: 'Acme' },
  offers: { '@type': 'Offer', priceCurrency: 'EUR', price: '77.95', availability: 'https://schema.org/InStock' }, ...extra,
});
const flix = (n: number) => ({ mpn: `MPN-${n}`, ean: '4719331551759' });

test('EAN needs a valid GS1 check digit', () => {
  expect(validEan('4719331551759')).toBe('4719331551759');
  expect(validEan('4719331551750')).toBeNull();
  expect(validEan('123')).toBeNull();
});

test('identity comes from the JSON-LD Product of the requested page, never from another product', () => {
  const documents = [{ '@type': 'LocalBusiness' }, product(1), product(2)];
  const identity = extractIdentity(documents, { url_public: url(2), flix: flix(2) });
  expect(identity).toMatchObject({ retailer_sku: 'PROD-2', mpn: 'MPN-2', ean: '4719331551759', price_cents: 7795, in_stock: true, seller_direct: true });
  expect(() => extractIdentity([product(1), product(2)], { url_public: 'https://www.coolmod.com/other/' })).toThrow(/ambiguous/);
  expect(() => extractIdentity([], { url_public: url(1) })).toThrow(/absent/);
  expect(() => extractIdentity([product(1)], { url_public: url(1), flix: { mpn: 'DIFFERENT' } })).toThrow(/disagree/);
});

test('invalid EAN is dropped, unknown stock stays null and a foreign seller is not direct', () => {
  const odd = product(3, { offers: { priceCurrency: 'EUR', price: '10', availability: 'https://schema.org/PreOrder', seller: { name: 'Other Shop' } } });
  const identity = extractIdentity([odd], { url_public: url(3), flix: { ean: '4719331551750' } });
  expect(identity).toMatchObject({ ean: null, in_stock: null, seller_direct: false });
  const candidate = buildCandidate({ url_public: url(3), name: 'N' }, identity, '2026-01-01T00:00:00Z');
  expect(candidate.missing_fields).toEqual(expect.arrayContaining(['ean', 'in_stock', 'direct_seller_verification']));
  expect(candidate.in_stock).toBeNull();
});

async function setup(count: number) {
  const root = await mkdtemp(path.join(tmpdir(), 'enrich-'));
  const config = { targetsFile: path.join(root, 'flow/enrich.json'), outputDir: path.join(root, 'out'), stateFile: path.join(root, 'state/s.json'),
    pageDelayMs: 2000, batchSize: 300, idleSeconds: 900, blockedSeconds: 21600 };
  const targets = Array.from({ length: count }, (_, i) => ({ review_id: i + 1, url_public: url(i + 1), retailer_sku: url(i + 1).replace(/\/$/, ''), name: `Item ${i + 1}`, brand: 'Acme', category_hint: 'psu' }));
  await mkdir(path.dirname(config.targetsFile), { recursive: true });
  await writeFile(config.targetsFile, JSON.stringify(targets));
  return { config, targets };
}
function deps(pages: (t: any) => any, sleeps: number[] = []) {
  return { launch: async () => ({ isConnected: () => true, newPage: async () => ({ close: async () => {} }), close: async () => {} }),
    sleep: async (ms: number) => { sleeps.push(ms); }, now: () => '2026-01-01T00:00:00.000Z',
    readPage: async (_p: unknown, t: any) => pages(t) };
}
const okPage = (t: any) => { const n = Number(t.url_public.match(/item-(\d+)/)[1]); return { status: 200, finalUrl: t.url_public, documents: [product(n)], flix: flix(n), title: 'x' }; };

test('a run waits >= 2 s between pages, writes batches and resumes without refetching', async () => {
  const { config } = await setup(3);
  const sleeps: number[] = [];
  const first = await runEnrich(config, deps(okPage, sleeps), { limit: 2 });
  expect(first).toMatchObject({ due: 2, ok: 2 });
  expect(sleeps.every((ms) => ms >= 2000)).toBe(true);
  const second = await runEnrich(config, deps(okPage, sleeps), {});
  expect(second).toMatchObject({ due: 1, ok: 1 });
  const files = await readdir(config.outputDir);
  expect(files.every((f) => /^enriched-.*\.json$/.test(f))).toBe(true);
  const records = (await Promise.all(files.map(async (f) => JSON.parse(await readFile(path.join(config.outputDir, f), 'utf8'))))).flat();
  expect(records.map((r: any) => r.mpn).sort()).toEqual(['MPN-1', 'MPN-2', 'MPN-3']);
  expect(records[0]).toMatchObject({ shop_id: 'coolmod', retailer_sku: expect.stringMatching(/^PROD-/), ean: '4719331551759' });
  expect((await runEnrich(config, deps(okPage), {})).due).toBe(0);
});

test('dry run writes nothing; a block stops the run and keeps the progress', async () => {
  const { config } = await setup(4);
  const dry = await runEnrich(config, deps(okPage), { dryRun: true });
  expect(dry.ok).toBe(4);
  await expect(readdir(config.outputDir)).rejects.toThrow();
  const blocked = await runEnrich(config, deps((t) => t.url_public === url(3) ? { status: 429, finalUrl: t.url_public, documents: [] } : okPage(t)), {});
  expect(blocked.blocked).toBe('HTTP 429');
  expect(blocked.ok).toBe(2);
  const state = JSON.parse(await readFile(config.stateFile, 'utf8'));
  expect(Object.keys(state.attempts)).toHaveLength(2);
  const resumed = await runEnrich(config, deps(okPage), {});
  expect(resumed).toMatchObject({ due: 2, ok: 2 });
});

test('404 and pages without MPN are recorded, not staged; selection honours cooldowns', async () => {
  const { config } = await setup(3);
  const pages = (t: any) => t.url_public === url(1) ? { status: 404, finalUrl: t.url_public, documents: [] }
    : t.url_public === url(2) ? { status: 200, finalUrl: t.url_public, documents: [product(2, { mpn: undefined })], flix: {}, title: '' } : okPage(t);
  const result = await runEnrich(config, deps(pages), {});
  expect(result).toMatchObject({ gone: 1, no_identity: 1, ok: 1 });
  const state = JSON.parse(await readFile(config.stateFile, 'utf8'));
  expect(selectTargets([{ review_id: 1, url_public: url(1) }], state)).toHaveLength(0);
  expect(selectTargets([{ review_id: 1, url_public: url(1) }], state, Date.now() + 8 * 24 * 3600e3)).toHaveLength(1);
  expect(selectTargets([{ review_id: 9, url_public: 'https://evil.example/x' }], { attempts: {} })).toHaveLength(0);
});
