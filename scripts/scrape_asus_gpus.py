"""Kept for backwards compatibility: the generic ASUS scraper is ``scripts/scrape_asus.py``.

This entry point scrapes the GPU category only and writes the same file as before
(``artifacts/manufacturer-asus-gpus.json``).

Usage:
  python3 scripts/scrape_asus_gpus.py [--only-current] [--limit N] [--refresh]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_asus import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main(['--category', 'gpu'] + sys.argv[1:]))
