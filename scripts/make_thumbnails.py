#!/usr/bin/env python3
"""Small WebP thumbnails for the product images shown in list rows, the comparison bar and the configurator picker.

    python3 scripts/make_thumbnails.py          # creates missing/outdated thumbnails and removes orphans
    python3 scripts/make_thumbnails.py --check  # exit 1 if any thumbnail is missing or outdated (no writes)

apps/web/public/images/products/<dir>/<name>.<ext>  ->  apps/web/public/images/thumbs/<dir>/<name>.webp (max 160 px).
The web app asks for the thumbnail first and falls back to the full image if it does not exist yet (ProductImage.tsx),
so running this after adding product images is an optimisation, never a requirement.
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / 'apps/web/public/images'
SRC, DST, SIZE = ROOT / 'products', ROOT / 'thumbs', 160
EXTS = {'.png', '.jpg', '.jpeg', '.webp'}


def target(src: Path) -> Path:
    return (DST / src.relative_to(SRC)).with_suffix('.webp')


def make(src: Path, dst: Path) -> None:
    with Image.open(src) as im:
        im = im.convert('RGBA' if im.mode in ('RGBA', 'LA', 'P') else 'RGB')
        im.thumbnail((SIZE, SIZE), Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        im.save(dst, 'WEBP', quality=78, method=4)


def main(check: bool) -> int:
    sources = [p for p in SRC.rglob('*') if p.suffix.lower() in EXTS]
    stale = [p for p in sources if not target(p).exists() or target(p).stat().st_mtime < p.stat().st_mtime]
    wanted = {target(p) for p in sources}
    orphans = [p for p in DST.rglob('*.webp') if p not in wanted] if DST.exists() else []
    if check:
        print(f'{len(stale)} missing/outdated, {len(orphans)} orphan thumbnails')
        return 1 if stale or orphans else 0
    with ProcessPoolExecutor() as pool:
        list(pool.map(make, stale, map(target, stale), chunksize=16))
    for p in orphans:
        p.unlink()
    total = sum(t.stat().st_size for t in wanted if t.exists())
    print(f'{len(stale)} written, {len(orphans)} removed, {len(wanted)} thumbnails, {total / 1e6:.1f} MB')
    return 0


if __name__ == '__main__':
    sys.exit(main('--check' in sys.argv))
