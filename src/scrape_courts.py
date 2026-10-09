"""
Scrape Landgericht Bremen press releases (Auftakt/Urteil/Rechtskraft PDFs --
the only Bremen court with a real, browsable multi-year press archive; see
README "Gerichtsurteile" section for why Amtsgericht/Staatsanwaltschaft don't
qualify) plus a running log of the Amtsgericht Bremen weekly hearing-preview
bulletin (no archive on their side -- we build our own history by polling).

Usage:
    python src/scrape_courts.py
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
import io

sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify import classify

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw_courts"
PROCESSED_DIR = ROOT / "data" / "processed"
USER_AGENT = (
    "bremen-crime-forecast-research/0.1 "
    "(personal research project, backtesting public crime-report data; "
    "contact via github issue if this causes any trouble)"
)

LG_BASE = "https://www.landgericht.bremen.de"
LG_LISTING_URLS = [
    f"{LG_BASE}/aktuelles/pressemitteilungen-und-terminvorschau-1641",
    f"{LG_BASE}/aktuelles/pressemitteilungen-und-terminvorschau/archiv-pressemitteilungen-14926",
]
AG_URL = "https://www.amtsgericht.bremen.de/aktuelles/pressemitteilungen-1739"

MIN_PM_YEAR_2DIGIT = 24  # skip PMs from before 2024 -- our incident data starts 2024-10-31

_PM_LINK_RE = re.compile(r'href="(/sixcms/media\.php/13/PM%20?(\d+)-(\d{2})%20([^"]+)\.pdf)"', re.IGNORECASE)


def _get(url: str) -> requests.Response:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp


def parse_pm_links(html: str) -> list[dict]:
    """Extract (pdf_url, pm_number, pm_year, title) for real case PMs, skipping
    the monthly 'Termine im <Monat>' calendar PDFs (no case-specific content)."""
    out = []
    for href, num, yy, title_enc in _PM_LINK_RE.findall(html):
        if int(yy) < MIN_PM_YEAR_2DIGIT:
            continue
        title = urllib.parse.unquote_plus(title_enc)
        if "termine im" in title.lower() or "terminübersicht" in title.lower():
            continue  # monthly hearing calendars, not a single case -- would pollute per-case matching
        out.append({
            "pdf_url": LG_BASE + href,
            "pm_number": int(num),
            "pm_year": 2000 + int(yy),
            "title": title,
        })
    return out


def stage_of(title: str) -> str:
    t = title.lower()
    if "rechtskraft" in t:
        return "rechtskraft"
    if "urteil" in t:
        return "urteil"
    if "auftakt" in t or "fortsetzung" in t:
        return "auftakt"
    if "anklage" in t:
        return "anklage"
    return "sonstiges"


_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
_PUBDATE_RE = re.compile(
    r"Pressemitteilung\s*Nr\.\s*(\d+)\s*/\s*(\d{4}).*?vom\s*(\d{1,2})\.\s*([A-Za-zäöü]+)\s*(\d{4})",
    re.IGNORECASE | re.DOTALL,
)


def extract_publish_date(text: str) -> str | None:
    m = _PUBDATE_RE.search(text)
    if not m:
        return None
    day, month_name, year = m.group(3), m.group(4).lower(), m.group(5)
    month = _MONTHS.get(month_name)
    if not month:
        return None
    return f"{year}-{month:02d}-{int(day):02d}"


def fetch_pdf_text(pdf_url: str, cache_key: str) -> str:
    cache_path = RAW_DIR / f"{cache_key}.txt"
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8")
    resp = _get(pdf_url)
    reader = PdfReader(io.BytesIO(resp.content))
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(text, encoding="utf-8")
    return text


def scrape_landgericht() -> list[dict]:
    seen_urls: set[str] = set()
    all_links: list[dict] = []
    for listing_url in LG_LISTING_URLS:
        html = _get(listing_url).text
        for link in parse_pm_links(html):
            if link["pdf_url"] not in seen_urls:
                seen_urls.add(link["pdf_url"])
                all_links.append(link)

    releases = []
    for i, link in enumerate(all_links):
        cache_key = f"lg_{link['pm_year']}_{link['pm_number']:03d}"
        try:
            text = fetch_pdf_text(link["pdf_url"], cache_key)
        except Exception as e:
            print(f"  [{i+1}/{len(all_links)}] FAILED {link['pdf_url']}: {e}")
            continue
        stage = stage_of(link["title"])
        category = classify(link["title"], text)
        publish_date = extract_publish_date(text)
        releases.append({
            "pm_id": f"lg-{link['pm_year']}-{link['pm_number']}",
            "court": "Landgericht Bremen",
            "pm_number": link["pm_number"],
            "pm_year": link["pm_year"],
            "title": link["title"],
            "stage": stage,
            "category": category,
            "publish_date": publish_date,
            "pdf_url": link["pdf_url"],
            "text": text,
        })
        print(f"  [{i+1}/{len(all_links)}] PM {link['pm_number']}/{link['pm_year']} "
              f"({stage}, {category}): {link['title'][:60]}")
        time.sleep(0.3)  # politeness -- robots.txt has no crawl-delay, but no reason to hammer it
    return releases


_AG_ENTRY_RE = re.compile(
    r"(?P<weekday>Montag|Dienstag|Mittwoch|Donnerstag|Freitag),\s*(?P<date>\d{1,2}\.\d{1,2}\.\d{4}),\s*"
    r"(?P<time>\d{1,2}:\d{2})\s*Uhr,\s*(?P<room_info>[^\n]+)\n"
    r"(?P<offense>[^\n]+)\n"
    r"(?P<az>[^\n]*Js\s*\d+/\d+)\n"
    r"(?P<narrative>.+?)(?=\n(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag),\s*\d{1,2}\.\d{1,2}\.\d{4}|\Z)",
    re.DOTALL,
)


def scrape_amtsgericht_weekly() -> list[dict]:
    html = _get(AG_URL).text
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("main") or soup.find(class_="content") or soup.body
    text = main.get_text("\n", strip=True)

    entries = []
    for m in _AG_ENTRY_RE.finditer(text):
        narrative = re.sub(r"\s+", " ", m.group("narrative")).strip()
        offense = m.group("offense").strip()
        entries.append({
            "weekday": m.group("weekday"),
            "date": m.group("date"),
            "time": m.group("time"),
            "room_info": m.group("room_info").strip(),
            "offense": offense,
            "az": m.group("az").strip(),
            "narrative": narrative,
            "category": classify(offense, narrative),
        })
    return entries


def update_amtsgericht_log(entries: list[dict]) -> int:
    """Append-only log, deduped by Az (case file number) -- builds our own
    forward-looking history since the Amtsgericht site itself only ever shows
    the current week and overwrites it."""
    log_path = PROCESSED_DIR / "amtsgericht_log.jsonl"
    existing_az: set[str] = set()
    if log_path.exists():
        with log_path.open(encoding="utf-8") as f:
            for line in f:
                existing_az.add(json.loads(line)["az"])

    new_count = 0
    with log_path.open("a", encoding="utf-8") as f:
        for e in entries:
            if e["az"] in existing_az:
                continue
            record = dict(e, first_seen=time.strftime("%Y-%m-%d"))
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing_az.add(e["az"])
            new_count += 1
    return new_count


def main():
    print("Scraping Landgericht Bremen press releases (current + archive)...")
    releases = scrape_landgericht()
    out_path = PROCESSED_DIR / "court_releases.jsonl"
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for r in releases:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote {len(releases)} Landgericht releases to {out_path}")

    print("\nPolling Amtsgericht Bremen weekly hearing bulletin...")
    try:
        ag_entries = scrape_amtsgericht_weekly()
        new_count = update_amtsgericht_log(ag_entries)
        print(f"Found {len(ag_entries)} entries this week, {new_count} new (appended to amtsgericht_log.jsonl)")
    except Exception as e:
        print(f"  Amtsgericht poll failed (non-fatal, continuing): {e}")


if __name__ == "__main__":
    main()
