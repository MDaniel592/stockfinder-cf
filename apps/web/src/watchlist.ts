export interface WatchedProduct { slug: string; name: string; baseline: number | null; target: number | null; backInStock: boolean; initialStock: boolean }
export function watchNotice(w: WatchedProduct, price: number | null): string | null {
  if (price === null) return null;
  if (w.target !== null && price <= w.target) return 'Precio objetivo alcanzado';
  if (w.backInStock && !w.initialStock) return 'Vuelve a haber una oferta en stock';
  if (w.baseline !== null && price < w.baseline) return 'Ha bajado de precio';
  return null;
}
export function readWatchlist(raw: string | null): WatchedProduct[] {
  if (!raw) return [];
  if (raw.length > 30000) throw new Error('watchlist-too-large');
  const rows: unknown = JSON.parse(raw);
  if (!Array.isArray(rows) || rows.length > 20) throw new Error('invalid-watchlist');
  const cents = (v: unknown) => v === null || (Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= 100000000);
  if (rows.some(w => !w || typeof w !== 'object' || !/^[a-zA-Z0-9_-]{1,100}$/.test(w.slug) || typeof w.name !== 'string' || w.name.length > 250 || !cents(w.baseline) || !cents(w.target) || typeof w.backInStock !== 'boolean' || typeof w.initialStock !== 'boolean') || new Set(rows.map(w => w.slug)).size !== rows.length) throw new Error('invalid-watchlist');
  return rows;
}
