import pytest
import pytest_asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text, inspect, Column, Integer
from backend.database.migrations import (
    create_tables,
    migrate_schema,
    drop_tables,
    check_tables_exist,
    init_database
)
from backend.database.models import Base

@pytest_asyncio.fixture
async def test_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    yield engine
    await engine.dispose()

@pytest.mark.asyncio
async def test_create_tables(test_engine):
    with patch("backend.database.migrations.ensure_directories") as mock_ensure:
        await create_tables(test_engine)
        mock_ensure.assert_called_once()
        
        # Verify tables exist
        async with test_engine.connect() as conn:
            def get_tables(sync_conn):
                inspector = inspect(sync_conn)
                return inspector.get_table_names()
            
            tables = await conn.run_sync(get_tables)
            assert "problems" in tables
            assert "runs" in tables
            assert "rounds" in tables
            assert "solutions" in tables
            assert "test_results" in tables
            assert "api_calls" in tables

@pytest.mark.asyncio
async def test_create_tables_no_engine():
    # Test that it uses the default engine if none is provided
    with patch("backend.database.migrations.ensure_directories"), \
         patch("backend.database.migrations.get_engine") as mock_get_engine, \
         patch("backend.database.migrations.migrate_schema") as mock_migrate:
        
        mock_engine = MagicMock(spec=AsyncEngine)
        # Mock engine.begin() context manager
        mock_begin = MagicMock()
        mock_engine.begin.return_value = mock_begin
        
        mock_conn = AsyncMock()
        async def mock_enter(self):
            return mock_conn
        mock_begin.__aenter__ = mock_enter
        async def mock_exit(self, exc_type, exc_val, exc_tb):
            pass
        mock_begin.__aexit__ = mock_exit
        
        mock_get_engine.return_value = mock_engine
        
        await create_tables()
        
        mock_get_engine.assert_called_once()
        mock_migrate.assert_called_once_with(mock_engine)

@pytest.mark.asyncio
async def test_migrate_schema_add_missing_column(test_engine):
    # Manually create a table missing one column (e.g., 'last_synced_at' in 'problems')
    async with test_engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE problems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR NOT NULL,
                slug VARCHAR NOT NULL,
                description_md TEXT NOT NULL,
                time_limit_ms INTEGER NOT NULL,
                memory_limit_mb INTEGER NOT NULL,
                test_count INTEGER NOT NULL,
                sample_input TEXT,
                sample_output TEXT,
                scoring_mode VARCHAR NOT NULL,
                source_url VARCHAR,
                short_description VARCHAR,
                tags TEXT,
                tests_downloaded BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL
            )
        """))
    
    # Verify column doesn't exist yet
    async with test_engine.connect() as conn:
        def get_cols(sync_conn):
            inspector = inspect(sync_conn)
            return [c["name"] for c in inspector.get_columns("problems")]
        cols = await conn.run_sync(get_cols)
        assert "last_synced_at" not in cols
    
    # Run migration
    await migrate_schema(test_engine)
    
    # Verify column exists now
    async with test_engine.connect() as conn:
        cols = await conn.run_sync(get_cols)
        assert "last_synced_at" in cols

@pytest.mark.asyncio
async def test_migrate_schema_with_default(test_engine):
    # Test adding a column with a default value to cover lines 81-82, 91
    # We'll create 'problems' table without 'test_count' which has a default of 0 in the model
    async with test_engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE problems (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR NOT NULL,
                slug VARCHAR NOT NULL,
                description_md TEXT NOT NULL,
                time_limit_ms INTEGER NOT NULL,
                memory_limit_mb INTEGER NOT NULL,
                sample_input TEXT,
                sample_output TEXT,
                scoring_mode VARCHAR NOT NULL,
                source_url VARCHAR,
                short_description VARCHAR,
                tags TEXT,
                last_synced_at DATETIME,
                tests_downloaded BOOLEAN NOT NULL,
                created_at DATETIME NOT NULL
            )
        """))
    
    # Run migration
    await migrate_schema(test_engine)
    
    # Verify column exists and check its properties if possible
    async with test_engine.connect() as conn:
        def get_col_details(sync_conn):
            inspector = inspect(sync_conn)
            cols = inspector.get_columns("problems")
            for c in cols:
                if c["name"] == "test_count":
                    return c
            return None
        col = await conn.run_sync(get_col_details)
        assert col is not None

@pytest.mark.asyncio
async def test_migrate_schema_exception_handling(test_engine):
    # Cover lines 100-101
    
    # Use a mock engine and connection to avoid read-only attribute issues
    mock_engine = MagicMock(spec=AsyncEngine)
    mock_engine.dialect = test_engine.dialect
    
    mock_conn = AsyncMock()
    # Mock run_sync to return some data so it thinks 'problems' exists but is missing a column
    mock_conn.run_sync.return_value = {"problems": ["id"]}
    mock_conn.execute.side_effect = Exception("Simulated failure")
    
    # Setup context manager for engine.connect()
    mock_connect_cm = AsyncMock()
    mock_connect_cm.__aenter__.return_value = mock_conn
    mock_engine.connect.return_value = mock_connect_cm
    
    with patch("backend.database.migrations.logger") as mock_logger:
        await migrate_schema(mock_engine)
        mock_logger.error.assert_called()
        assert "Failed to add column" in mock_logger.error.call_args[0][0]

@pytest.mark.asyncio
async def test_drop_tables(test_engine):
    # Create tables first
    await create_tables(test_engine)
    
    # Check tables exist
    exists = await check_tables_exist(test_engine)
    assert any(exists.values())
    
    # Try drop without confirmation
    with patch("backend.database.migrations.logger") as mock_logger:
        await drop_tables(test_engine, confirm=False)
        mock_logger.warning.assert_called_with("Drop tables called without confirmation. Set confirm=True to proceed.")
    
    # Verify tables still exist
    exists = await check_tables_exist(test_engine)
    assert any(exists.values())
    
    # Drop with confirmation
    await drop_tables(test_engine, confirm=True)
    
    # Verify tables are gone
    exists = await check_tables_exist(test_engine)
    assert not any(exists.values())

@pytest.mark.asyncio
async def test_drop_tables_no_engine():
    with patch("backend.database.migrations.get_engine") as mock_get_engine:
        mock_engine = MagicMock(spec=AsyncEngine)
        mock_begin = MagicMock()
        mock_engine.begin.return_value = mock_begin
        
        mock_conn = AsyncMock()
        async def mock_enter(self):
            return mock_conn
        mock_begin.__aenter__ = mock_enter
        async def mock_exit(self, exc_type, exc_val, exc_tb):
            pass
        mock_begin.__aexit__ = mock_exit
        
        mock_get_engine.return_value = mock_engine
        
        await drop_tables(confirm=True)
        mock_get_engine.assert_called_once()

@pytest.mark.asyncio
async def test_check_tables_exist_no_engine():
    with patch("backend.database.migrations.get_engine") as mock_get_engine:
        mock_engine = MagicMock(spec=AsyncEngine)
        mock_connect = MagicMock()
        mock_engine.connect.return_value = mock_connect
        
        mock_conn = AsyncMock()
        async def mock_enter(self):
            return mock_conn
        mock_connect.__aenter__ = mock_enter
        async def mock_exit(self, exc_type, exc_val, exc_tb):
            pass
        mock_connect.__aexit__ = mock_exit
        
        mock_get_engine.return_value = mock_engine
        
        await check_tables_exist()
        mock_get_engine.assert_called_once()

@pytest.mark.asyncio
async def test_init_database():
    with patch("backend.database.migrations.create_tables") as mock_create:
        await init_database()
        mock_create.assert_called_once()
