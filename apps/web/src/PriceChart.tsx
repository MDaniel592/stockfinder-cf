import { useEffect, useMemo, useState } from 'react';
import { money } from './format';

export interface PriceHistoryRow { offer_id: string; shop_name?: string; price_cents: number; in_stock: number | boolean; observed_at: string }
type Point = { t: number; v: number | null };
const DAY = 86400000;
const RANGES = [{ id: '30', label: '30 días', days: 30 }, { id: '90', label: '90 días', days: 90 }, { id: 'all', label: 'Todo', days: 0 }] as const;
type RangeId = typeof RANGES[number]['id'];

/** Lowest in-stock price over time (one point per change). */
export function minSeries(rows: PriceHistoryRow[]): Point[] {
  const state = new Map<string, { price: number; stock: boolean }>();
  const out: Point[] = [];
  for (const row of [...rows].sort((a, b) => Date.parse(a.observed_at) - Date.parse(b.observed_at))) {
    const t = Date.parse(row.observed_at);
    if (!Number.isFinite(t) || !Number.isFinite(row.price_cents)) continue;
    state.set(row.offer_id, { price: row.price_cents, stock: Boolean(row.in_stock) });
    let v: number | null = null;
    for (const offer of state.values()) if (offer.stock && (v === null || offer.price < v)) v = offer.price;
    const last = out[out.length - 1];
    if (last && last.t === t) last.v = v;
    else if (!last || last.v !== v) out.push({ t, v });
  }
  return out;
}
function windowed(series: Point[], days: number, now: number): Point[] {
  if (!series.length) return [];
  const cutoff = days ? now - days * DAY : series[0].t;
  const inside = series.filter(p => p.t >= cutoff);
  const before = [...series].reverse().find(p => p.t < cutoff);
  const points = before ? [{ t: cutoff, v: before.v }, ...inside] : inside;
  if (!points.length) return [];
  const end = points[points.length - 1];
  return end.t < now ? [...points, { t: now, v: end.v }] : points;
}

const W = 640; const H = 210; const L = 100; const R = 16; const T = 14; const B = 30;
export function PriceHistory({ slug }: { slug: string }) {
  const [rows, setRows] = useState<PriceHistoryRow[] | null>(null);
  const [range, setRange] = useState<RangeId>('90');
  useEffect(() => {
    const controller = new AbortController(); setRows(null);
    fetch(`/api/v1/products/${encodeURIComponent(slug)}/price-history`, { signal: controller.signal })
      .then((r): Promise<{ data?: PriceHistoryRow[] }> => r.ok ? r.json() : Promise.reject(new Error(String(r.status))))
      .then(body => { if (!controller.signal.aborted) setRows(Array.isArray(body?.data) ? body.data : []); })
      .catch(() => { if (!controller.signal.aborted) setRows([]); });
    return () => controller.abort();
  }, [slug]);
  const series = useMemo(() => minSeries(rows ?? []), [rows]);
  const now = useMemo(() => Date.now(), [rows]);
  const days = RANGES.find(r => r.id === range)!.days;
  const points = useMemo(() => windowed(series, days, now), [series, days, now]);
  if (rows === null) return <section className="price-history" aria-busy="true" aria-label="Histórico de precios"><h3>Histórico de precios</h3><div className="price-chart-placeholder" role="status">Cargando histórico…</div></section>;
  if (series.length < 2) return null;

  const values = points.map(p => p.v).filter((v): v is number => v !== null);
  const min = values.length ? Math.min(...values) : 0; const max = values.length ? Math.max(...values) : 0;
  const current = points.length ? points[points.length - 1].v : null;
  const t0 = points.length ? points[0].t : now; const t1 = points.length ? points[points.length - 1].t : now;
  const pad = max === min ? Math.max(1, min * 0.05) : (max - min) * 0.08;
  const lo = min - pad; const hi = max + pad;
  const x = (t: number) => L + (t1 === t0 ? 0 : ((t - t0) / (t1 - t0)) * (W - L - R));
  const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
  let path = ''; let pen = false;
  points.forEach((p, i) => {
    if (p.v === null) { pen = false; return; }
    if (!pen) { path += `M${x(p.t).toFixed(1)} ${y(p.v).toFixed(1)}`; pen = true; return; }
    const prev = points[i - 1];
    path += `H${x(p.t).toFixed(1)}${prev.v !== p.v ? `V${y(p.v).toFixed(1)}` : ''}`;
  });
  const label = range === 'all' ? 'Mínimo histórico' : `Mínimo en ${days} días`;
  const summary = values.length
    ? `${label}: ${money(min)}; máximo: ${money(max)}; hoy: ${current === null ? 'sin stock' : money(current)}.`
    : 'Sin precios con stock en este periodo.';
  const fmtDate = (t: number) => new Date(t).toLocaleDateString('es-ES', { day: 'numeric', month: 'short', year: range === 'all' ? '2-digit' : undefined });
  return <section className="price-history" aria-label="Histórico de precios">
    <div className="price-history-heading"><h3>Histórico de precios</h3>
      <div className="price-range" role="group" aria-label="Periodo del histórico">
        {RANGES.map(r => <button type="button" key={r.id} aria-pressed={range === r.id} onClick={() => setRange(r.id)}>{r.label}</button>)}
      </div></div>
    {values.length > 0 ? <svg className="price-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Gráfico del precio mínimo con stock. ${summary}`} preserveAspectRatio="xMidYMid meet">
      <line className="price-chart-grid" x1={L} x2={W - R} y1={y(max)} y2={y(max)}/>
      <line className="price-chart-grid" x1={L} x2={W - R} y1={y(min)} y2={y(min)}/>
      <text className="price-chart-label" x={L - 8} y={y(max) + 4} textAnchor="end">{money(max)}</text>
      {min !== max && <text className="price-chart-label" x={L - 8} y={y(min) + 4} textAnchor="end">{money(min)}</text>}
      <path className="price-chart-line" d={path} fill="none"/>
      {current !== null && <circle className="price-chart-dot" cx={x(t1)} cy={y(current)} r="4"/>}
      <text className="price-chart-axis" x={L} y={H - 8}>{fmtDate(t0)}</text>
      <text className="price-chart-axis" x={W - R} y={H - 8} textAnchor="end">hoy</text>
    </svg> : <div className="price-chart-placeholder">Sin precios con stock en este periodo.</div>}
    <p className="price-history-summary" role="status">{summary}</p>
    <ul className="price-history-legend" aria-hidden="true">
      {values.length > 0 && <><li>Mín. <strong>{money(min)}</strong></li><li>Máx. <strong>{money(max)}</strong></li><li>Actual <strong>{current === null ? 'sin stock' : money(current)}</strong></li></>}
    </ul>
  </section>;
}
