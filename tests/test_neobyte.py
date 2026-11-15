"""Neobyte observations must fail closed before entering the catalogue."""
import argparse
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'nas'))
from neobyte_local import candidate, Structured, product_url, product_id, inventory_links, run
from import_catalog import canonical_brand
from neobyte_pipeline import validate_review

URL = 'https://www.neobyte.es/asus-dual-rtx-5060-ti-28548.html'


def page(*documents):
    return ''.join('<script type="application/ld+json">' + json.dumps(d) + '</script>' for d in documents)


class NeobyteTests(unittest.TestCase):
    def test_official_case_only_brand_spellings_are_equivalent(self):
        self.assertEqual(canonical_brand('Coolbox'), canonical_brand('CoolBox'))
        self.assertEqual(canonical_brand('Unykach'), canonical_brand('UNYKAch'))
        self.assertNotEqual(canonical_brand('CoolBox'), canonical_brand('Unykach'))

    def product(self, **changes):
        offer = dict(price=588.99, priceCurrency='EUR', availability='https://schema.org/InStock',
                     itemCondition='https://schema.org/NewCondition', seller={'name': 'Neobyte'}, url=URL)
        offer.update(changes)
        p = {'@type': 'Product', 'name': 'Example', 'mpn': 'EXACT-123', 'gtin13': '4711387994290',
             'brand': {'name': 'Asus'}, 'sku': 'TG01AS513', 'offers': offer}
        return page({'@graph': [{'@type': 'WebPage'}, p]})

    def test_production_database_is_refused(self):
        with self.assertRaises(SystemExit):
            run(argparse.Namespace(database_url='postgresql://stockfinder@postgres/stockfinder'))

    def test_pipeline_review_rejects_structured_variant_conflict_before_catalog_lookup(self):
        class FakeConnection:
            def __init__(self):
                self.calls = 0
            def execute(self, *_args):
                self.calls += 1
                class Result:
                    def fetchone(self):
                        return ({'missing_fields':['manufacturer_variant_conflict'],
                                 'identity_status':'retailer_ean_variant_conflict'}, 'pending')
                return Result()
        conn=FakeConnection()
        with self.assertRaisesRegex(ValueError,'unresolved manufacturer variant conflict'):
            validate_review(conn,{'review_id':1,'action':'approve','product_id':'x'},[])
        self.assertEqual(conn.calls,1)

    def test_exact_cents_and_identity(self):
        row = candidate(self.product(), URL, 'gpu', '2026-11-13T12:00:00Z')
        self.assertEqual((row['price_cents'], row['retailer_sku'], row['mpn'], row['shop_id']),
                         (58899, '28548', 'EXACT-123', 'neobyte'))
        self.assertTrue(row['in_stock'])

    def test_rejects_ambiguous_commercial_data(self):
        for changes in [dict(priceCurrency='USD'), dict(price='NaN'), dict(price='1.001'), dict(price=-1),
                        dict(availability='unknown'), dict(seller={'name': 'Marketplace'}),
                        dict(url=URL + '?other=1'), dict(itemCondition='https://schema.org/UsedCondition')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                candidate(self.product(**changes), URL, 'gpu', '2026-11-13T12:00:00Z')

    def test_preorder_is_not_in_stock(self):
        row = candidate(self.product(availability='https://schema.org/PreOrder'), URL, 'gpu', '2026-11-13T12:00:00Z')
        self.assertFalse(row['in_stock'])

    def test_only_exact_neobyte_product_urls(self):
        self.assertTrue(product_url(URL))
        self.assertEqual(product_id(URL), '28548')
        for bad in ['https://www.neobyte.es.evil.example/x-1.html', 'http://www.neobyte.es/x-1.html',
                    'https://www.neobyte.es/x-1.html?a=1', 'https://www.neobyte.es/categoria-111']:
            self.assertFalse(product_url(bad), bad)

    def test_inventory_and_pagination(self):
        listing = {'@type': 'ItemList', 'itemListElement': [{'@type': 'ListItem', 'url': URL}, {'@type': 'ListItem', 'url': 'https://evil.example/x-2.html'}]}
        html = '<link rel="next" href="https://www.neobyte.es/tarjetas-graficas-111?page=2">' + page(listing)
        self.assertEqual(inventory_links(html), [URL])
        self.assertTrue(Structured(html).next.endswith('?page=2'))
        with self.assertRaises(ValueError):
            inventory_links('<html></html>')


if __name__ == '__main__':
    unittest.main()
