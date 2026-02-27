"""
Example solution verification endpoint.

Finds the IOI example/reference solution for a problem, compiles it,
runs it against all test cases, and returns the results.
"""

import logging
import tempfile
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import config
from backend.database.models import Problem
from backend.database.session import get_db
from backend.api.problems import get_problem_tests_dir

logger = logging.getLogger(__name__)

router = APIRouter(tags=["problems"])

# Directories inside an IOI problem folder that typically hold reference solutions,
# ordered by preference.
SOLUTION_SEARCH_DIRS = ["solution", "sol", "solutions", "correct", ""]

# Compilers to try in order when the solution doesn't specify one.
DEFAULT_COMPILER = "g++-14"
DEFAULT_FLAGS = ["-O2", "-std=c++17"]


# =============================================================================
# Schemas
# =============================================================================


class TestVerifyResult(BaseModel):
    test_index: int
    passed: bool
    time_ms: Optional[float]
    memory_kb: Optional[int]
    verdict: str
    error: Optional[str] = None


class VerifyExampleResponse(BaseModel):
    problem_id: int
    solution_file: str
    compile_success: bool
    compile_log: str
    tests_passed: int
    tests_total: int
    avg_time_ms: Optional[float]
    max_time_ms: Optional[float]
    all_passed: bool
    test_results: List[TestVerifyResult]
    verified_at: datetime


# =============================================================================
# Helpers
# =============================================================================


def _find_example_solution(problem_path: Path) -> Optional[Path]:
    """
    Search for a reference C++ solution file inside an IOI problem directory.

    Looks in common subdirectory names first, then falls back to the root.
    Returns the first `.cpp` file found, preferring files whose name contains
    'sol', 'correct', 'reference', or 'example'.
    """
    def rank_cpp_file(p: Path) -> int:
        name = p.stem.lower()
        if any(kw in name for kw in ("sol", "correct", "reference", "example", "ac")):
            return 0
        return 1

    for subdir in SOLUTION_SEARCH_DIRS:
        search_dir = problem_path / subdir if subdir else problem_path
        if not search_dir.is_dir():
            continue
        cpp_files = sorted(search_dir.glob("*.cpp"), key=rank_cpp_file)
        if cpp_files:
            return cpp_files[0]

    return None


# =============================================================================
# Endpoint
# =============================================================================


@router.post(
    "/problems/{problem_id}/verify-example",
    response_model=VerifyExampleResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_example_solution(
    problem_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    Compile and benchmark the IOI reference solution for a problem.

    Requires:
    - The problem was imported from a local repository (config.PROBLEMS_REPO_DIR set).
    - Tests have been downloaded (tests_downloaded == True).

    Returns full per-test results.
    """
    # Load problem
    result = await db.execute(select(Problem).where(Problem.id == problem_id))
    problem = result.scalar_one_or_none()
    if problem is None:
        raise HTTPException(status_code=404, detail=f"Problem {problem_id} not found")

    if not problem.source_url:
        raise HTTPException(
            status_code=400,
            detail="Problem has no source URL; cannot locate example solution.",
        )

    if not problem.tests_downloaded:
        raise HTTPException(
            status_code=400,
            detail="Tests have not been downloaded yet. Sync tests first.",
        )

    # Resolve problem path in the local repo
    if not config.PROBLEMS_REPO_DIR.exists():
        raise HTTPException(
            status_code=400,
            detail="Local problem repository (PROBLEMS_REPO_DIR) is not configured.",
        )

    from backend.services.problem_ingestion import IOIIngestor
    parsed = IOIIngestor.__new__(IOIIngestor)  # parse URL without full init
    # Use the static method directly
    parsed = _parse_github_url(problem.source_url)
    problem_local_path = config.PROBLEMS_REPO_DIR / parsed["path"]

    if not problem_local_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Local problem directory not found: {problem_local_path}",
        )

    # Find example solution
    solution_file = _find_example_solution(problem_local_path)
    if solution_file is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No .cpp solution file found in {problem_local_path}. "
                "Searched: " + ", ".join(SOLUTION_SEARCH_DIRS)
            ),
        )

    logger.info(f"Verifying example solution for problem {problem_id}: {solution_file}")

    source_code = solution_file.read_text(encoding="utf-8")
    tests_dir = get_problem_tests_dir(problem_id)

    # Compile + benchmark using the existing pipeline
    from backend.sandbox.benchmark import compile_and_benchmark

    # Use a throwaway solution_id sentinel (-problem_id) so we don't pollute
    # the Solution table. compile_and_benchmark updates Solution rows by ID,
    # so we give it a dummy that won't match any real row and catch the
    # harmless DB miss below.
    DUMMY_SOLUTION_ID = -(problem_id)

    try:
        success, message, summary = await compile_and_benchmark(
            solution_id=DUMMY_SOLUTION_ID,
            source_code=source_code,
            compiler=DEFAULT_COMPILER,
            compiler_flags=DEFAULT_FLAGS,
            test_cases_dir=str(tests_dir),
            problem_id=problem_id,
            time_limit_ms=problem.time_limit_ms,
            memory_limit_mb=problem.memory_limit_mb,
            benchmark_runs=1,   # Single run is enough for correctness verification
            warmup_runs=0,
            db_session=None,    # Don't persist — this is a one-off verify
        )
    except Exception as e:
        logger.error(f"Verify example failed for problem {problem_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Benchmark error: {e}")

    # compile_and_benchmark returns (False, error_msg, None) on compile failure
    if not success or summary is None:
        return VerifyExampleResponse(
            problem_id=problem_id,
            solution_file=str(solution_file.relative_to(config.PROBLEMS_REPO_DIR)),
            compile_success=False,
            compile_log=message,
            tests_passed=0,
            tests_total=problem.test_count,
            avg_time_ms=None,
            max_time_ms=None,
            all_passed=False,
            test_results=[],
            verified_at=datetime.now(),
        )

    test_results = [
        TestVerifyResult(
            test_index=r.test_index,
            passed=r.passed,
            time_ms=r.time_ms if r.time_ms > 0 else None,
            memory_kb=r.memory_kb if r.memory_kb > 0 else None,
            verdict=r.verdict,
            error=r.error if r.error else None,
        )
        for r in summary.test_results
    ]

    return VerifyExampleResponse(
        problem_id=problem_id,
        solution_file=str(solution_file.relative_to(config.PROBLEMS_REPO_DIR)),
        compile_success=True,
        compile_log=message,
        tests_passed=summary.tests_passed,
        tests_total=summary.tests_total,
        avg_time_ms=summary.avg_time_ms if summary.avg_time_ms > 0 else None,
        max_time_ms=summary.max_time_ms if summary.max_time_ms > 0 else None,
        all_passed=summary.all_passed,
        test_results=test_results,
        verified_at=datetime.now(),
    )


def _parse_github_url(url: str) -> dict:
    """Minimal URL parser (duplicates IOIIngestor.parse_github_url without needing an instance)."""
    import re
    repo_only = re.match(r"https://github\.com/([^/]+)/([^/]+)/?$", url)
    if repo_only:
        return {"owner": repo_only.group(1), "repo": repo_only.group(2), "ref": "main", "path": ""}
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.*)", url)
    if not m:
        m = re.match(r"https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)", url)
    if not m:
        raise HTTPException(status_code=400, detail=f"Cannot parse GitHub URL: {url}")
    return {"owner": m.group(1), "repo": m.group(2), "ref": m.group(3), "path": m.group(4)}
