/** Public category inventory. Never publishes products or offers. */
const fs = require('node:fs/promises');
const {chromium} = require('playwright');

const categories = [
  ['cpu', 'componentes-pc-procesadores'],
  ['gpu', 'tarjetas-graficas'],
  ['motherboard', 'componentes-pc-placas-base'],
  ['ram', 'componentes-pc-memorias-ram'],
  ['case', 'componentes-pc-torres-cajas'],
  ['psu', 'componentes-pc-fuentes-alimentacion'],
  ['storage', 'componentes-pc-discos-ssd'],
  ['hdd', 'componentes-pc-discos-hdd'],
  ['cooler', 'componentes-pc-disipadores-ventiladores'],
];
const output = process.env.COOLMOD_INVENTORY || '/output/coolmod-inventory.json';
const limit = Number(process.env.COOLMOD_PAGES || 8);
const requested = (process.env.COOLMOD_CATEGORIES || categories.map(c => c[0]).join(',')).split(',');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function save(state) {
  await fs.writeFile(output + '.tmp', JSON.stringify(state, null, 2));
  await fs.rename(output + '.tmp', output);
}

async function nextPage(page) {
  await page.waitForSelector('.paginate-buttons.active-page', {timeout: 20000});
  const controls = await page.locator('.paginate-buttons').evaluateAll(nodes => ({
    current: Number(nodes.find(n => n.classList.contains('active-page'))?.textContent),
    last: Math.max(0, ...nodes.map(n => /^\d+$/.test(n.textContent.trim()) ? Number(n.textContent) : 0)),
  }));
  if (!controls.current || !controls.last) throw new Error('Pagination controls unavailable');
  if (controls.current >= controls.last) return false;
  const old = page.url();
  await page.locator('.next-button').first().click();
  await page.waitForURL(url => url.href !== old, {timeout: 20000});
  await page.waitForFunction(previous => {
    const active = document.querySelector('.paginate-buttons.active-page');
    return (active && Number(active.textContent) > previous && document.querySelector('a[data-itemname]')) ||
      document.body.innerText.includes('No hay productos disponibles actualmente en esta categoría.');
  }, controls.current, {timeout: 20000});
  await delay(2000);
  if (!await page.locator('a[data-itemname]').count()) return null;
  return true;
}

async function main() {
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw new Error('COOLMOD_PAGES must be 1–200');
  if (requested.some(c => !categories.some(([name]) => name === c))) throw new Error('Unknown category');
  let state = {version: 1, categories: {}, candidates: []};
  try { state = JSON.parse(await fs.readFile(output, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (state.version !== 1 || !Array.isArray(state.candidates)) throw new Error('Invalid inventory');
  const seen = new Set(state.candidates.map(c => c.url_public.replace(/\/$/, '')));
  let pages = 0;
  const browser = await chromium.launch();
  try {
    for (const [category, slug] of categories) {
      if (!requested.includes(category)) continue;
      if (pages >= limit) break;
      const progress = state.categories[category] || {last_url: null, pages: 0, complete: false};
      if (progress.complete || progress.exhausted) continue;
      const page = await browser.newPage();
      try {
        const url = progress.last_url || `https://www.coolmod.com/${slug}/`;
        const parsed = new URL(url);
        if (parsed.hostname !== 'www.coolmod.com' || parsed.pathname !== `/${slug}/` || [...parsed.searchParams.keys()].some(k => k !== 'pagina')) throw new Error('Invalid saved page URL');
        const response = await page.goto(url, {waitUntil: 'domcontentloaded', timeout: 45000});
        if (!response || response.status() !== 200 || new URL(page.url()).hostname !== 'www.coolmod.com') throw new Error('Category unavailable');
        await page.locator('a[data-itemname]').first().waitFor({timeout: 20000});
        await delay(2000);
        if (progress.last_url) {
          const next = await nextPage(page);
          if (next !== true) {
            if (next === null) { progress.exhausted = true; progress.end_reason = 'empty_page'; }
            else progress.complete = true;
            state.categories[category] = progress; await save(state); continue;
          }
        }
        while (pages < limit) {
          const cards = await page.locator('a[data-itemname]').evaluateAll(nodes => nodes.map(n => ({
            url: n.href, name: n.dataset.itemname, brand: n.dataset.itembrand,
            listing_id: n.dataset.itemid, listing_category: n.dataset.itemcategory2,
            listing_price: n.dataset.itemprice,
          })));
          if (!cards.length) throw new Error('Empty category page');
          const now = new Date().toISOString();
          let added = 0;
          for (const card of cards) {
            const u = new URL(card.url);
            if (u.hostname !== 'www.coolmod.com' || u.protocol !== 'https:' || !card.name) continue;
            u.search = ''; u.hash = '';
            const key = u.href.replace(/\/$/, '');
            if (seen.has(key)) continue;
            seen.add(key); added++;
            state.candidates.push({shop_id: 'coolmod', retailer_sku: key, url_public: u.href,
              name: card.name.trim(), brand: card.brand || null, mpn: null, ean: null,
              category_hint: category, price_cents: null, in_stock: null, last_checked_at: now,
              missing_fields: ['retailer_sku', 'mpn', 'ean', 'manufacturer_url', 'verified_specifications', 'price', 'stock'],
              raw_properties: [{key: 'listing_id', value: card.listing_id || ''},
                {key: 'listing_category', value: card.listing_category || ''},
                {key: 'listing_price_unverified', value: card.listing_price || ''}]});
          }
          progress.last_url = page.url(); progress.pages++; pages++;
          state.categories[category] = progress;
          await save(state);
          console.log(JSON.stringify({category, page: progress.pages, cards: cards.length, added, total: state.candidates.length}));
          if (pages >= limit) break;
          const next = await nextPage(page);
          if (next !== true) {
            if (next === null) { progress.exhausted = true; progress.end_reason = 'empty_page'; }
            else progress.complete = true;
            await save(state); break;
          }
        }
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify({event: 'inventory_complete', pages, total: state.candidates.length, categories: state.categories}));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
