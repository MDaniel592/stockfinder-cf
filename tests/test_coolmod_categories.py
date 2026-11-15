"""Inventario de categorias de Coolmod sin navegador (scripts/coolmod_categories.py): sin red ni base de datos."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import coolmod_categories as cc  # noqa: E402

SLUG = 'tarjetas-graficas'


def tmpdir():
    for base in (None, '/dev/shm'):  # los contenedores de solo lectura no tienen /tmp
        try:
            return tempfile.TemporaryDirectory(dir=base)
        except OSError:
            continue
    raise unittest.SkipTest('No writable temporary directory')


def html(names, total=None, href=None):
    cards = ''.join(
        f'<a href="{(href or "/producto-{n}/").format(n=n)}?utm=x#top" data-itemname="Nombre {n} " data-itembrand="Marca"'
        f' data-itemid="{n}" data-itemcategory2="Tarjetas" data-itemprice="9.99"></a>' for n in names)
    return f'<html><body><span>{total} productos</span>{cards}</body></html>' if total else f'<body>{cards}</body>'


class FakeSession:
    def __init__(self, pages, status=None):
        self.pages = pages          # {url: (status, texto)}
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        status, text = self.pages[url]
        return SimpleNamespace(status_code=status, text=text, url=url)


def category_pages(total, size=25, slug=SLUG):
    """Paginas 1..ceil(total/size) con `size` productos (la ultima, el resto) y productos distintos por pagina."""
    pages, count, number = {}, 0, 1
    while count < total:
        n = min(size, total - count)
        pages[cc.page_url(slug, number)] = (200, html(range(count, count + n), total=total))
        count += n
        number += 1
    return pages


def new_state():
    return {'version': 1, 'categories': {}, 'candidates': []}


class CoolmodCategoriesTests(unittest.TestCase):
    def test_parse_page_normalizes_cards_and_total(self):
        page = html([1, 2], total='1.281') + '<a href="https://otra.com/x" data-itemname="Ajeno"></a>' \
            + '<a href="/sin-nombre/" data-itemname="  "></a>'
        cards, total = cc.parse_page(page, f'{cc.BASE}/{SLUG}/')
        self.assertEqual(total, 1281)
        self.assertEqual([c['url'] for c in cards], [f'{cc.BASE}/producto-1/', f'{cc.BASE}/producto-2/'])
        self.assertEqual(cards[0]['listing_id'], '1')
        self.assertIsNone(cc.parse_page('<body></body>', cc.BASE)[1])

    def test_page_urls_and_numbers(self):
        self.assertEqual(cc.page_url(SLUG, 1), f'{cc.BASE}/{SLUG}/')
        self.assertEqual(cc.page_url(SLUG, 3), f'{cc.BASE}/{SLUG}/?pagina=3')
        self.assertEqual(cc.page_number(f'{cc.BASE}/{SLUG}/'), 1)
        self.assertEqual(cc.page_number(f'{cc.BASE}/{SLUG}/?pagina=7'), 7)
        self.assertTrue(cc.valid_saved_url(f'{cc.BASE}/{SLUG}/?pagina=2', SLUG))
        self.assertFalse(cc.valid_saved_url(f'{cc.BASE}/{SLUG}/?ordenacion=precio', SLUG))
        self.assertFalse(cc.valid_saved_url(f'https://otra.com/{SLUG}/', SLUG))

    def test_candidate_matches_the_playwright_inventory_shape(self):
        card = {'url': f'{cc.BASE}/p/', 'name': ' RTX ', 'brand': '', 'listing_id': '9',
                'listing_category': 'Gpu', 'listing_price': '10'}
        row = cc.candidate(card, 'gpu', '2026-11-14T00:00:00.000Z')
        self.assertEqual(sorted(row), sorted([
            'shop_id', 'retailer_sku', 'url_public', 'name', 'brand', 'mpn', 'ean', 'category_hint', 'price_cents',
            'in_stock', 'last_checked_at', 'missing_fields', 'raw_properties']))
        self.assertEqual((row['retailer_sku'], row['brand'], row['name']), (f'{cc.BASE}/p', None, 'RTX'))
        self.assertEqual([p['key'] for p in row['raw_properties']],
                         ['listing_id', 'listing_category', 'listing_price_unverified'])

    def test_crawl_completes_by_total_and_deduplicates(self):
        session, state, saves, naps = FakeSession(category_pages(60)), new_state(), [], []
        pages = cc.crawl(session, state, lambda s: saves.append(json.dumps(s)), ['gpu'], 50, sleep=naps.append)
        self.assertEqual(pages, 3)                                   # 25 + 25 + 10
        self.assertEqual(len(state['candidates']), 60)
        self.assertTrue(state['categories']['gpu']['complete'])
        self.assertEqual(state['categories']['gpu']['total_products'], 60)
        self.assertEqual(len(saves), 3)                              # progreso guardado tras cada pagina
        self.assertEqual(len(naps), 2)                               # una pausa entre peticiones
        self.assertTrue(all(2.0 <= n <= 3.0 for n in naps))
        self.assertEqual(session.calls[1], f'{cc.BASE}/{SLUG}/?pagina=2')

    def test_crawl_resumes_after_the_limit(self):
        session, state = FakeSession(category_pages(60)), new_state()
        cc.crawl(session, state, lambda s: None, ['gpu'], 2, sleep=lambda s: None)
        self.assertEqual(state['categories']['gpu']['last_url'], f'{cc.BASE}/{SLUG}/?pagina=2')
        self.assertFalse(state['categories']['gpu'].get('complete'))
        session.calls.clear()
        cc.crawl(session, state, lambda s: None, ['gpu'], 2, sleep=lambda s: None)
        self.assertEqual(session.calls, [f'{cc.BASE}/{SLUG}/?pagina=3'])   # no repite paginas ya vistas
        self.assertTrue(state['categories']['gpu']['complete'])
        self.assertEqual(len(state['candidates']), 60)

    def test_crawl_skips_finished_categories_and_unrequested_ones(self):
        state = new_state()
        state['categories']['gpu'] = {'last_url': None, 'pages': 4, 'complete': True}
        session = FakeSession({})
        self.assertEqual(cc.crawl(session, state, lambda s: None, ['gpu'], 5, sleep=lambda s: None), 0)
        self.assertEqual(session.calls, [])

    def test_blocked_status_and_challenge_stop_the_run(self):
        for status, text in ((403, ''), (429, ''), (503, ''), (200, '<title>Just a moment...</title>')):
            session = FakeSession({cc.page_url(SLUG, 1): (status, text)})
            with self.assertRaises(cc.Blocked):
                cc.crawl(session, new_state(), lambda s: None, ['gpu'], 5, sleep=lambda s: None)
            self.assertEqual(len(session.calls), 1)

    def test_empty_first_page_is_an_error_but_empty_later_page_ends_the_category(self):
        with self.assertRaises(RuntimeError):
            cc.crawl(FakeSession({cc.page_url(SLUG, 1): (200, '<body>nada</body>')}), new_state(),
                     lambda s: None, ['gpu'], 5, sleep=lambda s: None)
        pages = category_pages(25)
        pages[cc.page_url(SLUG, 2)] = (200, '<body>No hay productos</body>')
        state = new_state()
        state['categories']['gpu'] = {'last_url': cc.page_url(SLUG, 1), 'pages': 1, 'page_size': 25}
        state['candidates'] = []
        cc.crawl(FakeSession(pages), state, lambda s: None, ['gpu'], 5, sleep=lambda s: None)
        self.assertEqual(state['categories']['gpu']['end_reason'], 'empty_page')

    def test_repeated_page_ends_the_category(self):
        same = html(range(25), total=500)
        pages = {cc.page_url(SLUG, 1): (200, same), cc.page_url(SLUG, 2): (200, same)}
        state = new_state()
        cc.crawl(FakeSession(pages), state, lambda s: None, ['gpu'], 5, sleep=lambda s: None)
        self.assertEqual(state['categories']['gpu']['end_reason'], 'repeat_page')
        self.assertEqual(len(state['candidates']), 25)

    def test_rejects_a_saved_url_with_foreign_parameters(self):
        state = new_state()
        state['categories']['gpu'] = {'last_url': f'{cc.BASE}/{SLUG}/?ordenacion=x', 'pages': 1}
        with self.assertRaises(RuntimeError):
            cc.crawl(FakeSession({}), state, lambda s: None, ['gpu'], 5, sleep=lambda s: None)

    def test_main_sets_blocked_until_and_then_makes_no_requests(self):
        with tmpdir() as directory:
            env = {'COOLMOD_INVENTORY': str(Path(directory) / 'inv.json'), 'COOLMOD_CATEGORIES': 'gpu',
                   'COOLMOD_PAGES': '5', 'COOLMOD_BLOCKED_SECONDS': '7200'}
            blocked = FakeSession({cc.page_url(SLUG, 1): (403, '')})
            self.assertEqual(cc.main(env, make_session=lambda: blocked), 3)
            state = json.loads(Path(env['COOLMOD_INVENTORY']).read_text())
            self.assertGreater(state['blocked_until'], __import__('time').time() + 7000)
            quiet = FakeSession({})
            self.assertEqual(cc.main(env, make_session=lambda: quiet), 0)
            self.assertEqual(quiet.calls, [])                       # bloqueado: ninguna peticion

    def test_main_writes_a_complete_inventory_and_validates_inputs(self):
        with tmpdir() as directory:
            env = {'COOLMOD_INVENTORY': str(Path(directory) / 'inv.json'), 'COOLMOD_CATEGORIES': 'gpu', 'COOLMOD_PAGES': '5'}
            session = FakeSession(category_pages(30))
            self.assertEqual(cc.main(env, make_session=lambda: session, sleep=lambda s: None), 0)
            state = json.loads(Path(env['COOLMOD_INVENTORY']).read_text())
            self.assertEqual((state['version'], len(state['candidates'])), (1, 30))
            self.assertNotIn('blocked_until', state)
            for bad in ({'COOLMOD_PAGES': '0'}, {'COOLMOD_PAGES': '201'}, {'COOLMOD_CATEGORIES': 'nope'}):
                with self.assertRaises(SystemExit):
                    cc.main({**env, **bad}, make_session=lambda: session)


if __name__ == '__main__':
    unittest.main()
