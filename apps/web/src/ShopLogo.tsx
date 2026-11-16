/** Retailer logos for the product page. Self-hosted (the CSP only allows `img-src 'self'`); shops without a logo keep their name. */
const LOGOS: Record<string, { src: string; ratio: number }> = {
  coolmod: { src: '/images/shops/coolmod.png', ratio: 762 / 166 },
  pccomponentes: { src: '/images/shops/pccomponentes.svg', ratio: 138 / 48 },
  ldlc: { src: '/images/shops/ldlc.svg', ratio: 338 / 135 },
};
const HEIGHT = 24;

export function ShopName({ name, className = '' }: { name: string; className?: string }) {
  const logo = LOGOS[name.toLowerCase().replace(/[^a-z0-9]/g, '')];
  if (!logo) return <strong className={className}>{name}</strong>;
  // The name stays available to screen readers and as alt text; the chip keeps dark logos readable in any theme.
  return <span className={`shop-logo ${className}`.trim()}><img src={logo.src} alt={name} width={Math.round(HEIGHT * logo.ratio)} height={HEIGHT} decoding="async"/></span>;
}
