# run_local.ps1
# ==============
# PowerShell launcher for dna-chain-project. Runs entirely with tools
# already on your PATH (python, optionally node) — does not touch
# System32 or copy/move any system binary anywhere.
#
# Usage (from a PowerShell prompt, in this project's folder):
#   .\run_local.ps1
#
# If Windows blocks the script with "running scripts is disabled on this
# system", that's PowerShell's execution policy, not this script — run
# PowerShell as yourself (not admin) and use:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# then re-run this script. This does not require admin rights.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "============================================================"
Write-Host "  dna-chain-project - local PowerShell launcher"
Write-Host "============================================================"
Write-Host ""

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

if (-not (Test-Command "python")) {
    Write-Host "[MISSING] python was not found on PATH." -ForegroundColor Red
    Write-Host "          Install it from https://www.python.org/downloads/windows/"
    Write-Host "          and check 'Add python.exe to PATH' during setup."
    exit 1
} else {
    $pyVersion = (python --version) 2>&1
    Write-Host "[OK] $pyVersion" -ForegroundColor Green
}

if (Test-Command "node") {
    $nodeVersion = (node --version) 2>&1
    Write-Host "[OK] node $nodeVersion" -ForegroundColor Green
} else {
    Write-Host "[OPTIONAL] node not found - dna_codec.js won't run, Python agents still work."
}

Write-Host ""
Write-Host "Checking Python dependencies..."
$depCheck = python -c "import cryptography, requests" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing cryptography + requests..."
    python -m pip install --upgrade cryptography requests
} else {
    Write-Host "[OK] cryptography, requests already installed" -ForegroundColor Green
}

if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host ""
    Write-Host "[NOTE] ANTHROPIC_API_KEY is not set. Agents that call Claude directly" -ForegroundColor Yellow
    Write-Host "       will run in fallback mode. To set it for this session only:"
    Write-Host '       $env:ANTHROPIC_API_KEY = "sk-ant-..."'
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  What do you want to run?"
Write-Host "============================================================"
Write-Host "  1. run_consolidated_network.py   (3 nodes, one process, localhost test)"
Write-Host "  2. run_node_cli.py                (ONE real node - for real network use)"
Write-Host "  3. dna_binary_codec.py            (codec self-test)"
Write-Host "  4. crypto_layer.py                (crypto self-test)"
Write-Host "  5. Custom - type a filename"
Write-Host "============================================================"
$choice = Read-Host "Enter 1-5"

switch ($choice) {
    "1" { python run_consolidated_network.py }
    "2" {
        $id = Read-Host "This node's id (integer, e.g. 0)"
        $port = Read-Host "Port to listen on (e.g. 9601)"
        $bind = Read-Host "Bind address (127.0.0.1 for local-only test, 0.0.0.0 for real LAN)"
        $peers = Read-Host "Peers, comma-separated host:port (e.g. 192.168.1.20:9601,192.168.1.21:9601) - leave blank for none"
        $duration = Read-Host "Run for how many seconds (0 = run until Ctrl+C)"
        if ($peers) {
            python run_node_cli.py --id $id --port $port --bind $bind --peers $peers --duration $duration
        } else {
            python run_node_cli.py --id $id --port $port --bind $bind --duration $duration
        }
    }
    "3" { python dna_binary_codec.py }
    "4" { python crypto_layer.py }
    "5" {
        $script = Read-Host "Filename"
        if (Test-Path $script) {
            python $script
        } else {
            Write-Host "[ERROR] $script not found in $(Get-Location)" -ForegroundColor Red
        }
    }
    default { Write-Host "No valid choice made." -ForegroundColor Red }
}

Write-Host ""
Read-Host "Press Enter to close"
