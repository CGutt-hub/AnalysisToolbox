# Generic Results Archive Server
# Serves any directory over HTTP — designed for *_results.html viewers backed by parquet sidecars.
#
# Usage:
#   .\serve_html.ps1                          # serves current directory, auto-detects HTML
#   .\serve_html.ps1 -ServeDir <path>         # serve a specific directory
#   .\serve_html.ps1 -ServeDir <path> -Port 9090
#   .\serve_html.ps1 -NoOpen                  # skip auto-opening browser

param(
    [string]$ServeDir = '',
    [int]$Port = 8080,
    [switch]$NoOpen
)

# Default: serve the current working directory
if (-not $ServeDir) {
    $ServeDir = (Get-Location).Path
}

$ServeDir = (Resolve-Path $ServeDir -ErrorAction SilentlyContinue)?.Path
if (-not $ServeDir -or -not (Test-Path $ServeDir)) {
    Write-Error "Directory not found: '$ServeDir'"
    exit 1
}

# Auto-detect the main HTML file (*_results.html preferred, else first .html)
$htmlFile = Get-ChildItem $ServeDir -Filter '*_results.html' | Select-Object -First 1
if (-not $htmlFile) {
    $htmlFile = Get-ChildItem $ServeDir -Filter '*.html' | Select-Object -First 1
}
$defaultUrl = if ($htmlFile) { $htmlFile.Name } else { '' }

Write-Host ""
Write-Host "Results Archive Server"
Write-Host "======================"
Write-Host "Serving: $ServeDir"
if ($defaultUrl) {
    Write-Host "URL:     http://localhost:$Port/$defaultUrl"
} else {
    Write-Host "URL:     http://localhost:$Port/  (no HTML file found)"
}
Write-Host ""
Write-Host "Press Ctrl+C to stop."
Write-Host ""

if ($defaultUrl -and -not $NoOpen) {
    Start-Process "http://localhost:$Port/$defaultUrl"
}

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $request = $context.Request
        $response = $context.Response

        $urlPath = $request.Url.LocalPath.TrimStart('/')
        if ($urlPath -eq '' -or $urlPath -eq '/') {
            $urlPath = $defaultUrl
        }

        $filePath = Join-Path $ServeDir $urlPath

        if ($filePath -and (Test-Path $filePath -PathType Leaf)) {
            $ext = [System.IO.Path]::GetExtension($filePath).ToLower()
            $response.ContentType = switch ($ext) {
                '.html'    { 'text/html; charset=utf-8' }
                '.js'      { 'application/javascript; charset=utf-8' }
                '.css'     { 'text/css; charset=utf-8' }
                '.json'    { 'application/json; charset=utf-8' }
                '.parquet' { 'application/octet-stream' }
                default    { 'application/octet-stream' }
            }
            $bytes = [System.IO.File]::ReadAllBytes($filePath)
            $response.ContentLength64 = $bytes.Length
            $response.OutputStream.Write($bytes, 0, $bytes.Length)
            Write-Host "200  $($request.Url.LocalPath)"
        } else {
            $response.StatusCode = 404
            $body = [System.Text.Encoding]::UTF8.GetBytes("Not found: $urlPath")
            $response.ContentLength64 = $body.Length
            $response.OutputStream.Write($body, 0, $body.Length)
            Write-Host "404  $($request.Url.LocalPath)"
        }

        $response.OutputStream.Close()
    }
} finally {
    $listener.Stop()
    Write-Host "Server stopped."
}
