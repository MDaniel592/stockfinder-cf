"""LDLC observations must fail closed before entering the catalogue."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from ldlc_local import candidate, Structured, product_url, inventory_links, exact_product, run, retry_delay, refresh_session_started_at, snapshot_is_current

URL = 'https://www.ldlc.com/es-es/ficha/PB00686694.html'


class LdlcTests(unittest.TestCase):
    def row(self, **changes):
        offer = dict(price='123.45', priceCurrency='EUR', availability='https://schema.org/InStock',
                     itemCondition='https://schema.org/NewCondition', seller={'name': 'LDLC'}, url=URL)
        offer.update(changes)
        p = dict(name='Example', mpn='EXACT-123', gtin13='4711581491205', brand={'name': 'ASRock'},
                 offers=offer, **{'@type': 'Product'})
        return '<script type="application/ld+json">' + json.dumps(p) + '</script>'

    def test_unknown_database_is_refused_before_any_write(self):
        # LDLC may write to the isolated copy and, since 2026-11-16, to production; any other database is refused.
        with self.assertRaises(SystemExit):
            run(argparse.Namespace(database_url='postgresql://stockfinder@postgres/stockfinder_other'))

    def test_blocked_crawl_keeps_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / 'run-state.json').write_text(json.dumps({'retry_after_epoch': time.time() + 7200}))
            with self.assertRaises(SystemExit):
                run(argparse.Namespace(database_url='postgresql://stockfinder@postgres/stockfinder_ldlc_local', output=output))

    def test_retry_after_accepts_seconds_and_http_dates(self):
        self.assertEqual(retry_delay('7200', now=0), 7200)
        self.assertEqual(retry_delay('Thu, 01 Jan 1970 02:00:00 GMT', now=0), 7200)
        self.assertEqual(retry_delay(None, now=0), 900)
        self.assertEqual(retry_delay('invalid', now=0), 900)

    def test_refresh_resumes_only_same_session_snapshots_and_restarts_after_completion(self):
        before = datetime(2026, 11, 14, 1, 0, tzinfo=timezone.utc)
        cutoff = '2026-11-14T01:00:00+00:00'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            path.write_text(json.dumps({'last_checked_at': '2026-11-14T01:00:00Z'}))
            self.assertTrue(snapshot_is_current(path, cutoff))
            path.write_text(json.dumps({'collected_at': '2026-11-14T00:59:59Z'}))
            self.assertFalse(snapshot_is_current(path, cutoff, 'collected_at'))
            path.write_text(json.dumps({'last_checked_at': '2026-11-13T23:59:59Z'}))
            self.assertFalse(snapshot_is_current(path, cutoff))
        active = {'status': 'blocked', 'refresh_started_at': cutoff}
        self.assertEqual(refresh_session_started_at(active, before), cutoff)
        complete = {'status': 'complete', 'refresh_started_at': cutoff}
        self.assertEqual(refresh_session_started_at(complete, datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc)),
                         '2026-11-14T02:00:00+00:00')

    def test_exact_cents_and_identifiers(self):
        row = candidate(self.row(), URL, 'gpu', '2026-11-12T12:00:00Z')
        self.assertEqual((row['price_cents'], row['retailer_sku'], row['mpn']), (12345, 'PB00686694', 'EXACT-123'))
        self.assertTrue(row['in_stock'])

    def test_rejects_ambiguous_commercial_data(self):
        for changes in [dict(priceCurrency='USD'), dict(price='NaN'), dict(price='1.001'),
                        dict(price='-1'), dict(availability='unknown'), dict(seller={'name': 'Marketplace'}),
                        dict(url=URL + '?other=1'), dict(itemCondition='https://schema.org/UsedCondition')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                candidate(self.row(**changes), URL, 'gpu', '2026-11-12T12:00:00Z')

    def test_preorder_is_not_in_stock(self):
        row = candidate(self.row(availability='https://schema.org/PreOrder'), URL, 'gpu', '2026-11-12T12:00:00Z')
        self.assertFalse(row['in_stock'])

    def test_identifier_conflict_and_category_mismatch_stay_pending(self):
        row = dict(brand='ASRock', mpn='MPN', ean='4711581491205', category_hint='gpu')
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [('product-a',), ('product-b',)]
        self.assertIsNone(exact_product(conn, row))
        conn.execute.return_value.fetchone.side_effect = [('product-a',), None, ({'category_id': 'cpu'},)]
        self.assertIsNone(exact_product(conn, row))
        conn.execute.return_value.fetchone.side_effect = [('product-a',), ('product-a',), ({'category_id': 'gpu'},)]
        self.assertEqual(exact_product(conn, row), 'product-a')

    def test_later_pages_exclude_recommendations(self):
        html = '<a href="/es-es/ficha/PB99999999.html">Recommended</a>'
        html += '<li id="pdt-x" class="pdt-item" data-position="49"><a href="/es-es/ficha/PB00000001.html">Item</a></li>'
        self.assertEqual(inventory_links(html), ['https://www.ldlc.com/es-es/ficha/PB00000001.html'])
        with self.assertRaises(ValueError):
            inventory_links('<html>Challenge or empty list</html>')

    def test_pagination_and_origin(self):
        self.assertEqual(Structured('<link rel="next" href="/es-es/test/pagina2/">').next,
                         'https://www.ldlc.com/es-es/test/pagina2/')
        for url in [URL.replace('https:', 'http:'), URL.replace('www.ldlc.com', 'evil.example'),
                    URL.replace('/es-es/', '/fr-fr/')]:
            self.assertFalse(product_url(url))


if __name__ == '__main__':
    unittest.main()
