"""
Database migration utilities for the AI Optimization Arena.
"""
import logging

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.config import ensure_directories
from backend.database.models import Base
from backend.database.session import get_engine

logger = logging.getLogger(__name__)


async def create_tables(engine: AsyncEngine | None = None) -> None:
    """
    Create all database tables if they don't exist.
    
    This function:
    1. Ensures the data directory exists
    2. Creates all tables defined in the ORM models
    3. Skips tables that already exist
    
    Args:
        engine: Optional async engine to use. If not provided, uses the default engine.
    """
    # Ensure data directories exist
    ensure_directories()
    
    # Get or create engine
    if engine is None:
        engine = get_engine()
    
    logger.info("Creating database tables if they don't exist...")
    
    async with engine.begin() as conn:
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
    
    logger.info("Database tables ready.")


async def drop_tables(engine: AsyncEngine | None = None, confirm: bool = False) -> None:
    """
    Drop all database tables. Use with caution!
    
    Args:
        engine: Optional async engine to use. If not provided, uses the default engine.
        confirm: Must be True to actually drop tables (safety measure).
    """
    if not confirm:
        logger.warning("Drop tables called without confirmation. Set confirm=True to proceed.")
        return
    
    if engine is None:
        engine = get_engine()
    
    logger.warning("Dropping all database tables...")
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    
    logger.warning("All database tables dropped.")


async def check_tables_exist(engine: AsyncEngine | None = None) -> dict[str, bool]:
    """
    Check which tables exist in the database.
    
    Args:
        engine: Optional async engine to use. If not provided, uses the default engine.
    
    Returns:
        Dictionary mapping table names to existence status.
    """
    if engine is None:
        engine = get_engine()
    
    table_names = Base.metadata.tables.keys()
    result: dict[str, bool] = {}
    
    def check_tables(sync_conn):
        inspector = inspect(sync_conn)
        existing = set(inspector.get_table_names())
        for name in table_names:
            result[name] = name in existing
    
    async with engine.connect() as conn:
        await conn.run_sync(check_tables)
    
    return result


async def init_database() -> None:
    """
    Initialize the database for application startup.
    Creates tables and ensures all directories exist.
    """
    await create_tables()
