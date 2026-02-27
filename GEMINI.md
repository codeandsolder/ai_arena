# AI Optimization Arena

## Project Overview

The **AI Optimization Arena** is a full-stack platform designed to facilitate competitive programming optimization among AI models. It orchestrates rounds where different Large Language Models (LLMs) compete to generate the fastest, most memory-efficient C++ solutions to algorithmic problems.

The system features a real-time dashboard, sandboxed execution environment for safety, and detailed performance analytics.

## Tech Stack

*   **Backend:** Python 3.10+, FastAPI, SQLAlchemy (Async SQLite), Uvicorn
*   **Frontend:** React 18+, Vite, Tailwind CSS, Recharts
*   **Infrastructure:** Docker (for sandboxed code execution)
*   **AI Integration:** OpenRouter API (for accessing various LLMs)
*   **Package Management:** `uv` (Python), `npm` (Node.js)

## Building and Running

### Prerequisites
*   Python 3.10+
*   Node.js 18+
*   Docker Desktop (must be running)
*   OpenRouter API Key

### Backend Setup

The backend uses `uv` for dependency management.

```bash
# Install dependencies
uv sync

# Activate virtual environment
# Windows: .venv\Scripts\activate
# Unix: source .venv/bin/activate

# Set API Key
# Windows: set OPENROUTER_API_KEY=your_key
# Unix: export OPENROUTER_API_KEY="your_key"

# Run the server
uv run uvicorn backend.main:app --reload --port 8000
```

### Frontend Setup

```bash
cd frontend
npm install
npm run dev
```
The frontend will be available at `http://localhost:5173`.

### Docker Sandbox

The sandbox image is required for compiling and running C++ solutions.

```bash
docker build -f docker/Dockerfile.sandbox -t arena-sandbox .
```

### Testing

*   **Backend Integration Tests:** `python scripts/test_backend.py`
*   **End-to-End Tests:**
    *   Windows: `scripts\test_e2e.bat`
    *   Unix: `./scripts/test_e2e.sh`

## Architecture

1.  **Orchestrator:** Manages the competition loop (Generation -> Compilation -> Benchmarking -> Judging).
2.  **Sandbox:** A Dockerized environment (`arena-sandbox`) that compiles and runs C++ code securely, enforcing time and memory limits.
3.  **API:** Exposes REST endpoints for managing problems/runs and WebSockets for real-time status updates.
4.  **Database:** Async SQLite database stores problems, runs, solutions, and benchmark results.

## Development Conventions

*   **Python:** Follows PEP 8. formatted with `black` and `ruff`. Type checking with `mypy`.
*   **Frontend:** React functional components with hooks. Styling via Tailwind CSS.
*   **Commits:** Use clear, descriptive commit messages.
