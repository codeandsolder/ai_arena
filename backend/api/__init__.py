"""
API module for the AI Optimization Arena backend.

This module contains all FastAPI routers and endpoint definitions.
"""

from backend.api.problems import router as problems_router
from backend.api.runs import router as runs_router
from backend.api.rounds import router as rounds_router
from backend.api.solutions import router as solutions_router
from backend.api.api_calls import router as api_calls_router
from backend.api.websocket import websocket_router

__all__ = [
    "problems_router",
    "runs_router",
    "rounds_router",
    "solutions_router",
    "api_calls_router",
    "websocket_router",
]