/** Only published specifications are offered as category filters. */
export const categorySpecFilters: Record<string, readonly string[]> = {
  // No filter on 'Memoria compatible' (the socket implies the platform) nor 'Potencia base' (Intel-only, sparse; kept in the detail).
  cpu: ['Socket', 'Núcleos', 'Gráficos integrados', 'Disipador incluido', 'TDP'],
  gpu: ['Chip gráfico', 'Memoria', 'Tipo de memoria'],
  motherboard: ['Socket', 'Chipset', 'Tipo de memoria', 'Formato', 'Ranuras de memoria'],
  ram: ['Tipo de memoria', 'Capacidad', 'Velocidad', 'Latencia', 'Formato', 'Perfil'],
  storage: ['Tipo', 'Capacidad', 'Interfaz', 'Formato', 'Disipador integrado'],
  psu: ['Potencia', 'Formato', 'Eficiencia 80 PLUS', 'Cybenetics', 'Modular', 'Estándar ATX'],
  case: ['Formatos compatibles', 'Panel lateral', 'Formato fuente'],
  cooler: ['Tipo', 'Sockets compatibles', 'Radiador', 'Ventiladores'],
};

/**
 * List-valued specs. The stored value is a canonical ", "-separated list (normalized at import by nas/spec_normalize.py);
 * facets list individual tokens and a selected token matches every product whose list CONTAINS it.
 * Several tokens of the same key combine with OR ("supports any of these"); different keys combine with AND.
 * The array is the display order of the facet options; tokens not listed follow alphabetically.
 */
const formFactors = ['EE-ATX', 'SSI-EEB', 'SSI-CEB', 'E-ATX', 'ATX', 'Micro-ATX', 'Mini-ITX'] as const;
export const multiValueSpecs: Record<string, Record<string, readonly string[]>> = {
  case: { 'Formatos compatibles': formFactors, 'Formato fuente': ['ATX', 'SFX-L', 'SFX'] },
  cooler: { 'Sockets compatibles': [
    'AM5', 'AM4', 'AM3+', 'AM3', 'AM2+', 'AM2', 'FM2+', 'FM2', 'FM1', 'TR5', 'TR4',
    'LGA1851', 'LGA1700', 'LGA1200', 'LGA2066', 'LGA1151', 'LGA2011-v3', 'LGA1150', 'LGA2011', 'LGA1155', 'LGA1156', 'LGA1366', 'LGA115x'] },
  ram: { Perfil: ['AMD EXPO', 'AMD EXPO 1.1', 'Intel XMP', 'Intel XMP 3.0', 'Intel XMP 2.0'] },
};
/** Display order of single-valued facets with a natural ranking (weakest first); other values follow alphabetically. */
export const specValueOrder: Record<string, Record<string, readonly string[]>> = {
  psu: {
    'Eficiencia 80 PLUS': ['80 PLUS', '80 PLUS Bronze', '80 PLUS Silver', '80 PLUS Gold', '80 PLUS Platinum', '80 PLUS Titanium'],
    Cybenetics: ['Cybenetics Bronze', 'Cybenetics Silver', 'Cybenetics Gold', 'Cybenetics Platinum', 'Cybenetics Titanium', 'Cybenetics Diamond'],
    Modular: ['Cables fijos', 'Semimodular', 'Totalmente modular'],
  },
};
const unitScale: Record<string, [string, number]> = { tb: ['size', 1000], gb: ['size', 1], mb: ['size', 0.001], ghz: ['freq', 1000], mhz: ['freq', 1],
  'mt/s': ['speed', 1], w: ['power', 1], mm: ['length', 1], cm: ['length', 10] };
/** Order of single-valued facet options: numeric-like values ("10", "500 GB" < "1 TB", "CL16", "65 W") compare by magnitude, the rest alphabetically. */
export const compareFacetValues = (a: string, b: string) => {
  const parse = (value: string) => {
    const m = /^(?:CL)?(\d+(?:\.\d+)?)\s*([A-Za-z/]+)?$/.exec(value.trim());
    if (!m) return null;
    const unit = m[2] ? unitScale[m[2].toLowerCase()] : ['none', 1] as [string, number];
    return unit ? { family: unit[0], magnitude: Number(m[1]) * unit[1] } : null;
  };
  const x = parse(a); const y = parse(b);
  if (x && y && x.family === y.family && x.magnitude !== y.magnitude) return x.magnitude - y.magnitude;
  return compareText(a, b);
};
let collator: Intl.Collator | undefined;
/** Same order as `a.localeCompare(b, 'es', { numeric: true })`, with one ICU collator per isolate instead of one per call. */
export const compareText = (a: string, b: string) => (collator ??= new Intl.Collator('es', { numeric: true })).compare(a, b);
export const isMultiValueSpec = (category: string, key: string) => Object.hasOwn(multiValueSpecs, category) && Object.hasOwn(multiValueSpecs[category], key);
/** Splits a stored list into its tokens (comma, slash or semicolon separated). */
export const specTokens = (value: string) => [...new Set(value.split(/\s*[,;/]\s*/).map(token => token.trim()).filter(Boolean))];

export interface CatalogFilters {
  brands: { value: string; count: number }[];
  shops?: { id: string; name: string; count: number }[];
  specs: { key: string; multi?: boolean; options: { value: string; count: number }[] }[];
}
