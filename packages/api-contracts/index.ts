import { z } from 'zod';
import { BUILD_SLOTS } from './builds';

const id = z.string().regex(/^[a-zA-Z0-9_-]{1,100}$/);
const text = z.string().min(1).max(250);
const date = z.string().datetime({ offset: true });
const url = z.string().url().max(2000).refine(v => /^https:\/\//.test(v), 'HTTPS required');
export const payloads = {
  category: z.object({ slug: id, name: text }).strict(),
  shop: z.object({ name: text, url_public: url }).strict(),
  product: z.object({ slug: id, name: text, category_id: id, brand: text,
    specs: z.record(z.string().max(80), z.string().max(250)).refine(v => Object.keys(v).length <= 24),
    manufacturer_url: url.optional(), specs_verified_at: date.optional(), mpn: text.optional(), mpn_source: z.enum(['manufacturer', 'retailer']).optional(),
    image_path: z.string().regex(/^\/images\/products\/[a-zA-Z0-9_/-]+\.(png|jpe?g|webp|svg)$/).optional(),
    image_source_url: url.optional(), image_scope: z.enum(['model','family']).optional(),
    retailer_codes: z.array(z.object({ shop_id: id, code: z.string().min(1).max(100) }).strict()).max(8).optional() }).strict(),
  offer: z.object({ product_id: id, shop_id: id, price_cents: z.number().int().min(0).max(100000000),
    in_stock: z.boolean(), last_checked_at: date, url_public: url }).strict(),
  // One row per shop: end of its last full pass and the offers that pass could NOT confirm (they keep their own last_checked_at).
  shop_check: z.object({ checked_at: date, unconfirmed_offer_ids: z.array(id).max(1000) }).strict()
};
export const reportSchema = z.object({ product_slug: id, offer_id: id.optional(),
  note: z.string().trim().min(1).max(500), page_url: z.string().url().max(500).optional(),
  website: z.string().max(200).optional() }).strict();
export { BUILD_SLOTS, SHARED_BUILD_ID } from './builds';
export const sharedBuildSchema = z.object({ name: z.string().trim().min(1).max(80),
  parts: z.object(Object.fromEntries(BUILD_SLOTS.map(slot => [slot, id.optional()])) as Record<typeof BUILD_SLOTS[number], z.ZodOptional<typeof id>>).strict()
    .refine(parts => Object.values(parts).some(Boolean), 'At least one part') }).strict();
export type Entity = keyof typeof payloads;
const base = { event_id: z.number().int().positive().safe(), entity_id: id,
  version: z.number().int().positive().safe(), changed_at: date };
const upsert = <T extends Entity>(entity: T) => z.object({ ...base, entity: z.literal(entity), operation: z.literal('upsert'), data: payloads[entity] }).strict();
export const eventSchema = z.union([
  upsert('category'), upsert('shop'), upsert('product'), upsert('offer'), upsert('shop_check'),
  z.object({ ...base, entity: z.enum(['category', 'shop', 'product', 'offer']), operation: z.literal('delete') }).strict()
]);
export const batchSchema = z.object({ batch_id: id, schema_version: z.literal(1),
  events: z.array(eventSchema).min(1).max(20) }).strict();
export interface Product {
  id: string; slug: string; name: string; category_id: string; brand: string;
  specs: Record<string, string>; min_price_cents: number | null; offers_count: number;
  last_checked_at: string | null;
  manufacturer_url?: string | null; specs_verified_at?: string | null; mpn?: string | null; mpn_source?: 'manufacturer' | 'retailer' | null;
  image_path?: string | null; image_source_url?: string | null; image_scope?: 'model' | 'family' | null;
  retailer_codes?: { shop_id: string; code: string }[];
}
export interface Offer { id: string; shop_name: string; price_cents: number; in_stock: number;
  last_checked_at: string; url_public: string; }
export interface CatalogStatus {
  data_updated_at: string | null; source_changed_at: string | null; last_event_id: number;
  sync_lag_seconds: number | null; age_seconds: number | null; stale: boolean; data_mode: string;
  offers_checked_at?: string | null;
  /** Optional: public offers and how many of them were checked in the last `offers_fresh_hours` hours (older Workers omit them). */
  offers_total?: number; offers_fresh?: number; offers_fresh_hours?: number;
}
