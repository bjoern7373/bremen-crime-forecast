"""
Geocoding for the Bremen crime map:

  1. One-time centroid lookup for each of the 19 Stadtteile (cached forever --
     district boundaries don't move).
  2. Per-incident street-level geocoding when the press release names a
     specific street, using Nominatim (OpenStreetMap's own geocoder).

Respects Nominatim's usage policy: max 1 req/sec, identifying User-Agent,
and aggressive caching so re-runs (e.g. the daily scheduled task) only ever
geocode genuinely new addresses, never re-hit the API for ones we have.

Usage: python src/geocode.py
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
CACHE_DIR = ROOT / "data" / "geocode_cache"
DISTRICT_CACHE = CACHE_DIR / "district_centroids.json"
ADDRESS_CACHE = CACHE_DIR / "address_cache.json"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "bremen-crime-forecast-research/0.1 (personal non-commercial research project)"
RATE_LIMIT_S = 1.1

# left,top,right,bottom -- generously covers the city of Bremen but excludes
# Bremerhaven (~53.55N), which otherwise gets accidentally matched for short/
# generic names (e.g. "Haefen", "Mitte").
BREMEN_VIEWBOX = "8.45,53.22,9.05,52.95"


def _nominatim_query(q: str, max_retries: int = 4) -> tuple[float, float] | None:
    for attempt in range(max_retries):
        resp = requests.get(NOMINATIM_URL, params={
            "q": q, "format": "json", "limit": 1, "countrycodes": "de",
            "viewbox": BREMEN_VIEWBOX, "bounded": 1,
        }, headers={"User-Agent": USER_AGENT}, timeout=20)
        if resp.status_code == 429:
            wait = 10 * (attempt + 1)
            print(f"  Nominatim 429 (rate limited), waiting {wait}s before retry...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        results = resp.json()
        time.sleep(RATE_LIMIT_S)
        if not results:
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
    raise RuntimeError(f"Nominatim still rate-limited after {max_retries} retries for query: {q}")


def load_district_centroids() -> dict[str, tuple[float, float]]:
    from districts import KNOWN_STADTTEILE

    cache = {}
    if DISTRICT_CACHE.exists():
        cache = json.loads(DISTRICT_CACHE.read_text(encoding="utf-8"))

    missing = sorted(KNOWN_STADTTEILE - cache.keys())
    if missing:
        print(f"Geocoding {len(missing)} Stadtteil centroids (one-time)...")
        for name in missing:
            # Plain "<name>, Bremen" resolves to the actual Stadtteil/suburb
            # entity; "Bremen-<name>" sometimes instead matches the larger,
            # oddly-shaped administrative Stadtbezirk of the same name (e.g.
            # Bremen-Mitte the Bezirk vs. Mitte the Stadtteil), whose centroid
            # can land far from the real district. Try the precise form first.
            latlon = _nominatim_query(f"{name}, Bremen, Germany")
            if latlon is None:
                latlon = _nominatim_query(f"Bremen-{name}, Bremen, Germany")
            if latlon:
                cache[name] = latlon
                print(f"  {name}: {latlon}")
            else:
                print(f"  {name}: NOT FOUND")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        DISTRICT_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    return {k: tuple(v) for k, v in cache.items()}


_STREET_RE = re.compile(r"OT\s+[A-Za-zÄÖÜäöüß/\s.-]+?,\s*(.+)$")


def extract_street(ort_raw: str) -> str | None:
    """Pull the street-name part (after the last ', OT <Ortsteil>, ') from the
    'Ort:' field, if present. Returns None for district-only mentions."""
    if not ort_raw:
        return None
    m = _STREET_RE.search(ort_raw)
    if not m:
        return None
    street = m.group(1).strip()
    # guard against the street part itself being another district/city note
    if not street or street.lower() in ("bremen", "stadtgebiet"):
        return None
    return street


def geocode_addresses(incidents: pd.DataFrame) -> dict[str, tuple[float, float]]:
    cache = {}
    if ADDRESS_CACHE.exists():
        cache = json.loads(ADDRESS_CACHE.read_text(encoding="utf-8"))

    to_geocode = []
    for _, row in incidents.iterrows():
        ort_raw = row.get("ort_raw")
        street = extract_street(ort_raw) if isinstance(ort_raw, str) else None
        if not street:
            continue
        key = f"{street}, Bremen, Germany"
        if key not in cache:
            to_geocode.append(key)
    to_geocode = sorted(set(to_geocode))

    if to_geocode:
        print(f"Geocoding {len(to_geocode)} new street addresses...")
        for i, key in enumerate(to_geocode):
            latlon = _nominatim_query(key)
            cache[key] = list(latlon) if latlon else None
            if (i + 1) % 20 == 0:
                print(f"  {i+1}/{len(to_geocode)}")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        ADDRESS_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")

    return {k: tuple(v) for k, v in cache.items() if v}


def main():
    incidents = pd.read_csv(PROCESSED_DIR / "incidents.csv")
    district_centroids = load_district_centroids()
    address_coords = geocode_addresses(incidents)

    rows = []
    for _, row in incidents.iterrows():
        if row.get("is_bremerhaven") or pd.isna(row.get("stadtteil")):
            continue
        ort_raw = row.get("ort_raw")
        street = extract_street(ort_raw) if isinstance(ort_raw, str) else None
        key = f"{street}, Bremen, Germany" if street else None
        if key and key in address_coords:
            lat, lon = address_coords[key]
            precision = "address"
        elif row["stadtteil"] in district_centroids:
            lat, lon = district_centroids[row["stadtteil"]]
            precision = "district"
        else:
            continue
        rows.append({**row.to_dict(), "lat": lat, "lon": lon, "precision": precision})

    out = pd.DataFrame(rows)
    out_path = PROCESSED_DIR / "incidents_geo.csv"
    out.to_csv(out_path, index=False, encoding="utf-8")
    print(f"\nWrote {len(out)} geocoded incidents to {out_path}")
    print(out["precision"].value_counts().to_string())


if __name__ == "__main__":
    main()
