@echo off
REM End-to-End Test Script for AI Optimization Arena
REM This script starts the backend, runs API tests, and verifies Docker setup

echo ============================================
echo AI Optimization Arena - End-to-End Test
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH
    exit /b 1
)
echo [OK] Python found

REM Check Node.js
node --version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Node.js not found - frontend tests will be skipped
    set FRONTEND_AVAILABLE=0
) else (
    echo [OK] Node.js found
    set FRONTEND_AVAILABLE=1
)

REM Check Docker
docker --version >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Docker is not installed or not running
    set DOCKER_AVAILABLE=0
) else (
    echo [OK] Docker found
    set DOCKER_AVAILABLE=1
)

REM Install test dependencies
echo.
echo Installing test dependencies...
uv add --dev aiohttp websockets
if errorlevel 1 (
    echo [WARNING] Failed to install test dependencies
)

REM Start backend in background
echo.
echo Starting backend server...
start "Backend Server" cmd /c "uv run uvicorn backend.main:app --reload --port 8000"
if errorlevel 1 (
    echo [ERROR] Failed to start backend
    exit /b 1
)
echo [OK] Backend starting on http://localhost:8000

REM Wait for backend to start
echo.
echo Waiting for backend to initialize (10 seconds)...
timeout /t 10 /nobreak >nul

REM Test backend health
echo.
echo Testing backend health...
curl -s http://localhost:8000/ >nul 2>&1
if errorlevel 1 (
    echo [WARNING] Backend health check failed, waiting 5 more seconds...
    timeout /t 5 /nobreak >nul
    curl -s http://localhost:8000/ >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Backend is not responding
        goto cleanup
    )
)
echo [OK] Backend is responding

REM Run backend API tests
echo.
echo ============================================
echo Running Backend API Tests
echo ============================================
uv run python scripts/test_backend.py --base-url http://localhost:8000
set TEST_RESULT=%ERRORLEVEL%

REM Build Docker image if available
if %DOCKER_AVAILABLE%==1 (
    echo.
    echo ============================================
    echo Building Docker Image
echo ============================================
    docker build -f docker/Dockerfile.sandbox -t arena-sandbox .
    if errorlevel 1 (
        echo [WARNING] Docker build failed
        set DOCKER_BUILD=0
    ) else (
        echo [OK] Docker image built successfully
        set DOCKER_BUILD=1
    )
) else (
    echo [SKIP] Docker not available, skipping image build
    set DOCKER_BUILD=0
)

REM Cleanup
echo.
:cleanup
echo ============================================
echo Cleaning up
echo ============================================

REM Stop backend
taskkill /F /FI "WINDOWTITLE eq Backend Server" >nul 2>&1
taskkill /F /IM uvicorn.exe >nul 2>&1

REM Report results
echo.
echo ============================================
echo Test Results
echo ============================================
if %TEST_RESULT%==0 (
    echo [PASS] Backend API tests passed
) else (
    echo [FAIL] Backend API tests failed
)

if %DOCKER_BUILD%==1 (
    echo [PASS] Docker image built successfully
) else if %DOCKER_AVAILABLE%==1 (
    echo [FAIL] Docker image build failed
) else (
    echo [SKIP] Docker image build skipped
)

echo.
echo ============================================
if %TEST_RESULT%==0 (
    echo All critical tests passed!
    exit /b 0
) else (
    echo Some tests failed. Check the output above for details.
    exit /b 1
)
