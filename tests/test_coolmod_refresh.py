"""Coolmod AJAX refresh (scripts/coolmod_refresh.py): no network, no database."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import coolmod_refresh as cm  # noqa: E402

BASE = 'https://www.coolmod.com'


def tmpdir():
    for base in (None, '/dev/shm'):  # read-only containers have no /tmp
        try:
            return tempfile.TemporaryDirectory(dir=base)
        except OSError:
            continue
    raise unittest.SkipTest('No writable temporary directory')


def sku(n):
    return f'PROD-{n:06d}'


def target(n):
    return {'product_id': f'p{n}', 'retailer_sku': sku(n), 'mpn': f'MPN-{n}', 'url_public': f'{BASE}/item-{n}/'}


def entry(n, **extra):
    base = {'ProducCode': sku(n), 'Price': '1.094,95', 'OldPrice': '0,00', 'TextDelivered': 'RecÃ­belo maÃ±ana',
            'PercentageDiscount': 0, 'BecomeBackinStockDate': '', 'Slug': f'/item-{n}/', 'Stock': '7',
            'LifeCycleCode': 'P', 'criteo': []}
    base.update(extra)
    return base


def page(n, price='1094.95', availability='InStock', item='InStock', mpn=None, sku_value=None):
    ld = {'@context': 'https://schema.org', '@type': 'Product', 'sku': sku_value or sku(n), 'mpn': mpn or f'MPN-{n}',
          'offers': {'@type': 'Offer', 'priceCurrency': 'EUR', 'price': price, 'availability': f'https://schema.org/{availability}'}}
    attr = f' data-itemavailability="{item}"' if item else ''
    return 200, f'{BASE}/item-{n}/', f'<html><script type="application/ld+json">{json.dumps(ld)}</script><div{attr}></div></html>'


CONFIG = {'chunk': 25, 'delay': 2.0, 'verify_days': 7, 'page_limit': 20}


class Fake:
    def __init__(self, answer=None, pages=None):
        self.calls, self.pages, self.sleeps = [], [], []
        self.answer = answer or (lambda codes: {c: entry(int(c[5:])) for c in codes})
        self.page_answers = pages or {}

    def fetch_prices(self, _session, codes):
        self.calls.append(list(codes))
        result = self.answer(codes)
        if isinstance(result, Exception):
            raise result
        return result

    def fetch_page(self, _session, url):
        self.pages.append(url)
        n = int(url.rstrip('/').split('-')[-1])
        result = self.page_answers.get(n, page(n))
        if isinstance(result, Exception):
            raise result
        return result

    def run(self, targets, state, config=CONFIG, clock=lambda: 1_000_000_000.0):
        return cm.refresh(None, targets, state, config, fetch_prices=self.fetch_prices, fetch_page=self.fetch_page,
                          sleep=self.sleeps.append, clock=clock)


def verified_state(targets, at=1_000_000_000.0):
    return {t['retailer_sku']: {'verified_at': at} for t in targets}  # page opened long ago


class ClassifyTests(unittest.TestCase):
    def test_clean_entry_gives_price_and_explicit_stock(self):
        observed, anomaly = cm.classify_ajax(entry(1), target(1))
        self.assertIsNone(anomaly)
        self.assertEqual(observed, {'price_cents': 109495, 'in_stock': True, 'stock': 7})
        observed, _ = cm.classify_ajax(entry(1, Stock='0', TextDelivered='Agotado', Price='84,95'), target(1))
        self.assertEqual(observed, {'price_cents': 8495, 'in_stock': False, 'stock': 0})
        self.assertEqual(cm.classify_ajax(entry(1, Slug='/item-1'), target(1))[1], None)  # trailing slash tolerated

    def test_every_oddity_is_an_anomaly(self):
        cases = {
            'absent': None,
            'no_slug': entry(1, Slug=None, Price=None, Stock=None),  # unknown code: all null
            'slug_mismatch': entry(1, Slug='/otro-producto/'),
            'price_format': entry(1, Price='94.95'),
            'stock_format': entry(1, Stock='5+'),
            'stock_text_conflict': entry(1, Stock='0'),
        }
        for expected, value in cases.items():
            self.assertEqual(cm.classify_ajax(value, target(1)), (None, expected), expected)
        self.assertEqual(cm.classify_ajax(entry(1, Price='0,00'), target(1))[1], 'price_format')
        self.assertEqual(cm.classify_ajax(entry(1, Stock='3', TextDelivered='Agotado'), target(1))[1], 'stock_text_conflict')
        self.assertEqual(cm.classify_ajax(entry(1, Stock=True), target(1))[1], 'stock_format')


class RefreshTests(unittest.TestCase):
    def test_chunks_of_25_polite_pauses_and_ajax_records_renew_freshness(self):
        targets = [target(n) for n in range(60)]
        fake = Fake()
        records, result = fake.run(targets, verified_state(targets))
        self.assertEqual([len(c) for c in fake.calls], [25, 25, 10])
        self.assertEqual(fake.pages, [])  # all verified recently: no product page
        self.assertEqual(len(records), 60)
        self.assertTrue(all(ms >= 2.0 for ms in fake.sleeps))
        record = records[0]
        self.assertEqual(record['source'], 'coolmod-ajax')
        self.assertEqual(record['price_cents'], 109495)
        self.assertIs(record['in_stock'], True)
        self.assertTrue(record['last_checked_at'].endswith('Z'))
        for key in ('product_id', 'shop_id', 'retailer_sku', 'mpn', 'url_public', 'match_evidence'):
            self.assertTrue(record[key])
        self.assertEqual(result['calls'], 3)

    def test_anomalies_go_to_the_product_page_and_only_consistent_stock_is_published(self):
        targets = [target(n) for n in range(5)]
        answer = lambda codes: {sku(0): entry(0, Slug='/otro/'), sku(1): entry(1, Stock='x'), sku(2): entry(2),
                                sku(3): entry(3, Price='??')}  # 4 absent
        fake = Fake(answer, pages={
            0: page(0),                                           # JSON-LD and page attribute agree
            1: page(1, availability='InStock', item='OutOfStock'),  # Coolmod JSON-LD lies: no record
            3: (404, f'{BASE}/item-3/', 'gone'),
            4: (200, f'{BASE}/', '<html>home</html>'),            # redirected away without the product
        })
        records, result = fake.run(targets, verified_state(targets))
        by = {r['retailer_sku']: r for r in records}
        self.assertEqual(sorted(fake.pages), sorted(t['url_public'] for t in targets if t['retailer_sku'] != sku(2)))
        self.assertEqual(by[sku(0)]['source'], 'coolmod-page')
        self.assertIs(by[sku(0)]['in_stock'], True)
        self.assertNotIn(sku(1), by)
        self.assertEqual(by[sku(2)]['source'], 'coolmod-ajax')
        self.assertTrue(by[sku(3)]['unavailable'] and by[sku(4)]['unavailable'])
        self.assertNotIn('price_cents', by[sku(3)])
        self.assertEqual(result['page'].get('stock_unconfirmed'), 1)

    def test_anomaly_is_not_reopened_within_24h(self):
        targets = [target(0)]
        state = verified_state(targets)
        fake = Fake(lambda codes: {sku(0): entry(0, Slug='/x/')})
        fake.run(targets, state, clock=lambda: 1_000_000_000.0)
        self.assertEqual(len(fake.pages), 1)
        fake.run(targets, state, clock=lambda: 1_000_000_000.0 + 3600)
        self.assertEqual(len(fake.pages), 1)
        fake.run(targets, state, clock=lambda: 1_000_000_000.0 + 25 * 3600)
        self.assertEqual(len(fake.pages), 2)

    def test_identity_reverification_is_periodic_bounded_and_blocks_ajax_on_failure(self):
        targets = [target(n) for n in range(30)]
        now = 1_000_000_000.0
        state = verified_state(targets, at=now)
        state[sku(5)] = {'verified_at': now - 8 * 86400}           # due (7 days)
        state[sku(6)] = {}                                          # never verified
        fake = Fake(pages={5: page(5, mpn='OTRO-MPN')})
        records, result = fake.run(targets, state, clock=lambda: now)
        self.assertEqual(sorted(fake.pages), [f'{BASE}/item-5/', f'{BASE}/item-6/'])
        self.assertNotIn(sku(5), {r['retailer_sku'] for r in records})  # identity broken: nothing published
        self.assertEqual(state[sku(6)]['verified_at'], now)
        self.assertIn('identity_failed_at', state[sku(5)])
        fake2 = Fake()
        records, result = fake2.run(targets, state, clock=lambda: now + 3600)
        self.assertNotIn(sku(5), {r['retailer_sku'] for r in records})
        self.assertEqual(result['identity_blocked'], 1)
        self.assertEqual(fake2.pages, [])  # identity retry waits 24 h
        # page limit
        many = {t['retailer_sku']: {} for t in targets}
        fake3 = Fake()
        _, result = fake3.run(targets, many, config={**CONFIG, 'page_limit': 4})
        self.assertEqual(len(fake3.pages), 4)
        self.assertEqual(result['page_pending'], 26)

    def test_page_price_conflict_drops_the_ajax_record(self):
        targets = [target(0)]
        fake = Fake(pages={0: page(0, price='999.95')})
        records, result = fake.run(targets, {sku(0): {}})
        self.assertEqual(records, [])
        self.assertEqual(result['price_conflict'], 1)

    def test_block_stops_the_pass_and_skips_pages(self):
        targets = [target(n) for n in range(80)]
        calls = []
        def answer(codes):
            calls.append(codes)
            return cm.Blocked('HTTP 429') if len(calls) == 2 else {c: entry(int(c[5:])) for c in codes}
        fake = Fake(answer)
        records, result = fake.run(targets, {})
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(result['blocked'], 'HTTP 429')
        self.assertEqual(fake.pages, [])
        self.assertEqual(len(records), 25)  # progress kept
        fake = Fake(pages={0: cm.Blocked('challenge page')})
        _, result = fake.run([target(0), target(1)], {})
        self.assertEqual(len(fake.pages), 1)
        self.assertEqual(result['blocked'], 'challenge page')

    def test_three_consecutive_technical_failures_abort(self):
        targets = [target(n) for n in range(200)]
        fake = Fake(lambda codes: RuntimeError('timeout'))
        records, result = fake.run(targets, {})
        self.assertEqual(len(fake.calls), 3)
        self.assertTrue(result['aborted'])
        self.assertEqual(records, [])
        self.assertEqual(fake.pages, [])


class CycleTests(unittest.TestCase):
    def workspace(self, root, count=3):
        data = Path(root) / 'data'
        (data / 'retailers').mkdir(parents=True)
        (data / 'catalog').mkdir()
        (data / 'retailers/pilot.json').write_text('[]')
        (data / 'retailers/approved-coolmod-targets.json').write_text(json.dumps([target(n) for n in range(count)] + [{'bad': 1}]))
        (data / 'catalog/a.json').write_text(json.dumps([{'id': f'p{n}', 'mpn': f'MPN-{n}'} for n in range(count - 1)]))
        (data / 'product-identifiers.json').write_text('[]')
        return {**CONFIG, 'targets': str(Path(root) / 'missing-flow.json'), 'data_dir': str(data),
                'output': str(Path(root) / 'out/offers/coolmod.json'), 'state': str(Path(root) / 'out/state/s.json'),
                'target_limit': 0}

    def test_cycle_skips_unverified_targets_writes_shards_and_state(self):
        with tmpdir() as root:
            config = self.workspace(root)
            fake = Fake()
            summary = cm.run_cycle(config, lambda: None, fetch_prices=fake.fetch_prices, fetch_page=fake.fetch_page,
                                   sleep=fake.sleeps.append)
            self.assertEqual(summary['unverified'], 1)
            self.assertEqual(fake.calls, [[sku(0), sku(1)]])
            self.assertEqual(summary['offers'], 2)
            out = json.loads(Path(config['output']).read_text())
            self.assertEqual([r['retailer_sku'] for r in out], [sku(0), sku(1)])
            state = json.loads(Path(config['state']).read_text())
            self.assertIn('verified_at', state['targets'][sku(0)])

    def test_alias_from_any_evidence_page_verifies_only_its_product(self):
        target = {'product_id': 'amd-ryzen-5-7600x', 'mpn': '100-100000593WOF',
                  'url_public': 'https://www.coolmod.com/amd-ryzen-5-7600x-5-3ghz-socket-am5-boxed/'}
        products = {'amd-ryzen-5-7600x': {'id': 'amd-ryzen-5-7600x', 'mpn': '100-000000593'}}
        alias = {'product_id': 'amd-ryzen-5-7600x', 'kind': 'mpn', 'value': '100-100000593WOF',
                 'source_url': 'https://www.amd.com/en/products/processors/desktops/ryzen/7000-series/amd-ryzen-5-7600x.html',
                 'note': 'Product ID Boxed en la ficha oficial de AMD.'}
        self.assertTrue(cm.verified_target(target, products, [alias]))
        self.assertFalse(cm.verified_target(target, products, [dict(alias, product_id='amd-ryzen-5-7600')]))
        self.assertFalse(cm.verified_target(target, products, [dict(alias, note='')]))
        self.assertFalse(cm.verified_target(target, products, [dict(alias, kind='ean')]))
        self.assertFalse(cm.verified_target(dict(target, product_id='missing'), products, [alias]))

    def test_shards_respect_the_sync_agent_limit(self):
        with tmpdir() as root:
            output = Path(root) / 'coolmod.json'
            names = cm.write_shards([{'retailer_sku': sku(n)} for n in range(5)], output, 2)
            self.assertEqual(names, ['coolmod.json', 'coolmod.2.json', 'coolmod.3.json'])
            (Path(root) / 'pccomponentes.2.json').write_text('[]')
            cm.write_shards([{'retailer_sku': '1'}], output, 2)
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()), ['coolmod.json', 'pccomponentes.2.json'])

    def test_config_is_off_by_default_and_clamped(self):
        config = cm.config_from_env({})
        self.assertFalse(config['enabled'])
        self.assertEqual((config['chunk'], config['delay'], config['verify_days'], config['interval']), (25, 2.0, 7, 3600))
        config = cm.config_from_env({'COOLMOD_AJAX_REFRESH': '1', 'COOLMOD_AJAX_CHUNK': '500', 'COOLMOD_DELAY_SECONDS': '0.1'})
        self.assertTrue(config['enabled'])
        self.assertEqual((config['chunk'], config['delay']), (100, 2.0))

    def test_error_fields_mask_secrets(self):
        fields = cm.error_fields(PermissionError("[Errno 13] Permission denied: '/output/offers/coolmod.json.1.tmp'"))
        self.assertEqual(fields['type'], 'PermissionError')
        self.assertIn('/output/offers', fields['message'])
        self.assertEqual(cm.error_fields(RuntimeError('https://u:p@h/x?api_key=1&a=2'))['message'], 'https://***@h/x?api_key=***&a=2')


if __name__ == '__main__':
    unittest.main()
