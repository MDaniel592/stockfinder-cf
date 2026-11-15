"""PcComponentes refresh + offers status: no network, no database."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import offers_status  # noqa: E402
import pccomponentes_batch as batch  # noqa: E402
import pccomponentes_refresh as refresh  # noqa: E402

BASE = 'https://www.pccomponentes.com'


def tmpdir():
    for base in (None, '/dev/shm'):  # read-only containers have no /tmp
        try:
            return tempfile.TemporaryDirectory(dir=base)
        except OSError:
            continue
    raise unittest.SkipTest('No writable temporary directory')


def target(n):
    return {'retailer_sku': str(1000 + n), 'mpn': f'MPN{n}', 'product_id': f'p{n}', 'url_public': f'{BASE}/item-{n}'}


def article(n, **extra):
    base = {'id': 1000 + n, 'mpn': f'MPN{n}', 'sellerName': 'PcComponentes', 'isPCC': True, 'price': 12.5,
            'delivery': {'availabilityCode': 1}, 'slug': f'item-{n}'}
    base.update(extra)
    return base


class RefreshTests(unittest.TestCase):
    def run_refresh(self, targets, articles_by_call, misses=None, **options):
        calls = []
        pauses = []

        def fetch(session, skus):
            calls.append(list(skus))
            outcome = articles_by_call(len(calls) - 1, skus)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        records, result = refresh.refresh(None, targets, {} if misses is None else misses, fetch, pauses.append, **options)
        return records, result, calls, pauses

    def test_batches_of_40_with_polite_pauses_and_direct_seller_rules(self):
        targets = [target(n) for n in range(85)]
        def answer(index, skus):
            out = []
            for sku in skus:
                n = int(sku) - 1000
                if n == 1: out.append(article(n, sellerName='Marketplace', isPCC=False))
                elif n == 2: out.append(article(n, mpn='OTHER'))
                elif n == 3: out.append(article(n, delivery={'availabilityCode': 4}))
                elif n == 4: out.append(article(n, delivery={'availabilityCode': 2}))
                elif n == 5: out.append(article(n, price=None, delivery={'availabilityCode': 9999}))
                else: out.append(article(n))
            return out
        records, result, calls, pauses = self.run_refresh(targets, answer)
        self.assertEqual([len(c) for c in calls], [40, 40, 5])
        self.assertEqual(len(pauses), 2)
        self.assertTrue(all(2.0 <= p <= 3.0 for p in pauses))  # >=2 s entre peticiones
        by_sku = {r['retailer_sku']: r for r in records}
        self.assertNotIn('1001', by_sku)  # marketplace seller skipped, data kept
        self.assertNotIn('1002', by_sku)  # MPN mismatch skipped
        self.assertFalse(by_sku['1003']['in_stock'])
        self.assertFalse(by_sku['1004']['in_stock'])  # only codes 1 and 3 are stock
        self.assertTrue(by_sku['1000']['in_stock'])
        self.assertEqual(by_sku['1005']['unavailable'], True)
        self.assertNotIn('price_cents', by_sku['1005'])
        self.assertEqual(result['skipped'], {'marketplace_seller': 1, 'mpn_mismatch': 1})
        self.assertEqual(len(records), 83)

    def test_absent_listing_needs_two_consecutive_checks_and_failures_keep_data(self):
        targets = [target(0), target(1)]
        misses = {}
        answer = lambda i, skus: [article(0)]  # 1001 missing from a healthy response
        records, _, _, _ = self.run_refresh(targets, answer, misses)
        self.assertEqual([r['retailer_sku'] for r in records], ['1000'])
        self.assertEqual(misses, {'1001': 1})
        records, _, _, _ = self.run_refresh(targets, answer, misses)
        gone = [r for r in records if r.get('unavailable')]
        self.assertEqual([r['retailer_sku'] for r in gone], ['1001'])
        self.assertFalse(gone[0]['in_stock'])
        # A failing or empty response never counts as "absent" and emits nothing.
        for outcome in (RuntimeError('Cloudflare challenge'), []):
            records, result, _, _ = self.run_refresh(targets, lambda i, s, o=outcome: o, {'1001': 1})
            self.assertEqual(records, [])
            self.assertEqual(result['failed'], 2)

    def test_three_consecutive_chunk_failures_abort_the_pass(self):
        targets = [target(n) for n in range(40 * 5)]
        # Sin enfriamientos (PCC_RATE_LIMIT_COOLDOWNS='') se conserva el comportamiento anterior.
        records, result, calls, _ = self.run_refresh(targets, lambda i, s: RuntimeError('blocked'), cooldowns=())
        self.assertEqual(len(calls), 3)
        self.assertTrue(result['aborted'])
        self.assertEqual(result['failed'], 200)
        self.assertEqual(len(result['failed_skus']), 200)
        self.assertEqual(records, [])
        # Con enfriamientos: 3 fallos -> 60 s, 3 mas -> 180 s, 3 mas -> aborta; nunca se reintenta tras abortar.
        targets = [target(n) for n in range(40 * 10)]
        records, result, calls, pauses = self.run_refresh(targets, lambda i, s: RuntimeError('blocked'))
        self.assertEqual((len(calls), result['cooldowns'], result['retried']), (9, 2, 0))
        self.assertEqual([p for p in pauses if p > 3], [60, 180])
        self.assertTrue(result['aborted'])
        self.assertEqual(result['failed'], 400)

    def test_rate_limit_cools_down_honours_retry_after_and_retries_chunk_in_same_pass(self):
        targets = [target(n) for n in range(40 * 4)]
        def answer(index, skus):  # la 2.a llamada recibe 429; el resto responde
            if index == 1:
                return batch.RateLimited(retry_after=90)
            return [article(int(s) - 1000) for s in skus]
        records, result, calls, pauses = self.run_refresh(targets, answer)
        self.assertEqual(len(calls), 5)  # 4 lotes + 1 reintento del lote limitado
        self.assertEqual(calls[-1], calls[1])
        self.assertIn(90, pauses)  # Retry-After (90) > primer enfriamiento (60)
        self.assertEqual((result['failed'], result['cooldowns'], result['retried'], result['aborted']), (0, 1, 1, False))
        self.assertEqual(len(records), 160)

    def test_still_rate_limited_on_retry_leaves_skus_for_next_pass(self):
        targets = [target(n) for n in range(40 * 3)]
        def answer(index, skus):
            return batch.RateLimited() if index in (0, 3) else [article(int(s) - 1000) for s in skus]
        records, result, calls, _ = self.run_refresh(targets, answer)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result['failed_skus'], [t['retailer_sku'] for t in targets[:40]])
        self.assertEqual(len(records), 80)
        reordered = refresh.prioritize(targets, result['failed_skus'][:2] + ['999999'])
        self.assertEqual([t['retailer_sku'] for t in reordered[:3]], ['1000', '1001', '1002'])
        self.assertEqual(len(reordered), len(targets))

    def test_marketplace_only_with_discontinued_code_needs_flag_and_two_checks(self):
        targets = [target(0), target(1)]
        answer = lambda i, skus: [article(0, sellerName='MORELE', isPCC=False, delivery={'availabilityCode': 9999}),
                                  article(1, sellerName='MORELE', isPCC=False, delivery={'availabilityCode': 1})]
        misses = {}
        records, result, _, _ = self.run_refresh(targets, answer, misses)  # flag apagado: solo se omite
        self.assertEqual((records, result['skipped'], misses), ([], {'marketplace_seller': 2}, {}))
        records, result, _, _ = self.run_refresh(targets, answer, misses, marketplace_gone=True)
        self.assertEqual(records, [])
        self.assertEqual(result['skipped'], {'marketplace_gone_pending_confirmation': 1, 'marketplace_seller': 1})
        records, result, _, _ = self.run_refresh(targets, answer, misses, marketplace_gone=True)
        self.assertEqual([(r['retailer_sku'], r['unavailable'], r['in_stock']) for r in records], [('1000', True, False)])
        self.assertIn('marketplace seller MORELE', records[0]['match_evidence'])
        # Vuelve a venderlo PcComponentes: oferta normal y contador a cero.
        records, _, _, _ = self.run_refresh(targets[:1], lambda i, s: [article(0)], misses, marketplace_gone=True)
        self.assertTrue(records[0]['in_stock'])
        self.assertNotIn('1000', misses)

    def test_target_loading_prefers_flow_then_database_then_file(self):
        with tmpdir() as folder:
            folder = Path(folder)
            flow, approved = folder / 'flow.json', folder / 'approved.json'
            approved.write_text(json.dumps([target(9)]))
            query = lambda url: [target(5), target(5), {'retailer_sku': 'x'}]
            self.assertEqual(refresh.load_targets(flow, 'db', approved, query)[1:], ('database', 1 + 1))
            flow.write_text(json.dumps([target(1), {'bad': True}]))
            targets, source, dropped = refresh.load_targets(flow, 'db', approved, query)
            self.assertEqual((source, dropped, [t['retailer_sku'] for t in targets]), ('flow', 1, ['1001']))
            flow.write_text('{truncated')
            def broken(url): raise OSError('down')
            self.assertEqual(refresh.load_targets(flow, 'db', approved, broken)[1], 'approved_file')
            self.assertEqual(refresh.load_targets(flow, None, approved)[0][0]['retailer_sku'], '1009')
            # Ofertas piloto: solo SKU nuevos de PcComponentes y nunca como unica fuente.
            pilot = folder / 'pilot.json'
            pilot.write_text(json.dumps([{**target(9), 'shop_id': 'pccomponentes', 'mpn': 'OTHER'},
                                         {**target(7), 'shop_id': 'pccomponentes', 'price_cents': 1},
                                         {**target(8), 'shop_id': 'coolmod'}]))
            extra = refresh.pilot_targets(pilot)
            self.assertEqual(sorted(t['retailer_sku'] for t in refresh.load_targets(flow, None, approved, extra=extra)[0]),
                             ['1007', '1009'])
            self.assertEqual(refresh.load_targets(flow, None, approved, extra=extra)[0][0]['mpn'], 'MPN9')
            self.assertNotIn('price_cents', extra[1])
            self.assertEqual(refresh.load_targets(None, None, None, extra=extra)[0], [])
            self.assertEqual(refresh.pilot_targets(''), [])

    def test_cycle_writes_atomic_shards_and_state(self):
        with tmpdir() as folder:
            folder = Path(folder)
            flow = folder / 'flow.json'
            flow.write_text(json.dumps([target(n) for n in range(5)]))
            args = SimpleNamespace(targets=str(flow), fallback_targets=None, output=folder / 'offers/pccomponentes.json',
                                   state=folder / 'state/state.json')
            fetch = lambda session, skus: [article(int(s) - 1000) for s in skus]
            summary = refresh.run_cycle(args, lambda: None, fetch, lambda s: None)
            self.assertEqual((summary['offers'], summary['failed'], summary['calls']), (5, 0, 1))
            self.assertEqual(len(json.loads(args.output.read_text())), 5)
            self.assertEqual(json.loads(args.state.read_text())['records'], 5)
            self.assertEqual(refresh.write_shards([{'retailer_sku': str(n)} for n in range(5)], args.output, 2),
                             ['pccomponentes.json', 'pccomponentes.2.json', 'pccomponentes.3.json'])
            refresh.write_shards([{'retailer_sku': '1'}], args.output, 2)
            self.assertEqual(sorted(p.name for p in args.output.parent.iterdir()), ['pccomponentes.json'])


    def test_cycle_keeps_failed_skus_first_and_drops_unpublished_unavailable(self):
        with tmpdir() as folder:
            folder = Path(folder)
            flow = folder / 'flow.json'
            flow.write_text(json.dumps([target(n) for n in range(45)]))
            args = SimpleNamespace(targets=str(flow), fallback_targets=None, output=folder / 'offers/pccomponentes.json',
                                   state=folder / 'state/state.json', cooldowns=(), delay=(0, 0))
            calls = []
            def fetch(session, skus):
                calls.append(list(skus))
                if len(calls) >= 2:  # el 2.o lote y su reintento siguen limitados
                    raise batch.RateLimited()
                return [article(int(s) - 1000) for s in skus]
            summary = refresh.run_cycle(args, lambda: None, fetch, lambda s: None)
            self.assertEqual((summary['failed'], summary['retried_chunks']), (5, 1))
            self.assertEqual(json.loads(args.state.read_text())['retry_first'], ['1040', '1041', '1042', '1043', '1044'])
            calls.clear()
            refresh.run_cycle(args, lambda: None, lambda session, skus: calls.append(list(skus)) or [], lambda s: None)
            self.assertEqual(calls[0][:5], ['1040', '1041', '1042', '1043', '1044'])
            # Dos sondeos sin ficha -> unavailable, pero solo para ofertas ya publicadas.
            published = {refresh.offer_identity('1001')}
            fetch = lambda session, skus: [article(int(s) - 1000) for s in skus if s not in ('1001', '1002')]
            old = os.environ.get('DATABASE_URL')
            os.environ['DATABASE_URL'] = 'postgresql://test'
            try:
                for _ in range(2):
                    summary = refresh.run_cycle(args, lambda: None, fetch, lambda s: None, lambda url: published)
            finally:
                os.environ.pop('DATABASE_URL') if old is None else os.environ.__setitem__('DATABASE_URL', old)
            self.assertEqual(summary['unavailable'], 1)
            self.assertEqual(summary['skipped'], {'unavailable_unpublished': 1})
            self.assertEqual(refresh.offer_identity('1001')[:14], 'pccomponentes-')


class FetchBatchTests(unittest.TestCase):
    class Session:
        def __init__(self, responses):
            self.responses, self.calls = list(responses), 0
        def get(self, url, **kwargs):
            self.calls += 1
            return self.responses.pop(0)

    @staticmethod
    def response(status, retry_after=None, articles=()):
        headers = {'retry-after': str(retry_after)} if retry_after is not None else {}
        return SimpleNamespace(status_code=status, headers=headers, text='', json=lambda: {'articles': list(articles)})

    def test_429_raises_rate_limited_or_waits_retry_after(self):
        session = self.Session([self.response(429, 120)])
        with self.assertRaises(batch.RateLimited) as caught:
            batch.fetch_batch(session, ['1'], retry_429=False, sleep=lambda s: None)
        self.assertEqual((caught.exception.retry_after, session.calls), (120, 1))
        waits = []
        session = self.Session([self.response(429, 7), self.response(200, articles=[{'id': 1}])])
        self.assertEqual(batch.fetch_batch(session, ['1'], sleep=waits.append), [{'id': 1}])
        self.assertEqual(waits, [7])
        session = self.Session([self.response(429, 'Wed, 21 Oct 2026 07:28:00 GMT')] * 3)
        with self.assertRaises(batch.RateLimited):
            batch.fetch_batch(session, ['1'], sleep=lambda s: None)
        self.assertEqual(batch.retry_after_seconds(self.response(429, 99999)), 600)

    def test_more_than_40_ids_is_refused_before_any_request(self):
        session = self.Session([])
        with self.assertRaises(ValueError):
            batch.fetch_batch(session, [str(n) for n in range(41)])
        self.assertEqual(session.calls, 0)


class StatusTests(unittest.TestCase):
    def test_age_percentiles_and_stale_counts(self):
        now = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)
        ago = lambda hours: now - timedelta(hours=hours)
        rows = [('coolmod', ago(1), True), ('coolmod', ago(25), False), ('coolmod', ago(50), True),
                ('pccomponentes', ago(2).isoformat().replace('+00:00', 'Z'), True)]
        report = offers_status.summarize(rows, now)
        self.assertEqual(report['coolmod'], {'offers': 3, 'in_stock': 2, 'age_p50_h': 25.0, 'age_p95_h': 50.0,
                                             'age_max_h': 50.0, 'stale_over_24h': 2, 'stale_over_48h': 1})
        self.assertEqual(report['pccomponentes']['age_max_h'], 2.0)
        self.assertIn('coolmod', offers_status.render(report, (0, None)))


if __name__ == '__main__':
    unittest.main()
