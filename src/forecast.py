"""
What the map's "Prognose" shows, computed from all the history we hold:

  - which Stadtteile to mark (red halo): the K with the highest score of the
    hotspot model in src/backtest.py (same math, same defaults);
  - per Stadtteil, when the next report is to be expected, overall and for
    the most likely offense types (src/timing.py);
  - how both did when tested against the past, so the map can say so next to
    every statement instead of asking to be believed.

Usage:
    python src/forecast.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

import timing
from backtest import (MODELS, NEEDS_WEEKDAY, load_panel, observed_days, pivot_target,
                      run_backtest, summarize)

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
CENTROIDS_PATH = ROOT / "data" / "geocode_cache" / "district_centroids.json"

TARGET = "n_total"
K = 5
HORIZON = 7
MODEL = "ensemble"       # the score on the map, see backtest.MODELS
BENCHMARK = "long_run"   # "always the busiest Stadtteile" -- what MODEL has to beat


def hotspot_check(matrix: pd.DataFrame) -> dict:
    """How the marking would have done in the past, next to random marking
    and to always marking the busiest Stadtteile."""
    observed = observed_days(matrix.index)
    model = summarize(run_backtest(matrix, MODEL, K, HORIZON, observed=observed), MODEL)
    bench = summarize(run_backtest(matrix, BENCHMARK, K, HORIZON, observed=observed), BENCHMARK)
    return {
        "n_test_days": model["n_test_days"],
        "hit_rate": model["precision@k"],
        "random": model["random"],
        "always_busiest": bench["precision@k"],
    }


def model_note(check: dict) -> str:
    pct = lambda x: f"{round(100 * x)} %"
    note = (
        f"Markiert sind die {K} Stadtteile mit dem höchsten Wert aus jüngster Häufung und "
        f"Wochentags-Muster. Rückblick-Test über {check['n_test_days']} Tage: In {pct(check['hit_rate'])} "
        f"der Fälle gab es im markierten Stadtteil binnen {HORIZON} Tagen tatsächlich eine Meldung "
        f"(bei zufälliger Auswahl: {pct(check['random'])}). "
    )
    if check["hit_rate"] > check["always_busiest"] + 0.02:
        note += (f"Wer stattdessen immer die {K} Stadtteile mit den meisten Meldungen markiert, "
                 f"kommt auf {pct(check['always_busiest'])}. ")
    else:
        note += (f"Wer einfach immer die {K} Stadtteile mit den meisten Meldungen markiert, kommt "
                 f"allerdings auf {pct(check['always_busiest'])}: Die Prognose zeigt bisher vor allem, "
                 f"WO dauerhaft viel gemeldet wird – ein zeitliches Muster darüber hinaus ist noch "
                 f"nicht nachweisbar. ")
    return note + "Statistische Tendenz, keine Vorhersage einzelner Taten."


def marked_then():
    """Returns a function day -> the Stadtteile the map would have marked on
    that day, scored exactly as main() does it, from the reports that had
    been published before that day. The time-statement check needs it: the
    statements are shown for the marked Stadtteile, so they are checked for
    those."""
    inc = pd.read_csv(PROCESSED_DIR / "incidents.csv")
    inc = inc[inc["stadtteil"].isin(timing.KNOWN_STADTTEILE) & ~inc["is_bremerhaven"].astype(bool)]
    happened = pd.to_datetime(inc["incident_date"], errors="coerce")
    published = pd.to_datetime(inc["pub"], errors="coerce").fillna(happened)
    known = happened.notna().to_numpy()
    happened, published = happened[known].to_numpy(), published[known].to_numpy()
    districts = sorted(timing.KNOWN_STADTTEILE)
    column = inc["stadtteil"][known].map({name: i for i, name in enumerate(districts)}).to_numpy()

    def marked(day: pd.Timestamp):
        before = published < np.datetime64(day)
        if not before.any():
            return None
        first = happened[before].min()
        days = pd.date_range(first, day - pd.Timedelta(days=1), freq="D")
        counts = np.zeros((len(days), len(districts)))
        np.add.at(counts, ((happened[before] - first) // np.timedelta64(1, "D"), column[before]), 1)
        history = pd.DataFrame(counts, index=days, columns=districts)
        weekday = {"current_dow": day.dayofweek} if MODEL in NEEDS_WEEKDAY else {}
        return set(MODELS[MODEL](history, HORIZON, **weekday).nlargest(K).index)
    return marked


def timing_block(grid: timing.Grid, as_of: pd.Timestamp) -> dict:
    check = timing.backtest(grid, marked=marked_then())
    return {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "levels": [int(q * 100) for q in timing.QUANTILES],
        "max_window_days": timing.MAX_WINDOW_DAYS,
        # How often the time windows of each level came true in the past --
        # measured on the kind of statement the map shows (busiest Stadtteile,
        # their most frequent offense types), not on the average of all pairs.
        "check": {kind: {str(int(q * 100)): check["windows"][q]["shown_" + kind] for q in timing.QUANTILES
                         if q in check["windows"]}
                  for kind in ("offense", "any")},
        "coverage": timing.coverage_summary(grid),
    }


def main():
    panel = load_panel()
    matrix = pivot_target(panel, TARGET)
    grid = timing.build_grid(*timing.load_inputs())
    # The forecast is for the day after the newest press release we hold --
    # not after the newest incident, which is usually a day older. Days up to
    # then on which nothing was reported count as what they are: quiet so far.
    forecast_for = max(matrix.index.max(), grid.days[-1]) + pd.Timedelta(days=1)
    matrix = matrix.reindex(pd.date_range(matrix.index.min(), forecast_for - pd.Timedelta(days=1), freq="D"),
                            fill_value=0)

    weekday = {"current_dow": forecast_for.dayofweek} if MODEL in NEEDS_WEEKDAY else {}
    scores = MODELS[MODEL](matrix, HORIZON, **weekday).sort_values(ascending=False)

    windows = timing.current_windows(grid)
    centroids = json.loads(CENTROIDS_PATH.read_text(encoding="utf-8"))

    districts = []
    for rank, (stadtteil, score) in enumerate(scores.items(), start=1):
        latlon = centroids.get(stadtteil)
        if latlon is None:
            continue  # no known centroid (shouldn't happen once geocode.py has run) -- skip rather than guess
        districts.append({
            "stadtteil": stadtteil,
            "lat": round(latlon[0], 5),
            "lon": round(latlon[1], 5),
            "score": round(float(score), 3),
            "rank": rank,
            "elevated": rank <= K,
            "timing": windows.get(stadtteil),
        })

    check = hotspot_check(matrix)
    out = {
        "generated_for_date": forecast_for.strftime("%Y-%m-%d"),
        "horizon_days": HORIZON,
        "k": K,
        "model_note": model_note(check),
        "hotspot_check": check,
        "timing": timing_block(grid, forecast_for),
        "districts": districts,
    }

    out_path = PROCESSED_DIR / "forecast.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Forecast for {out['generated_for_date']} (horizon {HORIZON}d, k={K}):")
    for d in districts[:K]:
        w = d["timing"]
        nxt = w["offenses"][0] if w and w["offenses"] else None
        print(f"  #{d['rank']} {d['stadtteil']:20s} score={d['score']:.3f}  "
              f"next report within {w['any']['t50']}-{w['any']['t80']} days"
              + (f", most likely {nxt['category']} ({nxt['t50']}-{nxt['t80']} days)" if nxt else ""))
    print(f"Hotspot check: hit rate {check['hit_rate']}, random {check['random']}, "
          f"always the busiest {check['always_busiest']} ({check['n_test_days']} test days)")
    print(f"Time-window check: {json.dumps(out['timing']['check'])}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
