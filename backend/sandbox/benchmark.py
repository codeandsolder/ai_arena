"""
Benchmark orchestration module for the AI Optimization Arena sandbox system.

Handles running solutions against test cases, collecting results, and updating database.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Solution, TestResult, Problem
from backend.database.session import get_db_session
from backend.sandbox.container import ContainerManager, ContainerResult, get_container_manager
from backend.sandbox.compiler import compile_in_container

logger = logging.getLogger(__name__)


@dataclass
class BenchmarkConfig:
    """Configuration for benchmark execution."""
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256
    benchmark_runs: int = 3
    warmup_runs: int = 1
    max_timeout_seconds: int = 300


@dataclass
class TestCaseResult:
    """Result of running a single test case."""
    test_index: int
    passed: bool
    actual_output: str
    time_ms: float
    memory_kb: int
    exit_code: int
    error: str
    verdict: str
    all_times_ms: List[float] = field(default_factory=list)


@dataclass
class BenchmarkSummary:
    """Summary of benchmark results across all test cases."""
    solution_id: int
    tests_passed: int
    tests_total: int
    avg_time_ms: float
    max_time_ms: float
    max_memory_kb: int
    all_passed: bool
    test_results: List[TestCaseResult] = field(default_factory=list)


class BenchmarkError(Exception):
    """Raised when benchmark execution fails."""
    pass


async def benchmark_solution(
    solution_id: int,
    solution_binary_path: str,
    test_cases_dir: str,
    time_limit_ms: int = 2000,
    memory_limit_mb: int = 256,
    benchmark_runs: int = 3,
    warmup_runs: int = 1,
    db_session: Optional[AsyncSession] = None
) -> BenchmarkSummary:
    """
    Run benchmark for a solution against all test cases.
    
    Args:
        solution_id: ID of the solution to benchmark
        solution_binary_path: Path to the compiled solution binary
        test_cases_dir: Directory containing test case files
        time_limit_ms: Time limit per test in milliseconds
        memory_limit_mb: Memory limit per test in MB
        benchmark_runs: Number of benchmark iterations per test
        warmup_runs: Number of warmup iterations (not counted)
        db_session: Database session (if None, a new one will be created)
        
    Returns:
        BenchmarkSummary with aggregated results
    """
    logger.info(f"Starting benchmark for solution {solution_id}")
    
    # Validate test cases directory
    test_cases_path = Path(test_cases_dir)
    if not test_cases_path.exists():
        raise BenchmarkError(f"Test cases directory not found: {test_cases_dir}")
    
    # Find all test case pairs (input/expected output)
    test_cases = _find_test_cases(test_cases_path)
    if not test_cases:
        raise BenchmarkError(f"No test cases found in {test_cases_dir}")
    
    logger.info(f"Found {len(test_cases)} test cases for solution {solution_id}")
    
    # Get container manager
    container_manager = get_container_manager()
    
    # Calculate timeout (time_limit * num_tests * 5, capped at 300s)
    test_count = len(test_cases)
    timeout_seconds = min(
        (time_limit_ms * test_count * 5) / 1000,
        300  # Max 5 minutes
    )
    
    # Run each test case
    test_results: List[TestCaseResult] = []
    
    for i, (input_file, expected_output_file) in enumerate(test_cases):
        test_index = i + 1
        logger.debug(f"Running test case {test_index}/{test_count} for solution {solution_id}")
        
        try:
            result = await _run_single_test(
                container_manager=container_manager,
                solution_binary_path=solution_binary_path,
                input_file=input_file,
                expected_output_file=expected_output_file,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                benchmark_runs=benchmark_runs,
                warmup_runs=warmup_runs,
                timeout_seconds=timeout_seconds
            )
            result.test_index = test_index
            test_results.append(result)
            
        except Exception as e:
            logger.error(f"Test case {test_index} failed: {e}")
            # Add failed result
            test_results.append(TestCaseResult(
                test_index=test_index,
                passed=False,
                actual_output="",
                time_ms=0.0,
                memory_kb=0,
                exit_code=-1,
                error=str(e),
                verdict="RE"
            ))
    
    # Aggregate results
    summary = _aggregate_results(solution_id, test_results)
    
    # Store results in database
    await _store_results(summary, test_results, db_session)
    
    logger.info(
        f"Benchmark complete for solution {solution_id}: "
        f"{summary.tests_passed}/{summary.tests_total} tests passed"
    )
    
    return summary


def _find_test_cases(test_cases_dir: Path) -> List[Tuple[Path, Path]]:
    """
    Find all test case pairs in the directory.
    
    Looks for files matching patterns like:
    - input_1.txt / output_1.txt
    - 1.in / 1.out
    - test1.in / test1.out
    
    Returns:
        List of (input_file, expected_output_file) tuples
    """
    test_cases = []
    
    # Try various naming patterns
    patterns = [
        # input_N.txt / output_N.txt
        ("input_{}.txt", "output_{}.txt"),
        # N.in / N.out
        ("{}.in", "{}.out"),
        # testN.in / testN.out
        ("test{}.in", "test{}.out"),
        # N.txt / N.ans
        ("{}.txt", "{}.ans"),
    ]
    
    for input_pattern, output_pattern in patterns:
        for i in range(1, 1000):  # Reasonable upper limit
            input_file = test_cases_dir / input_pattern.format(i)
            output_file = test_cases_dir / output_pattern.format(i)
            
            if input_file.exists() and output_file.exists():
                test_cases.append((input_file, output_file))
            elif i == 1 and not test_cases:
                # If first pattern doesn't work, try next
                continue
            elif not input_file.exists():
                # No more files with this pattern
                break
        
        if test_cases:
            # Found files with this pattern
            break
    
    return test_cases


async def _run_single_test(
    container_manager: ContainerManager,
    solution_binary_path: str,
    input_file: Path,
    expected_output_file: Path,
    time_limit_ms: int,
    memory_limit_mb: int,
    benchmark_runs: int,
    warmup_runs: int,
    timeout_seconds: float
) -> TestCaseResult:
    """
    Run a single test case using the test harness.
    
    Args:
        container_manager: ContainerManager instance
        solution_binary_path: Path to solution binary
        input_file: Path to input file
        expected_output_file: Path to expected output file
        time_limit_ms: Time limit in ms
        memory_limit_mb: Memory limit in MB
        benchmark_runs: Number of benchmark iterations
        warmup_runs: Number of warmup iterations
        timeout_seconds: Container timeout
        
    Returns:
        TestCaseResult
    """
    # Build harness command
    harness_cmd = [
        "/opt/harness/run_test",
        solution_binary_path,
        str(input_file),
        str(expected_output_file),
        str(time_limit_ms),
        str(memory_limit_mb),
        str(benchmark_runs),
        str(warmup_runs)
    ]
    
    # Mount volumes - binary must be accessible
    volumes = {
        solution_binary_path: {"bind": solution_binary_path, "mode": "ro"},
        str(input_file): {"bind": str(input_file), "mode": "ro"},
        str(expected_output_file): {"bind": str(expected_output_file), "mode": "ro"},
    }
    
    # Run container with harness
    result = await container_manager.execute_command(
        command=harness_cmd,
        volumes=volumes,
        mem_limit=f"{memory_limit_mb + 64}m",  # Extra memory for harness overhead
        timeout=int(timeout_seconds),
        network_disabled=True
    )
    
    if not result.success and not result.stdout:
        # Container failed to run
        return TestCaseResult(
            test_index=0,
            passed=False,
            actual_output="",
            time_ms=0.0,
            memory_kb=0,
            exit_code=result.exit_code,
            error=result.error or "Container execution failed",
            verdict="RE"
        )
    
    # Parse JSON output from harness
    try:
        # Extract JSON from stdout (may have other output before/after)
        stdout = result.stdout.strip()
        
        # Find JSON object (look for { and })
        json_start = stdout.find("{")
        json_end = stdout.rfind("}")
        
        if json_start == -1 or json_end == -1:
            raise ValueError("No JSON object found in output")
        
        json_str = stdout[json_start:json_end + 1]
        harness_result = json.loads(json_str)
        
        return TestCaseResult(
            test_index=0,  # Will be set by caller
            passed=harness_result.get("passed", False),
            actual_output=harness_result.get("actual_output", ""),
            time_ms=harness_result.get("time_ms", 0.0) or 0.0,
            memory_kb=harness_result.get("memory_kb", 0),
            exit_code=harness_result.get("exit_code", -1),
            error=harness_result.get("error", ""),
            verdict=harness_result.get("verdict", "RE"),
            all_times_ms=harness_result.get("all_times_ms", [])
        )
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse harness output: {e}")
        logger.debug(f"Raw output: {result.stdout}")
        return TestCaseResult(
            test_index=0,
            passed=False,
            actual_output=result.stdout[:1000],  # Truncate long output
            time_ms=0.0,
            memory_kb=0,
            exit_code=result.exit_code,
            error=f"Failed to parse harness output: {e}",
            verdict="RE"
        )
    except Exception as e:
        logger.error(f"Error processing test result: {e}")
        return TestCaseResult(
            test_index=0,
            passed=False,
            actual_output="",
            time_ms=0.0,
            memory_kb=0,
            exit_code=-1,
            error=str(e),
            verdict="RE"
        )


def _aggregate_results(
    solution_id: int,
    test_results: List[TestCaseResult]
) -> BenchmarkSummary:
    """
    Aggregate results across all test cases.
    
    Args:
        solution_id: Solution ID
        test_results: List of individual test results
        
    Returns:
        BenchmarkSummary
    """
    tests_total = len(test_results)
    tests_passed = sum(1 for r in test_results if r.passed)
    
    # Calculate timing statistics from passed tests only
    passed_times = [r.time_ms for r in test_results if r.passed and r.time_ms > 0]
    
    avg_time_ms = sum(passed_times) / len(passed_times) if passed_times else 0.0
    max_time_ms = max(passed_times) if passed_times else 0.0
    
    # Get max memory usage
    memory_values = [r.memory_kb for r in test_results if r.memory_kb > 0]
    max_memory_kb = max(memory_values) if memory_values else 0
    
    return BenchmarkSummary(
        solution_id=solution_id,
        tests_passed=tests_passed,
        tests_total=tests_total,
        avg_time_ms=avg_time_ms,
        max_time_ms=max_time_ms,
        max_memory_kb=max_memory_kb,
        all_passed=tests_passed == tests_total,
        test_results=test_results
    )


async def _store_results(
    summary: BenchmarkSummary,
    test_results: List[TestCaseResult],
    db_session: Optional[AsyncSession] = None
) -> None:
    """
    Store benchmark results in the database.
    
    Args:
        summary: Benchmark summary
        test_results: Individual test results
        db_session: Optional database session
    """
    should_close_session = False
    
    if db_session is None:
        db_session = await get_db_session().__anext__()
        should_close_session = True
    
    try:
        # Update Solution with aggregate stats
        from sqlalchemy import update
        
        await db_session.execute(
            update(Solution)
            .where(Solution.id == summary.solution_id)
            .values(
                tests_passed=summary.tests_passed,
                tests_total=summary.tests_total,
                avg_time_ms=summary.avg_time_ms if summary.avg_time_ms > 0 else None,
                max_time_ms=summary.max_time_ms if summary.max_time_ms > 0 else None,
                max_memory_kb=summary.max_memory_kb if summary.max_memory_kb > 0 else None,
                status="completed" if summary.all_passed else "failed"
            )
        )
        
        # Insert TestResult records
        for result in test_results:
            test_result = TestResult(
                solution_id=summary.solution_id,
                test_index=result.test_index,
                passed=result.passed,
                actual_output=result.actual_output[:10000] if result.actual_output else None,  # Limit size
                expected_output=None,  # Not stored to save space
                time_ms=result.time_ms if result.time_ms > 0 else None,
                memory_kb=result.memory_kb if result.memory_kb > 0 else None,
                exit_code=result.exit_code,
                error_output=result.error[:1000] if result.error else None,
                verdict=result.verdict
            )
            db_session.add(test_result)
        
        await db_session.commit()
        logger.debug(f"Stored benchmark results for solution {summary.solution_id}")
        
    except Exception as e:
        await db_session.rollback()
        logger.error(f"Failed to store benchmark results: {e}")
        raise
    finally:
        if should_close_session:
            await db_session.close()


async def compile_and_benchmark(
    solution_id: int,
    source_code: str,
    compiler: str,
    compiler_flags: List[str],
    test_cases_dir: str,
    problem_id: int,
    time_limit_ms: int = 2000,
    memory_limit_mb: int = 256,
    benchmark_runs: int = 3,
    warmup_runs: int = 1,
    db_session: Optional[AsyncSession] = None
) -> Tuple[bool, str, Optional[BenchmarkSummary]]:
    """
    Compile a solution and run benchmarks if compilation succeeds.
    
    This is a convenience function that combines compilation and benchmarking.
    
    Args:
        solution_id: Solution ID
        source_code: C++ source code
        compiler: Compiler to use
        compiler_flags: Compiler flags
        test_cases_dir: Directory with test cases
        problem_id: Problem ID (for fetching config)
        time_limit_ms: Time limit per test
        memory_limit_mb: Memory limit per test
        benchmark_runs: Number of benchmark runs
        warmup_runs: Number of warmup runs
        db_session: Optional database session
        
    Returns:
        Tuple of (success, message, benchmark_summary or None)
    """
    container_manager = get_container_manager()
    
    # Create temporary directory for compilation
    with tempfile.TemporaryDirectory() as temp_dir:
        binary_path = os.path.join(temp_dir, "solution")
        source_path = os.path.join(temp_dir, "solution.cpp")
        
        # Write source code
        with open(source_path, "w") as f:
            f.write(source_code)
        
        # Compile
        from backend.sandbox.compiler import validate_compiler, validate_flags, perform_safety_check
        
        validated_compiler = validate_compiler(compiler)
        validated_flags = validate_flags(compiler_flags)
        
        # Safety check
        violations = perform_safety_check(source_code)
        if violations:
            error_msg = "Source code failed safety check:\n" + "\n".join(violations)
            await _update_solution_compile_status(
                solution_id, False, error_msg, db_session
            )
            return False, error_msg, None
        
        # Build compilation command
        cmd_parts = [validated_compiler, "/workspace/solution.cpp", "-o", "/workspace/solution"]
        cmd_parts.extend(validated_flags)
        cmd_str = " ".join(cmd_parts)
        
        logger.info(f"Compiling solution {solution_id} with command: {cmd_str}")
        
        # Run compilation in container
        compile_result = await container_manager.execute_command(
            command=["sh", "-c", cmd_str],
            volumes={
                source_path: {"bind": "/workspace/solution.cpp", "mode": "ro"},
                temp_dir: {"bind": "/workspace", "mode": "rw"}
            },
            mem_limit="512m",
            timeout=60,
            network_disabled=True
        )
        
        if not compile_result.success:
            error_msg = f"Compilation failed:\n{compile_result.stderr or compile_result.stdout}"
            await _update_solution_compile_status(
                solution_id, False, error_msg, db_session
            )
            return False, error_msg, None
        
        # Update compilation success
        await _update_solution_compile_status(
            solution_id, True, compile_result.stdout + "\n" + compile_result.stderr, db_session
        )
        
        # Now run benchmarks
        try:
            summary = await benchmark_solution(
                solution_id=solution_id,
                solution_binary_path=os.path.join(temp_dir, "solution"),
                test_cases_dir=test_cases_dir,
                time_limit_ms=time_limit_ms,
                memory_limit_mb=memory_limit_mb,
                benchmark_runs=benchmark_runs,
                warmup_runs=warmup_runs,
                db_session=db_session
            )
            
            return True, "Compilation and benchmarking successful", summary
            
        except Exception as e:
            error_msg = f"Benchmarking failed: {e}"
            logger.error(error_msg)
            return True, "Compilation succeeded but benchmarking failed", None


async def _update_solution_compile_status(
    solution_id: int,
    success: bool,
    compile_log: str,
    db_session: Optional[AsyncSession] = None
) -> None:
    """
    Update solution compilation status in database.
    
    Args:
        solution_id: Solution ID
        success: Whether compilation succeeded
        compile_log: Compilation output
        db_session: Optional database session
    """
    should_close_session = False
    
    if db_session is None:
        db_session = await get_db_session().__anext__()
        should_close_session = True
    
    try:
        from sqlalchemy import update
        
        await db_session.execute(
            update(Solution)
            .where(Solution.id == solution_id)
            .values(
                compile_success=success,
                compile_log=compile_log[:10000],  # Limit size
                status="compiled" if success else "compile_error"
            )
        )
        await db_session.commit()
        
    except Exception as e:
        await db_session.rollback()
        logger.error(f"Failed to update solution status: {e}")
    finally:
        if should_close_session:
            await db_session.close()
