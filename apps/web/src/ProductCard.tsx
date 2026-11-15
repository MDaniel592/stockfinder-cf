import { Link } from './Link';
import { absoluteDate, money, offerFreshness, relativeAge } from './format';
import { productPath } from './router';
import { displayName, keySpecs, nameStartsWithBrand, tableColumns } from './display';
import { ArrowDown, ArrowUp, ArrowUpDown, GitCompareArrows } from 'lucide-react';
import { RefMark, Thumb, type Item } from './ProductBits';

export function stockLabel(product: Item) {
  if (product.min_price_cents !== null) return { tone: 'ok', text: 'En stock' };
  return product.offers_count ? { tone: 'out', text: 'Sin stock' } : { tone: 'none', text: 'Sin ofertas' };
}
export function shopsLabel(count: number | null | undefined) {
  return count ? `${count} ${count === 1 ? 'tienda' : 'tiendas'}` : 'Sin tiendas';
}

type Sort = 'featured' | 'name' | 'price' | '-price';
/** Default catalogue view: a dense comparison table (one row per product, category spec columns, price right-aligned).
 *  Rows link to the product page through the name; on phones each row collapses to thumbnail · name + 2 specs · price. */
export function ProductTable({ products, category, from, compare, onCompare, sort, onSort, demo }: {
  products: Item[]; category: string; from: string; compare: Item[]; onCompare: (product: Item) => void;
  sort: Sort; onSort: (sort: Sort) => void; demo?: boolean;
}) {
  const columns = tableColumns(category);
  const sortButton = (label: string, asc: Sort, desc: Sort | null) => {
    const active = sort === asc || sort === desc;
    const next: Sort = sort === asc && desc ? desc : sort === desc ? 'featured' : asc;
    return <button type="button" className={`th-sort ${active ? 'is-active' : ''}`} onClick={() => onSort(next)}
      aria-label={`Ordenar por ${label.toLowerCase()}${active ? (sort === asc ? ' (ascendente)' : ' (descendente)') : ''}`}>
      {label}{active ? (sort === asc ? <ArrowUp size={13} aria-hidden="true"/> : <ArrowDown size={13} aria-hidden="true"/>) : <ArrowUpDown size={13} aria-hidden="true"/>}</button>;
  };
  const ariaSort = (asc: Sort, desc: Sort | null) => sort === asc ? 'ascending' : desc && sort === desc ? 'descending' : undefined;
  return <table className={`product-table ${columns.length ? 'has-columns' : 'no-columns'}`}>
    <caption className="sr-only">Productos: nombre, características, disponibilidad y precio más bajo</caption>
    <thead><tr>
      <th scope="col" className="col-thumb"><span className="sr-only">Imagen</span></th>
      <th scope="col" className="col-name" aria-sort={ariaSort('name', null)}>{sortButton('Producto', 'name', null)}</th>
      {columns.map(c => <th scope="col" key={c.id} className="col-spec">{c.label}</th>)}
      <th scope="col" className="col-avail">Disponibilidad</th>
      <th scope="col" className="col-price" aria-sort={ariaSort('price', '-price')}>{sortButton('Precio', 'price', '-price')}</th>
      <th scope="col" className="col-compare"><span className="sr-only">Comparar</span><GitCompareArrows size={14} aria-hidden="true"/></th>
    </tr></thead>
    <tbody>{products.map(product => {
      const name = displayName(product); const stock = stockLabel(product);
      const fresh = product.last_checked_at && product.offers_count > 0 ? offerFreshness(product.last_checked_at) : null;
      const compared = compare.some(c => c.id === product.id);
      const chips = keySpecs(product, columns.length ? 2 : 4);
      return <tr key={product.id} className={`product-card product-row ${compared ? 'is-compared' : ''}`}>
        <td className="col-thumb"><Thumb product={product} size="row"/></td>
        <td className="col-name">
          <div className="pc-title">{!nameStartsWithBrand(product) && <span className="brand-label">{product.brand} </span>}<Link className="product-name" href={productPath(product.slug)} state={{ from }}>{name}</Link><RefMark product={product}/></div>
          {chips.length > 0 && <p className="spec-line">{chips.map(chip => <span key={chip.key} title={chip.label}>{chip.text}</span>)}</p>}
        </td>
        {columns.map(c => { const raw = c.value(product.specs ?? {}); const v = raw && product.brand && raw.toLowerCase().startsWith(product.brand.toLowerCase() + ' ') ? raw.slice(product.brand.length + 1) : raw; return <td key={c.id} className={`col-spec ${v ? '' : 'is-empty'}`} data-label={c.label}>{v ?? '—'}</td>; })}
        <td className="col-avail"><span className={`stock-dot ${stock.tone}`}>{stock.text}</span>
          <small>{shopsLabel(product.offers_count)}{demo ? ' · demo' : ''}{fresh && <> · <span className={`fresh ${fresh.level}`} title={absoluteDate(product.last_checked_at)}>{fresh.level === 'fresh' ? relativeAge(product.last_checked_at) : fresh.label}</span></>}</small></td>
        <td className="col-price"><div className="price-row" title={product.min_price_cents !== null && product.last_checked_at ? `Precio comprobado ${relativeAge(product.last_checked_at)} (${absoluteDate(product.last_checked_at)})` : undefined}>
          <strong className={product.min_price_cents === null ? 'price-pending' : 'price-verified'}>{product.min_price_cents === null ? stock.text : money(product.min_price_cents)}</strong></div>
          <small className="price-shops">{shopsLabel(product.offers_count)}</small></td>
        <td className="col-compare"><label className="pc-compare"><input type="checkbox" checked={compared} disabled={compare.length >= 4 && !compared} onChange={() => onCompare(product)}/><span className="sr-only">Comparar {name}</span></label></td>
      </tr>; })}</tbody>
  </table>;
}

/** Optional card view (grid toggle). The whole card links to the product page. */
export function ProductCard({ product, from, compared, compareDisabled, onCompare, demo }: {
  product: Item; from: string; compared: boolean; compareDisabled: boolean; onCompare: () => void; demo?: boolean;
}) {
  const href = productPath(product.slug); const state = { from };
  const name = displayName(product);
  const chips = keySpecs(product, 4);
  const stock = stockLabel(product);
  const fresh = product.last_checked_at ? offerFreshness(product.last_checked_at) : null;
  const priceTitle = product.min_price_cents !== null && product.last_checked_at ? `Precio comprobado ${relativeAge(product.last_checked_at)} (${absoluteDate(product.last_checked_at)})` : undefined;
  return <article className={`product-card ${compared ? 'is-compared' : ''}`}>
    <Thumb product={product}/>
    <div className="pc-body">
      {!nameStartsWithBrand(product) && <span className="brand-label">{product.brand}</span>}
      <h3 className="pc-title"><Link className="product-name" href={href} state={state}>{name}</Link><RefMark product={product}/></h3>
      {chips.length > 0 && <p className="spec-line">{chips.map(chip => <span key={chip.key} title={chip.label}>{chip.text}</span>)}</p>}
    </div>
    <div className="pc-offer">
      <div className="price-row" title={priceTitle}>
        {product.min_price_cents !== null && <small>Desde</small>}
        <strong className={product.min_price_cents === null ? 'price-pending' : 'price-verified'}>{product.min_price_cents === null ? 'Sin precio' : money(product.min_price_cents)}</strong>
      </div>
      <p className="pc-meta"><span className={`stock-dot ${stock.tone}`}>{stock.text}</span><span>{shopsLabel(product.offers_count)}{demo ? ' · demo' : ''}</span></p>
      {fresh && product.offers_count > 0 && <p className={`pc-fresh ${fresh.level}`} title={absoluteDate(product.last_checked_at)}>{fresh.level === 'fresh' ? `Comprobado ${relativeAge(product.last_checked_at)}` : fresh.label}</p>}
    </div>
    <label className="pc-compare"><input type="checkbox" checked={compared} disabled={compareDisabled && !compared} onChange={onCompare}/><span>Comparar</span></label>
  </article>;
}
