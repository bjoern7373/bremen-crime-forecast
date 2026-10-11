"""
When is the next police report to be expected -- per Stadtteil and offense
type? ("In den nächsten N Tagen ist in X mit ... zu rechnen.")

Model, per (Stadtteil, offense type): reports arrive at some rate per day.
We estimate that rate from how many reports we have seen on how many
*observed* days, and turn it into "with 50 % / 80 % probability the next one
comes within N days".

Three things keep this honest:

  - Observed days only. Our history has holes -- weeks whose archive page the
    press office had already deleted before we started collecting. A hole is
    neither "nothing happened" nor ignored: those days simply don't count,
    neither as time nor as events (see coverage_spans).
  - Thin data is pulled towards what is typical. With five reports of a kind
    in one Stadtteil the raw rate is shaky, so it is blended with the
    city-wide rate of that offense type scaled to the Stadtteil's share of
    all reports (PRIOR_DAYS says how many days of evidence that counts for).
    The remaining uncertainty widens the time window instead of being hidden.
  - It is checked against the past. backtest() walks through the history,
    makes the same statements using only what was known on that day, and
    counts how often they came true. forecast.py publishes those hit rates
    next to the statements.

A "report" is a press release of the Polizei Bremen, dated by the day it was
published -- not every offense that happens is reported there.

Usage:
    python src/timing.py            # today's time windows + how they did in the past
    python src/timing.py --compare  # compare model settings (how the defaults were chosen)
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

import export_news
from districts import KNOWN_STADTTEILE

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

ANY = "_any"               # all offense types of a Stadtteil taken together
CATCH_ALL = "Sonstiges"    # reports matching no offense keyword: never named as "what comes next"

HALF_LIFE = 120.0          # days after which a past report counts half; None = all history equally
PRIOR_DAYS = 90.0          # weight of the "typical" rate, in days of evidence
PRIOR_DAYS_ANY = 7.0       # same for "any report at all": Stadtteile differ far too much for a
                           # shared typical rate to deserve more than a token weight
TOP_N = 3                  # offense types named per Stadtteil
SHOWN_DISTRICTS = 5        # the map states windows for its marked Stadtteile -- in effect the busiest
MIN_REPORTS = 3            # fewer reports than this: no time window at all, the rate is a guess
MAX_GAP_DAYS = 7           # more days than this without any press release = a hole in our history
MIN_SPAN_DAYS = 5          # shorter "covered" stretches are stray dates (typos), not coverage
MIN_HISTORY_DAYS = 120     # observed days needed before the backtest starts making statements
HORIZONS = (14, 30)
QUANTILES = (0.5, 0.8)
MAX_WINDOW_DAYS = 180      # beyond this a time window says nothing useful ("selten")


# ---- data ------------------------------------------------------------------

def coverage_spans(publish_days: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Stretches of days for which we hold the press releases, from the
    publication days of ALL releases (not just crime reports): the press
    office publishes almost daily, so more than MAX_GAP_DAYS without a single
    release means we are missing that period, not that nothing happened."""
    days = sorted(pd.to_datetime(publish_days.dropna()).dt.normalize().unique())
    spans, start, prev = [], None, None
    for day in days:
        if start is None:
            start = day
        elif (day - prev).days > MAX_GAP_DAYS:
            spans.append((start, prev))
            start = day
        prev = day
    if start is not None:
        spans.append((start, prev))
    return [(a, b) for a, b in spans if (b - a).days + 1 >= MIN_SPAN_DAYS]


def load_inputs() -> tuple[pd.DataFrame, pd.Series]:
    """(events, publication days of all releases). An event is one report
    about one Stadtteil: day, stadtteil, category."""
    inc = pd.read_csv(PROCESSED_DIR / "incidents.csv")
    inc = inc[inc["stadtteil"].isin(KNOWN_STADTTEILE) & ~inc["is_bremerhaven"].astype(bool)]
    # "pub": the day of the release, with the press office's mistyped dates repaired (build_dataset.py)
    day = pd.to_datetime(inc["pub"], errors="coerce")
    day = day.fillna(pd.to_datetime(inc["incident_date"], errors="coerce"))
    events = pd.DataFrame({"day": day.dt.normalize(), "stadtteil": inc["stadtteil"],
                           "category": inc["category"]}).dropna()

    releases = export_news.read_jsonl(PROCESSED_DIR / "releases.jsonl")
    days = export_news.release_days(releases, export_news.key_maker(releases))
    publish_days = pd.to_datetime(pd.Series(list(days.values()), dtype="object"), errors="coerce")
    return events, publish_days


@dataclass
class Grid:
    """Daily counts per series on one common calendar."""
    days: pd.DatetimeIndex     # every calendar day from the first to the last observed one
    covered: np.ndarray        # [n_days] bool: do we hold that day's releases?
    series: list[tuple[str, str]]   # (stadtteil, category), category may be ANY
    counts: np.ndarray         # [n_series, n_days]
    is_any: np.ndarray         # [n_series] bool
    is_named: np.ndarray       # [n_series] bool: a real offense type (not ANY, not the catch-all)
    district_of: np.ndarray    # [n_series] index into sorted Stadtteile
    category_of: np.ndarray    # [n_series] index into categories (-1 for ANY)
    n_districts: int


def build_grid(events: pd.DataFrame, publish_days: pd.Series) -> Grid:
    spans = coverage_spans(publish_days)
    if not spans:
        raise ValueError("no covered period found -- are there releases with a publish date?")
    days = pd.date_range(spans[0][0], spans[-1][1], freq="D")
    covered = np.zeros(len(days), dtype=bool)
    for a, b in spans:
        covered[(days >= a) & (days <= b)] = True

    districts = sorted(KNOWN_STADTTEILE)
    categories = sorted(events["category"].unique())
    series = [(s, c) for s in districts for c in categories] + [(s, ANY) for s in districts]
    row = {key: i for i, key in enumerate(series)}
    counts = np.zeros((len(series), len(days)))
    day_idx = {d: i for i, d in enumerate(days)}
    for day, s, c in events.itertuples(index=False):
        i = day_idx.get(day)
        if i is None or not covered[i]:
            continue  # a stray date outside what we observed: can't be placed, so it doesn't count
        counts[row[(s, c)], i] += 1
        counts[row[(s, ANY)], i] += 1

    d_index = {s: i for i, s in enumerate(districts)}
    c_index = {c: i for i, c in enumerate(categories)}
    return Grid(
        days=days, covered=covered, series=series, counts=counts,
        is_any=np.array([c == ANY for _, c in series]),
        is_named=np.array([c not in (ANY, CATCH_ALL) for _, c in series]),
        district_of=np.array([d_index[s] for s, _ in series]),
        category_of=np.array([c_index.get(c, -1) for _, c in series]),
        n_districts=len(districts),
    )


# ---- the model -------------------------------------------------------------

def posterior(grid: Grid, t: int, half_life: float | None = HALF_LIFE,
              prior_days: float = PRIOR_DAYS,
              prior_days_any: float = PRIOR_DAYS_ANY) -> tuple[np.ndarray, np.ndarray]:
    """Rate estimate for every series from the days before index t only, as
    the two numbers (a, b) of a Gamma distribution: expected rate a / b per
    day, and the smaller b, the less sure we are."""
    age = np.arange(t, 0, -1)                       # yesterday = 1 day old
    w = np.ones(t) if half_life is None else 0.5 ** (age / half_life)
    w = w * grid.covered[:t]
    exposure = w.sum()                              # observed days, recent ones counting more
    n = grid.counts[:, :t] @ w                      # reports, weighted the same way

    # the "typical" rate: this offense type city-wide x this Stadtteil's share of all reports
    any_by_district = np.zeros(grid.n_districts)
    np.add.at(any_by_district, grid.district_of[grid.is_any], n[grid.is_any])
    total = any_by_district.sum()
    share = any_by_district / total if total > 0 else np.full(grid.n_districts, 1 / grid.n_districts)
    n_categories = grid.category_of.max() + 1
    by_category = np.zeros(n_categories)
    cat_rows = ~grid.is_any
    np.add.at(by_category, grid.category_of[cat_rows], n[cat_rows])
    rate_typical = np.where(
        grid.is_any,
        (total / grid.n_districts) / max(exposure, 1e-9),
        by_category[grid.category_of] * share[grid.district_of] / max(exposure, 1e-9),
    )

    prior = np.where(grid.is_any, prior_days_any, prior_days)
    a = np.maximum(prior * rate_typical + n, 1e-9)
    b = prior + exposure
    return a, b


def prob_within(a: np.ndarray, b: np.ndarray, days: float) -> np.ndarray:
    """Probability of at least one report within `days` observed days."""
    return 1.0 - (b / (b + days)) ** a


def days_until(a: np.ndarray, b: np.ndarray, p: float) -> np.ndarray:
    """Number of days within which the next report comes with probability p."""
    with np.errstate(over="ignore"):
        return b * ((1.0 - p) ** (-1.0 / a) - 1.0)


def top_offenses(grid: Grid, any_row: int, rate: np.ndarray, seen: np.ndarray) -> np.ndarray:
    """The TOP_N named offense types of one Stadtteil, as series rows: the
    ones reported most often so far (ties: the higher current rate)."""
    named = np.flatnonzero((grid.district_of == grid.district_of[any_row]) & grid.is_named)
    return named[np.lexsort((-rate[named], -seen[named]))[:TOP_N]]


def shown(grid: Grid, a: np.ndarray, b: np.ndarray, seen: np.ndarray, districts=None) -> np.ndarray:
    """Which statements the map actually puts in front of the reader: for
    the marked Stadtteile (`districts`; without them the SHOWN_DISTRICTS
    busiest), the 'any report' window and the windows of their most frequent
    named offense types -- and none for a series with fewer than MIN_REPORTS
    reports. Picking the most frequent also picks the over-estimates, so
    these statements have to be checked as a group of their own: the average
    over all pairs flatters them. `seen`: reports per series so far."""
    rate = a / b
    mask = np.zeros(len(rate), dtype=bool)
    any_rows = np.flatnonzero(grid.is_any)
    if districts is None:
        rows = any_rows[np.argsort(-rate[any_rows], kind="stable")[:SHOWN_DISTRICTS]]
    else:
        rows = [row for row in any_rows if grid.series[row][0] in districts]
    for row in rows:
        mask[row] = True
        mask[top_offenses(grid, row, rate, seen)] = True
    return mask & (seen >= MIN_REPORTS)


# ---- checking it against the past -----------------------------------------

def backtest(grid: Grid, half_life: float | None = HALF_LIFE, prior_days: float = PRIOR_DAYS,
             prior_days_any: float = PRIOR_DAYS_ANY, step: int = 1, marked=None) -> dict:
    """Walk forward through the history. On each observed day, make the
    statements from what was known before that day, then look at what
    actually followed. Time ahead is counted in observed days, so a hole in
    our history can neither produce nor hide a report.

    marked: function day -> names of the Stadtteile the map would have
    marked on that day (None: the busiest ones). The hit rates under
    "shown_..." are those of the statements the map would have shown."""
    cov_idx = np.flatnonzero(grid.covered)
    cum = np.concatenate([np.zeros((len(grid.series), 1)),
                          np.cumsum(grid.counts[:, cov_idx], axis=1)], axis=1)
    n_cov = len(cov_idx)

    def happened(j0: int, n_days: np.ndarray | int) -> np.ndarray:
        """At least one report within the next n_days observed days (from position j0)?"""
        end = np.minimum(j0 + np.asarray(n_days), n_cov)
        return (cum[np.arange(len(grid.series)), end] - cum[:, j0]) > 0

    horizon_rows = {h: [] for h in HORIZONS}      # (probability, outcome, is_any)
    quantile_rows = {q: [] for q in QUANTILES}    # (probability for the rounded window, outcome, is_any, shown)
    for j0 in range(MIN_HISTORY_DAYS, n_cov, step):
        t = cov_idx[j0]
        a, b = posterior(grid, t, half_life, prior_days, prior_days_any)
        on_map = shown(grid, a, b, grid.counts[:, :t].sum(axis=1), marked(grid.days[t]) if marked else None)
        for h in HORIZONS:
            if j0 + h <= n_cov:
                horizon_rows[h].append((prob_within(a, b, h), happened(j0, h), grid.is_any))
        for q in QUANTILES:
            window = np.ceil(days_until(a, b, q))
            # only windows we would actually show, and whose end we can see in the data;
            # both depend on what was known at the time, not on what happened afterwards
            usable = (window <= MAX_WINDOW_DAYS) & (j0 + window <= n_cov)
            if usable.any():
                win = np.where(usable, window, 0).astype(int)
                quantile_rows[q].append((prob_within(a, b, win)[usable], happened(j0, win)[usable],
                                         grid.is_any[usable], on_map[usable]))

    result = {"half_life": half_life, "prior_days": prior_days, "prior_days_any": prior_days_any,
              "n_origins": len(range(MIN_HISTORY_DAYS, n_cov, step)), "horizons": {}, "windows": {}}
    for h, rows in horizon_rows.items():
        if not rows:
            continue
        p = np.concatenate([r[0] for r in rows]); y = np.concatenate([r[1] for r in rows]).astype(float)
        is_any = np.concatenate([r[2] for r in rows])
        result["horizons"][h] = {name: _scores(p[mask], y[mask])
                                 for name, mask in (("offense", ~is_any), ("any", is_any))}
    for q, rows in quantile_rows.items():
        if not rows:
            continue
        p = np.concatenate([r[0] for r in rows]); y = np.concatenate([r[1] for r in rows])
        is_any = np.concatenate([r[2] for r in rows]); on_map = np.concatenate([r[3] for r in rows])
        # "said" is a little above the nominal 50 / 80 % because windows are rounded up to whole days
        result["windows"][q] = {name: {"n": int(mask.sum()),
                                       "said": round(float(p[mask].mean()), 3) if mask.any() else None,
                                       "came_true": round(float(y[mask].mean()), 3) if mask.any() else None}
                                for name, mask in (("offense", ~is_any), ("any", is_any),
                                                   ("shown_offense", ~is_any & on_map),
                                                   ("shown_any", is_any & on_map))}
    return result


def _scores(p: np.ndarray, y: np.ndarray) -> dict:
    eps = 1e-6
    pc = np.clip(p, eps, 1 - eps)
    base = y.mean()
    brier = float(np.mean((p - y) ** 2))
    brier_base = float(np.mean((base - y) ** 2))
    bins = []
    for lo, hi in ((0, .1), (.1, .25), (.25, .5), (.5, .75), (.75, 1.0001)):
        m = (p >= lo) & (p < hi)
        if m.sum():
            bins.append({"said": round(float(p[m].mean()), 3), "happened": round(float(y[m].mean()), 3),
                         "n": int(m.sum())})
    return {
        "n": int(len(p)),
        "base_rate": round(float(base), 4),
        "log_loss": round(float(-np.mean(y * np.log(pc) + (1 - y) * np.log(1 - pc))), 4),
        "brier": round(brier, 4),
        # > 0: better than always quoting the overall average probability
        "skill": round(1 - brier / brier_base, 3) if brier_base > 0 else None,
        "bins": bins,
    }


# ---- today's statements ----------------------------------------------------

def _window(a: float, b: float) -> dict:
    out = {"rate_per_30d": round(30 * a / b, 2)}
    for h in HORIZONS:
        out[f"p{h}"] = round(float(prob_within(np.array([a]), np.array([b]), h)[0]), 2)
    for q in QUANTILES:
        days = float(days_until(np.array([a]), np.array([b]), q)[0])
        out[f"t{int(q * 100)}"] = int(math.ceil(days)) if days <= MAX_WINDOW_DAYS else None
    return out


def current_windows(grid: Grid, top_n: int = TOP_N) -> dict:
    """Stadtteil -> time windows as of the day after the last observed one:
    for any report at all, and for its top_n most frequent named offense
    types (highest rate; the unclassified catch-all is left out), each with
    its share of all reports there."""
    t = len(grid.days)
    a, b = posterior(grid, t)
    rate = a / b
    seen = grid.counts.sum(axis=1)

    def info(i):
        entry = {**_window(a[i], b[i]), "n_reports": int(seen[i])}
        if seen[i] < MIN_REPORTS:
            entry.update({f"t{int(q * 100)}": None for q in QUANTILES})
        return entry

    out = {}
    for row in np.flatnonzero(grid.is_any):
        offenses = [{"category": grid.series[i][1], **info(i)} for i in top_offenses(grid, row, rate, seen)[:top_n]]
        out[grid.series[row][0]] = {"any": info(row), "offenses": [o for o in offenses if o["n_reports"] > 0]}
    return out


def coverage_summary(grid: Grid) -> dict:
    cov = grid.covered
    holes = int(np.sum(cov[:-1] & ~cov[1:]))
    return {
        "first_day": grid.days[0].strftime("%Y-%m-%d"),
        "last_day": grid.days[-1].strftime("%Y-%m-%d"),
        "observed_days": int(cov.sum()),
        "calendar_days": int(len(cov)),
        "holes": holes,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare", action="store_true", help="compare model settings")
    args = parser.parse_args()

    grid = build_grid(*load_inputs())
    cov = coverage_summary(grid)
    print(f"History {cov['first_day']} .. {cov['last_day']}: {cov['observed_days']} of "
          f"{cov['calendar_days']} days observed ({cov['holes']} holes), "
          f"{int(grid.counts[grid.is_any].sum())} reports\n")

    if args.compare:
        def row(label, r, kind):
            h14, h30 = r["horizons"][14][kind], r["horizons"][30][kind]
            w50, w80 = r["windows"][0.5][kind], r["windows"][0.8][kind]
            print(f"{label:22s} | 14d {h14['log_loss']:.4f} {h14['skill']:+.3f}  "
                  f"30d {h30['log_loss']:.4f} {h30['skill']:+.3f} | "
                  f"50%: {w50['came_true']} (n={w50['n']})  80%: {w80['came_true']} (n={w80['n']})")

        header = "half-life prior-days   | log-loss, skill vs. average       | time windows that came true"
        print("Per offense type")
        print(header)
        for half_life in (None, 240.0, 120.0, 60.0, 30.0):
            for prior_days in (0.0, 30.0, 90.0, 180.0, 365.0):
                row(f"{str(half_life):>8} {prior_days:6.0f}", backtest(grid, half_life, prior_days, step=2),
                    "offense")
        print()
        print("Any report at all")
        print(header)
        for half_life in (None, 240.0, 120.0, 60.0, 30.0):
            for prior_days_any in (0.0, 7.0, 14.0, 30.0):
                row(f"{str(half_life):>8} {prior_days_any:6.0f}",
                    backtest(grid, half_life, prior_days_any=prior_days_any, step=2), "any")
        return

    r = backtest(grid)
    print(json.dumps(r, indent=1))
    windows = current_windows(grid)
    print("\nToday's windows (50 % / 80 %), busiest Stadtteile:")
    for s in sorted(windows, key=lambda s: -windows[s]["any"]["n_reports"])[:8]:
        w = windows[s]
        line = ", ".join(f"{o['category']} {o['t50']}/{o['t80']}d" for o in w["offenses"])
        print(f"  {s:20s} any {w['any']['t50']}/{w['any']['t80']}d  |  {line}")


if __name__ == "__main__":
    main()
