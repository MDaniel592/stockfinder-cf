import { describe, expect, test } from 'vitest';
import { displayName, groupSpecs, keySpecs, splitList, tableColumns } from '../apps/web/src/display';

describe('display helpers', () => {
  test('strips a trailing (CODE) only when it is the MPN or a retailer code', () => {
    const ram = { name: 'ADATA XPG LANCER RGB 16 GB (1 × 16 GB) DDR5-6000 CL30-40-40 (AX5U6000C3016G-DCLARWH)', mpn: 'AX5U6000C3016G-DCLARWH' };
    expect(displayName(ram)).toBe('ADATA XPG LANCER RGB 16 GB (1 × 16 GB) DDR5-6000 CL30-40-40');
    expect(displayName({ name: 'ARCTIC Alpine 17 (ACALP00040A)', mpn: 'acalp 00040a' })).toBe('ARCTIC Alpine 17');
    expect(displayName({ name: 'Cooler X (CM-1)', mpn: null, retailer_codes: [{ shop_id: 'coolmod', code: 'CM-1' }] })).toBe('Cooler X');
    expect(displayName({ name: 'AMD Ryzen 5 5500F (Tray)', mpn: '100-000001905' })).toBe('AMD Ryzen 5 5500F (Tray)');
  });
  test('groups canonical keys per category and keeps unknown keys', () => {
    const groups = groupSpecs({ category_id: 'gpu', specs: { 'Memoria': '8 GB', 'Chip gráfico': 'RTX 5060', 'Consumo (TBP)': '145 W', 'Rareza': 'X' } });
    expect(groups.map(g => g.title)).toEqual(['Chip', 'Memoria', 'Consumo y alimentación', 'Otras características']);
    expect(groupSpecs({ category_id: 'zzz', specs: { A: '1' } })[0].title).toBe('Características');
    expect(splitList('Formatos compatibles', 'ATX, Micro-ATX, Mini-ITX')).toEqual(['ATX', 'Micro-ATX', 'Mini-ITX']);
    expect(splitList('Dimensiones', '300 x 120 mm')).toBeNull();
  });
  test('key specs and table columns follow the category', () => {
    expect(keySpecs({ category_id: 'cpu', specs: { Socket: 'AM5', 'Núcleos': '8', Hilos: '16' } }, 2).map(c => c.text)).toEqual(['AM5', '8 núcleos']);
    expect(keySpecs({ category_id: 'gpu', specs: { Memoria: '8 GB GDDR7', 'Tipo de memoria': 'GDDR7' } }).map(c => c.text)).toEqual(['8 GB GDDR7']);
    const cols = tableColumns('cpu');
    expect(cols.map(c => c.label)).toEqual(['Núcleos / hilos', 'Turbo', 'Socket', 'TDP']);
    expect(cols[0].value({ 'Núcleos': '8', Hilos: '16' })).toBe('8 / 16');
    expect(tableColumns('gpu')[1].value({ Memoria: '8 GB', 'Tipo de memoria': 'GDDR7' })).toBe('8 GB GDDR7');
  });
});
