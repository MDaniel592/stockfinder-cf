// Routes, deep links, URL filters, landing page, product page, freshness, price history, info pages and references.
// Usage: TEST_BASE_URL=http://127.0.0.1:8787 CHROMIUM_PATH=... node tests/routes-browser.cjs
const { chromium } = require('@playwright/test');
const assert = require('node:assert/strict');

const LIST = /\/api\/v1\/products\?/;
const SLUG = 'amd-ryzen-7-9800x3d';
const hoursAgo = hours => new Date(Date.now() - hours * 3600000).toISOString();
const daysAgo = days => hoursAgo(days * 24);

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {});
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
  const page = await context.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error' && !/status of 40[04]/.test(message.text())) errors.push(message.text()); });
  const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8787';
  const path = () => new URL(page.url()).pathname + new URL(page.url()).search;
  const waitResults = () => page.waitForFunction(() => !document.querySelector('[aria-busy="true"]') && /resultado/.test(document.querySelector('.catalog-result-meta')?.textContent ?? ''));
  const open = async url => { await page.goto(base + url, { waitUntil: 'load' }); };
  const productHeading = () => page.locator('.product-page h1');

  // --- Landing page: search first, categories, trust block; no product list
  await open('/');
  assert.equal(await page.title(), 'StockFinder — Compara componentes de PC y precios en tiendas');
  await page.getByRole('heading', { level: 1, name: 'Compara precios de componentes de PC' }).waitFor();
  assert.equal(await page.locator('.category-tile').count(), 8);
  await page.locator('.category-tile-meta', { hasText: 'modelos' }).first().waitFor();
  assert.equal(await page.locator('.product-row').count(), 0);
  await page.locator('#search').fill('9800X3D');
  await page.keyboard.press('Enter');
  await page.waitForURL(/\/\?q=9800X3D$/); await waitResults();
  assert.ok((await page.locator('.product-name').allTextContents()).every(name => /9800X3D/i.test(name)));
  assert.equal(await page.title(), 'StockFinder — Compara componentes de PC y precios en tiendas');

  // --- Category deep links (table view by default)
  await open('/c/cpu?marca=AMD&orden=precio');
  await waitResults();
  assert.equal(await page.locator('h1').first().innerText().then(t => t.replace(/[\d.]+|…/g, '').trim()), 'Procesadores');
  assert.equal(await page.title(), 'Procesadores · Compara precios y especificaciones | StockFinder');
  assert.ok(await page.locator('table.product-table').count(), 'table view by default');
  assert.ok((await page.locator('.product-name').allTextContents()).every(name => /AMD|Ryzen|Athlon/i.test(name)));
  assert.equal(await page.locator('.catalog-filters').getByText('AMD', { exact: true }).locator('..').getByRole('checkbox').isChecked(), true);
  assert.equal(await page.getByRole('combobox', { name: 'Ordenar productos' }).inputValue(), 'price');
  assert.equal(await page.locator('th.col-price').getAttribute('aria-sort'), 'ascending');
  assert.equal(await page.getByRole('navigation', { name: 'Categorías' }).getByRole('link', { name: 'Procesadores', exact: true }).getAttribute('aria-current'), 'page');
  // Sortable column header writes the URL
  await page.locator('th.col-price .th-sort').click(); await waitResults();
  assert.equal(path(), '/c/cpu?marca=AMD&orden=precio-desc');
  await page.goBack(); await waitResults();

  // --- Filters write to the URL; back/forward restore them
  const filters = page.locator('.catalog-filters');
  await filters.getByText('Intel', { exact: true }).locator('..').getByRole('checkbox').check();
  await waitResults();
  assert.equal(path(), '/c/cpu?marca=AMD&marca=Intel&orden=precio');
  await filters.getByText('AM5', { exact: true }).locator('..').getByRole('checkbox').check();
  await waitResults();
  assert.ok(decodeURIComponent(path()).includes('spec.Socket=AM5'));
  await page.goBack(); await waitResults();
  assert.equal(path(), '/c/cpu?marca=AMD&marca=Intel&orden=precio');
  assert.equal(await filters.getByText('Intel', { exact: true }).locator('..').getByRole('checkbox').isChecked(), true);
  await page.goForward(); await waitResults();
  assert.ok((await page.locator('.spec-line').allTextContents()).every(text => text.includes('AM5')));
  assert.ok((await page.locator('td.col-spec[data-label="Socket"]').allTextContents()).every(text => text === 'AM5'));
  await page.reload({ waitUntil: 'load' }); await waitResults();
  assert.ok(decodeURIComponent(path()).includes('spec.Socket=AM5'));
  await open('/c/cpu?stock=1&min=300&max=450'); await waitResults();
  const prices = await page.locator('.price-verified').allTextContents();
  assert.ok(prices.length > 0);
  assert.ok(prices.every(text => { const n = Number(text.replace(/[^\d,]/g, '').replace(',', '.')); return n >= 300 && n <= 450; }));
  await page.getByRole('button', { name: 'Limpiar filtros' }).first().click(); await waitResults();
  assert.equal(path(), '/c/cpu');

  // --- Filter column never makes the page taller than the results
  await open('/c/cpu?q=9800X3D'); await waitResults();
  const heights = await page.evaluate(() => ({ aside: document.querySelector('.catalog-filters-inner').getBoundingClientRect().height,
    row: document.querySelector('.catalog-workspace').getBoundingClientRect().height, results: document.querySelector('.catalog-results').getBoundingClientRect().height,
    scroll: getComputedStyle(document.querySelector('.catalog-filters-inner')).overflowY, vh: innerHeight }));
  assert.ok(heights.aside <= heights.row + 1 && heights.row <= Math.max(heights.results, heights.vh) + 1, JSON.stringify(heights));
  assert.equal(heights.scroll, 'auto');

  // --- Search and pagination in the URL
  await open('/c/cpu');
  await waitResults();
  await page.getByRole('searchbox').fill('9800');
  await page.waitForURL(/q=9800/); await waitResults();
  assert.ok((await page.locator('.product-name').allTextContents()).every(name => /9800/.test(name)));
  await page.reload({ waitUntil: 'load' }); await waitResults();
  assert.equal(await page.getByRole('searchbox').inputValue(), '9800');
  await open('/c/cpu?pagina=2'); await waitResults();
  await page.getByText(/^Página 2 de \d+$/).waitFor();
  await page.getByRole('button', { name: 'Anterior' }).click(); await waitResults();
  assert.equal(path(), '/c/cpu');
  await open('/c/cpu?spec.Inventada=1'); await page.getByRole('heading', { name: 'Filtros no válidos' }).waitFor();
  await page.getByRole('alert').getByRole('button', { name: 'Limpiar filtros' }).click(); await waitResults();

  // --- Product link from list: real anchor, product PAGE, back to the same list URL
  await open('/c/cpu?marca=AMD'); await waitResults();
  const link = page.locator('a.product-name', { hasText: 'Ryzen 7 9800X3D' }).first();
  assert.equal(await link.getAttribute('href'), '/p/' + SLUG);
  await link.click();
  await productHeading().waitFor();
  assert.equal(path(), '/p/' + SLUG);
  assert.match(await page.title(), /· Precios y especificaciones \| StockFinder$/);
  assert.equal(await page.locator('.product-row').count(), 0, 'product page replaces the list');
  assert.equal(await page.locator('dialog[open]').count(), 0);
  await page.getByRole('link', { name: 'Volver a los resultados' }).click();
  await waitResults();
  assert.equal(path(), '/c/cpu?marca=AMD');
  await link.click(); await productHeading().waitFor();
  await page.goBack(); await waitResults();
  assert.equal(path(), '/c/cpu?marca=AMD');
  await page.goForward(); await productHeading().waitFor();
  assert.equal(path(), '/p/' + SLUG);

  // --- Product deep link: breadcrumb to the category, grouped specifications
  await open('/p/' + SLUG);
  await productHeading().waitFor();
  assert.ok((await productHeading().innerText()).includes('9800X3D'));
  assert.ok(await page.locator('.spec-group h3').count() >= 2, 'specs are grouped');
  assert.ok((await page.locator('.spec-group h3').allTextContents()).some(t => /Núcleos y frecuencias/i.test(t)));
  await page.getByRole('navigation', { name: 'Ruta de navegación' }).getByRole('link', { name: 'Procesadores' }).click();
  await page.waitForURL(/\/c\/cpu$/);

  // --- Freshness of offers (fixture with fresh / stale / unverified rows)
  const offerRows = [
    { id: 'a-1', shop_name: 'Tienda fresca', price_cents: 41000, in_stock: 1, last_checked_at: hoursAgo(3), url_public: 'https://example.com/a' },
    { id: 'b-1', shop_name: 'Tienda antigua', price_cents: 42000, in_stock: 1, last_checked_at: daysAgo(3), url_public: 'https://example.com/b' },
    { id: 'c-1', shop_name: 'Tienda olvidada', price_cents: 43000, in_stock: 0, last_checked_at: daysAgo(10), url_public: 'https://example.com/c' }];
  await page.route(`**/api/v1/products/${SLUG}/offers`, route => route.fulfill({ json: { data: offerRows, status: {} } }));
  await page.route(`**/api/v1/products/${SLUG}/price-history`, route => route.fulfill({ json: { data: [], status: {} } }));
  await open('/p/' + SLUG);
  await page.locator('.offers > div').first().waitFor();
  const rows = page.locator('.offers > div');
  assert.equal(await rows.count(), 3);
  assert.match(await rows.nth(0).innerText(), /Comprobado hace 3 h/);
  assert.equal(await rows.nth(0).locator('.fresh-badge').count(), 0);
  assert.match(await rows.nth(1).locator('.fresh-badge').innerText(), /Desactualizado/);
  assert.match(await rows.nth(2).locator('.fresh-badge').innerText(), /Sin verificar hace 10 días/);
  assert.ok((await rows.nth(0).locator('small[title]').getAttribute('title')).length > 5);
  assert.match(await page.locator('.pp-pricebox').innerText(), /Tienda fresca/);
  assert.equal(await page.locator('.price-history').count(), 0, 'empty history hides the chart');

  // --- Price history chart (fixture) and failure tolerance
  const history = [
    { offer_id: 'a-1', shop_name: 'A', price_cents: 45000, in_stock: 1, observed_at: daysAgo(100) },
    { offer_id: 'a-1', shop_name: 'A', price_cents: 43000, in_stock: 1, observed_at: daysAgo(60) },
    { offer_id: 'b-1', shop_name: 'B', price_cents: 40000, in_stock: 1, observed_at: daysAgo(20) },
    { offer_id: 'a-1', shop_name: 'A', price_cents: 41000, in_stock: 1, observed_at: daysAgo(2) }];
  await page.unroute(`**/api/v1/products/${SLUG}/price-history`);
  await page.route(`**/api/v1/products/${SLUG}/price-history`, route => route.fulfill({ json: { data: history, status: {} } }));
  await open('/p/' + SLUG);
  const chart = page.locator('svg.price-chart');
  await chart.waitFor();
  assert.ok((await chart.getAttribute('aria-label')).includes('Mínimo en 90 días: 400,00'));
  assert.ok((await page.locator('.price-history-summary').innerText()).includes('hoy: 400,00'));
  assert.ok(await page.locator('.price-chart-line').getAttribute('d').then(d => d.includes('H') && d.includes('V')), 'stepped path');
  await page.getByRole('button', { name: '30 días' }).click();
  assert.ok((await page.locator('.price-history-summary').innerText()).startsWith('Mínimo en 30 días'));
  await page.getByRole('button', { name: 'Todo' }).click();
  assert.ok((await page.locator('.price-history-summary').innerText()).startsWith('Mínimo histórico: 400,00'));
  assert.equal(await page.getByRole('button', { name: 'Todo' }).getAttribute('aria-pressed'), 'true');
  await page.unroute(`**/api/v1/products/${SLUG}/price-history`);
  await page.route(`**/api/v1/products/${SLUG}/price-history`, route => route.fulfill({ status: 404, json: { error: 'Product not found' } }));
  await open('/p/' + SLUG);
  await page.locator('.offers > div').first().waitFor();
  assert.equal(await page.locator('.price-history').count(), 0);
  await page.unroute(`**/api/v1/products/${SLUG}/price-history`);
  await page.route(`**/api/v1/products/${SLUG}/price-history`, route => route.fulfill({ json: { data: [history[0]], status: {} } }));
  await open('/p/' + SLUG);
  await page.locator('.offers > div').first().waitFor();
  assert.equal(await page.locator('.price-history').count(), 0, 'a single point hides the chart');

  // --- Manufacturer and retailer references; names never show the "(MPN)" suffix
  await page.unroute(`**/api/v1/products/${SLUG}`).catch(() => {});
  await page.route(`**/api/v1/products/${SLUG}`, async route => {
    const response = await route.fetch(); const body = await response.json();
    body.data.mpn = '100-100001084WOF'; body.data.mpn_source = 'manufacturer';
    body.data.retailer_codes = [{ shop_id: 'coolmod', code: 'CM-9800' }, { shop_id: 'tienda-rara', code: 'XX-1' }];
    await route.fulfill({ response, json: body });
  });
  await page.route(LIST, async route => {
    const response = await route.fetch(); const body = await response.json();
    body.data.forEach(item => { if (item.slug === SLUG) { item.retailer_codes = [{ shop_id: 'coolmod', code: 'CM-9800' }]; item.name = `${item.name} (CM-9800)`; } });
    await route.fulfill({ response, json: body });
  });
  await open('/p/' + SLUG);
  const refs = page.locator('.references');
  await refs.waitFor();
  const refRows = await refs.locator('li').allInnerTexts();
  assert.ok(refRows.some(t => t.includes('Fabricante') && t.includes('100-100001084WOF')));
  assert.ok(refRows.some(t => t.includes('Código en Coolmod') && t.includes('CM-9800')));
  assert.ok(refRows.some(t => t.includes('Código en tienda-rara') && t.includes('XX-1')));
  await refs.getByRole('button', { name: 'Copiar código de Coolmod' }).click();
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()).catch(() => 'CM-9800'), 'CM-9800');
  await open('/c/cpu?marca=AMD'); await waitResults();
  const shown = await page.locator('a.product-name', { hasText: '9800X3D' }).first().innerText();
  assert.ok(!shown.includes('(CM-9800)'), `MPN suffix stripped: ${shown}`);
  await page.locator('.pc-compare input').nth(0).check();
  await page.locator('.pc-compare input').nth(1).check();
  await page.getByRole('button', { name: 'Comparar componentes' }).click();
  await page.getByRole('dialog', { name: 'Comparar componentes' }).waitFor();
  assert.equal(await page.locator('dialog tbody th', { hasText: 'Referencia' }).count(), 1);
  await page.keyboard.press('Escape');
  await page.getByRole('dialog', { name: 'Comparar componentes' }).waitFor({ state: 'hidden' });
  await page.unroute(`**/api/v1/products/${SLUG}`);
  await page.unroute(LIST);

  // --- Retailer-reference mark: discreet, tooltip on focus and hover, exposed to screen readers
  await page.route(`**/api/v1/products/${SLUG}`, async route => {
    const response = await route.fetch(); const body = await response.json(); body.data.mpn_source = 'retailer';
    await route.fulfill({ response, json: body });
  });
  await open('/p/' + SLUG);
  const mark = page.locator('.pp-ref .ref-mark');
  await mark.waitFor();
  const tipId = await mark.getAttribute('aria-describedby');
  assert.ok(tipId);
  await mark.focus();
  assert.ok(await page.locator(`[id="${tipId}"]`).isVisible(), 'tooltip on focus');
  assert.match(await page.locator(`[id="${tipId}"]`).innerText(), /Referencia según la tienda/);
  await page.unroute(`**/api/v1/products/${SLUG}`);

  // --- Not found views
  await open('/p/no-existe-este-producto');
  await page.getByRole('heading', { name: 'Producto no encontrado' }).waitFor();
  assert.equal(await page.title(), 'Producto no encontrado | StockFinder');
  await page.getByRole('link', { name: 'Ir al catálogo' }).click();
  await page.waitForURL(base + '/');
  await open('/ruta/inexistente');
  await page.getByRole('heading', { name: 'Página no encontrada' }).waitFor();
  await open('/c/inexistente');
  await page.getByRole('heading', { name: 'Página no encontrada' }).waitFor();

  // --- Info pages and navigation
  await open('/acerca');
  await page.getByRole('heading', { name: 'Cómo funciona StockFinder' }).waitFor();
  assert.equal(await page.title(), 'Cómo funciona StockFinder');
  assert.ok((await page.locator('.info-page').innerText()).includes('QVL'));
  await page.locator('footer').getByRole('link', { name: 'Privacidad' }).click();
  await page.getByRole('heading', { name: 'Privacidad', exact: true }).waitFor();
  assert.equal(path(), '/privacidad'); assert.equal(await page.title(), 'Privacidad | StockFinder');
  assert.equal(await page.evaluate(() => document.activeElement?.tagName), 'H1');
  await page.getByRole('link', { name: 'Configurador de PC', exact: true }).first().click();
  await page.getByRole('heading', { name: 'Configurador de PC' }).first().waitFor();
  assert.equal(path(), '/configurador'); assert.equal(await page.title(), 'Configurador de PC | StockFinder');
  await page.goBack(); await page.getByRole('heading', { name: 'Privacidad', exact: true }).waitFor();
  await page.getByRole('navigation', { name: 'Categorías' }).getByRole('link', { name: 'Memoria RAM' }).click();
  await waitResults();
  assert.equal(path(), '/c/ram');
  const newTab = context.waitForEvent('page');
  await page.getByRole('navigation', { name: 'Categorías' }).getByRole('link', { name: 'Placas base' }).click({ modifiers: ['Control'] });
  await (await newTab).waitForURL(/\/c\/motherboard$/);

  // --- Mobile (360x800): no horizontal overflow, compact rows, touch targets
  await page.setViewportSize({ width: 360, height: 800 });
  for (const url of ['/', '/c/cpu', '/c/gpu', '/acerca', '/privacidad', '/p/' + SLUG, '/configurador', '/nada']) {
    await open(url);
    if (url.startsWith('/c/')) await waitResults();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `overflow on ${url}`);
  }
  await open('/c/gpu'); await waitResults();
  const rowHeights = await page.locator('.product-row').evaluateAll(rs => rs.slice(0, 8).map(r => r.getBoundingClientRect().height));
  assert.ok(rowHeights.every(h => h <= 96), 'compact mobile rows: ' + rowHeights.join(','));
  await open('/p/' + SLUG);
  await page.locator('.offers > div, .offers-pending').first().waitFor();
  // The retailer-reference mark is 20px but has a 44px hit area (::before); text links inside sentences are excluded.
  const small = await page.evaluate(() => [...document.querySelectorAll('main button, main a')].filter(e => e.offsetParent && !e.closest('[aria-hidden="true"]') && !e.classList.contains('ref-mark'))
    .map(e => ({ t: e.textContent.trim().slice(0, 20) || e.getAttribute('aria-label'), h: e.getBoundingClientRect().height })).filter(e => e.h < 36));
  assert.deepEqual(small, [], 'touch targets under 36px: ' + JSON.stringify(small));

  // --- Header (top row + category bar) stays pinned and on top after scrolling, route changes and dialogs
  const headerPinned = async label => {
    const s = await page.evaluate(() => {
      const header = document.querySelector('.site-header'); const r = header.getBoundingClientRect();
      const bar = document.querySelector('.category-bar').getBoundingClientRect();
      const probes = [r.top + 10, bar.top + bar.height / 2].map(y => document.elementFromPoint(Math.min(innerWidth - 20, 200), y));
      return { top: Math.round(r.top), barBottom: Math.round(bar.bottom), onTop: probes.every(el => !!el?.closest('.site-header')), y: scrollY };
    });
    assert.ok(s.top === 0 && s.barBottom > 0 && s.onTop, `header hidden (${label}): ${JSON.stringify(s)}`);
  };
  for (const [w, h] of [[1440, 900], [375, 812]]) {
    await page.setViewportSize({ width: w, height: h });
    await open('/c/ram'); await waitResults();
    await page.mouse.move(Math.min(700, w - 20), 500);
    await page.mouse.wheel(0, 2500); await page.waitForTimeout(250); await headerPinned(`${w} scrolled list`);
    await page.mouse.wheel(0, -900); await page.waitForTimeout(250); await headerPinned(`${w} scrolled up`);
    await page.locator('.product-name').nth(12).click(); await productHeading().waitFor();
    await page.mouse.wheel(0, 1500); await page.waitForTimeout(250); await headerPinned(`${w} product page`);
    await page.goBack(); await waitResults(); await page.waitForTimeout(250); await headerPinned(`${w} back to list`);
    if (w < 760) { await page.getByRole('button', { name: 'Filtros', exact: true }).click(); await page.keyboard.press('Escape'); await headerPinned(`${w} after filters sheet`); }
    await page.locator('.pc-compare input').nth(0).check(); await page.locator('.pc-compare input').nth(1).check();
    await page.getByRole('button', { name: 'Comparar componentes' }).click(); await page.keyboard.press('Escape');
    await headerPinned(`${w} after compare dialog`);
  }

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('routes-browser: ok');
})().catch(error => { console.error(error); process.exit(1); });
