@echo off
cd /d "%~dp0"

rem Force offline: all models are local after first-time setup; no network calls at runtime
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1

rem ── Single-instance guard for the backend ────────────────────────────────────
rem Try to connect to port 7549 (the lock port pulse.py holds).
rem If it succeeds, the backend is already running — skip launching it.
rem If it fails, launch the backend fresh.
powershell -NoProfile -Command ^
  "$t = New-Object Net.Sockets.TcpClient; " ^
  "try { $t.Connect('127.0.0.1', 7549); $t.Close(); exit 0 } catch { exit 1 }" ^
  >nul 2>&1
if %errorlevel% == 0 (
    echo Pulse backend already running - skipping relaunch.
) else (
    echo Starting Pulse backend...
    start "Pulse Core" /MIN venv\Scripts\python.exe pulse.py %*
)

rem ── Always open / bring forward the UI ───────────────────────────────────────
rem The UI auto-connects once the backend is ready, so it's safe to open it
rem even if the backend is still booting. It will show "Starting up..." until connected.
start "" ui\pulse-ui.exe
