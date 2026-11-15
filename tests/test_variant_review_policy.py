"""Review-policy boundaries, with no connection to any database."""
import sys
import unittest
from pathlib import Path
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'scripts'), str(Path(__file__).resolve().parents[1] / 'nas')]
import ldlc_pipeline
import neobyte_pipeline

class Result:
    def __init__(self, row): self.row = row
    def fetchone(self): return self.row

class Connection:
    def __init__(self, category, conflict=False, flagged=False):
        self.candidate = dict(brand='ASUS', category_hint=category, mpn='STORE-BLACK', ean='1234567890123', url_public='https://www.ldlc.com/es-es/ficha/PB1.html')
        if flagged: self.candidate['missing_fields'] = ['manufacturer_variant_conflict']
        self.product = dict(brand='ASUS', category_id=category, mpn='MODEL', mpn_source='manufacturer', manufacturer_url='https://www.asus.com/model')
        self.conflict = conflict
    def execute(self, sql, args):
        if 'SELECT candidate,status' in sql: return Result((self.candidate, 'pending'))
        if 'SELECT data FROM catalog_entities' in sql: return Result((self.product,))
        if 'SELECT product_id FROM product_identifiers' in sql: return Result(('different-product',) if self.conflict else None)
        raise AssertionError(sql)

class VariantPolicyTests(unittest.TestCase):
    def decision(self):
        return dict(review_id=1, action='approve', product_id='example', note='Official full black variant confirmed at https://www.asus.com/model; code STORE-BLACK is retailer provenance.', variant_evidence=dict(official_model='Model Black', retailer_model='Model Black', source_url='https://www.asus.com/model', ean_checked=True, attributes={'Color':'Black','Panel':'Glass','Format':'ATX'}))
    def aliases(self):
        return [dict(product_id='example', kind='mpn', value='STORE-BLACK', source_url='https://www.ldlc.com/es-es/ficha/PB1.html', note='Official full black variant confirmed at https://www.asus.com/model; retailer code retained.')]
    def test_full_case_and_cooler_variant_is_accepted(self):
        for module in (ldlc_pipeline, neobyte_pipeline):
            for category in ('case','cooler'):
                with self.subTest(module=module.__name__, category=category):
                    self.assertIsNotNone(module.validate_review(Connection(category), self.decision(), self.aliases())[0])
    def test_variant_evidence_is_mandatory(self):
        for module in (ldlc_pipeline, neobyte_pipeline):
            d=self.decision();d.pop('variant_evidence')
            with self.assertRaisesRegex(ValueError, 'full variant evidence'):
                module.validate_review(Connection('case'), d, self.aliases())
    def test_cpu_ram_storage_psu_still_require_exact_mpn(self):
        for module in (ldlc_pipeline, neobyte_pipeline):
            for category in ('cpu','ram','storage','psu'):
                with self.assertRaisesRegex(ValueError,'exact manufacturer MPN'):
                    module.validate_review(Connection(category), self.decision(), self.aliases())
    def test_conflicting_ean_cannot_be_overridden(self):
        for module in (ldlc_pipeline, neobyte_pipeline):
            with self.assertRaisesRegex(ValueError,'EAN contradicts'):
                module.validate_review(Connection('case',conflict=True),self.decision(),self.aliases())
    def test_existing_variant_conflict_stays_blocked(self):
        for module in (ldlc_pipeline, neobyte_pipeline):
            with self.assertRaisesRegex(ValueError,'unresolved manufacturer variant conflict'):
                module.validate_review(Connection('cooler',flagged=True),self.decision(),self.aliases())
    def test_future_specification_timestamp_is_rejected(self):
        class EmptyCatalog:
            def execute(self, *args): return self
            def fetchall(self): return []
        for module in (ldlc_pipeline, neobyte_pipeline):
            product=dict(id='example', category_id='case', mpn_source='retailer', manufacturer_evidence='Official source explicitly verifies the complete model variant.', specs_verified_at='2099-01-01T00:00:00Z')
            with self.assertRaisesRegex(ValueError,'cannot be in the future'):
                module.import_reviewed_products(EmptyCatalog(),[product],[{'product_id':'example'}])
    def test_unofficial_variant_source_is_rejected(self):
        for module in (ldlc_pipeline, neobyte_pipeline):
            d=self.decision();d['variant_evidence']['source_url']='https://asus.example.com/model'
            with self.assertRaisesRegex(ValueError,'canonical manufacturer domain'):
                module.validate_review(Connection('case'),d,self.aliases())

if __name__ == '__main__': unittest.main()
