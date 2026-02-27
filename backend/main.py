"""
FastAPI application entry point for the AI Optimization Arena.

Provides the main FastAPI application with all API endpoints and startup/shutdown events.
"""

import asyncio
import logging
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

from backend.api import (
    api_calls_router,
    problems_router,
    rounds_router,
    runs_router,
    solutions_router,
    websocket_router,
)
from backend.config import ensure_directories
from backend.database.migrations import create_tables
from backend.database.session import close_engine, AsyncSessionLocal
from backend.services.problem_ingestion import IOIIngestor


async def ingest_default_problems():
    """Background task to ingest default problems on startup."""
    logger.info("Starting background ingestion of default problems...")
    repo_url = "https://github.com/austrian-olympiad-informatics/ioi-tasks"
    
    try:
        async with AsyncSessionLocal() as session:
            async with IOIIngestor(session) as ingestor:
                await ingestor.ingest_repository(repo_url)
        logger.info("Background ingestion completed successfully.")
    except Exception as e:
        logger.error(f"Background ingestion failed: {e}")


def check_docker_image() -> bool:
    """
    Check if the arena-sandbox Docker image exists.
    
    Returns:
        True if the image exists, False otherwise.
    """
    try:
        result = subprocess.run(
            ["docker", "images", "-q", "arena-sandbox"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return bool(result.stdout.strip())
    except (subprocess.SubprocessError, FileNotFoundError, TimeoutError):
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context manager.
    
    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting AI Optimization Arena API...")
    
    # Ensure directories exist
    logger.info("Ensuring data directories exist...")
    ensure_directories()
    
    # Create database tables
    logger.info("Creating database tables...")
    await create_tables()
    
    # Verify Docker image exists
    logger.info("Checking for arena-sandbox Docker image...")
    if check_docker_image():
        logger.info("✓ Docker image 'arena-sandbox' found")
    else:
        logger.warning(
            "⚠ Docker image 'arena-sandbox' not found! "
            "Compilation and benchmarking will not work. "
            "Build it with: docker build -f docker/Dockerfile.sandbox -t arena-sandbox ."
        )
    
    logger.info("AI Optimization Arena API started successfully!")
    
    # Start background ingestion of IOI tasks if needed
    asyncio.create_task(ingest_default_problems())
    
    yield
    
    # Shutdown
    logger.info("Shutting down AI Optimization Arena API...")
    
    # Close database engine
    logger.info("Closing database engine...")
    await close_engine()
    
    logger.info("AI Optimization Arena API shut down successfully!")


# Create FastAPI application
app = FastAPI(
    title="AI Optimization Arena API",
    description="""
    REST API and WebSocket endpoints for managing the AI Optimization Arena.
    
    ## Features
    
    * **Problems**: Create and manage competitive programming problems
    * **Runs**: Configure and execute optimization competitions
    * **Rounds**: Track competition rounds and results
    * **Solutions**: View solution code, compile logs, and benchmark results
    * **API Calls**: Monitor LLM API usage and costs
    * **WebSocket**: Real-time status updates during runs
    """,
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan
)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Vite dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routers
app.include_router(problems_router, prefix="/api/v1")
app.include_router(runs_router, prefix="/api/v1")
app.include_router(rounds_router, prefix="/api/v1")
app.include_router(solutions_router, prefix="/api/v1")
app.include_router(api_calls_router, prefix="/api/v1")
app.include_router(websocket_router, prefix="/api/v1")


@app.get("/", tags=["root"])
async def root():
    """
    Root endpoint returning API information.
    
    Returns:
        API metadata and available endpoints.
    """
    return {
        "name": "AI Optimization Arena API",
        "version": "1.0.0",
        "docs": "/docs",
        "redoc": "/redoc",
        "endpoints": {
            "problems": "/api/v1/problems",
            "runs": "/api/v1/runs",
            "rounds": "/api/v1/runs/{run_id}/rounds",
            "solutions": "/api/v1/solutions",
            "api_calls": "/api/v1/runs/{run_id}/api-calls",
            "websocket": "/api/v1/ws/runs/{run_id}"
        }
    }


@app.get("/health", tags=["health"])
async def health_check():
    """
    Health check endpoint.
    
    Returns:
        Health status of the API.
    """
    return {
        "status": "healthy",
        "service": "ai-optimization-arena-api"
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )