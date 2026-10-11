"""
Walk-forward hotspot backtest, in the spirit of a trading strategy backtest:

  - "signal" = a risk score per Stadtteil, computed only from data strictly
    before the prediction day (no look-ahead).
  - each day, rank Stadtteile by risk score and take the top-k as the
    "predicted hotspots" for the next `--horizon` days.
  - "hit" = an actual incident occurred in that Stadtteil within the horizon.
  - roll the origin forward one day at a time across the whole test period
    and aggregate hit rate, like an out-of-sample equity curve.

Metrics:
  - Precision@k ("hit rate"): share of the k flagged Stadtteile that really
    had an incident within the horizon.
  - Random: the hit rate you would get by flagging k Stadtteile at random,
    i.e. the share of ALL Stadtteile that had an incident in that window.
  - Lift = hit rate / random. 1.0 means no better than chance.
  - The "long_run" model always flags the Stadtteile with the most incidents
    so far. It is the benchmark that matters: beating random only shows that
    some Stadtteile are busier than others; beating long_run would show that
    the model knows something about *when*.

Days we hold no press releases for (see timing.coverage_spans) are not
tested: a window we cannot see into is neither a hit nor a miss.

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


def score_long_run(history: pd.DataFrame, horizon: int) -> pd.Series:
    """Risk = all incidents so far, i.e. "always the busiest Stadtteile".
    Knows nothing about timing -- the benchmark for the models that claim to."""
    return history.sum()


def normalize(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    if hi - lo < 1e-9:
        return pd.Series(0.0, index=s.index)
    return (s - lo) / (hi - lo)


def score_ensemble(history: pd.DataFrame, horizon: int, current_dow: int) -> pd.Series:
    """The score forecast.py puts on the map: exp_decay and dow_seasonal,
    each scaled to 0..1, averaged."""
    return (normalize(score_exp_decay(history, horizon))
            + normalize(score_dow_seasonal(history, horizon, current_dow))) / 2.0


MODELS = {
    "naive": score_naive_persistence,
    "exp_decay": score_exp_decay,
    "dow_seasonal": score_dow_seasonal,
    "ensemble": score_ensemble,
    "long_run": score_long_run,
}
NEEDS_WEEKDAY = {"dow_seasonal", "ensemble"}


def run_backtest(matrix: pd.DataFrame, model_name: str, k: int, horizon: int,
                  min_history: int = 21, observed: np.ndarray | None = None) -> pd.DataFrame:
    """observed: per row of `matrix`, whether we hold that day's press
    releases (None = all). Only days whose whole horizon is observed are
    tested, and only after min_history observed days."""
    dates = matrix.index
    districts = matrix.columns
    n_districts = len(districts)
    model_fn = MODELS[model_name]
    if observed is None:
        observed = np.ones(len(dates), dtype=bool)
    observed_so_far = np.cumsum(observed)

    records = []
    for i in range(1, len(dates) - horizon + 1):
        if observed_so_far[i - 1] < min_history or not observed[i:i + horizon].all():
            continue
        today = dates[i]
        history = matrix.iloc[:i]  # strictly before today: no look-ahead
        future = matrix.iloc[i:i + horizon]

        if model_name in NEEDS_WEEKDAY:
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
    # what flagging k random Stadtteile would have scored on the same days
    random_rate = (result["n_actually_hot"] / n_districts).mean()
    lift = precision_at_k / random_rate if random_rate > 0 else float("nan")
    return {
        "model": model_name,
        "n_test_days": len(result),
        "precision@k": round(precision_at_k, 3),
        "random": round(random_rate, 3),
        "lift": round(lift, 2),
        "any_hit_rate": round(result["any_hit"].mean(), 3),
    }


def observed_days(dates: pd.DatetimeIndex) -> np.ndarray:
    """Which incident days we hold press releases for. Releases appear the
    day after the incident as a rule, so incident day d counts as observed
    when publication day d+1 is."""
    from timing import coverage_spans, load_inputs
    _, publish_days = load_inputs()
    one_day = pd.Timedelta(days=1)
    observed = np.zeros(len(dates), dtype=bool)
    for start, end in coverage_spans(publish_days):
        observed[(dates >= start - one_day) & (dates <= end - one_day)] = True
    return observed


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
    observed = observed_days(matrix.index)
    print(f"Target: {args.target}  |  districts: {matrix.shape[1]}  |  days: {matrix.shape[0]} "
          f"({int(observed.sum())} observed)\n")

    summaries = []
    for model_name in MODELS:
        result = run_backtest(matrix, model_name, k=args.k, horizon=args.horizon,
                               min_history=args.min_history, observed=observed)
        summaries.append(summarize(result, model_name))

    summary_df = pd.DataFrame(summaries).set_index("model")
    print(summary_df.to_string())
    print(
        "\nlift = precision@k / random. 1.0 means no better than flagging k random\n"
        "Stadtteile. Compare against long_run (\"always the busiest Stadtteile\"):\n"
        "only a model that beats it knows something about timing."
    )


if __name__ == "__main__":
    main()
