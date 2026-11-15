"""Real local PostgreSQL validation of reviewed LDLC batches and rollback."""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'nas')]
import test_nas
from psycopg.types.json import Jsonb
from catalog import write
from import_catalog import canonical_brand, identify
from review_queue import approve, migrate, stage_candidates
from ldlc_pipeline import classify_errors, cycle, import_reviewed_products, verified_manufacturer_document


class PipelineTests(unittest.TestCase):
    setUp = test_nas.CatalogTests.setUp
    tearDown = test_nas.CatalogTests.tearDown

    def seed(self, conflict=False):
        migrate(self.conn)
        p = dict(slug='asus-example', name='ASUS Example OC 16GB', category_id='gpu', brand='ASUS',
                 mpn='CANONICAL', manufacturer_url='https://webshop.asus.com/example',
                 specs={'Memoria': '16 GB'}, specs_verified_at='2026-11-12T00:00:00Z')
        write(self.conn, 'product', 'asus-example', p)
        identify(self.conn, 'mpn', 'ASUS', 'canonical', 'asus-example')
        row = dict(shop_id='ldlc', retailer_sku='PB00000001', mpn='RETAIL-OC', ean='4711581491205',
                   name='ASUS Example OC 16GB', brand='ASUS', category_hint='gpu', price_cents=12345,
                   in_stock=True, last_checked_at='2026-11-12T12:00:00Z',
                   url_public='https://www.ldlc.com/es-es/ficha/PB00000001.html')
        if conflict:
            identify(self.conn, 'ean', 'global', row['ean'], 'other-product')
        review_id = stage_candidates(self.conn, [row])[0]
        note = 'Verified official ASUS OC 16GB variant at https://www.asus.com/example/techspec/; exact retailer code and no variant ambiguity.'
        batch = dict(aliases=[dict(product_id='asus-example', kind='mpn', value=row['mpn'], source_url=row['url_public'], note=note)],
                     decisions=[dict(review_id=review_id, action='approve', product_id='asus-example', note=note)])
        return review_id, batch

    def apply(self, batch):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'reviews').mkdir()
            (directory / 'reviews' / 'test-batch-001.json').write_text(json.dumps(batch))
            cycle(self.conn, directory)
            # The same file must not duplicate imports on the next cycle.
            cycle(self.conn, directory)
            return json.loads((directory / 'pipeline-state.json').read_text())['files']['test-batch-001.json']

    def test_official_subdomains_and_idempotent_import(self):
        rid, batch = self.seed()
        report = self.apply(batch)
        self.assertEqual(report['approved'], 1)
        self.assertEqual(report['errors'], [])
        self.assertEqual(self.conn.execute('SELECT status FROM retailer_review_queue WHERE review_id=%s', (rid,)).fetchone()[0], 'approved')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0], 1)

    def test_hyte_case_only_brand_variants_are_equivalent(self):
        rid, batch = self.seed()
        self.conn.execute("UPDATE catalog_entities SET data=data||%s WHERE entity='product' AND entity_id='asus-example'",
                          (Jsonb({'brand': 'HYTE', 'manufacturer_url': 'https://hyte.com/store/example'}),))
        self.conn.execute("UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s",
                          (Jsonb({'brand': 'Hyte'}), rid))
        note = 'Official HYTE page identifies this exact variant and model: https://hyte.com/store/example. Exact retailer code was compared.'
        batch['aliases'][0]['note'] = note
        batch['decisions'][0]['note'] = note
        report = self.apply(batch)
        self.assertEqual((report['approved'], report['errors']), (1, []))

    def test_wd_black_case_variant_is_canonical_but_brands_stay_distinct(self):
        self.assertEqual(canonical_brand('WD_Black'), 'WD_BLACK')
        self.assertEqual(canonical_brand('wd black'), 'WD_BLACK')
        self.assertNotEqual(canonical_brand('WD_BLACK'), canonical_brand('Western Digital'))
        self.assertNotEqual(canonical_brand('Western Digital'), canonical_brand('SanDisk'))

    def test_distinct_manufacturer_and_retailer_brands_still_reject(self):
        rid, batch = self.seed()
        self.conn.execute("UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s",
                          (Jsonb({'brand': 'HYTE'}), rid))
        report = self.apply(batch)
        self.assertIn('brands differ', report['errors'][0]['error'])
        self.assertEqual(self.conn.execute('SELECT status FROM retailer_review_queue WHERE review_id=%s', (rid,)).fetchone()[0], 'pending')

    def test_errors_for_later_approved_reviews_are_historical(self):
        rid, batch = self.seed()
        self.assertEqual(self.apply(batch)['approved'], 1)
        active, historical = classify_errors(self.conn, [
            {'review_id': rid, 'error': 'old brand spelling'},
            {'stage': 'products', 'error': 'still invalid'}])
        self.assertEqual(active, [{'stage': 'products', 'error': 'still invalid'}])
        self.assertEqual(historical, [{'review_id': rid, 'error': 'old brand spelling'}])

    def test_batch_errors_resolve_after_approval_or_explicit_retention(self):
        rid, _ = self.seed()
        identify(self.conn, 'mpn', 'ASUS', 'retail-oc', 'asus-example')
        approve(self.conn, rid, 'Approved after corrected evidence.', 'asus-example')
        retained_id = self.conn.execute(
            "INSERT INTO retailer_review_queue (shop_id,retailer_sku,fingerprint,candidate,status,review_note) "
            "VALUES ('ldlc','PB00000002','retained-fingerprint',%s,'pending',%s) RETURNING review_id",
            (Jsonb({'brand': 'Fox Spirit'}),
             'Pendiente: Retenido por ausencia de MPN de fabricante y ficha oficial independiente.')).fetchone()[0]
        active, historical = classify_errors(self.conn, [
            {'stage': 'products', 'review_ids': [rid, retained_id], 'error': 'batch import failed'},
            {'stage': 'products', 'review_ids': [rid, 999999], 'error': 'missing review remains unresolved'}])
        self.assertEqual(active, [{'stage': 'products', 'review_ids': [rid, 999999],
                                   'error': 'missing review remains unresolved'}])
        self.assertEqual(historical, [{'stage': 'products', 'review_ids': [rid, retained_id],
                                       'error': 'batch import failed'}])

    def test_rewritten_batch_errors_are_preserved_as_history(self):
        _, batch = self.seed()
        batch['aliases'][0]['note'] = batch['decisions'][0]['note'] = (
            'Exact product claim at https://asus.example.com/example/techspec/; exact retailer MPN compared.')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'reviews').mkdir()
            path = directory / 'reviews' / 'rewrite-batch-001.json'
            path.write_text(json.dumps(batch))
            cycle(self.conn, directory)
            first = json.loads((directory / 'pipeline-state.json').read_text())
            self.assertEqual(first['files']['rewrite-batch-001.json']['errors'][0]['error'],
                             'Review must cite the canonical manufacturer domain')

            good = 'Official ASUS page identifies exact model and MPN at https://www.asus.com/example/techspec/; exact retailer MPN compared.'
            batch['aliases'][0]['note'] = batch['decisions'][0]['note'] = good
            path.write_text(json.dumps(batch))
            cycle(self.conn, directory)
            state = json.loads((directory / 'pipeline-state.json').read_text())
            status = json.loads((directory / 'pipeline-status.json').read_text())
            self.assertEqual(state['files']['rewrite-batch-001.json']['approved'], 1)
            self.assertEqual(len(state['superseded_errors']), 1)
            self.assertEqual(status['errors'], [])
            self.assertEqual(status['historical_errors'][0]['error'],
                             'Review must cite the canonical manufacturer domain')

    def test_conflicting_ean_rolls_back_alias_and_offer(self):
        rid, batch = self.seed(conflict=True)
        report = self.apply(batch)
        self.assertIn('EAN contradicts', report['errors'][0]['error'])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM product_identifiers WHERE value='retail-oc'").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0], 0)

    def test_new_verified_product_precedes_alias_and_offer(self):
        rid, batch = self.seed()
        pid = 'asus-new-oc-16gb'
        product = dict(id=pid, slug=pid, name='ASUS New OC 16GB', brand='ASUS', category_id='gpu',
                       mpn='NEW-OC', mpn_source='manufacturer', specs={'Memoria': '16 GB', 'Tipo de memoria': 'GDDR7', 'Interfaz': 'PCI Express 5.0'},
                       manufacturer_url='https://www.asus.com/new/techspec/', specs_verified_at='2026-11-12T00:00:00Z',
                       manufacturer_evidence='Official ASUS page explicitly specifies NEW-OC, 16GB GDDR7 and PCIe 5.0.')
        batch['products'] = [product]
        batch['aliases'][0]['product_id'] = pid
        batch['decisions'][0]['product_id'] = pid
        report = self.apply(batch)
        self.assertEqual((report['products'], report['approved'], report['errors']), (1, 1, []))
        self.assertEqual(self.conn.execute("SELECT data->>'product_id' FROM catalog_entities WHERE entity='offer'").fetchone()[0], pid)

    def test_new_product_without_official_evidence_is_rejected(self):
        rid, batch = self.seed()
        batch['products'] = [dict(id='new', category_id='gpu')]
        batch['decisions'][0]['product_id'] = 'new'
        report = self.apply(batch)
        self.assertIn('official evidence', report['errors'][0]['error'])
        self.assertEqual(report['products'], 0)
        self.assertEqual(report['approved'], 0)

    def test_non_gpu_components_refuse_criterion_b_aliases(self):
        rid, batch = self.seed()
        self.conn.execute("UPDATE catalog_entities SET data=data||%s WHERE entity='product' AND entity_id='asus-example'", (Jsonb({'category_id': 'cpu', 'mpn_source': 'manufacturer'}),))
        self.conn.execute('UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s', (Jsonb({'category_hint': 'cpu'}), rid))
        report = self.apply(batch)
        self.assertIn('exact manufacturer MPN', report['errors'][0]['error'])
        self.assertEqual(report['approved'], 0)

    def test_non_gpu_exact_manufacturer_mpn_is_accepted(self):
        rid, batch = self.seed()
        self.conn.execute("UPDATE catalog_entities SET data=data||%s WHERE entity='product' AND entity_id='asus-example'", (Jsonb({'category_id': 'cpu', 'mpn_source': 'manufacturer', 'mpn': 'RETAIL-OC'}),))
        self.conn.execute('UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s', (Jsonb({'category_hint': 'cpu'}), rid))
        identify(self.conn, 'mpn', 'ASUS', 'retail-oc', 'asus-example')
        batch['aliases'] = []
        batch['decisions'][0]['note'] += ' Manufacturer MPN RETAIL-OC is exact.'
        report = self.apply(batch)
        self.assertEqual((report['approved'], report['errors']), (1, []))

    def test_psu_mpn_hyphen_requires_both_codes_and_keeps_official_code(self):
        rid, batch = self.seed()
        self.conn.execute("UPDATE catalog_entities SET data=data||%s WHERE entity='product' AND entity_id='asus-example'", (Jsonb({'category_id': 'psu', 'mpn_source': 'manufacturer', 'mpn': 'SRP-CGX651-A5A32SFW'}),))
        self.conn.execute('UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s', (Jsonb({'category_hint': 'psu', 'mpn': 'SRP-CGX651-A5A32SF-W'}), rid))
        identify(self.conn, 'mpn', 'ASUS', 'srp-cgx651-a5a32sfw', 'asus-example')
        batch['aliases'] = []
        batch['decisions'][0]['note'] = 'Official https://www.asus.com/example/techspec/ manufacturer MPN SRP-CGX651-A5A32SFW; retailer MPN SRP-CGX651-A5A32SF-W inserts a hyphen only.'
        report = self.apply(batch)
        self.assertEqual((report['approved'], report['errors']), (1, []))
        self.assertEqual(self.conn.execute("SELECT data->>'mpn' FROM catalog_entities WHERE entity='product' AND entity_id='asus-example'").fetchone()[0], 'SRP-CGX651-A5A32SFW')
        self.assertEqual(self.conn.execute("SELECT product_id FROM product_identifiers WHERE kind='mpn' AND value='srp-cgx651-a5a32sf-w'").fetchone()[0], 'asus-example')

    def test_psu_mpn_hyphen_requires_evidence_and_never_changes_characters(self):
        for observed, note in [('SRP-CGX651-A5A32SF-W', 'Official manufacturer MPN SRP-CGX651-A5A32SFW https://www.asus.com/example/techspec/ without retailer spelling.'), ('SRP-CGX751-A5A32SF-W', 'Official SRP-CGX651-A5A32SFW retailer SRP-CGX751-A5A32SF-W https://www.asus.com/example/techspec/')]:
            with self.subTest(observed=observed):
                rid, batch = self.seed()
                self.conn.execute("UPDATE catalog_entities SET data=data||%s WHERE entity='product' AND entity_id='asus-example'", (Jsonb({'category_id': 'psu', 'mpn_source': 'manufacturer', 'mpn': 'SRP-CGX651-A5A32SFW'}),))
                self.conn.execute('UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s', (Jsonb({'category_hint': 'psu', 'mpn': observed}), rid))
                batch['aliases'] = []
                batch['decisions'][0]['note'] = note
                report = self.apply(batch)
                self.assertIn('exact manufacturer MPN', report['errors'][0]['error'])
                self.assertEqual(report['approved'], 0)
                self.conn.execute('DELETE FROM retailer_review_queue')
                self.conn.execute('DELETE FROM product_identifiers')
                self.conn.execute('DELETE FROM catalog_entities')
                self.conn.execute('DELETE FROM catalog_outbox')

    def test_unofficial_evidence_is_rejected(self):
        rid, batch = self.seed()
        for entry in [*batch['aliases'], *batch['decisions']]:
            entry['note'] = entry['note'].replace('www.asus.com', 'asus.example.com')
        report = self.apply(batch)
        self.assertIn('manufacturer domain', report['errors'][0]['error'])
        self.assertEqual(report['approved'], 0)

    def test_unreadable_batch_is_logged_and_retried_without_blocking_other_batches(self):
        rid, batch = self.seed()
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            reviews = directory / 'reviews'
            reviews.mkdir()
            (reviews / 'blocked-batch-001.json').write_text('{}')
            (reviews / 'readable-batch-001.json').write_text(json.dumps(batch))
            read_bytes = Path.read_bytes

            def guarded_read(path):
                if path.name == 'blocked-batch-001.json':
                    raise PermissionError('mode 0600')
                return read_bytes(path)

            output = StringIO()
            with patch('ldlc_pipeline.Path.read_bytes', guarded_read), redirect_stdout(output):
                cycle(self.conn, directory)
            self.assertIn('ldlc_review_batch_unreadable', output.getvalue())
            self.assertIn('blocked-batch-001.json', output.getvalue())
            state = json.loads((directory / 'pipeline-state.json').read_text())
            self.assertNotIn('blocked-batch-001.json', state['files'])
            self.assertIn('readable-batch-001.json', state['files'])
            self.assertEqual(self.conn.execute(
                'SELECT status FROM retailer_review_queue WHERE review_id=%s', (rid,)).fetchone()[0], 'approved')


    def rejection_batch(self, rid):
        note = 'Manufacturer documents a different variant than observed RETAIL-OC: https://www.asus.com/example/techspec/. Reject this contradicted observation only.'
        return {'decisions': [{'review_id': rid, 'action': 'reject', 'note': note,
                'rejection_evidence': {'reason_code': 'identity_conflict',
                    'source_url': 'https://www.asus.com/example/techspec/',
                    'observed': 'RETAIL-OC 16GB OC model', 'official': 'RETAIL-OC is an 8GB non-OC model'}}]}

    def test_reviewed_rejection_is_idempotent_and_creates_no_offer(self):
        rid, _ = self.seed()
        report = self.apply(self.rejection_batch(rid))
        self.assertEqual((report['rejected'], report['approved'], report['errors']), (1, 0, []))
        self.assertEqual(self.conn.execute('SELECT status FROM retailer_review_queue WHERE review_id=%s', (rid,)).fetchone()[0], 'rejected')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0], 0)
        self.assertEqual(self.apply(self.rejection_batch(rid))['rejected'], 0)

    def test_rejection_without_primary_evidence_leaves_pending(self):
        rid, _ = self.seed()
        batch = self.rejection_batch(rid)
        batch['decisions'][0].pop('rejection_evidence')
        report = self.apply(batch)
        self.assertEqual(report['rejected'], 0)
        self.assertTrue(report['errors'])
        self.assertEqual(self.conn.execute('SELECT status FROM retailer_review_queue WHERE review_id=%s', (rid,)).fetchone()[0], 'pending')

    def test_rejection_cannot_reverse_an_approved_offer(self):
        rid, batch = self.seed()
        self.apply(batch)
        report = self.apply(self.rejection_batch(rid))
        self.assertEqual(report['rejected'], 0)
        self.assertTrue(report['errors'])
        self.assertEqual(self.conn.execute('SELECT status FROM retailer_review_queue WHERE review_id=%s', (rid,)).fetchone()[0], 'approved')
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0], 1)

    def test_rejection_cannot_target_multiple_pending_fingerprints(self):
        rid, _ = self.seed()
        self.conn.execute("INSERT INTO retailer_review_queue(shop_id,retailer_sku,fingerprint,candidate) SELECT shop_id,retailer_sku,fingerprint||'-second',candidate FROM retailer_review_queue WHERE review_id=%s", (rid,))
        report = self.apply(self.rejection_batch(rid))
        self.assertEqual(report['rejected'], 0)
        self.assertTrue(report['errors'])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM retailer_review_queue WHERE status='pending'").fetchone()[0], 2)

    def test_rejection_rejects_a_retailer_or_unrelated_source(self):
        rid, _ = self.seed()
        batch = self.rejection_batch(rid)
        decision = batch['decisions'][0]
        decision['note'] = decision['note'].replace('www.asus.com', 'asus.example.com')
        decision['rejection_evidence']['source_url'] = 'https://asus.example.com/example/techspec/'
        report = self.apply(batch)
        self.assertEqual(report['rejected'], 0)
        self.assertTrue(report['errors'])


    def test_existing_canonical_refresh_requires_explicit_pending_review(self):
        rid, batch = self.seed()
        current = self.conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id='asus-example'").fetchone()[0]
        product = dict(id='asus-example', **current, mpn_source='manufacturer', manufacturer_evidence='Exact official manufacturer MPN in https://www.asus.com/example/techspec/', verification_review_id=rid)
        with self.conn.transaction():
            self.assertEqual(import_reviewed_products(self.conn, [product], batch['decisions']), 1)
        data = self.conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id='asus-example'").fetchone()[0]
        self.assertEqual(data['mpn_source'], 'manufacturer')
        self.assertNotIn('verification_review_id', data)
        product['verification_review_id'] = rid + 9999
        with self.assertRaisesRegex(ValueError, 'explicit pending'):
            import_reviewed_products(self.conn, [product], batch['decisions'])

    def test_canonical_refresh_cannot_change_identity(self):
        rid, batch = self.seed()
        current = self.conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id='asus-example'").fetchone()[0]
        product = dict(id='asus-example', **current, manufacturer_evidence='Official source verifies model https://www.asus.com/example/techspec/', verification_review_id=rid)
        product['mpn'] = 'ANOTHER-REVISION'
        with self.assertRaisesRegex(ValueError, 'collides'):
            import_reviewed_products(self.conn, [product], batch['decisions'])

    def patriot_ram_batch(self):
        rid, batch = self.seed()
        self.conn.execute("UPDATE catalog_entities SET data=data||%s WHERE entity='product' AND entity_id='asus-example'", (Jsonb({'brand':'Patriot', 'category_id':'ram', 'mpn':'RETAIL-OC', 'mpn_source':'manufacturer', 'manufacturer_url':'https://www.patriotmemory.com/products/viper-venom-ddr5-performance-ram'}),))
        self.conn.execute("UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s", (Jsonb({'brand':'Viper Gaming', 'category_hint':'ram'}), rid))
        identify(self.conn, 'mpn', 'Patriot', 'retail-oc', 'asus-example')
        decision=batch['decisions'][0]
        decision['note']='Exact RETAIL-OC MPN and RAM configuration published by Patriot for its Viper Gaming line at https://www.patriotmemory.com/products/viper-venom-ddr5-performance-ram; EAN has no conflict.'
        decision['brand_evidence']={'retailer_brand':'Viper Gaming', 'manufacturer_brand':'Patriot', 'mpn':'RETAIL-OC', 'source_url':'https://www.patriotmemory.com/products/viper-venom-ddr5-performance-ram', 'relationship':'Patriot primary product page identifies the exact Viper Gaming SKU and full RAM configuration.'}
        batch['aliases']=[]
        return rid,batch

    def test_patriot_relationship_is_per_review_and_exact_mpn(self):
        rid,batch=self.patriot_ram_batch()
        report=self.apply(batch)
        self.assertEqual((report['approved'],report['errors']),(1,[]))
        self.assertNotEqual(canonical_brand('Viper Gaming'),canonical_brand('Patriot'))

    def test_patriot_relationship_rejects_wrong_mpn_evidence(self):
        rid,batch=self.patriot_ram_batch()
        batch['decisions'][0]['brand_evidence']['mpn']='ANOTHER-SKU'
        report=self.apply(batch)
        self.assertEqual(report['approved'],0)
        self.assertIn('brands differ',report['errors'][0]['error'])


    def test_authorized_regional_reference_imports_without_an_ean_match(self):
        rid,batch=self.seed()
        self.conn.execute("UPDATE catalog_entities SET data=data||%s WHERE entity='product' AND entity_id='asus-example'", (Jsonb({'category_id':'psu','mpn':'RETAIL-OC','mpn_source':'manufacturer'}),))
        self.conn.execute("UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s", (Jsonb({'category_hint':'psu','mpn':'RETAIL-OC-BEU','ean':None}),rid))
        identify(self.conn,'mpn','ASUS','retail-oc','asus-example')
        batch['aliases']=[]
        batch['decisions'][0]['note']='Official literal base MPN RETAIL-OC at https://www.asus.com/example/techspec/ matches the retailer regional RETAIL-OC-BEU. EAN absent; no conflict.'
        report=self.apply(batch)
        self.assertEqual((report['approved'],report['errors']),(1,[]))
        self.assertEqual(self.conn.execute("SELECT product_id FROM product_identifiers WHERE kind='mpn' AND scope='ASUS' AND value='retail-oc-beu'").fetchone()[0],'asus-example')


    def sn350_batch(self):
        rid,_=self.seed()
        pid='wd-green-sn350-500-gb-wds500g2g0c-00cdh0'
        product=dict(slug=pid,name='WD Green SN350 500 GB (WDS500G2G0C-00CDH0)',brand='SanDisk',category_id='storage',mpn='WDS500G2G0C-00CDH0',mpn_source='manufacturer',manufacturer_url='https://www.westerndigital.com/products/internal-drives/wd-green-sn350-nvme-ssd',specs={'Capacidad':'500 GB'},specs_verified_at='2026-11-12T00:00:00Z')
        write(self.conn,'product',pid,product)
        self.conn.execute("INSERT INTO retailer_review_queue(review_id,shop_id,retailer_sku,fingerprint,candidate) OVERRIDING SYSTEM VALUE SELECT 7347,shop_id,retailer_sku,fingerprint||'-wd',candidate||%s FROM retailer_review_queue WHERE review_id=%s",(Jsonb({'brand':'Western Digital','category_hint':'storage','mpn':'WDS500G2G0C','ean':'0718037880099'}),rid))
        self.conn.execute('DELETE FROM retailer_review_queue WHERE review_id=%s',(rid,))
        part='https://documents.westerndigital.com/content/dam/doc-library/en_us/assets/public/mywd/download/discover-your-drive-en.pdf'
        sku='https://www.sandisk.com/products/ssd/internal-ssd/wd-green-sn350-nvme-ssd'
        return {'decisions':[{'review_id':7347,'action':'approve','product_id':pid,'note':'Exact manufacturer WD part number WDS500G2G0C and UPC 718037880099 in '+part+' identify the same 500GB SN350 whose full SKU WDS500G2G0C-00CDH0 is published at '+sku+'. Both references remain distinct, with no general suffix or brand normalization.', 'identity_evidence':{'manufacturer_part_number':'WDS500G2G0C','manufacturer_full_sku':'WDS500G2G0C-00CDH0','manufacturer_upc':'718037880099','part_number_source':part,'full_sku_source':sku}}]}

    def test_documented_sn350_reference_reuses_canonical_and_exact_barcode(self):
        batch=self.sn350_batch();report=self.apply(batch)
        self.assertEqual((report['approved'],report['errors']),(1,[]))
        self.assertEqual(self.conn.execute("SELECT product_id FROM product_identifiers WHERE kind='ean' AND scope='global' AND value='0718037880099'").fetchone()[0],batch['decisions'][0]['product_id'])
        self.assertNotEqual(canonical_brand('Western Digital'),canonical_brand('SanDisk'))

    def test_sn350_exception_does_not_accept_a_different_observed_barcode(self):
        batch=self.sn350_batch()
        self.conn.execute("UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=7347",(Jsonb({'ean':'0718037880075'}),))
        report=self.apply(batch)
        self.assertEqual(report['approved'],0)
        self.assertTrue(report['errors'])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM catalog_entities WHERE entity='offer'").fetchone()[0],0)


    def textorm_document_fixture(self, root, rid=1):
        relative='artifacts/ldlc/audit-20261115/textorm-docs/tb1-guide.pdf'
        path=root/relative;path.parent.mkdir(parents=True);path.write_bytes(b'%PDF-test-authored-document')
        url='https://media.ldlc.com/bo/file/fiches/Textorm/TEXTORM-TB1-GUIDE-MONTAGE.pdf'
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        product={'name':'Textorm TB1','brand':'Textorm','category_id':'case','manufacturer_url':'https://www.textorm.com/'}
        decision={'review_id':rid,'note':'Authentic Textorm TB1 manufacturer manual mirrored at '+url+'; manufacturer landing remains https://www.textorm.com/. No global retailer-domain trust.', 'variant_evidence':{'official_model':'TB1','retailer_model':'Textorm TB1','source_url':url,'ean_checked':True,'attributes':{'Color':'Black','Form':'Mini Tower','Motherboard':'Micro-ATX/Mini-ITX'},'document_source':{'author_brand':'Textorm','sha256':digest,'local_path':relative,'official_landing_url':'https://www.textorm.com/','authorship_evidence':'Manufacturer Textorm TB1 manual cover and Textorm support contact identify the exact authored product document.'}}}
        return path,product,decision,{rid:('TB1',url,digest,relative)}

    def test_authentic_document_source_is_hash_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,product,decision,entries=self.textorm_document_fixture(root)
            with patch('ldlc_pipeline.ROOT',root),patch.dict('ldlc_pipeline.VERIFIED_TEXTORM_DOCUMENTS',entries,clear=True):
                self.assertTrue(verified_manufacturer_document(product,decision))
                path.write_bytes(b'%PDF-modified-document')
                self.assertFalse(verified_manufacturer_document(product,decision))

    def test_authentic_document_does_not_trust_other_reviews_or_mirrors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,product,decision,entries=self.textorm_document_fixture(root)
            with patch('ldlc_pipeline.ROOT',root),patch.dict('ldlc_pipeline.VERIFIED_TEXTORM_DOCUMENTS',entries,clear=True):
                decision['review_id']=999
                self.assertFalse(verified_manufacturer_document(product,decision))
                decision['review_id']=1;decision['variant_evidence']['source_url']='https://media.ldlc.com/another.pdf'
                self.assertFalse(verified_manufacturer_document(product,decision))

    def test_reviewed_document_allows_case_variant_without_global_domain_alias(self):
        rid,_=self.seed()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path,product,decision,entries=self.textorm_document_fixture(root,rid)
            self.conn.execute("UPDATE catalog_entities SET data=data||%s WHERE entity='product' AND entity_id='asus-example'",(Jsonb({**product,'mpn':'RETAIL-OC','mpn_source':'retailer'}),))
            self.conn.execute("UPDATE retailer_review_queue SET candidate=candidate||%s WHERE review_id=%s",(Jsonb({'brand':'Textorm','name':'Textorm TB1','category_hint':'case'}),rid))
            identify(self.conn,'mpn','Textorm','retail-oc','asus-example')
            decision.update(action='approve',product_id='asus-example')
            with patch('ldlc_pipeline.ROOT',root),patch.dict('ldlc_pipeline.VERIFIED_TEXTORM_DOCUMENTS',entries,clear=True):
                report=self.apply({'decisions':[decision]})
            self.assertEqual((report['approved'],report['errors']),(1,[]))


if __name__ == '__main__':
    unittest.main()
