#!/bin/bash
# End-to-End Test Script for AI Optimization Arena
# This script starts the backend, runs API tests, and verifies Docker setup

set -e

echo "============================================"
echo "AI Optimization Arena - End-to-End Test"
echo "============================================"
echo

# Check Python
if ! command -v python &> /dev/null; then
    echo "[ERROR] Python is not installed or not in PATH"
    exit 1
fi
echo "[OK] Python found: $(python --version)"

# Check Node.js
if command -v node &> /dev/null; then
    echo "[OK] Node.js found: $(node --version)"
    FRONTEND_AVAILABLE=1
else
    echo "[WARNING] Node.js not found - frontend tests will be skipped"
    FRONTEND_AVAILABLE=0
fi

# Check Docker
if command -v docker &> /dev/null; then
    echo "[OK] Docker found: $(docker --version)"
    DOCKER_AVAILABLE=1
else
    echo "[WARNING] Docker is not installed or not running"
    DOCKER_AVAILABLE=0
fi

# Install test dependencies
echo
echo "Installing test dependencies..."
uv add --dev aiohttp websockets || echo "[WARNING] Failed to install some test dependencies"

# Start backend in background
echo
echo "Starting backend server..."
uv run uvicorn backend.main:app --reload --port 8000 &
BACKEND_PID=$!
echo "[OK] Backend starting on http://localhost:8000 (PID: $BACKEND_PID)"

# Function to cleanup
cleanup() {
    echo
    echo "============================================"
    echo "Cleaning up"
    echo "============================================"
    
    if kill -0 $BACKEND_PID 2>/dev/null; then
        kill $BACKEND_PID 2>/dev/null || true
        wait $BACKEND_PID 2>/dev/null || true
    fi
}
trap cleanup EXIT

# Wait for backend to start
echo
echo "Waiting for backend to initialize (10 seconds)..."
sleep 10

# Test backend health
echo
echo "Testing backend health..."
if ! curl -s http://localhost:8000/ > /dev/null; then
    echo "[WARNING] Backend health check failed, waiting 5 more seconds..."
    sleep 5
    if ! curl -s http://localhost:8000/ > /dev/null; then
        echo "[ERROR] Backend is not responding"
        exit 1
    fi
fi
echo "[OK] Backend is responding"

# Run backend API tests
echo
echo "============================================"
echo "Running Backend API Tests"
echo "============================================"
uv run python scripts/test_backend.py --base-url http://localhost:8000
TEST_RESULT=$?

# Build Docker image if available
DOCKER_BUILD=0
if [ $DOCKER_AVAILABLE -eq 1 ]; then
    echo
    echo "============================================"
    echo "Building Docker Image"
    echo "============================================"
    if docker build -f docker/Dockerfile.sandbox -t arena-sandbox .; then
        echo "[OK] Docker image built successfully"
        DOCKER_BUILD=1
    else
        echo "[WARNING] Docker build failed"
    fi
else
    echo "[SKIP] Docker not available, skipping image build"
fi

# Report results
echo
echo "============================================"
echo "Test Results"
echo "============================================"

if [ $TEST_RESULT -eq 0 ]; then
    echo "[PASS] Backend API tests passed"
else
    echo "[FAIL] Backend API tests failed"
fi

if [ $DOCKER_BUILD -eq 1 ]; then
    echo "[PASS] Docker image built successfully"
elif [ $DOCKER_AVAILABLE -eq 1 ]; then
    echo "[FAIL] Docker image build failed"
else
    echo "[SKIP] Docker image build skipped"
fi

echo
echo "============================================"

if [ $TEST_RESULT -eq 0 ]; then
    echo "All critical tests passed!"
    exit 0
else
    echo "Some tests failed. Check the output above for details."
    exit 1
fi
