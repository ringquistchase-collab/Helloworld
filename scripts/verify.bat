@echo off
:: verify.bat
:: Local equivalent of .github/workflows/verify.yml, runnable from a
:: plain cmd.exe prompt. Batch has no reliable native way to track and
:: clean up a background process's PID (needed for the interop smoke
:: test's start-a-node-then-kill-it step), so this delegates to
:: verify.ps1 via PowerShell rather than risk a fragile hand-rolled
:: batch implementation of the same process-management logic a third
:: time -- CMD is just the entry point here, same idea as
:: double-clicking a .bat file that launches something more capable
:: underneath. PowerShell 5.1+ ships by default on all supported
:: Windows versions, so this isn't an extra dependency.
::
:: Run from anywhere: scripts\verify.bat

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify.ps1"
exit /b %errorlevel%
