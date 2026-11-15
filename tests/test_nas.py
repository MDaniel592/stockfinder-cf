"""Real PostgreSQL integration tests, isolated in a temporary schema."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'nas'))
from catalog import write
from sync_agent import run_once
from import_catalog import import_products, import_aliases
from import_offers import import_offer
from review_queue import migrate as migrate_reviews,stage_candidates,approve,reject

class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'test_' + uuid.uuid4().hex
        self.conn = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True)
        self.conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        self.conn.execute((Path(__file__).resolve().parents[1]/'db/postgres-migrations/0001_catalog.sql').read_text())
        self.conn.execute((Path(__file__).resolve().parents[1]/'db/postgres-migrations/0002_identifiers.sql').read_text())

    def tearDown(self):
        self.conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.conn.close()

    def test_transaction_rollback_and_versions(self):
        with self.assertRaises(RuntimeError):
            with self.conn.transaction():
                write(self.conn, 'category', 'gpu', {'name': 'GPU', 'slug': 'gpu'})
                raise RuntimeError('Simulated scraper failure')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM catalog_outbox').fetchone()[0], 0)
        self.assertEqual(write(self.conn, 'category', 'gpu', {'name': 'GPU', 'slug': 'gpu'}), (1,))
        self.assertIsNone(write(self.conn, 'category', 'gpu', {'name': 'GPU', 'slug': 'gpu'}))
        self.assertEqual(write(self.conn, 'category', 'gpu', {'name': 'Graphics', 'slug': 'gpu'}), (2,))
        self.assertEqual(self.conn.execute('SELECT count(*) FROM catalog_outbox').fetchone()[0], 2)

    def test_timestamp_only_checks_do_not_publish(self):
        data = dict(product_id='p', shop_id='s', price_cents=100, in_stock=True, last_checked_at='2026-01-01T00:00:00Z')
        write(self.conn, 'offer', 'o', data)
        write(self.conn, 'offer', 'o', {**data, 'last_checked_at':'2026-01-01T01:00:00Z'})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM catalog_outbox').fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT data->>'last_checked_at' FROM catalog_entities").fetchone()[0], '2026-01-01T01:00:00Z')
        write(self.conn, 'offer', 'o', {**data, 'price_cents': 99})
        self.assertEqual(self.conn.execute('SELECT version FROM catalog_entities').fetchone()[0], 2)

    def test_failed_send_keeps_backlog(self):
        write(self.conn, 'category', 'gpu', {'name':'GPU','slug':'gpu'})
        with patch('sync_agent.push', side_effect=OSError('Offline')):
            with self.assertRaises(OSError): run_once(self.conn, '', '')
        self.assertEqual(self.conn.execute('SELECT acknowledged_at,attempts FROM catalog_outbox').fetchone(), (None,1))

    def test_late_commit_is_not_skipped(self):
        late = psycopg.connect(os.environ['DATABASE_URL'])
        late.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        write(late, 'category', 'late', {'name':'Late','slug':'late'})
        write(self.conn, 'category', 'early', {'name':'Early','slug':'early'})
        seen = []
        def ack(endpoint, secret, events, allow_local):
            seen.extend(e['entity_id'] for e in events)
            return [e['event_id'] for e in events]
        with patch('sync_agent.push', side_effect=ack):
            run_once(self.conn, '', '')
            self.assertEqual(seen, ['early'])
            late.commit()
            run_once(self.conn, '', '')
        late.close()
        self.assertEqual(seen, ['early', 'late'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM catalog_outbox WHERE acknowledged_at IS NULL').fetchone()[0],0)

    def test_curated_import_and_retailer_identity_are_idempotent(self):
        from datetime import datetime,timezone,timedelta
        product=dict(id='amd-example',slug='amd-example',name='AMD Example',category_id='cpu',brand='AMD',mpn='BOXED-123',
          manufacturer_url='https://www.amd.com/example',specs_verified_at='2026-11-01T00:00:00Z',specs={'Socket':'AM5'})
        import_products(self.conn,[product]); import_products(self.conn,[product])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='product'").fetchone()[0],1)
        now=datetime.now(timezone.utc)
        offer=dict(product_id='amd-example',shop_id='pccomponentes',retailer_sku='123',mpn='BOXED-123',
          price_cents=10000,in_stock=True,last_checked_at=now.isoformat(),url_public='https://www.pccomponentes.com/example',
          match_evidence='Official boxed MPN matches retailer boxed MPN')
        first=import_offer(self.conn,offer); second=import_offer(self.conn,offer)
        self.assertEqual(first,second)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0],1)
        with self.assertRaises(ValueError): import_offer(self.conn,{**offer,'mpn':'UNKNOWN'})
        with self.assertRaises(ValueError): import_offer(self.conn,{**offer,'last_checked_at':(now-timedelta(days=1)).isoformat()})
        with self.assertRaises(ValueError): import_offer(self.conn,{**offer,'url_public':'https://evil.example/item'})
        with self.assertRaises(ValueError): import_products(self.conn,[product,product])
        ldlc = {**offer, 'shop_id': 'ldlc', 'retailer_sku': 'PB00000001',
                'url_public': 'https://www.ldlc.com/es-es/ficha/PB00000001.html'}
        self.assertEqual(import_offer(self.conn, ldlc), import_offer(self.conn, ldlc))
        with self.assertRaises(ValueError):
            import_offer(self.conn, {**ldlc, 'url_public': 'https://ldlc.com.evil.example/item'})
        neobyte = {**offer, 'shop_id': 'neobyte', 'retailer_sku': '28548',
                   'url_public': 'https://www.neobyte.es/amd-example-28548.html'}
        self.assertEqual(import_offer(self.conn, neobyte), import_offer(self.conn, neobyte))
        with self.assertRaises(ValueError):
            import_offer(self.conn, {**neobyte, 'url_public': 'https://neobyte.es.evil.example/item'})

    def test_manual_overrides_win_after_normalization_and_survive_reimport(self):
        import json,tempfile,spec_overrides as so
        base=dict(source='manual',date='2026-11-09',source_url='https://www.kingston.com/example')
        ram=dict(id='kingston-ram',slug='kingston-ram',name='Kingston RAM',category_id='ram',brand='Kingston',mpn='KF-1',
          manufacturer_url='https://www.kingston.com/example',specs_verified_at='2026-11-01',
          specs={'Memory type':'DDR5','Capacity':'32 GB','Tested speed':'DDR5-6000','Latencia':'CL40'})
        cpu=dict(id='intel-f',slug='intel-f',name='Intel F',category_id='cpu',brand='Intel',mpn='BX-F',
          manufacturer_url='https://www.intel.com/example',specs_verified_at='2026-11-01',specs={'Socket':'LGA 1700','Gráficos integrados':'Sí'})
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'data/overrides').mkdir(parents=True)
            (Path(root)/'data/overrides/manual-ram.json').write_text(json.dumps([
              {**base,'product_id':'kingston-ram','category_id':'ram','key':'Latencia','value':'CL36','note':'ficha oficial'},
              {**base,'product_id':'kingston-ram','category_id':'ram','key':'Formato','value':'UDIMM'}]))
            (Path(root)/'data/overrides/manual-cpu.json').write_text(json.dumps([
              {**base,'product_id':'intel-f','category_id':'cpu','key':'GPU integrada','status':'not_published'},
              {**base,'product_id':'intel-f','category_id':'ram','key':'Latencia','value':'CL30'}]))  # otra categoría: ignorado
            records,errors=so.load_overrides(root)
        self.assertEqual(errors,{})
        index=so.index(records)
        for _ in range(2): import_products(self.conn,[ram,cpu],overrides=index)
        specs=lambda pid:self.conn.execute("SELECT data->'specs' FROM catalog_entities WHERE entity='product' AND entity_id=%s",(pid,)).fetchone()[0]
        # Normalizado (claves canónicas, 6000 MT/s) y el valor manual gana sobre el importado.
        self.assertEqual(specs('kingston-ram'),{'Tipo de memoria':'DDR5','Capacidad':'32 GB','Velocidad':'6000 MT/s','Latencia':'CL36','Formato':'UDIMM'})
        # «No lo publica» no inventa nada en las specs, pero deja de ser hueco.
        self.assertEqual(specs('intel-f'),{'Socket':'LGA1700','Gráficos integrados':'Sí'})
        product=dict(cpu,specs=specs('intel-f'))
        self.assertIn('GPU integrada',[g['key'] for g in so.gaps(product)])
        self.assertNotIn('GPU integrada',[g['key'] for g in so.gaps(product,index)])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_outbox WHERE entity='product'").fetchone()[0],2)
        # Sin correcciones el import vuelve al dato del lote (no hay estado oculto).
        import_products(self.conn,[ram])
        self.assertEqual(specs('kingston-ram')['Latencia'],'CL40')

    def test_manual_overrides_are_validated_against_canonical_keys_and_formats(self):
        import json,tempfile,spec_overrides as so
        ok=dict(product_id='p',category_id='ram',key='Velocidad',value='6000 MT/s',source='manual',date='2026-11-09',source_url='https://www.example.com/p')
        self.assertEqual(so.validate_record(ok),[])
        self.assertEqual(so.check_value('ram','Velocidad','DDR5-6000'),('6000 MT/s',None))
        self.assertIsNotNone(so.check_value('ram','Latencia','36')[1])
        self.assertEqual(so.check_value('ram','Latencia','CL36'),('CL36',None))
        self.assertEqual(so.check_value('cooler','Sockets compatibles','LGA 1700 / AM5'),('AM5, LGA1700',None))
        self.assertEqual(so.check_value('motherboard','Chipset','amd B650'),('AMD B650',None))
        for record,reason in ((dict(ok,value='6000'),'formato canónico'),(dict(ok,value='DDR5-6000'),'no normalizado'),
                              (dict(ok,key='speed'),'no es canónica'),(dict(ok,key='Color'),'no admitida'),
                              (dict(ok,source='flujo'),'manual'),(dict(ok,source_url='http://x'),'https'),(dict(ok,date='9/11/2026'),'AAAA-MM-DD'),
                              (dict(ok,status='not_published'),'no lleva value'),(dict(ok,status='otro'),'status'),
                              (dict(ok,category_id='ram',key='Latencia',value='36'),'formato canónico'),
                              (dict(ok,category_id='gpu',key='Consumo (TBP)',value='doscientos'),'formato canónico'),
                              (dict(ok,extra=1),'desconocidos')):
            problems=so.validate_record(record)
            self.assertTrue(problems and reason in ' '.join(problems),(record,problems))
        with tempfile.TemporaryDirectory() as root:
            (Path(root)/'data/overrides').mkdir(parents=True)
            (Path(root)/'data/overrides/a.json').write_text(json.dumps([ok,dict(ok,value='rápida')]))  # un registro malo: fichero entero fuera
            (Path(root)/'data/overrides/b.json').write_text('[{"product_id": ')  # a medio escribir
            so.save_record(root,dict(ok,product_id='q',value='5600 MT/s'))
            so.save_record(root,dict(ok,product_id='q'))  # sustituye, no duplica
            records,errors=so.load_overrides(root)
            self.assertEqual(sorted(errors),['a.json','b.json'])
            self.assertEqual([(r['product_id'],r['value']) for r in records],[('q','6000 MT/s')])
            with self.assertRaises(ValueError): so.save_record(root,dict(ok,value='rápida'))
            self.assertTrue(so.delete_record(root,'ram','q','Velocidad'))
            self.assertEqual(so.load_overrides(root)[0],[])

    def test_spec_normalization_unifies_spellings_without_inventing_data(self):
        from spec_normalize import normalize_specs as ns
        self.assertEqual(ns('gpu',{'Bus de memoria':'128-bit','Memoria':'16GB GDDR6','Boost clock':'hasta 2780 MHz','Dato oficial 3':'256-bit','Arquitectura':'AMD RDNA 4'}),
          {'Bus de memoria':'128 bits','Memoria':'16 GB','Tipo de memoria':'GDDR6','Frecuencia turbo':'Hasta 2780 MHz','Arquitectura':'RDNA 4'})
        self.assertEqual(ns('motherboard',{'socket':'LGA 1700','chipset':'INTEL B760','form_factor':'micro-ATX','Chipset/model family':'B760','Formato':'microATX','Wireless networking':'Wi-Fi variant'}),
          {'Socket':'LGA1700','Chipset':'Intel B760','Formato':'Micro-ATX'})
        self.assertEqual(ns('cpu',{'Frecuencia base':'4,7 GHz','Socket':'LGA 1851'}),{'Frecuencia base':'4.7 GHz','Socket':'LGA1851'})
        self.assertEqual(ns('psu',{'Eficiencia':'80 Plus Gold','Modularidad':'Totalmente modular','Estándar ATX':'ATX 3.1'}),{'Eficiencia 80 PLUS':'80 PLUS Gold','Modular':'Totalmente modular','Estándar ATX':'3.1'})
        self.assertEqual(ns('ram',{'Voltaje':'1,40 V','Color':'negro','Latencia':'CL16','Latencia CAS':'CL16','Detail 2':'CL16'}),{'Voltaje':'1.4 V','Color':'Negro','Latencia':'CL16'})
        # Different facts are never merged: a differing duplicate keeps its own key.
        self.assertEqual(ns('cooler',{'TDP':'220 W','TDP máximo':'300 W'}),{'TDP':'220 W','TDP máximo':'300 W'})
        self.assertEqual(ns('storage',{'Controlador':'PHISON E18','Capacidad':'3,84 TB'}),{'Controlador':'Phison E18','Capacidad':'3.84 TB'})
        self.assertEqual(ns('cooler',{'Sockets compatibles':'AM5, LGA115X, LGA 1700'}),{'Sockets compatibles':'AM5, LGA1700, LGA115x'})

    def test_gpu_chip_labels_preserve_models_and_variants(self):
        from spec_normalize import normalize_specs as ns
        examples = {'NVIDIA GeForce RTX 5070 Ti':'GeForce RTX 5070 Ti', 'GeForce RTX 4070 SUPER':'GeForce RTX 4070 SUPER',
                    'AMD Radeon RX 9070 XT':'Radeon RX 9070 XT', 'Radeon RX 9070 GRE':'Radeon RX 9070 GRE',
                    'Intel Arc A380 Graphics':'Arc A380', 'Arc B580':'Arc B580', 'Intel Arc Pro B70':'Arc Pro B70',
                    'NVIDIA RTX PRO 6000 Blackwell Workstation Edition':'RTX PRO 6000 Blackwell Workstation Edition',
                    'Unknown future chip':'Unknown future chip'}
        for original,expected in examples.items():
            with self.subTest(original=original):
                result=ns('gpu',{'Chip gráfico':original,'Memoria':'16 GB'})
                self.assertEqual(result,{'Chip gráfico':expected,'Memoria':'16 GB'})
                self.assertEqual(ns('gpu',result),result)
        self.assertEqual(ns('gpu',{'GPU':'NVIDIA GeForce RTX 5060'}),{'Chip gráfico':'GeForce RTX 5060'})
        self.assertEqual(ns('cpu',{'Chip gráfico':'AMD Radeon RX 9070 XT'}),{'Chip gráfico':'AMD Radeon RX 9070 XT'})

    def test_psu_efficiency_is_split_into_80plus_and_cybenetics(self):
        from spec_normalize import normalize_specs as ns
        eff = lambda v: ns('psu', {'Potencia': '850 W', 'Eficiencia': v, 'Modular': 'Totalmente modular'})
        self.assertEqual(eff('80 Plus Gold'), {'Potencia': '850 W', 'Eficiencia 80 PLUS': '80 PLUS Gold', 'Modular': 'Totalmente modular'})
        self.assertEqual(eff('Cybenetics Platinum'), {'Potencia': '850 W', 'Cybenetics': 'Cybenetics Platinum', 'Modular': 'Totalmente modular'})
        for mixed, want in [('80 PLUS Gold / Cybenetics Platinum', ('80 PLUS Gold', 'Cybenetics Platinum')),
                            ('80 Plus Gold; Cybenetics Gold', ('80 PLUS Gold', 'Cybenetics Gold')),
                            ('80 PLUS Titanium / Cybenetics Titanium', ('80 PLUS Titanium', 'Cybenetics Titanium')),
                            ('80 PLUS / Cybenetics Platinum', ('80 PLUS', 'Cybenetics Platinum'))]:
            out = eff(mixed)
            self.assertEqual((out['Eficiencia 80 PLUS'], out['Cybenetics']), want, mixed)
            self.assertNotIn('Eficiencia', out)
            self.assertEqual(list(out), ['Potencia', 'Eficiencia 80 PLUS', 'Cybenetics', 'Modular'])
        # A tier that is not stated is never invented; a noise rating is its own fact.
        self.assertEqual(eff('80 PLUS'), {'Potencia': '850 W', 'Eficiencia 80 PLUS': '80 PLUS', 'Modular': 'Totalmente modular'})
        self.assertEqual(eff('Cybenetics'), {'Potencia': '850 W', 'Modular': 'Totalmente modular'})
        self.assertEqual(eff('80 PLUS Gold, Cybenetics Gold (ruido A-)')['Cybenetics ruido'], 'A-')
        # Idempotent: already split specs are stable.
        once = eff('80 PLUS Gold / Cybenetics Platinum')
        self.assertEqual(ns('psu', once), once)
        self.assertEqual(ns('psu', {'Cybenetics': 'Gold', 'Cybenetics ruido': 'A'}), {'Cybenetics': 'Cybenetics Gold', 'Cybenetics ruido': 'A'})

    def test_psu_standard_form_factor_modular_and_gpu_connector_spellings(self):
        from spec_normalize import normalize_specs as ns
        std = lambda v, key='Estándar ATX': ns('psu', {key: v}).get('Estándar ATX')
        for raw, want in [('3.1', '3.1'), ('ATX 3.1', '3.1'), ('ATX 3.0', '3.0'), ('3.1 / PCIe 5.1', '3.1'), ('3.0 (compatible con ATX 3.1)', '3.0'),
                          ('ATX12V 2.31', '2.x'), ('ATX12V 2.4', '2.x'), ('2.52', '2.x'), ('ATX 2.x', '2.x'), ('ATX3.1', '3.1')]:
            self.assertEqual(std(raw), want, raw)
        for key in ['Estándar', 'Norma ATX', 'ATX']: self.assertEqual(std('ATX 3.1', key), '3.1')
        # A standard glued to the form factor moves to its own key (an explicit one wins).
        self.assertEqual(ns('psu', {'Formato': 'ATX 3.1'}), {'Formato': 'ATX', 'Estándar ATX': '3.1'})
        self.assertEqual(ns('psu', {'Formato': 'ATX 3.1', 'Estándar ATX': '3.0'}), {'Formato': 'ATX', 'Estándar ATX': '3.0'})
        self.assertEqual(ns('psu', {'Formato': 'ATX12V 2.4'}), {'Formato': 'ATX', 'Estándar ATX': '2.x'})
        self.assertEqual(ns('psu', {'Formato': 'SFX'}), {'Formato': 'SFX'})
        self.assertEqual(ns('psu', {'Formato': 'sfx-l'}), {'Formato': 'SFX-L'})
        mod = lambda v: ns('psu', {'Modular': v})['Modular']
        for raw, want in [('Full modular', 'Totalmente modular'), ('Fully modular', 'Totalmente modular'), ('Totalmente modular', 'Totalmente modular'),
                          ('Semi-modular', 'Semimodular'), ('Semimodular', 'Semimodular'), ('No modular', 'Cables fijos'),
                          ('No modular; cableado fijo', 'Cables fijos'), ('Cables fijos', 'Cables fijos'), ('Modular', 'Modular')]:
            self.assertEqual(mod(raw), want, raw)
        con = lambda v: ns('psu', {'Conector GPU': v})['Conector GPU']
        for raw, want in [('12V-2x6', '12V-2x6'), ('12V-2x6 nativo', '12V-2x6'), ('12V-2x6 (600 W)', '12V-2x6'), ('PCIe 5.1 12V-2x6', '12V-2x6'),
                          ('PCIe 5.1 16 pines (600 W)', '12V-2x6'), ('12+4 pines (PCIe 5.1)', '12V-2x6'), ('PCIe Gen 5.1 (16 pines)', '12V-2x6'),
                          ('12VHPWR', '12VHPWR'), ('PCIe 5.0 16 pines (450 W)', '12VHPWR'), ('PCIe 12+4 pin (PCIe Gen 5.0)', '12VHPWR'),
                          ('PCIe 8 pines', 'PCIe 8 pines'), ('PCIe Gen 5.0', 'PCIe Gen 5.0'), ('16 pines nativo', '16 pines nativo'),
                          ('12V-2x6 y 4 × PCIe 6+2', '12V-2x6 y 4 × PCIe 6+2'), ('2 x PCIe 5.1 16 pines (600 W)', '2 x PCIe 5.1 16 pines (600 W)')]:
            self.assertEqual(con(raw), want, raw)

    def test_filter_facets_are_clean_values_with_extras_in_detail_keys(self):
        from spec_normalize import normalize_specs as ns
        mem = lambda v: ns('cpu', {'Memoria compatible': v})['Memoria compatible']
        for raw, want in [('DDR5', 'DDR5'), ('Hasta DDR5 6400 MT/s', 'DDR5-6400'), ('Hasta DDR5 5600 MT/s / DDR4 3200 MT/s', 'DDR5-5600, DDR4-3200'),
                          ('DDR5-5600 / DDR4-3200', 'DDR5-5600, DDR4-3200'), ('DDR5 RDIMM', 'DDR5 RDIMM'), ('DDR5-5200', 'DDR5-5200')]:
            self.assertEqual(mem(raw), want, raw)
        gfx = lambda v: ns('cpu', {'Socket': 'AM5', 'Gráficos integrados': v, 'TDP': '65 W'})
        self.assertEqual(gfx('No (requiere GPU dedicada)'), {'Socket': 'AM5', 'Gráficos integrados': 'No', 'TDP': '65 W'})
        self.assertEqual(gfx('AMD Radeon 780M'), {'Socket': 'AM5', 'Gráficos integrados': 'Sí', 'GPU integrada': 'Radeon 780M', 'TDP': '65 W'})
        self.assertEqual(gfx('Intel UHD Graphics 770')['GPU integrada'], 'Intel UHD Graphics 770')
        self.assertEqual(gfx('Sí'), {'Socket': 'AM5', 'Gráficos integrados': 'Sí', 'TDP': '65 W'})  # no model stated, none invented
        once = gfx('AMD Radeon Graphics, 2 CU'); self.assertEqual(ns('cpu', once), once)
        self.assertEqual(ns('cpu', {'Disipador incluido': 'AMD Wraith Stealth'}), {'Disipador incluido': 'Sí', 'Modelo disipador': 'AMD Wraith Stealth'})
        self.assertEqual(ns('cpu', {'Disipador incluido': 'No'}), {'Disipador incluido': 'No'})
        self.assertEqual(ns('motherboard', {'Formato': 'Micro-ATX (24.4 × 24.4 cm)'}), {'Formato': 'Micro-ATX', 'Dimensiones': '24.4 × 24.4 cm'})
        self.assertEqual(ns('motherboard', {'Formato': 'ATX (30,5 x 24,4 cm)', 'Dimensiones': '305 x 244 mm'}), {'Formato': 'ATX', 'Dimensiones': '305 x 244 mm'})
        lat = lambda v: ns('ram', {'Latencia': v})
        self.assertEqual(lat('CL16'), {'Latencia': 'CL16'})
        self.assertEqual(lat('CL18-22-22-42'), {'Latencia': 'CL18', 'Timings': '18-22-22-42'})
        self.assertEqual(lat('CL16 (16-20-20)'), {'Latencia': 'CL16', 'Timings': '16-20-20'})
        self.assertEqual(lat('CL30-40-40'), {'Latencia': 'CL30', 'Timings': '30-40-40'})
        self.assertEqual(ns('ram', {'Latencia': 'CL16-18-18-38', 'Timings': '16-18-18-36'}), {'Latencia': 'CL16', 'Timings': '16-18-18-36'})
        self.assertEqual(ns('ram', {'Capacidad': '32 GB (2×16 GB)'}), {'Capacidad': '32 GB', 'Módulos': '2 × 16 GB'})
        # speed/type spelled in many ways all end as "Tipo de memoria" + "Velocidad: N MT/s"
        self.assertEqual(ns('ram', {'speed': 'DDR4-3200'}), {'Tipo de memoria': 'DDR4', 'Velocidad': '3200 MT/s'})
        self.assertEqual(ns('ram', {'Memory': '16GB kit (2×8GB) DDR5-5200'}), {'Tipo de memoria': 'DDR5', 'Velocidad': '5200 MT/s', 'Capacidad': '16 GB', 'Módulos': '2 × 8 GB'})
        self.assertEqual(ns('ram', {'Memory type': 'DDR4', 'Tested speed': 'Up to 3200 MT/s with XMP'}), {'Tipo de memoria': 'DDR4', 'Velocidad': '3200 MT/s'})
        self.assertEqual(ns('ram', {'Velocidad probada (XMP/EXPO)': '6000 MT/s', 'Configuración': '2 × 32 GB'}), {'Velocidad': '6000 MT/s', 'Módulos': '2 × 32 GB', 'Capacidad': '64 GB'})  # the kit total is derived from the stated modules
        self.assertEqual(ns('ram', {'Formato': '288-pin non-ECC unbuffered DIMM'}), {'Formato': 'UDIMM'})
        self.assertEqual(ns('gpu', {'Memoria': '16 GB GDDR7', 'Interfaz': 'PCIe 5.0 x8'}),
                         {'Memoria': '16 GB', 'Tipo de memoria': 'GDDR7', 'Interfaz': 'PCIe 5.0', 'Enlace PCIe': 'x8'})
        self.assertEqual(ns('storage', {'Disipador integrado': 'Sí (disipador de grafeno ultrafino)'}),
                         {'Disipador integrado': 'Sí', 'Disipador': 'Disipador de grafeno ultrafino'})
        self.assertEqual(ns('cooler', {'Tipo': 'Aire, doble torre', 'Radiador': '360 mm (394 × 120 × 27 mm)', 'Ventiladores': '3 × 120 mm (F120P)'}),
                         {'Tipo': 'Aire', 'Diseño': 'Doble torre', 'Radiador': '360 mm', 'Detalle radiador': '394 × 120 × 27 mm',
                          'Ventiladores': '3 × 120 mm', 'Detalle ventiladores': 'F120P'})

    def test_cpu_filters_and_numeric_facet_order_contract(self):
        import re
        ts = (Path(__file__).resolve().parents[1]/'packages/api-contracts/catalog-filters.ts').read_text(encoding='utf8')
        cpu = re.search(r"cpu: \[([^\]]*)\]", ts).group(1)
        self.assertNotIn('Memoria compatible', cpu); self.assertNotIn('Potencia base', cpu); self.assertIn("'Gráficos integrados'", cpu)
        self.assertIn('compareFacetValues', ts)

    def test_psu_filters_expose_both_certifications(self):
        import re
        ts = (Path(__file__).resolve().parents[1]/'packages/api-contracts/catalog-filters.ts').read_text(encoding='utf8')
        psu = re.search(r"psu: \[([^\]]*)\]", ts).group(1)
        self.assertIn("'Eficiencia 80 PLUS'", psu); self.assertIn("'Cybenetics'", psu); self.assertNotIn("'Eficiencia'", psu)
        order = re.search(r"Cybenetics: \[([^\]]*)\]", ts).group(1)
        self.assertEqual(re.findall(r"'Cybenetics (\w+)'", order), ['Bronze', 'Silver', 'Gold', 'Platinum', 'Titanium', 'Diamond'])

    def test_list_valued_specs_are_canonical_tokens_in_a_fixed_order(self):
        from spec_normalize import normalize_specs as ns, LIST_ORDER
        case = lambda value: ns('case', {'Formatos compatibles': value})['Formatos compatibles']
        self.assertEqual(case('Micro-ATX, ITX'), 'Micro-ATX, Mini-ITX')
        self.assertEqual(case('mATX / Micro ATX / microATX, EATX, atx, ATX'), 'E-ATX, ATX, Micro-ATX')
        self.assertEqual(case('Mini-ITX, Micro-ATX, ATX'), 'ATX, Micro-ATX, Mini-ITX')
        self.assertEqual(case('EE-ATX, SSI-EEB, SSI-CEB, E-ATX, ATX, Micro-ATX, Mini-ITX'), 'EE-ATX, SSI-EEB, SSI-CEB, E-ATX, ATX, Micro-ATX, Mini-ITX')
        # A non-format word leaves the list and is kept verbatim under its own key.
        self.assertEqual(ns('case', {'Formatos compatibles': 'Mini-ITX, Micro-ATX, ATX, reverse connector'}),
                         {'Formatos compatibles': 'ATX, Micro-ATX, Mini-ITX', 'Compatibilidad conectores traseros': 'Reverse connector'})
        self.assertEqual(ns('case', {'Formato fuente': 'ATX, SFX, SFX-L'}), {'Formato fuente': 'ATX, SFX-L, SFX'})
        sockets = lambda value: ns('cooler', {'Sockets compatibles': value})['Sockets compatibles']
        self.assertEqual(sockets('AM4, AM5, LGA1700, LGA1851'), 'AM5, AM4, LGA1851, LGA1700')
        self.assertEqual(sockets('AM2/AM3/AM4/AM5, LGA1150/1151/1155/1156/1200/1700/1851'), 'AM5, AM4, AM3, AM2, LGA1851, LGA1700, LGA1200, LGA1151, LGA1150, LGA1155, LGA1156')
        self.assertEqual(sockets('Intel LGA115x, 1200, 1700, 2011, 2011-v3, 2066; AMD AM4, AM5'), 'AM5, AM4, LGA1700, LGA1200, LGA2066, LGA2011-v3, LGA2011, LGA115x')
        self.assertEqual(sockets('AMD AM5; Intel LGA1700'), 'AM5, LGA1700')
        self.assertEqual(ns('cooler', {'Sockets': 'Intel LGA 1700/1851; AMD AM5/AM4'}), {'Sockets compatibles': 'AM5, AM4, LGA1851, LGA1700'})
        profile = lambda value: ns('ram', {'Perfil': value})['Perfil']
        self.assertEqual(profile('XMP / EXPO'), 'AMD EXPO, Intel XMP')
        self.assertEqual(profile('Intel XMP 3.0, Intel XMP, XMP 3.0'), 'Intel XMP, Intel XMP 3.0')
        self.assertEqual(profile('AMD EXPO 1.1 e Intel XMP 3.0'), 'AMD EXPO, AMD EXPO 1.1, Intel XMP, Intel XMP 3.0')
        self.assertEqual(profile('Intel XMP 3.0 y AMD EXPO'), 'AMD EXPO, Intel XMP, Intel XMP 3.0')
        # Idempotent, and the token order mirrors the facet order the edge API uses.
        self.assertEqual(sockets(sockets('AM4, AM5, LGA1700')), sockets('AM4, AM5, LGA1700'))
        ts = (Path(__file__).resolve().parents[1]/'packages/api-contracts/catalog-filters.ts').read_text(encoding='utf8')
        import re
        for (category, key), order in LIST_ORDER.items():
            block = re.search(r"['\"]?%s['\"]?: (\[[^\]]*\]|formFactors)" % re.escape(key), ts, re.S).group(1)
            if block == 'formFactors': block = re.search(r'const formFactors = (\[[^\]]*\])', ts).group(1)
            self.assertEqual(re.findall(r"'([^']+)'", block), order, key)

    def test_brand_normalization_keeps_identity_and_review_approval(self):
        from datetime import datetime,timezone
        from import_catalog import canonical_brand
        self.assertEqual([canonical_brand(b) for b in ['Asus','ASUS ','Asrock','A-Data Technology','Abysm','G.Skill','Team Group','Powercolor','Zotac','Patriot Memory','Unknown Co','Abysm Gaming','ABYSM','Hyte','HYTE ','hYtE','Seagate Technology']],
          ['ASUS','ASUS','ASRock','ADATA','Abysm','G.SKILL','TEAMGROUP','PowerColor','ZOTAC','Patriot','Unknown Co','Abysm','Abysm','HYTE','HYTE','HYTE','Seagate'])
        self.assertNotEqual(canonical_brand('HYTE'), canonical_brand('Lian Li'))
        self.assertNotEqual(canonical_brand('Seagate'), canonical_brand('Western Digital'))
        migrate_reviews(self.conn)
        product=dict(id='asus-board',slug='asus-board',name='ASUS Board',category_id='motherboard',brand='Asus',mpn='90MB-TEST',
          manufacturer_url='https://www.asus.com/example',specs_verified_at='2026-11-01T00:00:00Z',specs={'Socket':'AM5'})
        # Legacy rows registered under the old spelling are moved to the canonical scope on re-import.
        self.conn.execute("INSERT INTO product_identifiers(kind,scope,value,product_id) VALUES('mpn','Asus','90mb-test','asus-board')")
        import_products(self.conn,[product])
        self.assertEqual(self.conn.execute("SELECT data->>'brand' FROM catalog_entities WHERE entity='product'").fetchone()[0],'ASUS')
        self.assertEqual({r[0] for r in self.conn.execute("SELECT scope FROM product_identifiers WHERE product_id='asus-board'").fetchall()},{'ASUS'})
        with self.assertRaises(ValueError): import_products(self.conn,[{**product,'id':'asus-board-2','brand':'ASUS','mpn':'x'}])
        # "Abysm Gaming" -> "Abysm": legacy scopes move on re-import and an alias written with either spelling still matches.
        abysm=dict(id='abysm-case',slug='abysm-case',name='Abysm Morpheo GX5',category_id='case',brand='Abysm Gaming',mpn='ABY-GX5',
          manufacturer_url='https://abysm.example/gx5',specs_verified_at='2026-11-01T00:00:00Z',specs={'Panel lateral':'Cristal templado'})
        self.conn.execute("INSERT INTO product_identifiers(kind,scope,value,product_id) VALUES('mpn','Abysm Gaming','aby-gx5','abysm-case')")
        import_products(self.conn,[abysm])
        self.assertEqual(self.conn.execute("SELECT data->>'brand' FROM catalog_entities WHERE entity_id='abysm-case'").fetchone()[0],'Abysm')
        self.assertEqual({r[0] for r in self.conn.execute("SELECT scope FROM product_identifiers WHERE product_id='abysm-case'").fetchall()},{'Abysm'})
        import_aliases(self.conn,[abysm],[dict(product_id='abysm-case',kind='mpn',value='ABY-GX5-BK',scope='Abysm Gaming',source_url='https://www.coolmod.com/aby',note='x')])
        self.assertEqual(self.conn.execute("SELECT product_id FROM product_identifiers WHERE kind='mpn' AND scope='Abysm' AND value='aby-gx5-bk'").fetchone()[0],'abysm-case')
        candidate=dict(shop_id='coolmod',retailer_sku='asus-sku',mpn='90MB-TEST',name='Placa',price_cents=10000,in_stock=True,
          last_checked_at=datetime.now(timezone.utc).isoformat(),url_public='https://www.coolmod.com/asus',missing_fields=[])
        review=stage_candidates(self.conn,[candidate])[0]
        with self.conn.transaction():approve(self.conn,review,'MPN exacto verificado contra ficha oficial.','asus-board')
        self.assertEqual(self.conn.execute("SELECT status FROM retailer_review_queue WHERE review_id=%s",(review,)).fetchone()[0],'approved')

    def test_review_queue_deduplicates_and_never_publishes_before_approval(self):
        from datetime import datetime,timezone,timedelta
        migrate_reviews(self.conn)
        product=dict(id='review-cpu',slug='review-cpu',name='Review CPU',category_id='cpu',brand='AMD',mpn='VERIFIED-BOX',
          manufacturer_url='https://www.amd.com/example',specs_verified_at='2026-11-01T00:00:00Z',specs={'Socket':'AM5'})
        import_products(self.conn,[product])
        now=datetime.now(timezone.utc)
        candidate=dict(shop_id='coolmod',retailer_sku='review-sku',mpn='VERIFIED-BOX',name='Raw store title',
          price_cents=30000,in_stock=True,last_checked_at=now.isoformat(),url_public='https://www.coolmod.com/example',missing_fields=['verified_specifications'])
        first=stage_candidates(self.conn,[candidate])[0]
        repeated=stage_candidates(self.conn,[{**candidate,'last_checked_at':(now+timedelta(seconds=1)).isoformat()}])[0]
        self.assertEqual(first,repeated)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0],0)
        with self.conn.transaction():approve(self.conn,first,'Official boxed MPN verified against source; raw EUR price and stock reviewed.')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0],1)
        self.assertIsNone(approve(self.conn,first,'Repeat review'))
        self.assertEqual(stage_candidates(self.conn,[{**candidate,'price_cents':29000,'last_checked_at':(now+timedelta(seconds=2)).isoformat()}]),[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM retailer_review_queue').fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT data->>'price_cents' FROM catalog_entities WHERE entity='offer'").fetchone()[0],'29000')
        unknown=stage_candidates(self.conn,[{**candidate,'retailer_sku':'unknown','mpn':'UNVERIFIED'}])[0]
        with self.assertRaises(ValueError):approve(self.conn,unknown,'Insufficient identity')
        self.assertEqual(self.conn.execute("SELECT status FROM retailer_review_queue WHERE review_id=%s",(unknown,)).fetchone()[0],'pending')
        reject(self.conn,unknown,'Otra presentación o referencia no admitida.')
        self.assertEqual(stage_candidates(self.conn,[{**candidate,'retailer_sku':'unknown','mpn':'UNVERIFIED','price_cents':25000}]),[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM retailer_review_queue').fetchone()[0],2)

    def test_review_approval_fails_closed_on_structured_variant_conflict(self):
        from datetime import datetime,timezone
        migrate_reviews(self.conn)
        product=dict(id='conflict-cpu',slug='conflict-cpu',name='Conflict CPU',category_id='cpu',brand='AMD',mpn='EXACT-CPU',
          manufacturer_url='https://www.amd.com/example',specs_verified_at='2026-11-01T00:00:00Z',specs={'Socket':'AM5'})
        import_products(self.conn,[product])
        candidate=dict(shop_id='coolmod',retailer_sku='conflict-sku',mpn='STORE-CPU',name='Conflict CPU',price_cents=30000,
          in_stock=True,last_checked_at=datetime.now(timezone.utc).isoformat(),url_public='https://www.coolmod.com/conflict',
          missing_fields=['manufacturer_variant_conflict'],identity_status='retailer_ean_variant_conflict')
        review=stage_candidates(self.conn,[candidate])[0]
        alias=dict(product_id='conflict-cpu',kind='mpn',value='STORE-CPU',source_url=candidate['url_public'],note='Reviewed retailer alias to the exact canonical product.')
        with self.assertRaisesRegex(ValueError,'unresolved manufacturer variant conflict'):
            with self.conn.transaction():
                import_aliases(self.conn,[product],[alias])
                approve(self.conn,review,'Exact MPN, conflicting UPC/EAN must remain held.','conflict-cpu')
        self.assertEqual(self.conn.execute("SELECT status FROM retailer_review_queue WHERE review_id=%s",(review,)).fetchone()[0],'pending')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0],0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM product_identifiers WHERE kind='mpn' AND value='store-cpu'").fetchone()[0],0)

    def test_url_only_discovery_promotes_to_sku_and_classification_survives_price_changes(self):
        from datetime import datetime,timezone
        migrate_reviews(self.conn)
        product=dict(id='known-ram',slug='known-ram',name='Known RAM',category_id='ram',brand='Corsair',mpn='KNOWN-KIT',
          manufacturer_url='https://www.corsair.com/example',specs_verified_at='2026-11-01T00:00:00Z',specs={'Tipo de memoria':'DDR5'})
        import_products(self.conn,[product])
        candidate=dict(shop_id='coolmod',retailer_sku='https://www.coolmod.com/known-kit',url_public='https://www.coolmod.com/known-kit/',
          name='Known RAM',mpn=None,price_cents=15000,in_stock=None,last_checked_at=datetime.now(timezone.utc).isoformat())
        initial=stage_candidates(self.conn,[candidate])[0]
        detail={**candidate,'retailer_sku':'REAL-SKU','mpn':'KNOWN-KIT','url_public':'https://www.coolmod.com/known-kit'}
        self.assertEqual(stage_candidates(self.conn,[detail]),[initial])
        self.assertIsNone(approve(self.conn,initial,'Exact manufacturer MPN reviewed; stock still unverified.'))
        self.assertEqual(stage_candidates(self.conn,[{**detail,'price_cents':14000}]),[])
        self.assertEqual(stage_candidates(self.conn,[candidate]),[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM retailer_review_queue').fetchone()[0],1)
        changed={**detail,'mpn':'NEW-IDENTITY','in_stock':True}
        reopened=stage_candidates(self.conn,[changed])
        self.assertEqual(len(reopened),1)
        self.assertNotEqual(reopened[0],initial)
        self.assertEqual(stage_candidates(self.conn,[changed]),reopened)

    def test_enrichment_fills_mpn_ean_sku_on_pending_row_and_proposes_exact_verified_product(self):
        from datetime import datetime,timezone
        migrate_reviews(self.conn)
        product=dict(id='psu-enrich',slug='psu-enrich',name='Enrich PSU',category_id='psu',brand='Gigabyte',mpn='GP-P750GM',
          manufacturer_url='https://www.gigabyte.com/example',specs_verified_at='2026-11-01T00:00:00Z',specs={'Potencia':'750 W'})
        import_products(self.conn,[product])
        listing=dict(shop_id='coolmod',retailer_sku='https://www.coolmod.com/gigabyte-p750gm',url_public='https://www.coolmod.com/gigabyte-p750gm/',
          name='Gigabyte P750GM',brand='Gigabyte',mpn=None,ean=None,price_cents=None,in_stock=None,
          missing_fields=['retailer_sku','mpn','ean','price','stock'],last_checked_at=datetime.now(timezone.utc).isoformat())
        review=stage_candidates(self.conn,[listing])[0]
        self.assertIsNone(self.conn.execute('SELECT proposed_product_id FROM retailer_review_queue WHERE review_id=%s',(review,)).fetchone()[0])
        enriched=dict(shop_id='coolmod',retailer_sku='PROD-012017',url_public='https://www.coolmod.com/gigabyte-p750gm/',mpn='GP-P750GM',
          ean='4719331551759',price_cents=7795,in_stock=True,last_checked_at=datetime.now(timezone.utc).isoformat(),
          missing_fields=['manufacturer_url','verified_specifications'])
        self.assertEqual(stage_candidates(self.conn,[enriched]),[review])
        row=self.conn.execute('SELECT candidate,proposed_product_id,retailer_sku,status FROM retailer_review_queue WHERE review_id=%s',(review,)).fetchone()
        self.assertEqual((row[0]['mpn'],row[0]['ean'],row[0]['retailer_sku'],row[1],row[2],row[3]),('GP-P750GM','4719331551759','PROD-012017','psu-enrich','PROD-012017','pending'))
        self.assertNotIn('retailer_sku',row[0]['missing_fields'])
        self.assertNotIn('mpn',row[0]['missing_fields'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM retailer_review_queue').fetchone()[0],1)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0],0)

    def test_unchanged_offer_republishes_freshness_at_most_every_twenty_four_hours(self):
        import catalog
        self.assertEqual(catalog.FRESHNESS_SECONDS, 86400)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop('OFFER_FRESHNESS_SECONDS', None)
            self.assertEqual(catalog.freshness_seconds(), 86400)
        with patch.dict(os.environ, {'OFFER_FRESHNESS_SECONDS': '21600'}):
            self.assertEqual(catalog.freshness_seconds(), 21600)  # the env override still wins
        data = dict(product_id='p', shop_id='s', price_cents=100, in_stock=True, last_checked_at='2026-01-01T00:00:00Z')
        count = lambda: self.conn.execute('SELECT count(*) FROM catalog_outbox').fetchone()[0]
        write(self.conn, 'offer', 'o', data)
        write(self.conn, 'offer', 'o', {**data, 'last_checked_at': '2026-01-01T06:01:00Z'})  # 6 h no longer republishes
        write(self.conn, 'offer', 'o', {**data, 'last_checked_at': '2026-01-01T23:59:00Z'})
        self.assertEqual(count(), 1)
        # Measured against the last PUBLISHED mark (00:00), not against the 23:59 observation.
        write(self.conn, 'offer', 'o', {**data, 'last_checked_at': '2026-01-02T00:01:00Z'})
        self.assertEqual(count(), 2)
        self.assertEqual(self.conn.execute('SELECT version FROM catalog_entities').fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT data->>'last_checked_at' FROM catalog_outbox WHERE version=2").fetchone()[0], '2026-01-02T00:01:00Z')
        write(self.conn, 'offer', 'o', {**data, 'last_checked_at': '2026-01-02T07:00:00Z'})
        self.assertEqual(count(), 2)
        # An older observation never re-publishes, and the freshness window is configurable.
        write(self.conn, 'offer', 'o', {**data, 'last_checked_at': '2025-12-31T00:00:00Z'})
        self.assertEqual(count(), 2)
        write(self.conn, 'offer', 'o', {**data, 'last_checked_at': '2026-01-02T07:30:00Z'}, freshness=60)
        self.assertEqual(count(), 3)

    def _live_offers(self, shop, count, checked='2026-01-01T00:00:00Z'):
        ids = [f'{shop}-{n:02d}' for n in range(count)]
        for offer_id in ids:
            write(self.conn, 'offer', offer_id, dict(product_id='p', shop_id=shop, price_cents=100, in_stock=True,
                                                    last_checked_at=checked, url_public='https://example.test/x'))
        return ids

    def _shop_checks(self, shop='coolmod'):
        return self.conn.execute("""SELECT version,data FROM catalog_outbox WHERE entity='shop_check' AND entity_id=%s
          ORDER BY version""", (shop,)).fetchall()

    def test_shop_check_after_full_pass_lists_live_minus_confirmed(self):
        from datetime import datetime, timezone
        from catalog import publish_shop_check
        ids = self._live_offers('coolmod', 10)
        self._live_offers('pccomponentes', 3)  # another shop never leaks into the list
        write(self.conn, 'offer', 'coolmod-gone', dict(product_id='p', shop_id='coolmod', price_cents=1, in_stock=True,
              last_checked_at='2026-01-01T00:00:00Z', url_public='https://example.test/x'), deleted=True)  # not live
        T = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        result = publish_shop_check(self.conn, 'coolmod', T, set(ids[:9]) | {'not-a-live-offer'}, now=T)
        self.assertEqual((result['published'], result['reason'], result['version'], result['unconfirmed']), (True, 'ok', 1, 1))
        self.assertEqual(self._shop_checks(), [(1, {'checked_at': '2026-01-01T10:00:00Z', 'unconfirmed_offer_ids': [ids[9]]})])
        self.assertEqual(self.conn.execute("SELECT operation FROM catalog_outbox WHERE entity='shop_check'").fetchone()[0], 'upsert')

    def test_shop_check_partial_pass_does_not_advance_the_mark(self):
        from datetime import datetime, timedelta, timezone
        from catalog import publish_shop_check
        ids = self._live_offers('coolmod', 10)
        T = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        result = publish_shop_check(self.conn, 'coolmod', T, set(ids[:7]), now=T)  # 70 % < 80 %
        self.assertEqual((result['published'], result['reason']), (False, 'partial_pass'))
        self.assertEqual(self._shop_checks(), [])
        self.assertTrue(publish_shop_check(self.conn, 'coolmod', T, set(ids[:8]), now=T)['published'])  # exactly 80 %
        later = T + timedelta(hours=1)
        result = publish_shop_check(self.conn, 'coolmod', later, set(ids[:3]), now=later)  # a bad pass later keeps the old mark
        self.assertEqual((result['published'], result['reason']), (False, 'partial_pass'))
        self.assertEqual([row[1]['checked_at'] for row in self._shop_checks()], ['2026-01-01T10:00:00Z'])
        self.assertEqual(publish_shop_check(self.conn, 'unknown-shop', T, set(), now=T)['reason'], 'no_live_offers')

    def test_shop_check_unchanged_pass_publishes_nothing_within_the_minimum_interval(self):
        from datetime import datetime, timedelta, timezone
        from catalog import publish_shop_check
        ids = self._live_offers('coolmod', 10)
        T = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        reason = lambda at, confirmed=ids: publish_shop_check(self.conn, 'coolmod', at, set(confirmed), now=at + timedelta(minutes=1))['reason']
        self.assertEqual(reason(T), 'ok')
        self.assertEqual(reason(T), 'not_newer')                               # same pass again
        self.assertEqual(reason(T - timedelta(hours=1)), 'not_newer')          # never goes backwards
        self.assertEqual(reason(T + timedelta(minutes=10)), 'too_soon')        # a new pass 10 min later
        self.assertEqual(reason(T + timedelta(minutes=10), ids[:8]), 'too_soon')  # nor does a changed exception list
        self.assertEqual(len(self._shop_checks()), 1)
        self.assertEqual(reason(T + timedelta(minutes=31), ids[:8]), 'ok')
        rows = self._shop_checks()
        self.assertEqual([row[0] for row in rows], [1, 2])
        self.assertEqual(rows[1][1], {'checked_at': '2026-01-01T10:31:00Z', 'unconfirmed_offer_ids': sorted(ids[8:])})

    def test_shop_check_is_never_in_the_future(self):
        from datetime import datetime, timedelta, timezone
        from catalog import publish_shop_check
        ids = self._live_offers('coolmod', 2)
        now = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        publish_shop_check(self.conn, 'coolmod', now + timedelta(hours=2), set(ids), now=now)
        self.assertEqual(self._shop_checks()[0][1]['checked_at'], '2026-01-01T10:00:00Z')

    def test_shop_check_migrates_an_old_entity_check_constraint(self):
        from datetime import datetime, timezone
        from catalog import ensure_shop_check_schema, publish_shop_check
        self.conn.execute('ALTER TABLE catalog_entities DROP CONSTRAINT catalog_entities_entity_check')
        self.conn.execute("ALTER TABLE catalog_entities ADD CONSTRAINT catalog_entities_entity_check CHECK(entity IN ('category','shop','product','offer'))")
        with self.assertRaises(psycopg.errors.CheckViolation):
            write(self.conn, 'shop_check', 'coolmod', {'checked_at': '2026-01-01T00:00:00Z', 'unconfirmed_offer_ids': []})
        ids = self._live_offers('coolmod', 2)
        T = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
        self.assertTrue(publish_shop_check(self.conn, 'coolmod', T, set(ids), now=T)['published'])
        ensure_shop_check_schema(self.conn); ensure_shop_check_schema(self.conn)  # idempotent
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM pg_constraint WHERE conrelid='catalog_entities'::regclass
          AND contype='c'""").fetchone()[0], 1)

    def test_ingest_snapshots_publishes_one_shop_check_per_pass(self):
        import json
        from datetime import datetime, timezone, timedelta
        from sync_agent import ingest_snapshots
        ago = lambda **delta: (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()
        offer = self._offer_fixture(datetime.now(timezone.utc) - timedelta(hours=2))
        with self._tmp() as folder:
            snapshot = Path(folder, 'pccomponentes.json')
            snapshot.write_text(json.dumps([offer]))
            env = {'RETAILER_SNAPSHOT_DIR': folder}
            signatures, passes = {}, {}
            ingest_snapshots(self.conn, signatures, env, passes)
            rows = self._shop_checks('pccomponentes')
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][1]['unconfirmed_offer_ids'], [])
            self.assertEqual(rows[0][1]['checked_at'][:16], offer['last_checked_at'][:16])
            ingest_snapshots(self.conn, signatures, env, passes)  # same bytes: nothing new
            self.assertEqual(len(self._shop_checks('pccomponentes')), 1)
            # A second pass 10 minutes later is within the minimum interval: no new event.
            snapshot.write_text(json.dumps([{**offer, 'last_checked_at': ago(hours=1, minutes=50)}]))
            ingest_snapshots(self.conn, signatures, env, passes)
            self.assertEqual(len(self._shop_checks('pccomponentes')), 1)
            # 1 of 2 live offers confirmed: partial pass, the mark stays.
            write(self.conn, 'offer', 'pccomponentes-extra', dict(product_id='amd-example', shop_id='pccomponentes', price_cents=1,
                  in_stock=True, last_checked_at='2026-01-01T00:00:00Z', url_public='https://www.pccomponentes.com/x'))
            snapshot.write_text(json.dumps([{**offer, 'last_checked_at': ago(minutes=30)}]))
            ingest_snapshots(self.conn, signatures, env, passes)
            self.assertEqual(len(self._shop_checks('pccomponentes')), 1)
            # Once the extra offer is gone, the full pass advances the mark again (an hour after the first one).
            self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0], 2)
            self.conn.execute("DELETE FROM catalog_entities WHERE entity_id='pccomponentes-extra'")
            snapshot.write_text(json.dumps([{**offer, 'last_checked_at': ago(minutes=20)}]))
            ingest_snapshots(self.conn, signatures, env, passes)
            rows = self._shop_checks('pccomponentes')
            self.assertEqual([row[0] for row in rows], [1, 2])
            self.assertEqual(rows[1][1]['unconfirmed_offer_ids'], [])

    @staticmethod
    def _tmp():
        import tempfile
        for base in (None, '/dev/shm'):
            try: return tempfile.TemporaryDirectory(dir=base)
            except OSError: continue
        raise unittest.SkipTest('No writable temporary directory')

    def _offer_fixture(self, now):
        product=dict(id='amd-example',slug='amd-example',name='AMD Example',category_id='cpu',brand='AMD',mpn='BOXED-123',
          manufacturer_url='https://www.amd.com/example',specs_verified_at='2026-11-01T00:00:00Z',specs={'Socket':'AM5'})
        import_products(self.conn,[product])
        return dict(product_id='amd-example',shop_id='pccomponentes',retailer_sku='123',mpn='BOXED-123',
          price_cents=10000,in_stock=True,last_checked_at=now.isoformat(),url_public='https://www.pccomponentes.com/example',
          match_evidence='Official boxed MPN matches retailer boxed MPN')

    def test_explicit_unavailable_keeps_last_price_and_requires_a_published_offer(self):
        from datetime import datetime,timezone,timedelta
        now=datetime.now(timezone.utc)-timedelta(hours=1)
        offer=self._offer_fixture(now)
        gone={k:v for k,v in offer.items() if k!='price_cents'}
        gone.update(unavailable=True,in_stock=False,last_checked_at=datetime.now(timezone.utc).isoformat())
        with self.assertRaises(ValueError): import_offer(self.conn,gone)
        import_offer(self.conn,offer)
        import_offer(self.conn,gone)
        data=self.conn.execute("SELECT data FROM catalog_entities WHERE entity='offer'").fetchone()[0]
        self.assertEqual((data['price_cents'],data['in_stock']),(10000,False))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer' AND deleted_at IS NOT NULL").fetchone()[0],0)
        with self.assertRaises(ValueError): import_offer(self.conn,{**gone,'in_stock':True})

    def test_snapshot_directory_isolates_invalid_files_and_rejected_offers(self):
        import json,tempfile
        from datetime import datetime,timezone,timedelta
        from sync_agent import ingest_snapshots
        now=datetime.now(timezone.utc)-timedelta(minutes=5)
        good=self._offer_fixture(now)
        bad={**good,'retailer_sku':'999','mpn':'UNKNOWN'}
        with self._tmp() as folder:
            Path(folder,'a-broken.json').write_text('{not json')
            Path(folder,'b-toobig.json').write_text(json.dumps([good]*4))
            Path(folder,'c-mixed.json').write_text(json.dumps([bad,good,'not an object']))
            env={'RETAILER_SNAPSHOT_DIR':folder,'RETAILER_SNAPSHOT_LIMIT':'3'}
            signatures={}
            ingest_snapshots(self.conn,signatures,env)
            self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0],1)
            self.assertEqual(len(signatures),3)
            before=self.conn.execute('SELECT count(*) FROM catalog_outbox').fetchone()[0]
            ingest_snapshots(self.conn,signatures,env)  # same bytes: not imported again
            self.assertEqual(self.conn.execute('SELECT count(*) FROM catalog_outbox').fetchone()[0],before)
            # Compatibility with the single-file variable.
            single=Path(folder,'single.txt'); single.write_text(json.dumps([{**good,'last_checked_at':datetime.now(timezone.utc).isoformat()}]))
            ingest_snapshots(self.conn,signatures,{'RETAILER_SNAPSHOT_FILE':str(single)})
            self.assertIn(str(single),signatures)

    def test_push_falls_back_to_cloudflare_ips_with_fresh_signatures(self):
        import sync_agent
        import urllib.error
        seen=[]
        def fake(parsed,body,headers,ip=None):
            seen.append((ip,dict(headers)))
            if ip!='172.67.74.226': raise TimeoutError('blocked')
            return {'acknowledged_event_ids':[1]}
        endpoint='https://stockfinder.example.workers.dev/internal/sync'
        sync_agent._preferred.update(ip=None,until=0.0)
        with patch('sync_agent._request_once',side_effect=fake), patch.dict(os.environ,{'SYNC_FALLBACK_IPS':'104.18.2.1, bad,172.67.74.226'}):
            ids=sync_agent.push(endpoint,'x'*32,[{'event_id':1}])
        self.assertEqual(ids,[1])
        self.assertEqual([ip for ip,_ in seen],[None,'104.18.2.1','172.67.74.226'])
        self.assertEqual(len({h['X-Sync-Nonce'] for _,h in seen}),3)
        self.assertEqual(len({h['X-Sync-Signature'] for _,h in seen}),3)
        # The working fallback is remembered and tried first next time.
        seen.clear()
        with patch('sync_agent._request_once',side_effect=fake), patch.dict(os.environ,{'SYNC_FALLBACK_IPS':'104.18.2.1,172.67.74.226'}):
            sync_agent.push(endpoint,'x'*32,[{'event_id':1}])
        self.assertEqual(seen[0][0],'172.67.74.226')
        sync_agent._preferred.update(ip=None,until=0.0)
        # HTTP answers are final (no fallback) and an empty list disables fallbacks.
        http_error=urllib.error.HTTPError('u',403,'Forbidden',{},None)
        with patch('sync_agent._request_once',side_effect=http_error) as request:
            with self.assertRaises(urllib.error.HTTPError): sync_agent.push(endpoint,'x'*32,[{'event_id':1}])
            self.assertEqual(request.call_count,1)
        with patch('sync_agent._request_once',side_effect=TimeoutError('t')) as request, patch.dict(os.environ,{'SYNC_FALLBACK_IPS':''}):
            with self.assertRaises(TimeoutError): sync_agent.push(endpoint,'x'*32,[{'event_id':1}])
            self.assertEqual(request.call_count,1)

    def test_pinned_connection_keeps_sni_host_and_full_tls_verification(self):
        import ssl,sync_agent
        connection=sync_agent.PinnedHTTPSConnection('stockfinder.example.workers.dev','104.18.2.1')
        self.assertEqual(connection._context.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(connection._context.check_hostname)
        wrapped={}
        class Context:
            def wrap_socket(self,sock,server_hostname=None):
                wrapped['sni']=server_hostname; return sock
        class Sock:
            def settimeout(self,value): wrapped['timeout']=value
        connection._context=Context()
        with patch('socket.create_connection',return_value=Sock()) as create:
            connection.connect()
        self.assertEqual(create.call_args[0][0],('104.18.2.1',443))
        self.assertEqual(wrapped['sni'],'stockfinder.example.workers.dev')
        self.assertEqual(connection.host,'stockfinder.example.workers.dev')

if __name__ == '__main__': unittest.main()
