import { describe, expect, test } from 'vitest';
import type { Product } from '../packages/api-contracts/index';
import { checkCompatibility, createPriceSnapshot, comparePriceSnapshots, filterCompatibleOptions, type BuildParts, type BuildSlot } from '../apps/web/src/pc-builder';

function part(slot: BuildSlot, id: string, specs: Record<string, string>, price: number | null = null): Product {
  return { id, slug: id, name: id, category_id: slot, brand: 'Test', specs,
    min_price_cents: price, offers_count: price === null ? 0 : 1, last_checked_at: '2026-11-01T12:00:00Z' };
}
const cpu = part('cpu', 'am5-cpu', { Socket: 'AM5', 'Memoria compatible': 'DDR5', TDP: '120 W', 'Gráficos integrados': 'Radeon Graphics', 'Disipador incluido': 'No' }, 30000);
const board = part('motherboard', 'am5-board', { Socket: 'AM5', 'Tipo de memoria': 'DDR5', Formato: 'ATX' }, 20000);
const ram = part('ram', 'ddr5-ram', { 'Tipo de memoria': 'DDR5' }, 10000);
const enclosure = part('case', 'case', { 'Formatos compatibles': 'ATX / Micro-ATX / Mini-ITX', 'Longitud GPU máxima': '355 mm', 'Altura disipador máxima': '170 mm' });

describe('specific compatibility checks without unverified guarantees', () => {
  test('reports AM5/LGA incompatibility, and keeps missing parts and BIOS visible', () => {
    const checks = checkCompatibility({ cpu, motherboard: part('motherboard', 'intel-board', { Socket: 'LGA1851' }) });
    expect(checks).toContainEqual(expect.objectContaining({ code: 'socket-mismatch', severity: 'error', slots: ['cpu', 'motherboard'] }));
    expect(checks.some(check => check.code === 'bios-unverified' && check.severity === 'warning')).toBe(true);
    expect(checks.some(check => check.code === 'missing-parts' && check.slots.includes('psu'))).toBe(true);
  });
  test('recognizes FCLGA/LGA normalization but never equates socket with BIOS support', () => {
    const checks = checkCompatibility({ cpu: part('cpu', 'intel-cpu', { Socket: 'FCLGA 1851' }), motherboard: part('motherboard', 'intel-board', { Socket: 'LGA1851' }) });
    expect(checks.find(check => check.code === 'socket-match')?.severity).toBe('pass');
    expect(checks.find(check => check.code === 'bios-unverified')?.severity).toBe('warning');
  });
  test('DDR4 vs DDR5 is a definite conflict; missing memory data stays unknown', () => {
    const mismatch = checkCompatibility({ cpu, motherboard: board, ram: part('ram', 'ddr4', { 'Memoria': '32 GB DDR4' }) });
    expect(mismatch.filter(check => check.severity === 'error').map(check => check.code)).toEqual(expect.arrayContaining(['memory-mismatch', 'cpu-memory-mismatch']));
    const unknown = checkCompatibility({ motherboard: board, ram: part('ram', 'unknown-ram', { Capacidad: '32 GB' }) });
    expect(unknown.find(check => check.code === 'memory-unknown')?.severity).toBe('warning');
    expect(unknown.some(check => check.code === 'memory-match')).toBe(false);
  });
  test('micro-ATX is not accidentally interpreted as full ATX support', () => {
    const checks = checkCompatibility({ motherboard: board, case: part('case', 'small-case', { 'Formatos compatibles': 'Micro-ATX, Mini-ITX' }) });
    expect(checks.find(check => check.code === 'case-format-mismatch')?.severity).toBe('error');
    expect(checkCompatibility({ motherboard: { ...board, specs: { Formato: 'mATX' } }, case: enclosure }).find(check => check.code === 'case-format-match')?.severity).toBe('pass');
  });
  test('cooler mounting and excessive height conflict, while reference GPU fit remains unknown', () => {
    const checks = checkCompatibility({ cpu, case: enclosure,
      cooler: part('cooler', 'intel-only-cooler', { 'Sockets compatibles': 'LGA1700 / LGA1851', Altura: '175 mm' }),
      gpu: part('gpu', 'reference-gpu', { Longitud: '304 mm', 'Consumo (TGP)': '300 W' }) });
    expect(checks.find(check => check.code === 'cooler-socket-mismatch')?.severity).toBe('error');
    expect(checks.find(check => check.code === 'cooler-height-mismatch')?.severity).toBe('error');
    expect(checks.find(check => check.code === 'gpu-fit-unverified')?.severity).toBe('warning');
    expect(checks.some(check => check.code === 'gpu-fit-match')).toBe(false);
  });
  test('explicit CPU without integrated graphics requires a GPU; unknown is not treated as no', () => {
    expect(checkCompatibility({ cpu: part('cpu', 'intel-f', { 'Gráficos integrados': 'No (requiere GPU dedicada)' }) }).find(check => check.code === 'gpu-required')?.severity).toBe('error');
    expect(checkCompatibility({ cpu: part('cpu', 'unknown', {}) }).some(check => check.code === 'gpu-required')).toBe(false);
    expect(checkCompatibility({ cpu: part('cpu', 'unknown', {}) }).find(check => check.code === 'display-output-unverified')?.severity).toBe('warning');
  });
  test('PSU uses turbo power and manufacturer guidance but never promises suitability', () => {
    const build: BuildParts = { cpu: part('cpu', 'turbo-cpu', { 'Potencia base': '65 W', 'Potencia máxima turbo': '250 W' }),
      gpu: part('gpu', 'high-power-gpu', { 'Consumo (TGP)': '575 W', 'Fuente recomendada': '1000 W (sistema de referencia)' }),
      psu: part('psu', 'small-psu', { Potencia: '650 W' }) };
    const checks = checkCompatibility(build);
    expect(checks.find(check => check.code === 'power-margin-low')).toEqual(expect.objectContaining({ severity: 'warning', message: expect.stringContaining('1150 W') }));
    const larger = checkCompatibility({ ...build, psu: part('psu', 'large-psu', { Potencia: '1.200 W' }) });
    expect(larger.find(check => check.code === 'power-unverified')?.severity).toBe('warning');
    expect(larger.filter(check => check.slots.includes('psu')).every(check => check.severity !== 'pass')).toBe(true);
  });
  test('missing wattage cannot produce a successful power check', () => {
    const checks = checkCompatibility({ cpu, psu: part('psu', 'unknown-power', {}) });
    expect(checks.find(check => check.code === 'power-data-unknown')?.severity).toBe('warning');
  });
});

describe('dated price snapshots', () => {
  test('copies immutable dated values and leaves unknown prices unknown', () => {
    const selection = { cpu: { ...cpu }, motherboard: { ...board, min_price_cents: null } };
    const snapshot = createPriceSnapshot(selection, '2026-11-01T13:00:00+01:00');
    expect(snapshot.created_at).toBe('2026-11-01T12:00:00.000Z');
    expect(snapshot.total_known_cents).toBe(30000);
    expect(snapshot.priced_items).toBe(1);
    expect(snapshot.total_items).toBe(2);
    expect(snapshot.items[1].price_cents).toBeNull();
    selection.cpu.min_price_cents = 1;
    selection.cpu.name = 'Changed live name';
    expect(snapshot.items[0]).toEqual(expect.objectContaining({ price_cents: 30000, name: 'am5-cpu' }));
    expect(Object.isFrozen(snapshot)).toBe(true);
    expect(Object.isFrozen(snapshot.items)).toBe(true);
    expect(Object.isFrozen(snapshot.items[0])).toBe(true);
  });
  test('only compares identical products priced in both, not unknown-as-zero', () => {
    const before = createPriceSnapshot({ cpu, motherboard: { ...board, min_price_cents: null }, ram }, '2026-11-01T12:00:00Z');
    const after = createPriceSnapshot({ cpu: { ...cpu, min_price_cents: 28000 }, motherboard: board,
      ram: part('ram', 'different-ram', { 'Tipo de memoria': 'DDR5' }, 12000) }, '2026-11-02T12:00:00Z');
    const compared = comparePriceSnapshots(before, after);
    expect(compared.comparable_items).toBe(1);
    expect(compared.delta_cents).toBe(-2000);
    expect(compared.complete).toBe(false);
    expect(compared.changes.find(change => change.product_id === board.id)?.delta_cents).toBeNull();
    expect(compared.changes.find(change => change.product_id === 'different-ram')?.before_cents).toBeNull();
    expect(compared.changes.find(change => change.product_id === ram.id)?.after_cents).toBeNull();
  });
  test('no comparable prices yields null, including empty snapshots', () => {
    const empty = createPriceSnapshot({});
    expect(comparePriceSnapshots(empty, empty)).toEqual({ delta_cents: null, comparable_items: 0, complete: false, changes: [] });
    const unknown = createPriceSnapshot({ cpu: { ...cpu, min_price_cents: null } });
    expect(comparePriceSnapshots(unknown, unknown).delta_cents).toBeNull();
    expect(comparePriceSnapshots(unknown, unknown).complete).toBe(false);
  });
  test('complete means identical selected items with all prices known; genuine zero is known', () => {
    const before = createPriceSnapshot({ cpu, ram: { ...ram, min_price_cents: 0 } });
    const after = createPriceSnapshot({ cpu: { ...cpu, min_price_cents: 31000 }, ram: { ...ram, min_price_cents: 0 } });
    expect(comparePriceSnapshots(before, after)).toEqual(expect.objectContaining({ delta_cents: 1000, comparable_items: 2, complete: true }));
  });
});

describe('options filtered by the parts already chosen', () => {
  const boards = [board, part('motherboard', 'lga-board', { Socket: 'LGA 1851', 'Tipo de memoria': 'DDR5' }),
    part('motherboard', 'am4-board', { Socket: 'AM4', 'Tipo de memoria': 'DDR4' }), part('motherboard', 'unknown-board', {})];
  const rams = [ram, part('ram', 'ddr4-ram', { 'Tipo de memoria': 'DDR4' }), part('ram', 'unknown-ram', {})];
  test('motherboards by CPU socket', () => {
    const result = filterCompatibleOptions('motherboard', boards, { cpu });
    expect(result?.options.map(p => p.id)).toEqual(['am5-board']);
    expect(result).toMatchObject({ label: 'socket AM5', hidden: 3 });
    expect(filterCompatibleOptions('motherboard', boards, { cpu: part('cpu', 'intel', { Socket: 'FCLGA1851' }) })?.options.map(p => p.id)).toEqual(['lga-board']);
  });
  test('RAM by the DDR generation of the board, or of the CPU when there is no board', () => {
    expect(filterCompatibleOptions('ram', rams, { motherboard: boards[2] })?.options.map(p => p.id)).toEqual(['ddr4-ram']);
    expect(filterCompatibleOptions('ram', rams, { cpu })?.options.map(p => p.id)).toEqual(['ddr5-ram']);
    expect(filterCompatibleOptions('ram', rams, { cpu, motherboard: boards[2] })?.label).toBe('DDR4 (placa base)');
  });
  test('no filter without a chosen part or without data', () => {
    expect(filterCompatibleOptions('motherboard', boards, {})).toBeNull();
    expect(filterCompatibleOptions('motherboard', boards, { cpu: part('cpu', 'x', {}) })).toBeNull();
    expect(filterCompatibleOptions('gpu', [], { cpu })).toBeNull();
  });
});
