"""
Orchestrator module for the AI Optimization Arena.

This module provides the core orchestration engine that manages the multi-round
optimization loop, coordinates LLM model interactions via OpenRouter API,
and handles solution generation, compilation, benchmarking, and judging.
"""

from backend.orchestrator.engine import OrchestrationEngine
from backend.orchestrator.model_client import ModelClient
from backend.orchestrator.summarizer import Summarizer
from backend.orchestrator.prompts import PromptFormatter

__all__ = [
    "OrchestrationEngine",
    "ModelClient",
    "Summarizer",
    "PromptFormatter",
]
