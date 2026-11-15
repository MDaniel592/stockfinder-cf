"""Pruebas de extracción/aceptación de scripts/fetch_product_images.py (sin red)."""
import io, json, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import fetch_product_images as fpi  # noqa: E402

PAGE = '''<html><head><title>TUF GAMING B850-PLUS WIFI | Motherboards | ASUS USA</title>
<meta property="og:image" content="/img/og-board.png"><meta property="og:title" content="TUF GAMING B850-PLUS WIFI">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Organization","image":"https://x.com/logo.png"},
{"@type":"Product","name":"TUF Gaming B850-Plus WiFi","sku":"90MB1ABC","image":["https://dlcdnwebimgs.asus.com/a.jpg",{"url":"https://dlcdnwebimgs.asus.com/b.jpg"}]}]}</script>
</head><body><h1>TUF GAMING B850-PLUS WIFI</h1><img class="product-gallery" src="/g1.jpg" width="600"><img class="x" src="/other.jpg"></body></html>'''
PRODUCT = {'id': 'asus-tuf', 'brand': 'ASUS', 'name': 'ASUS TUF Gaming B850-Plus WiFi', 'category_id': 'motherboard'}

class Extraction(unittest.TestCase):
    def test_candidate_order_and_resolution(self):
        c = fpi.extract_candidates(fpi.parse_page(PAGE), 'https://www.asus.com/us/p/')
        self.assertEqual([k for k, _ in c], ['jsonld', 'jsonld', 'og', 'gallery'])
        self.assertEqual(c[0][1], 'https://dlcdnwebimgs.asus.com/a.jpg')
        self.assertEqual(c[2][1], 'https://www.asus.com/img/og-board.png')
        self.assertNotIn('logo', ' '.join(u for _, u in c))  # Organization image is not a Product image

    def test_non_https_and_data_uris_dropped(self):
        html = '<meta property="og:image" content="http://a.com/x.jpg"><meta name="twitter:image" content="data:image/png;base64,AA">'
        self.assertEqual(fpi.extract_candidates(fpi.parse_page(html), 'https://a.com/'), [])

    def test_bad_urls(self):
        for url in ['https://a.com/img/logo.png', 'https://a.com/favicon.ico', 'https://a.com/x/placeholder-1.png', 'https://a.com/a.svg', 'https://a.com/default.jpg']:
            self.assertTrue(fpi.bad_image_url(url), url)
        self.assertFalse(fpi.bad_image_url('https://a.com/sites/default/files/board.jpg'))
        self.assertFalse(fpi.bad_image_url('https://a.com/products/blogosphere.jpg'))

class Hosts(unittest.TestCase):
    def test_official_hosts(self):
        bd = {'forgeon': ['pccomponentes.com'], 'crucial': ['micron.com']}
        self.assertTrue(fpi.host_official('https://dlcdnwebimgs.asus.com/a.jpg', 'https://www.asus.com/p', 'ASUS')[0])
        self.assertTrue(fpi.host_official('https://img.pccomponentes.com/a.jpg', 'https://www.pccomponentes.com/p', 'Forgeon', bd)[0])
        self.assertTrue(fpi.host_official('https://assets.micron.com/a.jpg', 'https://www.crucial.com/p', 'Crucial', bd)[0])
        self.assertFalse(fpi.host_official('https://cdn.shopify.com/a.jpg', 'https://www.asus.com/p', 'ASUS', bd)[0])
        self.assertFalse(fpi.host_official('https://asus.com.evil.net/a.jpg', 'https://www.asus.com/p', 'ASUS')[0])
        self.assertEqual(fpi.registrable('shop.example.co.uk'), 'example.co.uk')

class Scope(unittest.TestCase):
    def test_exact_model(self):
        self.assertTrue(fpi.page_is_exact_model(PRODUCT, fpi.parse_page(PAGE), 'https://www.asus.com/p/b850-plus-wifi/'))

    def test_family_when_variant_token_missing(self):
        product = {**PRODUCT, 'name': 'ASUS TUF Gaming B850-Plus WiFi7 Ice'}
        self.assertFalse(fpi.page_is_exact_model(product, fpi.parse_page(PAGE), 'https://www.asus.com/p/'))

    def test_mpn_match_and_pdf_qvl(self):
        page = fpi.parse_page('<title>Vengeance DDR5 CMK32GX5M2B6000Z30</title>')
        product = {'id': 'c', 'brand': 'Corsair', 'name': 'Corsair Vengeance 32 GB DDR5-6000 CL30', 'mpn': 'CMK32GX5M2B6000Z30'}
        self.assertTrue(fpi.page_is_exact_model(product, page, 'https://www.corsair.com/p'))
        self.assertFalse(fpi.page_is_exact_model(product, page, 'https://www.corsair.com/x.pdf'))
        self.assertFalse(fpi.page_is_exact_model(product, page, 'https://www.gskill.com/qvl/165/374'))

    def test_generic_page_is_family(self):
        self.assertFalse(fpi.page_is_exact_model(PRODUCT, fpi.parse_page('<title>Motherboards | ASUS</title>'), 'https://www.asus.com/motherboards/'))

class Challenges(unittest.TestCase):
    def test_challenge_detection(self):
        self.assertTrue(fpi.is_challenge(200, '<html>Incapsula incident ID</html>'))
        self.assertTrue(fpi.is_challenge(200, '<title>Just a moment...</title>'))
        self.assertTrue(fpi.is_challenge(429, ''))
        self.assertFalse(fpi.is_challenge(200, '<html>' + 'x' * 40000 + 'captcha</html>'))
        self.assertFalse(fpi.is_challenge(200, '<html><title>Product</title></html>'))

class Conversion(unittest.TestCase):
    def test_webp_size_and_aspect(self):
        try: from PIL import Image
        except ImportError: self.skipTest('Pillow not available')
        buf = io.BytesIO(); Image.linear_gradient('L').resize((1600, 1000)).convert('RGB').save(buf, 'PNG')
        webp, w, h = fpi.to_webp(buf.getvalue())
        self.assertEqual(max(w, h), 800); self.assertAlmostEqual(w / h, 1.6, places=1)
        self.assertEqual(webp[:4], b'RIFF'); self.assertLessEqual(len(webp), 80_000)
        tiny = io.BytesIO(); Image.new('RGB', (100, 100)).save(tiny, 'PNG')
        with self.assertRaises(ValueError): fpi.to_webp(tiny.getvalue())

class Flow(unittest.TestCase):
    def make_root(self, tmp):
        root = Path(tmp)
        (root/'data/catalog').mkdir(parents=True); (root/'data/images').mkdir(parents=True)
        products = [{'id': f'p{i}', 'slug': f'p{i}', 'name': f'N{i}', 'brand': 'ASUS', 'category_id': 'gpu', 'manufacturer_url': f'https://www.asus.com/{i}',
                     'specs_verified_at': '2026-11-01', 'specs': {}} for i in range(4)]
        (root/'data/catalog/a.json').write_text(json.dumps(products))
        (root/'data/images/gpus.json').write_text(json.dumps([{'product_id': 'p0', 'image_path': '/images/products/gpu/p0.jpg', 'image_source_url': 'https://a/x.jpg', 'image_scope': 'model'}]))
        return root, products

    def test_pending_skips_existing_and_done_and_stopped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, products = self.make_root(tmp)
            args = type('A', (), dict(ids=None, brand=None, category=None, retry_skipped=False, retry_failed=False))()
            state = {'products': {'p1': {'status': 'ok'}, 'p2': {'status': 'skip', 'reason': 'no_image'}}, 'domains': {}}
            have = fpi.existing_image_ids(root)
            self.assertEqual([p['id'] for p in fpi.pending_products(products, have, state, args)], ['p3'])
            args.retry_skipped = True
            self.assertEqual([p['id'] for p in fpi.pending_products(products, have, state, args)], ['p2', 'p3'])
            state['domains']['asus.com'] = {'stopped': True}
            self.assertEqual(fpi.pending_products(products, have, state, args), [])

    def test_manifest_drops_generic_images_and_enriches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, products = self.make_root(tmp)
            def ok(pid, img, page):
                return {'status': 'ok', 'image_path': f'/images/products/gpu/{pid}.webp', 'image_url': img, 'page_url': page,
                        'scope': 'family', 'attribution': 'ASUS', 'fetched_at': 'x', 'image_host': 'a'}
            state = {'date': '2026-11-08', 'domains': {}, 'products': {
                'p1': ok('p1', 'https://a/g.jpg', 'u1'), 'p2': ok('p2', 'https://a/g.jpg', 'u2'), 'p3': ok('p3', 'https://a/g.jpg', 'u3')}}
            self.assertEqual(fpi.build_manifest(state), [])  # same image on 3 different pages = generic
            state['products']['p3'] = ok('p3', 'https://a/own.jpg', 'u3')
            records = fpi.build_manifest(state)
            self.assertEqual([r['product_id'] for r in records], ['p1', 'p2', 'p3'])
            (root/'data/images/manufacturer-2026-11-08.json').write_text(json.dumps(records))
            import catalog_images
            enriched = {p['id']: p for p in catalog_images.enrich_images(products, root)}
            self.assertEqual(enriched['p3']['image_scope'], 'family')
            self.assertEqual(enriched['p3']['image_path'], '/images/products/gpu/p3.webp')
            self.assertNotIn('attribution', enriched['p3'])

class Concurrency(unittest.TestCase):
    def run_fake(self, blocked_domain=None):
        import time, threading
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            todo = [{'id': f'd{d}-{i}', 'brand': 'X', 'category_id': 'gpu', 'manufacturer_url': f'https://www.site{d}.com/{i}'} for d in range(4) for i in range(8)]
            state = {'date': '2026-11-08', 'domains': {}, 'products': {}}
            active, peak, lock = set(), [0], threading.Lock()
            def fake(product, fetcher, robots, bd, root_, dry):
                dom = product['id'].split('-')[0]
                with lock: active.add(dom); peak[0] = max(peak[0], len(active))
                time.sleep(0.01)
                with lock: active.discard(dom)
                if dom == blocked_domain: raise fpi.Blocked('challenge')
                return {'status': 'skip', 'reason': 'no_image'}
            original = fpi.process; fpi.process = fake
            try:
                args = type('A', (), dict(workers=4, dry_run=False))()
                progress = {'done': 0, 'started': time.monotonic()}
                fpi.run_all(todo, state, root, args, {}, {'now': False}, progress, lambda: object())
            finally: fpi.process = original
            saved = json.loads((root/fpi.STATE_DIR/'manufacturer-progress.json').read_text())  # always valid JSON
            return state, saved, peak[0], progress

    def test_parallel_state_is_consistent(self):
        state, saved, peak, progress = self.run_fake()
        self.assertEqual(len(state['products']), 32); self.assertEqual(len(saved['products']), 32); self.assertEqual(progress['done'], 32)
        self.assertGreater(peak, 1)  # domains really ran concurrently

    def test_blocked_domain_stops_only_itself(self):
        state, saved, _, _ = self.run_fake(blocked_domain='d1')
        self.assertTrue(saved['domains']['site1.com']['stopped'])
        self.assertEqual(sum(1 for k in state['products'] if k.startswith('d1-')), fpi.DOMAIN_STOP_AFTER)
        self.assertEqual(sum(1 for k in state['products'] if k.startswith('d0-')), 8)

if __name__ == '__main__': unittest.main()
