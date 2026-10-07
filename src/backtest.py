"""
Walk-forward hotspot backtest, in the spirit of a trading strategy backtest:

  - "signal" = a risk score per Stadtteil, computed only from data strictly
    before the prediction day (no look-ahead).
  - each day, rank Stadtteile by risk score and take the top-k as the
    "predicted hotspots" for the next `--horizon` days.
  - "hit" = an actual incident occurred in that Stadtteil within the horizon.
  - roll the origin forward one day at a time across the whole test period
    and aggregate hit rate, like an out-of-sample equity curve.

Metrics (standard in the predictive-policing literature):
  - Precision@k ("hit rate"): P(at least one incident | predicted hotspot)
  - PAI (Predictive Accuracy Index) = hit_rate / (k / n_districts), i.e. the
    lift over "flag k random districts". PAI = 1 means no better than chance.

Usage:
    python src/backtest.py --target n_total --k 5 --horizon 7
    python src/backtest.py --target n_Einbruch --k 3 --horizon 14
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PANEL_PATH = ROOT / "data" / "processed" / "daily_panel.csv"


def load_panel() -> pd.DataFrame:
    df = pd.read_csv(PANEL_PATH, parse_dates=["date"])
    return df.sort_values(["date", "stadtteil"])


def pivot_target(panel: pd.DataFrame, target: str) -> pd.DataFrame:
    """dates x districts matrix of daily counts for the given target column."""
    return panel.pivot(index="date", columns="stadtteil", values=target).fillna(0)


# ---- risk-score models -----------------------------------------------------
# Each takes the history matrix (dates x districts, STRICTLY before the
# current prediction date) and returns a pd.Series of risk scores per
# district (higher = riskier).

def score_naive_persistence(history: pd.DataFrame, horizon: int) -> pd.Series:
    """Risk = incident count in the most recent `horizon`-day window.
    The simplest possible 'near-repeat' signal: hot stays hot."""
    return history.tail(horizon).sum()


def score_exp_decay(history: pd.DataFrame, horizon: int, half_life: float = 7.0) -> pd.Series:
    """Risk = exponentially time-weighted sum of all past incidents, recent
    ones weighted more heavily. Decay chosen so weight halves every
    `half_life` days -- a simplified ETAS/near-repeat style kernel."""
    n = len(history)
    if n == 0:
        return pd.Series(0.0, index=history.columns)
    days_ago = np.arange(n - 1, -1, -1)  # most recent row -> 0
    weights = 0.5 ** (days_ago / half_life)
    return history.mul(weights, axis=0).sum()


def score_dow_seasonal(history: pd.DataFrame, horizon: int, current_dow: int) -> pd.Series:
    """Risk = historical average count on the same day-of-week, scaled to
    the horizon length. Captures 'Saturdays are busier' type patterns."""
    same_dow = history[history.index.dayofweek == current_dow]
    if same_dow.empty:
        return pd.Series(0.0, index=history.columns)
    return same_dow.mean() * horizon


MODELS = {
    "naive": score_naive_persistence,
    "exp_decay": score_exp_decay,
    "dow_seasonal": score_dow_seasonal,
}


def run_backtest(matrix: pd.DataFrame, model_name: str, k: int, horizon: int,
                  min_history: int = 21) -> pd.DataFrame:
    dates = matrix.index
    districts = matrix.columns
    n_districts = len(districts)
    model_fn = MODELS[model_name]

    records = []
    for i in range(min_history, len(dates) - horizon):
        today = dates[i]
        history = matrix.iloc[:i]  # strictly before today: no look-ahead
        future = matrix.iloc[i:i + horizon]

        if model_name == "dow_seasonal":
            scores = model_fn(history, horizon, current_dow=today.dayofweek)
        else:
            scores = model_fn(history, horizon)

        predicted_hot = scores.nlargest(k).index
        actually_hot = future.columns[(future.sum() > 0)]

        hits = len(set(predicted_hot) & set(actually_hot))
        records.append({
            "date": today,
            "n_predicted_hot": k,
            "n_actually_hot": len(actually_hot),
            "hits": hits,
            "hit_rate_in_topk": hits / k,
            "any_hit": hits > 0,
        })

    result = pd.DataFrame(records)
    result.attrs["n_districts"] = n_districts
    result.attrs["k"] = k
    return result


def summarize(result: pd.DataFrame, model_name: str) -> dict:
    k = result.attrs["k"]
    n_districts = result.attrs["n_districts"]
    precision_at_k = result["hit_rate_in_topk"].mean()
    chance_rate = k / n_districts
    pai = precision_at_k / chance_rate if chance_rate > 0 else float("nan")
    return {
        "model": model_name,
        "n_test_days": len(result),
        "precision@k": round(precision_at_k, 3),
        "chance_rate": round(chance_rate, 3),
        "PAI": round(pai, 2),
        "any_hit_rate": round(result["any_hit"].mean(), 3),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="n_total",
                        help="panel column to predict, e.g. n_total or n_Einbruch")
    parser.add_argument("--k", type=int, default=5, help="how many districts to flag as hotspots")
    parser.add_argument("--horizon", type=int, default=7, help="prediction window in days")
    parser.add_argument("--min-history", type=int, default=21,
                        help="days of burn-in before the first prediction")
    args = parser.parse_args()

    panel = load_panel()
    matrix = pivot_target(panel, args.target)
    print(f"Target: {args.target}  |  districts: {matrix.shape[1]}  |  days: {matrix.shape[0]}\n")

    summaries = []
    for model_name in MODELS:
        result = run_backtest(matrix, model_name, k=args.k, horizon=args.horizon,
                               min_history=args.min_history)
        summaries.append(summarize(result, model_name))

    summary_df = pd.DataFrame(summaries).set_index("model")
    print(summary_df.to_string())
    print(
        "\nPAI (Predictive Accuracy Index) = precision@k / chance rate.\n"
        "PAI = 1.0 means the model is no better than flagging k random "
        "Stadtteile; higher is better."
    )


if __name__ == "__main__":
    main()
