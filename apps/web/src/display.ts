// Presentation helpers: user-facing product names, key specs for cards and grouped specs for the product page.
// Pure functions (no React) so they can be unit-tested and shared by the catalogue, product page, compare and builder.
import type { Product } from '../../../packages/api-contracts/index';

type Named = Pick<Product, 'name'> & { brand?: string | null; mpn?: string | null; retailer_codes?: Array<{ shop_id: string; code: string }> | null };
const norm = (value: string) => value.toLowerCase().replace(/[\s_-]+/g, '');

/** Product name without a trailing "(CODE)" when CODE is the product's MPN or one of its retailer codes. Data is untouched. */
export function displayName(product: Named): string {
  const name = product.name.trim();
  const match = name.match(/^(.*\S)\s*\(([^()]{3,60})\)$/);
  if (!match) return name;
  const code = norm(match[2]);
  const codes = [product.mpn, ...(product.retailer_codes ?? []).map(c => c.code)].filter((c): c is string => !!c).map(norm);
  return codes.includes(code) ? match[1] : name;
}

/** True when the visible name already contains the brand (then a separate brand label is redundant). */
export const nameStartsWithBrand = (product: Named) => !!product.brand && displayName(product).toLowerCase().includes(product.brand.toLowerCase());

// Canonical Spanish spec keys (see artifacts/shards/PROTOCOL-w8.md, "Convenciones de especificaciones").
const KEY_SPECS: Record<string, string[]> = {
  cpu: ['Socket', 'Núcleos', 'Hilos', 'Frecuencia turbo', 'TDP'],
  gpu: ['Chip gráfico', 'Memoria', 'Tipo de memoria', 'Consumo (TBP)'],
  motherboard: ['Socket', 'Chipset', 'Formato', 'Tipo de memoria'],
  ram: ['Capacidad', 'Tipo de memoria', 'Velocidad', 'Latencia'],
  storage: ['Capacidad', 'Interfaz', 'Formato', 'Lectura secuencial'],
  psu: ['Potencia', 'Eficiencia 80 PLUS', 'Modular', 'Formato'],
  case: ['Formatos compatibles', 'Longitud GPU máxima', 'Panel lateral'],
  cooler: ['Tipo', 'Radiador', 'Altura', 'Ventiladores']
};
const UNIT_LABEL: Record<string, (v: string) => string> = {
  'Núcleos': v => /^\d+$/.test(v) ? `${v} núcleos` : v,
  'Hilos': v => /^\d+$/.test(v) ? `${v} hilos` : v,
  'Heatpipes': v => /^\d+$/.test(v) ? `${v} heatpipes` : v,
  'Ranuras de memoria': v => /^\d+$/.test(v) ? `${v} ranuras` : v,
  'Longitud GPU máxima': v => `GPU ≤ ${v}`,
  'Altura': v => `${v} de alto`,
  'TDP': v => /tdp/i.test(v) ? v : `TDP ${v}`
};
const text = (value: unknown) => typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : '';

export type SpecChip = { key: string; label: string; text: string; value: string };
/** Up to `max` short, category-relevant spec chips for cards (falls back to the first non-empty specs). */
export function keySpecs(product: Pick<Product, 'category_id' | 'specs'>, max = 4): SpecChip[] {
  const specs = product.specs ?? {};
  const wanted = KEY_SPECS[product.category_id] ?? [];
  const keys = [...wanted.filter(k => text(specs[k])), ...(wanted.some(k => text(specs[k])) ? [] : Object.keys(specs).filter(k => text(specs[k])))];
  const out: SpecChip[] = [];
  for (const key of keys) {
    const value = text(specs[key]);
    // Skip a value already contained in a previous chip (e.g. "GDDR7" after "8 GB GDDR7").
    if (out.some(chip => chip.text.toLowerCase().includes(value.toLowerCase()))) continue;
    out.push({ key, label: key, value, text: (UNIT_LABEL[key] ?? (v => v))(value) });
    if (out.length >= max) break;
  }
  return out;
}

/** Spec columns of the catalogue table, per category. Each column reads one or more canonical keys. */
export type SpecColumn = { id: string; label: string; value: (specs: Record<string, string>) => string | null };
const pick = (...keys: string[]) => (specs: Record<string, string>) => { for (const k of keys) { const v = text(specs?.[k]); if (v) return v; } return null; };
const joinKeys = (sep: string, ...keys: string[]) => (specs: Record<string, string>) => { const vs = keys.map(k => text(specs?.[k])).filter(Boolean); return vs.length ? vs.join(sep) : null; };
const memory = (specs: Record<string, string>) => {
  const size = text(specs?.['Memoria']); const kind = text(specs?.['Tipo de memoria']);
  if (!size) return kind || null;
  return kind && !size.toLowerCase().includes(kind.toLowerCase()) ? `${size} ${kind}` : size;
};
const coresThreads = (specs: Record<string, string>) => {
  const c = text(specs?.['Núcleos']); const t = text(specs?.['Hilos']);
  return c && t ? `${c} / ${t}` : c || t || null;
};
const COLUMNS: Record<string, SpecColumn[]> = {
  gpu: [{ id: 'chip', label: 'Chip', value: pick('Chip gráfico', 'Modelo') }, { id: 'mem', label: 'Memoria', value: memory },
    { id: 'bus', label: 'Bus / longitud', value: joinKeys(' · ', 'Bus de memoria', 'Longitud') }, { id: 'tbp', label: 'Consumo', value: pick('Consumo (TBP)') }],
  cpu: [{ id: 'ct', label: 'Núcleos / hilos', value: coresThreads }, { id: 'turbo', label: 'Turbo', value: pick('Frecuencia turbo', 'Frecuencia turbo máxima') },
    { id: 'socket', label: 'Socket', value: pick('Socket') }, { id: 'tdp', label: 'TDP', value: pick('TDP', 'Potencia base') }],
  motherboard: [{ id: 'socket', label: 'Socket', value: pick('Socket') }, { id: 'chipset', label: 'Chipset', value: pick('Chipset') },
    { id: 'form', label: 'Formato', value: pick('Formato') }, { id: 'mem', label: 'Memoria', value: pick('Tipo de memoria') }],
  ram: [{ id: 'cap', label: 'Capacidad', value: pick('Capacidad') }, { id: 'speed', label: 'Velocidad', value: joinKeys(' ', 'Tipo de memoria', 'Velocidad') },
    { id: 'cl', label: 'Latencia', value: pick('Latencia') }, { id: 'mod', label: 'Módulos', value: pick('Módulos', 'Configuración') }],
  storage: [{ id: 'cap', label: 'Capacidad', value: pick('Capacidad') }, { id: 'if', label: 'Interfaz', value: joinKeys(' · ', 'Formato', 'Interfaz') },
    { id: 'read', label: 'Lectura', value: pick('Lectura secuencial') }],
  psu: [{ id: 'w', label: 'Potencia', value: pick('Potencia') }, { id: 'eff', label: 'Eficiencia', value: pick('Eficiencia 80 PLUS', 'Cybenetics') },
    { id: 'mod', label: 'Modular', value: pick('Modular') }, { id: 'atx', label: 'ATX', value: pick('Estándar ATX', 'Formato') }],
  case: [{ id: 'mb', label: 'Placas', value: pick('Formatos compatibles') }, { id: 'gpu', label: 'GPU máx.', value: pick('Longitud GPU máxima') },
    { id: 'panel', label: 'Panel lateral', value: pick('Panel lateral') }],
  cooler: [{ id: 'type', label: 'Tipo', value: pick('Tipo') }, { id: 'size', label: 'Radiador / altura', value: pick('Radiador', 'Altura') },
    { id: 'sockets', label: 'Sockets', value: pick('Sockets compatibles') }]
};
export const tableColumns = (category: string): SpecColumn[] => COLUMNS[category] ?? [];

const GROUPS: Record<string, Array<[string, string[]]>> = {
  cpu: [
    ['Núcleos y frecuencias', ['Núcleos', 'Hilos', 'Distribución de núcleos', 'Frecuencia base', 'Frecuencia turbo', 'Frecuencia turbo máxima', 'Caché L2', 'Caché L3', 'Arquitectura']],
    ['Plataforma y memoria', ['Socket', 'Memoria compatible', 'Memoria', 'Memoria máxima', 'Tipo de módulo requerido', 'Interfaz', 'Gráficos integrados', 'GPU integrada', 'Gráfica integrada']],
    ['Consumo y refrigeración', ['TDP', 'Potencia base', 'Potencia máxima turbo', 'Disipador incluido', 'Modelo disipador', 'Refrigeración incluida']],
    ['Presentación', ['Presentación']]],
  gpu: [
    ['Chip', ['Chip gráfico', 'Modelo', 'Arquitectura', 'Unidades de cómputo', 'Procesadores de flujo', 'Frecuencia base', 'Frecuencia de juego', 'Frecuencia turbo']],
    ['Memoria', ['Memoria', 'Tipo de memoria', 'Bus de memoria', 'Velocidad de memoria']],
    ['Conectividad', ['Interfaz', 'Enlace PCIe', 'Salidas']],
    ['Consumo y alimentación', ['Consumo (TBP)', 'Fuente recomendada', 'Conectores de alimentación']],
    ['Dimensiones y refrigeración', ['Dimensiones', 'Longitud', 'Ranuras', 'Formato', 'Refrigeración']]],
  motherboard: [
    ['Plataforma', ['Socket', 'Chipset', 'Formato', 'Dimensiones']],
    ['Memoria', ['Tipo de memoria', 'Ranuras de memoria', 'Memoria máxima', 'Velocidad de memoria']],
    ['Expansión y almacenamiento', ['PCIe principal', 'Ranuras PCIe', 'Ranuras M.2', 'M.2', 'SATA']],
    ['Conectividad', ['Wi-Fi', 'Bluetooth', 'Red', 'USB', 'USB traseros', 'Audio']],
    ['Aspecto', ['Color', 'Iluminación']]],
  ram: [
    ['Kit', ['Capacidad', 'Módulos', 'Configuración', 'Formato', 'Línea']],
    ['Rendimiento', ['Tipo de memoria', 'Velocidad', 'Latencia', 'Timings', 'Voltaje', 'Perfil']],
    ['Aspecto', ['Iluminación', 'Color', 'Disipador']]],
  storage: [
    ['Capacidad y formato', ['Capacidad', 'Formato', 'Interfaz', 'Disipador integrado', 'Disipador']],
    ['Rendimiento', ['Lectura secuencial', 'Escritura secuencial', 'Lectura aleatoria', 'Escritura aleatoria']],
    ['Componentes y durabilidad', ['Controlador', 'Tipo de NAND', 'Caché', 'Resistencia', 'Garantía']]],
  psu: [
    ['Potencia y eficiencia', ['Potencia', 'Eficiencia 80 PLUS', 'Cybenetics', 'Cybenetics ruido', 'Estándar ATX']],
    ['Cableado', ['Modular', 'Conector GPU', 'Conectores']],
    ['Formato y ventilación', ['Formato', 'Dimensiones', 'Longitud', 'Ventilador', 'Color']]],
  case: [
    ['Compatibilidad', ['Formatos compatibles', 'Formato fuente', 'Longitud GPU máxima', 'Altura disipador máxima', 'Ranuras PCIe']],
    ['Refrigeración', ['Ventiladores incluidos', 'Capacidad de ventiladores', 'Radiador frontal máximo', 'Radiador superior máximo', 'Radiador superior/inferior máximo']],
    ['Diseño', ['Panel lateral', 'Panel frontal', 'Materiales', 'Color', 'Dimensiones', 'E/S frontal', 'USB frontal', 'Bahías']]],
  cooler: [
    ['Tipo y compatibilidad', ['Tipo', 'Diseño', 'Sockets compatibles', 'TDP']],
    ['Ventiladores', ['Ventiladores', 'Detalle ventiladores', 'Velocidad de ventiladores', 'Nivel de ruido']],
    ['Radiador y disipador', ['Radiador', 'Detalle radiador', 'Dimensiones radiador', 'Material del radiador', 'Placa fría', 'Longitud de tubos', 'Velocidad de bomba', 'Heatpipes', 'Altura']],
    ['Diseño', ['Color', 'Iluminación', 'Pantalla', 'Dimensiones']]]
};
/** Keys whose values are lists: rendered as tokens. */
const LIST_KEYS = new Set(['Formatos compatibles', 'Sockets compatibles', 'Perfil', 'Conector GPU', 'Salidas', 'Memoria compatible', 'Conectores de alimentación', 'E/S frontal']);

export type SpecRow = { key: string; value: string; tokens: string[] | null };
export type SpecGroup = { title: string; rows: SpecRow[] };
export function splitList(key: string, value: string): string[] | null {
  const parts = value.split(/\s*[,;]\s*|\s+\/\s+/).map(p => p.trim()).filter(Boolean);
  if (parts.length < 2) return null;
  if (LIST_KEYS.has(key) || (parts.length >= 3 && parts.every(p => p.length <= 18))) return parts;
  return null;
}
/** Specs grouped by category with a graceful "Otras características" group for unknown keys. Empty groups are dropped. */
export function groupSpecs(product: Pick<Product, 'category_id' | 'specs'>): SpecGroup[] {
  const specs = Object.entries(product.specs ?? {}).map(([k, v]) => [k, text(v)] as const).filter(([, v]) => v);
  const used = new Set<string>();
  const row = (key: string, value: string): SpecRow => ({ key, value, tokens: splitList(key, value) });
  const groups: SpecGroup[] = (GROUPS[product.category_id] ?? []).map(([title, keys]) => ({ title,
    rows: keys.flatMap(key => { const hit = specs.find(([k]) => k === key); if (!hit) return []; used.add(key); return [row(key, hit[1])]; }) }));
  const rest = specs.filter(([k]) => !used.has(k)).map(([k, v]) => row(k, v));
  if (rest.length) groups.push({ title: groups.length ? 'Otras características' : 'Características', rows: rest });
  return groups.filter(g => g.rows.length);
}
/** Spec keys in display order for a set of products (used by the comparison table). */
export function orderedSpecKeys(products: Pick<Product, 'category_id' | 'specs'>[]): string[] {
  const keys: string[] = [];
  for (const p of products) for (const g of groupSpecs(p)) for (const r of g.rows) if (!keys.includes(r.key)) keys.push(r.key);
  return keys;
}
