@echo off
cd /d "C:\Users\bjoer\Northern Lights\bremen-crime-forecast"
if not exist logs mkdir logs
echo ---- %date% %time% ---- >> logs\daily.log
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\scrape.py --no-cache >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\build_dataset.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\build_external_features.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\geocode.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\find_series.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\forecast.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\scrape_courts.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\match_verdicts.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\export_map_data.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\update_gate.py --mark-done >> logs\daily.log 2>&1

rem publish the refreshed data to the public GitHub Pages site (bjoern7373.github.io/bremen-crime-forecast)
copy /y assets\map_data.json docs\map_data.json >> logs\daily.log 2>&1
copy /y assets\forecast.json docs\forecast.json >> logs\daily.log 2>&1
copy /y assets\court_stats.json docs\court_stats.json >> logs\daily.log 2>&1
git add data\raw data\raw_courts data\geocode_cache data\processed docs\map_data.json docs\forecast.json docs\court_stats.json >> logs\daily.log 2>&1
git commit -m "Daily data update" >> logs\daily.log 2>&1
git push >> logs\daily.log 2>&1
