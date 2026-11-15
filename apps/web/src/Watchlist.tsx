import { useEffect, useId, useRef, useState } from 'react';
import { Heart, X } from 'lucide-react';
import type { Product } from '../../../packages/api-contracts/index';
import { readWatchlist, watchNotice, type WatchedProduct } from './watchlist';
import { enteredCents } from './purchase';
import { money, relativeAge } from './format';
import { Link } from './Link';

type WatchPrice = Pick<Product, 'slug' | 'name' | 'min_price_cents' | 'last_checked_at'>;
const KEY = 'stockfinder.watchlist.v1', EVENT = 'sf-watchlist';
function save(rows: WatchedProduct[]) { localStorage.setItem(KEY, JSON.stringify(rows)); window.dispatchEvent(new Event(EVENT)); }
function showFavorites() { try { return JSON.parse(localStorage.getItem('stockfinder.appearance.v1') ?? '{}').showFavorites !== false; } catch { return true; } }
export function WatchProduct({ product }: { product: Product }) {
  const [target,setTarget] = useState(''), [stock,setStock] = useState(true), [message,setMessage] = useState('');
  return <form className="watch-product" onSubmit={e => {
    e.preventDefault();
    const cents = target.trim() ? enteredCents(target) : null;
    if (target.trim() && cents === null) { setMessage('Introduce un precio válido.'); return; }
    try {
      const rows = readWatchlist(localStorage.getItem(KEY));
      if (rows.length >= 20 && !rows.some(w => w.slug === product.slug)) { setMessage('Puedes seguir hasta 20 productos. Quita alguno desde Favoritos y avisos.'); return; }
      save([...rows.filter(w => w.slug !== product.slug), { slug:product.slug,name:product.name,baseline:product.min_price_cents,target:cents,backInStock:stock,initialStock:product.min_price_cents !== null }]);
      setMessage('Guardado. Comprobaremos tus favoritos al abrir la web.');
    } catch { setMessage('No se pudo guardar. Comprueba el almacenamiento de este navegador.'); }
  }}><h2>Seguir precio y disponibilidad</h2><label>Precio objetivo (€), opcional<input inputMode="decimal" value={target} onChange={e => setTarget(e.target.value)} placeholder="Cualquier bajada"/></label><label><input type="checkbox" checked={stock} onChange={e => setStock(e.target.checked)}/> Avisar si vuelve a estar en stock</label><button type="submit" className="builder-secondary">Guardar favorito y aviso</button><p>Se guarda en este navegador. Los avisos se comprueban al abrir la web; no se envían correos ni notificaciones con la página cerrada.</p>{message && <p role="status">{message}</p>}</form>;
}
export function Watchlist({ endpoint = '/api/v1/watchlist' }: { endpoint?:string }) {
  const [visible,setVisible] = useState(showFavorites);
  const root=useRef<HTMLElement>(null),trigger=useRef<HTMLButtonElement>(null),panel=useRef<HTMLDivElement>(null);
  const panelId=useId();
  const [rows,setRows] = useState<WatchedProduct[]>([]), [products,setProducts] = useState<Record<string,WatchPrice>>({});
  const [error,setError] = useState(''), [open,setOpen] = useState(false), [busy,setBusy] = useState(false), [checked,setChecked] = useState(false);
  const abort = useRef<AbortController | null>(null);
  useEffect(()=>{
    const update=()=>setVisible(showFavorites());
    window.addEventListener('sf-appearance',update); window.addEventListener('storage',update);
    return ()=>{window.removeEventListener('sf-appearance',update);window.removeEventListener('storage',update);};
  },[]);
  useEffect(()=>{
    if(!visible) setOpen(false);
    if(!open || !visible) return;
    panel.current?.querySelector<HTMLButtonElement>('button')?.focus();
    const outside=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false);};
    const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'){setOpen(false);trigger.current?.focus();}};
    document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
    return ()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape);};
  },[open,visible]);
  async function check(current: WatchedProduct[]) {
    abort.current?.abort(); const controller = new AbortController(); abort.current = controller;
    setProducts({}); setChecked(false); setError('');
    if (!current.length) { setBusy(false); return; }
    setBusy(true);
    try {
      const query = new URLSearchParams(); current.map(w => w.slug).sort().forEach(slug => query.append('slug',slug));
      const response = await fetch(`${endpoint}?${query}`, { signal:controller.signal });
      if (!response.ok) throw new Error();
      const body = await response.json() as { data:WatchPrice[] };
      if (!controller.signal.aborted) {
        setProducts(Object.fromEntries(body.data.map(p => [p.slug,p]))); setChecked(true);
        // Remember an observed outage so a later restock can be reported even if it was in stock when first saved.
        const unavailable = new Set(body.data.filter(p => p.min_price_cents === null).map(p => p.slug));
        if (current.some(w => w.initialStock && unavailable.has(w.slug))) {
          const updated = current.map(w => unavailable.has(w.slug) ? { ...w, initialStock:false } : w);
          localStorage.setItem(KEY,JSON.stringify(updated)); setRows(updated);
        }
      }
    } catch { if (!controller.signal.aborted) setError('No se pudieron comprobar tus favoritos. Reintenta cuando tengas conexión.'); }
    finally { if (!controller.signal.aborted) setBusy(false); }
  }
  useEffect(() => {
    const load = () => { try { const current = readWatchlist(localStorage.getItem(KEY)); setRows(current); if(visible)void check(current); } catch { setError('No se pudieron leer tus favoritos. No se ha sobrescrito el contenido guardado.'); } };
    const storage=(event:StorageEvent)=>{if(event.key===KEY || event.key===null)load();};
    load(); window.addEventListener(EVENT,load); window.addEventListener('storage',storage);
    return () => { abort.current?.abort(); window.removeEventListener(EVENT,load); window.removeEventListener('storage',storage); };
  }, [endpoint,visible]);
  const notices = rows.filter(w => products[w.slug] && watchNotice(w,products[w.slug].min_price_cents));
  if(!visible)return null;
  return <aside ref={root} className={`watchlist watchlist-header ${notices.length ? 'has-updates' : ''}`} aria-label="Favoritos y avisos"><button ref={trigger} className="watchlist-toggle" type="button" aria-haspopup="dialog" aria-controls={panelId} aria-expanded={open} aria-label={`Favoritos y avisos (${rows.length})${notices.length ? ` · ${notices.length} ${notices.length===1?'novedad':'novedades'}`:''}`} title="Favoritos y avisos" onClick={() => setOpen(v => !v)}><Heart size={17} aria-hidden="true"/><span className="watchlist-label">Favoritos</span><span className="watchlist-count">{rows.length}</span>{notices.length > 0 && <span className="watchlist-badge">{notices.length}<span className="watchlist-badge-word"> {notices.length === 1 ? 'novedad' : 'novedades'}</span></span>}</button>
    {open && <div ref={panel} id={panelId} className="watchlist-popover" role="dialog" aria-label="Favoritos y avisos"><div className="watchlist-popover-heading"><h2>Favoritos y avisos</h2><button type="button" aria-label="Cerrar favoritos" onClick={()=>{setOpen(false);trigger.current?.focus();}}><X size={18} aria-hidden="true"/></button></div>
      {notices.length > 0 && <p className="watchlist-notice" role="status"><span className="watchlist-notice-dot" aria-hidden="true"/>Hay novedades de precio o disponibilidad en tus favoritos.</p>}
      {error && <p role="alert">{error}</p>}
      <p>Guardados en este navegador.</p><button type="button" className="builder-secondary" disabled={busy || !rows.length} onClick={() => void check(rows)}>{busy ? 'Comprobando…' : 'Actualizar favoritos'}</button>{!rows.length && <p>Abre una ficha para guardar tu primer favorito.</p>}<ul>{rows.map(w => {
      const p = products[w.slug]; const notice = p ? watchNotice(w,p.min_price_cents) : null;
      return <li key={w.slug}><Link href={`/p/${encodeURIComponent(w.slug)}`} onNavigate={()=>setOpen(false)}>{w.name}</Link><p>{notice && <strong>{notice} · </strong>}{p ? `${p.min_price_cents === null ? 'Sin oferta reciente en stock' : money(p.min_price_cents)}${p.last_checked_at ? ` · comprobado ${relativeAge(p.last_checked_at)}` : ''}` : checked ? 'Producto retirado o no publicado' : 'Sin comprobar'}{w.target !== null && ` · Objetivo: ${money(w.target)}`}</p><button className="text-button" type="button" onClick={() => { try { save(rows.filter(v => v.slug !== w.slug)); } catch { setError('No se pudo quitar el favorito.'); } }}>Quitar</button>{p && <button className="text-button" type="button" onClick={() => { try { save(rows.map(v => v.slug === w.slug ? {...v,baseline:p.min_price_cents,initialStock:p.min_price_cents !== null,target:notice === 'Precio objetivo alcanzado' ? null : v.target} : v)); } catch { setError('No se pudo guardar el cambio.'); } }}>Marcar como visto</button>}</li>;
    })}</ul></div>}
  </aside>;
}
