"""
Example solution verification endpoint.

Finds the IOI example/reference solution for a problem, compiles it,
runs it against all test cases, and returns the results.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import config
from backend.database.models import Problem
from backend.database.session import get_db
from backend.api.problems import get_problem_tests_dir
from backend.utils.task_yaml import parse_task_yaml

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


class SolutionInfo(BaseModel):
    name: str
    path: str
    expected_score: Optional[float] = None
    expected_verdict: Optional[str] = None


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
    score: Optional[float] = None
    test_results: List[TestVerifyResult]
    verified_at: datetime
    expected_score: Optional[float] = None
    expected_verdict: Optional[str] = None


class VerifyRequest(BaseModel):
    solution_path: Optional[str] = None


def _find_example_solution(problem_path: Path, task_config=None) -> Optional[Path]:
    """
    Search for a reference C++ solution file inside an IOI problem directory.
    (Kept for backward compatibility and tests).
    """
    if task_config:
        sol = task_config.get_correct_solution()
        if sol:
            return sol

    all_sols = _find_all_solutions(problem_path, task_config)
    if all_sols:
        # Prefer by verdict first (set from directory name during fallback scan), then
        # by keywords in any part of the path (handles e.g. correct/main.cpp where the
        # filename alone doesn't contain the keyword).
        for sol in all_sols:
            if sol.expected_verdict == "correct":
                return problem_path / sol.path
            path_lower = sol.path.lower().replace("\\", "/")
            if any(kw in path_lower for kw in ["correct", "sol", "reference", "example", "ac"]):
                return problem_path / sol.path
        return problem_path / all_sols[0].path
    return None


def _get_solution_expected_outcome(task_config, solution_path: Path):
    """Try to find expected score/verdict for a solution in task.yaml."""
    if not task_config or task_config.data is None:
        return None, None

    solutions = task_config.data.get("solutions")
    if not isinstance(solutions, list):
        return None, None

    for sol in solutions:
        if not isinstance(sol, dict):
            continue

        rel_path = sol.get("path")
        if not rel_path:
            continue

        if (task_config.problem_dir / rel_path).resolve() == solution_path.resolve():
            return sol.get("score"), sol.get("verdict")

    return None, None


def _find_all_solutions(problem_path: Path, task_config=None) -> List[SolutionInfo]:
    """
    Find all C++ solution files and their expected outcomes.
    If task.yaml has test_submissions, use that list exclusively.
    """
    found_solutions = []
    seen_paths = set()

    # 1. Check for test_submissions list in task.yaml (includes commented out ones)
    if task_config:
        # Use problem_path (not task_config.problem_dir) so the yaml existence check is
        # consistent with how solution files are resolved: p = problem_path / rel_path.
        yaml_exists = any(
            (problem_path / fn).exists()
            for fn in ["task.yaml", "tasks.yaml", "task.yml", "tasks.yml"]
        )
        
        if yaml_exists:
            test_submissions = task_config.get_test_submissions()
            logger.info(f"Using {len(test_submissions)} solutions from task.yaml (strict mode)")
            for rel_path in test_submissions:
                p = problem_path / rel_path
                if p.exists() and p.suffix == ".cpp":
                    abs_p = p.resolve()
                    if abs_p not in seen_paths:
                        # Try to find metadata in task_config.data['solutions'] if it exists
                        expected_score, expected_verdict = None, None
                        if task_config.data and isinstance(task_config.data.get("solutions"), list):
                            for sol_data in task_config.data["solutions"]:
                                if isinstance(sol_data, dict) and sol_data.get("path") == rel_path:
                                    expected_score = sol_data.get("score")
                                    expected_verdict = sol_data.get("verdict")
                                    break

                        found_solutions.append(SolutionInfo(
                            name=p.name,
                            path=str(rel_path),
                            expected_score=expected_score,
                            expected_verdict=expected_verdict
                        ))
                        seen_paths.add(abs_p)
            return found_solutions

    # 2. Fallback: Scanning directories ONLY if no YAML file was found
    for subdir in SOLUTION_SEARCH_DIRS:
        search_dir = problem_path / subdir if subdir else problem_path
        if not search_dir.is_dir():
            continue

        for p in search_dir.rglob("*.cpp"):
            abs_p = p.resolve()
            if abs_p not in seen_paths:
                # Try to infer verdict from directory name if it's one of the standard ones
                verdict = None
                rel_to_problem = p.relative_to(problem_path)
                parts = [part.lower() for part in rel_to_problem.parts]
                if "correct" in parts:
                    verdict = "correct"
                elif "time_limit" in parts or "tle" in parts:
                    verdict = "time_limit"
                elif "wrong_answer" in parts or "wa" in parts:
                    verdict = "wrong_answer"

                found_solutions.append(SolutionInfo(
                    name=p.name,
                    path=str(rel_to_problem),
                    expected_verdict=verdict
                ))
                seen_paths.add(abs_p)

    return found_solutions


# =============================================================================
# Endpoint
# =============================================================================


@router.get(
    "/problems/{problem_id}/solutions",
    response_model=List[SolutionInfo],
    status_code=status.HTTP_200_OK,
)
async def list_available_solutions(
    problem_id: int,
    db: AsyncSession = Depends(get_db),
):
    """
    List all available solution files for a problem and their expected outcomes.
    """
    # Load problem
    result = await db.execute(select(Problem).where(Problem.id == problem_id))
    problem = result.scalar_one_or_none()
    if problem is None:
        raise HTTPException(status_code=404, detail=f"Problem {problem_id} not found")

    if not problem.source_url:
        return []

    # Resolve problem path in the local repo
    if not config.PROBLEMS_REPO_DIR.exists():
        raise HTTPException(
            status_code=400,
            detail="Local problem repository (PROBLEMS_REPO_DIR) is not configured.",
        )

    parsed = _parse_github_url(problem.source_url)
    problem_local_path = config.PROBLEMS_REPO_DIR / parsed["path"]

    if not problem_local_path.is_dir():
        return []

    # Parse task.yaml if present
    task_config = parse_task_yaml(problem_local_path)

    return _find_all_solutions(problem_local_path, task_config)


@router.post(
    "/problems/{problem_id}/verify-example",
    response_model=VerifyExampleResponse,
    status_code=status.HTTP_200_OK,
)
async def verify_example_solution(
    problem_id: int,
    request: Optional[VerifyRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Compile and benchmark a solution for a problem.

    If request.solution_path is provided, verifies that specific solution.
    Otherwise, picks the best one from task.yaml or standard locations.
    """
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

    problem_local_path, solution_file, expected_score, expected_verdict, additional_files = \
        _resolve_verify_context(problem, request)

    logger.info(f"Verifying solution for problem {problem_id}: {solution_file}")

    source_code = solution_file.read_text(encoding="utf-8")
    tests_dir = get_problem_tests_dir(problem_id)

    # Compile + benchmark using the existing pipeline.
    # Use a throwaway solution_id sentinel (-problem_id) so we don't pollute the Solution
    # table. compile_and_benchmark updates Solution rows by ID; a negative ID won't match.
    from backend.sandbox.benchmark import compile_and_benchmark
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
            additional_files=additional_files,
            problem_dir=str(problem_local_path),
        )
    except Exception as e:
        logger.error(f"Verify example failed for problem {problem_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Benchmark error: {e}")

    # compile_and_benchmark returns (False, error_msg, None) on compile failure
    if not success or summary is None:
        return VerifyExampleResponse(
            problem_id=problem_id,
            solution_file=str(solution_file.relative_to(problem_local_path)),
            compile_success=False,
            compile_log=message,
            tests_passed=0,
            tests_total=problem.test_count,
            avg_time_ms=None,
            max_time_ms=None,
            all_passed=False,
            score=0.0,
            test_results=[],
            verified_at=datetime.now(),
            expected_score=expected_score,
            expected_verdict=expected_verdict,
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
        solution_file=str(solution_file.relative_to(problem_local_path)),
        compile_success=True,
        compile_log=message,
        tests_passed=summary.tests_passed,
        tests_total=summary.tests_total,
        avg_time_ms=summary.avg_time_ms if summary.avg_time_ms > 0 else None,
        max_time_ms=summary.max_time_ms if summary.max_time_ms > 0 else None,
        all_passed=summary.all_passed,
        score=summary.score,
        test_results=test_results,
        verified_at=datetime.now(),
        expected_score=expected_score,
        expected_verdict=expected_verdict,
    )


@router.post(
    "/problems/{problem_id}/verify-example-stream",
)
async def verify_example_solution_stream(
    problem_id: int,
    request: Optional[VerifyRequest] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Compile and benchmark a solution for a problem, streaming results via SSE.
    """
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

    problem_local_path, solution_file, expected_score, expected_verdict, additional_files = \
        _resolve_verify_context(problem, request)

    source_code = solution_file.read_text(encoding="utf-8")
    tests_dir = get_problem_tests_dir(problem_id)

    async def event_generator():
        import json
        from backend.sandbox.benchmark import compile_and_benchmark

        queue = asyncio.Queue()

        async def progress_callback(tc_result):
            await queue.put({
                "type": "test_result",
                "result": {
                    "test_index": tc_result.test_index,
                    "passed": tc_result.passed,
                    "time_ms": tc_result.time_ms if tc_result.time_ms > 0 else None,
                    "memory_kb": tc_result.memory_kb if tc_result.memory_kb > 0 else None,
                    "verdict": tc_result.verdict,
                    "error": tc_result.error if tc_result.error else None,
                }
            })

        # Run compilation and benchmarking in a separate task
        DUMMY_SOLUTION_ID = -(problem_id)

        benchmark_task = asyncio.create_task(compile_and_benchmark(
            solution_id=DUMMY_SOLUTION_ID,
            source_code=source_code,
            compiler=DEFAULT_COMPILER,
            compiler_flags=DEFAULT_FLAGS,
            test_cases_dir=str(tests_dir),
            problem_id=problem_id,
            time_limit_ms=problem.time_limit_ms,
            memory_limit_mb=problem.memory_limit_mb,
            benchmark_runs=1,
            warmup_runs=0,
            db_session=None,
            additional_files=additional_files,
            problem_dir=str(problem_local_path),
            progress_callback=progress_callback
        ))

        while True:
            # Check for new results or if benchmark is done
            try:
                # Wait for a short time for new results
                data = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield f"data: {json.dumps(data)}\n\n"
            except asyncio.TimeoutError:
                if benchmark_task.done():
                    break
                continue

        # Final summary
        try:
            success, message, summary = await benchmark_task

            if not success or summary is None:
                final_resp = {
                    "type": "final",
                    "result": {
                        "problem_id": problem_id,
                        "solution_file": str(solution_file.relative_to(problem_local_path)),
                        "compile_success": False,
                        "compile_log": message,
                        "tests_passed": 0,
                        "tests_total": problem.test_count,
                        "all_passed": False,
                        "score": 0.0,
                        "test_results": [],
                        "verified_at": datetime.now().isoformat(),
                        "expected_score": expected_score,
                        "expected_verdict": expected_verdict,
                    }
                }
            else:
                test_results = [
                    {
                        "test_index": r.test_index,
                        "passed": r.passed,
                        "time_ms": r.time_ms if r.time_ms > 0 else None,
                        "memory_kb": r.memory_kb if r.memory_kb > 0 else None,
                        "verdict": r.verdict,
                        "error": r.error if r.error else None,
                    }
                    for r in summary.test_results
                ]
                final_resp = {
                    "type": "final",
                    "result": {
                        "problem_id": problem_id,
                        "solution_file": str(solution_file.relative_to(problem_local_path)),
                        "compile_success": True,
                        "compile_log": message,
                        "tests_passed": summary.tests_passed,
                        "tests_total": summary.tests_total,
                        "avg_time_ms": summary.avg_time_ms if summary.avg_time_ms > 0 else None,
                        "max_time_ms": summary.max_time_ms if summary.max_time_ms > 0 else None,
                        "all_passed": summary.all_passed,
                        "score": summary.score,
                        "test_results": test_results,
                        "verified_at": datetime.now().isoformat(),
                        "expected_score": expected_score,
                        "expected_verdict": expected_verdict,
                    }
                }
            yield f"data: {json.dumps(final_resp)}\n\n"
        except Exception as e:
            logger.error(f"Error in benchmark stream: {e}")
            error_resp = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(error_resp)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _parse_github_url(url: str) -> dict:
    # TODO: deduplicate with IOIIngestor.parse_github_url into a shared utils module.
    """Minimal URL parser (mirrors IOIIngestor.parse_github_url without needing an instance)."""
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


def _resolve_verify_context(problem: "Problem", request: Optional[VerifyRequest]):
    """
    Shared setup for both verify endpoints.

    Validates PROBLEMS_REPO_DIR, resolves the local problem directory, parses task.yaml,
    resolves which solution file to use, and loads any grader files into additional_files.

    Returns:
        (problem_local_path, solution_file, expected_score, expected_verdict, additional_files)

    Raises:
        HTTPException 400/404 on any validation failure.
    """
    if not config.PROBLEMS_REPO_DIR.exists():
        raise HTTPException(
            status_code=400,
            detail="Local problem repository (PROBLEMS_REPO_DIR) is not configured.",
        )

    parsed = _parse_github_url(problem.source_url)
    problem_local_path = config.PROBLEMS_REPO_DIR / parsed["path"]

    if not problem_local_path.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"Local problem directory not found: {problem_local_path}",
        )

    task_config = parse_task_yaml(problem_local_path)

    # --- Resolve solution file ---
    solution_file = None
    if request and request.solution_path:
        solution_file = problem_local_path / request.solution_path
        if not solution_file.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Solution file not found: {request.solution_path}",
            )
    else:
        if task_config:
            solution_file = task_config.get_correct_solution()
            if solution_file:
                logger.info(f"Found solution from task.yaml: {solution_file}")
        if not solution_file:
            all_sols = _find_all_solutions(problem_local_path, task_config)
            if all_sols:
                solution_file = problem_local_path / all_sols[0].path

    if solution_file is None:
        raise HTTPException(
            status_code=404,
            detail=f"No .cpp solution file found in {problem_local_path}.",
        )

    expected_score, expected_verdict = _get_solution_expected_outcome(task_config, solution_file)

    # --- Load grader files ---
    additional_files: dict = {}
    grader_files = task_config.get_grader_files() if task_config else []
    if grader_files:
        logger.info(f"Found grader files from task.yaml: {[f.name for f in grader_files]}")
    else:
        grader_dir = problem_local_path / "grader"
        if grader_dir.is_dir():
            grader_files = list(grader_dir.glob("*"))
            logger.info(f"Found grader directory with files: {[f.name for f in grader_files if f.is_file()]}")
        else:
            logger.info(f"No grader directory found at {grader_dir}")

    for f in grader_files:
        if f.is_file() and f.suffix in [".cpp", ".h", ".hpp", ".c"]:
            additional_files[f.name] = f.read_text(encoding="utf-8")

    return problem_local_path, solution_file, expected_score, expected_verdict, additional_files