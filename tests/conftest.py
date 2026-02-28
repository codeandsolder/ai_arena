import pytest
import os
import shutil
import httpx
import unittest.mock as mock
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from fastapi.testclient import TestClient
from httpx import AsyncClient

from backend.database.models import Base
from backend.main import app
from backend.database.session import get_db
from backend.config import DATA_DIR

# Use a temporary directory for tests
TEST_DATA_DIR = Path("tests/test_data")
TEST_DATABASE_PATH = TEST_DATA_DIR / "test_arena.db"
TEST_DATABASE_URL = f"sqlite+aiosqlite:///{TEST_DATABASE_PATH}"

@pytest.fixture(scope="session", autouse=True)
def setup_test_env():
    """Setup and teardown test environment."""
    if TEST_DATA_DIR.exists():
        shutil.rmtree(TEST_DATA_DIR)
    TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # Override config paths for tests
    import backend.config
    backend.config.DATA_DIR = TEST_DATA_DIR
    backend.config.PROBLEMS_DIR = TEST_DATA_DIR / "problems"
    backend.config.RUNS_DIR = TEST_DATA_DIR / "runs"
    backend.config.DATABASE_PATH = TEST_DATABASE_PATH
    backend.config.DATABASE_URL = TEST_DATABASE_URL
    
    # Use openrouter/free for tests as requested
    backend.config.DEFAULT_MODELS = ["openrouter/free"]
    
    yield
    
    # Clean up
    if TEST_DATA_DIR.exists():
        shutil.rmtree(TEST_DATA_DIR)

@pytest.fixture
async def db_session():
    """Create a fresh database and session for each test."""
    engine = create_async_engine(TEST_DATABASE_URL, future=True)
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Use a fixed engine for the session factory to match what's used in backend
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    
    # Override get_session_maker in the backend
    with mock.patch("backend.database.session.get_session_maker", return_value=session_factory), \
         mock.patch("backend.database.session._engine", engine):
        async with session_factory() as session:
            yield session
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
async def client(db_session):
    """FastAPI test client with database override."""
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    
    async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    
    app.dependency_overrides.clear()