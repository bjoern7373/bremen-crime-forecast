"""
Daily weather for Bremen (city centre coords) via Open-Meteo -- free, no API
key, no attribution required for non-commercial research use.

Uses the historical "archive" API for anything far enough in the past, and
the forecast API's `past_days` feature (covers the last ~3 months plus
future forecast days) to fill the gap the archive hasn't caught up to yet.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

LAT, LON = 53.0793, 8.8017  # Bremen Marktplatz
DAILY_VARS = "temperature_2m_max,temperature_2m_min,precipitation_sum,windspeed_10m_max,sunset"

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


def _to_frame(payload: dict) -> pd.DataFrame:
    daily = payload["daily"]
    df = pd.DataFrame({
        "date": pd.to_datetime(daily["time"]).date,
        "temp_max_c": daily["temperature_2m_max"],
        "temp_min_c": daily["temperature_2m_min"],
        "precip_mm": daily["precipitation_sum"],
        "wind_max_kmh": daily["windspeed_10m_max"],
        "sunset": daily["sunset"],
    })
    df["sunset_hour"] = pd.to_datetime(df["sunset"]).dt.hour + pd.to_datetime(df["sunset"]).dt.minute / 60
    return df.drop(columns=["sunset"])


def fetch_weather(start: date, end: date) -> pd.DataFrame:
    """Daily weather for [start, end] inclusive, stitching archive + forecast APIs."""
    archive_cutoff = date.today() - timedelta(days=5)  # archive API usually lags ~2-5 days
    frames = []

    if start <= min(end, archive_cutoff):
        resp = requests.get(ARCHIVE_URL, params={
            "latitude": LAT, "longitude": LON,
            "start_date": start.isoformat(),
            "end_date": min(end, archive_cutoff).isoformat(),
            "daily": DAILY_VARS, "timezone": "Europe/Berlin",
        }, timeout=30)
        resp.raise_for_status()
        frames.append(_to_frame(resp.json()))

    if end > archive_cutoff:
        gap_days = (date.today() - (archive_cutoff + timedelta(days=1))).days
        resp = requests.get(FORECAST_URL, params={
            "latitude": LAT, "longitude": LON,
            "daily": DAILY_VARS, "timezone": "Europe/Berlin",
            "past_days": max(gap_days, 0),
            "forecast_days": max((end - date.today()).days + 1, 1),
        }, timeout=30)
        resp.raise_for_status()
        frames.append(_to_frame(resp.json()))

    out = pd.concat(frames, ignore_index=True).drop_duplicates("date")
    out = out[(out["date"] >= start) & (out["date"] <= end)]
    return out.sort_values("date").reset_index(drop=True)
