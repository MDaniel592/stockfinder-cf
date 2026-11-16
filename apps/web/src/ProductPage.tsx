import { PurchasePlanner } from './PurchasePlanner';
import { WatchProduct } from './Watchlist';
import type { CatalogStatus, Offer } from '../../../packages/api-contracts/index';
import { ArrowLeft, ArrowUpRight, BadgeCheck, ChevronRight, GitCompareArrows, Store } from 'lucide-react';
import { Link } from './Link';
import { PriceHistory } from './PriceChart';
import { absoluteDate, money, offerFreshness, relativeAge } from './format';
import { categoryPath } from './router';
import { displayName, groupSpecs, keySpecs } from './display';
import { References, RefMark, ReportButton, Thumb, type Item, type Shop } from './ProductBits';
import { shopsLabel } from './ProductCard';
import { ShopName } from './ShopLogo';

const SHOP_IMAGE_HOSTS: Array<[RegExp, string]> = [[/(^|\.)pccomponentes\.com$|(^|\.)pccomponentes\./i, 'PcComponentes'], [/(^|\.)coolmod\.com$|(^|\.)coolmod\./i, 'Coolmod']];
/** Retailer credit for images taken from a shop (explicit origin field when present, else the source URL host). */
function shopImageCredit(product: Item): string | null {
  const extra = product as Item & { image_origin?: string | null; image_credit?: string | null };
  const declared = extra.image_credit ?? extra.image_origin;
  if (declared && !/^(manufacturer|fabricante|model|family)$/i.test(declared)) {
    const known = SHOP_IMAGE_HOSTS.find(([re]) => re.test(declared))?.[1];
    if (known || /pccomponentes|coolmod/i.test(declared)) return known ?? declared;
  }
  try { const host = product.image_source_url ? new URL(product.image_source_url).hostname : ''; return SHOP_IMAGE_HOSTS.find(([re]) => re.test(host))?.[1] ?? null; }
  catch { return null; }
}
function imageNote(product: Item) {
  if (!product.image_path) return 'Imagen ilustrativa de la categoría. No representa el modelo exacto.';
  const shop = shopImageCredit(product);
  if (shop) return `Imagen: ${shop}. Puede diferir de la presentación exacta del modelo.`;
  return product.image_scope === 'family'
    ? `Imagen oficial de la familia (${product.brand}). Puede diferir de la presentación exacta del modelo.`
    : `Imagen del fabricante (${product.brand}) para este modelo. Comprueba la presentación y variante de la tienda.`;
}

function Offers({ product, offers, loading, error, demo }: { product: Item; offers: Offer[]; loading: boolean; error: string; demo: boolean }) {
  if (loading) return <p className="offers-loading" role="status">Cargando ofertas…</p>;
  if (error) return <p role="alert" className="notice">{error}</p>;
  if (!offers.length) return <div className="offers-pending"><Store size={22} aria-hidden="true"/><div><strong>Todavía no hay ofertas verificadas</strong><p>Que no haya ofertas no significa que el producto esté agotado.</p></div></div>;
  const best = offers.find(o => o.in_stock);
  return <div className="offers">{offers.map(offer => {
    const fresh = offerFreshness(offer.last_checked_at);
    return <div key={offer.id} className={`offer-row offer-${fresh.level} ${offer === best ? 'is-best' : ''}`}>
      <div className="offer-shop"><ShopName name={offer.shop_name}/>{offer === best && offers.filter(o => o.in_stock).length > 1 && <span className="best-tag">Mejor precio</span>}
        <small title={absoluteDate(offer.last_checked_at)}>Comprobado {relativeAge(offer.last_checked_at)}</small>
        {fresh.label && <span className={`fresh-badge ${fresh.level}`}>{fresh.label}</span>}</div>
      <span className={offer.in_stock ? 'in-stock' : 'out-stock'}>{offer.in_stock ? 'Disponible' : 'Sin stock'}</span>
      <strong className={`offer-price ${offer.in_stock ? 'price-verified' : 'price-pending'}`}>{money(offer.price_cents)}</strong>
      <div className="offer-actions">
        {!demo && <a className="shop-offer-link" href={offer.url_public} target="_blank" rel="noopener noreferrer">Ver en tienda <ArrowUpRight size={14} aria-hidden="true"/><span className="sr-only"> {offer.shop_name} (se abre en otra pestaña)</span></a>}
        <ReportButton slug={product.slug} offerId={offer.id} label="Reportar error en esta oferta"/>
      </div>
    </div>; })}</div>;
}

export function ProductPage({ product, offers, offersLoading, offerError, shops, status, categoryName, fromUrl, compared, compareDisabled, onCompare }: {
  product: Item; offers: Offer[]; offersLoading: boolean; offerError: string; shops: Shop[]; status: CatalogStatus | null;
  categoryName: string; fromUrl: string | null; compared: boolean; compareDisabled: boolean; onCompare: () => void;
}) {
  const name = displayName(product);
  const demo = status?.data_mode === 'synthetic';
  const groups = groupSpecs(product);
  const chips = keySpecs(product, 5);
  const inStock = offers.filter(o => o.in_stock);
  const best = inStock[0] ?? null;
  const note = imageNote(product);
  const catHref = categoryPath(product.category_id);
  return <article className="product-page" aria-labelledby="product-title">
    <div className="pp-topline">
      <nav className="crumbs" aria-label="Ruta de navegación"><Link href="/">Inicio</Link><ChevronRight size={14} aria-hidden="true"/><Link href={catHref}>{categoryName || product.category_id}</Link><ChevronRight size={14} aria-hidden="true"/><span aria-current="page">{name}</span></nav>
      {fromUrl && <Link className="back-link" href={fromUrl} onClick={event => { if (history.length > 1 && !event.metaKey && !event.ctrlKey && !event.shiftKey && event.button === 0) { event.preventDefault(); history.back(); } }}><ArrowLeft size={15} aria-hidden="true"/> Volver a los resultados</Link>}
    </div>
    <header className="pp-hero">
      <div className="pp-media"><Thumb product={product} size="page"/>{note && <p className="illustrative-note">{note}</p>}</div>
      <div className="pp-summary">
        <p className="eyebrow">{product.brand} · {categoryName}</p>
        <h1 id="product-title">{name}</h1>
        {chips.length > 0 && <ul className="pp-keyspecs" aria-label="Especificaciones clave">{chips.map(c => <li key={c.key}><span>{c.label}</span>{c.value}</li>)}</ul>}
        {product.mpn && <p className="pp-ref">Ref. <code>{product.mpn}</code><RefMark product={product}/></p>}
      </div>
      <aside className="pp-pricebox" aria-label="Resumen de precio">
        <span className="pp-price-label">{best ? 'Mejor precio' : 'Precio'}</span>
        <strong className={`pp-price ${product.min_price_cents === null ? 'price-pending' : 'price-verified'}`}>{product.min_price_cents === null ? 'Sin precio verificado' : money(product.min_price_cents)}</strong>
        {best ? <p className="pp-price-meta">en <ShopName name={best.shop_name} className="pp-shop"/> · comprobado <span title={absoluteDate(best.last_checked_at)}>{relativeAge(best.last_checked_at)}</span></p>
          : <p className="pp-price-meta">{offersLoading ? 'Buscando ofertas…' : offers.length ? 'Ninguna tienda lo tiene en stock ahora.' : 'Aún no hay ofertas verificadas.'}</p>}
        {best && !demo && <a className="primary pp-cta" href={best.url_public} target="_blank" rel="noopener noreferrer">Ver en {best.shop_name} <ArrowUpRight size={16} aria-hidden="true"/></a>}
        <p className="pp-price-meta">{shopsLabel(product.offers_count)} comparadas</p>
        <label className="pp-compare"><input type="checkbox" checked={compared} disabled={compareDisabled && !compared} onChange={onCompare}/><GitCompareArrows size={15} aria-hidden="true"/> Añadir a la comparación</label>
      </aside>
    </header>

    <section className="pp-section" id="ofertas" aria-labelledby="offers-title">
      <div className="pp-section-head"><h2 id="offers-title">Precios en tiendas</h2></div>
      <p className="detail-note">{demo ? 'Datos sintéticos. Estos enlaces son de ejemplo y no permiten comprar.' : 'Ofertas comprobadas en la fecha indicada. Envío, impuestos y precio final según destino y condiciones de la tienda.'}</p>
      <Offers product={product} offers={offers} loading={offersLoading} error={offerError} demo={demo}/>
      <PriceHistory slug={product.slug}/>
    </section>

    <WatchProduct key={product.slug} product={product}/>
    <PurchasePlanner products={[product]}/>
    <section className="pp-section" aria-labelledby="specs-title">
      <div className="pp-section-head"><h2 id="specs-title">Especificaciones</h2>
        {product.manufacturer_url && <a className="manufacturer-link" href={product.manufacturer_url} target="_blank" rel="noopener noreferrer"><BadgeCheck size={16} aria-hidden="true"/> Ficha del fabricante <ArrowUpRight size={14} aria-hidden="true"/></a>}</div>
      {product.category_id === 'gpu' && ['AMD', 'NVIDIA', 'Intel'].includes(product.brand) && product.manufacturer_url && <p className="detail-note">Modelo de referencia. Las dimensiones, refrigeración y frecuencias de cada ensamblador pueden variar.</p>}
      {groups.length ? <div className="spec-groups">{groups.map(group => <section className="spec-group" key={group.title} aria-label={group.title}>
        <h3>{group.title}</h3>
        <dl className="specs">{group.rows.map(row => <div key={row.key}><dt>{row.key}</dt><dd>{row.tokens ? <ul className="spec-tokens">{row.tokens.map(t => <li key={t}>{t}</li>)}</ul> : row.value}</dd></div>)}</dl>
      </section>)}</div> : <p className="detail-note">Este producto aún no tiene especificaciones publicadas.</p>}
      <References product={product} shops={shops}/>
    </section>

    <section className="pp-section pp-more" aria-label="Más opciones">
      <div className="retailer-links">{shops.filter(shop => ['pccomponentes', 'coolmod'].includes(shop.id)).map(shop => <a key={shop.id} href={shop.id === 'pccomponentes' ? `https://www.pccomponentes.com/search/?query=${encodeURIComponent(name)}` : `https://www.coolmod.com/#01cc/fullscreen/m=and&q=${encodeURIComponent(name)}`} target="_blank" rel="noopener noreferrer"><Store size={16} aria-hidden="true"/><span>Buscar en {shop.name}</span><ArrowUpRight size={15} aria-hidden="true"/></a>)}</div>
      <ReportButton slug={product.slug}/>
    </section>
  </article>;
}
