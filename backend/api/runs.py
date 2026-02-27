"""
Run management and execution control API endpoints.

Provides endpoints for creating, updating, and controlling optimization runs.
"""

import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Run, Problem, Round, Solution, ApiCall
from backend.database.session import get_db
from backend.orchestrator.engine import OrchestrationEngine

router = APIRouter(prefix="/runs", tags=["runs"])
logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Schemas
# =============================================================================


class ModelConfig(BaseModel):
    """Configuration for a model in a run."""
    slug: str
    enabled: bool = True
    temperature: float = 0.7
    max_tokens: int = 4096


class JudgeModelConfig(BaseModel):
    """Configuration for the judge model."""
    slug: str
    temperature: float = 0.5
    max_tokens: int = 2048


class PromptConfig(BaseModel):
    """Configuration for prompts."""
    system_prompt: Optional[str] = None
    round_1_prompt: Optional[str] = None
    subsequent_rounds_prompt: Optional[str] = None


class ScoringConfig(BaseModel):
    """Configuration for scoring."""
    correctness_weight: float = 0.6
    speed_weight: float = 0.25
    memory_weight: float = 0.15
    penalty_wrong_answer: float = -0.5


class ExecutionConfig(BaseModel):
    """Configuration for execution."""
    timeout_buffer_ms: int = 100
    enable_cache_simulation: bool = True
    measure_memory_peak: bool = True


class RunConfig(BaseModel):
    """Complete run configuration."""
    models: List[ModelConfig]
    judge_model: JudgeModelConfig
    prompts: PromptConfig
    response_format: str = "json"
    scoring: ScoringConfig
    execution: ExecutionConfig


class RunBase(BaseModel):
    """Base run schema."""
    name: str = Field(..., min_length=1, max_length=255)
    problem_id: int = Field(..., gt=0)
    config_json: str


class RunCreate(RunBase):
    """Schema for creating a run."""
    
    @validator('config_json')
    def validate_config_json(cls, v):
        """Validate that config_json is valid JSON."""
        try:
            config = json.loads(v)
            # Basic validation that required fields exist
            if 'models' not in config:
                raise ValueError("config_json must contain 'models' field")
            if 'judge_model' not in config:
                raise ValueError("config_json must contain 'judge_model' field")
            return v
        except json.JSONDecodeError as e:
            raise ValueError(f"config_json must be valid JSON: {e}")


class RunUpdate(BaseModel):
    """Schema for updating a run."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    config_json: Optional[str] = None
    
    @validator('config_json')
    def validate_config_json(cls, v):
        """Validate that config_json is valid JSON if provided."""
        if v is None:
            return v
        try:
            json.loads(v)
            return v
        except json.JSONDecodeError as e:
            raise ValueError(f"config_json must be valid JSON: {e}")


class RunListItem(BaseModel):
    """Schema for run list response (summary)."""
    id: int
    name: str
    problem_name: str
    status: str
    total_rounds: int
    created_at: datetime


class RunResponse(RunBase):
    """Schema for full run response."""
    id: int
    status: str
    total_rounds: int
    created_at: datetime
    updated_at: datetime
    config: Dict[str, Any]  # parsed from config_json

    class Config:
        from_attributes = True


class StartRunRequest(BaseModel):
    """Request body for starting a run."""
    num_rounds: int = Field(..., ge=1, le=100, description="Number of rounds to execute")


class ResumeRunRequest(BaseModel):
    """Request body for resuming a paused run."""
    num_rounds: int = Field(..., ge=1, le=100, description="Additional rounds to run")


# =============================================================================
# Helper Functions
# =============================================================================


def parse_config(config_json: str) -> Dict[str, Any]:
    """Parse config_json string to dictionary."""
    try:
        return json.loads(config_json)
    except json.JSONDecodeError:
        return {}


def can_modify_run(status: str) -> bool:
    """Check if a run can be modified based on its status."""
    return status in ("configured", "paused", "completed")


async def execute_run_background(run_id: int, num_rounds: int):
    """
    Background task to execute a run.
    
    Args:
        run_id: The run ID to execute
        num_rounds: Number of rounds to execute
    """
    logger.info(f"Starting background execution for run {run_id}, {num_rounds} rounds")
    
    engine = None
    try:
        engine = OrchestrationEngine()
        await engine.start_run(run_id, num_rounds)
        logger.info(f"Background execution completed for run {run_id}")
    except Exception as e:
        logger.error(f"Background execution failed for run {run_id}: {e}")
        # Update run status to error
        from backend.database.session import get_session_maker
        session_maker = get_session_maker()
        async with session_maker() as session:
            result = await session.execute(select(Run).where(Run.id == run_id))
            run = result.scalar_one_or_none()
            if run:
                run.status = "error"
                await session.commit()
    finally:
        if engine:
            await engine.close()


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("", response_model=List[RunListItem])
async def list_runs(db: AsyncSession = Depends(get_db)):
    """
    List all runs with summary information.
    
    Returns:
        List of runs with id, name, problem name, status, and total_rounds.
    """
    result = await db.execute(
        select(Run, Problem.name.label("problem_name"))
        .join(Problem, Run.problem_id == Problem.id)
        .order_by(Run.id)
    )
    rows = result.all()
    
    runs = []
    for run, problem_name in rows:
        runs.append(RunListItem(
            id=run.id,
            name=run.name,
            problem_name=problem_name,
            status=run.status,
            total_rounds=run.total_rounds,
            created_at=run.created_at
        ))
    
    return runs


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(run_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get full details of a specific run including parsed config.
    
    Args:
        run_id: The run ID
        
    Returns:
        Full run details with parsed config_json
        
    Raises:
        404: Run not found
    """
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with id {run_id} not found"
        )
    
    # Create response with parsed config
    response_data = {
        "id": run.id,
        "name": run.name,
        "problem_id": run.problem_id,
        "config_json": run.config_json,
        "status": run.status,
        "total_rounds": run.total_rounds,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "config": parse_config(run.config_json)
    }
    
    return response_data


@router.post("", response_model=RunResponse, status_code=status.HTTP_201_CREATED)
async def create_run(run_data: RunCreate, db: AsyncSession = Depends(get_db)):
    """
    Create a new run.
    
    Args:
        run_data: Run creation data
        
    Returns:
        Created run
        
    Raises:
        400: Problem not found or invalid config
    """
    # Verify problem exists
    result = await db.execute(select(Problem).where(Problem.id == run_data.problem_id))
    problem = result.scalar_one_or_none()
    
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Problem with id {run_data.problem_id} not found"
        )
    
    run = Run(
        name=run_data.name,
        problem_id=run_data.problem_id,
        config_json=run_data.config_json,
        status="configured",
        total_rounds=0
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    
    # Create response with parsed config
    response_data = {
        "id": run.id,
        "name": run.name,
        "problem_id": run.problem_id,
        "config_json": run.config_json,
        "status": run.status,
        "total_rounds": run.total_rounds,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "config": parse_config(run.config_json)
    }
    
    return response_data


@router.put("/{run_id}", response_model=RunResponse)
async def update_run(
    run_id: int,
    run_data: RunUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update run configuration (only if status is 'configured' or 'paused').
    
    Args:
        run_id: The run ID
        run_data: Fields to update
        
    Returns:
        Updated run
        
    Raises:
        404: Run not found
        400: Cannot update run in current status
    """
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with id {run_id} not found"
        )
    
    if not can_modify_run(run.status):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot update run with status '{run.status}'. Only 'configured' or 'paused' runs can be updated."
        )
    
    # Update fields
    update_data = run_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(run, field, value)
    
    await db.commit()
    await db.refresh(run)
    
    # Create response with parsed config
    response_data = {
        "id": run.id,
        "name": run.name,
        "problem_id": run.problem_id,
        "config_json": run.config_json,
        "status": run.status,
        "total_rounds": run.total_rounds,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "config": parse_config(run.config_json)
    }
    
    return response_data


@router.delete("/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: int, db: AsyncSession = Depends(get_db)):
    """
    Delete a run and all associated data (solutions, rounds, api_calls).
    
    Args:
        run_id: The run ID
        
    Raises:
        404: Run not found
    """
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with id {run_id} not found"
        )
    
    # Delete from database (cascade will handle related rounds, solutions, api_calls)
    await db.delete(run)
    await db.commit()
    
    return None


@router.post("/{run_id}/start")
async def start_run(
    run_id: int,
    request: StartRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Start executing rounds for a run.
    
    Launches a background task to execute the run.
    
    Args:
        run_id: The run ID
        request: Contains num_rounds to execute
        
    Returns:
        Status message
        
    Raises:
        404: Run not found
        400: Run cannot be started (not in configured or paused state)
    """
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with id {run_id} not found"
        )
    
    if run.status not in ("configured", "paused"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot start run with status '{run.status}'. Run must be 'configured' or 'paused'."
        )
    
    # Update status to running
    run.status = "running"
    await db.commit()
    
    # Launch background task
    background_tasks.add_task(execute_run_background, run_id, request.num_rounds)
    
    return {
        "message": "Run started",
        "run_id": run_id,
        "num_rounds": request.num_rounds,
        "status": "running"
    }


@router.post("/{run_id}/pause")
async def pause_run(run_id: int, db: AsyncSession = Depends(get_db)):
    """
    Request to pause a running run after the current round completes.
    
    Args:
        run_id: The run ID
        
    Returns:
        Status message
        
    Raises:
        404: Run not found
        400: Run cannot be paused (not in running state)
    """
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with id {run_id} not found"
        )
    
    if run.status != "running":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot pause run with status '{run.status}'. Run must be 'running'."
        )
    
    # Update status to pausing (will be set to paused after current round)
    run.status = "pausing"
    await db.commit()
    
    return {
        "message": "Pause requested. Run will pause after current round completes.",
        "run_id": run_id,
        "status": "pausing"
    }


@router.post("/{run_id}/resume")
async def resume_run(
    run_id: int,
    request: ResumeRunRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Resume a paused run.
    
    Args:
        run_id: The run ID
        request: Contains num_rounds (additional rounds to run)
        
    Returns:
        Status message
        
    Raises:
        404: Run not found
        400: Run cannot be resumed (not in paused state)
    """
    result = await db.execute(select(Run).where(Run.id == run_id))
    run = result.scalar_one_or_none()
    
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run with id {run_id} not found"
        )
    
    if run.status != "paused":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot resume run with status '{run.status}'. Run must be 'paused'."
        )
    
    # Update status to running
    run.status = "running"
    await db.commit()
    
    # Launch background task
    background_tasks.add_task(execute_run_background, run_id, request.num_rounds)
    
    return {
        "message": "Run resumed",
        "run_id": run_id,
        "num_rounds": request.num_rounds,
        "status": "running"
    }