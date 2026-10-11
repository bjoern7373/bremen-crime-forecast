"""
Recover archive pages the press office had already deleted before we started
collecting: ask the Wayback Machine (web.archive.org) which archive pages of
the Polizei Bremen press office it has ever saved, and store the latest
snapshot of every page we hold no copy of. Those pages then are part of our
own archive like any other (data/raw/, see scrape.py).

A snapshot may have been taken before the page was complete, so a recovered
page can still miss its last days -- timing.py only counts days we actually
hold releases for.

Safe to re-run: pages we already hold are left alone.

Usage:
    python src/backfill_wayback.py            # recover what is missing, then rebuild the archive
    python src/backfill_wayback.py --dry-run  # only list what the Wayback Machine has
"""
from __future__ import annotations

import argparse
import json
import time

import requests

import scrape

CDX_URL = "https://web.archive.org/cdx/search/cdx"
PAGE_PATTERN = "polizei.bremen.de/news/pressestelle/pressemeldungen-ab-*"
SOURCES_PATH = scrape.ROOT / "data" / "wayback_sources.json"   # which snapshot each recovered page is
MAX_SNAPSHOTS_TRIED = 4
POLITE_DELAY = 2.0


def _get(url: str, params: dict | None = None) -> requests.Response:
    """GET with a few patient retries -- the Wayback Machine answers 429/5xx when busy."""
    for attempt in range(4):
        try:
            resp = requests.get(url, params=params, timeout=60,
                                headers={"User-Agent": scrape.USER_AGENT})
            if resp.status_code not in (429, 500, 502, 503, 504):
                return resp
        except requests.RequestException as e:
            if attempt == 3:
                raise
            print(f"    retrying after {type(e).__name__}")
        time.sleep(5 * (attempt + 1))
    return resp


def list_snapshots() -> dict[str, list[str]]:
    """Archive-page URL -> timestamps of its saved snapshots, newest first
    (one per distinct content)."""
    resp = _get(CDX_URL, {"url": PAGE_PATTERN, "output": "json", "fl": "original,timestamp,digest",
                          "filter": "statuscode:200"})
    resp.raise_for_status()
    rows = resp.json()[1:]  # first row is the header
    by_page: dict[str, dict[str, str]] = {}
    for original, timestamp, digest in rows:
        m = scrape._CHUNK_SLUG_RE.search(original)
        if not m:
            continue
        url = scrape.CHUNK_BASE + m.group(0)
        # per distinct content keep the earliest capture; the newest *content* is what matters
        by_page.setdefault(url, {}).setdefault(digest, timestamp)
    return {url: sorted(digests.values(), reverse=True) for url, digests in by_page.items()}


def fetch_snapshot(url: str, timestamp: str) -> str | None:
    # "id_" returns the page exactly as archived, without the Wayback toolbar or rewritten links
    resp = _get(f"https://web.archive.org/web/{timestamp}id_/{url}")
    if resp.status_code != 200:
        return None
    resp.encoding = "utf-8"
    return resp.text


def recover(url: str, timestamps: list[str]) -> tuple[str, int] | None:
    """Store the newest snapshot that actually contains press releases.
    Returns (timestamp, number of releases) or None."""
    for timestamp in timestamps[:MAX_SNAPSHOTS_TRIED]:
        html = fetch_snapshot(url, timestamp)
        time.sleep(POLITE_DELAY)
        n = len(scrape.parse_chunk(html, url)) if html else 0
        if n:
            scrape.save_raw(url, html)
            return timestamp, n
        print(f"    snapshot {timestamp}: no releases in it, trying an older one")
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="only list, download nothing")
    args = parser.parse_args()

    snapshots = list_snapshots()
    known = scrape.load_known_chunks()
    missing = sorted((u for u in snapshots if not scrape._cache_path(u).exists()),
                     key=scrape._chunk_sort_key)
    print(f"The Wayback Machine has {len(snapshots)} archive pages; we hold "
          f"{sum(1 for u in snapshots if scrape._cache_path(u).exists())} of them, {len(missing)} are missing.")
    for url in missing:
        print(f"  missing: {scrape._slug(url)}  ({len(snapshots[url])} snapshots, newest {snapshots[url][0][:8]})")
    if args.dry_run or not missing:
        return

    sources = json.loads(SOURCES_PATH.read_text(encoding="utf-8")) if SOURCES_PATH.exists() else {}
    recovered = []
    for url in missing:
        print(f"  recovering {scrape._slug(url)}")
        result = recover(url, snapshots[url])
        if result is None:
            print("    nothing usable -- skipped")
            continue
        timestamp, n = result
        print(f"    stored snapshot {timestamp}: {n} releases")
        sources[scrape._slug(url)] = timestamp
        recovered.append(url)

    if not recovered:
        return
    scrape._atomic_write_text(SOURCES_PATH, json.dumps(sources, indent=2, sort_keys=True) + "\n")
    # make the recovered pages known to the scraper, exactly like automatically discovered ones
    all_urls = sorted(set(known) | set(recovered), key=scrape._chunk_sort_key)
    discovered = [u for u in all_urls if u not in scrape.CHUNK_URLS]
    scrape._atomic_write_text(scrape.KNOWN_CHUNKS_PATH, json.dumps(discovered, indent=2) + "\n")

    print(f"\nRecovered {len(recovered)} pages. Rebuilding the archive from all stored pages:\n")
    scrape.run("stored")


if __name__ == "__main__":
    main()
