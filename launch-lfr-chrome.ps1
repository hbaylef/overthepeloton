# Launches a dedicated Chrome instance with remote debugging for the LFR
# (La Flamme Rouge) GPX harvest. Local dev helper — see scrapers/scrape_lfr.py.
# Run with:  powershell -ExecutionPolicy Bypass -File .\launch-lfr-chrome.ps1

$paths = @(
    "C:\Program Files\Google\Chrome\Application\chrome.exe",
    "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
)

$chrome = $paths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) {
    Write-Host "Chrome not found in the usual locations. Edit this script with your path." -ForegroundColor Red
    exit 1
}

Write-Host "Launching Chrome (debug) from: $chrome" -ForegroundColor Cyan
Start-Process $chrome -ArgumentList @(
    "--remote-debugging-port=9222",
    "--user-data-dir=C:\lfr-profile"
)

Write-Host ""
Write-Host "A separate Chrome window should now be open." -ForegroundColor Green
Write-Host "  1. In it, go to: https://www.la-flamme-rouge.eu"
Write-Host "  2. Clear the Cloudflare 'Just a moment...' check if it appears."
Write-Host "  3. Leave the window open, then come back here."
Write-Host ""
Write-Host "Verify the debug port with:  curl http://localhost:9222/json/version"
