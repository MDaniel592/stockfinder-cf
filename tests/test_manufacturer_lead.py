"""The pending-queue diff: exact MPN, regional suffix, conflicts and official-model leads."""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts')]

import manufacturer_diff
import manufacturer_index


class ModelLeadTests(unittest.TestCase):
    def reference(self):
        rows = [
            manufacturer_index.row(brand='ASRock', mpn='CL-650G', mpn_source='manufacturer',
                                   source='scraper:manufacturer-asrock-psu',
                                   source_url='https://tw.asrock.com/Power-Supply/Challenger/CL-650G/',
                                   model='CL-650G', category='psu'),
            manufacturer_index.row(brand='ASUS', mpn='TUF-GAMING-850G-EVO',
                                   mpn_source='manufacturer', source='scraper:manufacturer-asus-psu',
                                   source_url='https://www.asus.com/es/.../techspec/',
                                   model='TUF Gaming 850W Gold EVO', category='psu'),
            manufacturer_index.row(brand='MSI', mpn='MAG-A650BN', mpn_source='manufacturer',
                                   source='store:msi-official-store',
                                   source_url='https://es-store.msi.com/products/mag-a650bn',
                                   model='MAG A650BN', category='psu'),
        ]
        return manufacturer_diff.Reference(rows)

    def item(self, **kwargs):
        base = dict(shop='neobyte', review_id=7221, brand='ASRock', mpn='90-UXC065-GNEAAA',
                    ean='4710483948145', name='ASRock CL-650G ATX 3.1 - Fuente de alimentación',
                    category='psu', retailer_sku='28567', url_public='', review_note='')
        base.update(kwargs)
        return base

    def test_internal_retailer_code_finds_the_official_model(self):
        result = manufacturer_diff.build_item(self.reference(), self.item())
        self.assertEqual(result['status'], 'sin_respaldo')
        lead = result['model_lead']
        self.assertIsNotNone(lead)
        self.assertEqual(lead['model'], 'CL-650G')
        self.assertEqual(lead['mpn'], 'CL-650G')
        self.assertTrue(lead['source_url'].endswith('/CL-650G/'))

    def test_lead_of_another_brand_is_not_proposed(self):
        result = manufacturer_diff.build_item(self.reference(), self.item(brand='Corsair'))
        self.assertIsNone(result['model_lead'])

    def test_unrelated_name_has_no_lead(self):
        result = manufacturer_diff.build_item(self.reference(),
                                              self.item(name='Cooler Master MasterBox MB510L Negro'))
        self.assertIsNone(result['model_lead'])

    def test_asus_lead_carries_the_official_code(self):
        result = manufacturer_diff.build_item(self.reference(), self.item(
            brand='ASUS', mpn='90YE00SF-B0NA00',
            name='Asus TUF Gaming 850W Gold EVO White - Fuente de alimentación 850W'))
        lead = result['model_lead']
        self.assertIsNotNone(lead)
        self.assertEqual(lead['mpn'], 'TUF-GAMING-850G-EVO')


if __name__ == '__main__':
    unittest.main()
