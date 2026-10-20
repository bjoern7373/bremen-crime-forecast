# Bremen Crime Forecast

Backtest of simple crime-hotspot prediction models for Bremen, built from
Polizei Bremen's public press releases ("Blaulicht"-Meldungen) -- same
walk-forward methodology as a trading strategy backtest: a risk score is
computed only from data strictly before the prediction day, then evaluated
against what actually happened.

## Why this exists / known limitations

- **The data source deletes itself.** Polizei Bremen's press archive
  (`polizei.bremen.de/news/pressestelle/...`) only keeps the last ~5 months
  of releases live; older chunks 404. So this project keeps its own
  permanent archive (see "Our own archive" below): whatever was fetched once
  stays. The Wayback Machine was used once to rescue pages deleted before
  the project started -- it won't help for releases nobody ever archived.
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
src/scrape.py          -> data/archive/releases.jsonl      (every press release ever parsed)
                        -> data/processed/releases.jsonl    (copy of the archive for the next step)
src/build_dataset.py   -> data/processed/incidents.csv     (one row per release x Stadtteil)
                        -> data/processed/daily_panel.csv   (date x Stadtteil, zero-filled counts)
src/backtest.py         walk-forward hotspot backtest against daily_panel.csv
```

Setup:
```
pip install -r requirements.txt
python src/scrape.py --update
python src/build_dataset.py
python src/backtest.py --target n_total --k 5 --horizon 7
python src/backtest.py --target n_Einbruch --k 3 --horizon 14
```

### Our own archive (nothing fetched once is ever dropped)

The whole point of running this daily is that the dataset gets better the
longer it runs, so `src/scrape.py` treats our own copies as the archive of
record rather than re-deriving everything from the web each day:

- `data/raw/` holds every archive page as fetched. A stored page is never
  replaced by a version that has lost entries -- if the police remove a
  release from a page, the previous version is kept alongside as
  `<page>@<date>.html` and both are parsed.
- `data/archive/releases.jsonl` holds every release ever parsed and only
  grows. Re-parsing refreshes existing records (so parser fixes apply
  retroactively) but never removes one, and a damaged archive file stops the
  run instead of being overwritten. Its copy in `data/processed/` is read as
  well, so a file that got emptied or cut short loses nothing.
- The press office types its release ids by hand, so an id alone is not
  trusted: a release only updates a stored one if it recognisably is the same
  release (similar text, or the same title). A re-used or mistyped id is
  filed next to the first release instead of replacing it, and a version that
  has become poorer (cut-off download, text shortened in place, a field gone)
  never replaces a richer one.
- `python src/scrape.py --update` (the daily run) re-downloads the newest
  page we hold, the one before it and everything newer -- older ones can't
  change -- and takes everything else from `data/raw/`. A network failure, a
  deleted page or an unreachable Wayback Machine therefore can't abort the
  update or cost any data; the run just reports that today's new releases
  aren't in yet (exit code 3, so the update is retried instead of counting
  as done -- at the next start of the map, but not within two hours of the
  last attempt; 1 or 2 mean the script itself broke).
- `python src/backfill_wayback.py` recovers archive pages the press office
  had already deleted before we started collecting, from the Wayback Machine
  (`--dry-run` lists what is available).
- New archive pages (the press office starts one every few weeks) are
  discovered automatically from the press overview page and RSS feed and
  remembered in `data/known_chunks.json`; `src/seed_urls.py` is only the
  hand-collected starting list.
- Two copies exist at all times: this folder (including its full git
  history) and the GitHub repository the daily run pushes to.

`python tests/test_scrape_archive.py` replays the loss scenarios (page loses
an entry, entry shortened in place, cut-off download, re-used id, network
down, page deleted, Wayback unreachable, damaged or emptied archive, two new
pages since the last run) without touching the network.

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

Reported per model: **precision@k** (share of the flagged Stadtteile that
really had an incident in the window), **random** (what flagging k Stadtteile
at random would have scored on the same days, i.e. the share of all
Stadtteile with an incident) and their ratio, **lift**. Two more models put
the first three into perspective: `ensemble` (what the map uses) and
`long_run`, which always flags the Stadtteile with the most incidents so far
and knows nothing about timing.

Only days we actually hold press releases for are tested: the history has
holes (archive pages the press office had deleted before collection began),
and a window we cannot see into is neither a hit nor a miss.

Result as of 11 October 2026 (269 test days): the map's model is right for
about 67 % of flagged Stadtteile, random flagging for 35 % -- but `long_run`
reaches 69 %. (The current figures are printed by `python src/backtest.py`
and shown in the map's sidebar.) So the marking is far better than chance, yet so far only
because some Stadtteile are permanently busier; no model here has shown that
it knows *when*. An earlier version of this file and of the map claimed
"PAI ~1.3-1.4, 30-40 % better than random": that figure divided by k/23
instead of by the real random hit rate and tested unobserved days as
misses, and is withdrawn.

## Forecast overlay (district halos)

`src/forecast.py` runs the `ensemble` model from `backtest.py` (same
defaults: k=5, horizon=7, half_life=7) once more, on all available history,
to score *today* instead of a past day; the top 5 of the 23 Stadtteile are
flagged `elevated: true`. Written to `data/processed/forecast.json` ->
copied to `assets/forecast.json`. The local and public interactive maps render
one forecast halo per Stadtteil (sidebar: "Prognose"): the top 5 are red,
the others neutral. Every large halo shows its district's forecast and timing,
including districts outside the top 5; those are explicitly described as not
being among the highlighted districts, not as having no risk. Hover shows the
forecast, clicking pins it until a click on the free map or the layer is hidden.
Individual incidents use only the small category dots, including jittered dots
for approximate district-only locations. The sidebar note is generated from the
backtest of that same day (hit rate, random, "always the busiest"), so it
cannot go stale or claim more than was measured.

**Time statements** (`src/timing.py`): per Stadtteil, for any report and
for its most frequently reported offense types, by when the next police
report is to be expected -- two "by when" marks, shown in a box in the
corner of the map while the mouse is over a forecast halo ("in rund 50 % der
Fälle binnen 8 Tagen, in rund 75 % binnen 18 Tagen"). The rate is estimated
from observed days only (holes in the history count neither as time nor as
events), thin data is pulled towards the city-wide rate of that offense type
scaled by the Stadtteil's share of all reports, and the same statements are
replayed over the past (`timing.backtest`) for the Stadtteile the map would
have marked on each day, from what had been published by then. The model
aims at 50 % and 80 %; the percentages the map prints are the measured hit
rates of those replayed statements, rounded to 5 % -- so where the model is
too optimistic (offense-level statements: about 43 % and 71 %), the reader
sees what it delivered, not what it intended. `python src/timing.py
--compare` shows how the settings were chosen. A "report" is a press
release, dated by its publication -- not every offense is reported there.

Dates: the publication day of every release is decided once, in
`export_news.release_days()` -- the date the press office typed, unless it
cannot be right (missing, in the future, or outside the dates of the
releases numbered around it and not fitting the archive page it sits on);
then its neighbour's date is used. No incident is dated after its own
publication (announcements of future events) or in the future.

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
src/geocode.py             Nominatim geocoding: 23 Stadtteil centroids (cached once) +
                            per-incident street addresses (cached, rate-limited 1 req/sec,
                            bounded to Bremen's bbox so short names don't match Bremerhaven)
                            -> data/processed/incidents_geo.csv
src/fetch_basemap.py       one-time stitch of OSM raster tiles into assets/bremen_base_map.png
                            (zoom 13, covers the Stadtteile) -- re-run only if the base
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
the 10-color + grey "Sonstiges" category scheme (the 14 raw `classify.py`
categories folded down, since a scatter/map view can't cleanly support more
than ~10-ish simultaneous hues per the dataviz palette rules). Fahrzeugaufbruch
and Sexualdelikt get their own legend slot despite low current volume --
deliberately, since both are highly relevant to the public and this way a
future rise in either shows up immediately instead of being buried in
"Sonstiges". Verkehrsunfall/Brandstiftung/Hasskriminalitaet still fold into
"Sonstiges" by color, but every incident's *real* category (`rawcat`) is
always shown in its own tooltip/text regardless of which legend color it's
grouped under -- "Sonstiges" in the legend is a grouping, never something an
individual incident's own text hides behind.

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
assets/map_local.html       the map window -- clicking a small point broadcasts
                             the incident (via BroadcastChannel) to...
assets/details_local.html   ...the side window ("Detail-Fenster"), which lists clicked
                             incidents and shows one full text at a time, loading...
assets/message_local.html   ...the full text of a police release (?id= / ?pm=) or a
                             court release (?court=), plus a link to the original
src/launch_map.py           the launcher: update check, local server
                             (src/serve_no_cache.py), opens the map window at 65 %
                             of the screen (via Edge --new-window)
src/update_gate.py          is an update due? Once every 24h (data/.last_update.txt,
                             written by run_daily.bat when an update completed); a
                             failed one is retried, but not within 2h of the last
                             attempt (data/.last_attempt.txt)
```

Large halos on the local and public interactive map always show a district
forecast, never a single incident. Small category dots keep their incident
tooltips and detail-window behavior; district-only locations are approximate.
Forecast halos sit below those dots, so both remain accessible. A halo also
opens its forecast with Enter/Space when focused; Escape closes that box.

Double-clicking the shortcut: runs `update_gate.py`; if an update is due,
runs the full `run_daily.bat` pipeline first (a minute or two); then makes
sure `src/serve_no_cache.py` is serving `assets/` on port 8731 and opens the
map window. The map opens the Detail-Fenster itself (`window.open`, in the
remaining third of the screen); the two talk over a `BroadcastChannel`
(same-origin, no server round-trip), and every request is acknowledged, so
the map notices when that window is gone.

**Reading lists.** Right of the map, the panel "Polizeimeldungen /
Gerichtsverfahren" lists every police press release and every court entry we
hold, newest first (`src/export_news.py` -> `assets/news_index.json`,
headlines only). A click shows the full text in the Detail-Fenster. Where
there is no room for a second window (maximized browser window, phone), the
browser blocks it or it doesn't answer, the text opens inside the map page
instead; `?reader=window` / `?reader=inline` in the map's address forces one
of the two. Every police release is addressed by its lasting key (its
press-release number, `message_local.html?pm=<key>`), never by its position
in a data file. Police releases that are not on the map (no Bremen Stadtteil
recognised) are listed too; their text is in `assets/police_other.json`. Court
entries are the Landgericht press releases since 2024 (`court_cases.json`,
PDF text with its line breaks re-joined) and the Amtsgericht hearings logged
since October 2026. Headlines already opened are remembered in the browser.

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

After exporting, the task also copies the data files the pages load
(`map_data.json`, `forecast.json`, `court_stats.json`, `news_index.json`,
`police_other.json`, `court_cases.json`) into `docs/` and pushes to GitHub,
which auto-publishes the updated data to the public site below.

`src/repo_guard.py` runs between `git add` and `git commit`: GitHub rejects
any push containing a file over 100 MB, and once such a file is in a commit
every later push fails too. A file of 50 MB or more is therefore taken out
of the commit (it stays on this PC) and reported; the repository size
(recommended limit 1 GB) and the result of every push are written to
`assets/repo_status.json`, which the local map shows at the bottom of its
sidebar. As of October 2026 the repository is about 20 MB and a daily
update adds well under 0.1 MB.

## Standalone package

`python src/build_package.py` builds `dist/BremenTatkarte/` and
`dist/BremenTatkarte.zip`: the map for a PC without this project. It takes
the launcher files from `package/` (Start.bat, launch_map.ps1, serve.ps1,
LIESMICH.txt -- PowerShell only, no Python needed to view), the pages and
data files from `assets/`, our whole archive from `data/`, and from `src/`
exactly the scripts its own update needs (the steps named in
`package/launch_map.ps1` plus everything they import). Before zipping, the
package is checked: its own export, run on its own data, must reproduce the
data files it ships -- so pages, data and scripts cannot drift apart. Run
the daily pipeline first if `data/` has changed since the last export.
The package does not collect court data; it keeps the court list it ships.

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
