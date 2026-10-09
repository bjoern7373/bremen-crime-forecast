"""
Heuristically link Landgericht Bremen court releases (src/scrape_courts.py's
output) back to Polizei Bremen incident reports, as an approximate
"Aufklaerung" (case-outcome) signal -- NOT an official clearance-rate
statistic. See README "Gerichtsurteile" section for the method and its
limits: defendants are never named in these releases, so matching relies on
offense category + approximate crime date (+ district, when mentioned) --
coincidences across similar nearby incidents are possible, confidence is
therefore capped and low-confidence matches are dropped rather than shown.

Usage:
    python src/match_verdicts.py
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from districts import KNOWN_STADTTEILE, _ORTSTEIL_ALIASES

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

# categories too generic/keyword-poor to match reliably -- matching these
# would mostly produce coincidental, misleading links
EXCLUDED_CATEGORIES = {"Sonstiges", "Verkehrsunfall"}

DATE_WINDOW_PAD_DAYS = 10  # tolerance around the extracted Tatzeit window
MAX_CANDIDATES_FOR_MEDIUM_CONFIDENCE = 4  # more than this = too generic, drop

_MONTHS = {
    "januar": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}

_JS_RE = re.compile(r"Js\s*(\d+)\s*/\s*(\d{2,4})")
_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_MONTHYEAR_RE = re.compile(
    r"\b(?:[Ii]m|seit|vor|zwischen)\s+([A-Za-zäöüÄÖÜ]+)\s+(\d{4})\b"
)
_AGE_RE = re.compile(r"(\d{2})\s*-?\s*j[äa]hrige[nr]?\b", re.IGNORECASE)
_PM_BACKREF_RE = re.compile(r"PM\s*(\d+)\s*/\s*(\d{2,4})", re.IGNORECASE)
_TATVORWURF_RE = re.compile(r"Tatvorwurf\s*:\s*(.{0,600})", re.DOTALL)
_STREET_RE = re.compile(
    r"\b((?:[A-ZÄÖÜ][a-zäöüß]+\s+)?[A-ZÄÖÜ][A-Za-zäöüßÄÖÜ-]*"
    r"(?:straße|strasse|weg|platz|allee|damm|ring|chaussee|wall))\b"
)


def extract_street_tokens(text: str) -> set[str]:
    """Normalized (lowercased) street-name-like tokens, e.g. 'Buntentorsteinweg'
    or 'Vegesacker Straße' -> 'vegesackerstraße'. Used as the primary --
    and only trustworthy -- signal for linking a court case to a specific
    incident: offense category + a same-ish date alone produced false matches
    in testing (two unrelated attacks on the same day, same category, were
    conflated), but an exact shared street name is specific enough to avoid
    that."""
    return {re.sub(r"[^a-zäöüß]", "", t.lower()) for t in _STREET_RE.findall(text)}
_SENTENCE_RE = re.compile(
    r"(lebenslange[n]?\s+(?:Gesamt)?Freiheitsstrafe"
    r"|Freiheitsstrafe[n]?\s+von\s+\d+\s*Jahren?(?:\s*und\s*\d+\s*Monaten?)?"
    r"|Jugendstrafe\s+von\s+\d+\s*Jahren?(?:\s*und\s*\d+\s*Monaten?)?"
    r"|Freispruch|freigesprochen)",
    re.IGNORECASE,
)


def normalize_js_year(yy: str) -> int:
    return int(yy) if len(yy) == 4 else 2000 + int(yy)


def extract_js_key(text: str) -> str | None:
    m = _JS_RE.search(text)
    if not m:
        return None
    return f"{m.group(1)}/{normalize_js_year(m.group(2))}"


def extract_pm_backrefs(text: str) -> set[str]:
    return {f"{n}/{2000 + int(yy) if len(yy) == 2 else int(yy)}" for n, yy in _PM_BACKREF_RE.findall(text)}


def extract_tat_events(text: str) -> list[dict]:
    """Returns one entry per distinct date mentioned in the Tatvorwurf
    paragraph, each with ONLY the street names found near THAT date.

    Many Landgericht indictments are multi-count ("u.a.") and describe
    several separate acts by the same defendant(s) on different dates and
    at different locations within one PM. Pooling all dates into a single
    min..max window (an earlier version of this script did that) produced a
    years-wide 'Tatzeit window' that then matched a same-street, same-category
    but otherwise unrelated incident from a completely different year --
    confirmed as a real false positive during testing. Keeping each date's
    local context (+/-250 chars) separate avoids that: an event's street
    tokens can only come from text actually describing that event.
    """
    m = _TATVORWURF_RE.search(text)
    blob = m.group(1) if m else text[:1200]

    events = []
    for dm in _DATE_RE.finditer(blob):
        dd, mm, yyyy = dm.groups()
        try:
            date = f"{yyyy}-{int(mm):02d}-{int(dd):02d}"
        except ValueError:
            continue
        local = blob[max(0, dm.start() - 250):dm.end() + 250]
        events.append({"date_min": date, "date_max": date, "streets": extract_street_tokens(local)})
    if events:
        return events

    m2 = _MONTHYEAR_RE.search(blob)
    if m2:
        month = _MONTHS.get(m2.group(1).lower())
        year = m2.group(2)
        if month:
            import calendar
            last_day = calendar.monthrange(int(year), month)[1]
            return [{"date_min": f"{year}-{month:02d}-01", "date_max": f"{year}-{month:02d}-{last_day:02d}",
                     "streets": extract_street_tokens(blob)}]
    return []


def extract_district(text: str) -> str | None:
    for name in KNOWN_STADTTEILE:
        if re.search(r"\b" + re.escape(name) + r"\b", text):
            return name
    for alias, canonical in _ORTSTEIL_ALIASES.items():
        if alias.lower() in text.lower():
            return canonical
    return None


def classify_outcome(title: str, text: str) -> tuple[str, str | None, str | None]:
    """Returns (stage, outcome_summary, outcome_date). stage is one of:
    auftakt, anklage, urteilstermin (verdict date announced but not yet
    delivered), urteil (verdict actually delivered), rechtskraft, sonstiges."""
    t = title.lower()
    verdict_delivered = bool(re.search(r"\bverurteilt\b|\bfreigesprochen\b", text, re.IGNORECASE))

    sentence_match = _SENTENCE_RE.search(text)
    outcome_summary = re.sub(r"\s+", " ", sentence_match.group(0)).strip() if sentence_match else None

    urteil_date = None
    m = re.search(r"Urteil(?:s)?\s+vom\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if m:
        urteil_date = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

    if "rechtskraft" in t:
        return "rechtskraft", outcome_summary, urteil_date
    if "urteil" in t:
        if verdict_delivered:
            return "urteil", outcome_summary, urteil_date
        return "urteilstermin", None, None
    if "auftakt" in t or "fortsetzung" in t:
        return "auftakt", None, None
    if "anklage" in t:
        return "anklage", None, None
    return "sonstiges", outcome_summary, urteil_date


_STAGE_RANK = {"anklage": 0, "auftakt": 1, "urteilstermin": 2, "urteil": 3, "rechtskraft": 4, "sonstiges": -1}


def build_cases(releases: list[dict]) -> list[dict]:
    by_js: dict[str, list[dict]] = defaultdict(list)
    for r in releases:
        js_key = extract_js_key(r["text"])
        if not js_key:
            continue  # no criminal file number found -- likely a civil case or calendar, skip
        r["_js_key"] = js_key
        by_js[js_key].append(r)

    cases = []
    for js_key, group in by_js.items():
        group.sort(key=lambda r: r.get("publish_date") or "")
        anchor = None
        for r in group:
            if _TATVORWURF_RE.search(r["text"]):
                anchor = r
                break
        if anchor is None:
            anchor = group[0]

        outcome_stage, outcome_summary, outcome_date = "auftakt", None, None
        best_rank = -1
        for r in group:
            stage, summary, odate = classify_outcome(r["title"], r["text"])
            rank = _STAGE_RANK.get(stage, -1)
            if rank > best_rank:
                best_rank = rank
                outcome_stage = stage
                if summary:
                    outcome_summary = summary
                if odate:
                    outcome_date = odate

        category = anchor["category"]
        if category in EXCLUDED_CATEGORIES:
            continue
        events = extract_tat_events(anchor["text"])
        if not events:
            continue  # no usable date -- can't responsibly match this to an incident

        district = extract_district(anchor["text"][:800])
        is_bremerhaven_case = "bremerhaven" in anchor["text"][:800].lower()

        cases.append({
            "js_key": js_key,
            "category": category,
            "events": events,
            "district": district,
            "is_bremerhaven_case": is_bremerhaven_case,
            "age": (_AGE_RE.search(anchor["text"][:800]).group(1) if _AGE_RE.search(anchor["text"][:800]) else None),
            "outcome_stage": outcome_stage,
            "outcome_summary": outcome_summary,
            "outcome_date": outcome_date,
            "releases": [{"pm_id": r["pm_id"], "title": r["title"], "stage": r["stage"],
                          "publish_date": r["publish_date"], "pdf_url": r["pdf_url"]} for r in group],
        })
    return cases


def match_cases_to_incidents(cases: list[dict], incidents: pd.DataFrame) -> list[dict]:
    """Deliberately conservative: category + a same-ish date alone is NOT
    enough to attribute a real criminal case to a specific incident -- tested
    against real data, that produced a confident-looking but wrong match
    (two unrelated violent incidents on the same day, same category, picked
    the wrong one). Only a shared, specific street name is treated as
    evidence; a shared Stadtteil alone is kept as a much weaker secondary
    signal and only surfaced when it narrows to very few candidates.
    Anything weaker is dropped rather than shown -- no match is better than
    a wrong one here."""
    matches = []
    for case in cases:
        if case["is_bremerhaven_case"]:
            continue  # our map covers Bremen city's 23 Stadtteile, not Bremerhaven

        for event in case["events"]:
            lo = pd.Timestamp(event["date_min"]) - pd.Timedelta(days=DATE_WINDOW_PAD_DAYS)
            hi = pd.Timestamp(event["date_max"]) + pd.Timedelta(days=DATE_WINDOW_PAD_DAYS)
            cand = incidents[
                (incidents["category"] == case["category"])
                & (~incidents["is_bremerhaven"].astype(bool))
                & (incidents["incident_date"] >= lo)
                & (incidents["incident_date"] <= hi)
            ]
            if len(cand) == 0:
                continue

            event_streets = event["streets"]
            street_matched_ids = []
            if event_streets:
                for _, row in cand.iterrows():
                    incident_streets = extract_street_tokens(str(row.get("ort_raw") or ""))
                    if event_streets & incident_streets:
                        street_matched_ids.append(int(row["pm_id"]))

            if street_matched_ids:
                confidence = "strasse"
                matched_ids = street_matched_ids
            elif case["district"]:
                district_cand = cand[cand["stadtteil"] == case["district"]]
                if 0 < len(district_cand) <= MAX_CANDIDATES_FOR_MEDIUM_CONFIDENCE:
                    confidence = "stadtteil"
                    matched_ids = [int(x) for x in district_cand["pm_id"].tolist()]
                else:
                    continue
            else:
                continue  # category + date only, no location signal at all -- too unreliable, don't surface

            matches.append({
                "js_key": case["js_key"],
                "confidence": confidence,
                "category": case["category"],
                "tat_date_min": event["date_min"],
                "tat_date_max": event["date_max"],
                "district": case["district"],
                "outcome_stage": case["outcome_stage"],
                "outcome_summary": case["outcome_summary"],
                "outcome_date": case["outcome_date"],
                "releases": case["releases"],
                "matched_pm_ids": matched_ids,
            })
    return matches


def main():
    releases_path = PROCESSED_DIR / "court_releases.jsonl"
    if not releases_path.exists():
        print("No court_releases.jsonl found -- run src/scrape_courts.py first.")
        return
    releases = [json.loads(line) for line in releases_path.open(encoding="utf-8")]

    incidents = pd.read_csv(ROOT / "data" / "processed" / "incidents_geo.csv", parse_dates=["incident_date"])

    cases = build_cases(releases)
    print(f"{len(releases)} releases -> {len(cases)} cases with a usable Tatvorwurf date+category")

    matches = match_cases_to_incidents(cases, incidents)
    print(f"{len(matches)} cases matched to at least one incident (hoch/mittel confidence)")
    for m in matches[:10]:
        print(f"  [{m['confidence']}] {m['category']} {m['tat_date_min']}..{m['tat_date_max']} "
              f"-> {len(m['matched_pm_ids'])} incident(s), outcome: {m['outcome_stage']} {m['outcome_summary'] or ''}")

    out_path = PROCESSED_DIR / "court_matches.json"
    out_path.write_text(json.dumps(matches, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")

    from collections import Counter
    stage_counts = Counter(c["outcome_stage"] for c in cases)
    category_counts = Counter(c["category"] for c in cases)
    stats = {
        "source": "Landgericht Bremen",
        "n_cases_since_2024": len(cases),
        "n_matched_to_incidents": len(matches),
        "outcome_stage_counts": dict(stage_counts),
        "category_counts": dict(category_counts),
        "note": (
            "Nur Landgericht Bremen (schwere Delikte); Amtsgericht/Staatsanwaltschaft "
            "haben kein nutzbares Archiv (siehe README). Angeklagte werden in den "
            "Pressemitteilungen nie namentlich genannt, daher nur sehr wenige "
            "Vorfall-Zuordnungen möglich/verlässlich (siehe n_matched_to_incidents)."
        ),
    }
    stats_path = PROCESSED_DIR / "court_stats.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {stats_path}: {len(cases)} cases, {stage_counts}")


if __name__ == "__main__":
    main()
