"""
Compact JSON export of geocoded incidents for the map artifact.
Usage: python src/export_map_data.py
"""
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

# Fold the 14 fine-grained classify.py categories into 8 colored headline
# categories + "Sonstiges" (grey catch-all) -- see src/classify.py for the
# source categories. Keys here must match classify.py's category strings.
CATEGORY_MAP = {
    "Raub": "Raub",
    "Einbruch": "Einbruch",
    "Diebstahl": "Diebstahl",
    "Drogendelikt": "Drogendelikt",
    "Koerperverletzung": "Koerperverletzung",
    "Waffendelikt": "Waffendelikt",
    "Toetungsdelikt": "Toetungsdelikt",
    "Sachbeschaedigung": "Sachbeschaedigung",
    # everything else folds into Sonstiges:
    "Verkehrsunfall": "Sonstiges",
    "Brandstiftung": "Sonstiges",
    "Hasskriminalitaet": "Sonstiges",
    "Fahrzeugaufbruch": "Sonstiges",
    "Sexualdelikt": "Sonstiges",
    "Sonstiges": "Sonstiges",
}


def load_series_by_pm_id() -> dict:
    """pm_id -> (series_id, series_size, series_category) for every incident
    that's part of a detected possible series (see src/find_series.py)."""
    series_path = PROCESSED_DIR / "series.json"
    if not series_path.exists():
        return {}
    series = json.loads(series_path.read_text(encoding="utf-8"))
    by_pm_id = {}
    for series_id, s in enumerate(series):
        for pm_id in s["pm_ids"]:
            by_pm_id[pm_id] = (series_id, s["n"], s["category"])
    return by_pm_id


def load_court_matches_by_pm_id() -> dict:
    """pm_id -> court match info, for the (very few, deliberately conservative
    -- see src/match_verdicts.py) incidents linked to a Landgericht Bremen
    court case. A pm_id can only end up here via a specific shared street
    name or Stadtteil, never from offense-category + date alone."""
    matches_path = PROCESSED_DIR / "court_matches.json"
    if not matches_path.exists():
        return {}
    matches = json.loads(matches_path.read_text(encoding="utf-8"))
    by_pm_id = {}
    for m in matches:
        latest_release = max(m["releases"], key=lambda r: r.get("publish_date") or "")
        info = {
            "confidence": m["confidence"],
            "category": m["category"],
            "outcome_stage": m["outcome_stage"],
            "outcome_summary": m["outcome_summary"],
            "outcome_date": m["outcome_date"],
            "pdf_url": latest_release["pdf_url"],
            "pdf_title": latest_release["title"],
        }
        for pm_id in m["matched_pm_ids"]:
            by_pm_id[pm_id] = info
    return by_pm_id


def load_external_features_by_date() -> dict:
    """date (YYYY-MM-DD str) -> dict of boolean 'extra criteria' flags, for
    the map's Serien-Explorer to optionally filter incidents by. Mirrors the
    group definitions in src/correlate.py."""
    ext_path = PROCESSED_DIR / "external_features.csv"
    if not ext_path.exists():
        return {}
    ext = pd.read_csv(ext_path)
    by_date = {}
    for _, row in ext.iterrows():
        by_date[row["date"]] = {
            "weekend": bool(row["is_weekend"]),
            "holiday": bool(row["is_public_holiday"]),
            "ferien": isinstance(row.get("school_holiday_name"), str),
            "festival": isinstance(row.get("festival_name"), str),
            "werder": bool(row["is_werder_heimspiel"]),
            "regen": bool(row["precip_mm"] > 1),
            "monatsanfang": bool(row["is_month_start_payday"]),
        }
    return by_date


def main():
    df = pd.read_csv(PROCESSED_DIR / "incidents_geo.csv")
    series_by_pm_id = load_series_by_pm_id()
    ext_by_date = load_external_features_by_date()
    court_by_pm_id = load_court_matches_by_pm_id()
    records = []
    for i, row in df.iterrows():
        series_info = series_by_pm_id.get(row["pm_id"])
        record = {
            "id": i,
            "lat": round(float(row["lat"]), 5),
            "lon": round(float(row["lon"]), 5),
            "prec": "a" if row["precision"] == "address" else "d",
            "cat": CATEGORY_MAP.get(row["category"], "Sonstiges"),
            "rawcat": row["category"],  # un-folded classify.py category, for
                                         # client-side series clustering (the
                                         # folded "cat" above would wrongly
                                         # merge e.g. Hasskriminalitaet and
                                         # Verkehrsunfall, both "Sonstiges")
            "date": row["incident_date"] if isinstance(row["incident_date"], str) else None,
            "title": row["title"],
            "stadtteil": row["stadtteil"],
            "ort": row["ort_raw"] if isinstance(row["ort_raw"], str) else "",
            "text": row["text"] if isinstance(row["text"], str) else "",
            "source_url": row["source_url"] if isinstance(row["source_url"], str) else "",
        }
        if series_info:
            record["series_id"], record["series_n"] = series_info[0], series_info[1]
        court_info = court_by_pm_id.get(row["pm_id"])
        if court_info:
            record["court_match"] = court_info
        if record["date"] and record["date"] in ext_by_date:
            record["x"] = ext_by_date[record["date"]]
        records.append(record)
    out_path = ROOT / "assets" / "map_data.json"
    out_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(records)} records to {out_path} "
          f"({out_path.stat().st_size / 1024:.0f} KB)")

    forecast_path = PROCESSED_DIR / "forecast.json"
    if forecast_path.exists():
        out_forecast = ROOT / "assets" / "forecast.json"
        out_forecast.write_text(forecast_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Copied forecast to {out_forecast}")

    stats_path = PROCESSED_DIR / "court_stats.json"
    if stats_path.exists():
        out_stats = ROOT / "assets" / "court_stats.json"
        out_stats.write_text(stats_path.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"Copied court_stats to {out_stats}")


if __name__ == "__main__":
    main()
