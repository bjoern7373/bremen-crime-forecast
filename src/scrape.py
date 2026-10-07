"""
Fetch and parse Polizei Bremen press-archive chunk pages into structured
records (one per press release).

Usage:
    python src/scrape.py            # scrape all known chunks, write data/processed/releases.jsonl
    python src/scrape.py --no-cache # ignore cached HTML, re-download everything
"""
from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from seed_urls import CHUNK_URLS

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
USER_AGENT = (
    "bremen-crime-forecast-research/0.1 "
    "(personal research project, backtesting public crime-report data; "
    "contact via github issue if this causes any trouble)"
)

_PUBDATE_RE = re.compile(r"^\((\d{2}\.\d{2}\.\d{4})\)$")


@dataclass
class PressRelease:
    pm_id: str
    source_url: str
    title: str
    publish_date: str | None  # DD.MM.YYYY, from the "(...)" line
    ort_raw: str | None
    zeit_raw: str | None  # tatzeit, free text as printed (format varies a lot)
    text: str


def _cache_path(url: str) -> Path:
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return RAW_DIR / f"{slug}.html"


def _wayback_fallback(url: str, target_yyyymmdd: str) -> str | None:
    """Fetch the closest Wayback Machine snapshot to target_yyyymmdd.

    Used when the Polizei Bremen press archive has deleted an old chunk
    from the live site (it rolls off after a few months). Returns None if
    no snapshot exists.
    """
    wb_url = f"https://web.archive.org/web/{target_yyyymmdd}000000/{url}"
    resp = requests.get(wb_url, headers={"User-Agent": USER_AGENT}, timeout=30,
                        allow_redirects=True)
    if resp.status_code != 200:
        return None
    resp.encoding = "utf-8"
    return resp.text


def fetch(url: str, use_cache: bool = True, wayback_target: str | None = None) -> str:
    cache_path = _cache_path(url)
    if use_cache and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        html = resp.text
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404 and wayback_target:
            print(f"  live page gone (404), falling back to Wayback Machine ({wayback_target})...")
            html = _wayback_fallback(url, wayback_target)
            if html is None:
                raise RuntimeError(f"No live page and no Wayback snapshot for {url}") from e
        else:
            raise

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(html, encoding="utf-8")
    return html


def parse_chunk(html: str, source_url: str) -> list[PressRelease]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for entry in soup.find_all("div", class_="entry-wrapper-1col"):
        pm_id = entry.get("id", "")
        h2 = entry.find("h2")
        if h2 is None:
            continue
        title = h2.get_text(strip=True)

        paragraphs = entry.find_all("p")
        publish_date = None
        ort_raw = None
        zeit_raw = None
        body_parts = []

        for p in paragraphs:
            raw_text = p.get_text("\n", strip=True)
            if not raw_text:
                continue
            m = _PUBDATE_RE.match(raw_text)
            if m and publish_date is None:
                publish_date = m.group(1)
                continue
            if ort_raw is None and raw_text.startswith("Ort:"):
                lines = raw_text.split("\n")
                for line in lines:
                    if line.startswith("Ort:"):
                        ort_raw = line[len("Ort:"):].strip()
                    elif line.startswith("Zeit:"):
                        zeit_raw = line[len("Zeit:"):].strip()
                continue
            body_parts.append(raw_text)

        out.append(PressRelease(
            pm_id=pm_id,
            source_url=source_url,
            title=title,
            publish_date=publish_date,
            ort_raw=ort_raw,
            zeit_raw=zeit_raw,
            text="\n".join(body_parts),
        ))
    return out


_SLUG_DATE_RE = re.compile(r"-ab-(\d{2})(\d{2})(\d{4})-\d+$")


def _slug_date(url: str) -> str | None:
    m = _SLUG_DATE_RE.search(url.rstrip("/"))
    if not m:
        return None
    dd, mm, yyyy = m.groups()
    return f"{yyyy}{mm}{dd}"


def scrape_all(use_cache: bool = True, polite_delay: float = 1.0) -> list[PressRelease]:
    all_releases: dict[str, PressRelease] = {}  # pm_id -> release, dedupes overlapping chunks
    for i, url in enumerate(CHUNK_URLS):
        # target the Wayback snapshot closest to just before the *next* chunk
        # started (so we catch the fullest version of this chunk before it
        # was superseded); for the last/open chunk, there's nothing to fall
        # back to (it's still live).
        next_url = CHUNK_URLS[i + 1] if i + 1 < len(CHUNK_URLS) else None
        wayback_target = _slug_date(next_url) if next_url else None

        html = fetch(url, use_cache=use_cache, wayback_target=wayback_target)
        releases = parse_chunk(html, url)
        print(f"[{i+1}/{len(CHUNK_URLS)}] {url} -> {len(releases)} releases")
        for r in releases:
            all_releases[r.pm_id] = r
        if not use_cache:
            time.sleep(polite_delay)
    return sorted(all_releases.values(), key=lambda r: r.pm_id)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cache", action="store_true")
    args = parser.parse_args()

    releases = scrape_all(use_cache=not args.no_cache)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PROCESSED_DIR / "releases.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in releases:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(f"\nWrote {len(releases)} press releases to {out_path}")


if __name__ == "__main__":
    main()
