"""
SQLAlchemy async database session management.
"""
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import sessionmaker
from contextlib import asynccontextmanager

from backend.config import DATABASE_URL

# Global engine instance (initialized lazily)
_engine: AsyncEngine | None = None

# Session factory (initialized lazily)
_async_session_maker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """
    Get or create the async database engine.
    
    Returns:
        AsyncEngine: The SQLAlchemy async engine instance.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            DATABASE_URL,
            echo=False,  # Set to True for SQL query logging
            future=True,
        )
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """
    Get or create the async session factory.
    
    Returns:
        async_sessionmaker: Factory for creating async sessions.
    """
    global _async_session_maker
    if _async_session_maker is None:
        engine = get_engine()
        _async_session_maker = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _async_session_maker


def AsyncSessionLocal():
    """
    Get a new async database session.
    
    This is a compatibility wrapper for code expecting a session factory.
    Returns:
        AsyncSession: A new SQLAlchemy async session.
    """
    return get_session_maker()()


async def get_db() -> AsyncSession:
    """
    FastAPI dependency function that yields an async database session.
    
    Usage:
        @app.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...
    
    Yields:
        AsyncSession: An async SQLAlchemy session.
    """
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_session() -> AsyncSession:
    """
    Get a database session for non-FastAPI contexts.
    
    This is a direct async function that returns a session,
    unlike get_db() which is a generator for FastAPI dependency injection.
    
    Usage:
        async with get_db_session() as session:
            result = await session.execute(select(Model))
            ...
    
    Yields:
        AsyncSession: An async SQLAlchemy session.
    """
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def close_engine() -> None:
    """
    Close the database engine and clean up resources.
    Call this on application shutdown.
    """
    global _engine, _async_session_maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_maker = None
