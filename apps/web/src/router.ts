import { useSyncExternalStore } from 'react';

export const CATEGORY_IDS = ['cpu', 'gpu', 'motherboard', 'ram', 'storage', 'psu', 'case', 'cooler'];
export const SORTS = ['featured', 'name', 'price', '-price'] as const;
export type Sort = typeof SORTS[number];
const SORT_TO_URL: Record<string, string> = { name: 'nombre', price: 'precio', '-price': 'precio-desc' };
const URL_TO_SORT: Record<string, Sort> = { nombre: 'name', precio: 'price', 'precio-desc': '-price' };
const SPEC_PREFIX = 'spec.';

export interface CatalogParams {
  q: string; brand: string[]; shop: string[]; stock: boolean;
  min: number | null; max: number | null; // euro cents
  sort: Sort; page: number; specs: Record<string, string[]>;
}
export const emptyParams: CatalogParams = { q: '', brand: [], shop: [], stock: false, min: null, max: null, sort: 'featured', page: 1, specs: {} };

const euros = (value: string | null): number | null => {
  if (value === null || !value.trim()) return null;
  const n = Number(value.replace(',', '.'));
  const cents = Math.round(n * 100);
  return Number.isFinite(n) && Number.isSafeInteger(cents) && cents >= 0 && cents <= 100000000 ? cents : null;
};
export function parseParams(search: string): CatalogParams {
  const sp = new URLSearchParams(search);
  const list = (name: string) => [...new Set(sp.getAll(name).map(v => v.trim()).filter(v => v && v.length <= 250))].slice(0, 24);
  const specs: Record<string, string[]> = {};
  for (const key of new Set([...sp.keys()].filter(k => k.startsWith(SPEC_PREFIX) && k.length > SPEC_PREFIX.length))) {
    const values = [...new Set(sp.getAll(key).filter(v => v && v.length <= 250))].slice(0, 24);
    if (values.length && Object.keys(specs).length < 8) specs[key.slice(SPEC_PREFIX.length)] = values;
  }
  let min = euros(sp.get('min')); let max = euros(sp.get('max'));
  if (min !== null && max !== null && min > max) { min = null; max = null; }
  const page = Number.parseInt(sp.get('pagina') ?? '1', 10);
  return { q: (sp.get('q') ?? '').trim().slice(0, 100), brand: list('marca'), shop: list('tienda'), stock: ['1', 'si'].includes(sp.get('stock') ?? ''),
    min, max, sort: URL_TO_SORT[sp.get('orden') ?? ''] ?? 'featured', page: Number.isInteger(page) && page >= 1 && page <= 1000 ? page : 1, specs };
}
export function buildSearch(p: CatalogParams): string {
  const sp = new URLSearchParams();
  if (p.q) sp.set('q', p.q);
  p.brand.forEach(v => sp.append('marca', v)); p.shop.forEach(v => sp.append('tienda', v));
  if (p.stock) sp.set('stock', '1');
  if (p.min !== null) sp.set('min', String(p.min / 100));
  if (p.max !== null) sp.set('max', String(p.max / 100));
  Object.entries(p.specs).forEach(([k, values]) => values.forEach(v => sp.append(SPEC_PREFIX + k, v)));
  if (SORT_TO_URL[p.sort]) sp.set('orden', SORT_TO_URL[p.sort]);
  if (p.page > 1) sp.set('pagina', String(p.page));
  const text = sp.toString();
  return text ? `?${text}` : '';
}
export const categoryPath = (id: string) => id ? `/c/${encodeURIComponent(id)}` : '/';
export const catalogUrl = (category: string, p: CatalogParams) => categoryPath(category) + buildSearch(p);
export const productPath = (slug: string) => `/p/${encodeURIComponent(slug)}`;

export type Route =
  | { kind: 'home' } | { kind: 'category'; id: string } | { kind: 'product'; slug: string }
  | { kind: 'builder' } | { kind:'deals' } | { kind: 'about' } | { kind: 'privacy' } | { kind: 'notfound' };
export function parseRoute(pathname: string): Route {
  const path = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
  if (path === '/' || path === '') return { kind: 'home' };
  if (path === '/ofertas') return {kind:'deals'};
  if (path === '/configurador') return { kind: 'builder' };
  if (path === '/acerca') return { kind: 'about' };
  if (path === '/privacidad') return { kind: 'privacy' };
  const segments = path.split('/').slice(1);
  let value = '';
  try { value = decodeURIComponent(segments[1] ?? ''); } catch { return { kind: 'notfound' }; }
  if (segments.length === 2 && segments[0] === 'c' && CATEGORY_IDS.includes(value)) return { kind: 'category', id: value };
  if (segments.length === 2 && segments[0] === 'p' && /^[a-zA-Z0-9_-]{1,100}$/.test(value)) return { kind: 'product', slug: value };
  return { kind: 'notfound' };
}
/** Identifies the "page" for scroll/focus management: opening a product dialog does not change it. */
export const viewKey = (route: Route) =>
  route.kind === 'category' ? `category:${route.id}` : route.kind === 'home' ? 'category:' : route.kind;

export interface Loc { pathname: string; search: string; state: { from?: string } | null }
function read(): Loc { return { pathname: location.pathname, search: location.search, state: history.state }; }
let snapshot: Loc = read();
const listeners = new Set<() => void>();
let popped = false;
function refresh() {
  const next = read();
  if (next.pathname !== snapshot.pathname || next.search !== snapshot.search || next.state !== snapshot.state) { snapshot = next; listeners.forEach(l => l()); }
}
if (typeof window !== 'undefined') window.addEventListener('popstate', () => { popped = true; refresh(); });
const subscribe = (cb: () => void) => { listeners.add(cb); return () => listeners.delete(cb); };
export const useLocation = () => useSyncExternalStore(subscribe, () => snapshot);
/** True once if the last location change came from back/forward. */
export const consumePopped = () => { const v = popped; popped = false; return v; };
export function go(url: string, options: { replace?: boolean; state?: Loc['state'] } = {}) {
  popped = false;
  history[options.replace ? 'replaceState' : 'pushState'](options.state ?? null, '', url);
  refresh();
}
