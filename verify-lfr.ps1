# Reports the LFR GPX store state for tour-de-romandie-2026 and writes it to
# lfr-verify.log (which the assistant can read). Inherits TURSO_* from the
# terminal you launch it from.
#   powershell -ExecutionPolicy Bypass -File .\verify-lfr.ps1

$root = $PSScriptRoot
$log  = Join-Path $root "lfr-verify.log"
Push-Location (Join-Path $root "scrapers")
try {
    python verify_lfr_store.py *>&1 | Tee-Object -FilePath $log
}
finally {
    Pop-Location
}
