"""Pruebas del publicador contra PostgreSQL real, en un esquema aislado (sufijo aleatorio) y con una raíz de datos temporal."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import uuid

import psycopg
from psycopg import sql

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'nas'))
import publisher  # noqa: E402
from review_queue import stage_candidates  # noqa: E402

NOTE = 'Ficha oficial https://www.amd.com/example coincide en modelo, memoria y edición con la variante de la tienda.'


def product(pid, mpn, brand='AMD', name=None):
    return dict(id=pid, slug=pid, name=name or f'{brand} {pid}', category_id='cpu', brand=brand, mpn=mpn, mpn_source='manufacturer',
                manufacturer_url='https://www.amd.com/example', specs_verified_at='2026-11-08T10:00:00Z', specs={'Socket': 'AM5'})


def candidate(sku, mpn, **extra):
    return {**dict(shop_id='coolmod', retailer_sku=sku, mpn=mpn, name=f'Raw store title {sku}', price_cents=30000, in_stock=True,
                   last_checked_at=datetime.now(timezone.utc).isoformat(), url_public=f'https://www.coolmod.com/{sku}',
                   category_hint='cpu', missing_fields=[]), **extra}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.schema = 'test_' + uuid.uuid4().hex
        self.conn = psycopg.connect(os.environ['DATABASE_URL'], autocommit=True)
        self.conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema)))
        for name in ('0001_catalog.sql', '0002_identifiers.sql'):
            self.conn.execute((REPO / 'db/postgres-migrations' / name).read_text())
        self.tmp = Path(tempfile.mkdtemp(prefix='publisher-test-'))
        for sub in ('data/catalog', 'data/aliases', 'artifacts', 'flow'):
            (self.tmp / sub).mkdir(parents=True)
        shutil.copy(REPO / 'data/brand-domains.json', self.tmp / 'data/brand-domains.json')
        self.cfg = publisher.Config(root=self.tmp, flow_dir=self.tmp / 'flow', artifacts_dir=self.tmp / 'artifacts',
                                    auto_approve=True, auto_approve_max=200, retailer_codes=False)

    def tearDown(self):
        self.conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # utilidades
    def put(self, relative, data):
        path = self.tmp / relative
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def cycle(self, **kwargs):
        return publisher.run_cycle(self.conn, self.cfg, **kwargs)

    def count(self, query, *args):
        return self.conn.execute(query, args).fetchone()[0]

    def stage(self, *candidates):
        publisher.ensure_schema(self.conn, self.cfg)
        return stage_candidates(self.conn, list(candidates))

    def status(self, review_id):
        return self.conn.execute('SELECT status,review_note FROM retailer_review_queue WHERE review_id=%s', (review_id,)).fetchone()

    # pruebas
    def test_import_is_idempotent_and_skips_unchanged_files(self):
        self.put('data/catalog/batch-a.json', [product('amd-a', 'A-1')])
        first = self.cycle()
        self.assertEqual(first['catalog']['imported_files'], 1)
        self.assertEqual(self.count("SELECT count(*) FROM catalog_entities WHERE entity='product'"), 1)
        events = self.count('SELECT count(*) FROM catalog_outbox')
        second = self.cycle()
        self.assertEqual(second['catalog']['imported_files'], 0)
        self.assertEqual(self.count('SELECT count(*) FROM catalog_outbox'), events)
        # Aunque el estado se pierda, reimportar no cambia nada (idempotencia en base de datos).
        self.cfg.state_file.unlink()
        again = self.cycle()
        self.assertEqual(again['catalog']['imported_files'], 1)
        self.assertEqual(self.count('SELECT count(*) FROM catalog_outbox'), events)

    def test_manual_overrides_reimport_only_affected_batches_and_bad_files_are_skipped(self):
        self.put('data/catalog/batch-a.json', [product('amd-a', 'A-1')])
        self.put('data/catalog/batch-b.json', [product('amd-b', 'B-1')])
        self.assertEqual(self.cycle()['catalog']['imported_files'], 2)
        (self.tmp / 'data/overrides').mkdir()
        record = dict(product_id='amd-a', category_id='cpu', key='TDP', value='65 W', source='manual', date='2026-11-09',
                      source_url='https://www.amd.com/example')
        self.put('data/overrides/manual-cpu.json', [record])
        report = self.cycle()
        self.assertEqual((report['catalog']['imported_files'], report['catalog']['overrides']), (1, 1))
        specs = "SELECT data->'specs' FROM catalog_entities WHERE entity='product' AND entity_id=%s"
        self.assertEqual(self.count(specs, 'amd-a'), {'Socket': 'AM5', 'TDP': '65 W'})
        self.assertEqual(self.cycle()['catalog']['imported_files'], 0)
        # Un fichero de correcciones inválido se registra y se omite; el dato vuelve al del lote.
        self.put('data/overrides/manual-cpu.json', [dict(record, value='65')])
        report = self.cycle()
        self.assertEqual(report['catalog']['imported_files'], 1)
        self.assertTrue(any(e['step'] == 'overrides' for e in report['errors']))
        self.assertEqual(self.count(specs, 'amd-a'), {'Socket': 'AM5'})

    def test_invalid_and_half_written_batches_are_skipped_without_blocking_others(self):
        self.put('data/catalog/good.json', [product('amd-good', 'G-1')])
        self.put('data/catalog/bad-contract.json', [{**product('amd-bad', 'B-1'), 'unexpected': True}])
        self.put('data/catalog/half-written.json', '[{"id": "amd-half", "slug"')
        report = self.cycle()
        self.assertEqual(report['catalog']['imported_files'], 1)
        self.assertEqual(report['catalog']['invalid'], 2)
        self.assertEqual(self.count("SELECT count(*) FROM catalog_entities WHERE entity='product'"), 1)
        state = json.loads(self.cfg.state_file.read_text())
        self.assertEqual(state['files']['catalog']['bad-contract.json']['status'], 'invalid')
        # El fichero se completa en el host: el siguiente ciclo lo importa.
        self.put('data/catalog/half-written.json', [product('amd-half', 'H-1')])
        self.assertEqual(self.cycle()['catalog']['imported_files'], 1)
        self.assertEqual(self.count("SELECT count(*) FROM catalog_entities WHERE entity='product'"), 2)

    def test_decision_for_absent_product_is_retried_until_it_exists(self):
        review = self.stage(candidate('sku-late', 'LATE-1'))[0]
        self.put('artifacts/decisions-test-late.json', [{'review_id': review, 'action': 'approve', 'product_id': 'amd-late',
                                                         'note': 'Ficha oficial https://www.amd.com/example, MPN exacto.'}])
        first = self.cycle()
        self.assertEqual(first['decisions']['retry'], 1)
        self.assertEqual(self.status(review)[0], 'pending')
        self.assertFalse(json.loads(self.cfg.state_file.read_text())['files']['decisions']['decisions-test-late.json']['done'])
        self.assertEqual(self.cycle()['decisions']['retry'], 1)  # sigue reintentando
        self.put('data/catalog/reviewed-late.json', [product('amd-late', 'LATE-1')])
        third = self.cycle()
        self.assertEqual(third['decisions']['approved'], 1)
        self.assertEqual(self.status(review)[0], 'approved')
        self.assertEqual(self.count("SELECT count(*) FROM catalog_entities WHERE entity='offer'"), 1)
        # Fichero ya aplicado: no se vuelve a procesar.
        self.assertEqual(self.cycle()['decisions']['approved'], 0)

    def test_decisions_reject_and_skip_already_decided_rows(self):
        review = self.stage(candidate('sku-r', 'REJ-1'))[0]
        decisions = [{'review_id': review, 'action': 'reject', 'note': 'Reacondicionado'}, {'review_id': 999999, 'action': 'approve', 'note': 'x'},
                     {'review_id': 'abc'}]
        self.put('artifacts/decisions-test-reject.json', decisions)
        report = self.cycle()
        self.assertEqual(report['decisions']['rejected'], 1)
        self.assertEqual(report['decisions']['malformed'], 1)
        self.assertEqual(report['decisions']['permanent_errors'], 1)
        self.assertEqual(self.status(review)[0], 'rejected')
        self.put('artifacts/decisions-half.json', '[{"review_id": 1,')  # a medio escribir: se ignora
        self.assertEqual(self.cycle()['decisions']['invalid_files'], 1)

    def test_alias_is_imported_before_the_decision_that_depends_on_it(self):
        self.put('data/catalog/reviewed-alias.json', [product('amd-alias', 'OFFICIAL-1')])
        review = self.stage(candidate('sku-alias', 'STORE-CODE-1'))[0]
        self.put('artifacts/decisions-test-alias.json', [{'review_id': review, 'action': 'approve', 'product_id': 'amd-alias',
                                                          'note': 'Ficha oficial https://www.amd.com/example; código de tienda como alias.'}])
        first = self.cycle()  # sin alias todavía: 'Verified identifier does not match' se reintenta
        self.assertEqual(first['decisions']['retry'], 1)
        self.assertEqual(self.status(review)[0], 'pending')
        self.put('data/aliases/reviewed-aliases-test.json', [dict(product_id='amd-alias', kind='mpn', value='STORE-CODE-1',
                                                                  source_url='https://www.coolmod.com/sku-alias', note=NOTE)])
        second = self.cycle()
        self.assertEqual(second['aliases']['imported'], 1)
        self.assertEqual(second['decisions']['approved'], 1)
        self.assertEqual(self.status(review)[0], 'approved')
        self.assertEqual(self.count("SELECT count(*) FROM product_identifiers WHERE value='store-code-1' AND product_id='amd-alias'"), 1)

    def test_invalid_alias_file_is_skipped_and_reported(self):
        self.put('data/catalog/reviewed-x.json', [product('amd-x', 'X-1')])
        self.put('data/aliases/bad.json', [dict(product_id='amd-x', kind='mpn', value='X-ALT', source_url='https://evil.example/x', note='corta')])
        report = self.cycle()
        self.assertEqual(report['aliases']['invalid'], 1)
        self.assertEqual(self.count("SELECT count(*) FROM product_identifiers WHERE value='x-alt'"), 0)
        self.assertEqual(report['catalog']['imported_files'], 1)

    def test_auto_approval_requires_exact_verified_mpn(self):
        self.put('data/catalog/reviewed-auto.json', [product('amd-auto', 'AUTO-1')])
        # Se ponen en cola ANTES de que exista el producto: el publicador debe proponer el producto por MPN exacto.
        exact, marketplace, held, similar, conflict = self.stage(
            candidate('sku-exact', 'AUTO-1'),
            candidate('sku-market', 'AUTO-1', missing_fields=['direct_seller_verification']),
            candidate('sku-held', 'AUTO-1'),
            candidate('sku-similar', 'AUTO-2', name='AMD amd-auto'),
            candidate('sku-conflict', 'AUTO-1', identity_status='retailer_mpn_changed_requires_review'))
        self.conn.execute("UPDATE retailer_review_queue SET review_note='Pendiente: confirmar variante' WHERE review_id=%s", (held,))
        report = self.cycle()
        self.assertEqual(report['auto_approve']['approved'], 1)
        self.assertEqual(self.status(exact)[0], 'approved')
        self.assertTrue(self.status(exact)[1].startswith('Auto-aprobado: MPN exacto coincide con el identificador verificado de amd-auto'))
        for review in (marketplace, held, similar, conflict):
            self.assertEqual(self.status(review)[0], 'pending')
        self.assertEqual(self.count("SELECT proposed_product_id IS NULL FROM retailer_review_queue WHERE review_id=%s", similar), 1)
        self.assertEqual(self.count("SELECT count(*) FROM catalog_entities WHERE entity='offer'"), 1)

    def test_row_without_mpn_ean_is_never_approved_by_name_and_is_retried_until_enriched(self):
        self.put('data/catalog/reviewed-null.json', [product('amd-null', 'NULL-1')])
        review = self.stage(candidate('sku-null', None, name='AMD amd-null'))[0]
        self.put('artifacts/decisions-test-null.json', [{'review_id': review, 'action': 'approve', 'product_id': 'amd-null',
                                                         'note': 'Ficha oficial https://www.amd.com/example, mismo nombre y color.'}])
        for _ in range(2):
            report = self.cycle()
            self.assertEqual(report['decisions']['retry'], 1)
            self.assertEqual(report['decisions']['permanent_errors'], 0)
            self.assertEqual(self.status(review)[0], 'pending')
        self.assertIn('sin MPN/EAN', json.loads(self.cfg.state_file.read_text())['files']['decisions']['decisions-test-null.json']['last_errors'][0]['error'])
        # El enriquecimiento rellena un código que NO coincide: sigue sin aprobarse (error de identificador).
        self.conn.execute("""UPDATE retailer_review_queue SET candidate=candidate||'{"mpn":"WRONG-9"}' WHERE review_id=%s""", (review,))
        self.assertEqual(self.cycle()['decisions']['retry'], 1)
        self.assertEqual(self.status(review)[0], 'pending')
        # Código correcto: se aplica la misma decisión.
        self.conn.execute("""UPDATE retailer_review_queue SET candidate=candidate||'{"mpn":"NULL-1"}' WHERE review_id=%s""", (review,))
        report = self.cycle()
        self.assertEqual(report['decisions']['approved'], 1)
        self.assertEqual(self.status(review)[0], 'approved')

    def test_alias_store_code_still_approves_and_empty_codes_do_not(self):
        self.put('data/catalog/reviewed-al2.json', [product('amd-al2', 'OFFICIAL-2')])
        self.put('data/aliases/reviewed-aliases-al2.json', [dict(product_id='amd-al2', kind='mpn', value='SHOP-2',
                                                                 source_url='https://www.coolmod.com/sku-al2', note=NOTE)])
        review, blank = self.stage(candidate('sku-al2', 'SHOP-2'), candidate('sku-blank', '  ', ean=''))
        self.put('artifacts/decisions-test-al2.json', [
            {'review_id': review, 'action': 'approve', 'product_id': 'amd-al2', 'note': 'Ficha oficial https://www.amd.com/example; alias de tienda.'},
            {'review_id': blank, 'action': 'approve', 'product_id': 'amd-al2', 'note': 'Ficha oficial https://www.amd.com/example; por nombre.'}])
        report = self.cycle()
        self.assertEqual(report['decisions']['approved'], 1)
        self.assertEqual(report['decisions']['retry'], 1)
        self.assertEqual(report['decisions']['permanent_errors'], 0)
        self.assertEqual(self.status(review)[0], 'approved')
        self.assertEqual(self.status(blank)[0], 'pending')

    def test_auto_approval_can_be_disabled_and_is_capped(self):
        self.put('data/catalog/reviewed-cap.json', [product('amd-cap', 'CAP-1')])
        reviews = self.stage(*[candidate(f'sku-cap-{i}', 'CAP-1') for i in range(3)])
        self.cfg.auto_approve = False
        self.assertEqual(self.cycle()['auto_approve']['approved'], 0)
        self.cfg.auto_approve, self.cfg.auto_approve_max = True, 2
        self.assertEqual(self.cycle()['auto_approve']['approved'], 2)
        self.assertEqual(self.cycle()['auto_approve']['approved'], 1)
        self.assertEqual({self.status(r)[0] for r in reviews}, {'approved'})

    def test_targets_are_exported_atomically_and_only_when_they_change(self):
        self.put('data/catalog/reviewed-t.json', [product('amd-t', 'T-1')])
        review = self.stage(candidate('sku-t', 'T-1'))[0]
        self.assertEqual(self.cycle()['auto_approve']['approved'], 1)
        target = self.cfg.flow_dir / 'targets/coolmod.json'
        self.assertEqual(json.loads(target.read_text()), [dict(product_id='amd-t', retailer_sku='sku-t', mpn='T-1', url_public='https://www.coolmod.com/sku-t')])
        self.assertEqual(json.loads((self.cfg.flow_dir / 'targets/pccomponentes.json').read_text()), [])
        os.utime(target, (1, 1))
        report = self.cycle()
        self.assertFalse(report['targets']['coolmod']['changed'])
        self.assertEqual(target.stat().st_mtime, 1)  # no se reescribe si no cambia
        self.assertFalse(list(target.parent.glob('*.tmp')))
        self.assertEqual(self.status(review)[0], 'approved')

    def test_pause_file_stops_all_work(self):
        self.put('data/catalog/reviewed-p.json', [product('amd-p', 'P-1')])
        (self.cfg.flow_dir / 'PAUSE').write_text('')
        report = self.cycle()
        self.assertTrue(report['paused'])
        self.assertEqual(self.count("SELECT count(*) FROM catalog_entities"), 0)
        self.assertFalse((self.cfg.flow_dir / 'targets').exists())
        (self.cfg.flow_dir / 'PAUSE').unlink()
        self.assertEqual(self.cycle()['catalog']['imported_files'], 1)

    def test_dry_run_writes_nothing(self):
        self.put('data/catalog/reviewed-d.json', [product('amd-d', 'D-1')])
        review = self.stage(candidate('sku-d', 'D-1'))[0]
        report = self.cycle(dry_run=True)
        self.assertEqual(report['auto_approve']['approved'], 1)
        self.assertEqual(self.count("SELECT count(*) FROM catalog_entities WHERE entity='product'"), 0)
        self.assertEqual(self.status(review)[0], 'pending')
        self.assertFalse((self.cfg.flow_dir / 'targets').exists())
        self.assertFalse(self.cfg.state_file.exists())

    def test_retailer_codes_stay_off_unless_enabled(self):
        self.put('data/catalog/reviewed-c.json', [product('amd-c', 'OFFICIAL-C')])
        self.put('data/aliases/reviewed-aliases-c.json', [dict(product_id='amd-c', kind='mpn', value='STORE-C',
                                                               source_url='https://www.coolmod.com/c', note=NOTE)])
        self.cycle()
        data = self.conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id='amd-c'").fetchone()[0]
        self.assertNotIn('retailer_codes', data)
        self.cfg.retailer_codes = True
        self.cycle()
        data = self.conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id='amd-c'").fetchone()[0]
        self.assertEqual(data['retailer_codes'], [{'shop_id': 'coolmod', 'code': 'STORE-C'}])


if __name__ == '__main__':
    unittest.main(verbosity=1)
