# run_local.ps1
# ==============
# PowerShell launcher for dna-chain-project; same menu as run_local.bat.
# Runs entirely with tools already on your PATH (python, optionally node)
# and does not touch System32 or copy/move any system binary anywhere.
#
# Usage (from a PowerShell prompt, in this project's folder):
#   .\run_local.ps1
#
# If Windows blocks the script with "running scripts is disabled on this
# system", that's PowerShell's execution policy, not this script. Run
# PowerShell as yourself (not admin) and use:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
# then re-run this script. This does not require admin rights.
#
# Scripted use (tests, automation): -Answers takes the menu answers as one
# "|"-separated string, used in order instead of prompting (blank = the
# prompt's default), and -NoPause skips the final "Press Enter" prompt:
#   .\run_local.ps1 -Answers "3|0" -NoPause

param(
    [string]$Answers,
    [switch]$NoPause
)

# Not "Stop": in Windows PowerShell 5.1 a native command writing to stderr
# under "Stop" (e.g. a failing `python -c "import ..." 2>&1`) throws before
# $LASTEXITCODE can be checked. Every native call below checks
# $LASTEXITCODE explicitly instead.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$script:AnswerQueue = [System.Collections.Queue]::new()
if ($PSBoundParameters.ContainsKey("Answers")) {
    foreach ($a in ($Answers -split "\|")) { $script:AnswerQueue.Enqueue($a) }
}

function Read-Answer([string]$prompt) {
    if ($script:AnswerQueue.Count -gt 0) {
        $a = [string]$script:AnswerQueue.Dequeue()
        Write-Host "${prompt}: $a"
        return $a.Trim()
    }
    return (Read-Host $prompt).Trim()
}

function Exit-Launcher([int]$code) {
    Write-Host ""
    if (-not $NoPause) { [void](Read-Host "Press Enter to close") }
    exit $code
}

function Invoke-Python([string[]]$arguments) {
    & python @arguments | Out-Host
    $code = $LASTEXITCODE
    Write-Host "------------------------------------------------------------"
    if ($code -ne 0) {
        Write-Host "[ERROR] python $($arguments[0]) exited with code $code." -ForegroundColor Red
    } else {
        Write-Host "Done."
    }
    return $code
}

Write-Host "============================================================"
Write-Host "  dna-chain-project - local PowerShell launcher"
Write-Host "============================================================"
Write-Host ""

# -- Check Python actually runs (not just the Microsoft Store stub, which
#    Get-Command also finds but which fails or opens the Store) --
$pyVersion = $null
try { $pyVersion = (& python --version 2>&1 | Out-String).Trim() } catch { }
if ($LASTEXITCODE -ne 0 -or -not ($pyVersion -like "Python *")) {
    Write-Host "[MISSING] python was not found on PATH, or only the Microsoft Store" -ForegroundColor Red
    Write-Host "          stub is present. Install it from"
    Write-Host "          https://www.python.org/downloads/windows/"
    Write-Host "          and check 'Add python.exe to PATH' during setup."
    Exit-Launcher 1
}
Write-Host "[OK] $pyVersion" -ForegroundColor Green

# -- Node is optional (only needed for dna_codec.js / interop_client.js) --
$nodeVersion = $null
try { $nodeVersion = (& node --version 2>&1 | Out-String).Trim() } catch { }
if ($LASTEXITCODE -eq 0 -and $nodeVersion -like "v*") {
    Write-Host "[OK] node $nodeVersion" -ForegroundColor Green
} else {
    Write-Host "[OPTIONAL] node not found - dna_codec.js / interop_client.js won't run,"
    Write-Host "           but the Python agents work without it."
}

# -- Required Python packages --
& python -c "import cryptography, requests" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[MISSING] required packages not installed. Installing now..." -ForegroundColor Yellow
    & python -m pip install --upgrade cryptography requests
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] pip install failed - see the output above." -ForegroundColor Red
        Exit-Launcher 1
    }
} else {
    Write-Host "[OK] cryptography, requests already installed" -ForegroundColor Green
}

if (-not $env:ANTHROPIC_API_KEY) {
    Write-Host "[NOTE] ANTHROPIC_API_KEY is not set. Agents that call Claude directly" -ForegroundColor Yellow
    Write-Host "       will run in fallback mode. To set it for this session only:"
    Write-Host '       $env:ANTHROPIC_API_KEY = "sk-ant-..."'
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  What do you want to run?"
Write-Host "============================================================"
Write-Host "  1. run_consolidated_network.py   (3 nodes, one process, localhost test)"
Write-Host "  2. run_node_cli.py               (ONE real node - for real network use)"
Write-Host "  3. Show a node's public key      (to give other nodes for --trust)"
Write-Host "  4. dna_binary_codec.py           (codec self-test only)"
Write-Host "  5. crypto_layer.py               (crypto self-test only)"
Write-Host "  6. Custom - type a filename"
Write-Host "============================================================"
$choice = Read-Answer "Enter 1-6"
$code = 0

switch ($choice) {
    "1" { $code = Invoke-Python @("run_consolidated_network.py") }
    "2" {
        $id = Read-Answer "This node's id (integer, e.g. 0)"
        if (-not $id) { Write-Host "No id given." -ForegroundColor Red; Exit-Launcher 1 }
        $port = Read-Answer "Port to listen on [9601]"
        $bind = Read-Answer "Bind address - 127.0.0.1 local-only, 0.0.0.0 real LAN [127.0.0.1]"
        $peers = Read-Answer "Peers, comma-separated host:port (blank for none)"
        $trust = Read-Answer "Peer keys, comma-separated ID=KEY (blank if already pinned; see option 3)"
        $tofu = Read-Answer "Also trust NEW peers on first contact? y/N"
        $duration = Read-Answer "Run for how many seconds (0 = until Ctrl+C) [0]"
        if (-not $port) { $port = "9601" }
        if (-not $bind) { $bind = "127.0.0.1" }
        if (-not $duration) { $duration = "0" }

        # Built as an array so every answer is its own argument, and blank
        # optional answers are left out rather than passed as empty strings
        # (which Windows PowerShell 5.1 drops, shifting the other arguments).
        $cliArgs = @("run_node_cli.py", "--id", $id, "--port", $port, "--bind", $bind, "--duration", $duration)
        if ($peers) { $cliArgs += @("--peers", $peers) }
        if ($trust) { $cliArgs += @("--trust", $trust) }
        if ($tofu -eq "y" -or $tofu -eq "Y") { $cliArgs += "--tofu" }
        Write-Host ""
        $code = Invoke-Python $cliArgs
    }
    "3" {
        $id = Read-Answer "This node's id (integer, e.g. 0)"
        if (-not $id) { Write-Host "No id given." -ForegroundColor Red; Exit-Launcher 1 }
        Write-Host ""
        $code = Invoke-Python @("run_node_cli.py", "--id", $id, "--show-key")
    }
    "4" { $code = Invoke-Python @("dna_binary_codec.py") }
    "5" { $code = Invoke-Python @("crypto_layer.py") }
    "6" {
        $file = Read-Answer "Filename"
        if ($file -and (Test-Path -LiteralPath $file -PathType Leaf)) {
            $code = Invoke-Python @($file)
        } else {
            Write-Host "[ERROR] '$file' is not a file in $(Get-Location)" -ForegroundColor Red
            $code = 1
        }
    }
    default {
        Write-Host "No valid choice made." -ForegroundColor Red
        $code = 1
    }
}

Exit-Launcher $code
