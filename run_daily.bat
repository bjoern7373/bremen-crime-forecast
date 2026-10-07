@echo off
cd /d "C:\Users\bjoer\Northern Lights\bremen-crime-forecast"
if not exist logs mkdir logs
echo ---- %date% %time% ---- >> logs\daily.log
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\scrape.py --no-cache >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\build_dataset.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\build_external_features.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\geocode.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\find_series.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\export_map_data.py >> logs\daily.log 2>&1
"C:\Users\bjoer\AppData\Local\Programs\Python\Python314\python.exe" src\update_gate.py --mark-done >> logs\daily.log 2>&1
