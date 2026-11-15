"""Normalisers shared by the manufacturer reference tools (index, diff, scrape)."""
import re
from datetime import datetime, timezone


def norm_brand(value) -> str:
    return re.sub(r'[^a-z0-9]', '', str(value or '').casefold())


def norm_mpn(value) -> str:
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def norm_model(value) -> str:
    return ' '.join(re.findall(r'[a-z0-9]+', str(value or '').casefold()))


def now_utc() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
