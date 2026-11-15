import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { z } from 'zod';
import { Activity, ArrowDownToLine, ArrowUpFromLine, Check, CircleAlert, Clock3, Copy, ExternalLink, Filter, FolderOpen,
  History, Link2, LoaderCircle, Plus, Printer, RefreshCw, Save, Share2, ShieldCheck, Trash2 } from 'lucide-react';
import { SHARED_BUILD_ID, type Product } from '../../../packages/api-contracts/index';
import { CategoryIcon } from './CategoryIcon';
import { PurchasePlanner } from './PurchasePlanner';
import { PartPicker } from './PartPicker';
import { ProductImage } from './ProductImage';
import { displayName, keySpecs } from './display';
import { productPath } from './router';
import './builder.css';
import { checkCompatibility, createPriceSnapshot, comparePriceSnapshots, filterCompatibleOptions, builderSpecFilters,
  type BuildParts, type BuildSlot } from './pc-builder';

const SLOT_KEYS = ['cpu', 'motherboard', 'ram', 'gpu', 'storage', 'psu', 'case', 'cooler'] as const;
const SLOTS: { id: BuildSlot; name: string; hint: string }[] = [
  { id: 'cpu', name: 'Procesador', hint: 'Socket, núcleos y consumo' },
  { id: 'motherboard', name: 'Placa base', hint: 'Socket, memoria y formato' },
  { id: 'ram', name: 'Memoria RAM', hint: 'Generación, capacidad y velocidad' },
  { id: 'gpu', name: 'Tarjeta gráfica', hint: 'Memoria, potencia y espacio' },
  { id: 'storage', name: 'Almacenamiento', hint: 'Interfaz y capacidad' },
  { id: 'psu', name: 'Fuente de alimentación', hint: 'Potencia y conectores' },
  { id: 'case', name: 'Caja', hint: 'Formato y espacio disponible' },
  { id: 'cooler', name: 'Refrigeración', hint: 'Anclaje y dimensiones' },
];
const STORAGE_KEY = 'stockfinder.pc-builds.v1';
const MAX_BUILDS = 50;
const MAX_SNAPSHOTS = 100;
type PriceSnapshot = ReturnType<typeof createPriceSnapshot>;
interface SavedBuild {
  id: string; name: string; created_at: string; updated_at: string;
  parts: BuildParts; snapshots: PriceSnapshot[];
}
const date = z.string().datetime({ offset: true });
const identifier = z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/);
const shortText = z.string().min(1).max(250);
const cents = z.number().int().min(0).max(100000000);
const slotSchema = z.enum(SLOT_KEYS);
const productSchema = z.object({
  id: identifier, slug: identifier, name: shortText, category_id: slotSchema, brand: shortText,
  specs: z.record(z.string().max(80), z.string().max(250)).refine(v => Object.keys(v).length <= 24),
  min_price_cents: cents.nullable(), offers_count: z.number().int().nonnegative(), last_checked_at: date.nullable(),
  manufacturer_url: z.string().url().max(2000).refine(v => v.startsWith('https://')).nullable().optional(),
  specs_verified_at: date.nullable().optional(), mpn: shortText.nullable().optional(),
  mpn_source: z.enum(['manufacturer','retailer']).nullable().optional(),
  retailer_codes: z.array(z.object({ shop_id: identifier, code: shortText }).strict()).max(8).nullable().optional(),
  image_path: z.string().regex(/^\/images\/products\/[a-zA-Z0-9_/-]+\.(png|jpe?g|webp|svg)$/).nullable().optional(),
  image_source_url: z.string().url().max(2000).refine(v => v.startsWith('https://')).nullable().optional(),
  image_scope: z.enum(['model','family']).nullable().optional(),
}).strict();
const snapshotSchema = z.object({
  id: identifier, created_at: date,
  items: z.array(z.object({ slot: slotSchema, product_id: identifier, name: shortText,
    price_cents: cents.nullable(), last_checked_at: date.nullable() }).strict()).min(1).max(8),
  total_known_cents: z.number().int().nonnegative().max(800000000),
  priced_items: z.number().int().min(0).max(8), total_items: z.number().int().min(0).max(8),
}).strict().superRefine((snapshot, ctx) => {
  const priced = snapshot.items.filter(item => item.price_cents !== null);
  const total = priced.reduce((sum, item) => sum + item.price_cents!, 0);
  if (new Set(snapshot.items.map(item => item.slot)).size !== snapshot.items.length ||
      new Set(snapshot.items.map(item => item.product_id)).size !== snapshot.items.length ||
      total !== snapshot.total_known_cents || priced.length !== snapshot.priced_items ||
      snapshot.items.length !== snapshot.total_items) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'La captura contiene totales o componentes inconsistentes.' });
  }
});
const savedSchema = z.object({
  id: identifier, name: z.string().trim().min(1).max(80), created_at: date, updated_at: date,
  parts: z.record(slotSchema, productSchema), snapshots: z.array(snapshotSchema).min(1).max(MAX_SNAPSHOTS),
}).strict().superRefine((build, ctx) => {
  if (!Object.keys(build.parts).length || Object.entries(build.parts).some(([slot, product]) => slot !== product.category_id) ||
      new Set(build.snapshots.map(snapshot => snapshot.id)).size !== build.snapshots.length) {
    ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'Lista o capturas inconsistentes.' });
  }
});
const envelopeSchema = z.object({ schema_version: z.literal(1), builds: z.array(savedSchema).max(MAX_BUILDS) }).strict();
function clone<T>(value: T): T { return JSON.parse(JSON.stringify(value)); }
const euro = (value: number | null) => value === null ? 'Sin precio' : new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(value / 100);
const timestamp = (value: string) => new Date(value).toLocaleString('es-ES', { dateStyle: 'medium', timeStyle: 'short' });
const deltaText = (value: number | null) => value === null ? 'Sin comparación' : `${value > 0 ? '+' : ''}${euro(value)}`;
async function request<T>(path: string, signal?: AbortSignal, fresh = true): Promise<T> {
  const response = await fetch(`/api/v1/${path}`, { signal, headers: { 'X-SF-Client-Freshness': '1', ...(fresh ? { 'Cache-Control': 'no-cache' } : {}) } });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

export function PcBuilder() {
  const [parts, setParts] = useState<BuildParts>({});
  const [catalogue, setCatalogue] = useState<Partial<Record<BuildSlot, Product[]>>>({});
  const [catalogueErrors, setCatalogueErrors] = useState<Partial<Record<BuildSlot, string>>>({});
  const [pages, setPages] = useState<Partial<Record<BuildSlot, { page: number; query: string; total: number; loading: boolean }>>>({});
  const catalogueRequests = useRef(new Map<BuildSlot, AbortController>());
  const [saved, setSaved] = useState<SavedBuild[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [name, setName] = useState('Mi PC');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [baselineId, setBaselineId] = useState('');
  const [showAll, setShowAll] = useState<Partial<Record<BuildSlot, boolean>>>({});
  const [share, setShare] = useState<{ id: string; url: string } | null>(null);
  const [sharedCode, setSharedCode] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const storageReady = useRef(false);

  // A shared list in the URL (/configurador?lista=<uuid>) is loaded once on arrival.
  useEffect(() => {
    const id = new URLSearchParams(location.search).get('lista');
    if (id) void openShared(id);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // The share link describes the parts at the time it was created: hide it once they change.
  useEffect(() => setShare(null), [parts, name]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        if (raw.length > 1000000) throw new Error('storage-too-large');
        setSaved(envelopeSchema.parse(JSON.parse(raw)).builds as SavedBuild[]);
      }
      storageReady.current = true;
    } catch {
      setError('No se pudieron leer las listas de este navegador. No se sobrescribirán los datos guardados; exporta o revisa el almacenamiento antes de continuar.');
    }
  }, []);

  useEffect(() => {
    const requests = catalogueRequests.current;
    return () => {
      for (const controller of requests.values()) controller.abort();
      requests.clear();
    };
  }, []);

  async function loadCatalogue(id: BuildSlot, query = '', page = 1) {
    if (query.length === 1) {
      catalogueRequests.current.get(id)?.abort();
      setCatalogue(current => ({ ...current, [id]: [] }));
      setPages(current => ({ ...current, [id]: { page: 0, query, total: 0, loading: false } }));
      return;
    }
    const requests = catalogueRequests.current;
    requests.get(id)?.abort();
    const controller = new AbortController(); requests.set(id, controller);
    setCatalogueErrors(current => ({ ...current, [id]: undefined }));
    if (page === 1) setCatalogue(current => ({ ...current, [id]: [] }));
    setPages(current => ({ ...current, [id]: { page, query, total: current[id]?.total ?? 0, loading: true } }));
    try {
      const params = new URLSearchParams({ category: id, limit: '48', page: String(page), sort: 'featured' });
      if (query) params.set('q', query);
      const specs = showAll[id] ? {} : builderSpecFilters(id, parts);
      if (Object.keys(specs).length) params.set('specs', JSON.stringify(specs));
      const result = await request<{ data: Product[]; total: number }>(`builder/options?${params}`, controller.signal, false);
      if (controller.signal.aborted) return;
      setCatalogue(current => ({ ...current, [id]: page === 1 ? result.data : [...(current[id] ?? []), ...result.data] }));
      setPages(current => ({ ...current, [id]: { page, query, total: result.total, loading: false } }));
    } catch {
      if (!controller.signal.aborted) {
        setCatalogueErrors(current => ({ ...current, [id]: 'No se pudieron cargar los componentes. Vuelve a abrir el selector para reintentar.' }));
        setPages(current => ({ ...current, [id]: { page: page - 1, query, total: current[id]?.total ?? 0, loading: false } }));
      }
    } finally { if (requests.get(id) === controller) requests.delete(id); }
  }

  const checks = useMemo(() => checkCompatibility(parts), [parts]);
  const selected = Object.values(parts).filter((part): part is Product => Boolean(part));
  const priced = selected.filter(part => part.min_price_cents !== null);
  const knownTotal = priced.reduce((total, part) => total + part.min_price_cents!, 0);
  const active = saved.find(build => build.id === activeId);
  const latest = active?.snapshots.at(-1);
  const baseline = active?.snapshots.find(snapshot => snapshot.id === baselineId) ?? active?.snapshots[0];
  const comparison = baseline && latest && baseline.id !== latest.id ? comparePriceSnapshots(baseline, latest) : null;
  const failures = checks.filter(check => check.severity === 'error');
  const warnings = checks.filter(check => check.severity === 'warning');
  const passes = checks.filter(check => check.severity === 'pass');

  function persist(next: SavedBuild[]) {
    if (!storageReady.current) { setError('El almacenamiento local no está disponible. No se ha guardado ningún cambio.'); return false; }
    try {
      const serialized = JSON.stringify({ schema_version: 1, builds: next });
      if (serialized.length > 1000000) throw new Error('storage-limit');
      localStorage.setItem(STORAGE_KEY, serialized);
      setSaved(next); return true;
    } catch {
      setError('No se ha podido guardar: el navegador puede tener el almacenamiento bloqueado o lleno. Tus capturas anteriores no se han modificado.');
      return false;
    }
  }
  function validateName() {
    const value = name.trim();
    if (!value || value.length > 80) { setError('Escribe un nombre de entre 1 y 80 caracteres.'); return null; }
    if (!selected.length) { setError('Selecciona al menos un componente antes de guardar.'); return null; }
    return value;
  }
  function saveBuild() {
    setError(''); setMessage('');
    const title = validateName(); if (!title) return;
    const now = new Date().toISOString();
    if (active) {
      const next = saved.map(build => build.id === active.id ? { ...build, name: title, parts: clone(parts), updated_at: now } : build);
      if (persist(next)) setMessage('Lista actualizada. Las capturas de precios anteriores se conservan sin cambios.');
      return;
    }
    if (saved.length >= MAX_BUILDS) { setError('Puedes guardar hasta 50 listas en este navegador. Exporta y elimina alguna antes de añadir otra.'); return; }
    const snapshot = createPriceSnapshot(parts, now);
    const build: SavedBuild = { id: crypto.randomUUID(), name: title, created_at: now, updated_at: now,
      parts: clone(parts), snapshots: [snapshot] };
    if (persist([...saved, build])) {
      setActiveId(build.id); setBaselineId(snapshot.id);
      setMessage('Lista guardada con su primera captura de precios.');
    }
  }
  function capturePrices() {
    setError(''); setMessage('');
    if (!active) { setError('Guarda primero la lista para empezar su historial de precios.'); return; }
    const title = validateName(); if (!title) return;
    if (active.snapshots.length >= MAX_SNAPSHOTS) { setError('Esta lista ya tiene 100 capturas. Exporta el historial y crea una lista nueva.'); return; }
    const now = new Date().toISOString();
    const snapshot = createPriceSnapshot(parts, now);
    const next = saved.map(build => build.id === active.id ? { ...build, name: title, parts: clone(parts), updated_at: now,
      snapshots: [...build.snapshots, snapshot] } : build);
    if (persist(next)) setMessage('Nueva captura guardada. Su fecha y precios quedan conservados en el historial.');
  }
  async function refreshPrices() {
    if (!selected.length) return;
    setBusy(true); setError(''); setMessage('');
    const results = await Promise.allSettled(SLOT_KEYS.filter(slot => parts[slot]).map(async slot => {
      const result = await request<{ data: Product }>(`builder/products/${parts[slot]!.slug}`);
      if (result.data.id !== parts[slot]!.id || result.data.category_id !== slot) throw new Error('product-mismatch');
      return [slot, result.data] as const;
    }));
    const next = { ...parts }; let missed = 0;
    for (const result of results) {
      if (result.status === 'fulfilled') next[result.value[0]] = result.value[1]; else missed++;
    }
    setParts(next); setBusy(false);
    if (missed) setError(`No se pudieron actualizar ${missed} componentes; conservan su última ficha. Las capturas anteriores siguen intactas.`);
    else setMessage('Precios consultados en el catálogo. Pulsa «Guardar captura» para añadir esta consulta al historial. La fecha de cada oferta indica cuándo se comprobó en la tienda.');
  }
  async function shareBuild() {
    setError(''); setMessage('');
    const title = validateName(); if (!title) return;
    setBusy(true);
    try {
      const body = { name: title, parts: Object.fromEntries(SLOT_KEYS.filter(slot => parts[slot]).map(slot => [slot, parts[slot]!.slug])) };
      const response = await fetch('/api/v1/builds', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
      if (response.status === 429) throw new Error('Has compartido muchas listas en poco tiempo. Espera un rato y vuelve a intentarlo.');
      if (response.status === 404) throw new Error('Algún componente ya no está publicado en el catálogo. Quítalo o cámbialo antes de compartir.');
      if (!response.ok) throw new Error('No se ha podido crear el enlace. Inténtalo de nuevo más tarde.');
      const { id } = await response.json() as { id: string };
      if (!SHARED_BUILD_ID.test(id)) throw new Error('Respuesta inesperada del servidor.');
      const url = `${location.origin}/configurador?lista=${id}`;
      setShare({ id, url });
      try { await navigator.clipboard.writeText(url); setMessage('Enlace copiado al portapapeles. Cualquiera que lo abra verá esta configuración con los precios actuales.'); }
      catch { setMessage('Enlace creado. Cópialo para compartirlo.'); }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'No se ha podido crear el enlace.');
    } finally { setBusy(false); }
  }
  async function copyShare(text: string, what: string) {
    try { await navigator.clipboard.writeText(text); setError(''); setMessage(`${what} copiado al portapapeles.`); }
    catch { setError('El navegador no permite copiar automáticamente; selecciona el texto y cópialo a mano.'); }
  }
  /** Accepts a bare UUID or any link that contains `lista=<uuid>`. */
  async function openShared(input: string) {
    const id = input.trim().toLowerCase().match(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/)?.[0];
    setError(''); setMessage('');
    if (!id || !SHARED_BUILD_ID.test(id)) { setError('Introduce un código de lista válido (UUID) o el enlace completo que te han compartido.'); return; }
    setBusy(true);
    try {
      let shared: { name: string; parts: Partial<Record<BuildSlot, string>> };
      try { shared = (await request<{ data: typeof shared }>(`builds/${id}`)).data; }
      catch (failure) { throw new Error(String(failure).includes('404') ? 'No existe ninguna lista compartida con ese código.' : 'No se ha podido abrir la lista compartida. Inténtalo de nuevo.'); }
      const results = await Promise.allSettled(SLOT_KEYS.filter(slot => shared.parts[slot]).map(async slot => {
        const product = (await request<{ data: Product }>(`builder/products/${encodeURIComponent(shared.parts[slot]!)}`)).data;
        if (product.category_id !== slot) throw new Error('product-mismatch');
        return [slot, product] as const;
      }));
      const next: BuildParts = {}; let missed = 0;
      for (const result of results) { if (result.status === 'fulfilled') next[result.value[0]] = result.value[1]; else missed++; }
      setParts(next); setName(shared.name); setActiveId(null); setBaselineId(''); setShowAll({}); setSharedCode('');
      if (missed) setError(`${missed} componentes de la lista compartida ya no están en el catálogo y se han omitido.`);
      setMessage(`Lista compartida «${shared.name}» cargada con precios actuales. Pulsa «Guardar lista» para conservarla en este navegador.`);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'No se ha podido abrir la lista compartida.');
    } finally { setBusy(false); }
  }
  function loadBuild(build: SavedBuild) {
    setParts(clone(build.parts)); setName(build.name); setActiveId(build.id);
    setBaselineId(build.snapshots[0]?.id ?? ''); setError(''); setMessage('Lista cargada con sus precios guardados. Puedes consultar el catálogo para actualizarlos.');
  }
  function newBuild() {
    setParts({}); setActiveId(null); setName('Mi PC'); setBaselineId(''); setError(''); setMessage('');
  }
  function deleteBuild(build: SavedBuild) {
    if (persist(saved.filter(item => item.id !== build.id))) {
      if (activeId === build.id) newBuild();
      setMessage(`Se ha eliminado «${build.name}» de este navegador.`);
    }
  }
  function exportBuilds() {
    if (!saved.length) return;
    const objectUrl = URL.createObjectURL(new Blob([JSON.stringify({ schema_version: 1, builds: saved }, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a'); link.href = objectUrl; link.download = 'stockfinder-listas.json'; link.click();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  }
  async function importBuilds(file: File | undefined) {
    if (!file) return;
    setError(''); setMessage('');
    try {
      if (file.size > 1000000) throw new Error('file-too-large');
      const imported = envelopeSchema.parse(JSON.parse(await file.text())).builds;
      if (!imported.length || saved.length + imported.length > MAX_BUILDS) throw new Error('invalid-count');
      // A fresh list identity prevents an imported file overwriting existing history.
      const next = imported.map(build => ({ ...build, id: crypto.randomUUID(), name: `${build.name.slice(0, 68)} (importado)` })) as SavedBuild[];
      if (persist([...saved, ...next])) setMessage(`${next.length} listas importadas como copias nuevas. Ninguna captura existente se ha sustituido.`);
    } catch {
      setError('No se ha importado el archivo. Usa un JSON de StockFinder versión 1, hasta 1 MB, con listas y capturas válidas; el límite total es de 50 listas.');
    }
    if (fileInput.current) fileInput.current.value = '';
  }

  return <section className="pc-builder" aria-labelledby="builder-heading">
    <header className="builder-intro"><div><h1 id="builder-heading">Configurador de PC</h1>
      <p>Selecciona componentes, revisa compatibilidad y guarda listas y precios.</p></div>
      <button type="button" className="builder-secondary" disabled={busy} onClick={newBuild}><Plus size={17}/> Nueva lista</button></header>
    <div className="builder-local-note"><FolderOpen size={16}/><span>Las listas se guardan en este navegador. Exporta un archivo para conservar una copia o llevarlas a otro dispositivo.</span></div>
    {error && <p className="builder-feedback is-error" role="alert"><CircleAlert size={17}/>{error}</p>}
    {message && <p className="builder-feedback" role="status"><Check size={17}/>{message}</p>}
    <div className="builder-workspace"><div className="builder-main">
      <div className="builder-name-row"><label htmlFor="builder-name">Nombre de la lista<input id="builder-name" maxLength={80} value={name} onChange={event => setName(event.target.value)} disabled={busy}/></label>
        <button className="primary" type="button" disabled={busy} onClick={saveBuild}><Save size={16}/>{active ? 'Guardar cambios' : 'Guardar lista'}</button></div>
      <div className="builder-parts">
        {SLOTS.map(({ id, name: slotName, hint }, index) => {
          const part = parts[id]; const catalogueOptions = catalogue[id];
          const filter = filterCompatibleOptions(id, catalogueOptions ?? [], parts);
          const filtering = filter !== null && !showAll[id];
          const options = catalogueOptions;
          const inCatalogue = part && catalogueOptions?.some(option => option.id === part.id);
          const extra = part && options && !options.some(option => option.id === part.id)
            ? { product: part, note: inCatalogue ? `No compatible con ${filter?.label ?? 'la selección'}` : 'Ficha guardada' } : undefined;
          return <article className={`builder-part ${part ? 'has-part' : ''}`} key={id}>
            <div className="builder-slot-icon"><CategoryIcon category={id}/><span>{String(index + 1).padStart(2, '0')}</span></div>
            <div className="builder-slot-main"><label htmlFor={`builder-slot-${id}`}>{slotName}</label>
              <PartPicker id={`builder-slot-${id}`} category={id} options={options} value={part} extra={extra} disabled={busy} onSearch={query => void loadCatalogue(id, query)}
                searchKey={JSON.stringify([showAll[id], builderSpecFilters(id, parts)])}
                filterLabel={filtering ? filter?.label : undefined} onShowAll={() => setShowAll(current => ({...current,[id]:true}))}
                hasMore={!!pages[id] && (pages[id]!.page * 48 < pages[id]!.total)}
                onMore={() => void loadCatalogue(id, pages[id]?.query ?? '', (pages[id]?.page ?? 0) + 1)}
                loading={pages[id]?.loading} loadError={catalogueErrors[id]}
                placeholder={catalogueErrors[id] ? 'Categoría no disponible' : !catalogueOptions ? `Elige ${slotName.toLowerCase()}` : !options?.length ? (filtering ? 'Ningún componente compatible publicado' : 'Sin componentes publicados todavía') : `Elige ${slotName.toLowerCase()}`}
                onChange={chosen => setParts(current => { const next = { ...current }; if (chosen) next[id] = chosen; else delete next[id]; return next; })}/>
              <p>{catalogueErrors[id] ?? (part ? keySpecs(part, 3).map(chip => chip.text).join(' · ') : hint)}</p>
              {filter && <p className="builder-filter-note"><Filter size={12} aria-hidden="true"/>{filtering
                ? <>{pages[id]?.total ?? 0} resultados compatibles con {filter.label}</>
                : <>Mostrando todos ({pages[id]?.total ?? 0})</>}
                {<button type="button" className="text-button" onClick={() => setShowAll(current => ({ ...current, [id]: !current[id] }))}>{filtering ? 'Ver todos' : 'Solo compatibles'}</button>}</p>}
            </div>
            {part && <a className="builder-part-image-link" href={productPath(part.slug)} target="_blank" rel="noopener" title="Abrir la ficha en una pestaña nueva" aria-label={`Ficha de ${displayName(part)} (pestaña nueva)`}><ProductImage className="builder-part-image" category={id} product={part}/></a>}
            <div className="builder-part-price"><strong className={part && part.min_price_cents !== null ? 'price-verified' : 'price-pending'}>{part ? euro(part.min_price_cents) : '—'}</strong>
              <small>{part?.last_checked_at ? `Tienda: ${timestamp(part.last_checked_at)}` : part ? 'Sin oferta comprobada' : 'Sin seleccionar'}</small>
              {part && <span className="builder-part-actions">
                <a className="builder-remove builder-open" href={productPath(part.slug)} target="_blank" rel="noopener" aria-label={`Ver ficha de ${displayName(part)} en una pestaña nueva`}><ExternalLink size={12}/> Ficha</a>
                <button type="button" className="builder-remove" disabled={busy} aria-label={`Quitar ${slotName.toLowerCase()}`} onClick={() => setParts(current => { const next = { ...current }; delete next[id]; return next; })}><Trash2 size={12}/> Quitar</button></span>}
            </div>
          </article>;
        })}
      </div>
      {Object.values(catalogueErrors).some(Boolean) && <button className="builder-secondary" type="button" onClick={() => SLOTS.forEach(({ id }) => { if (catalogueErrors[id]) void loadCatalogue(id); })}><RefreshCw size={14}/> Reintentar categorías</button>}
      <div className="builder-art-note">Imágenes de fabricante cuando están disponibles; imágenes ilustrativas como respaldo. Comprueba la variante y sus dimensiones antes de comprar.</div>
    </div><aside className="builder-summary" aria-label="Resumen y compatibilidad">
      <div className="builder-budget"><span className="eyebrow">TU CONFIGURACIÓN</span><h2>{selected.length}<small> / 8 piezas</small></h2>
        <span className="builder-total-label">Total de precios conocidos</span><strong className={`builder-total ${priced.length ? 'price-verified' : 'price-pending'}`}>{priced.length ? euro(knownTotal) : 'Pendiente'}</strong>
        <p>{priced.length} de {selected.length} componentes seleccionados tienen precio. {priced.length < selected.length && 'El total es parcial; no incluye las piezas sin oferta.'}</p>
        <button className="builder-secondary" type="button" disabled={busy || !selected.length} onClick={refreshPrices}>{busy ? <LoaderCircle size={15} className="builder-spinning"/> : <RefreshCw size={15}/>} Consultar precios actuales</button>
        <button className="primary" type="button" disabled={busy || !active || !selected.length} onClick={capturePrices}><Clock3 size={16}/> Guardar captura</button>
        <button className="builder-secondary" type="button" disabled={busy || !selected.length} onClick={() => void shareBuild()}><Share2 size={16}/> Compartir configuración</button>
        {share && <div className="builder-share" role="group" aria-label="Enlace para compartir">
          <label>Enlace<span className="builder-share-row"><input readOnly value={share.url} onFocus={event => event.currentTarget.select()}/>
            <button type="button" className="builder-secondary" aria-label="Copiar enlace" onClick={() => void copyShare(share.url, 'Enlace')}><Copy size={15}/></button></span></label>
          <label>Código (UUID)<span className="builder-share-row"><input readOnly value={share.id} onFocus={event => event.currentTarget.select()}/>
            <button type="button" className="builder-secondary" aria-label="Copiar código" onClick={() => void copyShare(share.id, 'Código')}><Copy size={15}/></button></span></label>
          <small>Guarda los componentes elegidos, no los precios: quien lo abra verá los precios del momento.</small>
        </div>}
        <button className="builder-secondary" type="button" disabled={busy || !selected.length} onClick={() => window.print()}><Printer size={16}/> Imprimir / Guardar PDF</button>
        <p>En el diálogo de impresión puedes elegir «Guardar como PDF».</p>
      </div>
      <div className="builder-compatibility"><div className="builder-panel-heading"><ShieldCheck size={19}/><h2>Compatibilidad</h2></div>
        <p className="builder-rules-note">Comprobación orientativa según las fichas disponibles. No certifica el montaje completo ni sustituye revisar BIOS, conectores y espacio físico.</p>
        <div className="builder-check-counts"><span className={failures.length ? 'is-error' : ''}>{failures.length} conflictos</span><span>{warnings.length} avisos</span><span className="is-pass">{passes.length} comprobaciones</span></div>
        {!selected.length ? <p className="builder-start-hint">Selecciona tu primera pieza para empezar las comprobaciones.</p> : <div className="builder-check-list">{[...failures, ...warnings].map((check, index) => <div className={`builder-check ${check.severity}`} key={`${check.code}-${index}`}><CircleAlert size={15}/><p>{check.message}</p></div>)}
          {passes.length > 0 && <details><summary>{passes.length} comprobaciones sin conflicto detectado</summary>{passes.map((check, index) => <div className="builder-check pass" key={`${check.code}-${index}`}><Check size={15}/><p>{check.message}</p></div>)}</details>}</div>}
      </div>
    </aside></div>
    <PurchasePlanner products={selected} builder/>
    <section className="builder-history" aria-labelledby="builder-history-heading"><div className="builder-section-heading"><div><h2 id="builder-history-heading"><History size={21}/> Mis listas y precios</h2></div>
      <div className="builder-file-actions"><button className="builder-secondary" type="button" disabled={!saved.length || busy} onClick={exportBuilds}><ArrowDownToLine size={15}/> Exportar listas</button>
        <button className="builder-secondary" type="button" disabled={busy} onClick={() => fileInput.current?.click()}><ArrowUpFromLine size={15}/> Importar JSON</button>
        <input ref={fileInput} type="file" accept="application/json,.json" hidden onChange={event => { void importBuilds(event.target.files?.[0]); }}/></div></div>
      <form className="builder-open-shared" onSubmit={event => { event.preventDefault(); void openShared(sharedCode); }}>
        <label htmlFor="builder-shared-code"><Link2 size={15}/> Abrir una lista compartida</label>
        <span className="builder-share-row"><input id="builder-shared-code" placeholder="Pega el código o el enlace" value={sharedCode} maxLength={300} onChange={event => setSharedCode(event.target.value)} disabled={busy}/>
          <button className="builder-secondary" type="submit" disabled={busy || !sharedCode.trim()}>Abrir</button></span>
      </form>
      {!saved.length ? <div className="builder-history-empty"><FolderOpen size={28}/><h3>No hay listas guardadas</h3><p>Guarda una lista para conservar los componentes y comparar sus precios más adelante.</p></div> : <>
        <div className="builder-saved-grid">{saved.map(build => <article className={`builder-saved-card ${activeId === build.id ? 'is-active' : ''}`} key={build.id}>
          <div className="builder-saved-top"><span><FolderOpen size={16}/>{build.name}</span><button type="button" className="builder-remove" disabled={busy} aria-label={`Eliminar lista ${build.name}`} onClick={() => deleteBuild(build)}><Trash2 size={15}/></button></div>
          <p>{Object.keys(build.parts).length} piezas · {build.snapshots.length} {build.snapshots.length === 1 ? 'captura' : 'capturas'}</p><small>Guardada: {timestamp(build.updated_at)}</small>
          <button className="builder-secondary" type="button" disabled={busy} onClick={() => loadBuild(build)}>{activeId === build.id ? 'Lista seleccionada' : 'Cargar lista'}</button></article>)}</div>
        {active && latest && <div className="builder-snapshot-panel"><div className="builder-snapshot-heading"><div><h3><Activity size={18}/> Historial de «{active.name}»</h3><p>Última captura: {timestamp(latest.created_at)} · {latest.priced_items}/{latest.total_items} precios conocidos</p></div>
          {active.snapshots.length > 1 && <label>Comparar última captura con<select value={baseline?.id ?? ''} onChange={event => setBaselineId(event.target.value)}>{active.snapshots.slice(0, -1).map(snapshot => <option value={snapshot.id} key={snapshot.id}>{timestamp(snapshot.created_at)}</option>)}</select></label>}</div>
          {comparison && baseline ? <><div className="builder-delta"><span>Cambio entre {timestamp(baseline.created_at)} y {timestamp(latest.created_at)}</span><strong className={comparison.delta_cents !== null && comparison.delta_cents < 0 ? 'decrease' : ''}>{deltaText(comparison.delta_cents)}</strong><p>{comparison.comparable_items} componentes con precio en ambas capturas. {!comparison.complete && 'Comparación parcial: las piezas cambiadas o sin precio no cuentan como cero.'}</p></div>
            <div className="builder-price-table"><table><thead><tr><th>Componente</th><th>Captura anterior</th><th>Última captura</th><th>Cambio</th></tr></thead><tbody>{comparison.changes.map(change => <tr key={change.product_id}><th>{change.name}</th><td>{euro(change.before_cents)}</td><td>{euro(change.after_cents)}</td><td className={change.delta_cents !== null && change.delta_cents < 0 ? 'decrease' : ''}>{deltaText(change.delta_cents)}</td></tr>)}</tbody></table></div></> : <p className="builder-start-hint">Ya tienes la primera captura. Consulta precios actuales y guarda una nueva captura para ver qué ha cambiado.</p>}
          <details className="builder-capture-list"><summary>Ver {active.snapshots.length === 1 ? 'la captura guardada' : `las ${active.snapshots.length} capturas guardadas`}</summary><ol>{active.snapshots.map(snapshot => <li key={snapshot.id}><time dateTime={snapshot.created_at}>{timestamp(snapshot.created_at)}</time><span>{snapshot.priced_items}/{snapshot.total_items} precios</span><strong>{snapshot.priced_items ? euro(snapshot.total_known_cents) : 'Sin precios'}</strong></li>)}</ol></details>
        </div>}
      </>}
    </section>
    {createPortal(<article className="builder-print-report" aria-label="Lista para imprimir">
      <header className="builder-print-header">
        <div className="builder-print-brand"><span className="builder-print-monogram">SF</span><strong>Stock<span>Finder</span></strong></div>
        <div className="builder-print-document"><span>CONFIGURACIÓN DE PC</span><time>{timestamp(new Date().toISOString())}</time></div>
      </header>
      <section className="builder-print-intro">
        <p className="builder-print-eyebrow">TU PRÓXIMO EQUIPO</p>
        <h1 className={name.trim().length>50 ? 'is-long-title' : undefined}>{name.trim() || 'Mi PC'}</h1>
      </section>
      <section className="builder-print-budget" aria-label="Presupuesto de la configuración">
        <div><span className="builder-print-budget-label">SUBTOTAL DE PIEZAS CON PRECIO</span><strong className="builder-print-budget-amount">{priced.length ? euro(knownTotal) : 'Sin precios'}</strong>
          <p>{priced.length} de {selected.length} piezas seleccionadas tienen precio.{priced.length < selected.length && ' Subtotal parcial.'}</p></div>
        <div className="builder-print-completion"><strong>{selected.length}<span>/8</span></strong><span>piezas seleccionadas</span></div>
      </section>
      <div className="builder-print-metrics"><div><span>Presupuesto</span><strong>{selected.length > 0 && priced.length === selected.length ? 'Precios disponibles' : 'Por completar'}</strong></div>
        <div><span>Compatibilidad</span><strong>{!selected.length ? 'Sin selección' : failures.length ? `${failures.length} conflictos` : 'Sin conflictos detectados'}</strong></div>
        <div><span>Revisión pendiente</span><strong>{selected.length ? `${warnings.length} ${warnings.length === 1 ? 'aviso' : 'avisos'}` : 'Sin comprobar'}</strong></div></div>
      <section className="builder-print-components" aria-labelledby="builder-print-components-heading">
        <div className="builder-print-section-title"><span>01</span><h2 id="builder-print-components-heading">Los componentes</h2><small>Precios de la lista actual</small></div>
        <div className="builder-print-parts-grid">{SLOTS.map(({id,name:slotName},index) => {
          const part=parts[id]; const chips=part ? keySpecs(part,2) : [];
          return <article className={`builder-print-part ${!part ? 'is-unselected' : ''}`} key={id}>
            <div className="builder-print-part-label"><span>{String(index+1).padStart(2,'0')}</span><CategoryIcon category={id}/><strong>{slotName}</strong></div>
            <div className="builder-print-part-content">{part ? <ProductImage className="builder-print-part-image" category={id} product={part} sizes="60px" eager/> : <span className="builder-print-part-placeholder"><CategoryIcon category={id}/></span>}
              <div><h3>{part ? displayName(part) : 'Pendiente de seleccionar'}</h3>{chips.length>0 && <p>{chips.map(chip=>chip.text).join(' · ')}</p>}</div></div>
            <div className="builder-print-part-bottom"><span>{part?.last_checked_at ? `Comprobado: ${timestamp(part.last_checked_at)}` : part ? 'Sin oferta comprobada' : 'Completa esta pieza en el configurador'}</span><strong>{part ? euro(part.min_price_cents) : 'Pendiente'}</strong></div>
          </article>;
        })}</div>
      </section>
      <aside className="builder-print-price-note"><strong>Un presupuesto para comparar</strong><p>El subtotal no incluye piezas sin oferta, envío ni ajustes por destino. Los precios pueden cambiar: confirma disponibilidad e importe final en cada tienda.</p></aside>
      {selected.length>0 && <section className="builder-print-review">
        <div className="builder-print-review-heading"><p className="builder-print-eyebrow">REVISIÓN DEL EQUIPO</p><h2>Antes de montar</h2><p>{name.trim() || 'Mi PC'}</p></div>
        <div className={`builder-print-review-status ${failures.length ? 'is-error' : ''}`}><ShieldCheck/><div><strong>{failures.length ? `${failures.length} conflictos requieren atención` : 'Sin conflictos detectados en las comprobaciones disponibles'}</strong><p>La revisión es orientativa: no certifica el montaje completo ni sustituye los manuales del fabricante.</p></div></div>
        {failures.length>0 && <section className="builder-print-check-group is-error"><h3>Conflictos que resolver</h3><ol>{failures.map(check=><li key={check.code}><CircleAlert/><p>{check.message}</p></li>)}</ol></section>}
        {warnings.length>0 && <section className="builder-print-check-group"><div className="builder-print-section-title"><span>02</span><h3>Comprobaciones pendientes</h3><small>{warnings.length} {warnings.length===1 ? 'aviso' : 'avisos'}</small></div><ol>{warnings.map((check,index)=><li key={check.code}><span className="builder-print-check-number">{String(index+1).padStart(2,'0')}</span><p>{check.message}</p></li>)}</ol></section>}
        {passes.length>0 && <p className="builder-print-passed"><Check size={14}/>{passes.length} comprobaciones adicionales no han detectado conflictos.</p>}
        <section className="builder-print-references"><div className="builder-print-section-title"><span>03</span><h3>Referencias de tu selección</h3></div><p>Abre la ficha para consultar la variante exacta, sus especificaciones y las fuentes.</p>
          <div>{SLOTS.filter(({id})=>parts[id]).map(({id,name:slotName})=>{const part=parts[id]!;return <div className="builder-print-reference" key={id}><span>{slotName}</span><strong>{part.mpn || displayName(part)}</strong><a href={`https://www.stockfinder.workers.dev${productPath(part.slug)}`}>Ver ficha y fuentes ↗</a></div>;})}</div>
        </section>
        <aside className="builder-print-final-note"><strong>La última comprobación, en la tienda</strong><p>Revisa BIOS, memoria compatible, conectores y espacio físico antes de comprar. Las imágenes identifican el producto o su familia; la variante y sus dimensiones deben coincidir con la referencia seleccionada.</p></aside>
      </section>}
    </article>, document.body)}
  </section>;
}
