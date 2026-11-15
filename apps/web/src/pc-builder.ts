import type { Product } from '../../../packages/api-contracts/index';

export type BuildSlot = 'cpu' | 'motherboard' | 'ram' | 'gpu' | 'storage' | 'psu' | 'case' | 'cooler';
export type BuildParts = Partial<Record<BuildSlot, Product>>;
export interface CompatibilityCheck {
  code: string;
  severity: 'error' | 'warning' | 'pass';
  message: string;
  slots: BuildSlot[];
}
export interface PriceSnapshot {
  id: string;
  created_at: string;
  items: { slot: BuildSlot; product_id: string; name: string; price_cents: number | null; last_checked_at: string | null }[];
  total_known_cents: number;
  priced_items: number;
  total_items: number;
}

const slots: BuildSlot[] = ['cpu', 'motherboard', 'ram', 'gpu', 'storage', 'psu', 'case', 'cooler'];
const labels: Record<BuildSlot, string> = {
  cpu: 'procesador', motherboard: 'placa base', ram: 'memoria RAM', gpu: 'tarjeta gráfica',
  storage: 'almacenamiento', psu: 'fuente', case: 'caja', cooler: 'refrigeración'
};
const spec = (part: Product | undefined, ...keys: string[]) => {
  for (const key of keys) {
    const value = part?.specs[key]?.trim();
    if (value && !/^(?:desconocido|sin datos|no especificado|n\/?a|—|-)$/i.test(value)) return value;
  }
  return undefined;
};
const socketList = (value?: string): string[] => {
  if (!value) return [];
  return [...new Set((value.toUpperCase().match(/(?:FC)?LGA\s*\d{3,5}|\bAM[345]\+?\b|\bTR[45]\b|\bSTRX[45]0\b/gi) ?? [])
    .map(socket => socket.replace(/\s/g, '').replace(/^FC/, '')))];
};
const memoryTypes = (value?: string): string[] => [...new Set(value?.toUpperCase().match(/\bDDR[345]\b/g) ?? [])];
const overlap = (first: string[], second: string[]) => first.some(value => second.includes(value));
function formats(value?: string): string[] {
  if (!value) return [];
  const matches = value.toUpperCase().match(/\bE[-\s]?ATX\b|\bMICRO[-\s]?ATX\b|\bM[-\s]?ATX\b|\bMINI[-\s]?ITX\b|\bATX\b|\bITX\b/g) ?? [];
  return [...new Set(matches.map(format => {
    const clean = format.replace(/[-\s]/g, '');
    if (clean === 'MATX' || clean === 'MICROATX') return 'MICROATX';
    if (clean === 'ITX' || clean === 'MINIITX') return 'MINIITX';
    return clean;
  }))];
}
function dimension(value?: string): number | undefined {
  const match = value?.match(/\b(\d+(?:[.,]\d+)?)\s*mm\b/i);
  const number = match ? Number(match[1].replace(',', '.')) : NaN;
  return Number.isFinite(number) && number > 0 ? number : undefined;
}
function watts(value?: string): number | undefined {
  if (!value) return undefined;
  const match = value.match(/\b(\d[\d.,]*)\s*(?:W|vatios)\b/i) ?? value.match(/^\s*(\d+(?:[.,]\d+)?)\s*$/);
  if (!match) return undefined;
  const normalized = match[1].replace(/[.,](?=\d{3}(?:[.,]|$))/g, '').replace(',', '.');
  const number = Number(normalized);
  return Number.isFinite(number) && number > 0 ? number : undefined;
}

export interface SlotFilter {
  /** Options compatible with the parts already chosen. */
  options: Product[];
  /** Short description of the active filter, e.g. "socket AM5". */
  label: string;
  /** Options left out (incompatible or without the data needed to check them). */
  hidden: number;
}

/**
 * Narrows the options of a slot to the ones compatible with the parts already chosen:
 * motherboards by CPU socket, RAM by DDR generation (board first, CPU otherwise).
 * Returns null when no filter applies (nothing chosen yet or the chosen part lacks the data).
 */
export function filterCompatibleOptions(slot: BuildSlot, options: Product[], parts: BuildParts): SlotFilter | null {
  let wanted: string[] = []; let read: (option: Product) => string[]; let label = '';
  if (slot === 'motherboard' && parts.cpu) {
    wanted = socketList(spec(parts.cpu, 'Socket'));
    read = option => socketList(spec(option, 'Socket'));
    label = `socket ${wanted.join('/')}`;
  } else if (slot === 'ram' && (parts.motherboard || parts.cpu)) {
    const board = parts.motherboard ? memoryTypes(spec(parts.motherboard, 'Tipo de memoria', 'Memoria')) : [];
    const cpu = parts.cpu ? memoryTypes(spec(parts.cpu, 'Memoria compatible', 'Tipo de memoria')) : [];
    wanted = board.length ? board : cpu;
    read = option => memoryTypes(spec(option, 'Tipo de memoria', 'Memoria'));
    label = `${wanted.join('/')}${board.length ? ' (placa base)' : ' (procesador)'}`;
  } else return null;
  if (!wanted.length) return null;
  const compatible = options.filter(option => overlap(read(option), wanted));
  return { options: compatible, label, hidden: options.length - compatible.length };
}

/** Same constraints as the compatibility picker, applied on the server before pagination. */
export function builderSpecFilters(slot: BuildSlot, parts: BuildParts): Record<string, string[]> {
  if (slot === 'motherboard') {
    const sockets = socketList(spec(parts.cpu, 'Socket'));
    return sockets.length ? { Socket: sockets } : {};
  }
  if (slot === 'ram') {
    const board = memoryTypes(spec(parts.motherboard, 'Tipo de memoria', 'Memoria'));
    const types = board.length ? board : memoryTypes(spec(parts.cpu, 'Memoria compatible', 'Tipo de memoria'));
    return types.length ? { 'Tipo de memoria': types } : {};
  }
  return {};
}

/** A list of specific checks, never a guarantee that a complete system will work. */
export function checkCompatibility(parts: BuildParts): CompatibilityCheck[] {
  const checks: CompatibilityCheck[] = [];
  const add = (code: string, severity: CompatibilityCheck['severity'], message: string, involved: BuildSlot[]) => checks.push({ code, severity, message, slots: involved });
  const { cpu, motherboard, ram, gpu, storage, psu, case: enclosure, cooler } = parts;
  const required: BuildSlot[] = ['cpu', 'motherboard', 'ram', 'storage', 'psu', 'case'];
  const missing = required.filter(slot => !parts[slot]);
  if (missing.length) add('missing-parts', 'warning', `Configuración incompleta: falta ${missing.map(slot => labels[slot]).join(', ')}.`, missing);
  for (const slot of slots) {
    if (parts[slot] && parts[slot]!.category_id !== slot) add(`wrong-category-${slot}`, 'error', `El componente elegido para ${labels[slot]} pertenece a otra categoría.`, [slot]);
  }

  if (cpu && motherboard) {
    const cpuSockets = socketList(spec(cpu, 'Socket'));
    const boardSockets = socketList(spec(motherboard, 'Socket'));
    if (!cpuSockets.length || !boardSockets.length) add('socket-unknown', 'warning', 'No hay datos suficientes para comprobar el socket del procesador y la placa.', ['cpu', 'motherboard']);
    else if (!overlap(cpuSockets, boardSockets)) add('socket-mismatch', 'error', `Socket incompatible: CPU ${cpuSockets.join('/')} y placa ${boardSockets.join('/')}.`, ['cpu', 'motherboard']);
    else add('socket-match', 'pass', `El socket ${cpuSockets.join('/')} coincide; falta comprobar la lista de CPU soportadas y la BIOS.`, ['cpu', 'motherboard']);
    add('bios-unverified', 'warning', 'BIOS sin verificar: comprueba la revisión exacta de la placa, la lista de CPU soportadas y la versión de BIOS requerida.', ['cpu', 'motherboard']);
  }
  if (motherboard && ram) {
    const boardMemory = memoryTypes(spec(motherboard, 'Tipo de memoria', 'Memoria'));
    const ramMemory = memoryTypes(spec(ram, 'Tipo de memoria', 'Memoria'));
    if (!boardMemory.length || !ramMemory.length) add('memory-unknown', 'warning', 'No se conoce el tipo DDR de la placa o la RAM; no se puede confirmar su compatibilidad.', ['motherboard', 'ram']);
    else if (!overlap(boardMemory, ramMemory)) add('memory-mismatch', 'error', `Memoria incompatible: placa ${boardMemory.join('/')} y módulos ${ramMemory.join('/')}.`, ['motherboard', 'ram']);
    else add('memory-match', 'pass', `El tipo ${ramMemory.join('/')} coincide con la placa. Velocidad, capacidad, formato de módulo y QVL aún requieren revisión.`, ['motherboard', 'ram']);
  }
  if (cpu && ram) {
    const cpuMemory = memoryTypes(spec(cpu, 'Memoria compatible', 'Tipo de memoria'));
    const ramMemory = memoryTypes(spec(ram, 'Tipo de memoria', 'Memoria'));
    if (!cpuMemory.length || !ramMemory.length) add('cpu-memory-unknown', 'warning', 'No hay información suficiente del controlador de memoria de la CPU o del tipo DDR de los módulos.', ['cpu', 'ram']);
    else if (!overlap(cpuMemory, ramMemory)) add('cpu-memory-mismatch', 'error', `La CPU admite ${cpuMemory.join('/')} y los módulos son ${ramMemory.join('/')}.`, ['cpu', 'ram']);
  }
  if (motherboard && enclosure) {
    const boardFormats = formats(spec(motherboard, 'Formato'));
    const caseFormats = formats(spec(enclosure, 'Formatos compatibles'));
    if (!boardFormats.length || !caseFormats.length) add('case-format-unknown', 'warning', 'Falta el formato de la placa o la lista de formatos admitidos por la caja.', ['motherboard', 'case']);
    else if (!overlap(boardFormats, caseFormats)) add('case-format-mismatch', 'error', `La caja no declara soporte para el formato ${spec(motherboard, 'Formato')} de la placa.`, ['motherboard', 'case']);
    else add('case-format-match', 'pass', `La caja declara soporte para el formato ${spec(motherboard, 'Formato')} de la placa.`, ['motherboard', 'case']);
  }
  if (cpu && !gpu) {
    const integrated = spec(cpu, 'Gráficos integrados');
    if (integrated && /^(?:no|ninguno|sin\s+gr[aá]ficos|none|false)\b/i.test(integrated)) add('gpu-required', 'error', 'Este procesador declara que no tiene gráficos integrados. Añade una GPU dedicada para disponer de salida de imagen.', ['cpu', 'gpu']);
    else add('display-output-unverified', 'warning', integrated
      ? 'La CPU declara gráficos integrados; comprueba que la placa incluya las salidas de vídeo que necesitas.'
      : 'Sin GPU dedicada y sin datos de gráficos integrados: no está confirmada la salida de imagen.', ['cpu', 'motherboard', 'gpu']);
  }
  if (cpu && cooler) {
    const cpuSockets = socketList(spec(cpu, 'Socket'));
    const coolerSockets = socketList(spec(cooler, 'Sockets compatibles', 'Socket'));
    if (!cpuSockets.length || !coolerSockets.length) add('cooler-socket-unknown', 'warning', 'Falta la lista exacta de sockets y kit de montaje compatibles con la refrigeración.', ['cpu', 'cooler']);
    else if (!overlap(cpuSockets, coolerSockets)) add('cooler-socket-mismatch', 'error', `La refrigeración no declara soporte para ${cpuSockets.join('/')}.`, ['cpu', 'cooler']);
    else add('cooler-socket-match', 'pass', 'La refrigeración declara soporte para el socket. Comprueba que el kit necesario venga incluido.', ['cpu', 'cooler']);
    add('cooling-capacity-unverified', 'warning', 'No se ha validado la capacidad térmica ni el espacio entre refrigeración, RAM y disipadores de la placa.', ['cpu', 'motherboard', 'ram', 'cooler']);
  } else if (cpu && !cooler) {
    const included = spec(cpu, 'Disipador incluido');
    add('cooler-missing', 'warning', included && /^s[ií]\b/i.test(included)
      ? 'La ficha declara un disipador incluido; revisa su montaje y adecuación al uso previsto.'
      : included && /^no\b/i.test(included)
        ? 'La CPU no incluye disipador. Falta seleccionar refrigeración compatible.'
        : 'No hay refrigeración seleccionada ni un disipador incluido confirmado.', ['cpu', 'cooler']);
  }
  if (cooler && enclosure) {
    const radiator = dimension(spec(cooler, 'Radiador'));
    const coolerHeight = dimension(spec(cooler, 'Altura'));
    const maxHeight = dimension(spec(enclosure, 'Altura disipador máxima', 'Altura máxima disipador'));
    if (radiator !== undefined) {
      const supported = spec(enclosure, 'Radiadores compatibles', 'Soporte de radiador');
      const sizes = supported?.match(/\d+(?:[.,]\d+)?/g)?.map(value => Number(value.replace(',', '.'))) ?? [];
      if (sizes.length && !sizes.includes(radiator)) add('radiator-size-mismatch', 'error', `El radiador de ${radiator} mm no figura entre los tamaños declarados por la caja.`, ['cooler', 'case']);
      else add('radiator-clearance-unverified', 'warning', sizes.includes(radiator)
        ? `La caja declara ${radiator} mm, pero falta verificar posición, grosor, tubos y espacio con placa y GPU.`
        : `No está verificado el montaje del radiador de ${radiator} mm en esta caja.`, ['cooler', 'case', 'gpu', 'motherboard']);
    } else if (coolerHeight !== undefined && maxHeight !== undefined) {
      if (coolerHeight > maxHeight) add('cooler-height-mismatch', 'error', `El disipador mide ${coolerHeight} mm y la caja admite hasta ${maxHeight} mm.`, ['cooler', 'case']);
      else add('cooler-clearance-unverified', 'warning', `Altura nominal ${coolerHeight}/${maxHeight} mm. Elevar el ventilador para salvar la RAM puede aumentar la altura real.`, ['cooler', 'case', 'ram']);
    } else add('cooler-clearance-unknown', 'warning', 'Faltan dimensiones o capacidad de montaje para comprobar la refrigeración en la caja.', ['cooler', 'case']);
  }
  if (gpu && enclosure) {
    if (spec(gpu,'Radiador GPU')) add('gpu-radiator-unverified','warning',`Esta GPU necesita su propio radiador de ${spec(gpu,'Radiador GPU')}. Comprueba posiciones y espacio compartido con la refrigeración de CPU.`,['gpu','case','cooler']);
    const length = dimension(spec(gpu, 'Longitud', 'Longitud GPU'));
    const maxLength = dimension(spec(enclosure, 'Longitud GPU máxima', 'Longitud máxima GPU'));
    if (gpu.mpn && length !== undefined && maxLength !== undefined && length > maxLength) add('gpu-length-mismatch', 'error', `La tarjeta de ${length} mm supera el máximo declarado de ${maxLength} mm de la caja.`, ['gpu', 'case']);
    else add('gpu-fit-unverified', 'warning', gpu.mpn && length !== undefined && maxLength !== undefined
      ? `Longitud nominal ${length}/${maxLength} mm. Revisa grosor, ranuras, cables y pérdida de espacio por radiadores frontales.`
      : 'Ajuste físico de GPU sin verificar: hace falta la variante exacta del ensamblador, dimensiones, grosor y espacio para conectores.', ['gpu', 'case', 'cooler']);
  }
  if (storage && motherboard) add('storage-interface-unverified', 'warning', 'Comprueba ranura, protocolo y tamaño del almacenamiento en el manual de la placa; no basta con que el nombre incluya M.2.', ['storage', 'motherboard']);
  if (psu) {
    const capacity = watts(spec(psu, 'Potencia'));
    const cpuMaximum = watts(spec(cpu, 'Potencia máxima turbo', 'Potencia máxima'));
    const cpuNominal = watts(spec(cpu, 'TDP', 'Potencia base', 'Consumo'));
    const cpuBudget = cpuMaximum ?? (cpuNominal === undefined ? undefined : cpuNominal * 1.35);
    const gpuBudget = gpu ? watts(spec(gpu, 'Consumo (TGP)', 'Consumo (TBP)', 'TGP', 'TBP', 'Consumo')) : 0;
    if (capacity === undefined || cpuBudget === undefined || gpuBudget === undefined) add('power-data-unknown', 'warning', 'Faltan datos de potencia de CPU, GPU o fuente. No se puede estimar el margen de alimentación ni verificar conectores.', ['cpu', 'gpu', 'psu']);
    else {
      const estimate = Math.ceil((cpuBudget + gpuBudget + 75) * 1.25 / 50) * 50;
      const manufacturerMinimum = watts(spec(gpu, 'Fuente recomendada'));
      const target = Math.max(estimate, manufacturerMinimum ?? 0);
      add(capacity < target ? 'power-margin-low' : 'power-unverified', 'warning',
        `Fuente ${capacity} W; objetivo orientativo ${target} W${manufacturerMinimum ? ` (incluye la recomendación de referencia GPU de ${manufacturerMinimum} W)` : ''}. ` +
        `Estimación con CPU, GPU, 75 W auxiliares y 25 % de margen${cpuMaximum === undefined ? '; TDP/base de CPU ajustado ×1,35, no potencia máxima medida' : ''}. ` +
        `${capacity < target ? 'La fuente queda por debajo de ese objetivo. ' : ''}No garantiza idoneidad: faltan conectores, transitorios, calidad y carga real.`, ['cpu', 'gpu', 'psu']);
    }
  }
  return checks;
}

const knownPrice = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;

/** Capture values, rather than references to products whose live prices can change. */
export function createPriceSnapshot(parts: BuildParts, now = new Date().toISOString()): PriceSnapshot {
  const created = new Date(now);
  if (!Number.isFinite(created.getTime())) throw new RangeError('La fecha de la captura no es válida.');
  const items = slots.flatMap(slot => {
    const product = parts[slot];
    if (!product) return [];
    return [{ slot, product_id: product.id, name: product.name,
      price_cents: knownPrice(product.min_price_cents) ? product.min_price_cents : null,
      last_checked_at: product.last_checked_at && Number.isFinite(Date.parse(product.last_checked_at)) ? product.last_checked_at : null }];
  });
  const total = items.reduce((sum, item) => sum + (item.price_cents ?? 0), 0);
  if (!Number.isSafeInteger(total)) throw new RangeError('El importe conocido excede el rango admitido.');
  items.forEach(item => Object.freeze(item));
  Object.freeze(items);
  return Object.freeze({ id: globalThis.crypto?.randomUUID?.() ?? `snapshot-${created.getTime()}-${Math.random().toString(36).slice(2)}`,
    created_at: created.toISOString(), items, total_known_cents: total,
    priced_items: items.filter(item => item.price_cents !== null).length, total_items: items.length });
}

export function comparePriceSnapshots(before: PriceSnapshot, after: PriceSnapshot): {
  delta_cents: number | null;
  comparable_items: number;
  complete: boolean;
  changes: { product_id: string; name: string; before_cents: number | null; after_cents: number | null; delta_cents: number | null }[];
} {
  const first = new Map(before.items.map(item => [item.product_id, item]));
  const second = new Map(after.items.map(item => [item.product_id, item]));
  const ids = [...new Set([...first.keys(), ...second.keys()])];
  let comparable = 0;
  let delta = 0;
  const changes = ids.map(product_id => {
    const previous = first.get(product_id);
    const current = second.get(product_id);
    const before_cents = knownPrice(previous?.price_cents) ? previous!.price_cents : null;
    const after_cents = knownPrice(current?.price_cents) ? current!.price_cents : null;
    const delta_cents = before_cents !== null && after_cents !== null ? after_cents - before_cents : null;
    if (delta_cents !== null) { comparable += 1; delta += delta_cents; }
    return { product_id, name: current?.name ?? previous!.name, before_cents, after_cents, delta_cents };
  });
  const sameItems = first.size === before.items.length && second.size === after.items.length &&
    first.size === second.size && ids.every(id => first.has(id) && second.has(id) && first.get(id)!.slot === second.get(id)!.slot);
  return { delta_cents: comparable ? delta : null, comparable_items: comparable,
    complete: sameItems && ids.length > 0 && comparable === ids.length, changes };
}
