# verify.ps1
# Local equivalent of .github/workflows/verify.yml -- runs the same
# checks CI runs (dependency install, project_identifier.py, pytest,
# a real interop smoke test), without needing to push and wait for
# GitHub Actions.
#
# Run from anywhere:  powershell -File scripts\verify.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "=== Installing dependencies ===" -ForegroundColor Cyan
py -3 -m pip install -q -r requirements.txt

Write-Host "`n=== project_identifier.py ===" -ForegroundColor Cyan
py -3 project_identifier.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== pytest ===" -ForegroundColor Cyan
py -3 -m pytest tests/ -v
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== Interop smoke test (Python client against a live node) ===" -ForegroundColor Cyan
$nodeProcess = $null
$interopExit = 1
try {
    $nodeProcess = Start-Process -FilePath "py" `
        -ArgumentList "-3", "scripts\local_verify_node.py" `
        -PassThru -NoNewWindow -WorkingDirectory $root
    Start-Sleep -Seconds 2

    py -3 interop_client.py 127.0.0.1 18765
    $interopExit = $LASTEXITCODE
}
finally {
    if ($nodeProcess -and -not $nodeProcess.HasExited) {
        Stop-Process -Id $nodeProcess.Id -Force -ErrorAction SilentlyContinue
    }
    Remove-Item -Path (Join-Path $root "local_verify_node.dna.json") -ErrorAction SilentlyContinue
}

if ($interopExit -ne 0) {
    Write-Host "Interop smoke test FAILED (exit $interopExit)" -ForegroundColor Red
    exit $interopExit
}

Write-Host "`n=== All checks passed ===" -ForegroundColor Green
