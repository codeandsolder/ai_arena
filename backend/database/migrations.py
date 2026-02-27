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
    
    # Check for missing columns and add them (simple migration)
    await migrate_schema(engine)
    
    logger.info("Database tables ready.")


async def migrate_schema(engine: AsyncEngine) -> None:
    """
    Check for missing columns in existing tables and add them.
    This is a simple alternative to full migrations for development.
    """
    from sqlalchemy import text
    
    async with engine.connect() as conn:
        def get_inspector_data(sync_conn):
            inspector = inspect(sync_conn)
            data = {}
            for table_name in Base.metadata.tables.keys():
                if table_name in inspector.get_table_names():
                    data[table_name] = [c["name"] for c in inspector.get_columns(table_name)]
            return data
            
        existing_columns = await conn.run_sync(get_inspector_data)
        
        for table_name, model_table in Base.metadata.tables.items():
            if table_name not in existing_columns:
                continue
                
            db_cols = existing_columns[table_name]
            for column in model_table.columns:
                if column.name not in db_cols:
                    logger.info(f"Adding missing column {column.name} to table {table_name}")
                    
                    # Construct ALTER TABLE statement
                    # SQLite has limited ALTER TABLE support, but adding columns is fine
                    type_str = str(column.type.compile(engine.dialect))
                    nullable = "NULL" if column.nullable else "NOT NULL"
                    default = ""
                    if column.default is not None and hasattr(column.default, "arg"):
                         # Very basic default handling
                         if isinstance(column.default.arg, (int, float, bool)):
                             default = f" DEFAULT {int(column.default.arg)}"
                    
                    # We use a simple approach for now, focusing on last_synced_at
                    # For DateTime columns in SQLite, they are usually TEXT or NUMERIC
                    # but we can just use the type string from SQLAlchemy
                    
                    # Special case for last_synced_at which we know is missing
                    sql = f"ALTER TABLE {table_name} ADD COLUMN {column.name} {type_str}"
                    if not column.nullable and default:
                        sql += default
                    elif not column.nullable:
                        # If it's NOT NULL but no default, this might fail on SQLite if there are rows
                        # For now let's hope it's nullable or has a default
                        pass
                        
                    try:
                        await conn.execute(text(sql))
                        await conn.commit()
                    except Exception as e:
                        logger.error(f"Failed to add column {column.name} to {table_name}: {e}")


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
