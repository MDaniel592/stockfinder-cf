export const money = (cents: number | null) => cents === null ? 'Sin precio verificado' : new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' }).format(cents / 100);
const HOUR = 3600000; const DAY = 24 * HOUR;
export function ageMs(value: string | null | undefined, now = Date.now()): number | null {
  const t = value ? Date.parse(value) : NaN;
  return Number.isFinite(t) ? Math.max(0, now - t) : null;
}
/** "hace 5 min", "hace 3 h", "hace 4 días" */
export function relativeAge(value: string | null | undefined, now = Date.now()): string {
  const age = ageMs(value, now);
  if (age === null) return 'en una fecha desconocida';
  const minutes = Math.floor(age / 60000);
  if (minutes < 1) return 'hace un momento';
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `hace ${hours} h`;
  return `hace ${Math.floor(age / DAY)} días`;
}
export const absoluteDate = (value: string | null | undefined) => {
  const t = value ? Date.parse(value) : NaN;
  return Number.isFinite(t) ? new Date(t).toLocaleString('es-ES', { dateStyle: 'long', timeStyle: 'short' }) : 'Fecha desconocida';
};
export type Freshness = { level: 'fresh' | 'stale' | 'unverified'; label: string | null };
/** <48 h normal; >=48 h "Desactualizado"; >=7 days "Sin verificar hace N días". */
export function offerFreshness(value: string | null | undefined, now = Date.now()): Freshness {
  const age = ageMs(value, now);
  if (age === null) return { level: 'unverified', label: 'Sin verificar' };
  if (age >= 7 * DAY) return { level: 'unverified', label: `Sin verificar hace ${Math.floor(age / DAY)} días` };
  if (age >= 48 * HOUR) return { level: 'stale', label: 'Desactualizado' };
  return { level: 'fresh', label: null };
}
