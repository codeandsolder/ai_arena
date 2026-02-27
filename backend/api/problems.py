"""
Problem management API endpoints.

Provides CRUD operations for problems and test case management.
"""

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import PROBLEMS_DIR
from backend.database.models import Problem
from backend.database.session import get_db

router = APIRouter(prefix="/problems", tags=["problems"])


# =============================================================================
# Pydantic Schemas
# =============================================================================


class ProblemImportRequest(BaseModel):
    """Schema for importing problems from APPS dataset."""
    start_id: int = Field(..., ge=0, description="Starting APPS problem ID")
    end_id: int = Field(..., ge=0, description="Ending APPS problem ID")


class ProblemBase(BaseModel):
    """Base problem schema with common fields."""
    name: str = Field(..., min_length=1, max_length=255, description="Problem name")
    slug: str = Field(..., min_length=1, max_length=255, pattern=r"^[a-z0-9_-]+$", description="URL-friendly identifier")
    description_md: str = Field(..., min_length=1, description="Problem description in Markdown")
    time_limit_ms: int = Field(default=2000, ge=100, le=60000, description="Time limit per test in milliseconds")
    memory_limit_mb: int = Field(default=256, ge=16, le=4096, description="Memory limit in megabytes")
    scoring_mode: str = Field(default="binary", pattern=r"^(binary|partial|custom)$", description="Scoring mode")


class ProblemCreate(ProblemBase):
    """Schema for creating a new problem."""
    pass


class ProblemUpdate(BaseModel):
    """Schema for updating a problem."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    slug: Optional[str] = Field(None, min_length=1, max_length=255, pattern=r"^[a-z0-9_-]+$")
    description_md: Optional[str] = Field(None, min_length=1)
    time_limit_ms: Optional[int] = Field(None, ge=100, le=60000)
    memory_limit_mb: Optional[int] = Field(None, ge=16, le=4096)
    scoring_mode: Optional[str] = Field(None, pattern=r"^(binary|partial|custom)$")


class ProblemListItem(BaseModel):
    """Schema for problem list response (summary)."""
    id: int
    name: str
    slug: str
    test_count: int
    time_limit_ms: int
    memory_limit_mb: int

    class Config:
        from_attributes = True


class ProblemResponse(ProblemBase):
    """Schema for full problem response."""
    id: int
    test_count: int
    created_at: datetime

    class Config:
        from_attributes = True


class TestCaseInfo(BaseModel):
    """Schema for test case file information."""
    filename: str
    size_bytes: int


class TestCaseListResponse(BaseModel):
    """Schema for test case list response."""
    problem_id: int
    test_count: int
    tests: List[TestCaseInfo]


# =============================================================================
# Helper Functions
# =============================================================================


def get_problem_tests_dir(problem_id: int) -> Path:
    """Get the directory path for problem test cases."""
    return PROBLEMS_DIR / str(problem_id) / "tests"


def count_test_cases(tests_dir: Path) -> int:
    """Count the number of test case pairs (.in/.out files) in the directory."""
    if not tests_dir.exists():
        return 0
    
    in_files = set()
    out_files = set()
    
    for f in tests_dir.iterdir():
        if f.is_file():
            if f.suffix == ".in":
                in_files.add(f.stem)
            elif f.suffix == ".out":
                out_files.add(f.stem)
    
    # Count pairs where both .in and .out exist
    return len(in_files & out_files)


def list_test_cases(tests_dir: Path) -> List[TestCaseInfo]:
    """List all test case files with their sizes."""
    if not tests_dir.exists():
        return []
    
    tests = []
    for f in tests_dir.iterdir():
        if f.is_file() and f.suffix in (".in", ".out"):
            tests.append(TestCaseInfo(
                filename=f.name,
                size_bytes=f.stat().st_size
            ))
    
    return sorted(tests, key=lambda x: x.filename)


# =============================================================================
# API Endpoints
# =============================================================================


@router.get("", response_model=List[ProblemListItem])
async def list_problems(db: AsyncSession = Depends(get_db)):
    """
    List all problems with summary information.
    
    Returns:
        List of problems with id, name, slug, test_count, and limits.
    """
    result = await db.execute(select(Problem).order_by(Problem.id))
    problems = result.scalars().all()
    return problems


@router.get("/{problem_id}", response_model=ProblemResponse)
async def get_problem(problem_id: int, db: AsyncSession = Depends(get_db)):
    """
    Get full details of a specific problem including description.
    
    Args:
        problem_id: The problem ID
        
    Returns:
        Full problem details
        
    Raises:
        404: Problem not found
    """
    result = await db.execute(select(Problem).where(Problem.id == problem_id))
    problem = result.scalar_one_or_none()
    
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Problem with id {problem_id} not found"
        )
    
    return problem


@router.post("", response_model=ProblemResponse, status_code=status.HTTP_201_CREATED)
async def create_problem(problem_data: ProblemCreate, db: AsyncSession = Depends(get_db)):
    """
    Create a new problem.
    
    Args:
        problem_data: Problem creation data
        
    Returns:
        Created problem
        
    Raises:
        400: Slug already exists
    """
    # Check if slug already exists
    result = await db.execute(select(Problem).where(Problem.slug == problem_data.slug))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Problem with slug '{problem_data.slug}' already exists"
        )
    
    problem = Problem(**problem_data.dict())
    db.add(problem)
    await db.commit()
    await db.refresh(problem)
    
    # Create problem directory
    problem_dir = PROBLEMS_DIR / str(problem.id)
    problem_dir.mkdir(parents=True, exist_ok=True)
    
    return problem


@router.put("/{problem_id}", response_model=ProblemResponse)
async def update_problem(
    problem_id: int,
    problem_data: ProblemUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    Update problem metadata.
    
    Args:
        problem_id: The problem ID
        problem_data: Fields to update
        
    Returns:
        Updated problem
        
    Raises:
        404: Problem not found
        400: Slug already exists
    """
    result = await db.execute(select(Problem).where(Problem.id == problem_id))
    problem = result.scalar_one_or_none()
    
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Problem with id {problem_id} not found"
        )
    
    # Check slug uniqueness if being updated
    if problem_data.slug and problem_data.slug != problem.slug:
        result = await db.execute(select(Problem).where(Problem.slug == problem_data.slug))
        if result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Problem with slug '{problem_data.slug}' already exists"
            )
    
    # Update fields
    update_data = problem_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(problem, field, value)
    
    await db.commit()
    await db.refresh(problem)
    
    return problem


@router.post("/{problem_id}/tests", status_code=status.HTTP_201_CREATED)
async def upload_test_cases(
    problem_id: int,
    file: UploadFile = File(..., description="ZIP file containing test cases (.in/.out files)"),
    db: AsyncSession = Depends(get_db)
):
    """
    Upload test cases as a ZIP file.
    
    The ZIP should contain test case files named like:
    - 001.in, 001.out
    - 002.in, 002.out
    - etc.
    
    Args:
        problem_id: The problem ID
        file: ZIP file containing test cases
        
    Returns:
        Upload status with test count
        
    Raises:
        404: Problem not found
        400: Invalid file format
    """
    result = await db.execute(select(Problem).where(Problem.id == problem_id))
    problem = result.scalar_one_or_none()
    
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Problem with id {problem_id} not found"
        )
    
    # Validate file type
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="File must be a ZIP archive"
        )
    
    # Prepare directories
    tests_dir = get_problem_tests_dir(problem_id)
    tests_dir.mkdir(parents=True, exist_ok=True)
    
    # Clear existing tests
    if tests_dir.exists():
        shutil.rmtree(tests_dir)
    tests_dir.mkdir(parents=True, exist_ok=True)
    
    # Save and extract ZIP
    temp_zip_path = tests_dir / "temp_upload.zip"
    try:
        with open(temp_zip_path, "wb") as f:
            content = await file.read()
            f.write(content)
        
        # Extract ZIP with path traversal validation (Zip Slip fix)
        with zipfile.ZipFile(temp_zip_path, "r") as zip_ref:
            for member in zip_ref.namelist():
                member_path = tests_dir / member
                # Resolve the path and verify it's still within tests_dir
                resolved_path = member_path.resolve()
                tests_dir_resolved = tests_dir.resolve()
                
                if not str(resolved_path).startswith(str(tests_dir_resolved)):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Path traversal attempt detected in ZIP file"
                    )
            
            # All members are safe, now extract
            zip_ref.extractall(tests_dir)
        
        # Remove the zip file after extraction
        temp_zip_path.unlink()
        
        # Count test cases
        test_count = count_test_cases(tests_dir)
        
        # Update problem test count
        problem.test_count = test_count
        await db.commit()
        
        return {
            "message": "Test cases uploaded successfully",
            "problem_id": problem_id,
            "test_count": test_count
        }
        
    except zipfile.BadZipFile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ZIP file format"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process ZIP file: {str(e)}"
        )


@router.get("/{problem_id}/tests", response_model=TestCaseListResponse)
async def list_test_cases_endpoint(problem_id: int, db: AsyncSession = Depends(get_db)):
    """
    List all test case files for a problem.
    
    Args:
        problem_id: The problem ID
        
    Returns:
        List of test case files with sizes
        
    Raises:
        404: Problem not found
    """
    result = await db.execute(select(Problem).where(Problem.id == problem_id))
    problem = result.scalar_one_or_none()
    
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Problem with id {problem_id} not found"
        )
    
    tests_dir = get_problem_tests_dir(problem_id)
    tests = list_test_cases(tests_dir)
    
    return TestCaseListResponse(
        problem_id=problem_id,
        test_count=problem.test_count,
        tests=tests
    )


@router.delete("/{problem_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_problem(problem_id: int, db: AsyncSession = Depends(get_db)):
    """
    Delete a problem and all associated test files.
    
    Args:
        problem_id: The problem ID
        
    Raises:
        404: Problem not found
    """
    result = await db.execute(select(Problem).where(Problem.id == problem_id))
    problem = result.scalar_one_or_none()
    
    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Problem with id {problem_id} not found"
        )
    
    # Delete test files
    problem_dir = PROBLEMS_DIR / str(problem_id)
    if problem_dir.exists():
        shutil.rmtree(problem_dir)
    
    # Delete from database (cascade will handle related runs)
    await db.delete(problem)
    await db.commit()
    
    return None


@router.post("/import", status_code=status.HTTP_202_ACCEPTED)
async def import_problems(
    import_data: ProblemImportRequest,
    background_tasks: BackgroundTasks
):
    """
    Import problems from the APPS dataset asynchronously.
    
    Args:
        import_data: IDs range to import
        background_tasks: FastAPI background tasks
        
    Returns:
        Status message
    """
    if import_data.end_id < import_data.start_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="end_id must be greater than or equal to start_id"
        )
    
    # Calculate count for ingest_batch
    count = import_data.end_id - import_data.start_id + 1
    
    # Limit count to prevent excessive resource usage in one go
    if count > 100:
         raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 100 problems can be imported at once"
        )

    from backend.services.problem_ingestion import ingest_batch
    
    background_tasks.add_task(ingest_batch, import_data.start_id, count)
    
    return {"message": f"Import of {count} problems started in the background"}