"""
Checks the time statements of the forecast (src/timing.py): they use only
what was known at the time, holes in our history count neither as time nor
as events, and the published hit rates are measured on the statements the
map shows. Also the court scraper's "what we collected stays" rule.

Run:  python tests/test_forecast_timing.py     (or: pytest tests)
"""
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import timing

DISTRICTS = sorted(timing.KNOWN_STADTTEILE)


def make_grid(rates, n_days=400, holes=(), seed=1):
    """Synthetic history: reports arrive at random at the given rates per day
    ({(stadtteil, category): rate}); `holes` are (first, last) day numbers we hold nothing for."""
    rng = np.random.default_rng(seed)
    start = pd.Timestamp("2025-01-01")
    covered = np.ones(n_days, dtype=bool)
    for a, b in holes:
        covered[a:b + 1] = False
    rows = []
    for (stadtteil, category), rate in rates.items():
        for d in np.flatnonzero(covered):
            rows += [(start + pd.Timedelta(days=int(d)), stadtteil, category)] * rng.poisson(rate)
    events = pd.DataFrame(rows, columns=["day", "stadtteil", "category"])
    publish_days = pd.Series([start + pd.Timedelta(days=int(d)) for d in np.flatnonzero(covered)])
    return timing.build_grid(events, publish_days)


RATES = {("Mitte", "Raub"): 0.20, ("Mitte", "Einbruch"): 0.05, ("Mitte", "Sonstiges"): 0.10,
         ("Walle", "Raub"): 0.04, ("Walle", "Drogendelikt"): 0.08, ("Huchting", "Einbruch"): 0.01}


def row(grid, stadtteil, category):
    return grid.series.index((stadtteil, category))


def test_statement_uses_nothing_from_the_day_it_is_made_or_later():
    grid = make_grid(RATES)
    t = 250
    a, b = timing.posterior(grid, t)
    grid.counts[:, t:] = 99                       # whatever happens from day t on ...
    grid.covered[t:] = False
    a2, b2 = timing.posterior(grid, t)
    assert np.array_equal(a, a2) and np.array_equal(b, b2)      # ... the statement of day t is the same


def test_hole_in_the_history_is_neither_time_nor_events():
    whole = make_grid({("Mitte", "Raub"): 0.2}, n_days=300, seed=3)
    holed = make_grid({("Mitte", "Raub"): 0.2}, n_days=300, holes=[(100, 199)], seed=3)
    for grid in (whole, holed):
        a, b = timing.posterior(grid, len(grid.days), half_life=None, prior_days=0)
        i = row(grid, "Mitte", "Raub")
        assert 0.15 < a[i] / b[i] < 0.25                        # a third of the days missing: still 0.2 a day
    assert holed.covered.sum() == 200 and holed.counts[:, 100:200].sum() == 0


def test_windows_come_true_about_as_often_as_they_say():
    # a world in which every Stadtteil has its own level and the offense mix is the same everywhere
    level = {"Mitte": 1.0, "Walle": 0.4, "Huchting": 0.15}
    mix = {"Raub": 0.12, "Einbruch": 0.06, "Drogendelikt": 0.03}
    rates = {(s, c): level[s] * mix[c] for s in level for c in mix}
    result = timing.backtest(make_grid(rates, n_days=700, seed=5))
    for level, said in ((0.5, 0.5), (0.8, 0.8)):
        came_true = result["windows"][level]["offense"]["came_true"]
        assert abs(came_true - said) < 0.08, (level, came_true)


def test_the_check_covers_exactly_the_statements_that_are_shown():
    grid = make_grid(RATES)
    t = len(grid.days)
    a, b = timing.posterior(grid, t)
    seen = grid.counts.sum(axis=1)
    windows = timing.current_windows(grid)
    for marked in ({"Mitte", "Walle"}, {"Walle"}):
        mask = timing.shown(grid, a, b, seen, marked)
        checked = {grid.series[i] for i in np.flatnonzero(mask)}
        displayed = set()
        for stadtteil in marked:
            if windows[stadtteil]["any"]["n_reports"] >= timing.MIN_REPORTS:
                displayed.add((stadtteil, timing.ANY))
            displayed |= {(stadtteil, o["category"]) for o in windows[stadtteil]["offenses"]
                          if o["n_reports"] >= timing.MIN_REPORTS}
        assert checked == displayed, (marked, checked ^ displayed)
    assert all(category != timing.CATCH_ALL for _, category in checked)          # "Sonstiges" is never named


def test_most_frequent_offense_comes_first_with_its_count():
    windows = timing.current_windows(make_grid(RATES))
    mitte = windows["Mitte"]
    assert [o["category"] for o in mitte["offenses"]][:2] == ["Raub", "Einbruch"]
    assert mitte["offenses"][0]["n_reports"] > mitte["offenses"][1]["n_reports"]
    assert mitte["any"]["n_reports"] >= sum(o["n_reports"] for o in mitte["offenses"])
    quiet = windows["Borgfeld"]                                                     # nothing ever reported there
    assert quiet["offenses"] == [] and quiet["any"]["t50"] is None and quiet["any"]["t80"] is None


def test_hit_rates_follow_the_marked_stadtteile():
    grid = make_grid(RATES, n_days=500, seed=7)
    busy = timing.backtest(grid, marked=lambda day: {"Mitte"})["windows"][0.8]
    quiet = timing.backtest(grid, marked=lambda day: {"Huchting"})["windows"][0.8]
    assert busy["shown_any"]["n"] > 100
    assert quiet["shown_any"]["n"] < busy["shown_any"]["n"]         # few statements: seldom 3 reports there
    assert busy["offense"] == quiet["offense"]                      # the all-pairs figure doesn't depend on it


# ---- court scraper: what we collected stays ----------------------------------

def test_court_page_without_links_keeps_the_releases_we_have():
    import scrape_courts
    folder = Path(tempfile.mkdtemp(prefix="courts-test-"))
    scrape_courts.PROCESSED_DIR = folder
    kept = [{"pm_id": "lg-2026-63", "pm_year": 2026, "pm_number": 63, "title": "Auftakt", "text": "x"},
            {"pm_id": "lg-2024-1", "pm_year": 2024, "pm_number": 1, "title": "Alt", "text": "y"}]
    (folder / "court_releases.jsonl").write_text("".join(json.dumps(r) + "\n" for r in kept), encoding="utf-8")
    (folder / "amtsgericht_log.jsonl").write_text(
        json.dumps({"az": "84 Ds 1 Js 1/26", "date": "12.10.2026"}) + "\n" + '{"az": "cut off by a cra', encoding="utf-8")

    class Page:
        text = "<html><body>Wartungsarbeiten</body></html>"
    scrape_courts._get = lambda url: Page()
    scrape_courts.scrape_amtsgericht_weekly = lambda: [{"az": "84 Ds 1 Js 1/26", "date": "12.10.2026"},
                                                       {"az": "70 Ds 100 Js 12345/26", "date": "13.10.2026"}]
    with contextlib.redirect_stdout(io.StringIO()) as out:
        scrape_courts.main()
    after = [json.loads(line) for line in (folder / "court_releases.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["pm_id"] for r in after] == ["lg-2026-63", "lg-2024-1"]
    # the hearing log: a cut-off line neither stops the collecting nor swallows the next record
    lines = (folder / "amtsgericht_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["az"] == "70 Ds 100 Js 12345/26" and "1 new" in out.getvalue()
    assert len(lines) == 3


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:  # noqa: BLE001 -- report every failure, then exit non-zero
            failed += 1
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed} of {len(tests)} passed")
    sys.exit(1 if failed else 0)
