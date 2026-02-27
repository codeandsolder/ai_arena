# AI Optimization Arena

A full-stack platform for competitive AI programming optimization. Models compete to generate the fastest, most memory-efficient C++ solutions to algorithmic problems.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109+-green.svg)
![React](https://img.shields.io/badge/React-18+-61DAFB.svg)
![Docker](https://img.shields.io/badge/Docker-Required-blue.svg)

## Features

- 🏆 **Competitive Optimization**: AI models compete in rounds to produce optimal C++ solutions
- 📊 **Real-time Dashboard**: WebSocket-powered live updates during runs
- 🔒 **Sandboxed Execution**: Docker-based secure compilation and benchmarking
- 📈 **Performance Analytics**: Detailed metrics on speed, memory, and correctness
- 🧑‍⚖️ **Judge Model**: AI-powered analysis and summary of solution quality

## Prerequisites

- **Python 3.10+**
- **uv** (modern Python package manager) - [Install uv](https://docs.astral.sh/uv/getting-started/installation/)
- **Node.js 18+**
- **Docker Desktop** (for sandboxed code execution)
- **OpenRouter API Key** (for AI model access)

## Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd ai-optimization-arena
```

### 2. Backend Setup

Using **uv** (recommended):

```bash
# Install dependencies and create virtual environment
uv sync

# Activate the virtual environment
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Set environment variable for OpenRouter
export OPENROUTER_API_KEY="your-api-key-here"  # On Windows: set OPENROUTER_API_KEY=your-api-key-here
```

Or using pip (legacy):

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e .
```

### 3. Frontend Setup

```bash
cd frontend
npm install
cd ..
```

### 4. Build Docker Sandbox Image

```bash
docker build -f docker/Dockerfile.sandbox -t arena-sandbox .
```

### 5. Start the Application

**Terminal 1 - Backend (using uv):**
```bash
# From project root (uv will use the virtual environment automatically)
uv run uvicorn backend.main:app --reload --port 8000
```

Or after activating the virtual environment:
```bash
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
cd backend
uvicorn main:app --reload --port 8000
```

**Terminal 2 - Frontend:**
```bash
cd frontend
npm run dev
```

The application will be available at:
- **Frontend**: http://localhost:5173
- **Backend API**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs (Swagger UI)

## Usage Guide

### 1. Create a Problem

1. Open the frontend at http://localhost:5173
2. Navigate to "Problems" section
3. Click "Create Problem"
4. Fill in the details:
   - **Name**: Display name for the problem
   - **Slug**: URL-friendly identifier (e.g., `sum-of-two-numbers`)
   - **Description**: Markdown-formatted problem statement
   - **Time Limit**: Maximum execution time per test (ms)
   - **Memory Limit**: Maximum memory usage (MB)
5. Upload test cases as a ZIP file containing `.in` and `.out` file pairs

### 2. Configure a Run

1. Navigate to "Runs" section
2. Click "Create Run"
3. Select a problem
4. Configure models:
   - Enable/disable models
   - Set temperature and max tokens
5. Set judge model for summarization
6. Adjust scoring weights (correctness, speed, memory)
7. Save the configuration

### 3. Start the Competition

1. From the Run detail page, click "Start Run"
2. Specify the number of rounds to execute
3. Watch real-time updates via WebSocket
4. Monitor:
   - Solution generation
   - Compilation results
   - Benchmark scores
   - Round summaries

### 4. View Results

- **Dashboard**: Overall run status and leaderboard
- **Round Details**: Per-round breakdown with solution code
- **API Call Log**: LLM API usage and costs
- **Solutions**: Individual solution analysis

## API Documentation

The backend provides a comprehensive REST API documented with Swagger UI:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc
- **OpenAPI JSON**: http://localhost:8000/openapi.json

### Key Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/problems/` | GET, POST | List/create problems |
| `/api/v1/problems/{id}` | GET, PUT, DELETE | Problem CRUD |
| `/api/v1/problems/{id}/tests` | POST | Upload test cases |
| `/api/v1/runs/` | GET, POST | List/create runs |
| `/api/v1/runs/{id}/start` | POST | Start a run |
| `/api/v1/runs/{id}/pause` | POST | Pause a run |
| `/api/v1/ws/runs/{id}` | WS | WebSocket for real-time updates |

## Testing

### Run Integration Tests

```bash
# Make sure backend is running, then:
python scripts/test_backend.py
```

### Run End-to-End Tests

**Windows:**
```bash
scripts\test_e2e.bat
```

**Linux/Mac:**
```bash
chmod +x scripts/test_e2e.sh
./scripts/test_e2e.sh
```

### Manual Testing with Sample Problem

A sample problem is provided in the `sample_problem/` directory:

```bash
# Upload the sample problem via API
curl -X POST http://localhost:8000/api/v1/problems/ \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Sum of Two Numbers",
    "slug": "sum-two-numbers",
    "description_md": "Read two integers and print their sum.",
    "time_limit_ms": 1000,
    "memory_limit_mb": 256
  }'

# Upload test cases (assuming problem ID is 1)
curl -X POST http://localhost:8000/api/v1/problems/1/tests \
  -F "file=@sample_problem/tests.zip"
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `OPENROUTER_API_KEY` | Yes | API key for OpenRouter.ai |
| `DATABASE_URL` | No | SQLite database path (default: `backend/data/arena.db`) |

## Project Structure

```
.
├── backend/                 # FastAPI backend
│   ├── api/                # API route handlers
│   ├── database/           # SQLAlchemy models and migrations
│   ├── orchestrator/       # Run execution engine
│   ├── sandbox/            # Docker sandbox management
│   ├── data/               # Persistent data storage
│   └── main.py             # Application entry point
├── frontend/               # React frontend
│   ├── src/
│   │   ├── components/     # Reusable UI components
│   │   ├── pages/          # Page components
│   │   └── api.js          # API client
│   └── package.json
├── docker/                 # Docker configurations
│   ├── Dockerfile.sandbox  # Sandbox image for code execution
│   └── test_harness.cpp    # C++ test harness
├── scripts/                # Utility scripts
│   ├── test_backend.py     # API integration tests
│   ├── test_e2e.bat        # Windows E2E test script
│   └── test_e2e.sh         # Unix E2E test script
└── sample_problem/         # Sample problem for testing
    ├── problem.md
    ├── tests/
    └── tests.zip
```

## Architecture

### Backend Components

1. **FastAPI Application**: REST API with async support
2. **SQLAlchemy ORM**: Async SQLite database
3. **Orchestration Engine**: Manages run execution across rounds
4. **Model Client**: Communicates with OpenRouter API
5. **Sandbox Manager**: Docker-based compilation and benchmarking
6. **WebSocket Manager**: Real-time status broadcasting

### Frontend Components

1. **React + Vite**: Modern React setup with fast HMR
2. **Tailwind CSS**: Utility-first styling
3. **Recharts**: Data visualization for benchmarks
4. **WebSocket Client**: Real-time updates

### Data Flow

```
User → Frontend → Backend API → Database
                        ↓
                   Orchestrator
                        ↓
            ┌──────────┼──────────┐
            ↓          ↓          ↓
        OpenRouter   Docker     WebSocket
        (Models)   (Sandbox)    (Updates)
```

## Troubleshooting

### Backend won't start

- Check Python version: `python --version` (must be 3.10+)
- Verify dependencies: `pip install -r backend/requirements.txt`
- Check port 8000 is available

### Docker image not found

```bash
docker build -f docker/Dockerfile.sandbox -t arena-sandbox .
```

### Frontend can't connect to backend

- Ensure backend is running on port 8000
- Check `frontend/vite.config.js` proxy settings
- Verify CORS is configured in `backend/main.py`

### OpenRouter API errors

- Verify `OPENROUTER_API_KEY` is set correctly
- Check API key has sufficient credits
- Review API call logs in the frontend

## Development

### Backend Development

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Auto-reload is enabled - changes to Python files will restart the server.

### Frontend Development

```bash
cd frontend
npm run dev
```

Vite dev server with hot module replacement.

### Adding New Features

1. **Backend**: Add routes in `backend/api/`, update models in `backend/database/models.py`
2. **Frontend**: Add pages in `frontend/src/pages/`, update routing in `App.jsx`
3. **Database**: Models auto-migrate on startup

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Run tests: `python scripts/test_backend.py`
5. Submit a pull request

## License

MIT License - see LICENSE file for details

## Acknowledgments

- OpenRouter for providing unified LLM API access
- FastAPI for the excellent web framework
- React team for the frontend library
