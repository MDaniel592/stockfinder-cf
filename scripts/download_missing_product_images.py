#!/usr/bin/env python3
import argparse
import collections
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1] if len(Path(__file__).resolve().parents) > 1 else Path("/opt/stockfinder")
sys.path.insert(0, str(ROOT / "scripts"))

try:
    import fetch_product_images as fpi
    import retailer_transport
except Exception as exc:
    print(f"Missing image library: {exc}", file=sys.stderr)
    raise

PUBLIC = ROOT / "apps/web/public"
WORK = ROOT / "work/all-missing-images"
STATE = WORK / "state.json"
MANIFEST = WORK / "manifest.json"


def load_proxy_environment():
    env_file = ROOT / ".env"
    names = ("NEOBYTE_PROXY", "LDLC_PROXY", "STOCKFINDER_PROXY", "HTTPS_PROXY", "ALL_PROXY")
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in names and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def missing_targets():
    sql = r"""
select coalesce(json_agg(t),'[]') from (
 select p.entity_id id,p.data->>'category_id' cat,p.data->>'brand' brand,
        p.data->>'mpn' mpn,p.data->>'name' name,p.data->>'manufacturer_url' murl,
        coalesce((
          select json_agg(json_build_object(
            'shop',o.data->>'shop_id','url',o.data->>'url_public','sku',o.data->>'retailer_sku'))
          from catalog_entities o
          where o.entity='offer' and o.deleted_at is null
            and o.data->>'product_id'=p.entity_id
        ),'[]') offers
 from catalog_entities p
 where p.entity='product' and p.deleted_at is null
   and coalesce(p.data->>'image_path','')=''
 order by p.entity_id
) t
"""
    cmd = ["docker", "exec", "stockfinder-v2-postgres-1", "psql", "-U", "stockfinder", "-d", "stockfinder", "-tAc", sql]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout.strip()
    targets = json.loads(out)
    for target in targets:
        target["offers"] = target.get("offers") or []
    return targets


def source_allowed(image_url, page_url, shop):
    host = (urlparse(image_url).hostname or "").lower()
    page_host = (urlparse(page_url).hostname or "").lower()
    registrable = fpi.registrable
    if shop == "ldlc":
        return host == "media.ldlc.com" or registrable(host) == "ldlc.com"
    if shop == "neobyte":
        return registrable(host) == "neobyte.es" or "neobyte" in host
    if shop == "pccomponentes":
        return registrable(host) == "pccomponentes.com"
    if shop == "coolmod":
        return registrable(host) == "coolmod.com"
    if shop == "manufacturer":
        return host and registrable(host) == registrable(page_host)
    return False


def make_record(target, image_path, image_url, page_url, source, scope):
    return {
        "product_id": target["id"],
        "image_path": image_path,
        "image_source_url": image_url,
        "image_page_url": page_url,
        "image_scope": scope,
        "image_origin": "retailer" if source != "manufacturer" else "manufacturer",
        "attribution": target.get("brand") or source,
        "source": source,
    }


def save_webp(target, webp):
    rel = f"/images/products/{target['cat']}/{target['id']}.webp"
    dest = PUBLIC / rel.lstrip("/")
    if dest.exists():
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(webp)
    thumb = PUBLIC / "images/thumbs" / target["cat"] / f"{target['id']}.webp"
    thumb.parent.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        with Image.open(dest) as im:
            im.thumbnail((160, 160))
            im.save(thumb, "WEBP", quality=82)
    except Exception:
        pass
    return rel


def download_binary(fetcher, url):
    status, ctype, data, final_url = fetcher.get(url, binary=True)
    if status != 200 or not ctype.startswith("image/") or len(data) > fpi.MAX_DOWNLOAD:
        return None
    try:
        webp, width, height = fpi.to_webp(data)
    except ValueError:
        return None
    return webp, width, height


def retailer_get(source, page_url, binary=False):
    if source not in ("ldlc", "neobyte"):
        return None
    session = retailer_transport.browser_session(source)
    response = session.get(page_url, timeout=30)
    ctype = response.headers.get("content-type", "")
    if binary:
        return response.status_code, ctype, response.content, str(response.url)
    return response.status_code, ctype, response.text, str(response.url)


def retailer_binary(source, image_url):
    if source not in ("ldlc", "neobyte"):
        return None
    session = retailer_transport.browser_session(source)
    response = session.get(image_url, timeout=30)
    ctype = response.headers.get("content-type", "")
    if response.status_code != 200 or not ctype.startswith("image/") or len(response.content) > fpi.MAX_DOWNLOAD:
        return None
    try:
        webp, width, height = fpi.to_webp(response.content)
    except ValueError:
        return None
    return webp, width, height


def process_target(target, fetcher, robots, brand_domains):
    if (PUBLIC / f"images/products/{target['cat']}/{target['id']}.webp").exists():
        return {"status": "skip", "reason": "file_exists"}

    candidate_pages = []
    if target.get("murl"):
        candidate_pages.append(("manufacturer", target["murl"]))
    for offer in target.get("offers") or []:
        shop = str(offer.get("shop") or "").lower()
        url = offer.get("url")
        if url and shop in ("ldlc", "neobyte", "pccomponentes", "coolmod"):
            candidate_pages.append((shop, url))

    for source, page_url in candidate_pages:
        if not page_url.startswith("https://"):
            continue
        if source not in ("ldlc", "neobyte") and not robots.allowed(page_url):
            continue
        try:
            if source in ("ldlc", "neobyte"):
                status, ctype, text, final_url = retailer_get(source, page_url)
            else:
                status, ctype, text, final_url = fetcher.get(page_url)
        except Exception as exc:
            continue
        if status != 200 or "html" not in ctype:
            continue
        parser = fpi.parse_page(text)
        candidates = fpi.extract_candidates(parser, final_url)
        if not candidates:
            continue
        for kind, image_url in candidates:
            if fpi.bad_image_url(image_url):
                continue
            if source == "manufacturer":
                allowed, _ = fpi.host_official(image_url, final_url, target.get("brand") or "", brand_domains)
                if not allowed:
                    continue
            elif not source_allowed(image_url, final_url, source):
                continue
            if source in ("ldlc", "neobyte"):
                result = retailer_binary(source, image_url)
            else:
                result = download_binary(fetcher, image_url)
            if not result:
                continue
            webp, width, height = result
            image_path = save_webp(target, webp)
            if not image_path:
                return {"status": "skip", "reason": "file_exists"}
            scope = "model"
            if source == "manufacturer":
                scope = "model" if fpi.page_is_exact_model(
                    {"id": target["id"], "brand": target.get("brand") or "", "name": target["name"], "mpn": target["mpn"], "category_id": target["cat"], "manufacturer_url": page_url},
                    parser, final_url
                ) else "family"
            return {
                "status": "ok",
                "source": source,
                "scope": scope,
                "page_url": final_url,
                "image_url": image_url,
                "image_path": image_path,
                "width": width,
                "height": height,
                "sha256": hashlib.sha256(webp).hexdigest(),
            }
    return {"status": "skip", "reason": "no_approved_image"}


def main():
    load_proxy_environment()
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int)
    parser.add_argument("--category")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--delay", type=float, default=1.5)
    args = parser.parse_args()

    WORK.mkdir(parents=True, exist_ok=True)
    targets = missing_targets()
    if args.category:
        targets = [t for t in targets if t["cat"] == args.category]
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    todo = [t for t in targets if t["id"] not in state]
    if args.limit:
        todo = todo[:args.limit]

    brand_domains = fpi.load_brand_domains(ROOT)
    lock = threading.Lock()
    done = 0
    counts = collections.Counter()

    def job(target):
        fetcher = fpi.Fetcher(args.delay)
        robots = fpi.Robots(fetcher)
        try:
            entry = process_target(target, fetcher, robots, brand_domains)
        except fpi.Blocked as exc:
            entry = {"status": "blocked", "reason": str(exc)}
        except Exception as exc:
            entry = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"[:200]}
        with lock:
            state[target["id"]] = entry
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
            counts[entry["status"]] += 1
            nonlocal_done()

    def nonlocal_done():
        pass

    def run_job(target):
        nonlocal done
        fetcher = fpi.Fetcher(args.delay)
        robots = fpi.Robots(fetcher)
        try:
            entry = process_target(target, fetcher, robots, brand_domains)
        except Exception as exc:
            entry = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"[:200]}
        with lock:
            state[target["id"]] = entry
            STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1))
            counts[entry["status"]] += 1
            done += 1
            if done % 25 == 0:
                print(done, "de", len(todo), dict(counts), flush=True)

    print(len(todo), "productos pendientes", len(targets), "totales", flush=True)
    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(run_job, todo))

    records = []
    for target in targets:
        entry = state.get(target["id"], {})
        if entry.get("status") != "ok":
            continue
        records.append(make_record(target, entry["image_path"], entry["image_url"], entry["page_url"], entry["source"], entry["scope"]))
    MANIFEST.write_text(json.dumps(records, ensure_ascii=False, indent=1))
    print(json.dumps({"done": len(todo), "manifest": len(records), "counts": dict(counts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
