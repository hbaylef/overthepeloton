# Convenience wrapper to run the LFR GPX scraper without typing a long command.
# Local dev helper — see scrapers/scrape_lfr.py.
#
#   .\run-lfr.ps1                          # DRY-RUN all upcoming races (no download)
#   .\run-lfr.ps1 -Real                    # REAL bulk run (downloads GPX into Turso)
#   .\run-lfr.ps1 -Slug tour-de-suisse-2026 -Real   # one race only
#   .\run-lfr.ps1 -Dump                    # also save fetched HTML (debugging)
#
# By default it targets all upcoming WT+ProSeries races (starts tomorrow onward).
# Inherits the TURSO_* env vars from the terminal you launch it from.

param(
    [string]$Slug = "",
    [switch]$Real,
    [switch]$Dump
)

$pyArgs = @("scrapers/scrape_lfr.py")
if ($Slug) { $pyArgs += @("--only", $Slug) }
if ($Dump) { $pyArgs += "--dump-html" }
if (-not $Real) { $pyArgs += "--dry-run" }

$log = Join-Path $PSScriptRoot "lfr-run.log"
Write-Host "Running: python $($pyArgs -join ' ')" -ForegroundColor Cyan
Write-Host "Output also being written to $log" -ForegroundColor Cyan
# Tee to a log file so the run can be inspected without copy-pasting the console.
python @pyArgs 2>&1 | Tee-Object -FilePath $log
