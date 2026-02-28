import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

from backend.sandbox.benchmark import _find_test_cases
from backend.sandbox.compiler import (
    perform_safety_check,
    validate_compiler,
    validate_flags,
    DEFAULT_COMPILER,
)
from backend.api.verify_example import _find_example_solution, _parse_github_url


# =============================================================================
# _find_test_cases
# =============================================================================

def test_find_test_cases_starts_at_zero(tmp_path):
    """Finds .in/.out pairs starting from index 0."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "0.in").write_text("in0")
    (d / "0.out").write_text("out0")
    (d / "1.in").write_text("in1")
    (d / "1.out").write_text("out1")

    cases = _find_test_cases(d)
    assert len(cases) == 2
    assert cases[0][0].name == "0.in"
    assert cases[0][1].name == "0.out"
    assert cases[1][0].name == "1.in"
    assert cases[1][1].name == "1.out"


def test_find_test_cases_stops_at_gap(tmp_path):
    """Stops at the first missing index in a sequence (gap at index 1)."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "0.in").write_text("in0")
    (d / "0.out").write_text("out0")
    (d / "2.in").write_text("in2")
    (d / "2.out").write_text("out2")

    cases = _find_test_cases(d)
    assert len(cases) == 1
    assert cases[0][0].name == "0.in"


def test_find_test_cases_input_output_pattern(tmp_path):
    """Finds input_N.txt / output_N.txt pattern."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "input_0.txt").write_text("in0")
    (d / "output_0.txt").write_text("out0")

    cases = _find_test_cases(d)
    assert len(cases) == 1
    assert cases[0][0].name == "input_0.txt"
    assert cases[0][1].name == "output_0.txt"


def test_find_test_cases_empty_dir(tmp_path):
    """Returns empty list when no test files exist."""
    d = tmp_path / "tests"
    d.mkdir()
    assert _find_test_cases(d) == []


def test_find_test_cases_missing_pair(tmp_path):
    """Ignores .in file with no matching .out."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "0.in").write_text("in0")
    # No 0.out

    cases = _find_test_cases(d)
    assert len(cases) == 0


def test_find_test_cases_nonexistent_dir(tmp_path):
    """Returns empty list for a directory that doesn't exist (BenchmarkError raised by caller)."""
    # _find_test_cases itself returns [] — benchmark_solution raises BenchmarkError on empty result.
    result = _find_test_cases(tmp_path / "nonexistent")
    assert result == []


# =============================================================================
# _find_example_solution
# =============================================================================

def test_find_example_solution_in_solution_subdir(tmp_path):
    """Finds .cpp in 'solution/' subdirectory."""
    sol_dir = tmp_path / "solution"
    sol_dir.mkdir()
    cpp = sol_dir / "solution.cpp"
    cpp.write_text("int main(){}")

    result = _find_example_solution(tmp_path)
    assert result == cpp


def test_find_example_solution_prefers_named_keywords(tmp_path):
    """Prefers files named sol*/correct*/ac* over generic names."""
    sol_dir = tmp_path / "solution"
    sol_dir.mkdir()
    (sol_dir / "brute.cpp").write_text("// brute")
    preferred = sol_dir / "sol_ac.cpp"
    preferred.write_text("// ac")

    result = _find_example_solution(tmp_path)
    assert result == preferred


def test_find_example_solution_falls_back_to_root(tmp_path):
    """Falls back to root directory if no standard subdir exists."""
    cpp = tmp_path / "main.cpp"
    cpp.write_text("int main(){}")

    result = _find_example_solution(tmp_path)
    assert result == cpp


def test_find_example_solution_searches_sol_dir(tmp_path):
    """Finds .cpp in 'sol/' when 'solution/' is absent."""
    sol_dir = tmp_path / "sol"
    sol_dir.mkdir()
    cpp = sol_dir / "fast.cpp"
    cpp.write_text("int main(){}")

    result = _find_example_solution(tmp_path)
    assert result == cpp


def test_find_example_solution_returns_none_when_no_cpp(tmp_path):
    """Returns None when no .cpp file exists anywhere."""
    (tmp_path / "solution").mkdir()
    (tmp_path / "solution" / "readme.txt").write_text("no cpp here")

    result = _find_example_solution(tmp_path)
    assert result is None


def test_find_example_solution_prefers_subdir_over_root(tmp_path):
    """solution/ subdirectory is searched before the root."""
    root_cpp = tmp_path / "other.cpp"
    root_cpp.write_text("// root")
    sol_dir = tmp_path / "solution"
    sol_dir.mkdir()
    sol_cpp = sol_dir / "solution.cpp"
    sol_cpp.write_text("// subdir")

    result = _find_example_solution(tmp_path)
    assert result == sol_cpp


# =============================================================================
# _parse_github_url
# =============================================================================

def test_parse_github_url_tree(dummy=None):
    """Parses standard tree URL."""
    result = _parse_github_url(
        "https://github.com/owner/repo/tree/main/path/to/problem"
    )
    assert result["owner"] == "owner"
    assert result["repo"] == "repo"
    assert result["ref"] == "main"
    assert result["path"] == "path/to/problem"


def test_parse_github_url_repo_only():
    """Parses bare repo URL, defaults ref to main and path to empty."""
    result = _parse_github_url("https://github.com/owner/repo")
    assert result["owner"] == "owner"
    assert result["repo"] == "repo"
    assert result["ref"] == "main"
    assert result["path"] == ""


def test_parse_github_url_blob():
    """Parses blob URL (file link)."""
    result = _parse_github_url(
        "https://github.com/owner/repo/blob/main/path/file.cpp"
    )
    assert result["path"] == "path/file.cpp"


def test_parse_github_url_invalid():
    """Raises HTTPException for unrecognisable URLs."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url("https://gitlab.com/owner/repo")
    assert exc_info.value.status_code == 400


# =============================================================================
# Compiler safety checks
# =============================================================================

def test_safety_check_clean_code():
    """Clean competitive programming code passes all checks."""
    code = """
#include <iostream>
#include <vector>
#include <algorithm>
int main() {
    int n; std::cin >> n;
    std::cout << n << std::endl;
}
"""
    assert perform_safety_check(code) == []


def test_safety_check_detects_system_call():
    """system() call is flagged."""
    code = '#include<cstdlib>\nint main(){ system("rm -rf /"); }'
    violations = perform_safety_check(code)
    assert any("system()" in v for v in violations)


def test_safety_check_detects_fstream():
    """<fstream> include is flagged."""
    code = "#include <fstream>\nint main(){}"
    violations = perform_safety_check(code)
    assert any("fstream" in v for v in violations)


def test_safety_check_detects_inline_asm():
    """Inline assembly is flagged."""
    code = 'int main(){ __asm__("nop"); }'
    violations = perform_safety_check(code)
    assert any("assembly" in v.lower() for v in violations)


def test_safety_check_detects_socket():
    """socket() call is flagged."""
    code = "#include<sys/socket.h>\nint main(){ int s = socket(AF_INET,SOCK_STREAM,0); }"
    violations = perform_safety_check(code)
    assert any("socket()" in v for v in violations)


# =============================================================================
# Compiler / flag validation
# =============================================================================

def test_validate_compiler_whitelisted():
    """Whitelisted compiler passes through unchanged."""
    assert validate_compiler("g++-14") == "g++-14"
    assert validate_compiler("clang++-17") == "clang++-17"


def test_validate_compiler_unknown_falls_back():
    """Unknown compiler falls back to DEFAULT_COMPILER."""
    result = validate_compiler("evil-compiler")
    assert result == DEFAULT_COMPILER


def test_validate_flags_removes_unknown():
    """Unknown flags are stripped; known flags pass through."""
    flags = ["-O2", "-std=c++17", "--inject-evil", "-Wall"]
    result = validate_flags(flags)
    assert "-O2" in result
    assert "-std=c++17" in result
    assert "-Wall" in result
    assert "--inject-evil" not in result


def test_validate_flags_allows_safe_defines():
    """Simple -D macro definitions are permitted."""
    flags = ["-DNDEBUG", "-DLOCAL=1", "-DMAX_N=100000"]
    result = validate_flags(flags)
    assert flags == result


def test_validate_flags_blocks_shell_injection_in_define():
    """-D with shell metacharacters is rejected."""
    flags = ["-DFOO=bar;rm -rf /"]
    result = validate_flags(flags)
    assert result == []


def test_validate_flags_empty():
    """Empty flag list returns empty list."""
    assert validate_flags([]) == []


# =============================================================================
# ContainerManager.ensure_image_available
# =============================================================================

@pytest.mark.asyncio
async def test_ensure_image_available_image_already_exists():
    """Returns True immediately when image is already available."""
    from backend.sandbox.container import ContainerManager
    
    with patch.object(ContainerManager, 'is_image_available', return_value=True):
        manager = ContainerManager(image="arena-sandbox:latest")
        result = await manager.ensure_image_available()
        assert result is True


@pytest.mark.asyncio
async def test_ensure_image_available_builds_missing_image(tmp_path):
    """Builds image when not available locally."""
    from backend.sandbox.container import ContainerManager
    
    # Create a mock Dockerfile in expected location
    docker_dir = tmp_path / "docker"
    docker_dir.mkdir()
    (docker_dir / "Dockerfile.sandbox").write_text("FROM alpine:latest\n")
    
    with patch.object(ContainerManager, 'is_image_available', return_value=False):
        with patch.object(ContainerManager, 'build_image', return_value=True) as mock_build:
            with patch('backend.sandbox.container.Path') as mock_path_cls:
                # Mock the path resolution for Dockerfile location
                mock_path = tmp_path / "docker"
                mock_dockerfile = mock_path / "Dockerfile.sandbox"
                mock_path_cls.return_value.parent.parent.parent = tmp_path
                
                manager = ContainerManager(image="arena-sandbox:latest")
                
                # Patch Path(__file__) to return our temp path
                with patch('pathlib.Path') as mock_pathlib:
                    instance = mock_pathlib.return_value
                    instance.parent.parent.parent = tmp_path
                    instance.__truediv__ = lambda self, other: tmp_path / str(other)
                    (tmp_path / "docker" / "Dockerfile.sandbox").write_text("FROM alpine\n")
                    
                    # Since we need the actual path logic to work, let's use a different approach
                    # Mock the specific path check to return True
                    with patch('pathlib.Path.exists', return_value=True):
                        result = await manager.ensure_image_available()
                        # Should return True since build_image is mocked to succeed
                        assert result is True
                        mock_build.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_image_available_build_fails():
    """Returns False when image build fails."""
    from backend.sandbox.container import ContainerManager
    
    with patch.object(ContainerManager, 'is_image_available', return_value=False):
        with patch.object(ContainerManager, 'build_image', return_value=False):
            with patch('pathlib.Path.exists', return_value=True):
                manager = ContainerManager(image="arena-sandbox:latest")
                result = await manager.ensure_image_available()
                assert result is False


@pytest.mark.asyncio
async def test_ensure_image_available_dockerfile_not_found():
    """Returns False when Dockerfile is not found."""
    from backend.sandbox.container import ContainerManager
    
    with patch.object(ContainerManager, 'is_image_available', return_value=False):
        with patch('pathlib.Path.exists', return_value=False):
            manager = ContainerManager(image="arena-sandbox:latest")
            result = await manager.ensure_image_available()
            assert result is False


# =============================================================================
# _run_task_maker (tmc JSON parsing)
# =============================================================================

@pytest.mark.asyncio
async def test_run_task_maker_parses_json_output():
    """Parses tmc --ui json output and creates BenchmarkSummary."""
    from backend.sandbox.benchmark import _run_task_maker, BenchmarkSummary
    from backend.sandbox.container import ContainerManager, ContainerResult
    
    # Sample tmc JSON output with IOITestcaseScore and IOIEvaluation
    tmc_output = '''
    {"IOITestcaseScore": {"test_name": "test01.in", "score": 1.0, "verdict": "Correct", "time": 0.05, "memory": 1048576}}
    {"IOITestcaseScore": {"test_name": "test02.in", "score": 1.0, "verdict": "Correct", "time": 0.07, "memory": 2097152}}
    {"IOITestcaseScore": {"test_name": "test03.in", "score": 0.0, "verdict": "WrongAnswer", "time": 0.03, "memory": 1048576}}
    {"IOIEvaluation": {"score": 66.67, "verdict": "Partial"}}
    '''
    
    mock_result = ContainerResult(
        success=True,
        stdout=tmc_output,
        stderr="",
        exit_code=0,
        execution_time_ms=1500.0
    )
    
    with patch('backend.sandbox.benchmark.get_container_manager') as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.execute_command = AsyncMock(return_value=mock_result)
        mock_get_manager.return_value = mock_manager
        
        with patch('backend.sandbox.benchmark._store_results', new_callable=AsyncMock):
            with tempfile.TemporaryDirectory() as problem_dir:
                summary = await _run_task_maker(
                    solution_id=42,
                    source_code="int main() { return 0; }",
                    problem_dir=problem_dir,
                    memory_limit_mb=256
                )
                
                assert isinstance(summary, BenchmarkSummary)
                assert summary.solution_id == 42
                assert summary.tests_total == 3
                assert summary.tests_passed == 2
                assert summary.score == 66.67
                assert summary.all_passed is False
                
                # Check test results
                assert len(summary.test_results) == 3
                assert summary.test_results[0].test_name == "test01.in"
                assert summary.test_results[0].passed is True
                assert summary.test_results[0].verdict == "AC"
                assert summary.test_results[0].time_ms == 50.0  # 0.05 * 1000
                assert summary.test_results[0].memory_kb == 1024  # 1048576 / 1024
                
                assert summary.test_results[2].test_name == "test03.in"
                assert summary.test_results[2].passed is False
                assert summary.test_results[2].verdict == "WA"


@pytest.mark.asyncio
async def test_run_task_maker_handles_all_verdicts():
    """Correctly maps all tmc verdicts to internal verdict codes."""
    from backend.sandbox.benchmark import _run_task_maker, BenchmarkSummary
    from backend.sandbox.container import ContainerManager, ContainerResult
    
    # Test all verdict mappings
    tmc_output = '''
    {"IOITestcaseScore": {"test_name": "test1", "score": 1.0, "verdict": "Correct", "time": 0.1, "memory": 1048576}}
    {"IOITestcaseScore": {"test_name": "test2", "score": 0.0, "verdict": "WrongAnswer", "time": 0.1, "memory": 1048576}}
    {"IOITestcaseScore": {"test_name": "test3", "score": 0.0, "verdict": "TimeLimit", "time": 1.0, "memory": 1048576}}
    {"IOITestcaseScore": {"test_name": "test4", "score": 0.0, "verdict": "MemoryLimit", "time": 0.1, "memory": 268435456}}
    {"IOITestcaseScore": {"test_name": "test5", "score": 0.0, "verdict": "RuntimeError", "time": 0.01, "memory": 1048576}}
    {"IOIEvaluation": {"score": 20.0, "verdict": "Partial"}}
    '''
    
    mock_result = ContainerResult(
        success=True,
        stdout=tmc_output,
        stderr="",
        exit_code=0,
        execution_time_ms=2000.0
    )
    
    with patch('backend.sandbox.benchmark.get_container_manager') as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.execute_command = AsyncMock(return_value=mock_result)
        mock_get_manager.return_value = mock_manager
        
        with patch('backend.sandbox.benchmark._store_results', new_callable=AsyncMock):
            with tempfile.TemporaryDirectory() as problem_dir:
                summary = await _run_task_maker(
                    solution_id=1,
                    source_code="int main() {}",
                    problem_dir=problem_dir
                )
                
                verdicts = [r.verdict for r in summary.test_results]
                assert "AC" in verdicts
                assert "WA" in verdicts
                assert "TLE" in verdicts
                assert "MLE" in verdicts
                assert "RE" in verdicts


@pytest.mark.asyncio
async def test_run_task_maker_no_evaluation_summary():
    """Calculates score proportionally when no IOIEvaluation present."""
    from backend.sandbox.benchmark import _run_task_maker, BenchmarkSummary
    from backend.sandbox.container import ContainerManager, ContainerResult
    
    # Output without IOIEvaluation - should use proportional scoring
    tmc_output = '''
    {"IOITestcaseScore": {"test_name": "test1", "score": 1.0, "verdict": "Correct", "time": 0.1, "memory": 1048576}}
    {"IOITestcaseScore": {"test_name": "test2", "score": 1.0, "verdict": "Correct", "time": 0.1, "memory": 1048576}}
    {"IOITestcaseScore": {"test_name": "test3", "score": 0.0, "verdict": "WrongAnswer", "time": 0.1, "memory": 1048576}}
    {"IOITestcaseScore": {"test_name": "test4", "score": 0.0, "verdict": "WrongAnswer", "time": 0.1, "memory": 1048576}}
    '''
    
    mock_result = ContainerResult(
        success=True,
        stdout=tmc_output,
        stderr="",
        exit_code=0,
        execution_time_ms=1000.0
    )
    
    with patch('backend.sandbox.benchmark.get_container_manager') as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.execute_command = AsyncMock(return_value=mock_result)
        mock_get_manager.return_value = mock_manager
        
        with patch('backend.sandbox.benchmark._store_results', new_callable=AsyncMock):
            with tempfile.TemporaryDirectory() as problem_dir:
                summary = await _run_task_maker(
                    solution_id=1,
                    source_code="int main() {}",
                    problem_dir=problem_dir
                )
                
                # 2/4 passed = 50% score
                assert summary.tests_passed == 2
                assert summary.tests_total == 4
                assert summary.score == 50.0


@pytest.mark.asyncio
async def test_run_task_maker_compilation_error():
    """Raises BenchmarkError when compilation error detected in stdout."""
    from backend.sandbox.benchmark import _run_task_maker, BenchmarkError
    from backend.sandbox.container import ContainerManager, ContainerResult
    
    # Compilation error appears in stdout (the code checks stdout for this)
    mock_result = ContainerResult(
        success=True,  # Container ran but compilation failed
        stdout='{"some_other_event": {}}\nCompilation error: undefined reference to main',
        stderr="",
        exit_code=0,
        execution_time_ms=100.0
    )
    
    with patch('backend.sandbox.benchmark.get_container_manager') as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.execute_command = AsyncMock(return_value=mock_result)
        mock_get_manager.return_value = mock_manager
        
        with tempfile.TemporaryDirectory() as problem_dir:
            with pytest.raises(BenchmarkError) as exc_info:
                await _run_task_maker(
                    solution_id=1,
                    source_code="invalid code",
                    problem_dir=problem_dir
                )
            assert "compilation" in str(exc_info.value).lower() or "no test results" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_run_task_maker_no_test_results():
    """Raises BenchmarkError when no test results produced."""
    from backend.sandbox.benchmark import _run_task_maker, BenchmarkError
    from backend.sandbox.container import ContainerManager, ContainerResult
    
    mock_result = ContainerResult(
        success=True,
        stdout="{\"some_other_event\": {}}\n",
        stderr="",
        exit_code=0,
        execution_time_ms=100.0
    )
    
    with patch('backend.sandbox.benchmark.get_container_manager') as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.execute_command = AsyncMock(return_value=mock_result)
        mock_get_manager.return_value = mock_manager
        
        with tempfile.TemporaryDirectory() as problem_dir:
            with pytest.raises(BenchmarkError) as exc_info:
                await _run_task_maker(
                    solution_id=1,
                    source_code="int main() {}",
                    problem_dir=problem_dir
                )
            assert "no test results" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_run_task_maker_uses_time_field():
    """Uses 'time' field from tmc output (prefers it over cpu_time when both present)."""
    from backend.sandbox.benchmark import _run_task_maker, BenchmarkSummary
    from backend.sandbox.container import ContainerManager, ContainerResult
    
    # tmc output with both time and cpu_time - code prefers 'time'
    tmc_output = '''
    {"IOITestcaseScore": {"test_name": "test1", "score": 1.0, "verdict": "Correct", "cpu_time": 0.123, "time": 0.456, "memory": 1048576}}
    {"IOIEvaluation": {"score": 100.0, "verdict": "Accepted"}}
    '''
    
    mock_result = ContainerResult(
        success=True,
        stdout=tmc_output,
        stderr="",
        exit_code=0,
        execution_time_ms=500.0
    )
    
    with patch('backend.sandbox.benchmark.get_container_manager') as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.execute_command = AsyncMock(return_value=mock_result)
        mock_get_manager.return_value = mock_manager
        
        with patch('backend.sandbox.benchmark._store_results', new_callable=AsyncMock):
            with tempfile.TemporaryDirectory() as problem_dir:
                summary = await _run_task_maker(
                    solution_id=1,
                    source_code="int main() {}",
                    problem_dir=problem_dir
                )
                
                # Code prefers 'time' (0.456) over 'cpu_time' (0.123)
                assert summary.test_results[0].time_ms == 456.0  # 0.456 * 1000


@pytest.mark.asyncio
async def test_run_task_maker_all_passed():
    """Correctly sets all_passed when all tests pass."""
    from backend.sandbox.benchmark import _run_task_maker, BenchmarkSummary
    from backend.sandbox.container import ContainerManager, ContainerResult
    
    tmc_output = '''
    {"IOITestcaseScore": {"test_name": "test1", "score": 1.0, "verdict": "Correct", "time": 0.1, "memory": 1048576}}
    {"IOITestcaseScore": {"test_name": "test2", "score": 1.0, "verdict": "Correct", "time": 0.15, "memory": 2097152}}
    {"IOIEvaluation": {"score": 100.0, "verdict": "Accepted"}}
    '''
    
    mock_result = ContainerResult(
        success=True,
        stdout=tmc_output,
        stderr="",
        exit_code=0,
        execution_time_ms=1000.0
    )
    
    with patch('backend.sandbox.benchmark.get_container_manager') as mock_get_manager:
        mock_manager = MagicMock()
        mock_manager.execute_command = AsyncMock(return_value=mock_result)
        mock_get_manager.return_value = mock_manager
        
        with patch('backend.sandbox.benchmark._store_results', new_callable=AsyncMock):
            with tempfile.TemporaryDirectory() as problem_dir:
                summary = await _run_task_maker(
                    solution_id=1,
                    source_code="int main() {}",
                    problem_dir=problem_dir
                )
                
                assert summary.all_passed is True
                assert summary.tests_passed == 2
                assert summary.tests_total == 2
                # Average time: (100 + 150) / 2 = 125ms
                assert summary.avg_time_ms == 125.0
                # Max time: 150ms
                assert summary.max_time_ms == 150.0
                # Max memory: 2097152 bytes = 2048 KB
                assert summary.max_memory_kb == 2048