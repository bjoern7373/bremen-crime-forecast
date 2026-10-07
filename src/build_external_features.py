"""
Fetch weather + calendar features for the date range covered by
data/processed/daily_panel.csv and merge them into one file:

    data/processed/external_features.csv   (one row per date, city-wide -- these
                                             features don't vary by Stadtteil)

Usage: python src/build_external_features.py
"""
from pathlib import Path

import pandas as pd

from weather import fetch_weather
from events_calendar import build_calendar

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"


def main():
    panel = pd.read_csv(PROCESSED_DIR / "daily_panel.csv", parse_dates=["date"])
    start, end = panel["date"].min().date(), panel["date"].max().date()
    print(f"Building external features for {start} .. {end}")

    weather = fetch_weather(start, end)
    print(f"  weather: {len(weather)} days")

    calendar = build_calendar(start, end)
    print(f"  calendar: {len(calendar)} days, "
          f"{calendar['is_werder_heimspiel'].sum()} Werder home games, "
          f"{calendar['festival_name'].notna().sum()} festival days")

    calendar["date"] = pd.to_datetime(calendar["date"]).dt.date
    weather["date"] = pd.to_datetime(weather["date"]).dt.date

    merged = calendar.merge(weather, on="date", how="left")
    out_path = PROCESSED_DIR / "external_features.csv"
    merged.to_csv(out_path, index=False, encoding="utf-8")
    print(f"Wrote {len(merged)} rows to {out_path}")


if __name__ == "__main__":
    main()
