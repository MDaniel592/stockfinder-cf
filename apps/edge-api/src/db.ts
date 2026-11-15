// Data-access layer: the routes speak to `Db`, not to a driver.
//   - D1 (default): the `DB` binding, unchanged behaviour (prepare/bind, batch = one implicit transaction).
//   - Postgres (Supabase via Hyperdrive): used only when a `HYPERDRIVE` binding exists. postgres.js, one
//     connection per request, unnamed statements, writes pipelined inside BEGIN..COMMIT.
// SQL is written with `?` placeholders for both dialects; the Postgres adapter renumbers them to $1..$n.
// Where the dialects differ, callers branch on `db.pg` (see docs/supabase-migration.md).
// The Postgres adapter lives in ./pgdb and is bundled only when the build defines `__SF_PG__` as true
// (wrangler.staging.jsonc, `npm run build:worker` for the tests); the D1 production build defines it false, so
// postgres.js and the Node built-ins it needs stay out of that bundle.
declare const __SF_PG__: boolean;

export type Row = Record<string, unknown>;
export interface Stmt { sql: string; params: unknown[] }
export const stmt = (sql: string, ...params: unknown[]): Stmt => ({ sql, params });

export interface Db {
  /** true for Postgres; SQL that differs between dialects branches on it. */
  readonly pg: boolean;
  all<T = Row>(sql: string, ...params: unknown[]): Promise<T[]>;
  /** Like all(), plus D1's rows_read (0 on Postgres). */
  query<T = Row>(sql: string, ...params: unknown[]): Promise<{ results: T[]; rowsRead: number }>;
  first<T = Row>(sql: string, ...params: unknown[]): Promise<T | null>;
  run(sql: string, ...params: unknown[]): Promise<number>;
  /** Atomic and ordered: every statement sees the previous ones; any error rolls back all of them. */
  batch(statements: Stmt[]): Promise<{ results: Row[][]; written: number }>;
  /** Independent reads in one round trip where the driver allows it (no transaction needed). */
  reads(statements: Stmt[]): Promise<Row[][]>;
  /** True when `error` is a unique violation on `table.column`. */
  uniqueViolation(error: unknown, table: string, column: string): boolean;
  close(): Promise<void>;
}

export function d1Db(db: D1Database): Db {
  const prepare = (s: Stmt) => db.prepare(s.sql).bind(...s.params);
  return {
    pg: false,
    async all<T>(sql: string, ...params: unknown[]) { return (await db.prepare(sql).bind(...params).all<T>()).results; },
    async query<T>(sql: string, ...params: unknown[]) {
      const r = await db.prepare(sql).bind(...params).all<T>(); return { results: r.results, rowsRead: r.meta.rows_read ?? 0 };
    },
    first: <T>(sql: string, ...params: unknown[]) => db.prepare(sql).bind(...params).first<T>(),
    async run(sql: string, ...params: unknown[]) { return (await db.prepare(sql).bind(...params).run()).meta.changes ?? 0; },
    async batch(statements: Stmt[]) {
      const r = await db.batch<Row>(statements.map(prepare));
      return { results: r.map(x => x.results), written: r.reduce((n, x) => n + (x.meta.rows_written ?? 0), 0) };
    },
    async reads(statements: Stmt[]) { return (await db.batch<Row>(statements.map(prepare))).map(r => r.results); },
    uniqueViolation: (error: unknown, table: string, column: string) => String(error).includes(`${table}.${column}`),
    close: async () => undefined
  };
}

/** `2026-11-09 17:00:00.123+00` (Postgres text output) -> `2026-11-09T17:00:00.123Z`, the format D1 stores. */
const isoTimestamp = (value: string) => new Date(value.replace(' ', 'T').replace(/([+-]\d\d)$/, '$1:00')).toISOString();
/**
 * Result parsing that keeps the public JSON identical to D1: bigint/numeric as numbers (all values are far below
 * 2^53), timestamptz as ISO-8601 with milliseconds and `Z`, json columns as their stored text (callers JSON.parse
 * them exactly as with D1, and `json` keeps the original key order).
 */
export const pgTypes = {
  int8: { to: 20, from: [20], serialize: (x: unknown) => String(x), parse: (x: string) => Number(x) },
  numeric: { to: 1700, from: [1700], serialize: (x: unknown) => String(x), parse: (x: string) => Number(x) },
  timestamptz: { to: 1184, from: [1184], serialize: (x: unknown) => x instanceof Date ? x.toISOString() : String(x), parse: isoTimestamp },
  jsontext: { to: 114, from: [114, 3802], serialize: (x: unknown) => typeof x === 'string' ? x : JSON.stringify(x), parse: (x: string) => x }
};

/** `?` -> `$1..$n`. The SQL in this codebase never contains `?` inside literals and does not use the jsonb `?` operator. */
export const numbered = (sql: string) => { let i = 0; return sql.replace(/\?/g, () => `$${++i}`); };

/** Postgres when the HYPERDRIVE binding is configured (staging / after cutover); D1 otherwise. */
export async function openDb(env: { DB?: D1Database; HYPERDRIVE?: Hyperdrive }): Promise<Db> {
  if (env.HYPERDRIVE) {
    // Dead code (and no postgres.js in the bundle) when the build defines __SF_PG__ as false.
    if (__SF_PG__) return (await import('./pgdb')).pgDb(env.HYPERDRIVE.connectionString);
    throw new Error('HYPERDRIVE is bound but this build has no Postgres support (define __SF_PG__=true)');
  }
  if (!env.DB) throw new Error('No database binding');
  return d1Db(env.DB);
}
