import { describe, expect, test } from 'vitest';
import { refreshStatus, withFreshness } from '../apps/edge-api/src/cache';

const enc = (value: unknown) => new TextEncoder().encode(JSON.stringify(value));
const dec = (bytes: Uint8Array) => new TextDecoder().decode(bytes);
const hourAgo = new Date(Date.now() - 3600_000).toISOString();
const status = { data_updated_at: hourAgo, source_changed_at: hourAgo, last_event_id: 7, age_seconds: 1, offers_checked_at: hourAgo,
  offers_total: 3, offers_fresh: 2, offers_fresh_hours: 12, sync_lag_seconds: 0, stale: false, data_mode: 'manufacturer-catalog' };

describe('cached JSON status refresh (byte splice)', () => {
  test('refreshes only the trailing status and leaves the rest byte-identical', () => {
    const data = [{ id: 'p1', name: 'Ñandú "status":{ 🚀', specs: { status: 'x', 'Formato': 'ATX' } }];
    const body = { data, total: 1, page: 1, limit: 24, status };
    const out = refreshStatus(enc(body), 60);
    const parsed = JSON.parse(dec(out));
    expect(parsed.data).toEqual(data);
    expect(parsed.status).toEqual(withFreshness(status, 60));
    expect(parsed.status.stale).toBe(true);                         // older than 60 s
    expect(parsed.status.age_seconds).toBeGreaterThanOrEqual(3600);
    const head = JSON.stringify(body).split('"status":{"data_updated_at"')[0];
    expect(dec(out).startsWith(head)).toBe(true);
    expect(JSON.parse(dec(refreshStatus(enc(body), 86400))).status.stale).toBe(false);
  });
  test('same result as the former parse/re-serialise for detail bodies', () => {
    const body = { data: { id: 'p1', specs: {} }, status };
    expect(dec(refreshStatus(enc(body), 86400))).toBe(JSON.stringify({ ...body, status: withFreshness(status, 86400) }));
  });
  test('bodies without a top-level status are returned untouched', () => {
    for (const body of [{ data: [1, 2, 3] }, { data: [{ status: { offers_checked_at: null } }], total: 1 }, { data: { brands: [] } }]) {
      const bytes = enc(body);
      expect(refreshStatus(bytes, 60)).toBe(bytes);
    }
    const short = new TextEncoder().encode('{}');
    expect(refreshStatus(short, 60)).toBe(short);
  });
});
