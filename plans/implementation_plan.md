# Implementation Plan: AI Optimization Arena

## Phase 1: Backend Data Layer
- **Goal**: Establish the database schema and persistence logic.
- **Tasks**:
    - Create `backend/database/models.py` with SQLAlchemy models: `Problem`, `Run`, `Round`, `Solution`, `TestResult`, `ApiCall`.
    - Implement `backend/database/session.py` for engine and session management.
    - Implement `backend/database/migrations.py` for automatic table creation.
    - Set up `backend/config.py` for environment variables and paths.

## Phase 2: Docker Sandbox
- **Goal**: Create a secure, isolated environment for running C++ code.
- **Tasks**:
    - Write `docker/Dockerfile.sandbox` with necessary compilers (`g++`, `clang`).
    - Develop `docker/test_harness.cpp` for execution and measurement.
    - Implement `backend/sandbox/compiler.py` with whitelist and safety checks.
    - Implement `backend/sandbox/container.py` using `docker-py`.
    - Implement `backend/sandbox/benchmark.py` to coordinate test runs.

## Phase 3: Backend API
- **Goal**: Build the REST and WebSocket interfaces.
- **Tasks**:
    - Implement `backend/api/problems.py` (CRUD + test case upload).
    - Implement `backend/api/runs.py` (CRUD + execution control).
    - Implement `backend/api/rounds.py` and `backend/api/solutions.py`.
    - Implement `backend/api/websocket.py` for real-time updates.
    - Create `backend/main.py` to mount routers and handle startup.

## Phase 4: Orchestration Engine
- **Goal**: Implement the core logic for the optimization loop.
- **Tasks**:
    - Implement `backend/orchestrator/model_client.py` for OpenRouter integration.
    - Define `backend/orchestrator/prompts.py` with default templates.
    - Implement `backend/orchestrator/engine.py` with the main round loop.
    - Implement `backend/orchestrator/summarizer.py` for the judge logic.

## Phase 5: Frontend Development
- **Goal**: Create the user interface.
- **Tasks**:
    - Set up Vite + React + Tailwind project in `frontend/`.
    - Implement `api.js` for backend communication.
    - Build pages: `RunList`, `RunConfig`, `RunDashboard`, `RoundDetail`, `SolutionDetail`, `ProblemManager`.
    - Build components: `CodeViewer`, `ScoreBoard`, `BenchmarkChart`, `StatusBadge`.

## Phase 6: Integration & Testing
- **Goal**: Ensure all components work together.
- **Tasks**:
    - Verify Docker image build and connectivity.
    - Test full run flow with mock/real LLM calls.
    - Finalize styling and error handling.
