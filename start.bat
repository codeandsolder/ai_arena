@echo off
setlocal

echo ==========================================
echo    AI Optimization Arena - Startup
echo ==========================================

:: Navigate to project root
cd /d "%~dp0"

:: Start Backend
echo Starting Backend...
start "Arena Backend" cmd /k "if exist .venv\Scripts\activate.bat (call .venv\Scripts\activate.bat) else if exist venv\Scripts\activate.bat (call venv\Scripts\activate.bat) & python -m backend.main"

:: Wait for backend to initialize
timeout /t 3 /nobreak > nul

:: Start Frontend
echo Starting Frontend...
cd frontend
start "Arena Frontend" cmd /k "npm run dev"

:: Wait for frontend to start
timeout /t 5 /nobreak > nul

:: Open Browser
echo Opening browser...
start http://localhost:5173

echo.
echo All components are starting!
echo - Backend: http://localhost:8000
echo - Frontend: http://localhost:5173
echo.
echo Close the terminal windows to stop the services.
pause
