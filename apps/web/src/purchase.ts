export interface BuyOffer { id: string; shop_name: string; price_cents: number; in_stock: number | boolean; url_public: string; last_checked_at: string }
export interface PurchaseLine { id: string; name: string; offers: BuyOffer[] }
export interface PurchasePlan { name: string; lines: { name: string; offer: BuyOffer }[]; missing: string[]; subtotal: number; shops: string[] }
/** Shipping is applied once per order/shop, never once per component. Missing prices never count as zero. */
export function purchasePlans(products: PurchaseLine[]): PurchasePlan[] {
  const available = products.map(p => ({ ...p, offers: p.offers.filter(o => o.in_stock && Date.now() - Date.parse(o.last_checked_at) <= 86400000 && Number.isSafeInteger(o.price_cents) && o.price_cents > 0).sort((a,b) => a.price_cents-b.price_cents || a.id.localeCompare(b.id)) }));
  const shops = [...new Set(available.flatMap(p => p.offers.map(o => o.shop_name)))].sort();
  return [null, ...shops].map(shop => {
    const lines: PurchasePlan['lines'] = [], missing: string[] = [];
    for (const p of available) {
      const offer = p.offers.find(o => shop === null || o.shop_name === shop);
      if (offer) lines.push({ name: p.name, offer }); else missing.push(p.name);
    }
    return { name: shop ?? 'Menor subtotal entre tiendas', lines, missing, subtotal: lines.reduce((n,l) => n+l.offer.price_cents,0), shops: [...new Set(lines.map(l => l.offer.shop_name))] };
  });
}
export function enteredCents(value: string): number | null {
  if (!/^\d{1,6}(?:[.,]\d{1,2})?$/.test(value.trim())) return null;
  return Math.round(Number(value.replace(',','.'))*100);
}
export function landedTotal(plan: PurchasePlan, costs: Record<string, { shipping: string; adjustment: string }>): number | null {
  if (plan.missing.length || !plan.lines.length) return null;
  let total = plan.subtotal;
  for (const shop of plan.shops) {
    const shipping = enteredCents(costs[shop]?.shipping ?? ''), adjustment = enteredCents(costs[shop]?.adjustment ?? '');
    if (shipping === null || adjustment === null) return null;
    total += shipping + adjustment;
  }
  return total;
}
