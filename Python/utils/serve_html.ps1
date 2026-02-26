# EmotiView HTML Archive Server
# Run this to serve the interactive HTML archive over HTTP.
# Then open: http://localhost:8080/EV_procedure_interactive.html

param(
    [string]$ServeDir = '',
    [int]$Port = 8080
)

if (-not $ServeDir) {
    # Search upward from the current working directory for an EV_analysis folder
    $search = Get-Location
    while ($search) {
        $candidate = Join-Path $search "EV_analysis"
        if (Test-Path $candidate) { $ServeDir = $candidate; break }
        $parent = Split-Path $search -Parent
        if ($parent -eq $search) { break }
        $search = $parent
    }
}

if (-not $ServeDir -or -not (Test-Path $ServeDir)) {
    Write-Error "Could not find EV_analysis folder. Pass -ServeDir <path> explicitly."
    exit 1
}

Write-Host ""
Write-Host "EmotiView Archive Server"
Write-Host "========================"
Write-Host "Serving: $ServeDir"
Write-Host "URL:     http://localhost:$Port/EV_procedure_interactive.html"
Write-Host ""
Write-Host "Press Ctrl+C to stop."
Write-Host ""

# Open browser automatically
Start-Process "http://localhost:$Port/EV_procedure_interactive.html"

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add("http://localhost:$Port/")
$listener.Start()

try {
    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $request = $context.Request
        $response = $context.Response

        # Map URL to file
        $urlPath = $request.Url.LocalPath.TrimStart('/')
        if ($urlPath -eq '' -or $urlPath -eq '/') {
            $urlPath = 'EV_procedure_interactive.html'
        }

        $filePath = Join-Path $ServeDir $urlPath

        if (Test-Path $filePath -PathType Leaf) {
            $ext = [System.IO.Path]::GetExtension($filePath).ToLower()
            $response.ContentType = switch ($ext) {
                '.html' { 'text/html; charset=utf-8' }
                '.js'   { 'application/javascript; charset=utf-8' }
                '.css'  { 'text/css; charset=utf-8' }
                '.json' { 'application/json; charset=utf-8' }
                default { 'application/octet-stream' }
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
