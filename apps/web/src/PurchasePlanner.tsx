import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Check, ChevronDown, Package, RefreshCw, Store, Truck } from 'lucide-react';
import type { Product } from '../../../packages/api-contracts/index';
import { purchasePlans, landedTotal, type BuyOffer, type PurchaseLine } from './purchase';
import { money, relativeAge } from './format';
import './purchase-planner.css';

const DESTINATIONS = [['peninsula','Península'],['baleares','Baleares'],['canarias','Canarias'],['ceuta-melilla','Ceuta / Melilla']];

export function PurchasePlanner({ products, builder = false }: { products: Product[]; builder?:boolean }) {
  const key = products.map(p => p.slug).sort().join('|');
  const [lines, setLines] = useState<PurchaseLine[]>([]), [busy,setBusy] = useState(false), [error,setError] = useState('');
  const [destination,setDestination] = useState('peninsula'), [selected,setSelected] = useState(0);
  const [costs,setCosts] = useState<Record<string, Record<string,{shipping:string;adjustment:string}>>>({});
  const controller = useRef<AbortController | null>(null);
  useEffect(() => { controller.current?.abort(); setLines([]); setCosts({}); setSelected(0); setError(''); setBusy(false); return () => controller.current?.abort(); }, [key]);
  const plans = useMemo(() => purchasePlans(lines), [lines]);
  async function load() {
    controller.current?.abort(); const abort = new AbortController(); controller.current = abort;
    setBusy(true); setError('');
    try {
      const result = await Promise.all(products.map(async p => {
        const res = await fetch(`/api/v1/${builder ? 'builder/' : ''}products/${encodeURIComponent(p.slug)}/offers`, { signal: abort.signal, headers: { 'X-SF-Client-Freshness': '1' } });
        if (!res.ok) throw new Error();
        const body = await res.json() as { data: BuyOffer[] };
        return { id:p.id, name:p.name, offers:body.data };
      }));
      if (!abort.signal.aborted) { setLines(result); setSelected(0); }
    } catch { if (!abort.signal.aborted) setError('No se pudieron consultar todas las ofertas. Vuelve a intentarlo.'); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  const shops = [...new Set(plans.flatMap(p => p.shops))];
  const active = plans[selected] ?? plans[0];
  const destinationCosts = costs[destination] ?? {};
  const total = active ? landedTotal(active,destinationCosts) : null;
  const completeTotals = plans.map(plan => landedTotal(plan,destinationCosts)).filter((value):value is number => value !== null);
  const bestTotal = completeTotals.length ? Math.min(...completeTotals) : null;
  const allComparableKnown = plans.filter(plan=>!plan.missing.length).every(plan=>landedTotal(plan,destinationCosts)!==null);
  function cost(shop:string, field:'shipping'|'adjustment', value:string) {
    setCosts(old => ({ ...old, [destination]: { ...old[destination], [shop]: {
      shipping:old[destination]?.[shop]?.shipping ?? '', adjustment:old[destination]?.[shop]?.adjustment ?? '', [field]:value
    } } }));
  }
  return <section className="purchase-planner" aria-label="Plan de compra" aria-busy={busy}>
    <header className="purchase-heading"><div><span className="purchase-eyebrow">DÓNDE COMPRAR</span><h2>Plan de compra</h2><p>Compara combinar tiendas con hacer un solo pedido.</p></div>
      <button className="purchase-action" type="button" disabled={busy || !products.length} aria-busy={busy} onClick={() => void load()}><RefreshCw size={15} aria-hidden="true" className={busy ? 'purchase-spin' : ''}/>{busy ? 'Consultando ofertas…' : lines.length ? 'Actualizar ofertas' : 'Comparar compra por tiendas'}</button>
    </header>
    {error && <p className="purchase-error" role="alert">{error}</p>}
    {!lines.length && !error && <div className="purchase-empty"><Package size={22} aria-hidden="true"/><p>{!products.length ? 'Añade componentes para ver dónde comprarlos.' : busy ? 'Buscando ofertas recientes para tus componentes…' : `${products.length} ${products.length === 1 ? 'componente listo' : 'componentes listos'} para comparar precios y disponibilidad.`}</p></div>}
    {lines.length > 0 && active && <>
      <div className="purchase-toolbar"><span>{lines.length} {lines.length === 1 ? 'componente' : 'componentes'} · ofertas en stock comprobadas en 24 h</span><label>Destino<select aria-label="Destino" value={destination} onChange={e => setDestination(e.target.value)}>{DESTINATIONS.map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label></div>
      <div className="purchase-plans" role="radiogroup" aria-label="Opciones de compra">{plans.map((plan,index) => {
        const final = landedTotal(plan,destinationCosts);
        const complete = !plan.missing.length;
        const title = index === 0 ? plan.shops.length > 1 ? 'Combinar tiendas' : 'Mejores ofertas' : plan.name;
        return <article key={plan.name} className={`purchase-option ${selected === index ? 'is-selected' : ''}`}>
          <label className="purchase-option-select"><input type="radio" name={`purchase-plan-${builder ? 'builder' : 'product'}`} checked={selected === index} onChange={() => setSelected(index)}/><span><strong>{title}</strong><small>{index === 0 ? `${plan.shops.length} ${plan.shops.length === 1 ? 'tienda' : 'tiendas'} · mejor precio por pieza` : 'Un solo pedido'}</small></span>{selected === index && <Check size={16} aria-hidden="true"/>}</label>
          <span className="purchase-price-label">{complete ? 'Componentes' : 'Subtotal parcial'}</span><strong className="purchase-option-price">{plan.lines.length ? money(plan.subtotal) : 'Sin oferta'}</strong>
          <div className="purchase-option-total"><span>Total con gastos</span><strong>{final === null ? 'Pendiente' : money(final)}</strong></div>
          <span className={`purchase-coverage ${complete ? '' : 'is-partial'}`}>{plan.lines.length}/{lines.length} piezas{final !== null ? allComparableKnown && final === bestTotal ? ' · menor total calculado' : ' · gastos incluidos' : !complete ? ' · faltan ofertas' : ' · gastos por completar'}</span>
        </article>;
      })}</div>
      <div className="purchase-selected-summary"><Store size={17} aria-hidden="true"/><span><strong>{selected === 0 ? active.shops.length > 1 ? 'Compra en varias tiendas' : active.shops[0] ?? 'Sin ofertas recientes' : active.name}</strong><small>{active.lines.length}/{lines.length} piezas disponibles{active.shops.length > 1 ? ` en ${active.shops.length} tiendas` : ''}</small></span><div><span>{total === null ? 'Subtotal de componentes' : 'Total calculado'}</span><strong>{total !== null ? money(total) : active.lines.length ? money(active.subtotal) : 'Sin precios'}</strong></div></div>
      {active.missing.length > 0 && <p className="purchase-missing">Sin oferta reciente: {active.missing.join(', ')}.</p>}
      <details className="purchase-breakdown"><summary>Ver piezas y enlaces a tiendas <span>{active.lines.length}<ChevronDown size={14} className="purchase-chevron" aria-hidden="true"/></span></summary><div className="purchase-shop-list">{active.shops.map(shop=><div key={shop} className="purchase-shop"><h3><Store size={15} aria-hidden="true"/>{shop}</h3><ul>{active.lines.filter(l=>l.offer.shop_name===shop).map(line=><li key={line.offer.id}><span><strong>{line.name}</strong><small>Comprobado {relativeAge(line.offer.last_checked_at)}</small></span><b>{money(line.offer.price_cents)}</b><a href={line.offer.url_public} target="_blank" rel="noopener noreferrer" aria-label={`Ver ${line.name} en ${shop}`}>Ver en tienda <ArrowUpRight size={13} aria-hidden="true"/></a></li>)}</ul></div>)}</div></details>
      <details className="purchase-costs"><summary><Truck size={17} aria-hidden="true"/><span>Envío y otros gastos<small>Completa el total para {DESTINATIONS.find(([id])=>id===destination)?.[1]}</small></span><b>{shops.length} {shops.length === 1 ? 'tienda' : 'tiendas'}</b><ChevronDown size={14} className="purchase-chevron" aria-hidden="true"/></summary><div className="purchase-costs-body"><p>Gastos confirmados para este pedido, una vez por tienda. Vacío = pendiente; introduce 0 si es gratuito.</p><div className="purchase-cost-grid">{shops.map(shop=><fieldset key={shop}><legend>{shop}</legend><label>Envío (€)<input inputMode="decimal" placeholder="Pendiente" aria-label={`Envío de ${shop}`} value={destinationCosts[shop]?.shipping ?? ''} onChange={e=>cost(shop,'shipping',e.target.value)}/></label><label>Otros gastos (€)<input inputMode="decimal" placeholder="Pendiente" aria-label={`Gastos adicionales de ${shop}`} value={destinationCosts[shop]?.adjustment ?? ''} onChange={e=>cost(shop,'adjustment',e.target.value)}/></label></fieldset>)}</div>{destination !== 'peninsula' && <p>Confirma entrega, impuestos y gestión para tu dirección. No se aplica devolución de IVA ni IGIC/IPSI automáticamente.</p>}</div></details>
      <p className="purchase-footnote">Los envíos pueden cambiar qué opción resulta más barata. Comprueba el importe final y el stock en la tienda.</p>
    </>}
  </section>;
}
