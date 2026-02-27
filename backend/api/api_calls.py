"""
API call logging API endpoints.

Provides endpoints for retrieving LLM API call logs.
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import ApiCall, Run
from backend.database.session import get_db

router = APIRouter(tags=["api-calls"])


# =============================================================================
# Pydantic Schemas
# =============================================================================


class ApiCallListItem(BaseModel):
    """Schema for API call list response."""
    id: int
    run_id: int
    round_id: Optional[int]
    solution_id: Optional[int]
    purpose: str
    model_slug: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    thinking_tokens: Optional[int]
    cost_usd: Optional[float]
    latency_ms: Optional[int]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiCallResponse(BaseModel):
    """Schema for full API call response with prompt/thinking/response."""
    id: int
    run_id: int
    round_id: Optional[int]
    solution_id: Optional[int]
    purpose: str
    model_slug: str
    prompt_text: Optional[str]
    thinking_text: Optional[str]
    response_text: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    thinking_tokens: Optional[int]
    cost_usd: Optional[float]
    latency_ms: Optional[int]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ApiCallListResponse(BaseModel):
    """Schema for paginated API call list response."""
    items: List[ApiCallListItem]
    total: int
    page: int
    page_size: int
    pages: int


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("/runs/{run_id}/api-calls", response_model=ApiCallListResponse)
async def list_api_calls(
    run_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db)
):
    """
    Get all API calls for a run, paginated.
    
    Args:
        run_id: The run ID
        page: Page number (1-indexed)
        page_size: Number of items per page
        
    Returns:
        Paginated list of API calls
        
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
    
    # Get total count
    result = await db.execute(
        select(func.count()).select_from(ApiCall).where(ApiCall.run_id == run_id)
    )
    total = result.scalar()
    
    # Calculate pagination
    offset = (page - 1) * page_size
    pages = (total + page_size - 1) // page_size
    
    # Get paginated results
    result = await db.execute(
        select(ApiCall)
        .where(ApiCall.run_id == run_id)
        .order_by(ApiCall.id.desc())
        .offset(offset)
        .limit(page_size)
    )
    api_calls = result.scalars().all()
    
    return ApiCallListResponse(
        items=api_calls,
        total=total,
        page=page,
        page_size=page_size,
        pages=pages
    )


@router.get("/api-calls/{api_call_id}", response_model=ApiCallResponse)
async def get_api_call(api_call_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get full API call details including prompt, thinking, and response.
    
    Args:
        api_call_id: The API call ID
        
    Returns:
        Full API call details
        
    Raises:
        404: API call not found
    """
    result = await db.execute(select(ApiCall).where(ApiCall.id == api_call_id))
    api_call = result.scalar_one_or_none()
    
    if api_call is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"API call with id {api_call_id} not found"
        )
    
    return api_call