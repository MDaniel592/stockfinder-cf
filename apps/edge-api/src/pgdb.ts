// Postgres adapter (Supabase via Hyperdrive). Kept apart from ./db so the D1 production bundle can leave it, and
// postgres.js with its Node built-ins, out: ./db imports this module only in builds with `__SF_PG__` = true.
import postgres from 'postgres';
import { numbered, pgTypes, type Db, type Row, type Stmt } from './db';

const RETRYABLE = new Set(['XX000', '53300', '08001', '08006', 'CONNECT_TIMEOUT', 'CONNECTION_CLOSED', 'ECONNRESET', 'ECONNREFUSED']);

export function pgDb(connectionString: string): Db {
  // One connection per request: postgres.js pipelines queued queries on it. fetch_types=false avoids a catalogue
  // round trip at connect; prepare=false uses unnamed statements (no per-connection state to reuse anyway).
  const sql = postgres(connectionString, { max: 1, fetch_types: false, prepare: false, connect_timeout: 10, idle_timeout: 5,
    types: pgTypes as never, onnotice: () => undefined });
  const run = (q: postgres.Sql | postgres.TransactionSql, s: Stmt) => q.unsafe(numbered(s.sql), s.params as never[]);
  // Connection-level refusals (Supavisor session mode answers XX000 "max clients" when its pool of 15 is full; 53300;
  // dropped sockets) are retried twice with a short backoff: nothing ran, so a retry is safe even for writes.
  const exec = async (q: postgres.Sql | postgres.TransactionSql, s: Stmt) => {
    for (let attempt = 0; ; attempt++) {
      try { return await run(q, s); }
      catch (error) {
        const code = (error as { code?: string } | null)?.code ?? '';
        if (attempt >= 2 || q !== sql || !RETRYABLE.has(code)) throw error;
        await new Promise(resolve => setTimeout(resolve, 150 * (attempt + 1) ** 2));
      }
    }
  };
  return {
    pg: true,
    async all<T>(text: string, ...params: unknown[]) { return [...await exec(sql, { sql: text, params })] as T[]; },
    async query<T>(text: string, ...params: unknown[]) { return { results: [...await exec(sql, { sql: text, params })] as T[], rowsRead: 0 }; },
    async first<T>(text: string, ...params: unknown[]) { return ((await exec(sql, { sql: text, params }))[0] as T | undefined) ?? null; },
    async run(text: string, ...params: unknown[]) { return (await exec(sql, { sql: text, params })).count ?? 0; },
    async batch(statements: Stmt[]) {
      // Returning the array of queries pipelines them: one round trip for the whole batch plus BEGIN/COMMIT.
      const results = await sql.begin(tx => statements.map(s => exec(tx, s))) as unknown as postgres.RowList<Row[]>[];
      return { results: results.map(r => [...r]), written: results.reduce((n, r) => n + (r.command === 'SELECT' ? 0 : r.count ?? 0), 0) };
    },
    async reads(statements: Stmt[]) { return (await Promise.all(statements.map(s => exec(sql, s)))).map(r => [...r] as Row[]); },
    uniqueViolation: (error: unknown, table: string) => {
      const e = error as { code?: string; table_name?: string } | null;
      return e?.code === '23505' && e.table_name === table;
    },
    close: () => sql.end({ timeout: 2 })
  };
}
