"""
Reading lists for the map's "Meldungen" panel: every police press release
and every court release we hold, newest first, each with its full text one
click away.

    assets/news_index.json    headlines only -- what the two lists show:
                              {"police": [...], "court": [...]}
    assets/police_other.json  full text of the police releases that are not
                              on the map (no Bremen Stadtteil recognised); the
                              text of the others is in map_data.json already
    assets/court_cases.json   full text of the court releases

Every police release is addressed by a key that stays the same from day to
day (its press-release number), never by its position in a file: positions
shift whenever a release is added.

Called by export_map_data.py; `python src/export_news.py` runs it alone.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
ASSETS_DIR = ROOT / "assets"
AG_URL = "https://www.amtsgericht.bremen.de/aktuelles/pressemitteilungen-1739"


def read_jsonl(path: Path) -> list[dict]:
    # "\n" only: str.splitlines() would also cut a record at U+2028 / U+0085 inside a text
    return [json.loads(line) for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def iso_date(ddmmyyyy) -> str | None:
    """'07.10.2026' -> '2026-10-07'; anything else (missing, a typo'd year) -> None."""
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", str(ddmmyyyy or "").strip())
    if not m:
        return None
    dd, mm, yyyy = (int(g) for g in m.groups())
    try:
        date(yyyy, mm, dd)
    except ValueError:
        return None
    return f"{yyyy:04d}-{mm:02d}-{dd:02d}" if 2000 <= yyyy <= 2100 else None


# ---- one stable key per police release ----------------------------------------

def release_key(pm_id, title: str) -> str:
    """The press-release number, the same whether it comes from releases.jsonl
    (text, '002825') or from a CSV read back by pandas (a number, 2825.0).
    The few releases without a number are keyed by their title."""
    try:
        return str(int(float(pm_id)))
    except (TypeError, ValueError):
        return "t:" + str(title)


def key_maker(releases: list[dict]):
    """Returns key(pm_id, title, publish_date): release_key(), made unique
    for the rare number the press office used twice (see scrape.py). The
    earliest release under a number keeps the bare number -- so its key does
    not change when a second one turns up -- and every later one gets a
    suffix that depends only on that release itself."""
    groups = defaultdict(list)
    for r in releases:
        groups[release_key(r.get("pm_id"), r["title"])].append(r)
    keeper = {}
    for base, group in groups.items():
        if len(group) > 1:
            first = min(group, key=lambda r: (iso_date(r.get("publish_date")) or "9999", r["title"]))
            keeper[base] = (first["title"], _text(first.get("publish_date")))

    def key(pm_id, title, publish_date) -> str:
        base = release_key(pm_id, title)
        if base not in keeper or keeper[base] == (title, _text(publish_date)):
            return base
        mark = hashlib.sha1(f"{title}|{_text(publish_date)}".encode("utf-8")).hexdigest()[:6]
        return f"{base}~{mark}"
    return key


def _number_and_year(key: str) -> tuple[int, int] | None:
    """'61626' -> (616, 26): the running number and year the press office
    encodes in its ids. None for anything that doesn't look like one."""
    if not key.isdigit() or len(key) < 3:
        return None
    number, year = int(key[:-2]), int(key[-2:])
    return (number, year) if 20 <= year <= 60 and number > 0 else None


def _page_start(source_url) -> date | None:
    """The day an archive page of the press office starts ('pressemeldungen-ab-11092026-...')."""
    m = re.search(r"pressemeldungen-ab-(\d{2})(\d{2})(\d{4})", _text(source_url))
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    except ValueError:
        return None


def release_days(releases: list[dict], key, today: date | None = None) -> dict[str, str | None]:
    """The publication day of every release, by key (ISO text, None if unknown).

    It is the date the press office typed -- unless that date cannot be
    right. They sometimes mistype it (a wrong month) or leave it out, while
    their running numbers stay in order. So a date that is missing, lies in
    the future, or lies outside the dates of the releases numbered around
    it AND does not fit the archive page the release sits on, is replaced by
    its neighbour's date. (A date that fits its own page is kept: then the
    number is the odd one out, not the date.)"""
    today = today or date.today()
    typed, numbered = {}, defaultdict(list)
    for r in releases:
        k = key(r.get("pm_id"), r["title"], r.get("publish_date"))
        iso = iso_date(r.get("publish_date"))
        day = date.fromisoformat(iso) if iso else None
        typed[k] = day if day and day <= today else None
        ny = _number_and_year(k.split("~")[0])
        if ny:
            numbered[ny[1]].append((ny[0], k, _page_start(r.get("source_url"))))
        elif typed[k] is None:
            typed[k] = _page_start(r.get("source_url"))      # no number to go by: at least its page
    days, slack = dict(typed), timedelta(days=3)
    for group in numbered.values():
        group.sort(key=lambda item: item[0])
        for i, (_, k, page) in enumerate(group):
            before = [typed[other] for _, other, _ in group[max(0, i - 3):i] if typed[other]]
            around = before + [typed[other] for _, other, _ in group[i + 1:i + 4] if typed[other]]
            mine = typed[k]
            if not around:
                days[k] = mine or page
            elif mine is None or mine < min(around) - slack or mine > max(around) + slack:
                if mine and page and page - slack <= mine <= page + timedelta(days=75):
                    continue
                days[k] = before[-1] if before else around[0]
    return {k: (d.isoformat() if d else None) for k, d in days.items()}


# ---- police ----------------------------------------------------------------

class StaleInput(RuntimeError):
    """incidents.csv / incidents_geo.csv are older than the archive: an
    earlier step of the update did not run through."""


def build_police(releases: list[dict], incidents: pd.DataFrame, geo: pd.DataFrame, key,
                 today: date | None = None) -> tuple[list[dict], dict]:
    """(index entries newest first, full records of the releases not on the map).

    releases: the archive (one record per press release). incidents: one row
    per release and Stadtteil, with its offense category. geo: the rows that
    made it onto the map. key: from key_maker(releases)."""
    rows_of = defaultdict(list)
    for row in incidents.itertuples(index=False):
        rows_of[key(row.pm_id, row.title, row.publish_date)].append(row)
    on_map = {key(row.pm_id, row.title, row.publish_date) for row in geo.itertuples(index=False)}
    days = release_days(releases, key, today)

    index, other = [], {}
    for r in releases:
        k = key(r.get("pm_id"), r["title"], r.get("publish_date"))
        rows = rows_of.get(k, [])
        if not rows and len(incidents):
            raise StaleInput(f"release {k!r} is in the archive but not in incidents.csv -- run build_dataset.py")
        stadtteile = []
        for row in rows:
            if _text(row.stadtteil) and row.stadtteil not in stadtteile:
                stadtteile.append(row.stadtteil)
        in_bremen = [row for row in rows if _text(row.stadtteil) and not bool(getattr(row, "is_bremerhaven", False))]
        if in_bremen and k not in on_map and len(geo):
            raise StaleInput(f"release {k!r} names {stadtteile} but is not in incidents_geo.csv -- run geocode.py")
        # no usable publication day at all: fall back to the day of the incident
        day = days.get(k) or next((_text(row.incident_date)[:10] for row in rows if _text(row.incident_date)), None)

        entry = {"k": k, "d": day, "t": r["title"], "c": rows[0].category if rows else "Sonstiges",
                 "s": " / ".join(stadtteile)}
        if k in on_map:
            entry["m"] = 1
        else:
            other[k] = {
                "title": r["title"], "pub": day, "cat": entry["c"], "stadtteil": entry["s"],
                "ort": _text(r.get("ort_raw")), "zeit": _text(r.get("zeit_raw")),
                "text": _text(r.get("text")), "source_url": _text(r.get("source_url")),
            }
        index.append(entry)

    def newest_first(entry):
        ny = _number_and_year(entry["k"].split("~")[0])
        return (entry["d"] or "", ny[0] if ny else 0)
    index.sort(key=newest_first, reverse=True)
    return index, other


# ---- courts ----------------------------------------------------------------

_WEEKDAY_LINE = re.compile(r"(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonnabend|Sonntag),?\s")
_JOINING_WORD = re.compile(r"(und|oder|bzw|sowie|beziehungsweise)\b")


def reflow(text: str) -> str:
    """Text extracted from a PDF has a line break at the end of every printed
    line. Join the lines of each paragraph again; the wording itself is left
    untouched. Blank lines separate paragraphs. Within a paragraph a line
    break is kept before each line of a list of hearing dates, and after a
    short line that ends a sentence (a paragraph the PDF set off without a
    blank line)."""
    paragraphs, lines = [], []
    for raw in (text or "").split("\n") + [""]:
        line = raw.strip()
        if line:
            lines.append(line)
            continue
        if lines:
            width = max(len(l) for l in lines)
            out, previous = lines[0], lines[0]
            for nxt in lines[1:]:
                glued = out.endswith("-") and len(out) > 1 and not out[-2].isspace()
                short_end = len(lines) > 2 and previous[-1] in ".!?:" and len(previous) < 0.6 * width
                # one hearing date per line -- but only in a list of dates, not where a
                # sentence merely happens to continue with a weekday on the next line
                date_list = _WEEKDAY_LINE.match(nxt) and (previous[-1] in ",:" or _WEEKDAY_LINE.match(previous))
                if date_list or short_end:
                    out += "\n" + nxt
                elif glued and _JOINING_WORD.match(nxt) and out.split()[-1][0].isupper():
                    out += " " + nxt                 # "Ein-" / "und Ausgang"
                elif (glued and out[-2].islower() and nxt[0].islower()
                      and "-" not in out[:-1].split()[-1]):
                    out = out[:-1] + nxt             # a word split at the line end: "Bre-" / "men"
                elif glued:
                    out += nxt                       # a real hyphen: "30-" / "jährige", "Sachs-" / "Straße"
                else:
                    out += " " + nxt
                previous = nxt
            paragraphs.append(re.sub(r"[ \t]+", " ", out))
            lines = []
    return "\n\n".join(paragraphs)


def court_stage(headline: str) -> str:
    """What a court release is about, from its headline. The specific cases
    come first: 'Termin zur Urteilsverkündung' announces a verdict, it isn't
    one, and a postponed opening is not an opening."""
    t = headline.lower()
    if "rechtskraft" in t or "rechtskräftig" in t:
        return "rechtskraft"
    if re.search(r"termins?(verschiebung|verlegung|aufhebung|änderung)|verlegung des termins|"
                 r"änderung des beginns|neubeginn|aussetzung|termine\b.*\baufgehoben", t):
        return "terminaenderung"
    if "urteilsverkündung" in t:
        return "urteilstermin"
    if re.search(r"\burteil", t):
        return "urteil"
    if "auftakt" in t or "fortsetzung" in t:
        return "auftakt"
    if "anklage" in t:
        return "anklage"
    return "sonstiges"


def headline_of(reflowed: str) -> str | None:
    """The headline printed in a Landgericht release: the paragraph after
    'Pressemitteilung Nr. x/yyyy vom ...'. (The title we get from the PDF's
    file name is often cut off or carries leftovers.)"""
    paragraphs = reflowed.split("\n\n")
    for i, p in enumerate(paragraphs[:4]):
        if re.search(r"Pressemitteilung\s*Nr\.", p) and i + 1 < len(paragraphs):
            head = paragraphs[i + 1].replace("\n", " ").strip()
            if 10 <= len(head) <= 200 and not head[0].isdigit():
                return head
            return None
    return None


def build_court(lg_releases: list[dict], ag_entries: list[dict], matches: list[dict],
                geo: pd.DataFrame, key, kept: list[dict] | None = None) -> list[dict]:
    """Full records, newest first: Landgericht press releases, plus the
    hearings from the Amtsgericht's weekly bulletin that we have logged.

    kept: the records exported last time. An entry the court's own archive
    page no longer lists (or a day on which that page could not be read)
    stays in our list instead of silently disappearing."""
    # court release id -> police releases on the map that match_verdicts.py linked to it
    linked = defaultdict(list)
    for m in matches:
        wanted = {release_key(pm_id, "") for pm_id in m.get("matched_pm_ids", [])}
        reports = {}
        for row in geo.itertuples(index=False):
            if release_key(row.pm_id, row.title) in wanted:
                k = key(row.pm_id, row.title, row.publish_date)
                reports.setdefault(k, {"k": k, "title": row.title, "date": _text(row.incident_date)[:10]})
        for rel in m.get("releases", []):
            for report in reports.values():
                if report not in linked[rel["pm_id"]]:
                    linked[rel["pm_id"]].append(report)

    cases = {c["id"]: c for c in (kept or [])}
    for r in lg_releases:
        text = reflow(r.get("text") or "")
        title = headline_of(text) or r["title"]
        cases[r["pm_id"]] = {
            "id": r["pm_id"], "court": r.get("court") or "Landgericht Bremen",
            "date": r.get("publish_date"), "time": "", "title": title, "stage": court_stage(title),
            "url": r.get("pdf_url") or "", "text": text, "reports": linked.get(r["pm_id"], []),
        }
    for e in ag_entries:
        day = iso_date(e.get("date"))
        slug = re.sub(r"[^0-9a-z]+", "-", (e.get("az") or "").lower()).strip("-")
        # the last hearing of a bulletin used to be logged with the press office's imprint attached
        narrative = re.split(r"\s+Verantwortlich\s+Richter", e.get("narrative") or "")[0]
        cases[f"ag-{slug}-{day}"] = {
            "id": f"ag-{slug}-{day}", "court": "Amtsgericht Bremen", "date": day,
            "time": e.get("time") or "", "title": f"Verhandlung: {e.get('offense') or 'Vorwurf nicht genannt'}",
            "stage": "termin", "url": AG_URL,
            "text": (f"{e.get('weekday', '')}, {e.get('date', '')}, {e.get('time', '')} Uhr – "
                     f"{e.get('room_info', '')}\nAktenzeichen: {e.get('az', '')}\n\n{narrative}"),
            "reports": [],
        }
    return sorted(cases.values(), key=lambda c: (c["date"] or "", c.get("time") or "", c["id"]), reverse=True)


def court_index(cases: list[dict]) -> list[dict]:
    return [{"id": c["id"], "d": c["date"], "t": c["title"], "court": c["court"], "stage": c["stage"]}
            for c in cases]


# ---- files -----------------------------------------------------------------

def write_all(files: dict[Path, object]) -> None:
    """Write a set of files that belong together: first every file under a
    temporary name, and only when all of them are complete put them in
    place -- so the pages never find a new file next to an old one."""
    staged = []
    for path, data in files.items():
        tmp = path.with_name(path.name + ".tmp")
        # allow_nan=False: a NaN that slipped in from a CSV would make the file unreadable for browsers
        tmp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        staged.append((tmp, path))
    for tmp, path in staged:
        for attempt in range(20):
            try:
                tmp.replace(path)
                break
            except PermissionError:      # the local map server is just sending the old file
                if attempt == 19:
                    raise
                time.sleep(0.25)
        print(f"Wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


def _read_log(path: Path) -> list[dict]:
    """The Amtsgericht log is appended to line by line; a line cut off by a
    crash must not take the whole list export down with it."""
    entries = []
    for n, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"  WARNING: {path.name} line {n} is not readable -- skipped")
    return entries


def build() -> dict[Path, object]:
    """Everything this module exports, as {file: content} -- nothing is written here."""
    geo = pd.read_csv(PROCESSED_DIR / "incidents_geo.csv")
    incidents = pd.read_csv(PROCESSED_DIR / "incidents.csv")
    releases = read_jsonl(PROCESSED_DIR / "releases.jsonl")
    key = key_maker(releases)
    police, other = build_police(releases, incidents, geo, key)

    cases_path = ASSETS_DIR / "court_cases.json"
    court_path = PROCESSED_DIR / "court_releases.jsonl"
    kept = json.loads(cases_path.read_text(encoding="utf-8")) if cases_path.exists() else []
    files = {}
    if court_path.exists():
        ag_path, matches_path = PROCESSED_DIR / "amtsgericht_log.jsonl", PROCESSED_DIR / "court_matches.json"
        kept = build_court(
            read_jsonl(court_path),
            _read_log(ag_path) if ag_path.exists() else [],
            json.loads(matches_path.read_text(encoding="utf-8")) if matches_path.exists() else [],
            geo, key, kept,
        )
        files[cases_path] = kept
    # without court data on this PC (the standalone package collects none) the
    # court list that was shipped is kept as it is instead of being emptied
    files[ASSETS_DIR / "police_other.json"] = other
    files[ASSETS_DIR / "news_index.json"] = {"police": police, "court": court_index(kept)}
    print(f"{len(police)} police releases ({len(other)} of them not on the map), {len(kept)} court entries")
    return files


def main():
    write_all(build())


if __name__ == "__main__":
    main()
