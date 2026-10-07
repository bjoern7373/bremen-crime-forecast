"""
Turn data/processed/releases.jsonl (raw parsed press releases) into:

  data/processed/incidents.csv
      One row per (press release x district) with category, used for audit
      /exploration.

  data/processed/daily_panel.csv
      date x Stadtteil panel with per-category incident counts, zero-filled
      for days/districts with no reported incidents. This is the modeling
      table for backtest.py.

Usage: python src/build_dataset.py
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from classify import classify
from districts import extract_stadtteile, canonicalize_stadtteil, is_bremerhaven, KNOWN_STADTTEILE

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})")


def _parse_one(source: str) -> date | None:
    m = _DATE_RE.search(source)
    if not m:
        return None
    dd, mm, yy_str = m.groups()
    if len(yy_str) not in (2, 4):
        return None  # malformed year in source text (e.g. a typo'd "206")
    yy = int(yy_str)
    if len(yy_str) == 2:
        yy += 2000
    if not (2020 <= yy <= 2030):
        return None  # sanity bound, catches further typo'd years
    try:
        return date(yy, int(mm), int(dd))
    except ValueError:
        return None


def parse_incident_date(zeit_raw: str | None, publish_date: str | None) -> date | None:
    """Best-effort extraction of the actual incident date from the free-text
    'Zeit:' field (formats vary a lot: '20.02.26, 13.30 Uhr', '3.7.26, 15 bis
    18.30 Uhr', 'November 2022', ...).

    A handful of releases reference a much older case being resolved now
    (cold cases, wanted-poster follow-ups) -- those dates are real but not
    "new daily crime" for hotspot modeling purposes, so if the parsed date is
    more than 90 days from the publish date we treat it as a backstory
    reference and use the publish date instead. Falls back to the publish
    date if nothing parseable is found at all.
    """
    pub = _parse_one(publish_date) if publish_date else None
    tat = _parse_one(zeit_raw) if zeit_raw else None

    if tat is not None:
        if pub is None or abs((tat - pub).days) <= 90:
            return tat
    return pub


def build_incidents(releases: list[dict]) -> pd.DataFrame:
    rows = []
    for r in releases:
        category = classify(r["title"], r["text"])
        incident_date = parse_incident_date(r.get("zeit_raw"), r.get("publish_date"))
        raw_districts = extract_stadtteile(r.get("ort_raw") or "")
        districts = []
        for raw_d in raw_districts:
            for canon in canonicalize_stadtteil(raw_d):
                if canon not in districts:
                    districts.append(canon)
        bhv = is_bremerhaven(r.get("ort_raw") or "")

        if not districts:
            # Keep a single row with district=None so nothing is silently
            # dropped from the audit CSV (city-wide announcements, Bremerhaven
            # incidents, or Ort text we failed to parse).
            rows.append({
                "pm_id": r["pm_id"], "incident_date": incident_date,
                "publish_date": r.get("publish_date"), "stadtteil": None,
                "is_bremerhaven": bhv, "category": category,
                "title": r["title"], "ort_raw": r.get("ort_raw"),
                "source_url": r["source_url"], "text": r.get("text", ""),
            })
        else:
            for d in districts:
                rows.append({
                    "pm_id": r["pm_id"], "incident_date": incident_date,
                    "publish_date": r.get("publish_date"), "stadtteil": d,
                    "is_bremerhaven": bhv, "category": category,
                    "title": r["title"], "ort_raw": r.get("ort_raw"),
                    "source_url": r["source_url"], "text": r.get("text", ""),
                })
    return pd.DataFrame(rows)


def build_daily_panel(incidents: pd.DataFrame) -> pd.DataFrame:
    df = incidents.dropna(subset=["incident_date", "stadtteil"])
    df = df[~df["is_bremerhaven"]]
    df = df[df["stadtteil"].isin(KNOWN_STADTTEILE)]

    full_dates = pd.date_range(df["incident_date"].min(), df["incident_date"].max(), freq="D")
    districts = sorted(KNOWN_STADTTEILE)
    index = pd.MultiIndex.from_product([full_dates, districts], names=["date", "stadtteil"])

    total_counts = (
        df.groupby(["incident_date", "stadtteil"]).size()
        .reindex(index, fill_value=0)
        .rename("n_total")
    )
    panel = total_counts.to_frame()

    for category in sorted(df["category"].unique()):
        col = f"n_{category}"
        cat_counts = (
            df[df["category"] == category]
            .groupby(["incident_date", "stadtteil"]).size()
            .reindex(index, fill_value=0)
        )
        panel[col] = cat_counts

    panel = panel.reset_index()
    panel["date"] = panel["date"].dt.date
    return panel


def main():
    releases = [json.loads(l) for l in
                (PROCESSED_DIR / "releases.jsonl").read_text(encoding="utf-8").splitlines()]

    incidents = build_incidents(releases)
    incidents.to_csv(PROCESSED_DIR / "incidents.csv", index=False, encoding="utf-8")
    print(f"Wrote {len(incidents)} incident rows to data/processed/incidents.csv")

    unresolved = incidents["stadtteil"].isna().sum()
    print(f"  of which {unresolved} rows have no resolved Stadtteil (city-wide notices, "
          f"Bremerhaven, or unparsed Ort field)")

    panel = build_daily_panel(incidents)
    panel.to_csv(PROCESSED_DIR / "daily_panel.csv", index=False, encoding="utf-8")
    print(f"Wrote daily panel: {panel['date'].nunique()} days x "
          f"{panel['stadtteil'].nunique()} Stadtteile to data/processed/daily_panel.csv")
    print("\nCategory totals:")
    print(incidents["category"].value_counts().to_string())


if __name__ == "__main__":
    main()
