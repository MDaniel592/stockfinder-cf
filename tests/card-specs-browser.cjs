// Catalogue rows: category spec columns on desktop, compact rows (name + 2 key specs + price) on mobile, optional card view.
// Runs against a local Worker (never the public site: production D1 has a daily read budget).
// Usage: TEST_BASE_URL=http://127.0.0.1:8795 CHROMIUM_PATH=... node tests/card-specs-browser.cjs
const { chromium } = require('@playwright/test');
const assert = require('node:assert/strict');

const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8787';
const EXPECTED = { gpu: ['Chip', 'Memoria', 'Bus / longitud', 'Consumo'], cpu: ['Núcleos / hilos', 'Turbo', 'Socket', 'TDP'],
  motherboard: ['Socket', 'Chipset', 'Formato', 'Memoria'], ram: ['Capacidad', 'Velocidad', 'Latencia', 'Módulos'],
  storage: ['Capacidad', 'Interfaz', 'Lectura'], psu: ['Potencia', 'Eficiencia', 'Modular', 'ATX'],
  case: ['Placas', 'GPU máx.', 'Panel lateral'], cooler: ['Tipo', 'Radiador / altura', 'Sockets'] };

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {});
  const errors = [];
  const ready = page => page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && /resultado/.test(document.querySelector('.catalog-result-meta')?.textContent ?? ''));

  // Desktop: one table, per-category spec columns, ~48–56 px rows, names without "(MPN)".
  const desktop = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  desktop.on('pageerror', e => errors.push(e.message));
  for (const [category, headers] of Object.entries(EXPECTED)) {
    await desktop.goto(`${base}/c/${category}`, { waitUntil: 'load' }); await ready(desktop);
    assert.deepEqual(await desktop.locator('thead th.col-spec').allInnerTexts(), headers, `${category}: spec columns`);
    const info = await desktop.evaluate(() => [...document.querySelectorAll('.product-row')].map(row => ({
      h: row.getBoundingClientRect().height, cells: row.querySelectorAll('td.col-spec').length, filled: [...row.querySelectorAll('td.col-spec')].filter(td => td.textContent !== '—').length,
      name: row.querySelector('.product-name').textContent })));
    assert.ok(info.length > 3, `${category}: rows rendered`);
    assert.ok(info.every(r => r.cells === headers.length), `${category}: one cell per column`);
    assert.ok(info.filter(r => r.filled > 0).length >= info.length / 2, `${category}: most rows have spec values`);
    const tall = info.filter(r => r.h > 64);
    assert.ok(tall.length <= info.length / 4, `${category}: rows stay compact (${tall.map(r => Math.round(r.h)).join(',')})`);
    assert.ok(await desktop.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${category}: no horizontal scroll`);
  }
  // RAM names in the data end with "(MPN)": the visible name must not.
  await desktop.goto(`${base}/c/ram?q=AX5U6000C3016G-DCLARWH`, { waitUntil: 'load' }); await ready(desktop);
  const ramNames = await desktop.locator('.product-name').allInnerTexts();
  assert.ok(ramNames.every(name => !name.includes('(AX5U6000C3016G-DCLARWH)')), 'MPN suffix stripped: ' + ramNames.join(' | '));
  // Optional card view still works and is remembered.
  await desktop.goto(`${base}/c/gpu`, { waitUntil: 'load' }); await ready(desktop);
  await desktop.getByRole('button', { name: 'Vista de tarjetas' }).click();
  await desktop.locator('.product-grid.grid .product-card').first().waitFor();
  await desktop.reload({ waitUntil: 'load' }); await ready(desktop);
  assert.ok(await desktop.locator('.product-grid.grid').count(), 'card view remembered');
  await desktop.getByRole('button', { name: 'Vista de lista' }).click();
  await desktop.locator('table.product-table').waitFor();

  // Mobile: rows (not cards), at most 2 visible key specs, values not clipped, price visible, no overflow.
  const mobile = await (await browser.newContext({ viewport: { width: 375, height: 812 } })).newPage();
  mobile.on('pageerror', e => errors.push(e.message));
  for (const category of ['gpu', 'cpu', 'ram', 'case']) {
    await mobile.goto(`${base}/c/${category}`, { waitUntil: 'load' }); await ready(mobile);
    const rows = await mobile.evaluate(() => [...document.querySelectorAll('.product-row')].slice(0, 12).map(row => {
      const box = row.getBoundingClientRect(); const price = row.querySelector('.col-price').getBoundingClientRect();
      const chips = [...row.querySelectorAll('.spec-line span')].filter(s => { const b = s.getBoundingClientRect(); return b.height > 0 && b.bottom <= s.parentElement.getBoundingClientRect().bottom + 1; });
      return { h: box.height, chips: chips.length, priceVisible: price.width > 30 && price.right <= box.right + 1, chipsInside: chips.every(s => s.getBoundingClientRect().right <= box.right + 1) };
    }));
    assert.ok(rows.length > 3, `${category}: mobile rows`);
    for (const row of rows) {
      assert.ok(row.h <= 96, `${category}: compact row (${row.h}px)`);
      assert.ok(row.chips <= 2 && row.chipsInside, `${category}: at most 2 key specs, inside the row`);
      assert.ok(row.priceVisible, `${category}: price visible`);
    }
    assert.equal(await mobile.locator('thead').evaluate(el => getComputedStyle(el).display), 'none');
    assert.ok(await mobile.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${category}: no horizontal scroll`);
  }
  assert.deepEqual(errors, []);
  await browser.close();
  console.log('card-specs-browser: ok (table columns per category, compact rows, MPN suffix hidden, card view toggle, mobile rows)');
})().catch(error => { console.error(error); process.exit(1); });
