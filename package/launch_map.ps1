<#
  Bremen Tatkarte - standalone launcher.
  No installation needed: uses only PowerShell + .NET (built into Windows)
  and Microsoft Edge (preinstalled on Windows 10/11). Python is OPTIONAL --
  if present, a data refresh runs when the last update is >24h old; if not
  present, the map simply shows the data bundled in assets/map_data.json.
#>

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Assets = Join-Path $Root "assets"
$Port = 8731
$StampFile = Join-Path $Root "data\.last_update.txt"

function Test-PortOpen($port) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(300)
        if ($ok -and $client.Connected) { $client.Close(); return $true }
        $client.Close()
        return $false
    } catch { return $false }
}

function Wait-ForPort($port, $timeoutSeconds = 8) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortOpen $port) { return $true }
        Start-Sleep -Milliseconds 200
    }
    return $false
}

function Test-ServesOurAssets($port) {
    # A stray/stuck server from an earlier run (wrong folder, or just hung)
    # otherwise looks "available" on the port and gets silently reused,
    # serving stale or wrong content with no visible error.
    try {
        # "localhost", not 127.0.0.1: serve.ps1 listens under that name only, and would answer 400 otherwise
        $resp = Invoke-WebRequest -Uri "http://localhost:$port/map_data.json" -TimeoutSec 2 -UseBasicParsing
        return $resp.StatusCode -eq 200 -and $resp.Content.TrimStart().StartsWith("[")
    } catch { return $false }
}

# ---- 1. optional data refresh (only if Python is available) ----
$needsUpdate = $true
if (Test-Path $StampFile) {
    try {
        $lastDate = [DateTime]::Parse((Get-Content $StampFile -Raw))
        if (((Get-Date) - $lastDate) -lt (New-TimeSpan -Hours 24)) { $needsUpdate = $false }
    } catch {}
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if ($needsUpdate -and $pythonCmd) {
    Write-Host "Letztes Update >24h her - aktualisiere Daten (Python gefunden)..."
    Push-Location $Root
    $ok = $true
    foreach ($step in @(
        @("src\scrape.py", "--update"),
        @("src\build_dataset.py"),
        @("src\build_external_features.py"),
        @("src\geocode.py"),
        @("src\find_series.py"),
        @("src\forecast.py"),
        @("src\export_map_data.py")
    )) {
        & python @step
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Update-Schritt fehlgeschlagen ($($step[0])) - zeige vorhandenen Datenstand."
            $ok = $false
            break
        }
    }
    if ($ok) {
        New-Item -ItemType Directory -Force -Path (Split-Path $StampFile) | Out-Null
        (Get-Date).ToString("o") | Set-Content $StampFile
        Write-Host "Update abgeschlossen."
    }
    Pop-Location
} elseif ($needsUpdate) {
    Write-Host "Python nicht gefunden - zeige den mitgelieferten Datenstand (kein automatisches Update moeglich)."
    Write-Host "Fuer Live-Updates: Python 3 installieren, dann 'pip install -r requirements.txt' im Hauptordner."
} else {
    Write-Host "Daten sind aktuell (letztes Update vor weniger als 24h)."
}

# ---- 2. make sure OUR server is running (never blindly reuse whatever's on
#         the port -- a stray/stuck server from an earlier run silently
#         serving wrong or stale content is otherwise indistinguishable from
#         a healthy one, and causes exactly this "one window stays blank" bug) ----
if ((Test-PortOpen $Port) -and -not (Test-ServesOurAssets $Port)) {
    Write-Warning "Port $Port ist belegt, antwortet aber nicht mit unseren Daten - weiche auf einen anderen Port aus."
    $p = $Port
    while (Test-PortOpen $p) { $p++ }
    $Port = $p
}

if (-not (Test-PortOpen $Port)) {
    $serveScript = Join-Path $Root "serve.ps1"
    # Build a single quoted argument string -- Start-Process -ArgumentList as an
    # array does not reliably quote elements containing spaces (e.g. paths),
    # which silently truncates them at the first space.
    $argStr = "-NoProfile -ExecutionPolicy Bypass -File `"$serveScript`" -AssetsPath `"$Assets`" -Port $Port"
    Start-Process powershell -WindowStyle Hidden -ArgumentList $argStr
}
# Always wait for an actual, content-verified response, whether the server
# was just started or (occasionally) slower than usual to come up -- a fixed
# short sleep here is what caused one browser window to load before the
# server was ready (blank page).
$deadline = (Get-Date).AddSeconds(10)
$ready = $false
while ((Get-Date) -lt $deadline) {
    if (Test-ServesOurAssets $Port) { $ready = $true; break }
    Start-Sleep -Milliseconds 300
}
if (-not $ready) {
    Write-Warning "Lokaler Server antwortet nach 10s nicht auf Port $Port - oeffne Fenster trotzdem, ggf. Seite neu laden (F5)."
}

# ---- 3. open the map window (it opens the details window itself via
#         window.open() on load/click -- coordinating two independent
#         msedge.exe command-line launches was unreliable: Edge doesn't
#         consistently honor --window-position/--window-size for a second
#         invocation once it's already running, and the two could even land
#         as tabs of the same window instead of separate ones) ----
Add-Type -AssemblyName System.Windows.Forms
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$mapW = [int]($screen.Width * 0.65)
$h = $screen.Height

$edgeCandidates = @(
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe"
)
$edge = $edgeCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

$base = "http://localhost:$Port"
if ($edge) {
    Start-Process -FilePath $edge -ArgumentList "--new-window --window-position=0,0 --window-size=$mapW,$h `"$base/map_local.html`""
} else {
    Write-Host "Microsoft Edge nicht gefunden - oeffne mit dem Standardbrowser (ohne Fensterpositionierung)."
    Start-Process "$base/map_local.html"
}
