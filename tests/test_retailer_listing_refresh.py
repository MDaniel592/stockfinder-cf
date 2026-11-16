import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'nas')]

import ldlc_local
import neobyte_local
import retailer_listing_refresh as refresh


def ld_json(items):
    return '<script type="application/ld+json">' + json.dumps(
        {'@type': 'ItemList', 'itemListElement': [{'item': {'url': u}} for u in items]}) + '</script>'


def ldlc_page(cards, marketplace='0'):
    html = ld_json([f'https://www.ldlc.com/es-es/ficha/{sku}.html' for sku, *_ in cards])
    scripts = ''
    for sku, offer, price, stock in cards:
        html += (f'<li class="pdt-item"><a href="/es-es/ficha/{sku}.html">x</a>'
                 f'<div class="wrap-stock"><div class="stock-web"><div></div></div></div>'
                 f'<div class="basket"><div class=\'price\'></div><button data-offer-id="{offer}" '
                 f'data-is-marketplace="{marketplace}" class="add-to-cart"></button></div></li>')
        scripts += (f"var pdtElements = document.querySelectorAll('[id*=\"pdt\"][data-offer-id=\"{offer}\"]'); "
                    "pdtElements.forEach((pdtElement) => { ")
        if price:
            scripts += f"if (x) {{ pdtElement.querySelector('.price').outerHTML ='<div class=\"price\"><div class=\"price\">{price}</div></div>'; }} "
        scripts += (f"if (y) {{ pdtElement.querySelector('.stocks .stock-web div').outerHTML = "
                    f"'<div class=\"modal-stock-web pointer stock stock-{stock}\" data-stock-web=\"{stock}\"><span>x</span></div>'; }} "
                    "if (z) { pdtElement.querySelector('.stocks .stock-shop div').outerHTML = '<div data-stock-shop=\"9\"></div>'; } }); ")
    return html + '<script>' + scripts + '</script>'


def neobyte_page(items):
    links = [f'https://www.neobyte.es/{slug}-{pid}.html' for pid, slug, *_ in items]
    html = '<script type="application/ld+json">' + json.dumps(
        {'@type': 'ItemList', 'itemListElement': [{'url': u} for u in links]}) + '</script>'
    for (pid, slug, price, button), link in zip(items, links):
        action = ('<button class="btn add-to-cart" data-button-action="add-to-cart">Añadir al carrito</button>'
                  if button == 'add' else '<a class="btn btn-product-list" href="%s">Más detalles +</a>' % link)
        html += (f'<article class="product-miniature js-product-miniature" data-id-product="{pid}" >'
                 f'<a href="{link}" class="thumbnail">i</a><div class="product-price-and-shipping">'
                 f'<span class="product-price" content="{price}" aria-label="Precio"> x € </span></div>'
                 f'<div class="product-add-cart">{action}</div></article>')
    return html


class ListingRefreshTests(unittest.TestCase):
    def test_ldlc_price_and_stock_from_inline_scripts(self):
        html = ldlc_page([('PB1', 'AR1', '110€<sup>95</sup>', 1),
                          ('PB2', 'AR2', '2&nbsp;400€<sup>95</sup>', 9),
                          ('PB3', 'AR3', None, 9),
                          ('PB4', 'AR4', '10€<sup>5</sup>', 2)])
        cards = refresh.ldlc_cards(html, ldlc_local)
        by_sku = {c['retailer_sku']: c for c in cards.values()}
        assert (by_sku['PB1']['price_cents'], by_sku['PB1']['in_stock']) == (11095, True)
        assert (by_sku['PB2']['price_cents'], by_sku['PB2']['in_stock']) == (240095, False)
        assert (by_sku['PB3']['price_cents'], by_sku['PB3']['in_stock']) == (None, False)
        assert (by_sku['PB4']['price_cents'], by_sku['PB4']['in_stock']) == (None, True)  # one-digit decimals are ambiguous (no price); stock code 2 means in stock

    def test_ldlc_marketplace_cards_are_not_trusted(self):
        cards = refresh.ldlc_cards(ldlc_page([('PB1', 'AR1', '110€<sup>95</sup>', 1)], marketplace='1'), ldlc_local)
        assert next(iter(cards.values()))['price_cents'] is None

    def test_neobyte_price_and_button_state(self):
        html = neobyte_page([('1', 'a-b', '588.99', 'add'), ('2', 'c-d', '1,000.5'.replace(',', ''), 'more'),
                             ('3', 'e-f', 'oops', 'add')])
        cards = refresh.neobyte_cards(html, neobyte_local)
        by_id = {c['retailer_sku']: c for c in cards.values()}
        assert (by_id['1']['price_cents'], by_id['1']['in_stock']) == (58899, True)
        assert (by_id['2']['price_cents'], by_id['2']['in_stock']) == (100050, False)
        assert by_id['3']['price_cents'] is None

    def test_merge_listing_falls_back_on_untrusted_values(self):
        row = dict(price_cents=10000, in_stock=True, last_checked_at='old', commercial_observation_source='S')
        assert refresh.merge_listing(row, dict(price_cents=None, in_stock=True), 'now') is None
        assert refresh.merge_listing(row, dict(price_cents=9000, in_stock=None), 'now') is None
        assert refresh.merge_listing(row, dict(price_cents=30000, in_stock=True), 'now') is None  # >2x jump
        merged = refresh.merge_listing(row, dict(price_cents=9000, in_stock=False), 'now')
        assert (merged['price_cents'], merged['in_stock'], merged['last_checked_at']) == (9000, False, 'now')
        assert merged['commercial_observation_source'] == 'S' + refresh.SOURCE_NOTE
        again = refresh.merge_listing(merged, dict(price_cents=9000, in_stock=False), 'later')
        assert again['commercial_observation_source'].count(refresh.SOURCE_NOTE) == 1


if __name__ == "__main__":
    unittest.main()
