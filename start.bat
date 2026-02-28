@echo off
setlocal
cd /d "%~dp0"
echo Starting Arena Launcher...
uv run python launcher.py
pause
