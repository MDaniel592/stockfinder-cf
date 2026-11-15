import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { ChevronDown, Search } from 'lucide-react';
import type { Product } from '../../../packages/api-contracts/index';
import { ProductImage } from './ProductImage';
import { displayName, keySpecs } from './display';

const euro = (value: number | null) => value === null ? 'Sin precio' : new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(value / 100);
const fold = (value: string) => value.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

/**
 * Searchable dropdown with a thumbnail per option (a native <select> cannot show images).
 * Combobox pattern: the trigger opens a popup with a search box; arrows move, Enter selects, Escape closes.
 */
export function PartPicker({ id, category, options, value, disabled, placeholder, extra, loading, loadError, onOpen, onChange, onSearch, onMore, hasMore, searchKey, filterLabel, onShowAll }: {
  id: string; category: string; options: Product[] | undefined; value: Product | undefined; disabled?: boolean;
  loading?: boolean; loadError?: string; onOpen?: () => void;
  onSearch?: (query: string) => void; onMore?: () => void; hasMore?: boolean; searchKey?: string;
  filterLabel?: string; onShowAll?: () => void;
  placeholder: string; /** A selected part that is not among `options` (filtered out or no longer published), with its note. */
  extra?: { product: Product; note: string }; onChange: (product: Product | undefined) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const searchCallback = useRef(onSearch); searchCallback.current = onSearch;
  useEffect(() => {
    if (!open || !searchCallback.current) return;
    const timer = setTimeout(() => searchCallback.current?.(query.trim()), 300);
    return () => clearTimeout(timer);
  }, [open, query, searchKey]);
  const all = useMemo(() => extra ? [extra.product, ...(options ?? [])] : options ?? [], [options, extra]);
  const shown = useMemo(() => {
    if (onSearch) return all;
    const terms = fold(query).split(/\s+/).filter(Boolean);
    if (!terms.length) return all;
    return all.filter(option => { const text = fold(`${option.name} ${option.brand} ${option.mpn ?? ''}`); return terms.every(term => text.includes(term)); });
  }, [all, query, onSearch]);

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('pointerdown', close);
    return () => document.removeEventListener('pointerdown', close);
  }, [open]);
  useEffect(() => { if (open) { input.current?.focus(); const index = value ? shown.findIndex(o => o.id === value.id) : -1; setActive(Math.max(0, index)); } }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: 'nearest' }); }, [active, open]);

  function openPicker() {
    setOpen(true);
    onOpen?.();
  }

  function choose(product: Product | undefined) { onChange(product); setOpen(false); setQuery(''); trigger.current?.focus(); }
  function onKey(event: KeyboardEvent<HTMLInputElement>) {
    const last = Math.max(0, shown.length - 1);
    if (event.key === 'ArrowDown') { event.preventDefault(); setActive(i => Math.min(last, i + 1)); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); setActive(i => Math.max(0, i - 1)); }
    else if (event.key === 'Home') { event.preventDefault(); setActive(0); }
    else if (event.key === 'End') { event.preventDefault(); setActive(Math.max(0, last)); }
    else if (event.key === 'Enter') { event.preventDefault(); if (shown[active]) choose(shown[active]); }
    else if (event.key === 'Escape') { event.preventDefault(); setOpen(false); setQuery(''); trigger.current?.focus(); }
    else if (event.key === 'Tab') setOpen(false);
  }

  return <div className={`part-picker ${open ? 'is-open' : ''}`} ref={root}>
    <button type="button" id={id} ref={trigger} className="part-picker-trigger" disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
      onClick={() => { if (open) setOpen(false); else openPicker(); }} onKeyDown={event => { if (event.key === 'ArrowDown' && !open) { event.preventDefault(); openPicker(); } }}>
      {value && <ProductImage className="part-picker-thumb" category={category} product={value} sizes="40px"/>}
      <span className={value ? 'part-picker-value' : 'part-picker-placeholder'}>{value ? displayName(value) : placeholder}</span>
      <ChevronDown size={16} aria-hidden="true"/>
    </button>
    {open && <div className="part-picker-popup">
      {onSearch && <div className="part-picker-scope"><span>{filterLabel ? `Filtro activo: ${filterLabel}` : 'Busca en toda la categoría'}</span>{filterLabel && <button type="button" className="text-button" onClick={onShowAll}>Ver todos</button>}</div>}
      <div className="part-picker-search"><Search size={15} aria-hidden="true"/>
        <input ref={input} role="combobox" aria-expanded="true" aria-controls={listId} aria-autocomplete="list" aria-label="Buscar por nombre, marca o referencia"
          aria-activedescendant={shown[active] ? `${listId}-${active}` : undefined} placeholder="Buscar por nombre, marca o referencia" maxLength={100}
          value={query} onChange={event => { setQuery(event.target.value); setActive(0); }} onKeyDown={onKey}/></div>
      <ul className="part-picker-list" role="listbox" id={listId} ref={list} aria-label="Componentes">
        {shown.map((option, index) => {
          const note = extra && option.id === extra.product.id ? extra.note : '';
          return <li key={option.id} id={`${listId}-${index}`} data-index={index} data-product-id={option.id} role="option" aria-selected={value?.id === option.id}
            className={`part-picker-option ${index === active ? 'is-active' : ''} ${note ? 'is-extra' : ''}`}
            onPointerMove={() => setActive(index)} onClick={() => choose(option)}>
            <ProductImage className="part-picker-thumb" category={category} product={option} sizes="56px"/>
            <span className="part-picker-text"><strong>{displayName(option)}</strong>
              <small>{note || keySpecs(option, 3).map(chip => chip.text).join(' · ')}</small></span>
            <span className={`part-picker-price ${option.min_price_cents !== null ? 'price-verified' : 'price-pending'}`}>{euro(option.min_price_cents)}</span>
          </li>;
        })}
        {!shown.length && <li className="part-picker-empty" role="presentation">{loadError ?? (loading ? 'Buscando en toda la categoría…' : `Ningún componente${filterLabel ? ' compatible' : ''} coincide con «${query}».`)}</li>}
      </ul>
      {loadError && shown.length > 0 && <p role="alert">{loadError}</p>}
      {query.trim().length === 1 && onSearch && <p role="status">Escribe al menos dos caracteres para buscar.</p>}
      {hasMore && <button type="button" className="part-picker-clear" disabled={loading} onClick={onMore}>{loading ? 'Cargando…' : 'Cargar más componentes'}</button>}
      {value && <button type="button" className="part-picker-clear" onClick={() => choose(undefined)}>Quitar selección</button>}
    </div>}
  </div>;
}
