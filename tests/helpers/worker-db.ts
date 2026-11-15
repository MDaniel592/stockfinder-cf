// Test Worker + database, D1 (default) or Postgres (TEST_PG_URL set: admin URL of a THROWAWAY server, see
// scripts/test_pg.sh). In Postgres mode each Worker instance gets its own fresh database with
// db/supabase-migrations applied, and the Worker connects through a Hyperdrive binding as the least-privileged
// `stockfinder_worker` role, so grants and RLS policies are exercised exactly as in Supabase.
import { Miniflare, type MiniflareOptions } from 'miniflare';
import postgres from 'postgres';
import { readFile, readdir } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';
import { numbered, pgTypes } from '../../apps/edge-api/src/db';

const ADMIN_URL = process.env.TEST_PG_URL;
export const dbMode: 'd1' | 'pg' = ADMIN_URL ? 'pg' : 'd1';
const WORKER_PASSWORD = 'synthetic-worker-password-for-tests';

export interface TestWorker {
  mf: Miniflare;
  /** Create the schema (D1 migrations or a fresh Postgres database). */
  migrate(): Promise<void>;
  /** Direct read for assertions; `?` placeholders in both dialects. */
  query<T = Record<string, any>>(sql: string, ...params: unknown[]): Promise<T[]>;
  dispose(): Promise<void>;
}

type Options = Omit<MiniflareOptions, 'd1Databases' | 'hyperdrives'> & Record<string, unknown>;

export function testWorker(options: Options): TestWorker {
  const base = { modules: true, scriptPath: 'dist/worker.js', compatibilityDate: '2026-01-01', compatibilityFlags: ['nodejs_compat'], ...options };
  if (!ADMIN_URL) {
    const mf = new Miniflare({ ...base, d1Databases: ['DB'] } as MiniflareOptions);
    return {
      mf,
      async migrate() {
        const db = await mf.getD1Database('DB');
        for (const file of (await readdir('db/d1-migrations')).filter(f => f.endsWith('.sql')).sort())
          await db.exec((await readFile(`db/d1-migrations/${file}`, 'utf8')).replace(/--[^\n]*/g, '').replace(/\n/g, ' '));
      },
      async query(sql, ...params) { return (await (await mf.getD1Database('DB')).prepare(sql).bind(...params).all()).results as never[]; },
      dispose: () => mf.dispose()
    };
  }
  const name = `sf_test_${randomUUID().replace(/-/g, '').slice(0, 12)}`;
  const admin = new URL(ADMIN_URL);
  const target = new URL(ADMIN_URL); target.pathname = `/${name}`;
  const worker = new URL(target); worker.username = 'stockfinder_worker'; worker.password = WORKER_PASSWORD;
  const mf = new Miniflare({ ...base, hyperdrives: { HYPERDRIVE: worker.toString() } } as MiniflareOptions);
  let direct: postgres.Sql | undefined;
  const server = () => postgres(admin.toString(), { max: 1, onnotice: () => undefined });
  return {
    mf,
    async migrate() {
      const sql = server();
      try {
        // Test files run in parallel and the role is cluster-wide: serialise role changes and migrations.
        await sql.unsafe('SELECT pg_advisory_lock(7300001)');
        await sql.unsafe(`CREATE DATABASE ${name}`);
        // The password is test-only.
        await sql.unsafe(`DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='stockfinder_worker') THEN CREATE ROLE stockfinder_worker NOLOGIN; END IF; END $$`);
        await sql.unsafe(`ALTER ROLE stockfinder_worker LOGIN PASSWORD '${WORKER_PASSWORD}'`);
        const db = postgres(target.toString(), { max: 1, onnotice: () => undefined });
        try {
          for (const file of (await readdir('db/supabase-migrations')).filter(f => f.endsWith('.sql')).sort())
            await db.unsafe(await readFile(`db/supabase-migrations/${file}`, 'utf8'));
        } finally { await db.end(); }
      } finally { await sql.end(); }
      direct = postgres(target.toString(), { max: 1, types: pgTypes as never, connection: { search_path: 'stockfinder' }, onnotice: () => undefined });
    },
    async query(text, ...params) { return [...await direct!.unsafe(numbered(text), params as never[])] as never[]; },
    async dispose() {
      await mf.dispose();
      await direct?.end();
      const sql = server();
      try { await sql.unsafe(`DROP DATABASE IF EXISTS ${name} WITH (FORCE)`); } finally { await sql.end(); }
    }
  };
}
