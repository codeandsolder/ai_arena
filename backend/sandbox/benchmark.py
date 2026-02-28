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
    __test__ = False
    test_index: int
    test_name: str
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
    score: float = 0.0
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
    db_session: Optional[AsyncSession] = None,
    progress_callback: Optional[Any] = None
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
        progress_callback: Optional async callback for intermediate results
        
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
                timeout_seconds=timeout_seconds,
                test_name=input_file.name
            )
            result.test_index = test_index
            test_results.append(result)
            
            if progress_callback:
                await progress_callback(result)
            
        except Exception as e:
            logger.error(f"Test case {test_index} failed: {e}")
            # Add failed result
            failed_result = TestCaseResult(
                test_index=test_index,
                test_name=input_file.name,
                passed=False,
                actual_output="",
                time_ms=0.0,
                memory_kb=0,
                exit_code=-1,
                error=str(e),
                verdict="RE"
            )
            test_results.append(failed_result)
            if progress_callback:
                await progress_callback(failed_result)
    
    # Aggregate results
    summary = _aggregate_results(solution_id, test_results, test_cases_dir=test_cases_dir)
    
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
        for i in range(0, 1000):  # Reasonable upper limit
            input_file = test_cases_dir / input_pattern.format(i)
            output_file = test_cases_dir / output_pattern.format(i)
            
            if input_file.exists() and output_file.exists():
                test_cases.append((input_file, output_file))
            elif i == 0 and not test_cases:
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
    timeout_seconds: float,
    test_name: str
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
        "/tmp/solution.exe",
        "/tmp/input.in",
        "/tmp/expected.out",
        str(time_limit_ms),
        str(memory_limit_mb),
        str(benchmark_runs),
        str(warmup_runs)
    ]
    
    # Mount volumes - binary must be accessible
    # Use absolute paths for host side to avoid issues
    host_sol_path = os.path.abspath(solution_binary_path)
    host_in_path = os.path.abspath(str(input_file))
    host_out_path = os.path.abspath(str(expected_output_file))

    volumes = {
        host_sol_path: {"bind": "/tmp/solution.exe", "mode": "ro"},
        host_in_path: {"bind": "/tmp/input.in", "mode": "ro"},
        host_out_path: {"bind": "/tmp/expected.out", "mode": "ro"},
    }
    
    # Run container with harness
    result = await container_manager.execute_command(
        command=harness_cmd,
        volumes=volumes,
        mem_limit=f"{memory_limit_mb + 64}m",  # Extra memory for harness overhead
        timeout=int(timeout_seconds),
        network_disabled=True,
        working_dir="/tmp"
    )
    
    if not result.success and not result.stdout:
        # Container failed to run
        return TestCaseResult(
            test_index=0,
            test_name=test_name,
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
            test_index=0,
            test_name=test_name,  # Will be set by caller
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
            test_name=test_name,
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
            test_name=test_name,
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
    test_results: List[TestCaseResult],
    test_cases_dir: Optional[str] = None
) -> BenchmarkSummary:
    """
    Aggregate results across all test cases.
    
    Args:
        solution_id: Solution ID
        test_results: List of individual test results
        test_cases_dir: Optional directory to check for task.yaml for subtask scoring
        
    Returns:
        BenchmarkSummary
    """
    tests_total = len(test_results)
    tests_passed = sum(1 for r in test_results if r.passed)
    
    # Calculate timing statistics from all tests with timing data
    timing_data = [r.time_ms for r in test_results if r.time_ms > 0]
    
    avg_time_ms = sum(timing_data) / len(timing_data) if timing_data else 0.0
    max_time_ms = max(timing_data) if timing_data else 0.0
    
    # Get max memory usage
    memory_values = [r.memory_kb for r in test_results if r.memory_kb > 0]
    max_memory_kb = max(memory_values) if memory_values else 0
    
    score = 0.0
    
    # Try to calculate score based on task.yaml subtasks
    if test_cases_dir:
        from backend.utils.task_yaml import parse_task_yaml
        import fnmatch
        
        problem_dir = Path(test_cases_dir).parent if Path(test_cases_dir).name == "tests" else Path(test_cases_dir)
        task_config = parse_task_yaml(problem_dir)
        
        if task_config:
            subtasks = task_config.get_subtasks()
            if subtasks:
                for subtask in subtasks:
                    subtask_score = subtask.get("score", 0.0)
                    patterns = subtask.get("patterns", [])
                    
                    if not patterns:
                        continue
                        
                    # Find which tests belong to this subtask
                    subtask_tests = []
                    for pattern_dict in patterns:
                        inp_pattern = pattern_dict.get("input")
                        if not inp_pattern:
                            continue
                            
                        for r in test_results:
                            if fnmatch.fnmatch(r.test_name, inp_pattern):
                                subtask_tests.append(r)
                                
                    # A subtask score is awarded only if all its test cases passed
                    if subtask_tests and all(t.passed for t in subtask_tests):
                        score += subtask_score
            else:
                # No subtasks defined, proportional scoring
                if tests_total > 0:
                    score = (tests_passed / tests_total) * 100.0
        else:
            # No task.yaml, proportional scoring
            if tests_total > 0:
                score = (tests_passed / tests_total) * 100.0
    else:
        # Default proportional scoring
        if tests_total > 0:
            score = (tests_passed / tests_total) * 100.0
            
    return BenchmarkSummary(
        solution_id=solution_id,
        tests_passed=tests_passed,
        tests_total=tests_total,
        avg_time_ms=avg_time_ms,
        max_time_ms=max_time_ms,
        max_memory_kb=max_memory_kb,
        all_passed=tests_passed == tests_total,
        score=score,
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
        async with get_db_session() as session:
            return await _store_results(summary, test_results, db_session=session)
    
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


import shutil
import re
import json
import tempfile
from pathlib import Path
from typing import Optional, Any, List
from sqlalchemy.ext.asyncio import AsyncSession

# (Assuming BenchmarkSummary, TestCaseResult, get_container_manager, _store_results are already imported)

async def _run_task_maker(
    solution_id: int,
    problem_id: int,
    source_code: str,
    problem_dir: str,
    memory_limit_mb: int = 256,
    db_session: Optional[AsyncSession] = None,
    progress_callback: Optional[Any] = None
) -> BenchmarkSummary:
    """
    Run evaluation using Task Maker CLI (tmc).
    """
    logger.info(f"Using Task Maker for solution {solution_id}")
    container_manager = get_container_manager()
    
    # 1. Find the repository root by looking upwards for a TMC base file
    problem_path = Path(problem_dir).absolute()
    repo_root = problem_path
    current = problem_path
    base_file = None
    
    while current != current.parent:
        if (current / "base-batch.yaml").exists():
            repo_root = current
            base_file = "base-batch.yaml"
            break
        elif (current / "base.yaml").exists():
            repo_root = current
            base_file = "base.yaml"
            break
        current = current.parent
        
    # 2. Set up an isolated temporary workspace so TMC doesn't pollute the host repo
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_repo = Path(temp_dir)
        
        # Copy base configuration file to the temp repo root
        if base_file:
            shutil.copy2(repo_root / base_file, temp_repo / base_file)
            
        # Copy the task directory to temp_repo/task, excluding the original tc/ dir.
        # We populate tc/ from the already-processed tests_dir instead (sync_tests has
        # already downloaded and decompressed everything to plain N.in / N.out files).
        temp_task_dir = temp_repo / "task"
        shutil.copytree(problem_path, temp_task_dir, ignore=shutil.ignore_patterns("tc"))

        # Copy pre-processed test cases into tc/
        from backend.services.problem_ingestion import get_problem_tests_dir
        base_dir = Path(__file__).parent.parent.parent
        tests_dir = base_dir / "backend" / "data"/ "problems" / str(problem_id) / "tests"
        tc_dir = temp_task_dir / "tc"
        tc_dir.mkdir()
        test_count = 0
        if tests_dir.exists():
            for in_file in sorted(tests_dir.glob("*.in")):
                out_file = in_file.with_suffix(".out")
                if out_file.exists():
                    shutil.copy2(in_file, tc_dir / in_file.name)
                    shutil.copy2(out_file, tc_dir / out_file.name)
                    test_count += 1
            logger.info(f"Copied {test_count} pre-processed test case(s) from {tests_dir} into temp tc/")
        else:
            logger.warning(f"Pre-processed tests_dir not found at {tests_dir}; tc/ will be empty")

        # ---------------------------------------------------------
        # Patch task.yaml for tmc compatibility
        # ---------------------------------------------------------
        task_yaml_path = temp_task_dir / "task.yaml"
        if task_yaml_path.exists():
            content = task_yaml_path.read_text(encoding="utf-8")

            # Remove 's' from time_limit (e.g., "3.0s" -> "3.0")
            content = re.sub(r'(time_limit:\s*[\d\.]+)\s*s', r'\1', content)

            # Remove units from memory_limit (e.g., "512MiB" -> "512")
            content = re.sub(r'(memory_limit:\s*[\d\.]+)\s*[A-Za-z]+', r'\1', content)

            # Replace long_name with title
            if 'title:' not in content and 'long_name:' in content:
                content = content.replace('long_name:', 'title:')

            # Replace the subtasks block with a single subtask referencing the flat
            # N.in / N.out files from sync_tests (original globs used .gz names).
            subtasks_block = "subtasks:\n  - points: 100\n    testcases:\n"
            for i in range(test_count):
                subtasks_block += f"    - input: tc/{i}.in\n      output: tc/{i}.out\n"
            content = re.sub(r'(?ms)^subtasks:.*', subtasks_block.rstrip(), content)

            task_yaml_path.write_text(content, encoding="utf-8")
        
        # Write the solution directly inside the task's solution/ directory
        sol_dir = temp_task_dir / "solution"
        sol_dir.mkdir(exist_ok=True)
        solution_filename = "sandbox_solution.cpp"
        source_path = sol_dir / solution_filename
        with open(source_path, "w", encoding="utf-8") as f:
            f.write(source_code)
            
        # Mount the temp repo as RW
        volumes = {
            str(temp_repo.absolute()): {"bind": "/repo", "mode": "rw"}
        }
        
        # Initialize Git at the ROOT of the repo so base-batch.yaml is tracked.
        # Use `git add -f .` to forcefully bypass any .gitignore files hiding our testcases!
        script = " ".join([
            "tmc",
            "-s", f"solution/{solution_filename}.cpp",
            "-W", "StatementPresent",
            "-W", "StatementValid",
            "-W", "StatementCompiledOrGit",
            "-W", "StatementSubtasks",
            "-W", "AttNoDirectory",
            "-W", "AttSampleFiles",
            "--ui", "json"
        ])
        command = ["sh", "-c", script]
        
        container = await container_manager.create_container(
            command=command,
            volumes=volumes,
            mem_limit=f"{memory_limit_mb + 512}m",
            network_disabled=True,
            working_dir="/repo",
            read_only=False,
            cap_add=["SYS_ADMIN"],
            user="root" # Required to manipulate docker volumes flawlessly
        )
        
        try:
            result = await container_manager.run_container(container, timeout=300)
        finally:
            await container_manager.cleanup_container(container)
            
        if not result.success and not result.stdout:
             raise BenchmarkError(f"Task Maker execution failed: {result.error or 'Unknown error'}\nStderr: {result.stderr}")

        # Parse tmc output
        test_results: List[TestCaseResult] = []
        subtask_results: List[TestCaseResult] = []
        final_summary = None
        task_score = None
        
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
                
            try:
                msg = json.loads(line)
                
                # Check for individual granular test results
                if "IOITestcaseScore" in msg:
                    data = msg["IOITestcaseScore"]
                    test_name = data.get("test_name", f"test_{len(test_results)+1}")
                    passed = data.get("score", 0.0) > 0.0 or data.get("verdict") == "Correct"
                    
                    verdict_map = {
                        "Correct": "AC",
                        "WrongAnswer": "WA",
                        "TimeLimit": "TLE",
                        "MemoryLimit": "MLE",
                        "RuntimeError": "RE",
                        "Score": "SC"
                    }
                    raw_verdict = data.get("verdict", "RE")
                    verdict = verdict_map.get(raw_verdict, "RE")
                    
                    tc_result = TestCaseResult(
                        test_index=len(test_results) + 1,
                        test_name=test_name,
                        passed=passed,
                        actual_output="",
                        time_ms=(data.get("time") or data.get("cpu_time") or 0.0) * 1000.0,
                        memory_kb=data.get("memory", 0) // 1024,
                        exit_code=0 if passed else -1,
                        error="",
                        verdict=verdict
                    )
                    test_results.append(tc_result)
                    
                    if progress_callback:
                        try:
                            await progress_callback(tc_result)
                        except Exception as e:
                            logger.warning(f"Progress callback failed: {e}")
                            
                # Collect subtask-level scores as fallback when no testcase scores appear
                elif "IOISubtaskScore" in msg:
                    data = msg["IOISubtaskScore"]
                    subtask_id = data.get("subtask", len(subtask_results))
                    normalized = data.get("normalized_score", 0.0)
                    passed = normalized >= 1.0
                    tc_result = TestCaseResult(
                        test_index=len(subtask_results) + 1,
                        test_name=f"subtask_{subtask_id}",
                        passed=passed,
                        actual_output="",
                        time_ms=0.0,
                        memory_kb=0,
                        exit_code=0 if passed else -1,
                        error="",
                        verdict="AC" if passed else "WA"
                    )
                    subtask_results.append(tc_result)

                # Check for evaluation summary
                elif "IOIEvaluation" in msg:
                    final_summary = msg["IOIEvaluation"]

                # Capture overall task score
                elif "IOITaskScore" in msg:
                    task_score = msg["IOITaskScore"].get("score", None)
                    
            except json.JSONDecodeError:
                continue

        # If no per-testcase results but we have subtask results, use those
        if not test_results and subtask_results:
            logger.info(
                f"No IOITestcaseScore messages found; using {len(subtask_results)} "
                f"IOISubtaskScore entries as synthetic test results"
            )
            test_results = subtask_results
            # Inject task score into final_summary so it propagates correctly
            if task_score is not None and final_summary is None:
                final_summary = {"score": task_score}
            elif task_score is not None and final_summary is not None:
                final_summary.setdefault("score", task_score)

            if progress_callback:
                for tc_result in test_results:
                    try:
                        await progress_callback(tc_result)
                    except Exception as e:
                        logger.warning(f"Progress callback failed: {e}")

        # Strict requirement: if we have no test case results, something critically failed
        if not test_results:
             if "Compilation error" in result.stdout or "Compilation error" in result.stderr:
                 raise BenchmarkError(f"Compilation failed under Task Maker:\n{result.stdout}\n{result.stderr}")
             raise BenchmarkError(f"Task Maker produced no test results. Output: {result.stdout[:50000]} Stderr: {result.stderr[:50000]}")
        print((f"Output: {result.stdout[:50000]} Stderr: {result.stderr[:50000]}"))
        # Aggregate summary
        tests_total = len(test_results)
        tests_passed = sum(1 for r in test_results if r.passed)
        
        passed_times = [r.time_ms for r in test_results if r.passed and r.time_ms > 0]
        avg_time_ms = sum(passed_times) / len(passed_times) if passed_times else 0.0
        max_time_ms = max(passed_times) if passed_times else 0.0
        
        memory_values = [r.memory_kb for r in test_results if r.memory_kb > 0]
        max_memory_kb = max(memory_values) if memory_values else 0
        
        # Get score from final_summary if available
        score = 0.0
        if final_summary:
            score = final_summary.get("score", 0.0)
        else:
            score = (tests_passed / tests_total * 100.0) if tests_total > 0 else 0.0

        summary = BenchmarkSummary(
            solution_id=solution_id,
            tests_passed=tests_passed,
            tests_total=tests_total,
            avg_time_ms=avg_time_ms,
            max_time_ms=max_time_ms,
            max_memory_kb=max_memory_kb,
            all_passed=tests_passed == tests_total,
            score=score,
            test_results=test_results
        )
        
        await _store_results(summary, test_results, db_session)
        
        return summary


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
    db_session: Optional[AsyncSession] = None,
    additional_files: Optional[Dict[str, str]] = None,
    problem_dir: Optional[str] = None,
    progress_callback: Optional[Any] = None
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
        additional_files: Optional dictionary of {filename: content} to include in compilation
        problem_dir: Optional problem directory containing task.yaml
        progress_callback: Optional async callback for intermediate results
        
    Returns:
        Tuple of (success, message, benchmark_summary or None)
    """
    # 1. Check if we should use Task Maker (tmc)
    if problem_dir and os.path.exists(os.path.join(problem_dir, "task.yaml")):
        try:
            summary = await _run_task_maker(
                solution_id=solution_id,
                problem_id=problem_id,
                source_code=source_code,
                problem_dir=problem_dir,
                memory_limit_mb=memory_limit_mb,
                db_session=db_session,
                progress_callback=progress_callback
            )
            return True, "Task Maker evaluation successful", summary
        except Exception as e:
            logger.warning(f"Task Maker evaluation failed, falling back: {e}")
            # If Task Maker fails, we continue with custom behavior
            # BUT if it was a compilation error from Task Maker, we might want to return that
            if isinstance(e, BenchmarkError) and "Compilation failed" in str(e):
                await _update_solution_compile_status(solution_id, False, str(e), db_session)
                return False, str(e), None

    container_manager = get_container_manager()
    
    # Create temporary directory for compilation
    with tempfile.TemporaryDirectory() as temp_dir:
        binary_path = os.path.join(temp_dir, "solution")
        source_path = os.path.join(temp_dir, "solution.cpp")
        
        # Write source code
        with open(source_path, "w") as f:
            f.write(source_code)
        
        # Write additional files
        if additional_files:
            for filename, content in additional_files.items():
                file_path = os.path.join(temp_dir, filename)
                with open(file_path, "w") as f:
                    f.write(content)
        
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
        # Include all .cpp files in the sandbox directory
        cpp_files = ["/sandbox/solution.cpp"]
        if additional_files:
            for filename in additional_files:
                if filename.endswith(".cpp"):
                    cpp_files.append(f"/sandbox/{filename}")
        
        cmd_parts = [validated_compiler] + cpp_files + ["-o", "/sandbox/solution"]
        cmd_parts.extend(validated_flags)
        
        logger.info(f"Compiling solution {solution_id} with command: {' '.join(cmd_parts)}")
        
        # Run compilation in container
        # Execute directly instead of via sh -c to prevent shell injection
        # Use /sandbox to avoid conflict with default tmpfs at /workspace
        compile_result = await container_manager.execute_command(
            command=cmd_parts,
            volumes={
                source_path: {"bind": "/sandbox/solution.cpp", "mode": "ro"},
                temp_dir: {"bind": "/sandbox", "mode": "rw"}
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
                db_session=db_session,
                progress_callback=progress_callback
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
    if db_session is None:
        logger.debug("Skipping solution status update (no db_session provided)")
        return
    
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