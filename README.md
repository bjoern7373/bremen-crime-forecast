# Bremen Crime Forecast

Backtest of simple crime-hotspot prediction models for Bremen, built from
Polizei Bremen's public press releases ("Blaulicht"-Meldungen) -- same
walk-forward methodology as a trading strategy backtest: a risk score is
computed only from data strictly before the prediction day, then evaluated
against what actually happened.

## Why this exists / known limitations

- **The data source deletes itself.** Polizei Bremen's press archive
  (`polizei.bremen.de/news/pressestelle/...`) only keeps the last ~5 months
  of releases live; older chunks 404. `src/scrape.py` falls back to the
  Wayback Machine for anything already deleted, but that's a one-time rescue
  -- it won't help for releases nobody ever archived. See `src/seed_urls.py`.
- **`presseportal.de` is off-limits.** Its `robots.txt` disallows AI crawlers
  and blocks the date-range query params you'd need for historical
  scraping. We don't scrape it.
- **A daily Windows scheduled task** (`BremenCrimeForecastDaily`, see below)
  re-scrapes and rebuilds the dataset every morning so future releases are
  captured before they age out -- the dataset only gets more useful (and
  more statistically meaningful, especially for sparse categories like
  Einbruch) the longer this runs.
- **Delikt classification is rule-based keyword matching** (`src/classify.py`),
  not ML -- good enough for press-release text, not a substitute for actual
  police crime statistics (Kriminalstatistik), which this project has no
  access to.
- **Stadtteil extraction** (`src/districts.py`) is regex-based on the
  free-text "Ort:" field; it's not a geocoder and will miss or misparse
  unusual phrasing.

## Pipeline

```
src/scrape.py          -> data/processed/releases.jsonl   (raw parsed press releases)
src/build_dataset.py   -> data/processed/incidents.csv     (one row per release x Stadtteil)
                        -> data/processed/daily_panel.csv   (date x Stadtteil, zero-filled counts)
src/backtest.py         walk-forward hotspot backtest against daily_panel.csv
```

Setup:
```
pip install -r requirements.txt
python src/scrape.py --no-cache
python src/build_dataset.py
python src/backtest.py --target n_total --k 5 --horizon 7
python src/backtest.py --target n_Einbruch --k 3 --horizon 14
```

## Backtest methodology

`src/backtest.py` rolls a prediction origin forward one day at a time. At
each origin it computes a risk score per Stadtteil from history strictly
before that day, takes the top-`k` districts, and checks whether an incident
actually occurred there within the next `--horizon` days. Three baseline
models are compared:

- `naive`: risk = incident count in the most recent `horizon`-day window
  (the simplest possible near-repeat signal)
- `exp_decay`: exponentially time-weighted sum of all past incidents
  (simplified ETAS/near-repeat kernel)
- `dow_seasonal`: historical average for that day-of-week

Reported as **Precision@k** and **PAI** (Predictive Accuracy Index =
precision@k / chance rate; PAI 1.0 = no better than flagging k random
Stadtteile). As of the first ~23 months of recovered data: overall crime
shows a modest real signal (PAI ~1.1), but Einbruch-specific prediction does
not beat chance yet (PAI < 1) -- there are simply too few burglaries (n=63)
in the dataset so far for a reliable signal; this should improve as the
daily scheduled run accumulates more history.

## Forecast overlay (red halo)

`src/forecast.py` runs the exact same `exp_decay` + `dow_seasonal` models
from `backtest.py` (same defaults: k=5, horizon=7, half_life=7) once more,
on all available history, to score *today* instead of a past day -- an
ensemble (mean of min-max-normalized scores) ranks all 23 Stadtteile, and
the top 5 are flagged `elevated: true`. Written to
`data/processed/forecast.json` -> copied to `assets/forecast.json`. The map
renders these as a toggleable red halo layer (sidebar: "Prognose"), always
labeled with the backtested PAI so it's clear this is a statistical
tendency (~30-40% better than random), not a prediction of individual
events.

## Gerichtsurteile (court verdicts -- experimental, read this before trusting it)

`src/scrape_courts.py` scrapes Landgericht Bremen's press releases (the
*only* Bremen court with a real, browsable multi-year archive -- see below)
and `src/match_verdicts.py` heuristically links them back to incidents on
this map, as a rough "was there a court case?" signal.

**Why only Landgericht Bremen:** researched all three candidates.
Staatsanwaltschaft Bremen's own press page is currently dead ("wird in
Kuerze aktualisiert"). Amtsgericht Bremen (the court that actually handles
most everyday crime -- Diebstahl, Drogendelikte, einfache
Koerperverletzung) has no archive at all, just a single page that gets
overwritten every week with the *next* week's scheduled hearings -- no
verdicts, no history. So in practice this only covers the *serious* end
(Mord, Totschlag, Raub, schwere Koerperverletzung, sexueller Missbrauch,
grosse Drogenverfahren) that goes to Landgericht, not the bulk of incidents
on this map.

**Why matching is intentionally very conservative:** defendants are never
named in these releases -- only age and offense. An early version matched
on offense-category + date alone and produced a confident-*looking* but
**wrong** match (two unrelated violent incidents on the same day, same
category, in a city of ~570,000 people -- coincidences happen). Rebuilt to
require a specific, shared street name between the court's Tatvorwurf text
and the incident's location (or, failing that, an exact Stadtteil match
narrowed to very few candidates); anything weaker is dropped rather than
shown. Result on the current dataset: of 111 Landgericht cases since 2024
with a usable date, exactly **1** meets this bar. That's the honest number,
not a bug -- most court cases simply don't have a matchable incident yet
(crime-to-verdict lag is typically 10-20+ months; our police data only
starts Oct 2024) or don't mention a specific-enough location.

A matched incident shows a "⚖" badge and, in the full-text view, the
linked PM (stage, PDF link) with an explicit caveat that this is a
probability estimate, not an official record of the same case.
`data/processed/court_stats.json` (-> `assets/court_stats.json`) also
tracks an honest aggregate: how many Landgericht cases exist and what
stage they're at (almost all still "laufend" -- ongoing).

Separately, `scrape_courts.py` also polls Amtsgericht Bremen's weekly
bulletin and appends new entries to `data/processed/amtsgericht_log.jsonl`
(deduped by case number) -- not matched to incidents (no verdicts to match
against), just building our own forward-looking history of scheduled
hearings since there's no archive to backfill from.

## External features (weather, events, calendar)

```
src/weather.py            Open-Meteo daily weather for Bremen (temp, precip, wind, sunset time)
src/events_calendar.py     public holidays (live, via `holidays` pkg) + school holidays and
                           festivals (Freimarkt/Osterwiese/Weihnachtsmarkt -- hand-seeded, extend
                           yearly) + SV Werder Bremen home games (live, via OpenLigaDB)
src/build_external_features.py -> data/processed/external_features.csv  (one row per date)
src/correlate.py           quick correlation/group-mean teaser against daily_panel.csv
```

`SCHOOL_HOLIDAYS` and `FESTIVALS` in `events_calendar.py` are manually seeded
for 2025/2026 -- add next year's dates by hand (or scrape
bremen-city.de/veranstaltungen) when they run out.

First-pass correlations (n=704 days, exploratory, small-sample caveats
apply -- see below): total daily incidents are mildly higher during
Bremen school holidays (+0.35/day, +47% vs. term time) and on rainy days
(+0.16/day), and mildly lower on weekends/holidays. Temperature shows a
weak positive correlation (r~+0.16). None of this holds up yet for
Einbruch specifically -- only 56 resolved burglary-days total, far too few
to say anything. Re-run `python src/correlate.py` as the dataset grows.

## Map

```
src/geocode.py             Nominatim geocoding: 19 Stadtteil centroids (cached once) +
                            per-incident street addresses (cached, rate-limited 1 req/sec,
                            bounded to Bremen's bbox so short names don't match Bremerhaven)
                            -> data/processed/incidents_geo.csv
src/fetch_basemap.py       one-time stitch of OSM raster tiles into assets/bremen_base_map.png
                            (zoom 13, covers all 19 Stadtteile) -- re-run only if the base
                            map needs refreshing, not part of the daily job
src/find_series.py         spatio-temporal series detection (see below) -> data/processed/series.json
src/export_map_data.py     -> assets/map_data.json (compact per-incident data for the page,
                            series_id/series_n merged in from series.json if present)
assets/map_artifact.html   the published page itself
```

Published at: https://claude.ai/artifact/DpaY1gPuxUn4MXnewzbF7t

**Why a static base image, not a live Leaflet/OSM map**: Claude Artifacts run
in a sandboxed CSP that blocks live image/tile requests to any host (even
the allowed script CDNs only permit `<script>` fetches) -- so a real pannable
slippy map can't load OSM tiles at runtime. Instead, `fetch_basemap.py`
stitches a set of OSM tiles into one static image once, and the page places
incident markers on top of it using the exact Web Mercator pixel math
(`src/fetch_basemap.py`'s zoom/tile-origin), so the markers line up pixel-
accurately with the real street geography. No pan/zoom, but real OSM
cartography and precise positions.

**Address vs. district precision**: a press release naming a specific street
gets a small colored dot at that exact geocoded point; one that only names a
Stadtteil (e.g. "Bremen-Walle") gets a soft halo centered on that district's
centroid, with a small deterministic jitter so multiple district-only
incidents in the same Stadtteil fan out instead of stacking exactly on top
of each other. Both carry a 2-letter category monogram so identity never
relies on color alone (colorblind-safe) -- see `src/export_map_data.py` for
the 8-color + grey "Sonstiges" category scheme (the 14 raw `classify.py`
categories folded down, since a scatter/map view can't cleanly support more
than ~8 simultaneous hues per the dataviz palette rules).

**Keeping it current**: `run_daily.bat` now also re-geocodes new addresses
and re-exports `map_data.json` every morning, so the underlying files stay
fresh -- but the published Artifact itself is a snapshot (the data is baked
into the HTML at publish time) and needs a manual republish to pick up new
incidents. Ask to "update the map" to get a fresh publish.

**The local desktop version** (`assets/map_local.html`, see "Desktop app"
below) isn't under the Artifact sandbox, so it uses a real Leaflet map with
live OSM tiles instead -- genuine pan/zoom, unlike the static-image Artifact
above.

## Series detection

`src/find_series.py` flags groups of incidents of the *same category* that
are close in both space and time as a possible series -- the same chaining
idea as real crime-linkage analysis: two incidents are linked if within
`--max-distance-m` (default 1000m) and `--max-days` (default 14) of each
other, and linked incidents transitively join one cluster (so a series can
"walk" across the city over weeks, the way a repeat offender's pattern
often does), reported once it reaches `--min-size` (default 3) members.

Only `precision == "address"` incidents are used -- a district-only
("halo") incident's coordinates are a jittered Stadtteil centroid, not a
real location, and would create false clusters just from sharing a
neighbourhood.

**Follow-up deduplication**: Polizei Bremen often publishes 2-3 separate
press releases about the *same* underlying incident (initial report, a
"Taeter gefasst" update, sometimes a correction) -- same category, same
date, same address. Left alone these look exactly like a tiny series, so
`dedupe_followups()` collapses any incidents sharing a category, calendar
date, and location within 30m into one before clustering.

Output: `data/processed/series.json` (one entry per detected series, with
its member `pm_id`s, date range, max pairwise spread, and the Stadtteile
touched), merged into `assets/map_data.json` as `series_id`/`series_n` per
incident (used as the initial/default state of the live explorer below).

**Live explorer** (`assets/map_local.html` only -- not the published
Artifact): the same chaining logic is reimplemented in plain JS
(`computeLiveClusters`/`dedupeFollowups`, mirroring `find_series.py`
exactly, including the follow-up dedup) and reruns instantly in the browser
as you drag the sidebar's three sliders (max distance / max days / min
group size). Matching incidents stay full-size; everything else fades and
shrinks, and a dashed line connects each current cluster -- lets you feel
out, live, how sensitive a "series" is to the thresholds instead of trusting
one fixed set of numbers. This is explicitly a "go look at this" hint, not
a statistical claim -- no significance testing is applied (unlike
`src/correlate.py`), since with a handful of members per cluster there's
nothing to test; treat it as a prompt for a human to check the linked
press releases, not a finding.

## Desktop app (local, two-window)

A desktop shortcut **"Bremen Tatkarte"** launches a local two-window version
of the map (separate from the published Artifact above, which can't do any
of this due to the Artifact sandbox's CSP):

```
assets/map_local.html       the map window -- clicking a point/halo broadcasts
                             the incident (via BroadcastChannel) to...
assets/details_local.html   ...the side window, which lists clicked incidents
                             with a link that opens...
assets/message_local.html   ...the full press-release text in its own window/tab,
                             plus a link to the (possibly dead) original source URL
src/launch_map.py           the launcher: update check, local server, opens both
                             windows positioned side by side (via Edge --new-window)
src/update_gate.py          24h staleness check (data/.last_update.txt) so launching
                             the shortcut repeatedly in one day doesn't re-scrape
                             every time; run_daily.bat marks it done too
```

Double-clicking the shortcut: runs `update_gate.py`; if the last update was
>24h ago (PC time), runs the full `run_daily.bat` pipeline first (this can
take several minutes the first time or after a long gap); then makes sure
`python -m http.server 8731` is serving `assets/` and opens two positioned
Edge windows. The three local pages talk to each other via `BroadcastChannel`
(same-origin, no server round-trip) and plain `<a target="_blank">` links
(never popup-blocked, unlike `window.open()`).

To recreate the shortcut (e.g. after moving the project folder), re-run the
PowerShell `WScript.Shell` snippet that created
`%USERPROFILE%\Desktop\Bremen Tatkarte.lnk`, pointing at `pythonw.exe` with
`src\launch_map.py` as its argument and this folder as "Start in".

## Daily scheduled task

A Windows Task Scheduler task (`BremenCrimeForecastDaily`, daily at 06:00,
see `run_daily.bat`) re-runs scrape + build_dataset so new releases are
captured before Polizei Bremen deletes them. Logs go to `logs/daily.log`.
Manage it with `schtasks /query /tn BremenCrimeForecastDaily` or via Task
Scheduler GUI (`taskschd.msc`).

After exporting, the task also copies `assets/map_data.json` into `docs/`
and pushes to GitHub, which auto-publishes the updated data to the public
site below.

## Public site (GitHub Pages)

The same map, installable as a PWA (works on Android/iOS/desktop, no local
server, no Python needed), is published from `docs/` at:

    https://bjoern7373.github.io/bremen-crime-forecast/

Only the *hosting* lives in the cloud — the scraping step must stay on a
residential connection. Polizei Bremen's site returns 403 for requests
coming from GitHub Actions' (and presumably other cloud providers') IP
ranges, confirmed by testing; a scheduled Actions workflow attempting the
full pipeline fails at the scrape step for this reason. So the public site
is only as fresh as the last local `run_daily.bat` run and its push, same
cadence as before, just now viewable from anywhere instead of only on this
PC.

## Ethics / scope note

This project only reads already-public press releases and does not attempt
to identify, track, or profile individuals. The district-level hotspot
predictions are a research/backtesting exercise, not a decision tool -- see
the discussion earlier in this project about predictive-policing feedback
loops and bias before using anything here to actually direct attention to
places or people.
