"""Generation rules for PcComponentes discovery (no network, no database)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from pccomponentes_rules import classify


def modern(category, name, specs=None):
    return classify(category, name, specs)['modern']


class CpuRules(unittest.TestCase):
    def test_current_and_previous(self):
        for name in ('Procesador AMD Ryzen 7 9800X3D 4.7/5.2GHz',
                     'Procesador AMD Ryzen 5 7600X 6 Núcleos',
                     'Intel Core Ultra 7 265K 3.9/5.5GHz',
                     'Intel Core i5-14600K 3.5/5.4GHz',
                     'Procesador AMD Ryzen 7 8700G 8 Núcleos AM5',
                     'AMD Ryzen 9 9950X3D2 5.6GHz Socket AM5 Boxed',
                     'Intel Core i7-13700KF 3.4 GHz Box',
                     'Intel Core Ultra 5 245KF 4.2/5.2GHz',
                     'AMD Ryzen 5 5600 3.5GHz Box',
                     'AMD Ryzen 7 5700X 3.4GHz Box sin Ventilador',
                     'AMD Ryzen 5 5600G AM4',
                     'Procesador AMD Ryzen 5 5500F 6 Núcleos 3.0 GHz Base 4.4 GHz Turbo 65 W TDP',
                     'AMD Ryzen 7 5800X3D 3.4GHz',
                     'AMD Ryzen 5 4500 3.6GHz Box'):
            self.assertTrue(modern('procesadores', name), name)

    def test_legacy_and_enterprise(self):
        for name in ('Intel Core i5-12400 6 núcleos',
                     'Procesador Intel Xeon Gold 6430',
                     'Procesador Hikvision DS-DT60C 60 Hz',
                     'Intel Core i9-11900K LGA1200',
                     'AMD Ryzen 5 5600 Tray',
                     'AMD Ryzen 7 5800H portátil',
                     'AMD Ryzen 5 5625U',
                     'AMD Ryzen 7 7735HS portátil',
                     'AMD Ryzen 9 7945HX 16 núcleos',
                     'Intel Core i7-13700H 14 núcleos',
                     'Intel Core Ultra 7 255H',
                     'AMD Ryzen Threadripper PRO 7965WX'):
            self.assertFalse(modern('procesadores', name), name)


class GpuRules(unittest.TestCase):
    def test_modern(self):
        for name in ('MSI GeForce RTX 5060 Ti Ventus 8GB GDDR7',
                     'Gigabyte Radeon RX 9070 XT Gaming OC 16GB',
                     'ASRock Intel Arc B580 Steel Legend',
                     'Sparkle Intel Arc A750 8GB',
                     'Sparkle Intel Arc A380 ELF 6GB',
                     'Tarjeta Gráfica Asus Prime Radeon RX 9070 GRE 12 GB GDDR6 OC'):
            self.assertTrue(modern('tarjetas-graficas', name), name)

    def test_old_or_professional(self):
        for name in ('Gigabyte GeForce RTX 3060 WINDFORCE 12GB',
                     'ASUS GeForce GT 710 2GB GDDR5',
                     'NVIDIA Quadro RTX 4000',
                     'ASRock Intel Arc Pro B70 Creator 32GB',
                     'MSI GeForce RTX 4070 SUPER Ventus 12GB',
                     'Gigabyte GeForce RTX 4060 Gaming OC 8GB',
                     'Sapphire Radeon RX 7800 XT Pulse 16GB',
                     'XFX Radeon RX 6600 8GB',
                     'ASUS GeForce GTX 1660 Super 6GB',
                     'Intel Arc Pro A60 12GB',
                     'NVIDIA RTX 5000 Ada Generation 32GB',
                     'NVIDIA RTX PRO 6000 Blackwell'):
            self.assertFalse(modern('tarjetas-graficas', name), name)


class BoardRules(unittest.TestCase):
    def test_am5_and_lga1851_kept(self):
        for name in ('MSI B650M GAMING PLUS WIFI AM5',
                     'ASUS ROG CROSSHAIR X870E',
                     'MSI PRO Z890-S WIFI LGA 1851'):
            self.assertTrue(modern('placas-base', name), name)

    def test_am4_and_server_dropped(self):
        for name in ('Gigabyte B550M DS3H R2 AM4',
                     'MSI PRO H610M-E DDR4 LGA1700',
                     'GIGABYTE MZ33-AR0 AMD EPYC 9004 Socket SP5'):
            self.assertFalse(modern('placas-base', name), name)


class RamRules(unittest.TestCase):
    def test_ddr4_is_allowed_and_ddr3_is_not(self):
        self.assertTrue(modern('memorias-ram',
                               'G.Skill Ripjaws V 32GB 2x16GB DDR4 3200MHz CL16'))
        self.assertTrue(modern('memorias-ram',
                               'Corsair Vengeance 32GB 2x16GB DDR5 6000MHz CL30'))
        self.assertFalse(modern('memorias-ram', 'Kingston 8GB DDR3 1600MHz'))

    def test_laptop_and_server_modules_dropped(self):
        for name in ('Crucial 16GB DDR5 SO-DIMM 5600MHz ECC',
                     'Samsung 32GB DDR4 ECC REG 3200MHz'):
            self.assertFalse(modern('memorias-ram', name), name)

    def test_speed_inference_when_ddr_is_absent(self):
        self.assertTrue(modern('memorias-ram',
                               'Corsair Vengeance 32GB 2x16GB 6000MHz CL30'))
        self.assertTrue(modern('memorias-ram',
                               'Corsair Vengeance LPX 16GB 1x16GB 3200MHz CL16'))
        self.assertFalse(modern('memorias-ram', 'Kingston 8GB 1x8GB 1600MHz'))


class PsuRules(unittest.TestCase):
    def test_atx3_and_premium_high_wattage_kept(self):
        for name in ('Corsair RM1000e 1000W ATX 3.1 PCIe 5.1',
                     'GIGABYTE UD1300GM PG5 1300W 80+ Gold',
                     'Be quiet Pure Power 13 M 1200W 80 PLUS Gold'):
            self.assertTrue(modern('fuentes-alimentacion', name), name)

    def test_pre_atx3_units_are_kept(self):
        for name in ('Aerocool LUX RGB 550W 80 PLUS Bronze',
                     'FSP Hexa 85+ Pro 650W 80 Plus Bronze',
                     'Corsair RM750x 750W 80 Plus Gold ATX 2.4'):
            self.assertTrue(modern('fuentes-alimentacion', name), name)

    def test_generation_label_distinguishes_atx3(self):
        self.assertEqual(classify('fuentes-alimentacion', 'NZXT C1200 1200W ATX 3.1 PCIe 5.1')['generation'], 'ATX3')
        self.assertEqual(classify('fuentes-alimentacion', 'Hiditec BZ750 v2 750W Bronze')['generation'], 'ATX2')

    def test_tiny_units_and_adapters_dropped(self):
        for name in ('UNYKAch 52004V2 350W 80 Plus Bronze TFX',
                     'Intenso 7806510 65W Voltaje 120-140V',
                     'APC Smart-UPS X 750 750VA 600W Rack/Tower',
                     'HP 65W GaN USB-C Adaptador'):
            self.assertFalse(modern('fuentes-alimentacion', name), name)


class StorageRules(unittest.TestCase):
    def test_nvme_kept_with_or_without_generation(self):
        for name in ('Samsung 990 PRO 2TB NVMe PCIe 4.0 M.2',
                     'Sandisk NAS 800 3.84TB M.2 NVMe PCIe 5.0',
                     'Samsung 990 PRO 2TB PCIe x4 NVMe',
                     'Kingston KC3000 1TB PCIe x4 NVMe'):
            self.assertTrue(modern('discos-duros', name), name)
            self.assertEqual(classify('discos-duros', name)['generation'], 'NVMe SSD')

    def test_sata_ssd_kept(self):
        for name in ('WD Blue SA510 2.5" 500GB SSD SATA 3',
                     'Samsung 870 EVO SSD 2.5 250GB SATA3',
                     'Western Digital Blue SA510 M.2 1TB SATA 3 SSD'):
            verdict = classify('discos-duros', name)
            self.assertTrue(verdict['modern'], name)
            self.assertEqual(verdict['generation'], 'SATA SSD', name)

    def test_shop_listing_marks_sata_ssd_without_the_word(self):
        # Coolmod "Discos SSD" titles often read "Crucial BX500 500GB 2.5 SATA3".
        name = "Kioxia EXCERIA 960GB 2.5'' SATA3"
        self.assertEqual(classify('discos-duros', name)['generation'], 'HDD')
        marked = classify('discos-duros', name, [{'name': 'Tipo', 'value': 'SSD'}])
        self.assertEqual(marked['generation'], 'SATA SSD')

    def test_hdd_kept(self):
        for name in ('Seagate Exos X24 16TB HDD SATA III',
                     'WD Blue 2TB 3.5" SATA3',
                     'Seagate BarraCuda 3.5" 2TB SATA 3',
                     'Seagate Barracuda 2TB 2.5',
                     'Western Digital Red Plus NAS 6TB 3.5 SATA3'):
            verdict = classify('discos-duros', name)
            self.assertTrue(verdict['modern'], name)
            self.assertEqual(verdict['generation'], 'HDD', name)

    def test_explicit_pcie3_nvme_kept(self):
        for name in ('PNY CS1030 2TB M.2 NVMe PCIe 3.0',
                     'Adata Legend 710 256GB SSD M.2 NVMe NAND Gen3x4'):
            self.assertTrue(modern('discos-duros', name), name)

    def test_external_usb_sas_and_non_drives_dropped(self):
        for name in ('Kingston XS1000 Disco SSD Externo 1TB USB 3.2',
                     'Toshiba Canvio Gaming 2TB 2.5" USB 3.2 Gen 1',
                     'Seagate Exos X24 20 TB 3.5" SAS',
                     'Dell 400-ATJM 1,2 TB HDD SAS 12 Gb/s 10.000 rpm 2,5"',
                     'Impresora Térmica CUSTOM P3X 203 x 203 DPI USB'):
            self.assertFalse(modern('discos-duros', name), name)

    def test_specs_mark_external_drives(self):
        specs = [{'name': 'Disco Duro', 'value': 'Externos'}]
        self.assertFalse(modern('discos-duros', 'Seagate One Touch 2.5" 5TB', specs))


class ScopeRules(unittest.TestCase):
    def test_peripherals_excluded_by_default(self):
        self.assertFalse(modern('monitores-pc', 'Monitor Dell 27 4K'))

    def test_cases_are_components(self):
        self.assertTrue(modern('cajas-pc', 'Corsair 4000D Airflow Cristal Templado'))

    def test_unknown_category_rejected(self):
        self.assertFalse(modern('juguetes', 'Cualquier cosa'))


if __name__ == '__main__':
    unittest.main()
