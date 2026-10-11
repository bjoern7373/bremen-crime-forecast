"""
Fetch and parse Polizei Bremen press-archive chunk pages into structured
records (one per press release), and keep our own permanent archive of them.

The press office deletes releases from its site after a few months, so what
we have fetched once is kept for good and never depends on the site (or the
Wayback Machine) still having it:

    data/raw/                      every archive page as fetched; a stored page
                                   is never replaced by a version that has lost
                                   entries (the older version is kept alongside)
    data/archive/releases.jsonl    every release ever parsed -- only ever grows
    data/known_chunks.json         archive pages discovered automatically
    data/processed/releases.jsonl  copy of the archive, read by build_dataset.py

Usage:
    python src/scrape.py                # re-parse our stored pages (e.g. after a parser fix);
                                        # downloads only pages we have no copy of
    python src/scrape.py --update       # daily run: look for new archive pages and re-download
                                        # the newest ones; everything older comes from our copy
    python src/scrape.py --refetch-all  # try to re-download every page
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from seed_urls import CHUNK_URLS

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
ARCHIVE_PATH = ROOT / "data" / "archive" / "releases.jsonl"
KNOWN_CHUNKS_PATH = ROOT / "data" / "known_chunks.json"
USER_AGENT = (
    "bremen-crime-forecast-research/0.1 "
    "(personal research project, backtesting public crime-report data; "
    "contact via github issue if this causes any trouble)"
)

CHUNK_BASE = "https://www.polizei.bremen.de/news/pressestelle/"
# where the press office links its current archive page(s) from
DISCOVERY_URLS = [
    "https://www.polizei.bremen.de/news/pressestelle-60902",
    "https://www.polizei.bremen.de/rss-feed/polizei-bremen-pressemeldungen-4481?lang=de",
]
# Only the newest pages can still change. The second-newest is included so
# late additions made around a page rollover are still picked up.
FRESH_CHUNKS = 2
# exit code of a run that worked but could not download the newest page --
# distinct from a crash (1) or a usage error (2), so callers can tell
# "police site not reachable, try again soon" from "something is broken"
EXIT_NOT_UP_TO_DATE = 3

_PUBDATE_RE = re.compile(r"^\(?\s*(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\s*\)?$")
_CHUNK_SLUG_RE = re.compile(r"pressemeldungen-ab-(\d{2})(\d{2})(\d{4})-(\d+)")


def _parse_pubdate_line(line: str) -> str | None:
    """Normalize the press office's date line to DD.MM.YYYY. They write it as
    '(07.10.2026)', bare '07.10.2026' or with a 2-digit year '(06.02.26)';
    a typo'd year like '(11.02.206)' is left unrecognized rather than guessed."""
    m = _PUBDATE_RE.match(line.strip())
    if not m:
        return None
    dd, mm, yy = m.groups()
    if len(yy) == 2:
        yy = "20" + yy
    return f"{int(dd):02d}.{int(mm):02d}.{yy}"


@dataclass
class PressRelease:
    pm_id: str
    source_url: str
    title: str
    publish_date: str | None  # DD.MM.YYYY, from the date line under the title
    ort_raw: str | None
    zeit_raw: str | None  # tatzeit, free text as printed (format varies a lot)
    text: str


# ---- which release is which --------------------------------------------------
# Releases are filed under the id the press office gives them. Those ids are
# typed by hand, so the id alone is not trusted: a release only ever updates
# a stored one if it recognisably IS that release. Anything else under the
# same id (a re-used or mistyped number) is a different release and is kept
# as well, never in place of the first.

def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


def _similar(a: str | None, b: str | None) -> bool:
    """The same text, give or take a correction, an addition or a cut-off end."""
    a, b = _norm(a), _norm(b)
    if a == b:
        return True
    if not a or not b:
        return False
    shorter, longer = sorted((a[:3000], b[:3000]), key=len)
    matcher = difflib.SequenceMatcher(None, shorter, longer, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks()) >= 0.8 * len(shorter)


def _same_release(old: dict, new: dict) -> bool:
    if _similar(old.get("text"), new.get("text")):
        return True
    same_title = _norm(old["title"]) == _norm(new["title"])
    if not old["pm_id"]:
        # filed under its title only, and titles repeat ("Einbrecher gestellt")
        return same_title and old.get("publish_date") == new.get("publish_date")
    return same_title


def _identical(a: dict, b: dict) -> bool:
    return (_norm(a["title"]) == _norm(b["title"]) and a.get("publish_date") == b.get("publish_date")
            and _norm(a.get("text")) == _norm(b.get("text")))


def _richer(old: dict, new: dict) -> dict:
    """The new version of a release, minus anything it has lost: a field that
    went missing or a text that got clearly shorter (a cut-off download, an
    entry emptied by the press office) keeps its stored value."""
    out = dict(new)
    for field in ("publish_date", "ort_raw", "zeit_raw"):
        if not out.get(field) and old.get(field):
            out[field] = old[field]
    if len(_norm(new.get("text"))) < 0.8 * len(_norm(old.get("text"))):
        out["text"] = old["text"]
    return out


def _store(store: dict[str, dict], rec: dict) -> tuple[str, str]:
    """File one release in a key -> record dict. Returns its key and whether
    it was 'added', 'updated' or left 'unchanged'."""
    key = rec["pm_id"] if rec["pm_id"] else "no-id:" + rec["title"]
    old = store.get(key)
    if old is not None and not _same_release(old, rec):
        # same id, different release: file it next to the first one
        fingerprint = hashlib.sha1((_norm(rec["title"]) + "|" + (rec.get("publish_date") or ""))
                                   .encode("utf-8")).hexdigest()[:8]
        key = f"{key}#{fingerprint}"
        old = store.get(key)
    if old is None:
        store[key] = rec
        return key, "added"
    merged = _richer(old, rec)
    if merged == old:
        return key, "unchanged"
    store[key] = merged
    return key, "updated"


def _drop_rekeyed(store: dict[str, dict], rec: dict, still_present: set[str]) -> None:
    """When the press office adds or corrects the id of a release, the same
    release shows up under a new key. Remove its old entry (identical
    content, key no longer in use) so it isn't counted twice."""
    for key in [k for k, old in store.items()
                if k not in still_present and old["source_url"] == rec["source_url"] and _identical(old, rec)]:
        del store[key]


def _parse_into_store(html: str, url: str) -> dict[str, dict]:
    store: dict[str, dict] = {}
    for r in parse_chunk(html, url):
        _store(store, asdict(r))
    return store


def _read_jsonl(path: Path) -> list[dict]:
    # split on "\n" only: str.splitlines() would also break a record at
    # U+2028 / U+0085 inside a text, which json.dumps writes unescaped
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via a temp file so a crash mid-write can't leave a truncated file
    where a complete one used to be."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---- archive pages (chunks): which exist, and our stored copies ------------

def _slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


def _slug_date(url: str) -> str | None:
    m = _CHUNK_SLUG_RE.search(url)
    if not m:
        return None
    dd, mm, yyyy, _ = m.groups()
    return f"{yyyy}{mm}{dd}"


def _chunk_sort_key(url: str) -> tuple[str, int]:
    m = _CHUNK_SLUG_RE.search(url)
    dd, mm, yyyy, chunk_id = m.groups()
    return (f"{yyyy}{mm}{dd}", int(chunk_id))


def load_known_chunks() -> list[str]:
    """Hand-seeded pages (seed_urls.py), automatically discovered ones and
    every page we hold a copy of, oldest first."""
    urls = set(CHUNK_URLS)
    if KNOWN_CHUNKS_PATH.exists():
        urls.update(json.loads(KNOWN_CHUNKS_PATH.read_text(encoding="utf-8")))
    if RAW_DIR.exists():
        for path in RAW_DIR.glob("pressemeldungen-ab-*.html"):
            urls.add(CHUNK_BASE + _CHUNK_SLUG_RE.search(path.name).group(0))
    return sorted((u for u in urls if _CHUNK_SLUG_RE.search(u)), key=_chunk_sort_key)


def discover_chunks(known: list[str]) -> list[str]:
    """Check the press office's overview page and RSS feed for archive pages
    we don't know yet (they start a new one every few weeks). Without this a
    new page would go unnoticed and the map would silently stop updating.
    A failed check is only a warning -- the known pages still get processed."""
    found = set()
    for url in DISCOVERY_URLS:
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"  WARNING: could not check for new archive pages at {url} ({e})")
            continue
        for m in _CHUNK_SLUG_RE.finditer(resp.text):
            if 2024 <= int(m.group(3)) <= 2035:  # ignore anything that isn't a plausible date
                found.add(CHUNK_BASE + m.group(0))

    new = found - set(known)
    if not new:
        return known
    for u in sorted(new, key=_chunk_sort_key):
        print(f"  NEW archive page discovered: {u}")
    all_urls = sorted(set(known) | new, key=_chunk_sort_key)
    discovered = [u for u in all_urls if u not in CHUNK_URLS]
    _atomic_write_text(KNOWN_CHUNKS_PATH, json.dumps(discovered, indent=2) + "\n")
    return all_urls


def _cache_path(url: str) -> Path:
    return RAW_DIR / f"{_slug(url)}.html"


def stored_versions(url: str) -> list[Path]:
    """Our stored copies of one archive page, oldest first: any dated
    snapshots kept by save_raw() (their names sort by time), then the
    current copy."""
    versions = sorted(RAW_DIR.glob(f"{_slug(url)}@*.html"))
    if _cache_path(url).exists():
        versions.append(_cache_path(url))
    return versions


def save_raw(url: str, html: str) -> None:
    """Store a freshly fetched page as our copy -- without ever losing
    anything: if our current copy contains releases the new version no longer
    has, or has in a poorer form (shorter text, a field gone), the current
    copy is kept alongside as a dated snapshot instead of being overwritten."""
    path = _cache_path(url)
    if path.exists():
        old_html = path.read_text(encoding="utf-8")
        old, new = _parse_into_store(old_html, url), _parse_into_store(html, url)
        lost = [k for k in old if k not in new]
        poorer = [k for k in old if k in new and _richer(old[k], new[k]) != new[k]]
        if lost or poorer:
            while True:
                snapshot = RAW_DIR / f"{_slug(url)}@{time.strftime('%Y%m%d-%H%M%S')}.html"
                if not snapshot.exists():
                    break
                time.sleep(1)  # one name per second keeps plain sorting chronological
            _atomic_write_text(snapshot, old_html)
            print(f"  NOTE: {len(lost)} release(s) no longer on the page, {len(poorer)} shortened -- "
                  f"previous version kept as {snapshot.name}")
    _atomic_write_text(path, html)


def _wayback_fallback(url: str, target_yyyymmdd: str) -> str | None:
    """Fetch the closest Wayback Machine snapshot to target_yyyymmdd.

    Only used for a page the press office has already deleted AND that we
    hold no copy of (e.g. a fresh checkout). Returns None if no snapshot
    exists.
    """
    wb_url = f"https://web.archive.org/web/{target_yyyymmdd}000000/{url}"
    resp = requests.get(wb_url, headers={"User-Agent": USER_AGENT}, timeout=30,
                        allow_redirects=True)
    if resp.status_code != 200:
        return None
    resp.encoding = "utf-8"
    return resp.text


def _fetch_live(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    return resp.text


def refresh_chunk(url: str, wayback_target: str | None) -> tuple[str, bool]:
    """Try to bring our copy of one archive page up to date; returns a short
    status for the log and whether a version was actually fetched. Never
    raises on network trouble and never makes our copy worse: if the page
    can't be fetched (or comes back without entries) we simply keep working
    from the copy we have."""
    have_copy = _cache_path(url).exists()
    try:
        html = _fetch_live(url)
        source = "live"
    except requests.RequestException as e:
        gone = (isinstance(e, requests.exceptions.HTTPError)
                and e.response is not None and e.response.status_code == 404)
        reason = "deleted from the police site" if gone else f"live fetch failed: {type(e).__name__}"
        if have_copy:
            return f"our copy ({reason})", False
        html = None
        if wayback_target:
            try:
                html = _wayback_fallback(url, wayback_target)
            except requests.RequestException as e2:
                reason += f"; Wayback Machine failed: {type(e2).__name__}"
        if html is None:
            return f"UNAVAILABLE ({reason}; no copy of our own)", False
        source = f"Wayback Machine snapshot {wayback_target}"

    problem = None
    if "</html" not in html.lower():
        problem = "fetched page was cut off"
    elif not parse_chunk(html, url):
        # an error/maintenance page served with status 200, or a layout change
        problem = "fetched page had no entries"
    if problem:
        return (f"our copy ({problem} -- ignored)" if have_copy else f"UNAVAILABLE ({problem})"), False
    save_raw(url, html)
    return source, True


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
            # the date line precedes "Ort:" -- usually its own paragraph, but
            # sometimes the first line of one that also holds Ort/Zeit
            if publish_date is None and ort_raw is None:
                first_line, _, rest = raw_text.partition("\n")
                parsed = _parse_pubdate_line(first_line)
                if parsed:
                    publish_date = parsed
                    raw_text = rest.strip()
                    if not raw_text:
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


def parse_stored(url: str) -> dict[str, dict]:
    """Every release found in any stored version of one archive page; where
    a release appears in several versions, the newest version wins (minus
    anything it has lost, see _richer)."""
    merged: dict[str, dict] = {}
    for path in stored_versions(url):
        version = _parse_into_store(path.read_text(encoding="utf-8"), url)
        for key, rec in version.items():
            if key not in merged:
                _drop_rekeyed(merged, rec, still_present=set(version))
            _store(merged, rec)
    return merged


def scrape_all(mode: str = "stored", polite_delay: float = 1.0) -> tuple[dict[str, dict], bool]:
    """mode 'stored': work from our copies, downloading only pages we have
    none of. 'update': additionally look for new archive pages and re-download
    the newest FRESH_CHUNKS ones. 'refetch-all': try to re-download all.

    Returns the releases plus whether the run is up to date: False if the
    newest page -- the only one that receives new releases -- was due for a
    download and couldn't be fetched."""
    chunks = load_known_chunks()
    # the newest page we hold was still growing when we last fetched it, so it
    # is re-downloaded together with everything newer -- however many new
    # pages have appeared since the last run (the PC may have been off for weeks)
    held = [url for url in chunks if _cache_path(url).exists()]
    newest_held = held[-1] if held else None
    if mode != "stored":
        chunks = discover_chunks(chunks)
    if mode == "update":
        start = chunks.index(newest_held) - (FRESH_CHUNKS - 1) if newest_held else 0
        to_refresh = set(chunks[max(start, 0):]) | set(chunks[-FRESH_CHUNKS:])
    elif mode == "refetch-all":
        to_refresh = set(chunks)
    else:
        to_refresh = set()

    all_releases: dict[str, dict] = {}
    up_to_date = True
    for i, url in enumerate(chunks):
        # if we hold no copy and the page is gone, target the Wayback snapshot
        # closest to just before the *next* page started (the fullest version
        # of this one); the newest page has no successor and no such fallback
        next_url = chunks[i + 1] if i + 1 < len(chunks) else None
        wayback_target = _slug_date(next_url) if next_url else None

        if url in to_refresh or not _cache_path(url).exists():
            status, fetched = refresh_chunk(url, wayback_target)
            if not fetched and i == len(chunks) - 1:
                up_to_date = False
            if fetched:
                time.sleep(polite_delay)  # be gentle with whoever just answered
        else:
            status = "our copy"
        releases = parse_stored(url)
        print(f"[{i+1}/{len(chunks)}] {_slug(url)} -> {len(releases)} releases  [{status}]")
        for rec in releases.values():
            _store(all_releases, rec)
    return all_releases, up_to_date


# ---- the permanent archive -------------------------------------------------

def load_archive() -> dict[str, dict]:
    """key -> release record: everything found in the archive file or in its
    copy under data/processed (the same content after every run; on the very
    first run only the copy exists). Reading both means a file that got
    emptied or cut short -- a power cut, a failed copy -- cannot make the
    archive smaller. A line that isn't valid JSON raises instead: carrying
    on would overwrite a damaged archive with less."""
    archive: dict[str, dict] = {}
    counts = []
    for path in (ARCHIVE_PATH, PROCESSED_DIR / "releases.jsonl"):
        if path.exists():
            records = _read_jsonl(path)
            counts.append(len(records))
            for rec in records:
                _store(archive, rec)
    if len(set(counts)) > 1:
        print(f"  WARNING: the archive and its copy differ ({counts[0]} vs {counts[1]} releases) -- "
              f"continuing with everything found in either ({len(archive)})")
    return archive


def merge_into_archive(archive: dict[str, dict], parsed: dict[str, dict]) -> tuple[int, int]:
    """Add newly parsed releases and refresh ones we already hold (so parser
    fixes and corrections by the press office take effect). No release is
    ever removed or replaced by a different or poorer one. Returns (added,
    updated)."""
    added = updated = 0
    for key, rec in parsed.items():
        if key not in archive:
            _drop_rekeyed(archive, rec, still_present=set(parsed))
        what = _store(archive, rec)[1]
        added += what == "added"
        updated += what == "updated"
    return added, updated


def write_archive(archive: dict[str, dict], n_loaded: int) -> None:
    if len(archive) < n_loaded:
        raise RuntimeError(f"refusing to shrink the archive ({n_loaded} -> {len(archive)} releases)")
    records = sorted(archive.values(), key=lambda rec: rec["pm_id"])
    text = "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records)
    _atomic_write_text(ARCHIVE_PATH, text)
    _atomic_write_text(PROCESSED_DIR / "releases.jsonl", text)


def run(mode: str, polite_delay: float = 1.0) -> tuple[dict[str, dict], bool]:
    archive = load_archive()
    n_loaded = len(archive)
    parsed, up_to_date = scrape_all(mode, polite_delay)
    added, updated = merge_into_archive(archive, parsed)
    write_archive(archive, n_loaded)

    only_in_archive = sum(1 for key in archive if key not in parsed)
    print(f"\nArchive: {len(archive)} press releases ({added} new, {updated} updated, "
          f"{only_in_archive} kept that are on no stored page) -> {ARCHIVE_PATH}")
    if not up_to_date:
        print("WARNING: the newest archive page could not be downloaded -- nothing was lost, "
              "but today's new releases are not in yet.")
    return archive, up_to_date


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", "--no-cache", dest="update", action="store_true",
                        help="daily run: discover new archive pages, re-download the newest ones")
    parser.add_argument("--refetch-all", action="store_true",
                        help="try to re-download every archive page")
    args = parser.parse_args()
    _, up_to_date = run("refetch-all" if args.refetch_all else ("update" if args.update else "stored"))
    # lets callers retry soon instead of treating today's update as done
    sys.exit(0 if up_to_date else EXIT_NOT_UP_TO_DATE)


if __name__ == "__main__":
    main()
