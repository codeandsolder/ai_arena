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
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import config
from backend.database.models import Problem
from backend.database.session import AsyncSessionLocal, get_db
from backend.orchestrator.model_client import ModelClient

router = APIRouter(prefix="/problems", tags=["problems"])


# =============================================================================
# Pydantic Schemas
# =============================================================================


class ProblemMetadataParseRequest(BaseModel):
    """Schema for triggering metadata parsing."""
    model: str = Field(..., description="LLM model to use for parsing")
    prompt: str = Field(..., description="Prompt to use for parsing")
    fallback_model: Optional[str] = Field(None, description="Model to use if no editorial is found")


class ProblemImportRequest(BaseModel):
    """Schema for importing problems from APPS dataset."""
    start_id: int = Field(..., ge=0, description="Starting APPS problem ID")
    end_id: int = Field(..., ge=0, description="Ending APPS problem ID")


class GitHubImportRequest(BaseModel):
    """Schema for importing a problem from GitHub."""
    url: str = Field(..., description="GitHub URL of the problem")
    download_tests: bool = Field(default=False, description="Whether to download tests immediately")


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
    scoring_mode: str

    model_config = ConfigDict(from_attributes=True)


class ProblemResponse(ProblemBase):
    """Schema for full problem response."""
    id: int
    test_count: int
    sample_input: Optional[str] = None
    sample_output: Optional[str] = None
    source_url: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    tests_downloaded: bool = False
    created_at: datetime
    short_description: Optional[str] = None
    tags: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


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
    return config.PROBLEMS_DIR / str(problem_id) / "tests"


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


@router.get("/{problem_id}/tests/{filename}")
async def get_problem_test_file(problem_id: int, filename: str):
    """
    Get content of a specific test case file.
    """
    if ".." in filename or filename.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    tests_dir = get_problem_tests_dir(problem_id)
    file_path = tests_dir / filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Test file not found")

    from fastapi.responses import FileResponse
    return FileResponse(file_path)


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

    problem = Problem(**problem_data.model_dump())
    db.add(problem)
    await db.commit()
    await db.refresh(problem)

    # Create problem directory
    problem_dir = config.PROBLEMS_DIR / str(problem.id)
    problem_dir.mkdir(parents=True, exist_ok=True)

    return problem


@router.post("/parse-metadata")
async def parse_metadata(
    request: ProblemMetadataParseRequest,
    background_tasks: BackgroundTasks
):
    """
    Trigger a background job to parse metadata for all available problems.
    """
    from backend.services.metadata_extraction import parse_all_problems_metadata
    
    model_client = ModelClient()
    
    # Pass the session maker instead of the request-scoped db session
    # to avoid issues where the session is closed before the background task runs
    background_tasks.add_task(
        parse_all_problems_metadata,
        session_factory=AsyncSessionLocal,
        model_client=model_client,
        model=request.model,
        prompt=request.prompt,
        fallback_model=request.fallback_model
    )
    
    return {"message": "Metadata parsing started in background"}


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
    update_data = problem_data.model_dump(exclude_unset=True)
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

    # 100 MB limit for uncompressed files to prevent Zip Bombs
    MAX_UNCOMPRESSED_SIZE_BYTES = 100 * 1024 * 1024

    # Save and extract ZIP
    temp_zip_path = tests_dir / "temp_upload.zip"
    try:
        with open(temp_zip_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Extract ZIP with path traversal and zip bomb validation
        with zipfile.ZipFile(temp_zip_path, "r") as zip_ref:
            total_uncompressed_size = 0

            # Use infolist() to inspect file metadata before extraction
            for member in zip_ref.infolist():
                # 1. Zip Bomb Check
                total_uncompressed_size += member.file_size
                if total_uncompressed_size > MAX_UNCOMPRESSED_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Uncompressed ZIP size exceeds maximum allowed limit of {MAX_UNCOMPRESSED_SIZE_BYTES / (1024*1024)}MB. Potential Zip Bomb detected."
                    )

                # 2. Path Traversal Check (Zip Slip)
                member_path = tests_dir / member.filename
                resolved_path = member_path.resolve()
                tests_dir_resolved = tests_dir.resolve()

                if not resolved_path.is_relative_to(tests_dir_resolved):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Path traversal attempt detected in ZIP file"
                    )

            # All members are safe and within size limits, now extract
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
    except HTTPException:
        # Re-raise FastAPIs HTTPExceptions as is
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process ZIP file: {str(e)}"
        )


# Bug fix: removed the duplicate GET /{problem_id}/tests route that existed at line 196
# in the original. Only one definition kept here with correct test_count from the DB.
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
    problem_dir = config.PROBLEMS_DIR / str(problem_id)
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


@router.post("/import/github", response_model=ProblemResponse, status_code=status.HTTP_201_CREATED)
async def import_problem_from_github(
    import_data: GitHubImportRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Import a single problem from a GitHub repository.

    Args:
        import_data: GitHub URL to import
        db: Database session

    Returns:
        Created problem details
    """
    from backend.services.problem_ingestion import IOIIngestor

    async with IOIIngestor(db) as ingestor:
        problem = await ingestor.ingest_from_github(import_data.url)
        if not problem:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to import problem from GitHub. Ensure the URL is correct and contains a PDF statement."
            )

        if import_data.download_tests:
            # Sync tests in background
            from backend.database.session import AsyncSessionLocal
            async def sync_task(p_id: int):
                async with AsyncSessionLocal() as session:
                    async with IOIIngestor(session) as sync_ingestor:
                        await sync_ingestor.sync_tests(p_id)

            background_tasks.add_task(sync_task, problem.id)

        return problem


@router.post("/{problem_id}/tests/sync-github")
async def sync_problem_tests_from_github(
    problem_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Sync test cases from GitHub for a problem.
    Runs as a background task.

    Args:
        problem_id: The problem ID
        background_tasks: FastAPI background tasks
        db: Database session

    Returns:
        Status message
    """
    result = await db.execute(select(Problem).where(Problem.id == problem_id))
    problem = result.scalar_one_or_none()

    if problem is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Problem with id {problem_id} not found"
        )

    if not problem.source_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Problem {problem_id} was not imported from GitHub and has no source URL."
        )

    from backend.services.problem_ingestion import IOIIngestor

    async def sync_task(p_id: int):
        from backend.database.session import AsyncSessionLocal
        async with AsyncSessionLocal() as session:
            async with IOIIngestor(session) as ingestor:
                await ingestor.sync_tests(p_id)

    background_tasks.add_task(sync_task, problem_id)

    return {"message": f"Test synchronization for problem {problem_id} started in the background"}