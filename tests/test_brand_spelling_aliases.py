"""Focused brand spelling checks that do not need a PostgreSQL fixture."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'nas'), str(ROOT / 'scripts')]

from import_catalog import canonical_brand
from neobyte_pipeline import validate_review


class BrandSpellingAliasTests(unittest.TestCase):
    def test_exact_brand_spellings_and_neobyte_fractal_review(self):
        self.assertEqual(canonical_brand('Fractal'), 'Fractal Design')
        self.assertNotEqual(canonical_brand('Fractal Gaming Group'), 'Fractal Design')
        self.assertEqual(canonical_brand('Tooq'), 'TooQ')
        self.assertEqual(canonical_brand('Tryx'), 'TRYX')
        self.assertEqual(canonical_brand('TRYX'), 'TRYX')
        self.assertNotEqual(canonical_brand('Tryx Cooling'), 'TRYX')

        product_id = 'fractal-design-lumen-s24-v2-rgb-fd-w-l1-s2412'
        product = {
            'brand': 'Fractal Design', 'category_id': 'cooler',
            'mpn': 'FD-W-L1-S2412', 'mpn_source': 'manufacturer',
            'manufacturer_url': 'https://www.fractal-design.com/products/water-cooling/lumen-series/lumen-s24/lumen-s24-v2-rgb/',
        }
        candidate = {
            'brand': 'Fractal', 'category_hint': 'cooler', 'mpn': 'FD-W-L1-S2412',
            'ean': '7340172705932',
            'url_public': 'https://www.neobyte.es/fractal-design-lumen-s24-v2-rgb-9553.html',
        }

        class Result:
            def __init__(self, row): self.row = row
            def fetchone(self): return self.row

        class Conn:
            def execute(self, sql, params):
                if 'SELECT candidate,status' in sql:
                    return Result((candidate, 'pending'))
                if 'SELECT data FROM catalog_entities' in sql:
                    return Result((product,))
                if 'SELECT product_id FROM product_identifiers' in sql:
                    return Result((product_id,))
                raise AssertionError(sql)

        decision = {
            'review_id': 9553, 'action': 'approve', 'product_id': product_id,
            'note': ('Fractal own-site about page identifies Fractal Design as its PC gaming brand and presents products as Fractal; '
                     'the official Lumen S24 V2 RGB page publishes exact MPN FD-W-L1-S2412: '
                     'https://www.fractal-design.com/about/ '
                     'https://www.fractal-design.com/products/water-cooling/lumen-series/lumen-s24/lumen-s24-v2-rgb/'),
        }
        validated, aliases = validate_review(Conn(), decision, [])
        self.assertEqual(validated['id'], product_id)
        self.assertEqual(aliases, [])

        product_id = 'tryx-panorama-se-360-argb-white-l-p360l-am3m-g0w'
        product = {
            'brand': 'Tryx', 'category_id': 'cooler',
            'mpn': 'L-P360L-AM3M-G0W', 'mpn_source': 'manufacturer',
            'manufacturer_url': 'https://shop.tryx.com/products/tryx-panorama-se-360-argb-white',
        }
        candidate = {
            'brand': 'TRYX', 'category_hint': 'cooler', 'mpn': 'L-P360L-AM3M-G0W',
            'ean': '6977029650254',
            'url_public': 'https://www.ldlc.com/es-es/ficha/PB00677896.html',
        }
        decision = {
            'review_id': 8614, 'action': 'approve', 'product_id': product_id,
            'note': ('El store oficial TRYX identifica el producto blanco PANORAMA SE 360 ARGB y publica el MPN exacto '
                     'L-P360L-AM3M-G0W: https://shop.tryx.com/products/tryx-panorama-se-360-argb-white. '
                     'La página oficial About presenta la marca como TRYX: https://shop.tryx.com/pages/about-us. '
                     'El producto y categoría cooler coinciden con el canónico local; solo difiere la capitalización histórica Tryx/TRYX.'),
        }
        validated, aliases = validate_review(Conn(), decision, [])
        self.assertEqual(validated['id'], product_id)
        self.assertEqual(aliases, [])


if __name__ == '__main__':
    unittest.main()
