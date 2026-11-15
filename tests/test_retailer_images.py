"""Pruebas de scripts/fetch_retailer_images.py (sin red): selección de objetivos, hosts aceptados, formato de registros."""
import argparse, io, json, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import fetch_retailer_images as fri  # noqa: E402
import fetch_product_images as fpi  # noqa: E402
import catalog_images  # noqa: E402

def args(**kw):
    base = dict(ids=None, shop=None, retry_skipped=False, retry_failed=False, dry_run=False)
    return argparse.Namespace(**{**base, **kw})

PRODUCTS = [
    {'id': 'a', 'category_id': 'gpu', 'brand': 'X', 'name': 'X a', 'manufacturer_url': 'https://x.com/a'},   # unprocessed by manufacturer
    {'id': 'b', 'category_id': 'gpu', 'brand': 'X', 'name': 'X b'},                                         # no manufacturer URL
    {'id': 'c', 'category_id': 'gpu', 'brand': 'X', 'name': 'X c', 'manufacturer_url': 'https://x.com/c'},  # manufacturer ok
    {'id': 'd', 'category_id': 'gpu', 'brand': 'X', 'name': 'X d', 'manufacturer_url': 'https://x.com/d'},  # curated image
    {'id': 'e', 'category_id': 'gpu', 'brand': 'X', 'name': 'X e'},                                         # no approved listing
    {'id': 'f', 'category_id': 'gpu', 'brand': 'X', 'name': 'X f', 'manufacturer_url': 'https://x.com/f'},  # manufacturer skip
]
ROWS = [{'shop_id': s, 'retailer_sku': f'{s}-{p}', 'product_id': p, 'url_public': f'https://www.{s}.com/{p}'}
        for p in 'abcdf' for s in ('pccomponentes', 'coolmod')] + [
        {'shop_id': 'amazon', 'retailer_sku': '1', 'product_id': 'b', 'url_public': 'https://amazon.es/b'},
        {'shop_id': 'coolmod', 'retailer_sku': '2', 'product_id': 'zzz', 'url_public': 'https://www.coolmod.com/zzz'}]
MFR = {'c': {'status': 'ok'}, 'f': {'status': 'skip', 'reason': 'no_image'}}

class Targets(unittest.TestCase):
    def test_manufacturer_images_are_never_targets_and_order(self):
        todo = fri.select_targets(PRODUCTS, ROWS, {'d'}, MFR, {'products': {}}, args())
        self.assertEqual([t['product']['id'] for t in todo], ['b', 'f', 'a'])   # c (mfr ok) and d (curated) excluded; unprocessed last
        self.assertEqual(set(todo[0]['listings']), {'pccomponentes', 'coolmod'})  # unknown shop / unknown product ignored

    def test_shop_filter_state_and_existing_file(self):
        state = {'products': {'b': {'status': 'ok'}, 'f': {'status': 'skip', 'reason': 'no_image'}}}
        self.assertEqual([t['product']['id'] for t in fri.select_targets(PRODUCTS, ROWS, {'d'}, MFR, state, args())], ['a'])
        self.assertEqual([t['product']['id'] for t in fri.select_targets(PRODUCTS, ROWS, {'d'}, MFR, state, args(retry_skipped=True))], ['f', 'a'])
        self.assertEqual(set(fri.select_targets(PRODUCTS, ROWS, {'d'}, MFR, {'products': {}}, args(shop='coolmod'))[0]['listings']), {'coolmod'})
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'images/products/gpu').mkdir(parents=True); (Path(tmp)/'images/products/gpu/a.webp').write_bytes(b'x')
            ids = [t['product']['id'] for t in fri.select_targets(PRODUCTS, ROWS, {'d'}, MFR, {'products': {}}, args(), tmp)]
            self.assertNotIn('a', ids)   # a file not created by us is never overwritten

    def test_curated_ids_ignore_retailer_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp)/'data/images'; images.mkdir(parents=True)
            (images/'gpus.json').write_text(json.dumps([{'product_id': 'd'}]))
            (images/'manufacturer-2026-11-08.json').write_text(json.dumps([{'product_id': 'c'}]))
            (images/'retailer-2026-11-08.json').write_text(json.dumps([{'product_id': 'a'}]))
            self.assertEqual(fri.curated_image_ids(tmp), {'c', 'd'})

class Hosts(unittest.TestCase):
    def test_shop_hosts(self):
        self.assertTrue(fri.host_ok('https://thumb.pccomponentes.com/w-530-530/a.jpg', 'pccomponentes')[0])
        self.assertTrue(fri.host_ok('https://www.coolmod.com/images/product/large/PROD-1_1.jpg', 'coolmod')[0])
        self.assertEqual(fri.host_ok('https://thumb.pccomponentes.com/a.jpg', 'pccomponentes')[1], 'thumb.pccomponentes.com')
        for url in ['http://thumb.pccomponentes.com/a.jpg', 'https://pccomponentes.com.evil.net/a.jpg', 'https://cdn.shopify.com/a.jpg', 'https://www.coolmod.com.x.io/a.jpg']:
            self.assertFalse(fri.host_ok(url, 'pccomponentes')[0] and fri.host_ok(url, 'coolmod')[0], url)
        self.assertFalse(fri.host_ok('https://www.coolmod.com/a.jpg', 'pccomponentes')[0])   # cross-shop host rejected

    def test_pcc_image_prefers_large(self):
        art = {'images': {'small': {'path': 's'}, 'large': {'path': 'L'}, 'medium': {'path': 'M'}}}
        self.assertEqual(fri.pcc_image_url(art), 'L')
        self.assertEqual(fri.pcc_image_url({'images': {'small': {'path': 's'}}}), 's')
        self.assertIsNone(fri.pcc_image_url({}))

    def test_coolmod_page_image(self):
        html = ('<meta property="og:image" content="https://www.coolmod.com/images/product/thumb/PROD-1_1.jpg">'
                '<script type="application/ld+json">{"@type":"Product","sku":"PROD-1","image":"https://www.coolmod.com/images/product/large/PROD-1_1.jpg"}</script>')
        picked, reason = fri.coolmod_image_url(fpi.parse_page(html), 'https://www.coolmod.com/x/', 'PROD-1')
        self.assertEqual(picked, ('https://www.coolmod.com/images/product/large/PROD-1_1.jpg', 'jsonld'))
        self.assertEqual(fri.coolmod_image_url(fpi.parse_page(html), 'https://www.coolmod.com/x/', 'PROD-2'), (None, 'sku_mismatch'))
        foreign = '<meta property="og:image" content="https://cdn.other.com/a.jpg">'
        self.assertEqual(fri.coolmod_image_url(fpi.parse_page(foreign), 'https://www.coolmod.com/x/', 'PROD-1'), (None, 'image_host_not_official'))
        icon = '<meta name="twitter:image" content="https://www.coolmod.com/images/favicon/icon-coolmod-200x200.jpg"><meta property="og:image" content="https://www.coolmod.com/images/favicon/icon-coolmod-200x200.jpg">'
        self.assertEqual(fri.coolmod_image_url(fpi.parse_page(icon), 'https://www.coolmod.com/x/', 'PROD-1')[0], None)

def ok_entry(pid, url='https://thumb.pccomponentes.com/a.jpg', shop='pccomponentes', sha='h'):
    return {'status': 'ok', 'shop': shop, 'image_path': f'/images/products/gpu/{pid}.webp', 'image_url': url, 'page_url': f'https://www.pccomponentes.com/{pid}',
            'image_host': 'thumb.pccomponentes.com', 'attribution': 'PcComponentes' if shop == 'pccomponentes' else 'Coolmod', 'retailer_sku': '1',
            'sha256': sha, 'fetched_at': '2026-11-08T00:00:00Z'}

class Records(unittest.TestCase):
    def test_record_format(self):
        state = {'date': '2026-11-08', 'products': {'a': ok_entry('a', 'https://thumb.pccomponentes.com/1.jpg'), 'b': ok_entry('b', 'https://www.coolmod.com/2.jpg', 'coolmod'),
                                                     'c': {'status': 'skip', 'reason': 'no_image'}}}
        records = fri.build_manifest(state)
        self.assertEqual([r['product_id'] for r in records], ['a', 'b'])
        r = records[0]
        self.assertEqual((r['image_origin'], r['attribution'], r['image_scope']), ('retailer', 'PcComponentes', 'model'))
        for key in ('image_path', 'image_source_url', 'image_page_url', 'fetched_at', 'image_host', 'rights'): self.assertTrue(r[key], key)
        self.assertEqual(records[1]['attribution'], 'Coolmod')

    def test_generic_and_superseded_images_dropped(self):
        shared = {pid: ok_entry(pid, 'https://thumb.pccomponentes.com/same.jpg') for pid in 'abc'}
        self.assertEqual(fri.build_manifest({'products': shared}), [])   # same image on >=3 products
        state = {'products': {'a': ok_entry('a'), 'b': ok_entry('b', 'https://thumb.pccomponentes.com/b.jpg')}}
        self.assertEqual([r['product_id'] for r in fri.build_manifest(state, None, {'a'}, {})], ['b'])   # a got a curated image meanwhile
        self.assertEqual([r['product_id'] for r in fri.build_manifest(state, None, set(), {'a': {'status': 'ok'}})], ['b'])   # ... or a manufacturer one

    def test_file_replaced_by_manufacturer_is_dropped(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp)/'apps/web/public/images/products/gpu/a.webp'; f.parent.mkdir(parents=True); f.write_bytes(b'retailer')
            import hashlib
            state = {'products': {'a': ok_entry('a', sha=hashlib.sha256(b'retailer').hexdigest())}}
            self.assertEqual(len(fri.build_manifest(state, tmp)), 1)
            f.write_bytes(b'manufacturer')
            self.assertEqual(fri.build_manifest(state, tmp), [])

    def test_save_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fri.save_webp(tmp, PRODUCTS[0], b'one'), '/images/products/gpu/a.webp')
            self.assertIsNone(fri.save_webp(tmp, PRODUCTS[0], b'two'))
            self.assertEqual((Path(tmp)/'apps/web/public/images/products/gpu/a.webp').read_bytes(), b'one')

class Precedence(unittest.TestCase):
    def test_manufacturer_beats_retailer_in_any_file_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp)/'data/images'; images.mkdir(parents=True)
            def rec(src, **extra): return {'product_id': 'a', 'image_path': '/images/products/gpu/a.webp', 'image_source_url': src, 'image_scope': 'family', **extra}
            retailer = rec('https://thumb.pccomponentes.com/a.jpg', image_origin='retailer', attribution='PcComponentes')
            products = [{'id': 'a'}, {'id': 'b'}]
            for name in ('aaa-retailer.json', 'zzz-retailer.json'):   # before and after the manufacturer file alphabetically
                for f in images.glob('*.json'): f.unlink()
                (images/name).write_text(json.dumps([retailer]))
                (images/'manufacturer-2026-11-08.json').write_text(json.dumps([rec('https://x.com/a.jpg')]))
                out = {p['id']: p for p in catalog_images.enrich_images(products, tmp)}
                self.assertEqual(out['a']['image_source_url'], 'https://x.com/a.jpg')
                self.assertEqual(out['a']['image_scope'], 'family')
            for f in images.glob('*.json'): f.unlink()
            (images/'retailer-2026-11-08.json').write_text(json.dumps([retailer]))
            out = {p['id']: p for p in catalog_images.enrich_images(products, tmp)}
            self.assertEqual(out['a']['image_source_url'], 'https://thumb.pccomponentes.com/a.jpg')   # retailer used when nothing else
            self.assertNotIn('image_path', out['b'])
            (images/'retailer-2026-11-09.json').write_text(json.dumps([retailer]))
            with self.assertRaises(ValueError): catalog_images.enrich_images(products, tmp)   # duplicate retailer records still rejected

class Flow(unittest.TestCase):
    def test_pcc_failure_falls_back_to_coolmod_and_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            calls = []
            class Fake:
                throttle = fri.Gap(0)
                class session:
                    @staticmethod
                    def get(url, **kw):
                        calls.append(url)
                        class R:
                            status_code = 200; text = ''
                            @staticmethod
                            def json(): return {'articles': [{'id': 'pccomponentes-a', 'images': {'large': {'path': 'https://thumb.pccomponentes.com/a.jpg'}}}]}
                        return R()
                def get(self, url, binary=False):
                    calls.append(url)
                    return 200, 'text/html', ('<script type="application/ld+json">{"@type":"Product","sku":"coolmod-b",'
                                              '"image":"https://www.coolmod.com/images/product/large/b.jpg"}</script>'), url
            fake = Fake()
            state = {'date': '2026-11-08', 'products': {}, 'shops': {}}
            runner = fri.Runner(tmp, state, args(dry_run=True), {'pccomponentes': fake, 'coolmod': fake})
            robots = type('R', (), {'allowed': lambda self, u: True})()
            orig = fpi.Robots; fpi.Robots = lambda f: robots
            try:
                todo = fri.select_targets(PRODUCTS, ROWS, {'d'}, MFR, state, args())
                runner.run([t for t in todo if t['product']['id'] in ('a', 'b')])
            finally: fpi.Robots = orig
            self.assertEqual(state['products'], {})   # dry-run: state untouched
            self.assertFalse((Path(tmp)/'apps').exists())
            self.assertEqual(runner.progress['done'], 2)

if __name__ == '__main__': unittest.main()
