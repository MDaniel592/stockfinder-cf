// Small product UI pieces shared by the catalogue, the product page, the landing page and the comparison.
import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { Product } from '../../../packages/api-contracts/index';
import { Copy, Flag, ShieldQuestion } from 'lucide-react';
import { ProductImage } from './ProductImage';

export type Item = Product & { retailer_codes?: Array<{ shop_id: string; code: string }> | null };
export type Shop = { id: string; name: string; url_public: string };

/** Product picture on a neutral tile: the manufacturer photo when there is one (model or family scope), otherwise the
 *  category placeholder render (ProductImage also falls back to it if the photo fails). Same box size in both cases. */
/** Rendered CSS width of each thumbnail box (app.css .thumb-*), so srcset picks the smallest adequate file. */
const THUMB_SIZES = { row: '48px', mini: '36px', card: '(max-width: 760px) 50vw, 300px', page: '(max-width: 760px) 72px, 240px' } as const;
export function Thumb({ product, size = 'card' }: { product: Product; size?: 'card' | 'row' | 'page' | 'mini' }) {
  return <div aria-hidden="true" className={`thumb thumb-${size} ${product.image_path ? 'has-photo' : 'is-placeholder'}`}>
    <ProductImage className="component-photo" category={product.category_id} product={product} eager={size === 'page'} sizes={THUMB_SIZES[size]}/></div>;
}

export const MPN_NOTE = 'Referencia según la tienda; modelo verificado en la ficha oficial, pendiente de confirmar el código';
/** Discreet mark for references taken from a retailer. Tooltip on hover, keyboard focus and tap; read by screen readers. */
export function RefMark({ product }: { product: Pick<Product, 'mpn_source'> }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null); const tip = useRef<HTMLSpanElement>(null);
  const place = () => {
    const b = button.current?.getBoundingClientRect(); const t = tip.current; if (!b || !t) return;
    const width = Math.min(280, window.innerWidth - 24);
    t.style.width = `${width}px`;
    t.style.left = `${Math.max(12, Math.min(b.left + b.width / 2 - width / 2, window.innerWidth - width - 12))}px`;
    const below = b.bottom + 8; const height = t.offsetHeight || 60;
    t.style.top = `${below + height > window.innerHeight - 8 ? Math.max(8, b.top - height - 8) : below}px`;
  };
  useLayoutEffect(() => { if (open) place(); }, [open]);
  useEffect(() => {
    if (!open) return;
    const close = (event: Event) => { if (event.type === 'keydown' ? (event as KeyboardEvent).key === 'Escape' : !button.current?.contains(event.target as Node)) setOpen(false); };
    const hide = () => setOpen(false);
    document.addEventListener('pointerdown', close); document.addEventListener('keydown', close);
    window.addEventListener('scroll', hide, { passive: true, capture: true }); window.addEventListener('resize', hide);
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('keydown', close);
      window.removeEventListener('scroll', hide, { capture: true }); window.removeEventListener('resize', hide); };
  }, [open]);
  if (product.mpn_source !== 'retailer') return null;
  return <span className="ref-mark-wrap">
    <button ref={button} type="button" className="ref-mark" aria-label="Referencia según la tienda" aria-describedby={id} aria-expanded={open}
      onClick={event => { event.preventDefault(); event.stopPropagation(); setOpen(v => !v); }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}>
      <ShieldQuestion size={13} aria-hidden="true"/>
    </button>
    <span ref={tip} id={id} role="tooltip" className={`ref-tooltip ${open ? 'is-open' : ''}`}>{MPN_NOTE}.</span>
  </span>;
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try { await navigator.clipboard.writeText(text); setDone(true); setTimeout(() => setDone(false), 1600); } catch { /* silent fallback */ }
  };
  return <button type="button" className="copy-button" onClick={copy} aria-label={`Copiar ${label}`}><Copy size={13} aria-hidden="true"/>{done ? 'Copiado' : 'Copiar'}<span className="sr-only" role="status">{done ? 'Copiado al portapapeles' : ''}</span></button>;
}

export function References({ product, shops }: { product: Item; shops: Shop[] }) {
  const codes = (product.retailer_codes ?? []).slice(0, 8);
  if (!product.mpn && !codes.length) return null;
  return <section className="references" aria-labelledby="references-title"><h3 id="references-title">Referencias</h3><ul>
    {product.mpn && <li><span className="ref-label">{product.mpn_source === 'retailer' ? 'Referencia' : 'Fabricante'}</span>
      <span className="ref-value"><code>{product.mpn}</code><RefMark product={product}/></span><CopyButton text={product.mpn} label="referencia"/></li>}
    {codes.map(c => { const shop = shops.find(s => s.id === c.shop_id)?.name ?? c.shop_id;
      return <li key={`${c.shop_id}-${c.code}`}><span className="ref-label">Código en {shop}</span><span className="ref-value"><code>{c.code}</code></span><CopyButton text={c.code} label={`código de ${shop}`}/></li>; })}
  </ul></section>;
}

export function ReportButton({ slug, offerId, label = 'Reportar un error' }: { slug: string; offerId?: string; label?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [note, setNote] = useState(''); const [website, setWebsite] = useState('');
  const [state, setState] = useState<'idle' | 'sending' | 'done' | 'error'>('idle');
  const open = () => { setState('idle'); ref.current?.showModal(); };
  const send = async (event: React.FormEvent) => {
    event.preventDefault(); setState('sending');
    try {
      const response = await fetch('/api/v1/reports', { method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ product_slug: slug, offer_id: offerId, note: note.trim(), page_url: location.href.slice(0, 500), website: website || undefined }) });
      if (!response.ok) throw new Error(String(response.status));
      setNote(''); setState('done');
    } catch { setState('error'); }
  };
  return <><button type="button" className={`report-button ${offerId ? 'is-icon' : ''}`} onClick={open} aria-label={`${label}: ${slug}`} title={label}><Flag size={14} aria-hidden="true"/>{offerId ? '' : label}</button>
    <dialog ref={ref} className="report-dialog" aria-label={label} onClick={e => { if (e.target === ref.current) ref.current?.close(); }}>
      <form onSubmit={send}><h3>{label}</h3>
        {state === 'done' ? <p role="status">Gracias, lo revisaremos.</p> : <>
          <textarea value={note} maxLength={500} rows={4} required onChange={e => setNote(e.target.value)} aria-label="Descripción del error"/>
          <input className="hp-field" tabIndex={-1} autoComplete="off" aria-hidden="true" value={website} onChange={e => setWebsite(e.target.value)}/>
          <small>{note.length}/500</small>
          {state === 'error' && <p role="alert">No se pudo enviar. Inténtalo más tarde.</p>}</>}
        <div className="report-actions"><button type="button" onClick={() => ref.current?.close()}>Cerrar</button>
          {state !== 'done' && <button type="submit" disabled={!note.trim() || state === 'sending'}>Enviar</button>}</div>
      </form></dialog></>;
}
