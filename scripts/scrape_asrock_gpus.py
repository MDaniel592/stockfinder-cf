"""Kept for backwards compatibility: the generic ASRock scraper is ``scripts/scrape_asrock.py``.

This entry point scrapes the GPU category only and writes the same file as before
(``artifacts/manufacturer-asrock-gpus.json``).

Usage:
  python3 scripts/scrape_asrock_gpus.py [--only-current] [--limit N] [--refresh]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scrape_asrock import main  # noqa: E402

if __name__ == '__main__':
    raise SystemExit(main(['--category', 'gpu'] + sys.argv[1:]))
