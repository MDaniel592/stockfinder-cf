import { useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import type { CatalogFilters } from '../../../packages/api-contracts/catalog-filters';

type Props = {
  data: CatalogFilters | null; loading: boolean; error: boolean; retry: () => void;
  brand: string[]; shop: string[]; stock: boolean; specs: Record<string, string[]>;
  minPrice: number | null; maxPrice: number | null;
  onBrand: (value: string) => void; onShop: (value: string) => void; onStock: (value: boolean) => void;
  onSpec: (key: string, value: string) => void;
  onPrice: (min: number | null, max: number | null) => void; onReset: () => void;
};
type Option = { id: string; label: string; count: number };
/** Checkbox list with counts; long lists show the first options (plus any checked one) and a "Ver más" toggle. */
function Options({ options, checked, onToggle, limit = 8 }: { options: Option[]; checked: (id: string) => boolean; onToggle: (id: string) => void; limit?: number }) {
  const [all, setAll] = useState(false);
  const long = options.length > limit + 2;
  const shown = !long || all ? options : options.filter((o, i) => i < limit || checked(o.id));
  return <div className="filter-options">{shown.map(option => <label key={option.id}><input type="checkbox" checked={checked(option.id)} onChange={() => onToggle(option.id)}/><span>{option.label}</span><small>{option.count}</small></label>)}
    {long && <button type="button" className="filter-more" aria-expanded={all} onClick={() => setAll(v => !v)}>{all ? 'Ver menos' : `Ver ${options.length - shown.length} más`}</button>}</div>;
}
export function CatalogFilterControls(props: Props) {
  const [min, setMin] = useState('');
  const [max, setMax] = useState('');
  const [priceError, setPriceError] = useState('');
  useEffect(() => { setMin(props.minPrice === null ? '' : String(props.minPrice / 100)); setMax(props.maxPrice === null ? '' : String(props.maxPrice / 100)); setPriceError(''); }, [props.minPrice, props.maxPrice]);
  return <>
    <div className="filter-panel-heading"><strong>Filtros</strong><button type="button" onClick={props.onReset}><RotateCcw size={12}/> Limpiar</button></div>
    <label className="filter-stock"><input type="checkbox" checked={props.stock} onChange={e => props.onStock(e.target.checked)}/> Solo con stock</label>
    <details className="filter-group" open><summary>Precio</summary>
      <form onSubmit={event => {
        event.preventDefault();
        const lower = min.trim() ? Math.round(Number(min.replace(',', '.')) * 100) : null;
        const upper = max.trim() ? Math.round(Number(max.replace(',', '.')) * 100) : null;
        if ([lower, upper].some(value => value !== null && (!Number.isSafeInteger(value) || value < 0 || value > 100000000)) || (lower !== null && upper !== null && lower > upper)) { setPriceError('Revisa el rango de precios.'); return; }
        setPriceError(''); props.onPrice(lower, upper);
      }}>
        <div className="filter-price-inputs"><label>Mínimo (€)<input type="text" inputMode="decimal" maxLength={10} value={min} placeholder="0" onChange={e => setMin(e.target.value)}/></label>
          <label>Máximo (€)<input type="text" inputMode="decimal" maxLength={10} value={max} placeholder="Sin límite" onChange={e => setMax(e.target.value)}/></label></div>
        {priceError && <p role="alert" className="filter-error">{priceError}</p>}
        <button className="filter-price-apply" type="submit">Aplicar precio</button>
        <p className="filter-caption">El rango solo incluye piezas con precio verificado.</p>
      </form>
    </details>
    {props.loading ? <p className="filter-caption" role="status">Cargando filtros…</p> : props.error ? <div className="filter-caption" role="alert">No se pudieron cargar los filtros. <button onClick={props.retry}>Reintentar</button></div> : <>
      {!!props.data?.brands.length && <details className="filter-group" open><summary>Marca</summary><Options options={props.data.brands.map(o => ({ id: o.value, label: o.value, count: o.count }))} checked={id => props.brand.includes(id)} onToggle={props.onBrand}/></details>}
      {!!props.data?.shops?.length && <details className="filter-group" open><summary>Tienda</summary><Options options={props.data.shops.map(o => ({ id: o.id, label: o.name, count: o.count }))} checked={id => props.shop.includes(id)} onToggle={props.onShop}/></details>}
      {props.data?.specs.map(facet => <details className="filter-group" key={facet.key} open><summary>{facet.key === 'Chip gráfico' ? 'Chip' : facet.key}</summary><Options options={facet.options.map(o => ({ id: o.value, label: o.value, count: o.count }))} checked={id => props.specs[facet.key]?.includes(id) ?? false} onToggle={id => props.onSpec(facet.key, id)}/></details>)}
    </>}
  </>;
}
