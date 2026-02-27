"""
SQLAlchemy ORM models for the AI Optimization Arena database.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all ORM models with async support."""
    pass


class Problem(Base):
    """
    Represents a competitive programming problem.
    """
    __tablename__ = "problems"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, nullable=False, index=True)
    description_md = Column(Text, nullable=False)
    time_limit_ms = Column(Integer, default=2000, nullable=False)
    memory_limit_mb = Column(Integer, default=256, nullable=False)
    test_count = Column(Integer, default=0, nullable=False)
    sample_input = Column(Text, nullable=True)
    sample_output = Column(Text, nullable=True)
    scoring_mode = Column(String, default="binary", nullable=False)
    source_url = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    tests_downloaded = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationships
    runs = relationship("Run", back_populates="problem", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Problem(id={self.id}, slug='{self.slug}', name='{self.name}')>"


class Run(Base):
    """
    Represents a competition run between models on a specific problem.
    """
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    problem_id = Column(Integer, ForeignKey("problems.id"), nullable=False, index=True)
    status = Column(String, default="configured", nullable=False, index=True)
    config_json = Column(Text, nullable=False)
    total_rounds = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    problem = relationship("Problem", back_populates="runs")
    rounds = relationship("Round", back_populates="run", cascade="all, delete-orphan")
    api_calls = relationship("ApiCall", back_populates="run", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Run(id={self.id}, name='{self.name}', status='{self.status}')>"


class Round(Base):
    """
    Represents a single round within a run where models submit solutions.
    """
    __tablename__ = "rounds"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False, index=True)
    round_number = Column(Integer, nullable=False)
    status = Column(String, default="pending", nullable=False, index=True)
    summary_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationships
    run = relationship("Run", back_populates="rounds")
    solutions = relationship("Solution", back_populates="round", cascade="all, delete-orphan")
    api_calls = relationship("ApiCall", back_populates="round", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Round(id={self.id}, run_id={self.run_id}, round_number={self.round_number})>"


class Solution(Base):
    """
    Represents a solution submitted by a model in a specific round.
    """
    __tablename__ = "solutions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=False, index=True)
    model_slug = Column(String, nullable=False, index=True)
    source_code = Column(Text, nullable=True)
    compiler = Column(String, nullable=True)
    compiler_flags = Column(String, nullable=True)
    compile_success = Column(Boolean, nullable=True)
    compile_log = Column(Text, nullable=True)
    tests_passed = Column(Integer, default=0, nullable=False)
    tests_total = Column(Integer, default=0, nullable=False)
    avg_time_ms = Column(Float, nullable=True)
    max_time_ms = Column(Float, nullable=True)
    max_memory_kb = Column(Integer, nullable=True)
    score = Column(Float, default=0.0, nullable=False)
    status = Column(String, default="pending", nullable=False, index=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationships
    round = relationship("Round", back_populates="solutions")
    test_results = relationship("TestResult", back_populates="solution", cascade="all, delete-orphan")
    api_calls = relationship("ApiCall", back_populates="solution", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Solution(id={self.id}, model_slug='{self.model_slug}', status='{self.status}')>"


class TestResult(Base):
    """
    Represents the result of running a solution against a single test case.
    """
    __test__ = False
    __tablename__ = "test_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    solution_id = Column(Integer, ForeignKey("solutions.id"), nullable=False, index=True)
    test_index = Column(Integer, nullable=False)
    passed = Column(Boolean, nullable=True)
    actual_output = Column(Text, nullable=True)
    expected_output = Column(Text, nullable=True)
    time_ms = Column(Float, nullable=True)
    memory_kb = Column(Integer, nullable=True)
    exit_code = Column(Integer, nullable=True)
    error_output = Column(Text, nullable=True)
    verdict = Column(String, nullable=True)

    # Relationships
    solution = relationship("Solution", back_populates="test_results")

    def __repr__(self) -> str:
        return f"<TestResult(id={self.id}, solution_id={self.solution_id}, test_index={self.test_index}, passed={self.passed})>"


class ApiCall(Base):
    """
    Represents an API call made to an LLM provider during a run.
    """
    __tablename__ = "api_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False, index=True)
    round_id = Column(Integer, ForeignKey("rounds.id"), nullable=True, index=True)
    solution_id = Column(Integer, ForeignKey("solutions.id"), nullable=True, index=True)
    purpose = Column(String, nullable=False)
    model_slug = Column(String, nullable=False, index=True)
    prompt_text = Column(Text, nullable=True)
    thinking_text = Column(Text, nullable=True)
    response_text = Column(Text, nullable=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    thinking_tokens = Column(Integer, nullable=True)
    cost_usd = Column(Float, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    # Relationships
    run = relationship("Run", back_populates="api_calls")
    round = relationship("Round", back_populates="api_calls")
    solution = relationship("Solution", back_populates="api_calls")

    def __repr__(self) -> str:
        return f"<ApiCall(id={self.id}, model_slug='{self.model_slug}', purpose='{self.purpose}')>"
