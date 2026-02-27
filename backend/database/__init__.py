"""
Database package for the AI Optimization Arena.
"""
from backend.database.models import (
    ApiCall,
    Base,
    Problem,
    Round,
    Run,
    Solution,
    TestResult,
)
from backend.database.session import get_db, get_engine, get_session_maker
from backend.database.migrations import create_tables, init_database

__all__ = [
    "Base",
    "Problem",
    "Run",
    "Round",
    "Solution",
    "TestResult",
    "ApiCall",
    "get_db",
    "get_engine",
    "get_session_maker",
    "create_tables",
    "init_database",
]
