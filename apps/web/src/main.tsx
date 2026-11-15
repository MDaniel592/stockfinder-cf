import React, { Suspense, lazy, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { LogoMark } from './LogoMark'
import { createRoot } from 'react-dom/client';
import type { CatalogStatus, Offer } from '../../../packages/api-contracts/index';
import './app.css';
import './print.css';
import { CategoryIcon } from './CategoryIcon';
import { AppearanceSettings } from './AppearanceSettings';
import { CatalogFilterControls } from './CatalogFilters';
import type { CatalogFilters } from '../../../packages/api-contracts/catalog-filters';
import { Link } from './Link';
import { AboutPage, NotFoundPage, PrivacyPage } from './InfoPages';
import { absoluteDate, money, relativeAge } from './format';
import { catalogUrl, categoryPath, consumePopped, emptyParams, go, parseParams, parseRoute, useLocation } from './router';
import type { CatalogParams, Route } from './router';
import { displayName, orderedSpecKeys } from './display';
import { Thumb, type Item, type Shop } from './ProductBits';
import { ProductCard, ProductTable, shopsLabel } from './ProductCard';
import { ProductPage } from './ProductPage';
import { Watchlist } from './Watchlist';
import { HomePage } from './Home';
import { Search, LayoutGrid, List, X, ArrowLeft, ArrowRight, GitCompareArrows, Wrench, SlidersHorizontal, TrendingDown } from 'lucide-react';

type Status = CatalogStatus & { offers_checked_at?: string | null };

const DealsPage = lazy(() => import('./DealsPage').then(m=>({default:m.DealsPage})));
const BuilderPage = lazy(() => import('./PcBuilder').then(m => ({ default: function BuilderLoaded() {
  useEffect(() => { const a = document.activeElement; if (!a || a === document.body || a.closest('.site-header')) focusHeading(); }, []);
  return <m.PcBuilder/>;
} })));

const HOME_TITLE = 'StockFinder — Compara componentes de PC y precios en tiendas';
const FALLBACK_CATEGORIES = [['cpu', 'Procesadores'], ['motherboard', 'Placas base'], ['ram', 'Memoria RAM'], ['gpu', 'Tarjetas gráficas'], ['storage', 'Almacenamiento'], ['psu', 'Fuentes de alimentación'], ['case', 'Cajas'], ['cooler', 'Refrigeración']].map(([id, name]) => ({ id, name }));
const CATEGORY_ORDER = ['cpu', 'motherboard', 'ram', 'gpu', 'storage', 'psu', 'case', 'cooler'];
const SHORT_NAMES: Record<string, string> = { gpu: 'Gráficas', psu: 'Fuentes', ram: 'RAM' };
const categoryOrder = (id: string) => { const i = CATEGORY_ORDER.indexOf(id); return i === -1 ? 99 : i; };
const PAGE_SIZE = 24;

class ApiError extends Error { constructor(message: string, public status: number) { super(message); } }
async function api<T,>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`/api/v1/${path}`, { signal, headers: { 'X-SF-Client-Freshness': '1' } });
  if (!res.ok) throw new ApiError(res.status === 400 ? 'Alguno de los filtros del enlace no es válido.' : 'No se ha podido cargar el catálogo. Inténtalo de nuevo.', res.status);
  const body = await res.json() as T & { status?: Status };
  if (body.status) {
    const basis = body.status.offers_checked_at ?? body.status.source_changed_at;
    body.status.stale = !basis || Date.now() - Date.parse(basis) > 86400000;
    body.status.age_seconds = body.status.source_changed_at ? Math.max(0, Math.floor((Date.now() - Date.parse(body.status.source_changed_at)) / 1000)) : null;
  }
  return body;
}
function focusHeading() {
  const heading = document.querySelector<HTMLElement>('main h1');
  if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
}
function freshnessLabel(value: string | null, prefix = 'Actualizado') {
  if (!value || !Number.isFinite(Date.parse(value))) return 'Sin actualización registrada';
  const age = relativeAge(value);
  if (age === 'hace un momento') return `${prefix} ahora`;
  const days = Math.floor((Date.now() - Date.parse(value)) / 86400000);
  if (days >= 7) return `${prefix} el ${new Date(value).toLocaleDateString('es-ES', { day: 'numeric', month: 'short' })}`;
  return `${prefix} ${age}`;
}
function offersFreshnessLabel(status: Status | null) {
  if (!status?.offers_checked_at) return freshnessLabel(status?.data_updated_at ?? null);
  const total = status.offers_total, fresh = status.offers_fresh, hours = status.offers_fresh_hours ?? 12;
  if (typeof total === 'number' && typeof fresh === 'number' && total > 0) {
    const share = Math.round((fresh / total) * 100);
    return fresh === total ? `Precios comprobados en las últimas ${hours} h` : `Precios comprobados: ${share} % en las últimas ${hours} h`;
  }
  return freshnessLabel(status.offers_checked_at, 'Precios comprobados');
}
/** True when the URL carries any catalogue parameter: then "/" lists the whole catalogue instead of the landing page. */
const hasListParams = (p: CatalogParams) => !!p.q || p.brand.length > 0 || p.shop.length > 0 || p.stock || p.min !== null || p.max !== null
  || Object.keys(p.specs).length > 0 || p.sort !== emptyParams.sort || p.page > 1;
const pageKey = (route: Route, isList: boolean) => route.kind === 'category' ? `list:${route.id}` : route.kind === 'home' ? (isList ? 'list:' : 'landing')
  : route.kind === 'product' ? `product:${route.slug}` : route.kind;
function referenceCell(p: Item, shops: Shop[]) {
  const codes = p.retailer_codes ?? [];
  if (!p.mpn && !codes.length) return '—';
  return <>{p.mpn && <div>{p.mpn}</div>}{codes.map(c => <div key={c.shop_id + c.code}><small>{shops.find(s => s.id === c.shop_id)?.name ?? c.shop_id}: {c.code}</small></div>)}</>;
}

function CompareDialog({ open, items, shops, onClose, onRemove }: { open: boolean; items: Item[]; shops: Shop[]; onClose: () => void; onRemove: (item: Item) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [onlyDiff, setOnlyDiff] = useState(false);
  useEffect(() => { const el = ref.current; if (!el) return; if (open && !el.open) el.showModal(); else if (!open && el.open) el.close(); }, [open]);
  const keys = orderedSpecKeys(items);
  const differs = (values: string[]) => new Set(values).size > 1;
  const specRows = keys.filter(key => !onlyDiff || differs(items.map(c => c.specs[key] ?? '—')));
  return <dialog ref={ref} className="compare-dialog" aria-labelledby="compare-title" onClose={onClose} onClick={e => { if (e.target === ref.current) ref.current?.close(); }}>
    <div className="compare-head"><h2 id="compare-title">Comparar componentes</h2>
      <label className="compare-diff"><input type="checkbox" role="switch" checked={onlyDiff} onChange={e => setOnlyDiff(e.target.checked)}/> Solo diferencias</label>
      <button autoFocus className="close-dialog" aria-label="Cerrar" onClick={() => ref.current?.close()}><X size={20} aria-hidden="true"/></button></div>
    <p className="detail-note">Comparación de especificaciones. La compatibilidad entre componentes aún no se comprueba.</p>
    {open && <div className="comparison-scroll"><table><thead><tr><th scope="col"><span className="sr-only">Característica</span></th>{items.map(c => <th scope="col" key={c.id}>
      <Thumb product={c} size="mini"/><Link href={`/p/${encodeURIComponent(c.slug)}`} onNavigate={() => ref.current?.close()}>{displayName(c)}</Link>
      <button type="button" className="compare-remove" aria-label={`Quitar ${displayName(c)} de la comparación`} onClick={() => onRemove(c)}><X size={14} aria-hidden="true"/></button></th>)}</tr></thead>
      <tbody>
        <tr className="compare-price"><th scope="row">Desde</th>{items.map(c => <td key={c.id}><strong className={c.min_price_cents === null ? 'price-pending' : 'price-verified'}>{money(c.min_price_cents)}</strong><small>{shopsLabel(c.offers_count)}</small></td>)}</tr>
        <tr><th scope="row">Marca</th>{items.map(c => <td key={c.id}>{c.brand}</td>)}</tr>
        {items.some(c => c.mpn || c.retailer_codes?.length) && <tr><th scope="row">Referencia</th>{items.map(c => <td key={c.id}>{referenceCell(c, shops)}</td>)}</tr>}
        {specRows.map(key => { const values = items.map(c => c.specs[key] ?? '—'); return <tr key={key} className={differs(values) ? 'is-diff' : ''}><th scope="row">{key}</th>{values.map((v, i) => <td key={items[i].id}>{v}</td>)}</tr>; })}
        {onlyDiff && !specRows.length && <tr><td colSpan={items.length + 1}>Las especificaciones publicadas coinciden.</td></tr>}
      </tbody></table></div>}
  </dialog>;
}

function App() {
  const loc = useLocation();
  const route = useMemo(() => parseRoute(loc.pathname), [loc.pathname]);
  const [filters, setFilters] = useState<CatalogFilters | null>(null);
  const [filtersLoading, setFiltersLoading] = useState(true);
  const [filtersError, setFiltersError] = useState(false);
  const [shops, setShops] = useState<Shop[]>([]);
  const [categories, setCategories] = useState<{ id: string; name: string }[]>(FALLBACK_CATEGORIES);
  const [products, setProducts] = useState<Item[]>([]);
  const [status, setStatus] = useState<Status | null>(null); const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true); const [error, setError] = useState(''); const [errorStatus, setErrorStatus] = useState(0); const [retry, setRetry] = useState(0);
  const [view, setViewState] = useState<'grid' | 'list'>(() => { try { return localStorage.getItem('stockfinder.view') === 'grid' ? 'grid' : 'list'; } catch { return 'list'; } });
  const setView = (value: 'grid' | 'list') => { setViewState(value); try { localStorage.setItem('stockfinder.view', value); } catch { /* per-browser convenience only */ } };
  const [detail, setDetail] = useState<Item | null>(null); const [detailState, setDetailState] = useState<'idle' | 'loading' | 'missing' | 'error'>('idle');
  const [offers, setOffers] = useState<Offer[]>([]); const [offerError, setOfferError] = useState(''); const [offersLoading, setOffersLoading] = useState(false);
  const [compare, setCompare] = useState<Item[]>([]); const [compareOpen, setCompareOpen] = useState(false);
  const known = useRef(new Map<string, Item>());
  const listCache = useRef(new Map<string, { data: Item[]; total: number; status: Status; at: number }>());
  const filterDialog = useRef<HTMLDialogElement>(null);
  const filterTrigger = useRef<HTMLButtonElement>(null);
  const compareTrigger = useRef<HTMLButtonElement>(null);
  const searchFocused = useRef(false);

  // ----- Route-derived state -----
  const slug = route.kind === 'product' ? route.slug : null;
  const selected = slug && detail?.slug === slug ? detail : null;
  const fromUrl = route.kind === 'product' && typeof loc.state?.from === 'string' && loc.state.from.startsWith('/') ? loc.state.from : null;
  const params = useMemo(() => route.kind === 'home' || route.kind === 'category' ? parseParams(loc.search) : emptyParams, [route, loc.search]);
  const isList = route.kind === 'category' || (route.kind === 'home' && hasListParams(params));
  const isLanding = route.kind === 'home' && !isList;
  const featureOnly = route.kind === 'builder' || route.kind === 'deals';
  const [replicaAt,setReplicaAt] = useState<string|null>(null);
  const [featureStale,setFeatureStale] = useState(false);
  const category = route.kind === 'category' ? route.id : '';
  const p = params;
  const listUrl = catalogUrl(category, p);
  const categoryName = (id: string) => categories.find(c => c.id === id)?.name ?? '';
  const currentCategory = category ? categoryName(category) || category : p.q ? 'Resultados de búsqueda' : 'Todos los componentes';
  const apiQuery = useMemo(() => {
    if (!isList) return null;
    const q = new URLSearchParams({ page: String(p.page), limit: String(PAGE_SIZE), sort: p.sort });
    if (category) q.set('category', category); if (p.q) q.set('q', p.q);
    p.brand.forEach(value => q.append('brand', value)); p.shop.forEach(value => q.append('shop', value)); if (p.stock) q.set('stock', '1');
    if (p.min !== null) q.set('min_price', String(p.min));
    if (p.max !== null) q.set('max_price', String(p.max));
    if (Object.keys(p.specs).length) q.set('specs', JSON.stringify(p.specs));
    return q.toString();
  }, [isList, category, p]);

  const update = (patch: Partial<CatalogParams>, replace = false) => go(catalogUrl(category, { ...p, page: 1, ...patch }), { replace });
  const [qInput, setQInput] = useState(p.q);
  useEffect(() => setQInput(p.q), [p.q]);
  const updateRef = useRef(update); updateRef.current = update;
  useEffect(() => {
    if (!isList || qInput.trim() === p.q) return;
    const timer = setTimeout(() => updateRef.current({ q: qInput.trim() }, true), 250);
    return () => clearTimeout(timer);
  }, [qInput]);
  const submitSearch = (event: React.FormEvent) => {
    event.preventDefault();
    const q = qInput.trim();
    if (isList) { if (q !== p.q) update({ q }, true); return; }
    if (q) go(catalogUrl('', { ...emptyParams, q }));
  };

  // ----- Data loading -----
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === '/' && !(event.target instanceof HTMLInputElement) && !(event.target instanceof HTMLTextAreaElement) && !(event.target instanceof HTMLSelectElement) && !document.querySelector('dialog[open]')) {
        const input = document.querySelector<HTMLInputElement>('#search');
        if (input) { event.preventDefault(); input.focus(); }
      }
    };
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey);
  }, []);
  useEffect(() => { if(featureOnly) return; api<{ data: { id: string; name: string }[] }>('categories').then(r => setCategories([...r.data].sort((a, b) => categoryOrder(a.id) - categoryOrder(b.id)))).catch(() => {}); }, [retry,featureOnly]);
  useEffect(() => { if(featureOnly) return; api<{ data: Shop[] }>('shops').then(r => setShops(r.data)).catch(() => {}); }, [retry,featureOnly]);
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => {
      if (document.hidden) return;
      if (featureOnly) api<{categories:{id:string;name:string}[];shops:Shop[];replica_updated_at:string|null;stale:boolean}>('builder/state',controller.signal)
        .then(r=>{if(!controller.signal.aborted){setCategories(r.categories.sort((a,b)=>categoryOrder(a.id)-categoryOrder(b.id)));setShops(r.shops);setReplicaAt(r.replica_updated_at);setFeatureStale(r.stale);}}).catch(()=>{if(!controller.signal.aborted)setFeatureStale(true);});
      else api<Status>('catalog/status',controller.signal).then(setStatus).catch(() => {});
    };
    refresh();
    const timer = setInterval(refresh, 60000);
    document.addEventListener('visibilitychange', refresh);
    return () => {
      controller.abort();
      clearInterval(timer);
      document.removeEventListener('visibilitychange', refresh);
    };
  }, [featureOnly]);
  useEffect(() => {
    if (!isList) return;
    const controller = new AbortController(); setFilters(null); setFiltersLoading(true); setFiltersError(false);
    api<{ data: CatalogFilters }>(`catalog/filters${category ? `?category=${encodeURIComponent(category)}` : ''}`, controller.signal)
      .then(result => { if (!controller.signal.aborted) setFilters(result.data); })
      .catch(() => { if (!controller.signal.aborted) setFiltersError(true); })
      .finally(() => { if (!controller.signal.aborted) setFiltersLoading(false); });
    return () => controller.abort();
  }, [category, retry, isList]);
  useEffect(() => {
    if (apiQuery === null) return;
    const key = `${retry}|${apiQuery}`;
    const cached = listCache.current.get(key);
    // Back from a product page: reuse the list we already had so the position can be restored instantly.
    if (cached && Date.now() - cached.at < 120000) { setProducts(cached.data); setTotal(cached.total); setStatus(cached.status); setError(''); setLoading(false); return; }
    const controller = new AbortController(); setLoading(true); setError(''); setErrorStatus(0);
    api<{ data: Item[]; total: number; status: Status }>(`products?${apiQuery}`, controller.signal)
      .then(r => { if (!controller.signal.aborted) { r.data.forEach(item => known.current.set(item.slug, item)); setProducts(r.data); setTotal(r.total); setStatus(r.status);
        listCache.current.set(key, { data: r.data, total: r.total, status: r.status, at: Date.now() }); if (listCache.current.size > 30) listCache.current.delete(listCache.current.keys().next().value!); } })
      .catch(e => { if (!controller.signal.aborted) { setError(e.message); setErrorStatus(e.status ?? 0); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [apiQuery, retry]);
  useEffect(() => {
    if (!slug) { setDetailState('idle'); return; }
    const controller = new AbortController();
    setDetail(known.current.get(slug) ?? null); setDetailState('loading');
    api<{ data: Item }>(`products/${encodeURIComponent(slug)}`, controller.signal)
      .then(r => { known.current.set(slug, r.data); setDetail(r.data); setDetailState('idle'); })
      .catch((e: ApiError) => { if (!controller.signal.aborted) setDetailState(e.status === 404 ? 'missing' : 'error'); });
    return () => controller.abort();
  }, [slug, retry]);
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController(); setOffers([]); setOfferError(''); setOffersLoading(true);
    api<{ data: Offer[] }>(`products/${encodeURIComponent(selected.slug)}/offers`, controller.signal).then(r => setOffers(r.data))
      .catch(e => { if (!controller.signal.aborted) setOfferError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setOffersLoading(false); });
    return () => controller.abort();
  }, [selected?.slug, retry]);
  // A page beyond the last one (e.g. a stale link) falls back to the first page.
  useEffect(() => { if (isList && !loading && !error && !products.length && total > 0 && p.page > 1) update({}, true); }, [loading, error, products.length, total, p.page]);

  // ----- Title, focus and scroll -----
  useEffect(() => {
    const names: Record<string, string> = { builder: 'Configurador de PC | StockFinder', about: 'Cómo funciona StockFinder', privacy: 'Privacidad | StockFinder' };
    if (route.kind === 'home') document.title = HOME_TITLE;
    else if (route.kind === 'category') document.title = `${categoryName(route.id) || route.id} · Compara precios y especificaciones | StockFinder`;
    else if (route.kind === 'product') document.title = selected ? `${selected.name} · Precios y especificaciones | StockFinder` : detailState === 'missing' ? 'Producto no encontrado | StockFinder' : 'StockFinder';
    else if (route.kind === 'notfound') document.title = 'Página no encontrada | StockFinder';
    else document.title = route.kind === 'deals' ? 'Las 20 mayores bajadas | StockFinder' : names[route.kind];
  }, [route, selected?.name, detailState, categories]);
  const scrollMemo = useRef(new Map<string, number>());
  const pendingRestore = useRef<number | null>(null);
  useEffect(() => {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
    const onScroll = () => scrollMemo.current.set(location.pathname + location.search, window.scrollY);
    window.addEventListener('scroll', onScroll, { passive: true }); return () => window.removeEventListener('scroll', onScroll);
  }, []);
  const key = pageKey(route, isList);
  const prevKey = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevKey.current; prevKey.current = key;
    const popped = consumePopped();
    if (prev === null || prev === key) return;
    if (popped) pendingRestore.current = scrollMemo.current.get(loc.pathname + loc.search) ?? 0;
    else window.scrollTo({ top: 0 });
    requestAnimationFrame(() => {
      const search = document.querySelector<HTMLInputElement>('#search');
      if (searchFocused.current && search) search.focus(); else focusHeading();
    });
  }, [key]);
  const ready = isList ? !loading : route.kind === 'product' ? !!selected : true;
  useLayoutEffect(() => {
    if (pendingRestore.current === null || !ready) return;
    const y = pendingRestore.current; pendingRestore.current = null;
    window.scrollTo({ top: y });
  });
  const prevPage = useRef(p.page);
  useEffect(() => { if (prevPage.current !== p.page && isList) window.scrollTo({ top: 0 }); prevPage.current = p.page; }, [p.page]);

  // ----- Actions -----
  const toggleIn = (list: string[], value: string) => list.includes(value) ? list.filter(item => item !== value) : [...list, value];
  const toggleSpec = (specKey: string, value: string) => {
    const specs = { ...p.specs }; const chosen = toggleIn(specs[specKey] ?? [], value);
    if (chosen.length) specs[specKey] = chosen; else delete specs[specKey];
    update({ specs });
  };
  const reset = () => go(category ? categoryPath(category) : p.q ? catalogUrl('', { ...emptyParams, q: p.q }) : '/');
  const filterCount = p.brand.length + p.shop.length + Number(p.stock) + Number(p.min !== null || p.max !== null) + Object.values(p.specs).reduce((sum, values) => sum + values.length, 0);
  const filterControls = <CatalogFilterControls data={filters} loading={filtersLoading} error={filtersError} retry={() => setRetry(value => value + 1)}
    brand={p.brand} shop={p.shop} stock={p.stock} specs={p.specs} minPrice={p.min} maxPrice={p.max}
    onBrand={value => update({ brand: toggleIn(p.brand, value) })} onShop={value => update({ shop: toggleIn(p.shop, value) })} onStock={value => update({ stock: value })} onSpec={toggleSpec}
    onPrice={(min, max) => update({ min, max })} onReset={() => go(categoryPath(category) + (category || !p.q ? '' : `?q=${encodeURIComponent(p.q)}`))}/>;
  const toggleCompare = (product: Item) => setCompare(current => current.some(c => c.id === product.id) ? current.filter(c => c.id !== product.id) : current.length < 4 ? [...current, product] : current);
  useEffect(() => { if (compare.length < 2 && compareOpen) setCompareOpen(false); }, [compare.length]);

  // ----- Views -----
  const staleSince = status?.offers_checked_at ?? status?.data_updated_at ?? null;
  const freshness = featureOnly ? (featureStale ? 'La réplica necesita actualizarse' : freshnessLabel(replicaAt,'Catálogo sincronizado')) : offersFreshnessLabel(status);
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const searchBox = (variant: 'header' | 'hero') => <form role="search" className={`search-form search-${variant}`} onSubmit={submitSearch} aria-label="Buscar componentes">
    <label className="search-wrap"><Search size={variant === 'hero' ? 20 : 17} aria-hidden="true"/>
      <input id="search" type="search" placeholder={category ? `Buscar en ${currentCategory.toLowerCase()}…` : 'Busca un modelo, marca o referencia…'} maxLength={100} aria-label="Buscar componentes"
        value={qInput} onChange={e => setQInput(e.target.value)} onFocus={() => { searchFocused.current = true; }} onBlur={() => { searchFocused.current = false; }} enterKeyHint="search" autoComplete="off"/>
      <kbd aria-hidden="true">/</kbd></label>
    {variant === 'hero' && <button type="submit" className="primary">Buscar</button>}
  </form>;

  const catalogView = <section className="catalog" aria-labelledby="catalog-title">
    <div className="catalog-heading">
      <h1 id="catalog-title">{currentCategory}<span>{loading ? '…' : total.toLocaleString('es-ES')}</span></h1>
      <p className="catalog-subtitle">{p.q && category ? <>Resultados para «{p.q}» · </> : p.q ? <>Resultados para «{p.q}» en todas las categorías · </> : null}Precios comprobados en tiendas españolas.</p>
    </div>
    <div className="catalog-workspace"><aside className="catalog-filters" aria-label="Filtros de componentes"><div className="catalog-filters-inner">{filterControls}</div></aside>
    <div className="catalog-results">
    <div className="catalog-tools">
      <div className="catalog-result-meta"><span role="status" aria-live="polite">{loading ? 'Buscando componentes…' : error ? '' : `${total.toLocaleString('es-ES')} ${total === 1 ? 'resultado' : 'resultados'}`}</span></div>
      <button ref={filterTrigger} className="catalog-filter-trigger" aria-haspopup="dialog" onClick={() => filterDialog.current?.showModal()}><SlidersHorizontal size={16} aria-hidden="true"/> Filtros{filterCount > 0 && <span>{filterCount}</span>}</button>
      <label className="sort"><span className="sr-only">Ordenar</span><select aria-label="Ordenar productos" value={p.sort} onChange={e => update({ sort: e.target.value as CatalogParams['sort'] })}><option value="featured">Destacados</option><option value="price">Precio: menor a mayor</option><option value="-price">Precio: mayor a menor</option><option value="name">Nombre A–Z</option></select></label>
      <div className="view-toggle" role="group" aria-label="Vista"><button aria-label="Vista de tarjetas" aria-pressed={view === 'grid'} onClick={() => setView('grid')}><LayoutGrid size={16} aria-hidden="true"/></button><button aria-label="Vista de lista" aria-pressed={view === 'list'} onClick={() => setView('list')}><List size={16} aria-hidden="true"/></button></div>
    </div>
    {(filterCount > 0 || p.q) && <div className="catalog-active-filters" aria-label="Filtros aplicados">
      {p.q && <button onClick={() => update({ q: '' })} aria-label={`Quitar búsqueda «${p.q}»`}>«{p.q}» <X size={12} aria-hidden="true"/></button>}
      {p.brand.map(value => <button key={value} onClick={() => update({ brand: p.brand.filter(item => item !== value) })}>{value} <X size={12} aria-hidden="true"/></button>)}
      {p.shop.map(value => <button key={value} onClick={() => update({ shop: p.shop.filter(item => item !== value) })}>{shops.find(s => s.id === value)?.name ?? value} <X size={12} aria-hidden="true"/></button>)}
      {p.stock && <button onClick={() => update({ stock: false })}>Con stock <X size={12} aria-hidden="true"/></button>}
      {(p.min !== null || p.max !== null) && <button onClick={() => update({ min: null, max: null })}>{p.min === null ? '0 €' : money(p.min)} – {p.max === null ? 'Sin límite' : money(p.max)} <X size={12} aria-hidden="true"/></button>}
      {Object.entries(p.specs).flatMap(([k, values]) => values.map(value => <button key={`${k}-${value}`} onClick={() => toggleSpec(k, value)}>{k}: {value} <X size={12} aria-hidden="true"/></button>))}
      <button className="clear-all" onClick={reset}>Limpiar filtros</button>
    </div>}
    {status?.stale && <p className="notice" role="status">Las ofertas llevan tiempo sin comprobarse{staleSince ? ` (última comprobación ${relativeAge(staleSince)})` : ''}. Los precios y el stock pueden haber cambiado: confírmalos en la tienda.</p>}
    {error ? <div className="empty" role="alert"><h2>{errorStatus === 400 ? 'Filtros no válidos' : 'No se ha podido cargar el catálogo'}</h2><p>{error}</p>{errorStatus === 400 ? <button className="primary" onClick={() => go(categoryPath(category))}>Limpiar filtros</button> : <button className="primary" onClick={() => setRetry(x => x + 1)}>Reintentar</button>}</div>
    : loading ? <div className={`product-grid ${view}`} aria-busy="true" aria-label="Cargando productos">{Array.from({ length: 8 }, (_, i) => <div key={i} className="skeleton"/>)}</div>
    : !products.length ? <div className="empty"><Search size={36} aria-hidden="true"/><h2>No encontramos esa pieza</h2><p>Prueba otro modelo o amplía los filtros.</p><button className="primary" onClick={() => go(categoryPath(category))}>Limpiar filtros</button></div>
    : view === 'list' ? <div className="product-grid list"><ProductTable products={products} category={category} from={listUrl} compare={compare} onCompare={toggleCompare}
        sort={p.sort} onSort={sort => update({ sort })} demo={status?.data_mode === 'synthetic'}/></div>
    : <div className="product-grid grid">
      {products.map(product => <ProductCard key={product.id} product={product} from={listUrl} demo={status?.data_mode === 'synthetic'}
        compared={compare.some(c => c.id === product.id)} compareDisabled={compare.length >= 4} onCompare={() => toggleCompare(product)}/>)}
    </div>}
    {!error && total > PAGE_SIZE && <nav className="pagination" aria-label="Paginación"><span>Página {p.page} de {pages}</span><div><button disabled={loading || p.page === 1} onClick={() => update({ page: p.page - 1 })}><ArrowLeft size={14} aria-hidden="true"/> Anterior</button><button disabled={loading || p.page * PAGE_SIZE >= total} onClick={() => update({ page: p.page + 1 })}>Siguiente <ArrowRight size={14} aria-hidden="true"/></button></div></nav>}
    </div></div>
    <dialog ref={filterDialog} className="catalog-filter-dialog" aria-labelledby="mobile-filters-title" onClose={() => filterTrigger.current?.focus()} onClick={event => { if (event.target === filterDialog.current) filterDialog.current.close(); }}>
      <div className="mobile-filters-heading"><h2 id="mobile-filters-title">Filtrar componentes</h2><button aria-label="Cerrar filtros" onClick={() => filterDialog.current?.close()}><X size={20} aria-hidden="true"/></button></div>
      <div className="mobile-filter-controls">{filterControls}</div><div className="mobile-filters-footer"><button className="primary" onClick={() => filterDialog.current?.close()}>{loading ? 'Buscando…' : `Ver ${total} ${total === 1 ? 'resultado' : 'resultados'}`}</button></div>
    </dialog>
  </section>;

  let content: React.ReactNode;
  if (route.kind === 'builder') content = <Suspense fallback={<div className="route-loading" role="status"><h1>Configurador de PC</h1><p>Cargando el configurador…</p></div>}><BuilderPage/></Suspense>;
  else if (route.kind === 'deals') content = <Suspense fallback={<p role="status">Cargando ofertas…</p>}><DealsPage/></Suspense>;
  else if (route.kind === 'about') content = <AboutPage/>;
  else if (route.kind === 'privacy') content = <PrivacyPage/>;
  else if (route.kind === 'notfound') content = <NotFoundPage/>;
  else if (route.kind === 'product') {
    if (detailState === 'missing') content = <NotFoundPage title="Producto no encontrado" text="Este producto no existe o ya no está en el catálogo."/>;
    else if (selected) content = <ProductPage product={selected} offers={offers} offersLoading={offersLoading} offerError={offerError} shops={shops} status={status}
      categoryName={categoryName(selected.category_id)} fromUrl={fromUrl} compared={compare.some(c => c.id === selected.id)} compareDisabled={compare.length >= 4} onCompare={() => toggleCompare(selected)}/>;
    else if (detailState === 'error') content = <div className="empty" role="alert"><h1>No se ha podido cargar el producto</h1><p>Comprueba tu conexión e inténtalo de nuevo.</p><button className="primary" onClick={() => setRetry(x => x + 1)}>Reintentar</button></div>;
    else content = <div className="product-page is-loading" aria-busy="true" role="status"><span className="sr-only">Cargando producto…</span><div className="skeleton pp-skeleton"/><div className="skeleton"/></div>;
  }
  else if (isLanding) content = <HomePage search={searchBox('hero')} categories={categories} status={status} freshness={freshness}/>;
  else content = catalogView;

  const activeCategory = route.kind === 'category' ? route.id : route.kind === 'product' && selected ? selected.category_id : null;
  return <div className={`app-shell ${isLanding ? 'is-landing' : ''}`}>
    <a className="skip-link" href="#main-content" onClick={e => { e.preventDefault(); focusHeading(); }}>Saltar al contenido</a>
    <header className="site-header">
      <div className="header-row">
        <Link className="logo" href="/" aria-label="StockFinder, inicio"><LogoMark/><span className="logo-text">Stock<b>Finder</b></span></Link>
        {!isLanding && searchBox('header')}
        <div className="header-favorites"><Watchlist endpoint={featureOnly ? '/api/v1/builder/watchlist' : '/api/v1/watchlist'}/></div>
        <nav className="header-actions" aria-label="Herramientas">
          {/* Compact: a status dot only; the full text is the tooltip/aria-label and lives in the footer. */}
          <span className="catalog-freshness catalog-freshness-dot" role="img" aria-label={freshness} title={`${freshness}${staleSince ? ` · Comprobación más reciente: ${absoluteDate(staleSince)}` : ''}`}><span className={`dot ${(featureOnly ? featureStale : status?.stale) ? 'amber' : ''}`} aria-hidden="true"/></span>
          {status?.data_mode === 'synthetic' && <span className="demo-pill">Datos de demostración</span>}
          <Link className={`header-link ${route.kind === 'builder' ? 'active' : ''}`} href="/configurador" aria-current={route.kind === 'builder' ? 'page' : undefined}><Wrench size={17} aria-hidden="true"/><span>Configurador de PC</span></Link>
          <Link className="header-link" href="/ofertas" aria-current={route.kind === 'deals' ? 'page' : undefined} title="Ofertas"><TrendingDown size={17} aria-hidden="true"/><span>Ofertas</span></Link>
          <AppearanceSettings/>
        </nav>
      </div>
      <nav className="category-bar" aria-label="Categorías"><div className="category-bar-inner">
        {categories.map(c => <Link key={c.id} aria-label={c.name} className={activeCategory === c.id ? 'chosen' : ''} aria-current={route.kind === 'category' && route.id === c.id ? 'page' : undefined} href={categoryPath(c.id)}><CategoryIcon category={c.id}/><span className="cat-long">{c.name}</span><span className="cat-short" aria-hidden="true">{SHORT_NAMES[c.id] ?? c.name}</span></Link>)}
      </div></nav>
    </header>
    <main id="main-content">
      <div className="page-content">{featureOnly && featureStale && <p role="alert">La réplica del catálogo necesita actualizarse. Comprueba las fechas de las ofertas antes de comprar.</p>}{content}</div>
    </main>
    <footer className="site-footer"><div className="footer-inner">
      <div className="footer-about"><span className="footer-brand">StockFinder</span><p>{status?.data_mode === 'synthetic' ? 'Precios de demostración · Sin compras reales' : 'Comparador de componentes de PC con fichas de fabricante y ofertas comprobadas.'}</p><p className="footer-fresh">{freshness}</p></div>
      <nav className="footer-links" aria-label="Información legal y metodología"><Link href="/acerca">Cómo funciona</Link><Link href="/privacidad">Privacidad</Link><Link href="/configurador">Configurador de PC</Link></nav>
      <nav className="footer-cats" aria-label="Secciones del catálogo">{categories.map(c => <Link key={c.id} href={categoryPath(c.id)}>{c.name}</Link>)}</nav>
    </div></footer>
    {compare.length > 0 && <div className="compare-bar" role="region" aria-label="Comparación">
      <ul className="compare-bar-items">{compare.map(c => <li key={c.id}><Thumb product={c} size="mini"/><span>{displayName(c)}</span><button type="button" aria-label={`Quitar ${displayName(c)}`} onClick={() => toggleCompare(c)}><X size={13} aria-hidden="true"/></button></li>)}</ul>
      <span className="compare-bar-count"><strong>{compare.length}/4</strong> seleccionados</span>
      <button className="text-button" onClick={() => setCompare([])}>Limpiar</button>
      <button ref={compareTrigger} className="primary" disabled={compare.length < 2} onClick={() => setCompareOpen(true)}>Comparar componentes <GitCompareArrows size={16} aria-hidden="true"/></button></div>}
    <CompareDialog open={compareOpen} items={compare} shops={shops} onClose={() => { setCompareOpen(false); compareTrigger.current?.focus(); }} onRemove={toggleCompare}/>
  </div>;
}
createRoot(document.getElementById('root')!).render(<App/>);
