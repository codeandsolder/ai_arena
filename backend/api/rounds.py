"""
Round details API endpoints.

Provides endpoints for retrieving round information and solutions.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Round, Run, Solution, TestResult
from backend.database.session import get_db

router = APIRouter(tags=["rounds"])


# =============================================================================
# Pydantic Schemas
# =============================================================================


class SolutionSummary(BaseModel):
    """Summary of a solution for round response."""
    id: int
    model_slug: str
    status: str
    tests_passed: int
    tests_total: int
    score: float
    avg_time_ms: Optional[float]
    max_time_ms: Optional[float]

    class Config:
        from_attributes = True


class RoundListItem(BaseModel):
    """Schema for round list response."""
    id: int
    round_number: int
    status: str
    created_at: datetime
    solution_count: int


class RoundResponse(BaseModel):
    """Schema for full round response."""
    id: int
    run_id: int
    round_number: int
    status: str
    summary_text: Optional[str]
    created_at: datetime
    solutions: List[SolutionSummary]


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("/runs/{run_id}/rounds", response_model=List[RoundListItem])
async def list_rounds(run_id: int, db: AsyncSession = Depends(get_db)):
    """
    List all rounds in a run.
    
    Args:
        run_id: The run ID
        
    Returns:
        List of rounds with summary information
        
    Raises:
        404: Run not found
    """
    # Verify run exists
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with id {run_id} not found"
        )
    
    # Get rounds with solution count
    result = await db.execute(
        select(Round)
        .where(Round.run_id == run_id)
        .order_by(Round.round_number)
    )
    rounds = result.scalars().all()
    
    # Build response with solution counts
    response = []
    for round_obj in rounds:
        result = await db.execute(
            select(Solution).where(Solution.round_id == round_obj.id)
        )
        solution_count = len(result.scalars().all())
        
        response.append(RoundListItem(
            id=round_obj.id,
            round_number=round_obj.round_number,
            status=round_obj.status,
            created_at=round_obj.created_at,
            solution_count=solution_count
        ))
    
    return response


@router.get("/rounds/{round_id}", response_model=RoundResponse)
async def get_round(round_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get full round details including summary_text and all solutions.
    
    Args:
        round_id: The round ID
        
    Returns:
        Full round details with solutions
        
    Raises:
        404: Round not found
    """
    result = await db.execute(select(Round).where(Round.id == round_id))
    round_obj = result.scalar_one_or_none()
    
    if round_obj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Round with id {round_id} not found"
        )
    
    # Get all solutions for this round
    result = await db.execute(
        select(Solution)
        .where(Solution.round_id == round_id)
        .order_by(Solution.model_slug)
    )
    solutions = result.scalars().all()
    
    # Build solution summaries
    solution_summaries = []
    for solution in solutions:
        solution_summaries.append(SolutionSummary(
            id=solution.id,
            model_slug=solution.model_slug,
            status=solution.status,
            tests_passed=solution.tests_passed,
            tests_total=solution.tests_total,
            score=solution.score,
            avg_time_ms=solution.avg_time_ms,
            max_time_ms=solution.max_time_ms
        ))
    
    return RoundResponse(
        id=round_obj.id,
        run_id=round_obj.run_id,
        round_number=round_obj.round_number,
        status=round_obj.status,
        summary_text=round_obj.summary_text,
        created_at=round_obj.created_at,
        solutions=solution_summaries
    )