import { readFileSync, readdirSync } from 'node:fs';
import { describe, test, expect } from 'vitest';
import { payloads } from '../packages/api-contracts/index';

const products = readdirSync('data/catalog').filter(file => file.endsWith('.json')).flatMap(file => JSON.parse(readFileSync(`data/catalog/${file}`, 'utf8')));
const offers = JSON.parse(readFileSync('data/retailers/pilot.json', 'utf8'));
// A manufacturer_url belongs to a brand when the registrable domain label matches the brand
// (rog.asus.com / estore.asus.com for ASUS) or data/brand-domains.json lists a documented extra
// domain (e.g. PcComponentes for its own brands). Same rule as scripts/validate_batch.py.
const extraDomains: Record<string, string[]> = Object.fromEntries(Object.entries(JSON.parse(readFileSync('data/brand-domains.json', 'utf8')) as Record<string, string[] | string>).filter(([key]) => !key.startsWith('_')) as [string, string[]][]);
const TWO_LEVEL_SUFFIXES = ['com.tw', 'com.tr', 'co.uk', 'com.cn', 'co.jp', 'com.br', 'com.au'];
const alnum = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, '');
function registrable(host: string) {
  const labels = host.toLowerCase().split('.');
  if (labels.length >= 3 && TWO_LEVEL_SUFFIXES.includes(labels.slice(-2).join('.'))) return { domain: labels.slice(-3).join('.'), label: labels.at(-3)! };
  return { domain: labels.slice(-2).join('.'), label: labels.at(-2) ?? labels[0] };
}
function hostBelongsToBrand(brand: string, host: string) {
  const key = alnum(brand); const { domain, label } = registrable(host);
  if ((extraDomains[key] ?? []).some(extra => domain === extra || domain.endsWith(`.${extra}`))) return true;
  const slug = alnum(label);
  return slug.length >= 3 && key.length >= 2 && (slug.includes(key) || key.includes(slug));
}
describe('Manufacturer provenance and exact retailer matching', () => {
  test('manufacturer host rule rejects another brand, retailers and social networks', () => {
    expect(hostBelongsToBrand('ASUS', 'rog.asus.com')).toBe(true);
    expect(hostBelongsToBrand('Kingston Technology', 'www.kingston.com')).toBe(true);
    expect(hostBelongsToBrand('Forgeon', 'www.pccomponentes.com')).toBe(true);
    expect(hostBelongsToBrand('MSI', 'www.asus.com')).toBe(false);
    expect(hostBelongsToBrand('Corsair', 'www.pccomponentes.com')).toBe(false);
    expect(hostBelongsToBrand('Samsung', 'www.linkedin.com')).toBe(false);
  });
  test('all public records satisfy strict contracts and are attributed to the correct manufacturer', () => {
    // Review evidence stays in the local batches; nas/import_catalog.py never sends it to the API.
    for (const { id, manufacturer_sources: _sources, manufacturer_evidence: _evidence, verification_review_id: _review, ...product } of products) {
      expect(id).toMatch(/^[a-zA-Z0-9_-]{1,100}$/);
      expect(payloads.product.safeParse(product).success, id).toBe(true);
      const host = new URL(product.manufacturer_url).hostname;
      expect(hostBelongsToBrand(product.brand, host), `${id}: ${product.brand} → ${host}`).toBe(true);
      expect(Number.isFinite(Date.parse(product.specs_verified_at))).toBe(true);
    }
  });
  test('canonical identities, slugs, model names and known MPNs are unique', () => {
    for (const key of ['id', 'slug']) expect(new Set(products.map(p => p[key])).size).toBe(products.length);
    expect(new Set(products.map(p => `${p.brand}:${p.name.toLowerCase()}`)).size).toBe(products.length);
    const knownMpn = products.filter(p => p.mpn).map(p => `${p.brand}:${p.mpn.toLowerCase()}`);
    expect(new Set(knownMpn).size).toBe(knownMpn.length);
  });
  test('every checked pilot offer matches an exact official MPN, not a fuzzy product name', () => {
    const skus = new Set();
    for (const offer of offers) {
      const product = products.find(p => p.id === offer.product_id);
      expect(product, offer.product_id).toBeTruthy();
      expect(offer.mpn).toBe(product.mpn);
      expect(payloads.offer.safeParse(Object.fromEntries(['product_id','shop_id','price_cents','in_stock','last_checked_at','url_public'].map(k => [k,offer[k]]))).success).toBe(true);
      const identity = `${offer.shop_id}:${offer.retailer_sku}`;
      expect(skus.has(identity)).toBe(false); skus.add(identity);
      expect(offer.match_evidence.length).toBeGreaterThan(0);
    }
  });
  test('manufacturer images reference real products and safe local image files, with explicit scope', () => {
    const identities = new Set();
    const productById = new Map(products.map(product => [product.id, product]));
    for (const file of readdirSync('data/images').filter(file => file.endsWith('.json'))) {
      for (const image of JSON.parse(readFileSync(`data/images/${file}`, 'utf8'))) {
        expect(identities.has(image.product_id)).toBe(false); identities.add(image.product_id);
        const product = productById.get(image.product_id);
        expect(product).toBeTruthy();
        // Local provenance fields are stripped before the strict API, as in nas/import_catalog.py and the test above.
        const { id, manufacturer_sources: _sources, manufacturer_evidence: _evidence, verification_review_id: _review, ...data } = product;
        expect(payloads.product.safeParse({ ...data, image_path: image.image_path, image_source_url: image.image_source_url, image_scope: image.image_scope }).success).toBe(true);
        const content = readFileSync('apps/web/public'+image.image_path);
        expect(content.length).toBeGreaterThan(100);
        expect(content.subarray(0,200).toString('utf8')).not.toMatch(/<!doctype html|<html/i);
        if (image.image_path.endsWith('.svg')) expect(content.toString('utf8')).not.toMatch(/<script|<foreignObject|on\w+\s*=|javascript:/i);
      }
    }
  });
});
