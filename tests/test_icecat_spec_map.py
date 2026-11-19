"""Pruebas del mapeo Icecat -> claves canonicas (scripts/icecat_spec_map.py). Ejecutar: python3 -m unittest tests.test_icecat_spec_map"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'nas'))
from icecat_spec_map import map_features
import spec_normalize as sn


def F(name, raw, unit='', type_='dropdown'):
    return {'name': name, 'raw': raw, 'unit': unit, 'pv': f'{raw} {unit}'.strip(), 'type': type_}


def values(cat, feats):
    return {k: v[0] for k, v in map_features(cat, feats).items()}


class IcecatSpecMapTests(unittest.TestCase):
    def test_case_dimensions_and_glass(self):
        got = values('case', [F('Maximum CPU cooler height', '165.5', 'mm'), F('Maximum graphics card length', '330', 'mm'),
                              F('Tempered glass panel(s)', 'Y', '', 'y_n')])
        self.assertEqual(got, {'Altura disipador máxima': '165.5 mm', 'Longitud GPU máxima': '330 mm', 'Panel lateral': 'Cristal templado'})

    def test_case_ambiguous_form_factor_not_mapped(self):
        # "Mini-ATX" no es un formato inequivoco: no se mapea la clave entera
        self.assertNotIn('Formatos compatibles', values('case', [F('Supported motherboard form factors', 'ATX,Mini-ATX,Mini-ITX')]))
        self.assertEqual(values('case', [F('Supported motherboard form factors', 'ATX,Micro-ATX,Mini-ITX')]),
                         {'Formatos compatibles': 'ATX, Micro-ATX, Mini-ITX'})

    def test_case_fans_need_every_supported_location(self):
        ok = [F('Rear fans installed', '1x 120 mm'), F('Number of rear fans supported (max)', '1', '', 'numerical'),
              F('Front fans installed', '2x 120 mm'), F('Number of front fans supported (max)', '3', '', 'numerical')]
        self.assertEqual(values('case', ok)['Ventiladores incluidos'], '3 × 120 mm')
        partial = ok[:2] + [F('Number of top fans supported (max)', '2', '', 'numerical')]
        self.assertNotIn('Ventiladores incluidos', values('case', partial))

    def test_color_single_only(self):
        self.assertEqual(values('case', [F('Product colour', 'Black')]), {'Color': 'Negro'})
        self.assertEqual(values('case', [F('Product colour', 'Black,Transparent')]), {})

    def test_storage_capacity_speed_form(self):
        got = values('storage', [F('SSD capacity', '2000', 'GB', 'numerical'), F('Read speed', '7.4', 'GB/s', 'numerical'),
                                 F('Write speed', '6500', 'MB/s', 'numerical'), F('M.2 SSD size', '2280 (22 x 80 mm)')])
        self.assertEqual(got, {'Tipo': 'SSD', 'Capacidad': '2 TB', 'Lectura secuencial': '7400 MB/s',
                               'Escritura secuencial': '6500 MB/s', 'Formato': 'M.2 2280'})

    def test_storage_interface_nvme(self):
        feats = [F('Interface', 'PCI Express 4.0'), F('PCI Express interface data lanes', 'x4'), F('NVMe', 'Y', '', 'y_n'),
                 F('NVMe version', '1.4')]
        self.assertEqual(values('storage', feats)['Interfaz'], 'PCIe 4.0 x4, NVMe 1.4')
        self.assertEqual(values('storage', [F('Interface', 'Serial ATA III')]), {'Interfaz': 'SATA III'})
        self.assertEqual(values('storage', [F('Interface', 'PCI Express 4.0')]), {})  # sin carriles/NVMe: ambiguo

    def test_cooler_sockets_unknown_token_skips_all(self):
        raw = 'LGA 1150 (Socket H3),LGA 1700,Socket AM5'
        got = values('cooler', [F('Supported processor sockets', raw)])
        self.assertEqual(sn.normalize_specs('cooler', got)['Sockets compatibles'], 'AM5, LGA1700, LGA1150')
        self.assertEqual(values('cooler', [F('Supported processor sockets', raw + ',Socket XYZ')]), {})

    def test_cooler_radiator_requires_coherent_width(self):
        feats = [F('Type', 'All-in-one liquid cooler'), F('Number of fans', '3', 'fan(s)', 'numerical'), F('Fan diameter', '120', 'mm'),
                 F('Radiator width', '394', 'mm', 'numerical')]
        got = values('cooler', feats)
        self.assertEqual((got['Tipo'], got['Ventiladores'], got['Radiador']), ('Líquida AIO', '3 × 120 mm', '360 mm'))
        feats[-1] = F('Radiator width', '280', 'mm', 'numerical')
        self.assertNotIn('Radiador', values('cooler', feats))

    def test_psu_gpu_connector_ambiguous_with_16pin(self):
        base = [F('PCI Express power connectors (6+2 pin)', '3', '', 'numerical')]
        self.assertEqual(values('psu', base), {'Conector GPU': '3 × PCIe 6+2 pines'})
        self.assertNotIn('Conector GPU', values('psu', base + [F('PCI Express power connectors (16-pin)', '1', '', 'numerical')]))

    def test_psu_misc(self):
        got = values('psu', [F('Total power', '750', 'W', 'numerical'), F('Cabling type', 'Fully modular'), F('ATX version', '3.1'),
                             F('80 PLUS certification', '80 PLUS Gold'), F('Power supply unit (PSU) form factor', 'SFX-L')])
        self.assertEqual(got, {'Potencia': '750 W', 'Modular': 'Totalmente modular', 'Estándar ATX': '3.1',
                               'Eficiencia 80 PLUS': '80 PLUS Gold', 'Formato': 'SFX-L'})

    def test_ram(self):
        got = values('ram', [F('CAS latency', '30'), F('Memory data transfer rate', '6000', 'MT/s', 'numerical'),
                             F('Memory layout (modules x size)', '2 x 16', 'GB'), F('Memory form factor', '288-pin DIMM'),
                             F('Buffered memory type', 'Unregistered (unbuffered)')])
        self.assertEqual(got, {'Latencia': 'CL30', 'Velocidad': '6000 MT/s', 'Formato': 'UDIMM'} | {'Módulos': '2 × 16 GB'})

    def test_motherboard(self):
        got = values('motherboard', [F('Wi-Fi', 'Y', '', 'y_n'), F('Top Wi-Fi standard', 'Wi-Fi 7 (802.11be)'),
                                     F('Maximum internal memory', '256', 'GB', 'numerical'), F('Number of memory slots', '4', '', 'numerical'),
                                     F('Motherboard form factor', 'Micro-ATX')])
        self.assertEqual(got, {'Wi-Fi': 'Wi-Fi 7', 'Memoria máxima': '256 GB', 'Ranuras de memoria': '4', 'Formato': 'Micro-ATX'})
        self.assertEqual(values('motherboard', [F('Maximum internal memory', '2048', 'GB', 'numerical')]), {'Memoria máxima': '2 TB'})

    def test_only_target_keys(self):
        self.assertEqual(values('storage', [F('Weight', '40', 'g', 'numerical')]), {})


if __name__ == '__main__':
    unittest.main()
