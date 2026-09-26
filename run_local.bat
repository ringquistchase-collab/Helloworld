@echo off
REM run_local.bat
REM ==============
REM Launches the dna-chain-project agents locally from cmd.exe on Windows.
REM Does NOT copy, move, or touch anything in System32 - it only checks for
REM and calls python/node that are already on your PATH, and runs the
REM project's own scripts from wherever this .bat file lives.
REM
REM Usage: double-click it, or from cmd.exe:  run_local.bat

setlocal
cd /d "%~dp0"

echo ============================================================
echo   dna-chain-project - local Windows launcher
echo ============================================================
echo.

REM -- Check Python actually runs (not just the Microsoft Store stub) --
python --version >nul 2>&1
if errorlevel 1 (
    echo [MISSING] python was not found on PATH, or only the Microsoft Store
    echo           stub is present. Install it from
    echo           https://www.python.org/downloads/windows/
    echo           and make sure "Add python.exe to PATH" is checked during setup.
    goto :end
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] %%v

REM -- Check Node is on PATH (only needed for dna_codec.js / interop_client.js) --
node --version >nul 2>&1
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
    if errorlevel 1 (
        echo [ERROR] pip install failed - see the output above.
        goto :end
    )
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
echo   2. run_node_cli.py               (ONE real node - for real network use)
echo   3. Show a node's public key      (to give other nodes for --trust)
echo   4. dna_binary_codec.py           (codec self-test only)
echo   5. crypto_layer.py               (crypto self-test only)
echo   6. Custom - type a filename
echo ============================================================
set "SCRIPT="
set "choice="
set /p choice="Enter 1-6: "

if "%choice%"=="1" set "SCRIPT=run_consolidated_network.py"
if "%choice%"=="2" goto :run_node
if "%choice%"=="3" goto :show_key
if "%choice%"=="4" set "SCRIPT=dna_binary_codec.py"
if "%choice%"=="5" set "SCRIPT=crypto_layer.py"
if "%choice%"=="6" set /p SCRIPT="Filename: "

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
if errorlevel 1 (
    echo ------------------------------------------------------------
    echo [ERROR] %SCRIPT% exited with code %errorlevel%.
) else (
    echo ------------------------------------------------------------
    echo Done.
)
goto :end

:show_key
set "NODEID="
set /p NODEID="This node's id (integer, e.g. 0): "
if not defined NODEID (
    echo No id given.
    goto :end
)
echo.
python run_node_cli.py --id "%NODEID%" --show-key
goto :end

:run_node
REM Every answer is passed to Python as its own quoted argument, so spaces
REM or & in what's typed can't split or inject into the command.
set "NODEID="
set "NODEPORT="
set "NODEBIND="
set "NODEPEERS="
set "NODETRUST="
set "NODETOFU="
set "NODEDURATION="
set /p NODEID="This node's id (integer, e.g. 0): "
if not defined NODEID (
    echo No id given.
    goto :end
)
set /p NODEPORT="Port to listen on [9601]: "
set /p NODEBIND="Bind address - 127.0.0.1 local-only, 0.0.0.0 real LAN [127.0.0.1]: "
set /p NODEPEERS="Peers, comma-separated host:port (blank for none): "
set /p NODETRUST="Peer keys, comma-separated ID=KEY (blank if already pinned; see option 3): "
set /p NODETOFU="Also trust NEW peers on first contact? y/N: "
set /p NODEDURATION="Run for how many seconds (0 = until Ctrl+C) [0]: "
if not defined NODEPORT set "NODEPORT=9601"
if not defined NODEBIND set "NODEBIND=127.0.0.1"
if not defined NODEDURATION set "NODEDURATION=0"

set NODEARGS=--id "%NODEID%" --port "%NODEPORT%" --bind "%NODEBIND%" --duration "%NODEDURATION%"
if defined NODEPEERS set NODEARGS=%NODEARGS% --peers "%NODEPEERS%"
if defined NODETRUST set NODEARGS=%NODEARGS% --trust "%NODETRUST%"
if /i "%NODETOFU%"=="y" set NODEARGS=%NODEARGS% --tofu
echo.
python run_node_cli.py %NODEARGS%
if errorlevel 1 echo [ERROR] run_node_cli.py exited with an error.
goto :end

:end
echo.
pause
endlocal
