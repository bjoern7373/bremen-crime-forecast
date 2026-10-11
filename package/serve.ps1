<#
  Minimal static file server for the Bremen Tatkarte assets folder.
  Pure .NET (System.Net.HttpListener) -- no external dependency, works on
  any Windows machine with PowerShell (built in since Windows 7).
  Runs forever; started hidden/detached by launch_map.ps1.
#>
param(
    [Parameter(Mandatory = $true)][string]$AssetsPath,
    [int]$Port = 8731
)

$ErrorActionPreference = "Stop"
$fullAssets = (Resolve-Path $AssetsPath).Path

$mime = @{
    ".html" = "text/html; charset=utf-8"
    ".json" = "application/json; charset=utf-8"
    ".js"   = "application/javascript; charset=utf-8"
    ".css"  = "text/css; charset=utf-8"
    ".jpg"  = "image/jpeg"
    ".jpeg" = "image/jpeg"
    ".png"  = "image/png"
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()

while ($listener.IsListening) {
    $context = $null
    try {
        $context = $listener.GetContext()
        $req = $context.Request
        $res = $context.Response

        $relPath = [Uri]::UnescapeDataString($req.Url.AbsolutePath.TrimStart('/'))
        if ([string]::IsNullOrWhiteSpace($relPath)) { $relPath = "map_local.html" }

        $filePath = Join-Path $fullAssets $relPath
        $fullFile = [System.IO.Path]::GetFullPath($filePath)

        if (-not $fullFile.StartsWith($fullAssets, [StringComparison]::OrdinalIgnoreCase)) {
            $res.StatusCode = 403
            $res.OutputStream.Close()
            continue
        }

        if (Test-Path $fullFile -PathType Leaf) {
            $ext = [System.IO.Path]::GetExtension($fullFile).ToLowerInvariant()
            $ct = $mime[$ext]
            if (-not $ct) { $ct = "application/octet-stream" }
            $res.ContentType = $ct
            # without this, Edge can silently keep serving an old cached copy
            # of a page after we update it on disk -- confusing to debug
            $res.Headers.Add("Cache-Control", "no-store, no-cache, must-revalidate")
            $res.Headers.Add("Pragma", "no-cache")
            $bytes = [System.IO.File]::ReadAllBytes($fullFile)
            $res.ContentLength64 = $bytes.Length
            $res.OutputStream.Write($bytes, 0, $bytes.Length)
        } else {
            $res.StatusCode = 404
            $msg = [System.Text.Encoding]::UTF8.GetBytes("404 Not Found: $relPath")
            $res.OutputStream.Write($msg, 0, $msg.Length)
        }
    } catch {
        try { if ($context) { $context.Response.StatusCode = 500 } } catch {}
    } finally {
        try { if ($context) { $context.Response.OutputStream.Close() } } catch {}
    }
}
