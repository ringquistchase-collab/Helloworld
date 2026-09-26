@echo off
REM run_local.bat
REM ==============
REM Launches the dna-chain-project agents locally from cmd.exe on Windows.
REM Does NOT copy, move, or touch anything in System32 — it only checks for
REM and calls python/node that are already on your PATH, and runs the
REM project's own scripts from wherever this .bat file lives.
REM
REM Usage: double-click it, or from cmd.exe:  run_local.bat

setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   dna-chain-project - local Windows launcher
echo ============================================================
echo.

REM -- Check Python is on PATH --
where python >nul 2>&1
if errorlevel 1 (
    echo [MISSING] python was not found on PATH.
    echo           Install it from https://www.python.org/downloads/windows/
    echo           and make sure "Add python.exe to PATH" is checked during setup.
    goto :end
) else (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v
)

REM -- Check Node is on PATH (only needed for dna_codec.js / interop_client.js) --
where node >nul 2>&1
if errorlevel 1 (
    echo [OPTIONAL] node was not found on PATH - dna_codec.js / interop_client.js
    echo            will not run, but the Python agents work without it.
) else (
    for /f "tokens=*" %%v in ('node --version 2^>^&1') do echo [OK] node %%v
)

REM -- Check required Python packages --
python -c "import cryptography, requests" >nul 2>&1
if errorlevel 1 (
    echo [MISSING] required packages not installed. Installing now...
    python -m pip install --upgrade cryptography requests
) else (
    echo [OK] cryptography, requests already installed
)

REM -- Optional: warn if ANTHROPIC_API_KEY isn't set (only needed by
REM    claude_agent_hook.py / research_summarizer.py, not by the core network) --
if "%ANTHROPIC_API_KEY%"=="" (
    echo [NOTE] ANTHROPIC_API_KEY is not set in this session. Agents that call
    echo        Claude directly will run in fallback mode. To set it for this
    echo        cmd window only:  set ANTHROPIC_API_KEY=sk-ant-...
)

echo.
echo ============================================================
echo   Which script to run?
echo ============================================================
echo   1. run_consolidated_network.py   (3 nodes, one process, localhost test)
echo   2. run_node_cli.py                (ONE real node - for real network use)
echo   3. dna_binary_codec.py            (codec self-test only)
echo   4. crypto_layer.py                (crypto self-test only)
echo   5. Custom - type a filename
echo ============================================================
set /p choice="Enter 1-5: "

if "%choice%"=="1" set SCRIPT=run_consolidated_network.py
if "%choice%"=="2" goto :run_node
if "%choice%"=="3" set SCRIPT=dna_binary_codec.py
if "%choice%"=="4" set SCRIPT=crypto_layer.py
if "%choice%"=="5" set /p SCRIPT="Filename: "

if not defined SCRIPT (
    echo No valid choice made.
    goto :end
)

if not exist "%SCRIPT%" (
    echo [ERROR] %SCRIPT% not found in %cd%
    goto :end
)

echo.
echo Running %SCRIPT% ...
echo ------------------------------------------------------------
python "%SCRIPT%"
echo ------------------------------------------------------------
echo Done.
goto :end

:run_node
set /p NODEID="This node's id (integer, e.g. 0): "
set /p NODEPORT="Port to listen on (e.g. 9601): "
set /p NODEBIND="Bind address (127.0.0.1 for local-only test, 0.0.0.0 for real LAN): "
set /p NODEPEERS="Peers, comma-separated host:port (leave blank for none): "
set /p NODEDURATION="Run for how many seconds (0 = run until Ctrl+C): "
echo.
if "%NODEPEERS%"=="" (
    python run_node_cli.py --id %NODEID% --port %NODEPORT% --bind %NODEBIND% --duration %NODEDURATION%
) else (
    python run_node_cli.py --id %NODEID% --port %NODEPORT% --bind %NODEBIND% --peers %NODEPEERS% --duration %NODEDURATION%
)
goto :end

:end
echo.
pause
endlocal
