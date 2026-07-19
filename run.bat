@echo off
cd /d "%~dp0"
rem Force offline: all models are local after first-time setup; no network calls at runtime
set HF_HUB_OFFLINE=1
set TRANSFORMERS_OFFLINE=1
venv\Scripts\python.exe pulse.py %*
