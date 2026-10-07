"""
Builds a date-indexed calendar of exogenous "something's going on" features:
public holidays, school holidays, Bremen festivals (Freimarkt/Osterwiese/
Weihnachtsmarkt/...), and SV Werder Bremen home games.

Festival and school-holiday dates below were looked up for 2025/2026 and
are NOT auto-updating -- extend the lists by hand each year (or replace with
a scraper of https://www.bremen-city.de/veranstaltungen if this grows to
matter). Public holidays and Werder fixtures ARE fetched live.
"""
from __future__ import annotations

from datetime import date, timedelta

import holidays
import pandas as pd
import requests

# --- Bremen festivals (Bürgerweide / Domshof) --------------------------------
# (name, start, end) -- look up and append new ones at https://www.bremen-city.de/veranstaltungen
FESTIVALS = [
    ("Osterwiese", date(2025, 4, 11), date(2025, 4, 27)),
    ("Freimarkt", date(2025, 10, 17), date(2025, 11, 2)),
    ("Weihnachtsmarkt", date(2025, 11, 24), date(2025, 12, 23)),
    ("Osterwiese", date(2026, 3, 27), date(2026, 4, 12)),
    ("Freimarkt", date(2026, 10, 16), date(2026, 11, 1)),
    ("Weihnachtsmarkt", date(2026, 11, 23), date(2026, 12, 23)),
]

# --- Bremen school holidays (Schulferien HB) ---------------------------------
SCHOOL_HOLIDAYS = [
    ("Osterferien", date(2025, 4, 7), date(2025, 4, 19)),
    ("Sommerferien", date(2025, 7, 3), date(2025, 8, 13)),
    ("Herbstferien", date(2025, 10, 13), date(2025, 10, 25)),
    ("Weihnachtsferien", date(2025, 12, 22), date(2026, 1, 5)),
    ("Winterferien", date(2026, 2, 2), date(2026, 2, 3)),
    ("Osterferien", date(2026, 3, 23), date(2026, 4, 7)),
    ("Sommerferien", date(2026, 7, 2), date(2026, 8, 12)),
    ("Herbstferien", date(2026, 10, 12), date(2026, 10, 24)),
]

WERDER_SEASONS = [2024, 2025]  # OpenLigaDB season label = year the season starts in
OPENLIGADB_URL = "https://api.openligadb.de/getmatchdata/bl1/{season}"


def fetch_werder_home_games() -> set[date]:
    home_dates: set[date] = set()
    for season in WERDER_SEASONS:
        try:
            resp = requests.get(OPENLIGADB_URL.format(season=season), timeout=30)
            resp.raise_for_status()
            matches = resp.json()
        except requests.exceptions.RequestException:
            continue
        for m in matches:
            team1 = m.get("team1", {}).get("teamName", "")
            if "Werder" in team1 and m.get("matchDateTime"):
                home_dates.add(pd.to_datetime(m["matchDateTime"]).date())
    return home_dates


def _in_any_range(d: date, ranges: list[tuple[str, date, date]]) -> str | None:
    for name, start, end in ranges:
        if start <= d <= end:
            return name
    return None


def build_calendar(start: date, end: date) -> pd.DataFrame:
    de_hb_holidays = holidays.Germany(subdiv="HB", years=range(start.year, end.year + 1))
    werder_home = fetch_werder_home_games()

    rows = []
    d = start
    while d <= end:
        rows.append({
            "date": d,
            "weekday": d.weekday(),  # 0=Monday
            "is_weekend": d.weekday() >= 5,
            "is_public_holiday": d in de_hb_holidays,
            "public_holiday_name": de_hb_holidays.get(d),
            "school_holiday_name": _in_any_range(d, SCHOOL_HOLIDAYS),
            "festival_name": _in_any_range(d, FESTIVALS),
            "is_werder_heimspiel": d in werder_home,
            "is_month_start_payday": d.day <= 3,  # Monatsersten + Nachlauf (Buergergeld/Rente/Gehalt)
        })
        d += timedelta(days=1)
    return pd.DataFrame(rows)
