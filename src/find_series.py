"""
Spatio-temporal series detection: flags groups of incidents of the SAME
category that are close together in both space and time as a possible
series (crime-linkage-analysis style chaining -- two incidents are linked if
they're within max_distance_m and max_days of each other; linked incidents
transitively form one cluster, so a series can "walk" across the city over
time the way a real repeat offender's pattern does).

Only "address"-precision incidents are used -- a "district"-only incident's
coordinates are a jittered Stadtteil centroid, not a real location, and
would create false clusters just from sharing a neighborhood.

Usage: python src/find_series.py [--max-distance-m 1000] [--max-days 14] [--min-size 3]
"""
import argparse
import json
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

EARTH_RADIUS_M = 6_371_000


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def dedupe_followups(df: pd.DataFrame, same_event_radius_m: float = 30) -> pd.DataFrame:
    """Collapse multiple press releases about the SAME underlying incident
    (initial report, "Taeter gefasst" follow-up, a later correction, ...)
    into one row before series detection. Polizei Bremen reports these as
    separate press releases on the same date at the same address, which
    would otherwise inflate a single event into a fake 2-3-incident
    "series". Two rows count as the same event if they share a category,
    the same calendar date, AND are within same_event_radius_m of each
    other -- genuinely distinct incidents essentially never share both.
    """
    kept_idx = []
    for _, group in df.groupby(["category", "incident_date"]):
        idx = group.index.tolist()
        uf = UnionFind(len(idx))
        for a in range(len(idx)):
            for b in range(a + 1, len(idx)):
                ra, rb = group.loc[idx[a]], group.loc[idx[b]]
                if haversine_m(ra["lat"], ra["lon"], rb["lat"], rb["lon"]) <= same_event_radius_m:
                    uf.union(a, b)
        clusters = {}
        for a in range(len(idx)):
            clusters.setdefault(uf.find(a), []).append(idx[a])
        for members in clusters.values():
            kept_idx.append(members[0])  # first (lowest pm_id / earliest-scraped) survives
    return df.loc[sorted(kept_idx)].reset_index(drop=True)


def find_series(df: pd.DataFrame, max_distance_m: float, max_days: int, min_size: int) -> list[dict]:
    df = dedupe_followups(df)
    series = []
    for category, group in df.groupby("category"):
        group = group.sort_values("incident_date").reset_index(drop=True)
        n = len(group)
        if n < min_size:
            continue
        dates = pd.to_datetime(group["incident_date"])
        uf = UnionFind(n)

        for i in range(n):
            for j in range(i + 1, n):
                day_gap = abs((dates[j] - dates[i]).days)
                if day_gap > max_days:
                    # incidents are sorted by date, so once we're past the
                    # window for j, every later j is too -- stop early
                    break
                dist = haversine_m(group.loc[i, "lat"], group.loc[i, "lon"],
                                    group.loc[j, "lat"], group.loc[j, "lon"])
                if dist <= max_distance_m:
                    uf.union(i, j)

        clusters = {}
        for i in range(n):
            clusters.setdefault(uf.find(i), []).append(i)

        for members in clusters.values():
            if len(members) < min_size:
                continue
            rows = group.loc[members].sort_values("incident_date")
            lats, lons = rows["lat"].tolist(), rows["lon"].tolist()
            max_pairwise_dist = max(
                (haversine_m(lats[a], lons[a], lats[b], lons[b])
                 for a in range(len(lats)) for b in range(a + 1, len(lats))),
                default=0.0,
            )
            series.append({
                "category": category,
                "pm_ids": rows["pm_id"].tolist(),
                "n": len(rows),
                "date_start": rows["incident_date"].min(),
                "date_end": rows["incident_date"].max(),
                "span_days": (pd.to_datetime(rows["incident_date"].max())
                              - pd.to_datetime(rows["incident_date"].min())).days,
                "max_spread_m": round(max_pairwise_dist),
                "centroid_lat": round(rows["lat"].mean(), 5),
                "centroid_lon": round(rows["lon"].mean(), 5),
                "titles": rows["title"].tolist(),
                "stadtteile": sorted(rows["stadtteil"].dropna().unique().tolist()),
            })

    series.sort(key=lambda s: s["n"], reverse=True)
    return series


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-distance-m", type=float, default=1000)
    parser.add_argument("--max-days", type=int, default=14)
    parser.add_argument("--min-size", type=int, default=3)
    args = parser.parse_args()

    df = pd.read_csv(PROCESSED_DIR / "incidents_geo.csv")
    df = df[df["precision"] == "address"].copy()

    series = find_series(df, args.max_distance_m, args.max_days, args.min_size)

    out_path = PROCESSED_DIR / "series.json"
    out_path.write_text(json.dumps(series, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"{len(series)} moegliche Serien gefunden "
          f"(>= {args.min_size} Taten, <= {args.max_days} Tage auseinander, "
          f"<= {args.max_distance_m:.0f}m auseinander):\n")
    for s in series:
        print(f"  [{s['n']}x {s['category']}] {s['date_start']} .. {s['date_end']} "
              f"({s['span_days']} Tage), Streuung bis {s['max_spread_m']}m, "
              f"Stadtteile: {', '.join(s['stadtteile'])}")
        for t in s["titles"]:
            print(f"      - {t}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
