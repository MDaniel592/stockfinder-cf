"""Shared HTTP transport for the retailer crawlers (LDLC, Neobyte).

Every retailer page is read with a browser TLS fingerprint (``curl_cffi``
``impersonate="chrome"``), the same one the rest of the project uses.

Some shops sit behind a WAF that challenges every request that leaves from a
datacenter IP, no matter how correct the headers are: Neobyte answers HTTP 403
with ``cf-mitigated: challenge`` to the whole domain (even ``robots.txt``) when
the request comes from the Azure VM, while the same request from a residential
connection answers 200.  For those shops the crawl has to leave through a
residential connection, so the session can be pointed at a SOCKS5 node with the
``<RETAILER>_PROXY`` variable, for example the node of the NAS reached from
Azure over the reverse SSH tunnel::

    NEOBYTE_PROXY=socks5h://172.18.0.1:1081

``socks5h`` and not ``socks5``: the name must be resolved by the proxy, because
the Azure DNS does not resolve the shop's CDN addresses.  ``STOCKFINDER_PROXY``,
``HTTPS_PROXY`` and ``ALL_PROXY`` are used as fallbacks when the retailer
variable is not set.  Without any of them the behaviour does not change: the
session goes out directly, as before.

The proxy only changes the route of the request, never its content: the same
hosts, the same delay between requests and the same parsing apply.
"""
from __future__ import annotations

import os

FALLBACK_PROXY_VARIABLES = ('STOCKFINDER_PROXY', 'HTTPS_PROXY', 'ALL_PROXY')


def proxy_url(retailer=None):
    """Proxy URL for a retailer, or an empty string when there is none."""
    names = []
    if retailer:
        names.append(f'{retailer.upper()}_PROXY')
    names.extend(FALLBACK_PROXY_VARIABLES)
    for name in names:
        value = os.environ.get(name, '').strip()
        if value:
            return value
    return ''


def browser_session(retailer=None):
    """``curl_cffi`` session with a browser fingerprint and the retailer's proxy."""
    from curl_cffi import requests
    session = requests.Session(impersonate='chrome')
    proxy = proxy_url(retailer)
    if proxy:
        session.proxies = {'http': proxy, 'https': proxy}
    return session
