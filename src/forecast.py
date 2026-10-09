"""
Forward-looking hotspot forecast, using exactly the model(s) validated in
src/backtest.py (same math, same defaults: k=5, horizon=7, half_life=7) --
but instead of rolling through history to score past performance, this
scores *today* using ALL available history, to flag which Stadtteile are
currently elevated-risk for the map's red "Prognose"-halo.

This is NOT a new/different model -- it's the same exp_decay + dow_seasonal
signals backtest.py already showed beat random guessing by ~30-40% (PAI
~1.3-1.4), just run once more on the freshest data instead of on a past day.

Usage:
    python src/forecast.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from backtest import load_panel, pivot_target, score_exp_decay, score_dow_seasonal

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
CENTROIDS_PATH = ROOT / "data" / "geocode_cache" / "district_centroids.json"

TARGET = "n_total"
K = 5
HORIZON = 7
HALF_LIFE = 7.0


def normalize(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-9:
        return pd.Series(0.0, index=s.index)
    return (s - lo) / (hi - lo)


def main():
    panel = load_panel()
    matrix = pivot_target(panel, TARGET)
    last_date = matrix.index.max()
    forecast_for = last_date + pd.Timedelta(days=1)

    exp_scores = score_exp_decay(matrix, HORIZON, half_life=HALF_LIFE)
    dow_scores = score_dow_seasonal(matrix, HORIZON, current_dow=forecast_for.dayofweek)
    ensemble = (normalize(exp_scores) + normalize(dow_scores)) / 2.0
    ensemble = ensemble.sort_values(ascending=False)

    centroids = json.loads(CENTROIDS_PATH.read_text(encoding="utf-8"))

    districts = []
    for rank, (stadtteil, score) in enumerate(ensemble.items(), start=1):
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
        })

    out = {
        "generated_for_date": forecast_for.strftime("%Y-%m-%d"),
        "horizon_days": HORIZON,
        "k": K,
        "model_note": (
            "Ensemble aus zwei in src/backtest.py getesteten Modellen "
            "(exponentiell abklingende Tatenhäufigkeit + Wochentags-Muster), "
            "gemittelt. Im Backtest ~30-40% besser als Zufallsraten (PAI ~1.3-1.4). "
            "Statistische Tendenz, keine Vorhersage einzelner Taten."
        ),
        "districts": districts,
    }

    out_path = PROCESSED_DIR / "forecast.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Forecast for {out['generated_for_date']} (horizon {HORIZON}d, k={K}):")
    for d in districts[:K]:
        print(f"  #{d['rank']} {d['stadtteil']:20s} score={d['score']:.3f}")
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
