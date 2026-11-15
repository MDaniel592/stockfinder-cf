import { afterAll, beforeAll, describe, expect, test } from 'vitest';
import { testWorker } from './helpers/worker-db';
import { d1Db, type Db } from '../apps/edge-api/src/db';
import { highlights } from '../apps/edge-api/src/highlights';
import { finalizeStatements } from '../apps/edge-api/src/aggregates';
import { persistedHighlights } from '../apps/edge-api/src/cache';

// Landing highlights: the rewritten drops query (migration 0010) is equivalent to the old window-function query, it seeks the new
// index instead of scanning the history, the payload is persisted per (facets_version, hour), and the api_cache sweep is guarded.
const assets = { ASSETS: async () => new Response('x') };
const rng = (seed: number) => () => { seed |= 0; seed = (seed + 0x6D2B79F5) | 0; let t = Math.imul(seed ^ (seed >>> 15), 1 | seed); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
const lit = (v: unknown) => v === null ? 'NULL' : typeof v === 'number' ? String(v) : `'${String(v).replace(/'/g, "''")}'`;

// The query as it was before migration 0010 (window functions over the whole history of the recently changed offers).
const OLD_DROPS_SQL = `WITH h AS (SELECT h.offer_id,h.product_id,h.price_cents,h.in_stock,h.observed_at,
        LAG(h.price_cents) OVER w AS prev_price, LAG(h.in_stock) OVER w AS prev_stock,
        ROW_NUMBER() OVER (PARTITION BY h.offer_id ORDER BY h.observed_at DESC,h.id DESC) AS rn
        FROM offer_price_history h
        WHERE h.offer_id IN (SELECT DISTINCT offer_id FROM offer_price_history WHERE observed_at>=?)
        WINDOW w AS (PARTITION BY h.offer_id ORDER BY h.observed_at,h.id))
      SELECT p.id,(SELECT MAX(x.last_checked_at) FROM catalog_offers x WHERE x.product_id=p.id AND x.deleted_at IS NULL) AS last_checked_at,
        h.price_cents,h.prev_price,h.observed_at,s.name AS shop_name
      FROM h JOIN catalog_offers o ON o.id=h.offer_id AND o.deleted_at IS NULL AND o.in_stock=1 AND o.price_cents=h.price_cents
        JOIN catalog_shops s ON s.id=o.shop_id AND s.deleted_at IS NULL
        JOIN catalog_products p ON p.id=h.product_id AND p.deleted_at IS NULL AND p.min_price_in_stock_cents=h.price_cents
        JOIN catalog_categories c ON c.id=p.category_id AND c.deleted_at IS NULL
      WHERE h.rn=1 AND h.observed_at>=? AND h.in_stock=1 AND h.prev_stock=1
        AND h.price_cents*100<=h.prev_price*99 AND h.price_cents*10>=h.prev_price*3
      ORDER BY (h.prev_price-h.price_cents)*1.0/h.prev_price DESC,h.observed_at DESC LIMIT 40`;

/** Counts D1 rows_read / rows_written over everything run through the returned Db. */
function metered(raw: any) {
  const c = { read: 0, written: 0 };
  const add = (r: any) => { c.read += r.meta?.rows_read ?? 0; c.written += r.meta?.rows_written ?? 0; };
  const wrap = (s: any): any => ({ _s: s, bind: (...a: unknown[]) => wrap(s.bind(...a)),
    all: async () => { const r = await s.all(); add(r); return r; },
    first: async () => { const r = await s.all(); add(r); return r.results[0] ?? null; },
    run: async () => { const r = await s.run(); add(r); return r; } });
  const db: any = { prepare: (sql: string) => wrap(raw.prepare(sql)), batch: async (stmts: any[]) => { const rs = await raw.batch(stmts.map(x => x._s)); rs.forEach(add); return rs; } };
  return { db: d1Db(db), c, reset: () => { c.read = 0; c.written = 0; } };
}

/** Deterministic catalogue: products with 1–2 offers, a 60-day price walk per offer (incl. same-timestamp ties), aggregates kept consistent. */
async function seed(raw: any, products: number, now: number) {
  const R = rng(7), day = 86400_000, iso = (t: number) => new Date(t).toISOString(), shops = ['coolmod', 'pccomponentes'];
  const rows: Record<string, string[]> = {};
  const add = (table: string, cols: string, ...values: unknown[]) => (rows[`${table}|${cols}`] ??= []).push(`(${values.map(lit).join(',')})`);
  ['cpu', 'gpu'].forEach(c => add('catalog_categories', 'id,slug,name,version,changed_at', c, c, c.toUpperCase(), 1, iso(now)));
  shops.forEach(s => add('catalog_shops', 'id,name,url_public,version,changed_at', s, s, `https://${s}.example`, 1, iso(now)));
  let n = 0;
  for (let p = 0; p < products; p++) {
    const offers = p % 7 === 0 ? 1 : 2; let minIn: number | null = null, inCount = 0;
    for (let k = 0; k < offers; k++) {
      const id = `o${n++}`; let price = Math.round(5000 + R() * 145000), stock = 1;
      const times = Array.from({ length: 1 + Math.floor(R() * 12) }, () => Math.round(now - R() * 60 * day)).sort((a, b) => a - b);
      if (times.length > 2 && R() < 0.4) times[times.length - 1] = times[times.length - 2];   // tie: same observed_at, ordered by id
      times.forEach((t, i) => {
        if (i > 0) { price = Math.max(500, Math.round(price * (R() < 0.55 ? 0.7 + R() * 0.29 : 1 + R() * 0.2))); stock = R() < 0.9 ? 1 : 0; }
        add('offer_price_history', 'offer_id,product_id,shop_id,price_cents,in_stock,observed_at', id, `p${p}`, shops[k], price, stock, iso(t));
      });
      if (stock) { inCount++; minIn = minIn === null ? price : Math.min(minIn, price); }
      add('catalog_offers', 'id,product_id,shop_id,price_cents,in_stock,last_checked_at,url_public,version,changed_at', id, `p${p}`, shops[k], price, stock, iso(now - R() * 3 * 3600_000), 'https://x.example/o', 1, iso(now));
    }
    add('catalog_products', 'id,slug,name,category_id,brand,specs,version,changed_at,min_price_in_stock_cents,offers_count,in_stock_count',
      `p${p}`, `p${p}`, `Product ${p}`, p % 2 ? 'gpu' : 'cpu', `Brand${p % 5}`, '{}', 1, iso(now), minIn, offers, inCount);
  }
  for (const [key, values] of Object.entries(rows)) {
    const [table, cols] = key.split('|');
    for (let i = 0; i < values.length; i += 400) await raw.exec(`INSERT INTO ${table}(${cols}) VALUES ${values.slice(i, i + 400).join(',')}`);
  }
}

describe.sequential('highlights: drops query, persisted payload, api_cache sweep guard', () => {
  const w = testWorker({ serviceBindings: assets, bindings: { DATA_MODE: 'synthetic' } });
  let raw: any; const now = Date.now();
  beforeAll(async () => { await w.migrate(); raw = await w.mf.getD1Database('DB'); await seed(raw, 400, now); }, 120000);
  afterAll(() => w.dispose());

  test('migration 0010 adds the offer-history index and the sweep token column', async () => {
    const idx = (await w.query<{ name: string }>("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='offer_price_history'")).map(r => r.name);
    expect(idx).toContain('offer_price_history_offer');
    expect((await w.query<{ name: string }>("SELECT name FROM pragma_table_info('sync_status')")).map(r => r.name)).toContain('cache_swept');
  });

  test('the drops query matches the old window-function query over the last 24 hours', async () => {
    const since = new Date(now - 24 * 3600_000).toISOString();
    const old = (await raw.prepare(OLD_DROPS_SQL).bind(since, since).all()).results as any[];
    expect(old.length).toBeGreaterThan(3);                                                      // the seed produces real drops
    const seen = new Set<string>();
    const expected = old.filter(r => !seen.has(r.id) && seen.add(r.id)).slice(0, 8)
      .map(r => ({ id: r.id, min_price_cents: r.price_cents, previous_price_cents: r.prev_price, dropped_at: r.observed_at, shop_name: r.shop_name, last_checked_at: r.last_checked_at }));
    const got = (await highlights(d1Db(raw), now, [])).drops
      .map(d => ({ id: d.id, min_price_cents: d.min_price_cents, previous_price_cents: d.previous_price_cents, dropped_at: d.dropped_at, shop_name: d.shop_name, last_checked_at: d.last_checked_at }));
    expect(got).toEqual(expected);
  });

  test('the query seeks the history through the new index and never scans the history table', async () => {
    let captured: Array<{ sql: string; params: unknown[] }> = [];
    await highlights({ pg: false, reads: async (s: typeof captured) => { captured = s; return [[], []]; } } as unknown as Db, now, []);
    const plan = ((await raw.prepare(`EXPLAIN QUERY PLAN ${captured[1].sql}`).bind(...captured[1].params).all()).results as any[]).map(r => String(r.detail));
    expect(plan.some(d => d.includes('offer_price_history_offer'))).toBe(true);
    expect(plan.filter(d => /^SCAN (l|q|n|h|offer_price_history)\b/.test(d))).toEqual([]);
  });

  test('rows read by the drops query drop by an order of magnitude versus the old query', async () => {
    const since = new Date(now - 24 * 3600_000).toISOString();
    const oldRead = (await raw.prepare(OLD_DROPS_SQL).bind(since, since).all()).meta.rows_read as number;
    const m = metered(raw); await highlights(m.db, now, []);
    expect(m.c.read).toBeLessThan(oldRead / 3);
  });

  test('without a persisted payload and with a failing database there is nothing to serve (the error surfaces)', async () => {
    const broken = { ...d1Db(raw), first: async () => { throw new Error('d1 down'); } } as Db;
    await expect(persistedHighlights({ db: broken }, async () => ({ n: 1 }))).rejects.toThrow('d1 down');
  });

  test('persisted payload: ONE row per hour (no facets_version in the key); a miss computes and stores, a hit reads 1 row and does not compute, a new hour recomputes', async () => {
    const m = metered(raw), env = { db: m.db }; let computed = 0;
    const compute = async () => { computed++; return { categories: [], drops: [], n: computed }; };
    const hour = Math.floor(Date.now() / 3600_000);
    expect((await persistedHighlights(env, compute)).data.n).toBe(1);
    expect(await w.query('SELECT version,length(body)>0 AS body FROM api_cache WHERE key=?', 'hourly:highlights:24h')).toEqual([{ version: hour, body: 1 }]);
    m.reset(); expect((await persistedHighlights(env, compute)).data.n).toBe(1); expect(computed).toBe(1);
    expect(m.c.read).toBe(1);                                                                   // the api_cache row, nothing else
    await raw.prepare('UPDATE api_cache SET version=version-1 WHERE key=?').bind('hourly:highlights:24h').run();
    expect((await persistedHighlights(env, compute)).data.n).toBe(2);                                // hour rolled over: the stale row is not served
  });

  test('50 consecutive structural batches (facets_version bumps) cause 0 extra recomputes within the hour, and the row survives every sweep', async () => {
    const db = d1Db(raw), env = { db }; let computed = 0;
    const compute = async () => { computed++; return { categories: [], drops: [], n: 100 + computed }; };
    await persistedHighlights(env, compute); expect(computed).toBe(0);                          // the hour row from the previous test is served
    const f0 = (await w.query<{ f: number }>('SELECT facets_version AS f FROM sync_status WHERE id=1'))[0].f;
    for (let i = 0; i < 50; i++) {
      await raw.prepare('UPDATE sync_status SET facets_version=facets_version+1,data_version=data_version+1 WHERE id=1').run();
      await db.batch(finalizeStatements());                                                     // the sweep that closes every sync batch
      await persistedHighlights(env, compute);                                                  // a request between two batches
    }
    expect(computed).toBe(0);
    expect((await w.query<{ f: number }>('SELECT facets_version AS f FROM sync_status WHERE id=1'))[0].f).toBe(f0 + 50);
    expect(await w.query("SELECT count(*) AS n FROM api_cache WHERE key LIKE 'hourly:highlights:24h%'")).toEqual([{ n: 1 }]);
  }, 30000);

  test('a failing database serves the last known payload', async () => {
    const broken = { ...d1Db(raw), first: async () => { throw new Error('d1 down'); } } as Db;
    const value = await persistedHighlights({ db: broken }, async () => ({ n: 99 }));
    expect(value).toMatchObject({ data: { n: 2 }, stale: true });                                                 // the last payload this isolate computed
  });

  test('finalizeStatements: the api_cache sweep is skipped (≈1 row read) unless the structure version or the hour changed', async () => {
    const fill = async (n: number, version: number) => { await raw.exec('DELETE FROM api_cache'); await raw.exec(`INSERT INTO api_cache(key,version,body) VALUES ${Array.from({ length: n }, (_, i) => `('list:${i}',${version},'{}')`).join(',')}`); };
    const fv = async () => (await w.query<{ facets_version: number }>('SELECT facets_version FROM sync_status WHERE id=1'))[0].facets_version;
    const hour = Math.floor(Date.now() / 3600_000);
    for (const n of [50, 500]) {
      await fill(n, await fv());
      const m = metered(raw);
      await m.db.batch(finalizeStatements());                                                   // sets the token (may sweep)
      m.reset(); await m.db.batch(finalizeStatements());
      expect(m.c.read).toBeLessThan(15); expect(m.c.written).toBe(0);                           // before the guard: n + 2 reads
      expect((await w.query<{ n: number }>('SELECT count(*) AS n FROM api_cache'))[0].n).toBe(n);
    }
    // structure change: rows of the old version are evicted, current and hourly-current rows survive
    const m = metered(raw);
    await raw.exec('DELETE FROM api_cache');
    await raw.exec(`INSERT INTO api_cache(key,version,body) VALUES ('list:a',${await fv()},'{}'),('hourly:x',${hour},'{}'),('hourly:highlights:24h',${hour},'{}'),('hourly:old',${hour - 1},'{}')`);
    await raw.prepare('UPDATE sync_status SET facets_version=facets_version+1 WHERE id=1').run();
    await raw.exec(`INSERT INTO api_cache(key,version,body) VALUES ('list:b',${await fv()},'{}')`);
    await m.db.batch(finalizeStatements());
    expect((await w.query<{ key: string }>('SELECT key FROM api_cache ORDER BY key')).map(r => r.key)).toEqual(['hourly:highlights:24h', 'hourly:x', 'list:b']);   // current-hour highlights row survives the structural bump
    // hour rollover with the same structure version: a token from the previous hour triggers the sweep and evicts hourly rows of the old hour
    await raw.prepare('UPDATE sync_status SET cache_swept=? WHERE id=1').bind(`${await fv()}.${hour - 1}`).run();
    await raw.exec(`INSERT INTO api_cache(key,version,body) VALUES ('hourly:old2',${hour - 1},'{}')`);
    await raw.prepare("UPDATE api_cache SET version=? WHERE key='hourly:highlights:24h'").bind(hour - 1).run();   // the highlights row is now from the previous hour
    await m.db.batch(finalizeStatements());
    expect((await w.query<{ key: string }>('SELECT key FROM api_cache ORDER BY key')).map(r => r.key)).toEqual(['hourly:x', 'list:b']);
  }, 30000);
});
