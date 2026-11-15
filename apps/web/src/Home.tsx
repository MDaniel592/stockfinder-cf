import { useEffect, useState, type ReactNode } from 'react';
import type { CatalogStatus } from '../../../packages/api-contracts/index';
import { ArrowRight, BadgeCheck, Clock3, Fingerprint, Search, TrendingDown, Wrench } from 'lucide-react';
import { Link } from './Link';
import { money, relativeAge } from './format';
import { categoryPath, productPath } from './router';
import { displayName, keySpecs } from './display';
import { Thumb, type Item } from './ProductBits';

type Category = { id: string; name: string; products: number; in_stock: number; min_price_cents: number | null };
export type Drop = Omit<Item, 'min_price_cents'> & { min_price_cents: number; previous_price_cents: number; shop_name: string; dropped_at: string };
type Highlights = { categories: Category[]; drops: Drop[] };

const SUGGESTIONS: Array<[string, string]> = [['RTX 5070', '/c/gpu?q=RTX+5070'], ['RX 9070 XT', '/c/gpu?q=9070+XT'], ['Ryzen 7 9800X3D', '/c/cpu?q=9800X3D'],
  ['DDR5 32 GB', '/c/ram?spec.Tipo+de+memoria=DDR5&spec.Capacidad=32+GB'], ['SSD 2 TB', '/c/storage?spec.Capacidad=2+TB'], ['Fuente 850 W', '/c/psu?spec.Potencia=850+W']];

const pct = (before: number, after: number) => Math.round(((before - after) / before) * 100);

export function HomePage({ search, categories, status, freshness }: {
  search: ReactNode; categories: { id: string; name: string }[]; status: CatalogStatus | null; freshness: string;
}) {
  const [data, setData] = useState<Highlights | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetch('/api/v1/catalog/highlights', { signal: controller.signal })
      .then((r): Promise<{ data?: Highlights }> => r.ok ? r.json() : Promise.reject(new Error(String(r.status))))
      .then((body: { data?: Highlights }) => { if (body?.data) setData(body.data); else setFailed(true); })
      .catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, []);
  const stats = new Map((data?.categories ?? []).map(c => [c.id, c]));
  const totalProducts = data?.categories.reduce((n, c) => n + c.products, 0) ?? 0;
  return <div className="home">
    <section className="home-hero" aria-labelledby="home-title">
      <h1 id="home-title">Compara precios de componentes de PC</h1>
      <p className="home-lead">Especificaciones oficiales y precios comprobados en tiendas españolas{totalProducts ? <>: <strong>{totalProducts.toLocaleString('es-ES')}</strong> componentes</> : ''}.</p>
      {search}
      <nav className="home-suggestions" aria-label="Búsquedas populares"><span>Populares:</span>{SUGGESTIONS.map(([label, href]) => <Link key={label} href={href}>{label}</Link>)}</nav>
    </section>

    <section className="home-section" aria-labelledby="home-categories">
      <div className="home-section-head"><h2 id="home-categories">Categorías</h2><Link className="text-link" href="/?stock=1">Todo lo que está en stock <ArrowRight size={14} aria-hidden="true"/></Link></div>
      <ul className="category-tiles">{categories.map(c => { const s = stats.get(c.id);
        return <li key={c.id}><Link href={categoryPath(c.id)} className="category-tile">
          <span className="category-tile-media"><img src={`/images/cat/${c.id}.webp`} alt="" loading="lazy" decoding="async" width="260" height="180" onError={e => { e.currentTarget.style.display = "none"; }}/></span>
          <span className="category-tile-name">{c.name}</span>
          <span className="category-tile-meta">{s ? <>{s.products.toLocaleString('es-ES')} modelos{s.min_price_cents !== null && <> · desde <strong>{money(s.min_price_cents)}</strong></>}</> : failed ? 'Ver modelos' : ' '}</span>
        </Link></li>; })}</ul>
    </section>

    {/* Reserve the drops block while highlights load so the sections below do not jump (CLS). */}
    {!data && !failed && <section className="home-section" aria-hidden="true">
      <div className="home-section-head"><h2><TrendingDown size={20} aria-hidden="true"/> Bajadas de precio recientes</h2></div>
      <ul className="drop-list">{Array.from({ length: 8 }, (_, i) => <li key={i} className="drop-card drop-skeleton"><span className="skeleton"/></li>)}</ul>
    </section>}
    {data && data.drops.length > 0 && <section className="home-section" aria-labelledby="home-drops">
      <div className="home-section-head"><h2 id="home-drops"><TrendingDown size={20} aria-hidden="true"/> Bajadas de precio recientes</h2><span className="home-section-note">Últimas 24 horas · mejor precio actual</span></div>
      <DropCards drops={data.drops}/>
    </section>}

    <p className="home-section"><Link className="text-link" href="/ofertas">Mostrar más ofertas <ArrowRight size={14} aria-hidden="true"/></Link></p>

    <section className="home-section home-trust" aria-labelledby="home-trust">
      <h2 id="home-trust" className="sr-only">Por qué fiarte de estos datos</h2>
      <ul>
        <li><BadgeCheck size={22} aria-hidden="true"/><div><strong>Fichas oficiales</strong><p>Las especificaciones salen de la web de cada fabricante y cada producto enlaza a su ficha.</p></div></li>
        <li><Fingerprint size={22} aria-hidden="true"/><div><strong>Emparejado por referencia</strong><p>Una oferta solo se asocia a un producto si su referencia (MPN) o EAN coincide exactamente.</p></div></li>
        <li><Clock3 size={22} aria-hidden="true"/><div><strong>Precios con fecha</strong><p>{freshness}. Cada oferta indica cuándo se comprobó.</p></div></li>
      </ul>
      <Link className="text-link" href="/acerca">Cómo funciona StockFinder <ArrowRight size={14} aria-hidden="true"/></Link>
    </section>

    <section className="home-builder" aria-labelledby="home-builder">
      <div><h2 id="home-builder"><Wrench size={20} aria-hidden="true"/> Monta tu PC</h2><p>Elige pieza a pieza, comprueba la compatibilidad básica y sigue el precio total de tu configuración.</p></div>
      <Link className="primary" href="/configurador">Abrir el configurador <ArrowRight size={16} aria-hidden="true"/></Link>
    </section>
    <p className="home-search-hint"><Search size={14} aria-hidden="true"/> Pulsa <kbd>/</kbd> en cualquier página para buscar.</p>
  </div>;
}

export function DropCards({drops}:{drops:Drop[]}) { return (<ul className="drop-list">{drops.map(d => { const chips = keySpecs(d, 3);
        return <li key={d.id} className="drop-card">
          <Thumb product={d} size="row"/>
          <div className="drop-body">
            <Link className="product-name" href={productPath(d.slug)}>{displayName(d)}</Link>
            {chips.length > 0 && <p className="spec-line">{chips.map(c => <span key={c.key} title={c.label}>{c.text}</span>)}</p>}
          </div>
          <div className="drop-price">
            <span className="drop-badge">−{pct(d.previous_price_cents, d.min_price_cents)} %</span>
            <strong className="price-verified">{money(d.min_price_cents)}</strong>
            <s aria-label={`Antes ${money(d.previous_price_cents)}`}>{money(d.previous_price_cents)}</s>
            <small>{d.shop_name} · {relativeAge(d.dropped_at)}</small>
          </div>
        </li>; })}</ul>); }
