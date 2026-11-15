"""Manufacturer reference index, pending-queue diff and category specifications.

No database and no network: everything here is a pure function of local files.
"""
import sys
import unittest
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts')]

import manufacturer_diff
import manufacturer_index
import manufacturer_scrape


SEASONIC_ROWS = [
    ['COMPANY INFO', '', '', '', '', 'RECOGNIZED RETAIL MODELS', '', '', '', 'RECOGNIZED BULK MODELS',
     '', '', 'Country', '2 letter country code', '', '', ''],
    ['Company name', 'Example Company', '', '', '', 'Series name', 'Model name',
     'Model number [use this]', '', 'Type', 'Model number [use this]', '', 'AFGHANISTAN', 'AF', '', '', ''],
    ['Reference', 'REF01234', '', '', '', 'Eco Series', 'ECO-430', 'SSR-430ST', '', 'PC', 'SS-300ES',
     '', 'ALBANIA', 'AL', '', '', ''],
    ['', '', '', '', '', 'CORE ATX 3.1 Series', 'CORE GX-850 White ATX 3.1', 'SRP-CGX851-A5A32SFW',
     '', '', '', '', '', '', '', '', ''],
]


class SeasonicCsvTests(unittest.TestCase):
    def test_rows_and_example_block_are_read(self):
        text = '\n'.join(';'.join(row) for row in SEASONIC_ROWS)
        rows = manufacturer_index.parse_seasonic_rma(text, 'Seasonic', Path('x.csv'), 'deadbeef')
        by_mpn = {row['mpn']: row for row in rows}
        self.assertIn('SRP-CGX851-A5A32SFW', by_mpn)
        self.assertEqual(by_mpn['SRP-CGX851-A5A32SFW']['model'], 'CORE GX-850 White ATX 3.1')
        self.assertEqual(by_mpn['SRP-CGX851-A5A32SFW']['series'], 'CORE ATX 3.1 Series')
        self.assertEqual(by_mpn['SRP-CGX851-A5A32SFW']['brand'], 'Seasonic')
        self.assertEqual(by_mpn['SRP-CGX851-A5A32SFW']['mpn_source'], 'manufacturer')
        # The row that belongs to the company block of the template is kept but flagged.
        self.assertIn('SSR-430ST', by_mpn)
        self.assertIn('verificar', by_mpn['SSR-430ST']['notes'])


class GenericCsvTests(unittest.TestCase):
    def test_header_decides_the_columns(self):
        text = 'Brand;Category;MPN;EAN;Model\nASUS;gpu;90YV0L71-M0NA00;4719331882853;Prime OC\n'
        rows = manufacturer_index.parse_generic_csv(text, '', Path('x.csv'), 'deadbeef')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['brand'], 'ASUS')
        self.assertEqual(rows[0]['category'], 'gpu')
        self.assertEqual(rows[0]['mpn'], '90YV0L71-M0NA00')
        self.assertEqual(rows[0]['ean'], '4719331882853')

    def test_csv_without_mpn_column_is_ignored(self):
        rows = manufacturer_index.parse_generic_csv('a;b\n1;2\n', 'X', Path('x.csv'), 'd')
        self.assertEqual(rows, [])


class IndexTests(unittest.TestCase):
    def test_manufacturer_row_wins_and_fields_are_merged(self):
        index = manufacturer_index.Index()
        index.add(manufacturer_index.row(brand='ASUS', mpn='MPN-1', mpn_source='retailer',
                                          source='catalog', source_url='', model='Prime OC'))
        index.add(manufacturer_index.row(brand='ASUS', mpn='MPN-1', mpn_source='manufacturer',
                                          source='scraper:asus', source_url='https://www.asus.com/x',
                                          ean='4719331882853'))
        rows = index.finalise()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['mpn_source'], 'manufacturer')
        self.assertEqual(rows[0]['source'], 'scraper:asus')
        self.assertEqual(rows[0]['ean'], '4719331882853')
        self.assertEqual(rows[0]['model'], 'Prime OC')

    def test_evidence_hashes_are_attached_by_url(self):
        index = manufacturer_index.Index()
        index.evidence['https://www.asus.com/x'] = {
            'sha256': 'abc', 'checked_at': '2026-11-15T00:00:00Z', 'title': 't',
            'status': 200, 'challenge': False, 'path': 'e.json'}
        index.add(manufacturer_index.row(brand='ASUS', mpn='MPN-1', mpn_source='manufacturer',
                                          source='scraper:asus', source_url='https://www.asus.com/x'))
        rows = index.finalise()
        self.assertEqual(rows[0]['sha256'], 'abc')
        self.assertEqual(rows[0]['checked_at'], '2026-11-15T00:00:00Z')


class DiffTests(unittest.TestCase):
    def reference(self):
        rows = [
            manufacturer_index.row(brand='ASUS', mpn='SRP-CGX851-A5A32SFW',
                                   mpn_source='manufacturer', source='csv:seasonic.csv',
                                   source_url='https://rma.seasonic.com/example_import.csv',
                                   model='CORE GX-850 White ATX 3.1'),
            manufacturer_index.row(brand='ASRock', mpn='SL-850G', mpn_source='manufacturer',
                                   source='scraper:asrock', source_url='https://www.asrock.com/x'),
        ]
        return manufacturer_diff.Reference(rows)

    def item(self, **kwargs):
        base = dict(shop='ldlc', review_id=1, brand='ASUS', mpn='SRP-CGX851-A5A32SFW', ean='',
                    name='CORE GX-850 White ATX 3.1', category='psu', retailer_sku='PB1',
                    url_public='', review_note='')
        base.update(kwargs)
        return base

    def test_exact_mpn_is_backed(self):
        result = manufacturer_diff.build_item(self.reference(), self.item())
        self.assertEqual(result['status'], 'mpn')
        self.assertEqual(result['mpn_source'], 'manufacturer')
        self.assertEqual(result['reference_source'], 'csv:seasonic.csv')

    def test_regional_suffix_is_reported(self):
        result = manufacturer_diff.build_item(self.reference(), self.item(mpn='SRP-CGX851-A5A32SFW-EU'))
        self.assertEqual(result['status'], 'mpn_sufijo_regional')
        self.assertEqual(result['reference_mpn'], 'SRP-CGX851-A5A32SFW')

    def test_same_mpn_in_another_brand_is_a_conflict(self):
        result = manufacturer_diff.build_item(self.reference(), self.item(brand='Seasonic', mpn='SL-850G'))
        self.assertEqual(result['status'], 'mpn_otra_marca')
        self.assertEqual(result['reference_source'], 'scraper:asrock')

    def test_missing_reference_keeps_a_lead(self):
        result = manufacturer_diff.build_item(self.reference(),
                                              self.item(mpn='UNKNOWN-1', name='CORE GX-850 White ATX 3.1'))
        self.assertEqual(result['status'], 'sin_respaldo')
        self.assertTrue(result['lead'])
        self.assertEqual(result['lead'][0]['mpn'], 'SRP-CGX851-A5A32SFW')


class CategorySpecTests(unittest.TestCase):
    def test_psu_uses_the_catalogue_vocabulary(self):
        specs = manufacturer_scrape.normalise_specs(
            'Seasonic', {'Wattage': '850W', '80 PLUS': 'Gold', 'Form Factor': 'ATX',
                         'Modular': 'Fully modular', 'Fan Size': '135 mm'},
            category='psu')
        self.assertEqual(specs['Potencia'], '850W')
        self.assertEqual(specs['Eficiencia 80 PLUS'], 'Gold')
        self.assertEqual(specs['Formato'], 'ATX')
        self.assertEqual(specs['Modular'], 'Fully modular')
        self.assertEqual(specs['Ventilador'], '135 mm')

    def test_bare_numbers_never_get_an_invented_unit(self):
        specs = manufacturer_scrape.normalise_specs('X', {'Wattage': '750'}, category='psu')
        self.assertEqual(specs['Potencia'], '750')

    def test_ram_and_motherboard_vocabulary(self):
        ram = manufacturer_scrape.normalise_specs(
            'Kingston', {'Memory Type': 'DDR5', 'Capacity': '32 GB', 'Speed': '6000 MT/s',
                         'CAS Latency': 'CL36'}, category='ram')
        self.assertEqual(ram['Tipo de memoria'], 'DDR5')
        self.assertEqual(ram['Capacidad'], '32 GB')
        self.assertEqual(ram['Velocidad'], '6000 MT/s')
        self.assertEqual(ram['Latencia CAS'], 'CL36')
        board = manufacturer_scrape.normalise_specs(
            'ASUS', {'Socket': 'AM5', 'Chipset': 'AMD B850', 'Max Memory': '256 GB',
                     'Form Factor': 'ATX', 'Wireless Networking': 'Wi-Fi 7'}, category='motherboard')
        self.assertEqual(board['Socket'], 'AM5')
        self.assertEqual(board['Chipset'], 'AMD B850')
        self.assertEqual(board['Memoria máxima'], '256 GB')
        self.assertEqual(board['Formato'], 'ATX')
        self.assertEqual(board['Wi-Fi'], 'Wi-Fi 7')

    def test_gpu_vocabulary_is_unchanged(self):
        specs = manufacturer_scrape.normalise_specs(
            'ASRock', {'GPU': 'AMD Radeon RX 9070', 'Memory': '16GB GDDR6',
                       'Bus Standard': 'PCI Express 5.0'}, category='gpu')
        self.assertEqual(specs['Chip gráfico'], 'AMD Radeon RX 9070')
        self.assertEqual(specs['Memoria'], '16 GB')
        self.assertEqual(specs['Tipo de memoria'], 'GDDR6')
        self.assertEqual(specs['Interfaz'], 'PCIe 5.0')


if __name__ == '__main__':
    unittest.main()
