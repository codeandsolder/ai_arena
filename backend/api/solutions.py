"""
Solution details API endpoints.

Provides endpoints for retrieving solution code, compile logs, and benchmark results.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Solution, TestResult
from backend.database.session import get_db

router = APIRouter(tags=["solutions"])


# =============================================================================
# Pydantic Schemas
# =============================================================================


class TestResultResponse(BaseModel):
    """Schema for test result response."""
    id: int
    test_index: int
    passed: Optional[bool]
    time_ms: Optional[float]
    memory_kb: Optional[int]
    exit_code: Optional[int]
    verdict: Optional[str]
    error_output: Optional[str]
    actual_output: Optional[str]
    expected_output: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class SolutionResponse(BaseModel):
    """Schema for full solution response."""
    id: int
    round_id: int
    model_slug: str
    source_code: Optional[str]
    compiler: Optional[str]
    compiler_flags: Optional[str]
    compile_success: Optional[bool]
    compile_log: Optional[str]
    tests_passed: int
    tests_total: int
    avg_time_ms: Optional[float]
    max_time_ms: Optional[float]
    max_memory_kb: Optional[int]
    score: float
    status: str
    error_message: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SolutionTestsResponse(BaseModel):
    """Schema for solution tests response."""
    solution_id: int
    model_slug: str
    status: str
    tests_passed: int
    tests_total: int
    test_results: List[TestResultResponse]


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("/solutions/{solution_id}", response_model=SolutionResponse)
async def get_solution(solution_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get full solution details including code, compile log, and benchmark results.
    
    Args:
        solution_id: The solution ID
        
    Returns:
        Full solution details
        
    Raises:
        404: Solution not found
    """
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    solution = result.scalar_one_or_none()
    
    if solution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Solution with id {solution_id} not found"
        )
    
    return solution


@router.get("/solutions/{solution_id}/tests", response_model=SolutionTestsResponse)
async def get_solution_tests(solution_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get all test results for a solution.
    
    Args:
        solution_id: The solution ID
        
    Returns:
        Solution with all test results
        
    Raises:
        404: Solution not found
    """
    # Verify solution exists
    result = await db.execute(select(Solution).where(Solution.id == solution_id))
    solution = result.scalar_one_or_none()
    
    if solution is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Solution with id {solution_id} not found"
        )
    
    # Get all test results
    result = await db.execute(
        select(TestResult)
        .where(TestResult.solution_id == solution_id)
        .order_by(TestResult.test_index)
    )
    test_results = result.scalars().all()
    
    return SolutionTestsResponse(
        solution_id=solution.id,
        model_slug=solution.model_slug,
        status=solution.status,
        tests_passed=solution.tests_passed,
        tests_total=solution.tests_total,
        test_results=test_results
    )