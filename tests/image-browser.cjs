// Product images: a manufacturer image is shown as-is (160 px thumbnail in list rows); without one (or if it fails) the category placeholder render is
// shown in the same box, without retry loops. Image paths are injected so the test does not depend on the catalogue.
// Usage: TEST_BASE_URL=http://127.0.0.1:8795 CHROMIUM_PATH=... node tests/image-browser.cjs
const { chromium } = require('@playwright/test');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH, args: ['--no-sandbox'] } : {});
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15000);
  const base = process.env.TEST_BASE_URL || 'http://127.0.0.1:8787';
  // Any manufacturer image shipped with the build (file names change as the image run progresses).
  const dir = require('node:path').resolve(__dirname, '../apps/web/dist/images/products/gpu');
  const IMAGE = '/images/products/gpu/' + require('node:fs').readdirSync(dir).find(f => /.(png|jpe?g|webp)$/.test(f));
  let failures = 0;
  await page.route(/\/api\/v1\/products\?/, async route => {
    const response = await route.fetch(); const body = await response.json();
    body.data.forEach((item, i) => { item.image_path = i === 0 ? IMAGE : null; item.image_scope = i === 0 ? 'model' : null; });
    await route.fulfill({ response, json: body });
  });
  const firstImg = page.locator('.product-row').first().locator('.component-photo');
  await page.goto(`${base}/c/gpu`, { waitUntil: 'networkidle' });
  await page.waitForFunction(() => { const img = document.querySelector('.product-row .component-photo'); return img?.complete && img.naturalWidth > 0 && img.dataset.imageOrigin === 'model'; });
  assert.equal(await firstImg.getAttribute('src'), IMAGE);
  const second = page.locator('.product-row').nth(1).locator('.component-photo');
  assert.equal(await second.getAttribute('data-image-origin'), 'placeholder');
  assert.ok((await second.getAttribute('src')).endsWith('/images/placeholders/gpu-400.webp'));
  // List rows load the 160 px thumbnail (scripts/make_thumbnails.py) instead of the full photo
  const THUMB = IMAGE.replace('/images/products/', '/images/thumbs/').replace(/\.\w+$/, '.webp');
  if (require('node:fs').existsSync(require('node:path').resolve(__dirname, '../apps/web/dist' + THUMB)))
    assert.ok((await firstImg.evaluate(img => img.currentSrc)).endsWith(THUMB), 'row uses the thumbnail');
  const boxes = await page.evaluate(() => [...document.querySelectorAll('.product-row .thumb')].slice(0, 2).map(t => { const b = t.getBoundingClientRect(); return [Math.round(b.width), Math.round(b.height)]; }));
  assert.deepEqual(boxes[0], boxes[1], 'photo and placeholder share the same box');
  await page.route(url => url.pathname === IMAGE || url.pathname === THUMB, route => { failures++; return route.abort(); });
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForFunction(() => { const img = document.querySelector('.product-row .component-photo'); return img?.complete && img.naturalWidth > 0 && img.dataset.imageOrigin === 'placeholder'; });
  assert.ok((await firstImg.getAttribute('src')).endsWith('/images/placeholders/gpu-400.webp'));
  assert.ok(failures >= 1 && failures <= 2, `thumbnail then photo, each tried once (${failures})`);
  await browser.close(); console.log('Image QA passed: manufacturer image, placeholder in the same box, simulated failure without retry loop.');
})().catch(error => { console.error(error); process.exit(1); });
