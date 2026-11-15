"""Helpers shared by the LDLC and Neobyte local crawlers (identical in both before extraction)."""
import time
from email.utils import parsedate_to_datetime


def retry_delay(value, now=None):
    now = time.time() if now is None else now
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(value).timestamp() - now
        except (TypeError, ValueError, OverflowError):
            seconds = 0
    return max(900, seconds)


class MissingListing(RuntimeError):
    def __init__(self, status, url):
        self.status = status
        self.url = url
        super().__init__(f'Listing unavailable: HTTP {status}')


def exact_product(conn, row):
    from import_catalog import canonical_brand, normalize
    matches = set()
    for kind, scope, value in [('mpn', canonical_brand(row.get('brand')), row.get('mpn')),
                               ('ean', 'global', row.get('ean'))]:
        if not value:
            continue
        found = conn.execute('SELECT product_id FROM product_identifiers WHERE kind=%s AND scope=%s AND value=%s',
                             (kind, scope, normalize(value))).fetchone()
        if found:
            matches.add(found[0])
    if len(matches) != 1:
        return None
    pid = next(iter(matches))
    product = conn.execute("SELECT data FROM catalog_entities WHERE entity='product' AND entity_id=%s AND deleted_at IS NULL", (pid,)).fetchone()
    if not product or product[0]['category_id'] != row['category_hint']:
        return None
    return pid
