import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import asyncio
import json
from datetime import datetime
from fastapi import HTTPException

from backend.api.verify_example import (
    _find_example_solution,
    _get_solution_expected_outcome,
    _find_all_solutions,
    _parse_github_url,
    list_available_solutions,
    verify_example_solution,
    verify_example_solution_stream,
    SolutionInfo,
    VerifyRequest
)
from backend.utils.task_yaml import TaskConfig
from backend.database.models import Problem
from backend import config

# =============================================================================
# Helper for TaskConfig
# =============================================================================

def create_mock_task_config(problem_dir, data=None):
    if data is None:
        data = {}
    return TaskConfig(data, problem_dir)

# =============================================================================
# Tests for _find_example_solution
# =============================================================================

def test_find_example_solution_with_task_config(tmp_path):
    """Should return solution from task_config if available."""
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    sol_file = problem_dir / "sol.cpp"
    sol_file.write_text("int main() {}")
    
    task_config = create_mock_task_config(problem_dir, {
        "solutions": [
            {"path": "sol.cpp", "name": "correct"}
        ]
    })
    
    assert _find_example_solution(problem_dir, task_config) == sol_file

def test_find_example_solution_fallback_to_all_solutions(tmp_path):
    """Should fallback to _find_all_solutions if task_config doesn't have a correct one."""
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    (problem_dir / "random.cpp").write_text("int main() {}")
    correct_sol = problem_dir / "correct_sol.cpp"
    correct_sol.write_text("int main() {}")
    
    # No task_config or empty one
    assert _find_example_solution(problem_dir, None) == correct_sol

# =============================================================================
# Tests for _get_solution_expected_outcome
# =============================================================================

def test_get_solution_expected_outcome_missing_config():
    assert _get_solution_expected_outcome(None, Path("sol.cpp")) == (None, None)

def test_get_solution_expected_outcome_not_in_list(tmp_path):
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    task_config = create_mock_task_config(problem_dir, {"solutions": "not a list"})
    assert _get_solution_expected_outcome(task_config, problem_dir / "sol.cpp") == (None, None)

def test_get_solution_expected_outcome_found(tmp_path):
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    sol_path = problem_dir / "sol.cpp"
    sol_path.write_text("int main() {}")
    
    task_config = create_mock_task_config(problem_dir, {
        "solutions": [
            {"path": "sol.cpp", "score": 100.0, "verdict": "correct"}
        ]
    })
    assert _get_solution_expected_outcome(task_config, sol_path) == (100.0, "correct")

# =============================================================================
# Tests for _find_all_solutions
# =============================================================================

def test_find_all_solutions_with_task_yaml_exists(tmp_path):
    """If task.yaml exists, it should use get_test_submissions."""
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    (problem_dir / "task.yaml").write_text("test_submissions:\n  - sol1.cpp\n  - # sol2.cpp")
    (problem_dir / "sol1.cpp").write_text("int main() {}")
    
    task_config = create_mock_task_config(problem_dir, {
        "solutions": [
            {"path": "sol1.cpp", "score": 100.0, "verdict": "correct"}
        ]
    })
    
    sols = _find_all_solutions(problem_dir, task_config)
    assert len(sols) == 1
    assert sols[0].path == "sol1.cpp"
    assert sols[0].expected_score == 100.0
    assert sols[0].expected_verdict == "correct"

def test_find_all_solutions_fallback_scan(tmp_path):
    """If no task.yaml, it should scan directories."""
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    
    # Create files in various standard locations
    (problem_dir / "root.cpp").write_text("x")
    
    sol_dir = problem_dir / "correct"
    sol_dir.mkdir()
    (sol_dir / "correct.cpp").write_text("x")
    
    tle_dir = problem_dir / "tle"
    tle_dir.mkdir()
    (tle_dir / "too_slow.cpp").write_text("x")
    
    wa_dir = problem_dir / "wa"
    wa_dir.mkdir()
    (wa_dir / "wrong.cpp").write_text("x")

    # Pass None for task_config to trigger fallback
    sols = _find_all_solutions(problem_dir, None)
    
    paths = {s.path for s in sols}
    assert "root.cpp" in paths
    assert str(Path("correct/correct.cpp")) in paths
    assert str(Path("tle/too_slow.cpp")) in paths
    assert str(Path("wa/wrong.cpp")) in paths
    
    verdicts = {s.path: s.expected_verdict for s in sols}
    assert verdicts[str(Path("correct/correct.cpp"))] == "correct"
    assert verdicts[str(Path("tle/too_slow.cpp"))] == "time_limit"
    assert verdicts[str(Path("wa/wrong.cpp"))] == "wrong_answer"

# =============================================================================
# API Endpoint Tests
# =============================================================================

@pytest.mark.asyncio
async def test_list_available_solutions_endpoint(client, db_session, tmp_path):
    # Setup mock problem repo
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "problem1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="Test Prob",
            slug="test-prob",
            description_md="x",
            scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/problem1",
        )
        db_session.add(problem)
        await db_session.commit()
        await db_session.refresh(problem)
        
        # Direct call for coverage
        await list_available_solutions(problem_id=problem.id, db=db_session)
        
        response = await client.get(f"/api/v1/problems/{problem.id}/solutions")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["path"] == "sol.cpp"

@pytest.mark.asyncio
async def test_list_available_solutions_no_repo(client, db_session, tmp_path):
    non_existent_repo = tmp_path / "non_existent"
    with patch("backend.config.PROBLEMS_REPO_DIR", non_existent_repo):
        problem = Problem(
            name="Test Prob",
            slug="test-prob-no-repo",
            description_md="x",
            scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/problem1",
        )
        db_session.add(problem)
        await db_session.commit()
        
        # Direct call for coverage
        with pytest.raises(HTTPException):
            await list_available_solutions(problem_id=problem.id, db=db_session)

        response = await client.get(f"/api/v1/problems/{problem.id}/solutions")
        assert response.status_code == 400
        assert "not configured" in response.json()["detail"]

@pytest.mark.asyncio
async def test_verify_example_solution_with_custom_path(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "problem1"
    problem_path.mkdir()
    sol_path = problem_path / "custom_sol.cpp"
    sol_path.write_text("int main() {}")
    
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tests_dir), \
         patch("backend.sandbox.benchmark.compile_and_benchmark") as mock_cab:
        
        from backend.sandbox.benchmark import BenchmarkSummary
        mock_cab.return_value = (True, "OK", BenchmarkSummary(
            solution_id=-1, tests_passed=1, tests_total=1, avg_time_ms=10.0, 
            max_time_ms=10.0, max_memory_kb=1024, all_passed=True, test_results=[]
        ))
        
        problem = Problem(
            name="Test Prob",
            slug="test-prob-custom",
            description_md="x",
            scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/problem1",
            tests_downloaded=True,
            test_count=1
        )
        db_session.add(problem)
        await db_session.commit()
        
        # Direct call for coverage
        await verify_example_solution(problem_id=problem.id, request=VerifyRequest(solution_path="custom_sol.cpp"), db=db_session)

        # 1. Test existing custom path
        response = await client.post(
            f"/api/v1/problems/{problem.id}/verify-example",
            json={"solution_path": "custom_sol.cpp"}
        )
        assert response.status_code == 200
        assert response.json()["solution_file"] == "custom_sol.cpp"
        
        # 2. Test non-existent custom path
        response = await client.post(
            f"/api/v1/problems/{problem.id}/verify-example",
            json={"solution_path": "ghost.cpp"}
        )
        assert response.status_code == 404
        assert "Solution file not found" in response.json()["detail"]

@pytest.mark.asyncio
async def test_verify_example_solution_with_graders(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "problem1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    
    # Create grader dir
    grader_dir = problem_path / "grader"
    grader_dir.mkdir()
    (grader_dir / "grader.cpp").write_text("grader code")
    (grader_dir / "helper.h").write_text("header code")
    (grader_dir / "random.txt").write_text("ignore me")
    
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tests_dir), \
         patch("backend.sandbox.benchmark.compile_and_benchmark") as mock_cab:
        
        mock_cab.return_value = (True, "OK", None) # Just checking what's passed to it
        
        problem = Problem(
            name="Test Prob",
            slug="test-prob-graders",
            description_md="x",
            scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/problem1",
            tests_downloaded=True,
        )
        db_session.add(problem)
        await db_session.commit()
        
        await client.post(f"/api/v1/problems/{problem.id}/verify-example")
        
        # Check that additional_files included the correct grader files
        args, kwargs = mock_cab.call_args
        additional_files = kwargs["additional_files"]
        assert "grader.cpp" in additional_files
        assert "helper.h" in additional_files
        assert "random.txt" not in additional_files
        assert additional_files["grader.cpp"] == "grader code"

@pytest.mark.asyncio
async def test_verify_example_solution_exception(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "problem1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", side_effect=Exception("BOOM")):
        
        problem = Problem(
            name="Test Prob",
            slug="test-prob-boom",
            description_md="x",
            scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/problem1",
            tests_downloaded=True,
        )
        db_session.add(problem)
        await db_session.commit()
        
        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
        assert response.status_code == 500
        assert "Benchmark error: BOOM" in response.json()["detail"]

@pytest.mark.asyncio
async def test_verify_example_solution_stream_endpoint(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "problem1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    
    from backend.sandbox.benchmark import BenchmarkSummary, TestCaseResult
    mock_summary = BenchmarkSummary(
        solution_id=-1, tests_passed=1, tests_total=1, avg_time_ms=10.0, 
        max_time_ms=10.0, max_memory_kb=1024, all_passed=True, 
        test_results=[TestCaseResult(test_index=1, test_name="1.in", passed=True, actual_output="1", time_ms=10.0, memory_kb=1024, exit_code=0, error="", verdict="AC")]
    )

    # We need to mock compile_and_benchmark to handle progress_callback
    async def mock_cab_stream(*args, **kwargs):
        progress_callback = kwargs.get("progress_callback")
        if progress_callback:
            await progress_callback(mock_summary.test_results[0])
        return True, "OK", mock_summary

    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tests_dir), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", side_effect=mock_cab_stream):
        
        problem = Problem(
            name="Test Prob",
            slug="test-prob-stream",
            description_md="x",
            scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/problem1",
            tests_downloaded=True,
            test_count=1
        )
        db_session.add(problem)
        await db_session.commit()
        
        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example-stream")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        
        # Read the stream
        events = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        
        assert len(events) >= 2
        assert events[0]["type"] == "test_result"
        assert events[-1]["type"] == "final"
        assert events[-1]["result"]["compile_success"] is True

@pytest.mark.asyncio
async def test_verify_example_solution_stream_compile_fail(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "problem1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", return_value=(False, "Compile error", None)):
        
        problem = Problem(
            name="Test Prob",
            slug="test-prob-stream-fail",
            description_md="x",
            scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/problem1",
            tests_downloaded=True,
        )
        db_session.add(problem)
        await db_session.commit()
        
        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example-stream")
        
        events = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        
        assert events[-1]["type"] == "final"
        assert events[-1]["result"]["compile_success"] is False
        assert events[-1]["result"]["compile_log"] == "Compile error"

@pytest.mark.asyncio
async def test_verify_example_solution_stream_exception(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "problem1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", side_effect=Exception("Stream BOOM")):
        
        problem = Problem(
            name="Test Prob",
            slug="test-prob-stream-boom",
            description_md="x",
            scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/problem1",
            tests_downloaded=True,
        )
        db_session.add(problem)
        await db_session.commit()
        
        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example-stream")
        
        events = []
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        
        assert events[-1]["type"] == "error"
        assert "Stream BOOM" in events[-1]["message"]

# =============================================================================
# Additional Edge Case Tests
# =============================================================================

def test_find_example_solution_not_found(tmp_path):
    problem_dir = tmp_path / "empty"
    problem_dir.mkdir()
    assert _find_example_solution(problem_dir, None) is None

def test_get_solution_expected_outcome_edge_cases(tmp_path):
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    
    # Not a list
    task_config = create_mock_task_config(problem_dir, {"solutions": "not a list"})
    assert _get_solution_expected_outcome(task_config, Path("sol.cpp")) == (None, None)
    
    # Not a dict in list
    task_config = create_mock_task_config(problem_dir, {"solutions": [123]})
    assert _get_solution_expected_outcome(task_config, Path("sol.cpp")) == (None, None)
    
    # Missing path in dict
    task_config = create_mock_task_config(problem_dir, {"solutions": [{"no_path": "x"}]})
    assert _get_solution_expected_outcome(task_config, Path("sol.cpp")) == (None, None)

@pytest.mark.asyncio
async def test_list_available_solutions_not_found(client, db_session):
    response = await client.get("/api/v1/problems/999999/solutions")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_list_available_solutions_no_source_url(client, db_session):
    problem = Problem(
        name="No Source", slug="no-source", description_md="x", scoring_mode="binary"
    )
    db_session.add(problem)
    await db_session.commit()
    response = await client.get(f"/api/v1/problems/{problem.id}/solutions")
    assert response.status_code == 200
    assert response.json() == []

@pytest.mark.asyncio
async def test_list_available_solutions_dir_not_found(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="Test Prob", slug="test-prob-nodir", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/nonexistent",
        )
        db_session.add(problem)
        await db_session.commit()
        response = await client.get(f"/api/v1/problems/{problem.id}/solutions")
        assert response.status_code == 200
        assert response.json() == []

@pytest.mark.asyncio
async def test_verify_example_no_repo(client, db_session, tmp_path):
    non_existent_repo = tmp_path / "non_existent"
    with patch("backend.config.PROBLEMS_REPO_DIR", non_existent_repo):
        problem = Problem(
            name="Test Prob", slug="test-prob-no-repo-verify", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        # Direct call for coverage
        with pytest.raises(HTTPException):
            await verify_example_solution(problem_id=problem.id, db=db_session)

        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
        assert response.status_code == 400
        assert "Local problem repository" in response.json()["detail"]

@pytest.mark.asyncio
async def test_verify_example_dir_not_found(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="Test Prob", slug="test-prob-nodir-verify", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/nonexistent",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()

        # Direct call for coverage
        with pytest.raises(HTTPException):
            await verify_example_solution(problem_id=problem.id, db=db_session)

        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
        assert response.status_code == 400
        assert "Local problem directory not found" in response.json()["detail"]

@pytest.mark.asyncio
async def test_verify_example_no_solution_found(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="Test Prob", slug="test-prob-nosol", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()

        # Direct call for coverage
        with pytest.raises(HTTPException):
            await verify_example_solution(problem_id=problem.id, db=db_session)

        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
        assert response.status_code == 404
        assert "No .cpp solution file found" in response.json()["detail"]

@pytest.mark.asyncio
async def test_verify_example_stream_no_repo(client, db_session, tmp_path):
    non_existent_repo = tmp_path / "non_existent"
    with patch("backend.config.PROBLEMS_REPO_DIR", non_existent_repo):
        problem = Problem(
            name="Test Prob", slug="test-prob-no-repo-stream", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()

        # Direct call for coverage
        with pytest.raises(HTTPException):
            await verify_example_solution_stream(problem_id=problem.id, db=db_session)

        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example-stream")
        assert response.status_code == 400

@pytest.mark.asyncio
async def test_verify_example_stream_dir_not_found(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="Test Prob", slug="test-prob-nodir-stream", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/nonexistent",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()

        # Direct call for coverage
        with pytest.raises(HTTPException):
            await verify_example_solution_stream(problem_id=problem.id, db=db_session)

        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example-stream")
        assert response.status_code == 400

@pytest.mark.asyncio
async def test_verify_example_stream_no_solution_found(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="Test Prob", slug="test-prob-nosol-stream", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()

        # Direct call for coverage
        with pytest.raises(HTTPException):
            await verify_example_solution_stream(problem_id=problem.id, db=db_session)

        response = await client.post(f"/api/v1/problems/{problem.id}/verify-example-stream")
        assert response.status_code == 404

@pytest.mark.asyncio
async def test_verify_example_no_tests_downloaded(client, db_session):
    problem = Problem(
        name="No Tests", slug="no-tests-verify", description_md="x", scoring_mode="binary",
        source_url="https://github.com/owner/repo/tree/main/p1",
        tests_downloaded=False
    )
    db_session.add(problem)
    await db_session.commit()
    
    with pytest.raises(HTTPException) as exc:
        await verify_example_solution(problem_id=problem.id, db=db_session)
    assert exc.value.status_code == 400
    assert "Tests have not been downloaded" in exc.value.detail

@pytest.mark.asyncio
async def test_verify_example_stream_no_tests_downloaded(client, db_session):
    problem = Problem(
        name="No Tests", slug="no-tests-stream", description_md="x", scoring_mode="binary",
        source_url="https://github.com/owner/repo/tree/main/p1",
        tests_downloaded=False
    )
    db_session.add(problem)
    await db_session.commit()
    
    with pytest.raises(HTTPException) as exc:
        await verify_example_solution_stream(problem_id=problem.id, db=db_session)
    assert exc.value.status_code == 400

@pytest.mark.asyncio
async def test_verify_example_solution_not_found_endpoint(client, db_session):
    with pytest.raises(HTTPException) as exc:
        await verify_example_solution(problem_id=999999, db=db_session)
    assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_verify_example_stream_problem_not_found(client, db_session):
    with pytest.raises(HTTPException) as exc:
        await verify_example_solution_stream(problem_id=999999, db=db_session)
    assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_verify_example_no_source_url_endpoint(client, db_session):
    problem = Problem(
        name="No Source", slug="no-source-verify", description_md="x", scoring_mode="binary"
    )
    db_session.add(problem)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await verify_example_solution(problem_id=problem.id, db=db_session)
    assert exc.value.status_code == 400

@pytest.mark.asyncio
async def test_verify_example_stream_no_source_url(client, db_session):
    problem = Problem(
        name="No Source", slug="no-source-stream", description_md="x", scoring_mode="binary"
    )
    db_session.add(problem)
    await db_session.commit()
    with pytest.raises(HTTPException) as exc:
        await verify_example_solution_stream(problem_id=problem.id, db=db_session)
    assert exc.value.status_code == 400

@pytest.mark.asyncio
async def test_verify_example_solution_from_task_config(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    sol_path = problem_path / "model.cpp"
    sol_path.write_text("int main() {}")
    (problem_path / "task.yaml").write_text("solutions:\n  - {path: model.cpp, name: correct}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tmp_path), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", return_value=(True, "OK", None)):
        
        problem = Problem(
            name="P1", slug="p1", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        await verify_example_solution(problem_id=problem.id, db=db_session)

@pytest.mark.asyncio
async def test_verify_example_stream_solution_from_task_config(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    sol_path = problem_path / "model.cpp"
    sol_path.write_text("int main() {}")
    (problem_path / "task.yaml").write_text("solutions:\n  - {path: model.cpp, name: correct}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tmp_path), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", side_effect=Exception("stop")):
        
        problem = Problem(
            name="P1", slug="p1-stream", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        response = await verify_example_solution_stream(problem_id=problem.id, db=db_session)
        events = []
        async for line in response.body_iterator:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        
        assert events[-1]["type"] == "error"
        assert "stop" in events[-1]["message"]

@pytest.mark.asyncio
async def test_verify_example_solution_from_all_sols(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    (problem_path / "random.cpp").write_text("int main() {}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tmp_path), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", return_value=(True, "OK", None)):
        
        problem = Problem(
            name="P1", slug="p1-all-sols", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        await verify_example_solution(problem_id=problem.id, db=db_session)

@pytest.mark.asyncio
async def test_verify_example_stream_solution_from_all_sols(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    (problem_path / "random.cpp").write_text("int main() {}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tmp_path), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", side_effect=Exception("stop")):
        
        problem = Problem(
            name="P1", slug="p1-stream-all-sols", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        response = await verify_example_solution_stream(problem_id=problem.id, db=db_session)
        events = []
        async for line in response.body_iterator:
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
        
        assert events[-1]["type"] == "error"
        assert "stop" in events[-1]["message"]

@pytest.mark.asyncio
async def test_verify_example_solution_grader_from_task_config(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    (problem_path / "my_grader.cpp").write_text("grader")
    # Include solution in task.yaml to avoid 404
    (problem_path / "task.yaml").write_text("solutions:\n  - sol.cpp\ngrader: my_grader.cpp")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tmp_path), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", return_value=(True, "OK", None)) as mock_cab:
        
        problem = Problem(
            name="P1", slug="p1-grader-task", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        await verify_example_solution(problem_id=problem.id, db=db_session)
        assert "my_grader.cpp" in mock_cab.call_args[1]["additional_files"]

@pytest.mark.asyncio
async def test_verify_example_solution_exception_direct(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", side_effect=Exception("BOOM")):
        
        problem = Problem(
            name="Test Prob", slug="test-prob-boom-direct", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True,
        )
        db_session.add(problem)
        await db_session.commit()
        
        with pytest.raises(HTTPException) as exc:
            await verify_example_solution(problem_id=problem.id, db=db_session)
        assert exc.value.status_code == 500
        assert "Benchmark error" in exc.value.detail

@pytest.mark.asyncio
async def test_verify_example_solution_custom_path_not_found(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="P1", slug="p1-custom-notfound", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        with pytest.raises(HTTPException) as exc:
            await verify_example_solution(problem_id=problem.id, request=VerifyRequest(solution_path="ghost.cpp"), db=db_session)
        assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_list_available_solutions_not_found_direct(client, db_session):
    with pytest.raises(HTTPException) as exc:
        await list_available_solutions(problem_id=999999, db=db_session)
    assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_list_available_solutions_no_source_url_direct(client, db_session):
    problem = Problem(
        name="No Source", slug="no-source-list-direct", description_md="x", scoring_mode="binary"
    )
    db_session.add(problem)
    await db_session.commit()
    res = await list_available_solutions(problem_id=problem.id, db=db_session)
    assert res == []

@pytest.mark.asyncio
async def test_list_available_solutions_dir_not_found_direct(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="Test Prob", slug="test-prob-nodir-list-direct", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/nonexistent",
        )
        db_session.add(problem)
        await db_session.commit()
        res = await list_available_solutions(problem_id=problem.id, db=db_session)
        assert res == []

@pytest.mark.asyncio
async def test_verify_example_grader_directory(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    (problem_path / "sol.cpp").write_text("int main() {}")
    grader_dir = problem_path / "grader"
    grader_dir.mkdir()
    (grader_dir / "grader.cpp").write_text("grader")
    
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         patch("backend.api.verify_example.get_problem_tests_dir", return_value=tmp_path), \
         patch("backend.sandbox.benchmark.compile_and_benchmark", return_value=(True, "OK", None)) as mock_cab:
        
        problem = Problem(
            name="P1", slug="p1-grader-dir", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        # Test regular verify
        await verify_example_solution(problem_id=problem.id, db=db_session)
        assert "grader.cpp" in mock_cab.call_args[1]["additional_files"]
        
        # Test stream verify
        await verify_example_solution_stream(problem_id=problem.id, db=db_session)
        # Verify call arguments from stream call
        # ... actually it's easier to just check coverage

def test_find_example_solution_no_keyword(tmp_path):
    problem_dir = tmp_path / "problem"
    problem_dir.mkdir()
    (problem_dir / "random.cpp").write_text("int main() {}")
    # _find_all_solutions will find it, but it doesn't match any keyword
    # so it should return the first one (line 100)
    sol = _find_example_solution(problem_dir, None)
    assert sol is not None
    assert sol.name == "random.cpp"

@pytest.mark.asyncio
async def test_verify_example_stream_custom_path_not_found_endpoint(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="P1", slug="p1-stream-custom-notfound", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        response = await client.post(
            f"/api/v1/problems/{problem.id}/verify-example-stream",
            json={"solution_path": "ghost.cpp"}
        )
        assert response.status_code == 404

@pytest.mark.asyncio
async def test_verify_example_stream_custom_path_not_found_direct(client, db_session, tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    problem_path = repo_dir / "p1"
    problem_path.mkdir()
    with patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        problem = Problem(
            name="P1", slug="p1-stream-custom-notfound-direct", description_md="x", scoring_mode="binary",
            source_url="https://github.com/owner/repo/tree/main/p1",
            tests_downloaded=True
        )
        db_session.add(problem)
        await db_session.commit()
        
        # Explicitly pass request as keyword argument
        with pytest.raises(HTTPException) as exc:
            await verify_example_solution_stream(
                problem_id=problem.id, 
                request=VerifyRequest(solution_path="ghost.cpp"), 
                db=db_session
            )
        assert exc.value.status_code == 404

def test_parse_github_url_edge_cases():
    # Test repo only
    assert _parse_github_url("https://github.com/user/repo") == {
        "owner": "user", "repo": "repo", "ref": "main", "path": ""
    }
    # Test repo with trailing slash
    assert _parse_github_url("https://github.com/user/repo/") == {
        "owner": "user", "repo": "repo", "ref": "main", "path": ""
    }
    # Test invalid URL
    with pytest.raises(Exception) as exc:
        _parse_github_url("https://google.com")
    assert "Cannot parse GitHub URL" in str(exc.value)
