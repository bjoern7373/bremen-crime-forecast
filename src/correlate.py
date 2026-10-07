"""
Correlations between daily city-wide incident counts and the external
features (weather/calendar), WITH proper significance testing:

  - continuous features: Pearson r, its standard p-value, a 95% CI (Fisher
    z-transform), AND a block-permutation p-value that accounts for
    day-to-day autocorrelation (daily crime counts are not independent
    draws -- near-repeat effects and multi-day weather spells mean the
    naive p-value is almost always too optimistic).
  - binary/group features (weekend, holiday, festival, ...): Welch's t-test
    p-value plus a Mann-Whitney U p-value (daily counts are small-integer
    and non-normal, so a rank test is the more honest default; the t-test is
    shown alongside it for comparison).
  - Benjamini-Hochberg FDR correction across ALL tests run in one call,
    since testing ~12 features at once and reading off whichever one has
    p<0.05 is exactly the multiple-comparisons trap.

Usage: python src/correlate.py [--target n_total|n_Einbruch|...] [--n-perm 2000]
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"


def load_daily_totals(target: str) -> pd.DataFrame:
    panel = pd.read_csv(PROCESSED_DIR / "daily_panel.csv", parse_dates=["date"])
    city_wide = panel.groupby("date")[target].sum().rename(target)
    ext = pd.read_csv(PROCESSED_DIR / "external_features.csv", parse_dates=["date"])
    return ext.merge(city_wide, on="date", how="left").sort_values("date").reset_index(drop=True)


def pearson_ci(r: float, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """95% CI for Pearson r via the Fisher z-transform."""
    z = np.arctanh(r)
    se = 1 / np.sqrt(n - 3)
    z_crit = stats.norm.ppf(1 - alpha / 2)
    lo, hi = z - z_crit * se, z + z_crit * se
    return np.tanh(lo), np.tanh(hi)


def block_permutation_pvalue(x: np.ndarray, y: np.ndarray, n_perm: int, block: int,
                              rng: np.random.Generator) -> float:
    """Permutation p-value for corr(x, y) that shuffles y in contiguous
    blocks (not individual days) to preserve its autocorrelation structure
    under the null -- a plain per-day shuffle would destroy exactly the
    day-to-day dependence that makes the naive p-value too optimistic, and
    so would understate how often a |r| this big arises by chance."""
    observed = abs(np.corrcoef(x, y)[0, 1])
    n = len(y)
    n_blocks = int(np.ceil(n / block))
    count = 0
    for _ in range(n_perm):
        order = rng.permutation(n_blocks)
        shuffled = np.concatenate([y[b * block:(b + 1) * block] for b in order])[:n]
        r_perm = abs(np.corrcoef(x, shuffled)[0, 1])
        if r_perm >= observed:
            count += 1
    return (count + 1) / (n_perm + 1)  # +1/+1: avoid a reported p=0.0


def benjamini_hochberg(pvals: list[float], q: float = 0.05) -> list[bool]:
    """Returns, per input p-value (in original order), whether it survives
    BH false-discovery-rate control at level q."""
    n = len(pvals)
    order = np.argsort(pvals)
    sorted_p = np.array(pvals)[order]
    thresholds = (np.arange(1, n + 1) / n) * q
    passed = sorted_p <= thresholds
    # BH rule: find the largest k where p_(k) <= (k/n)*q, then all tests up
    # to k pass.
    if not passed.any():
        return [False] * n
    k_max = np.where(passed)[0].max()
    survive_sorted = np.zeros(n, dtype=bool)
    survive_sorted[:k_max + 1] = True
    survive = np.zeros(n, dtype=bool)
    survive[order] = survive_sorted
    return survive.tolist()


CONDITION_KEYS = {
    "wochenende": lambda df: df["is_weekend"],
    "feiertag": lambda df: df["is_public_holiday"],
    "ferien": lambda df: df["school_holiday_name"].notna(),
    "festival": lambda df: df["festival_name"].notna(),
    "werder": lambda df: df["is_werder_heimspiel"],
    "monatsanfang": lambda df: df["is_month_start_payday"],
    "regen": lambda df: df["precip_mm"] > 1,
}


def run_combo(df: pd.DataFrame, y: str, keys: list[str]):
    """Playful 'what if ALL of these conditions hold at once' explorer --
    deliberately NOT run through the Benjamini-Hochberg correction above:
    with n candidate conditions there are 2^n possible combos, so picking one
    combo after seeing the data is pure fishing. Treat any 'finding' here as
    a fun pattern to joke about (einbrecher.exe gucken lieber Werder), not a
    claim -- and note how few days usually match, since a 3-way AND of
    already-rare conditions (rain AND a home game AND school holidays) can
    leave single digits.
    """
    unknown = [k for k in keys if k not in CONDITION_KEYS]
    if unknown:
        print(f"Unbekannte Bedingung(en): {unknown}. Verfuegbar: {list(CONDITION_KEYS)}")
        return
    mask = np.ones(len(df), dtype=bool)
    for k in keys:
        mask &= CONDITION_KEYS[k](df).to_numpy()

    n_match = mask.sum()
    print(f"\n-- Kombination: {' + '.join(keys)} (nur zum Spielen, keine Korrektur!) --")
    print(f"  Tage mit ALLEN Bedingungen gleichzeitig: {n_match} von {len(df)}")
    if n_match < 5:
        print("  Zu wenige Tage fuer irgendeine Aussage -- aber hier sind sie, falls dich "
              "die Anekdote interessiert:")
        cols = ["date", y]
        print(df.loc[mask, cols].to_string(index=False))
        return

    on = df.loc[mask, y]
    off = df.loc[~mask, y]
    u_stat, p_u = stats.mannwhitneyu(on, off, alternative="two-sided")
    print(f"  Mittelwert an diesen Tagen: {on.mean():.2f}  |  sonst: {off.mean():.2f}  "
          f"(p={p_u:.4f}, unkorrigiert, n_match={n_match} -- mit Vorsicht geniessen)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="n_total")
    parser.add_argument("--n-perm", type=int, default=2000,
                        help="block-permutation draws for the autocorrelation-aware p-value")
    parser.add_argument("--block-days", type=int, default=7,
                        help="block length for the permutation test (default: 1 week)")
    parser.add_argument("--combo", default=None,
                        help="comma-separated condition keys to AND together, e.g. "
                             f"'regen,werder,ferien'. Available: {','.join(CONDITION_KEYS)}")
    args = parser.parse_args()

    if args.combo:
        df = load_daily_totals(args.target)
        run_combo(df, args.target, [k.strip() for k in args.combo.split(",")])
        return

    df = load_daily_totals(args.target)
    y = args.target
    n = len(df)
    rng = np.random.default_rng(0)
    print(f"n={n} days, target={y}, city-wide total={df[y].sum()}\n")

    results = []  # (label, kind, stat_str, p_naive, p_robust_or_None)

    print("-- continuous features --")
    print(f"{'feature':16s} {'r':>7s} {'95% CI':>18s} {'p (naive)':>10s} {'p (block-perm)':>15s}")
    for col in ["temp_max_c", "temp_min_c", "precip_mm", "wind_max_kmh", "sunset_hour"]:
        x = df[col].to_numpy(dtype=float)
        yv = df[y].to_numpy(dtype=float)
        r, p_naive = stats.pearsonr(x, yv)
        lo, hi = pearson_ci(r, n)
        p_perm = block_permutation_pvalue(x, yv, args.n_perm, args.block_days, rng)
        print(f"{col:16s} {r:+.3f} [{lo:+.3f},{hi:+.3f}] {p_naive:10.4f} {p_perm:15.4f}")
        results.append((col, "corr", f"r={r:+.3f}", p_naive, p_perm))

    print("\n-- group comparisons (mean incidents/day) --")
    groups = {
        "Wochenende": df["is_weekend"],
        "Feiertag": df["is_public_holiday"],
        "Schulferien": df["school_holiday_name"].notna(),
        "Festival (Freimarkt/Osterwiese/Weihnachtsmarkt)": df["festival_name"].notna(),
        "Werder-Heimspiel": df["is_werder_heimspiel"],
        "Monatsanfang (Tag 1-3)": df["is_month_start_payday"],
        "Regen (>1mm)": df["precip_mm"] > 1,
    }
    print(f"{'feature':48s} {'ja':>6s} {'nein':>6s} {'p (t-test)':>11s} {'p (Mann-Whitney)':>17s}")
    for label, mask in groups.items():
        mask = mask.to_numpy()
        if mask.sum() < 5 or (~mask).sum() < 5:
            print(f"{label:48s}  (zu wenige Tage in einer Gruppe, uebersprungen)")
            continue
        on = df.loc[mask, y]
        off = df.loc[~mask, y]
        t_stat, p_t = stats.ttest_ind(on, off, equal_var=False)
        u_stat, p_u = stats.mannwhitneyu(on, off, alternative="two-sided")
        print(f"{label:48s} {on.mean():6.2f} {off.mean():6.2f} {p_t:11.4f} {p_u:17.4f}")
        results.append((label, "group", f"ja={on.mean():.2f} nein={off.mean():.2f}", p_t, p_u))

    # ---- multiple-comparisons correction across everything tested above ----
    naive_pvals = [r[3] for r in results]
    survives = benjamini_hochberg(naive_pvals, q=0.05)
    print("\n-- nach Benjamini-Hochberg-Korrektur (FDR q=0.05, ueber alle "
          f"{len(results)} Tests in diesem Lauf) --")
    any_survive = False
    for (label, kind, stat_str, p_naive, p_robust), ok in zip(results, survives):
        if ok:
            any_survive = True
            print(f"  {label}: {stat_str}, p_naiv={p_naive:.4f} -- UEBERLEBT Korrektur")
    if not any_survive:
        print("  Keiner der Tests ueberlebt die Korrektur -- bei dieser Datenmenge ist "
              "aktuell nichts hier statistisch robust, nur deskriptiv interessant.")

    print(
        "\nHinweise zur Methodik:\n"
        "- 'p (naiv)' nimmt an, dass jeder Tag ein unabhaengiger Datenpunkt ist. Stimmt nicht:\n"
        "  Serienkriminalitaet, mehrtaegige Wetterlagen etc. machen aufeinanderfolgende Tage\n"
        "  aehnlicher als Zufall -- das macht naive p-Werte zu optimistisch (zu klein).\n"
        "- 'p (block-perm)' / Mann-Whitney sind robuster, weil sie entweder die Tagesstruktur\n"
        "  beim Mischen erhalten (Bloecke a 7 Tage) oder keine Normalverteilung annehmen.\n"
        "- Die Benjamini-Hochberg-Korrektur verhindert das 'Signifikant wird man, wenn man nur\n"
        "  genug Dinge testet'-Problem -- bei 12 Tests pro Lauf waere sonst im Schnitt >1 davon\n"
        "  rein zufaellig unter p<0.05, auch wenn es keinen echten Effekt gibt.\n"
        "- Trotzdem: n liegt noch bei einigen hundert Tagen, viele Kategorien sind sehr\n"
        "  duenn besetzt (z.B. Einbruch). Je mehr Tage der taegliche Scheduled Task sammelt,\n"
        "  desto aussagekraeftiger werden sowohl die Korrelationen als auch diese Tests."
    )


if __name__ == "__main__":
    main()
