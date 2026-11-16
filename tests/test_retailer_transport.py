"""The retailer transport routes requests through the configured proxy and changes nothing else."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from retailer_transport import proxy_url, browser_session

VARIABLES = ('NEOBYTE_PROXY', 'LDLC_PROXY', 'STOCKFINDER_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')


class TransportTests(unittest.TestCase):
    def environment(self, **values):
        clean = {k: v for k, v in os.environ.items() if k not in VARIABLES}
        clean.update(values)
        return patch.dict(os.environ, clean, clear=True)

    def test_no_proxy_by_default(self):
        with self.environment():
            self.assertEqual(proxy_url('neobyte'), '')

    def test_retailer_variable_wins_over_fallbacks(self):
        with self.environment(NEOBYTE_PROXY='socks5h://a:1', STOCKFINDER_PROXY='socks5h://b:2', ALL_PROXY='socks5h://c:3'):
            self.assertEqual(proxy_url('neobyte'), 'socks5h://a:1')

    def test_fallback_order_and_other_retailers_are_unaffected(self):
        with self.environment(NEOBYTE_PROXY='socks5h://a:1', ALL_PROXY='socks5h://c:3', HTTPS_PROXY='socks5h://d:4'):
            self.assertEqual(proxy_url('ldlc'), 'socks5h://d:4')
        with self.environment(NEOBYTE_PROXY='socks5h://a:1'):
            self.assertEqual(proxy_url('ldlc'), '')

    def test_blank_values_are_ignored(self):
        with self.environment(NEOBYTE_PROXY='  ', STOCKFINDER_PROXY=' socks5h://b:2 '):
            self.assertEqual(proxy_url('neobyte'), 'socks5h://b:2')

    def test_session_uses_proxy_only_when_configured(self):
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            self.skipTest('curl_cffi is not installed')
        with self.environment(NEOBYTE_PROXY='socks5h://a:1'):
            self.assertEqual(browser_session('neobyte').proxies, {'http': 'socks5h://a:1', 'https': 'socks5h://a:1'})
        with self.environment():
            self.assertFalse(browser_session('neobyte').proxies)
