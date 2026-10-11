@echo off
cd /d "C:\Users\bjoer\Northern Lights\bremen-crime-forecast"
if not exist logs mkdir logs
set PY="C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe"
rem a character the Windows code page lacks (in a title, say) must not crash a step that prints it
set PYTHONIOENCODING=cp1252:backslashreplace
echo ---- %date% %time% ---- >> logs\daily.log
rem remembered so a start of the map soon after a failed update doesn't run all of this again
%PY% src\update_gate.py --mark-attempt >> logs\daily.log 2>&1

rem 99 = "the scrape step never ran" (e.g. the log file was locked by another instance),
rem so a left-over errorlevel can never be mistaken for a successful scrape
set SCRAPE_STATUS=99
%PY% src\scrape.py --update >> logs\daily.log 2>&1 && set "SCRAPE_STATUS=0" || call set "SCRAPE_STATUS=%%errorlevel%%"

%PY% src\build_dataset.py >> logs\daily.log 2>&1
%PY% src\build_external_features.py >> logs\daily.log 2>&1
%PY% src\geocode.py >> logs\daily.log 2>&1
%PY% src\find_series.py >> logs\daily.log 2>&1
%PY% src\forecast.py >> logs\daily.log 2>&1
%PY% src\scrape_courts.py >> logs\daily.log 2>&1
%PY% src\match_verdicts.py >> logs\daily.log 2>&1
set EXPORT_STATUS=99
%PY% src\export_map_data.py >> logs\daily.log 2>&1 && set "EXPORT_STATUS=0" || call set "EXPORT_STATUS=%%errorlevel%%"

rem Only count today's update as done if the newest police page was actually fetched AND the
rem result could be exported; otherwise a later start of the map tries again instead of waiting 24h.
rem scrape.py exit codes: 0 = up to date, 3 = newest page not reachable, anything else = it broke.
if "%SCRAPE_STATUS%"=="0" (
    if "%EXPORT_STATUS%"=="0" (
        %PY% src\update_gate.py --mark-done >> logs\daily.log 2>&1
    ) else (
        echo Export fehlgeschlagen - Update gilt als nicht erledigt, ein spaeterer Start versucht es erneut >> logs\daily.log
    )
) else if "%SCRAPE_STATUS%"=="3" (
    echo Polizei-Seite nicht erreicht - Update gilt als nicht erledigt, ein spaeterer Start versucht es erneut >> logs\daily.log
) else (
    echo FEHLER: scrape.py ist abgebrochen ^(Code %SCRAPE_STATUS%, Meldung steht weiter oben^) - Update gilt als nicht erledigt >> logs\daily.log
)

rem Publish the refreshed data to the public GitHub Pages site (bjoern7373.github.io/bremen-crime-forecast)
rem -- but only as a complete set: the pages load these files together, so if the export broke,
rem the public site keeps yesterday's set instead of getting a mix of old and new files.
if "%EXPORT_STATUS%"=="0" (
    for %%F in (map_data.json forecast.json court_stats.json news_index.json police_other.json court_cases.json) do (
        if exist assets\%%F copy /y assets\%%F docs\%%F >> logs\daily.log 2>&1
    )
) else (
    echo FEHLER: export_map_data.py ist abgebrochen ^(Code %EXPORT_STATUS%^) - oeffentliche Karte behaelt den bisherigen Stand >> logs\daily.log
)
rem "data" as a whole: raw pages, the permanent archive, caches -- everything worth keeping a second copy of
rem assets\*.json: the local map's copies of the same files, so the working folder stays in step with the history
git add data docs\*.json assets\*.json >> logs\daily.log 2>&1
rem keeps anything too large for GitHub out of the commit (see src\repo_guard.py)
%PY% src\repo_guard.py --check >> logs\daily.log 2>&1
git commit -m "Daily data update" >> logs\daily.log 2>&1
git push >> logs\daily.log 2>&1
set PUSH_STATUS=%errorlevel%
%PY% src\repo_guard.py --pushed %PUSH_STATUS% >> logs\daily.log 2>&1
rem keep the local history compact, so its size stays close to what GitHub stores
git -c gc.auto=300 gc --auto --quiet >> logs\daily.log 2>&1
