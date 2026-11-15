"""Coolmod PDP enrichment sin navegador (scripts/coolmod_enrich.py): sin red ni base de datos."""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import coolmod_enrich as en  # noqa: E402

EAN = '4719331551759'


def tmpdir():
    for base in (None, '/dev/shm'):  # los contenedores de solo lectura no tienen /tmp
        try:
            return tempfile.TemporaryDirectory(dir=base)
        except OSError:
            continue
    raise unittest.SkipTest('No writable temporary directory')


def url(n):
    return f'https://www.coolmod.com/item-{n}/'


def product(n, **extra):
    base = {'@type': 'Product', 'sku': f'PROD-{n}', 'mpn': f'MPN-{n}', 'url': url(n).rstrip('/'), 'brand': {'name': 'Acme'},
            'offers': {'@type': 'Offer', 'priceCurrency': 'EUR', 'price': '77.95',
                       'availability': 'https://schema.org/InStock'}}
    base.update(extra)
    return {k: v for k, v in base.items() if v is not None}


def flix(n):
    return {'mpn': f'MPN-{n}', 'ean': EAN}


def ok_page(target):
    n = int(re.search(r'item-(\d+)', target['url_public']).group(1))
    return {'status': 200, 'final_url': target['url_public'], 'documents': [product(n)], 'flix': flix(n), 'title': 'x'}


def setup(directory, count):
    root = Path(directory)
    config = {'targets_file': str(root / 'flow/enrich.json'), 'output_dir': str(root / 'out'),
              'state_file': str(root / 'state/s.json'), 'page_delay': 2.0, 'batch_size': 300,
              'idle_seconds': 900, 'blocked_seconds': 21600}
    targets = [{'review_id': i + 1, 'url_public': url(i + 1), 'retailer_sku': url(i + 1).rstrip('/'),
                'name': f'Item {i + 1}', 'brand': 'Acme', 'category_hint': 'psu'} for i in range(count)]
    Path(config['targets_file']).parent.mkdir(parents=True)
    Path(config['targets_file']).write_text(json.dumps(targets))
    return config, targets


def deps(pages, sleeps=None):
    sleeps = [] if sleeps is None else sleeps
    return SimpleNamespace(session=None, read_page=lambda s, t: pages(t), sleep=sleeps.append,
                           now=lambda: '2026-01-01T00:00:00.000Z')


class IdentityTests(unittest.TestCase):
    def test_ean_needs_a_valid_gs1_check_digit(self):
        self.assertEqual(en.valid_ean(EAN), EAN)
        self.assertIsNone(en.valid_ean('4719331551750'))
        self.assertIsNone(en.valid_ean('123'))
        self.assertIsNone(en.valid_ean(None))

    def test_identity_comes_from_the_product_of_the_requested_page_never_another(self):
        documents = [{'@type': 'LocalBusiness'}, product(1), product(2)]
        identity = en.extract_identity(documents, url(2), flix(2))
        self.assertEqual((identity['retailer_sku'], identity['mpn'], identity['ean'], identity['price_cents'],
                          identity['in_stock'], identity['seller_direct']), ('PROD-2', 'MPN-2', EAN, 7795, True, True))
        with self.assertRaisesRegex(en.IdentityError, 'ambiguous'):
            en.extract_identity([product(1), product(2)], 'https://www.coolmod.com/other/')
        with self.assertRaisesRegex(en.IdentityError, 'absent'):
            en.extract_identity([], url(1))
        with self.assertRaisesRegex(en.IdentityError, 'disagree'):
            en.extract_identity([product(1)], url(1), {'mpn': 'DIFFERENT'})

    def test_graph_documents_and_list_offers_are_flattened(self):
        offers = [{'priceCurrency': 'USD', 'price': '1'},
                  {'priceCurrency': 'EUR', 'price': '12.5', 'availability': 'https://schema.org/OutOfStock'}]
        document = {'@graph': [{'@type': ['Thing', 'Product'], **product(5, offers=offers)}]}
        identity = en.extract_identity([document], url(5))
        self.assertEqual((identity['price_cents'], identity['in_stock'], identity['mpn']), (1250, False, 'MPN-5'))

    def test_invalid_ean_is_dropped_unknown_stock_stays_none_and_foreign_seller_is_not_direct(self):
        odd = product(3, offers={'priceCurrency': 'EUR', 'price': '10', 'availability': 'https://schema.org/PreOrder',
                                 'seller': {'name': 'Other Shop'}})
        identity = en.extract_identity([odd], url(3), {'ean': '4719331551750'})
        self.assertIsNone(identity['ean'])
        self.assertIsNone(identity['in_stock'])
        self.assertFalse(identity['seller_direct'])
        candidate = en.build_candidate({'url_public': url(3), 'name': 'N'}, identity, '2026-01-01T00:00:00Z')
        for field in ('ean', 'in_stock', 'direct_seller_verification'):
            self.assertIn(field, candidate['missing_fields'])
        self.assertIsNone(candidate['in_stock'])
        self.assertNotIn('ean', candidate)
        self.assertEqual(candidate['brand'], 'Acme')                 # sin marca en el objetivo: la de la ficha

    def test_mpn_falls_back_to_product_id_then_flix(self):
        self.assertEqual(en.extract_identity([product(7, mpn=None, productID='PID-7')], url(7))['mpn'], 'PID-7')
        self.assertEqual(en.extract_identity([product(8, mpn=None)], url(8), {'mpn': 'FLIX-8'})['mpn'], 'FLIX-8')
        self.assertIsNone(en.extract_identity([product(9, mpn=None)], url(9))['mpn'])


class ReadPageTests(unittest.TestCase):
    def test_read_page_extracts_json_ld_flix_and_title(self):
        html = ('<html><head><title> Ficha </title><script type="application/ld+json">{"@type":"LocalBusiness"}</script>'
                '<script type="application/ld+json">{no es json}</script></head><body>'
                f'<script src="//x/loader.js" data-flix-brand="B" data-flix-mpn="MPN-1" data-flix-ean="{EAN}"></script>'
                f'<script type="application/ld+json">{json.dumps(product(1))}</script></body></html>')
        session = SimpleNamespace(get=lambda u, timeout=None: SimpleNamespace(status_code=200, text=html, url=u))
        page = en.read_page(session, {'url_public': url(1)})
        self.assertEqual(len(page['documents']), 2)                  # el bloque invalido se omite
        self.assertEqual(page['flix'], {'ean': EAN, 'mpn': 'MPN-1'})
        self.assertEqual(page['title'], 'Ficha')
        identity = en.extract_identity(page['documents'], url(1), page['flix'])
        self.assertEqual((identity['mpn'], identity['ean']), ('MPN-1', EAN))

    def test_non_200_returns_only_status(self):
        session = SimpleNamespace(get=lambda u, timeout=None: SimpleNamespace(status_code=404, text='', url=u))
        self.assertEqual(en.read_page(session, {'url_public': url(1)}),
                         {'status': 404, 'final_url': url(1), 'documents': []})


class RunTests(unittest.TestCase):
    def test_a_run_waits_between_pages_writes_batches_and_resumes_without_refetching(self):
        with tmpdir() as directory:
            config, _ = setup(directory, 3)
            sleeps = []
            first = en.run_enrich(config, deps(ok_page, sleeps), limit=2)
            self.assertEqual((first['due'], first['ok']), (2, 2))
            self.assertEqual(len(sleeps), 1)
            self.assertTrue(all(s >= 2.0 for s in sleeps))
            second = en.run_enrich(config, deps(ok_page, sleeps))
            self.assertEqual((second['due'], second['ok']), (1, 1))
            files = sorted(Path(config['output_dir']).iterdir())
            self.assertTrue(all(re.match(r'^enriched-\d{17}-\d{3}\.json$', f.name) for f in files))
            records = [r for f in files for r in json.loads(f.read_text())]
            self.assertEqual(sorted(r['mpn'] for r in records), ['MPN-1', 'MPN-2', 'MPN-3'])
            self.assertEqual((records[0]['shop_id'], records[0]['ean']), ('coolmod', EAN))
            self.assertTrue(records[0]['retailer_sku'].startswith('PROD-'))
            self.assertEqual(en.run_enrich(config, deps(ok_page))['due'], 0)

    def test_dry_run_writes_nothing_and_a_block_stops_the_run_keeping_progress(self):
        with tmpdir() as directory:
            config, _ = setup(directory, 4)
            dry = en.run_enrich(config, deps(ok_page), dry_run=True)
            self.assertEqual(dry['ok'], 4)
            self.assertFalse(Path(config['output_dir']).exists())
            self.assertFalse(Path(config['state_file']).exists())

            def pages(target):
                return ({'status': 429, 'final_url': target['url_public'], 'documents': []}
                        if target['url_public'] == url(3) else ok_page(target))
            blocked = en.run_enrich(config, deps(pages))
            self.assertEqual((blocked['blocked'], blocked['ok']), ('HTTP 429', 2))
            state = json.loads(Path(config['state_file']).read_text())
            self.assertEqual(len(state['attempts']), 2)
            self.assertEqual(state['totals']['blocked'], 1)
            resumed = en.run_enrich(config, deps(ok_page))
            self.assertEqual((resumed['due'], resumed['ok']), (2, 2))

    def test_404_and_pages_without_mpn_are_recorded_not_staged_and_cooldowns_apply(self):
        with tmpdir() as directory:
            config, _ = setup(directory, 3)

            def pages(target):
                if target['url_public'] == url(1):
                    return {'status': 404, 'final_url': target['url_public'], 'documents': []}
                if target['url_public'] == url(2):
                    return {'status': 200, 'final_url': target['url_public'], 'documents': [product(2, mpn=None)],
                            'flix': {}, 'title': ''}
                return ok_page(target)
            result = en.run_enrich(config, deps(pages))
            self.assertEqual((result['gone'], result['no_identity'], result['ok']), (1, 1, 1))
            state = json.loads(Path(config['state_file']).read_text())
            one = [{'review_id': 1, 'url_public': url(1)}]
            self.assertEqual(en.select_targets(one, state), [])
            self.assertEqual(len(en.select_targets(one, state, en.now_ms() + 8 * 24 * 3600 * 1000)), 1)
            self.assertEqual(en.select_targets([{'review_id': 9, 'url_public': 'https://evil.example/x'}], {'attempts': {}}), [])
            self.assertEqual(en.select_targets([{'review_id': 9, 'url_public': 'http://www.coolmod.com/x'}], {'attempts': {}}), [])

    def test_redirect_away_challenge_and_mismatch(self):
        with tmpdir() as directory:
            config, _ = setup(directory, 1)
            moved = lambda t: {'status': 200, 'final_url': 'https://www.coolmod.com/otra-cosa/', 'documents': [product(1)],
                               'flix': flix(1), 'title': 'x'}
            self.assertEqual(en.run_enrich(config, deps(moved))['gone'], 1)
        with tmpdir() as directory:
            config, _ = setup(directory, 1)
            challenge = lambda t: {'status': 200, 'final_url': t['url_public'], 'documents': [], 'title': 'Just a moment...'}
            self.assertEqual(en.run_enrich(config, deps(challenge))['blocked'], 'challenge page')
        with tmpdir() as directory:
            config, _ = setup(directory, 1)
            clash = lambda t: {'status': 200, 'final_url': t['url_public'], 'documents': [product(1)],
                               'flix': {'mpn': 'OTHER', 'ean': EAN}, 'title': 'x'}
            self.assertEqual(en.run_enrich(config, deps(clash))['mismatch'], 1)

    def test_transient_error_is_retried_once_and_consecutive_failures_stop_the_run(self):
        with tmpdir() as directory:
            config, _ = setup(directory, 1)
            calls = []

            def flaky(target):
                calls.append(1)
                if len(calls) == 1:
                    raise TimeoutError('slow')
                return ok_page(target)
            sleeps = []
            self.assertEqual(en.run_enrich(config, deps(flaky, sleeps))['ok'], 1)
            self.assertEqual((len(calls), sleeps), (2, [2.0]))
        with tmpdir() as directory:
            config, _ = setup(directory, 8)

            def boom(target):
                raise RuntimeError('boom')
            result = en.run_enrich(config, deps(boom))
            self.assertEqual((result['failed'], result['blocked']), (5, 'consecutive failures'))

    def test_a_fresh_lock_skips_the_run(self):
        with tmpdir() as directory:
            config, _ = setup(directory, 1)
            first = en.acquire_lock(config['state_file'])
            self.assertIsNotNone(first)
            self.assertEqual(en.run_enrich(config, deps(ok_page)), {'skipped': 'locked'})
            first.release()
            self.assertEqual(en.run_enrich(config, deps(ok_page))['ok'], 1)

    def test_main_dry_run_and_exit_codes(self):
        with tmpdir() as directory:
            config, _ = setup(directory, 2)
            env = {'ENRICH_TARGETS_FILE': config['targets_file'], 'ENRICH_OUTPUT_DIR': config['output_dir'],
                   'ENRICH_STATE_FILE': config['state_file']}

            def session_for(status):
                def get(u, timeout=None):
                    html = '' if status != 200 else (f'<script type="application/ld+json">{json.dumps(product(1))}</script>'
                                                      f'<script data-flix-mpn="MPN-1" data-flix-ean="{EAN}"></script>')
                    return SimpleNamespace(status_code=status, text=html, url=u)
                return SimpleNamespace(get=get)
            self.assertEqual(en.main(['--dry-run', '--limit', '1'], env, lambda: session_for(200), lambda s: None), 0)
            self.assertEqual(en.main(['--dry-run'], env, lambda: session_for(403), lambda s: None), 3)
            self.assertFalse(Path(config['output_dir']).exists())

    def test_config_defaults_and_minimum_delay(self):
        config = en.config_from_env({})
        self.assertEqual((config['page_delay'], config['batch_size']), (2.0, 300))
        self.assertEqual(en.config_from_env({'ENRICH_PAGE_DELAY_MS': '500'})['page_delay'], 2.0)   # nunca menos de 2 s
        self.assertEqual(en.config_from_env({'ENRICH_PAGE_DELAY_MS': '5000'})['page_delay'], 5.0)


if __name__ == '__main__':
    unittest.main()
