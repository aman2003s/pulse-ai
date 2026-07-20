@echo off
cd /d "%~dp0"

rem Force offline: all models are local; no network calls at runtime
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1

rem ── Step 1: Start backend only if not already running ────────────────────────
powershell -NoProfile -Command ^
  "$t=New-Object Net.Sockets.TcpClient;try{$t.Connect('127.0.0.1',7549);$t.Close();exit 0}catch{exit 1}" ^
  >nul 2>&1

if %errorlevel% == 0 (
    echo [Pulse] Backend already running.
    goto launch_ui
)

echo [Pulse] Starting backend...
start "Pulse Core" /MIN venv\Scripts\python.exe pulse.py %*

rem ── Step 2: Wait until WebSocket port 7550 is open (backend is ready) ────────
echo [Pulse] Waiting for backend to be ready...
:wait_loop
    timeout /t 1 /nobreak >nul
    powershell -NoProfile -Command ^
      "$t=New-Object Net.Sockets.TcpClient;try{$t.Connect('127.0.0.1',7550);$t.Close();exit 0}catch{exit 1}" ^
      >nul 2>&1
    if %errorlevel% == 0 goto backend_ready
goto wait_loop

:backend_ready
echo [Pulse] Backend ready.

rem ── Step 3: Launch UI ─────────────────────────────────────────────────────────
:launch_ui
echo [Pulse] Launching UI...
start "" ui\pulse-ui.exe
