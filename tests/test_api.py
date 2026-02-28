import pytest
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from backend.database.models import Problem, Run


# =============================================================================
# Helpers
# =============================================================================

def make_problem_payload(**overrides):
    """Return a minimal valid problem creation payload."""
    base = {
        "name": "Test Problem",
        "slug": "test-problem",
        "description_md": "# Test Description",
        "time_limit_ms": 1000,
        "memory_limit_mb": 128,
        "scoring_mode": "binary",
    }
    base.update(overrides)
    return base


def make_run_config(**overrides):
    """Return a minimal valid run config dict."""
    base = {
        "models": [{"slug": "openrouter/free", "enabled": True}],
        "judge_model": {"slug": "openrouter/free"},
        "prompts": {"system_prompt": "x", "round_1_prompt": "y"},
        "scoring": {
            "correctness_weight": 0.6,
            "speed_weight": 0.25,
            "memory_weight": 0.15,
            "penalty_wrong_answer": -0.5,
        },
        "execution": {
            "timeout_buffer_ms": 100,
            "enable_cache_simulation": True,
            "measure_memory_peak": True,
        },
    }
    base.update(overrides)
    return base


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
async def setup_problem(db_session):
    """Create a prerequisite problem for run-related tests."""
    problem = Problem(
        name="Run Problem",
        slug="run-problem-fixture",
        description_md="Desc",
        scoring_mode="binary",
        test_count=1,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)
    return problem


@pytest.fixture
async def setup_problem_with_tests(db_session, tmp_path, monkeypatch):
    """
    Create a problem that looks like it has tests downloaded, with a mock
    local repo directory containing a reference solution.
    """
    problem = Problem(
        name="IOI Problem",
        slug="ioi-problem-fixture",
        description_md="Desc",
        scoring_mode="binary",
        test_count=2,
        tests_downloaded=True,
        source_url="https://github.com/owner/repo/tree/main/ioi2023-soccer",
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    # Build a fake local repo structure
    repo_dir = tmp_path / "repo"
    problem_dir = repo_dir / "ioi2023-soccer"
    sol_dir = problem_dir / "solution"
    sol_dir.mkdir(parents=True)
    (sol_dir / "solution.cpp").write_text('#include<iostream>\nint main(){int x;std::cin>>x;std::cout<<x;}')

    import backend.config as cfg
    monkeypatch.setattr(cfg, "PROBLEMS_REPO_DIR", repo_dir)

    return problem


# =============================================================================
# Problem CRUD
# =============================================================================

@pytest.mark.asyncio
async def test_problem_crud(client, db_session):
    """Test full CRUD lifecycle for problems."""
    # 1. Create
    response = await client.post("/api/v1/problems", json=make_problem_payload())
    assert response.status_code == 201
    data = response.json()
    problem_id = data["id"]
    assert data["scoring_mode"] == "binary"

    # 2. Verify DB fields
    problem = await db_session.get(Problem, problem_id)
    assert problem.name == "Test Problem"
    assert problem.tests_downloaded is False
    assert problem.source_url is None

    # 3. List
    response = await client.get("/api/v1/problems")
    assert response.status_code == 200
    assert any(p["id"] == problem_id for p in response.json())

    # 4. Get
    response = await client.get(f"/api/v1/problems/{problem_id}")
    assert response.status_code == 200
    assert response.json()["slug"] == "test-problem"
    assert response.json()["description_md"] == "# Test Description"

    # 5. Update
    response = await client.put(
        f"/api/v1/problems/{problem_id}",
        json={"name": "Updated Problem", "time_limit_ms": 2000},
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Problem"
    assert response.json()["time_limit_ms"] == 2000
    # Unchanged fields stay the same
    assert response.json()["scoring_mode"] == "binary"

    # 6. Delete
    response = await client.delete(f"/api/v1/problems/{problem_id}")
    assert response.status_code == 204

    # 7. Confirm deletion
    response = await client.get(f"/api/v1/problems/{problem_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_problem_duplicate_slug(client):
    """Duplicate slug must return 400."""
    payload = make_problem_payload(slug="duplicate-slug", name="First")
    assert (await client.post("/api/v1/problems", json=payload)).status_code == 201
    response = await client.post("/api/v1/problems", json=payload)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_problem_not_found(client):
    """Non-existent problem returns 404."""
    assert (await client.get("/api/v1/problems/999999")).status_code == 404


@pytest.mark.asyncio
async def test_problem_invalid_scoring_mode(client):
    """scoring_mode must be one of binary|partial|custom."""
    response = await client.post(
        "/api/v1/problems",
        json=make_problem_payload(scoring_mode="standard"),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_problem_invalid_slug_characters(client):
    """Slug must match ^[a-z0-9_-]+$."""
    response = await client.post(
        "/api/v1/problems",
        json=make_problem_payload(slug="Invalid Slug!"),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_problem_time_limit_bounds(client):
    """time_limit_ms must be within [100, 60000]."""
    response = await client.post(
        "/api/v1/problems",
        json=make_problem_payload(slug="too-fast", time_limit_ms=50),
    )
    assert response.status_code == 422

    response = await client.post(
        "/api/v1/problems",
        json=make_problem_payload(slug="too-slow", time_limit_ms=99999),
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_problem_update_slug_conflict(client):
    """Updating a problem's slug to one already taken returns 400."""
    await client.post("/api/v1/problems", json=make_problem_payload(slug="slug-a", name="A"))
    r2 = await client.post("/api/v1/problems", json=make_problem_payload(slug="slug-b", name="B"))
    b_id = r2.json()["id"]

    response = await client.put(f"/api/v1/problems/{b_id}", json={"slug": "slug-a"})
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_problem_list_includes_scoring_mode(client):
    """ProblemListItem must include scoring_mode (regression: was missing before)."""
    await client.post("/api/v1/problems", json=make_problem_payload(slug="list-test-p"))
    response = await client.get("/api/v1/problems")
    assert response.status_code == 200
    item = next(p for p in response.json() if p["slug"] == "list-test-p")
    assert "scoring_mode" in item
    assert item["scoring_mode"] == "binary"


# =============================================================================
# Run CRUD & state machine
# =============================================================================

@pytest.mark.asyncio
@patch("backend.api.runs.execute_run_background")
async def test_run_crud_and_states(mock_background, client, setup_problem):
    problem = setup_problem

    run_data = {
        "name": "Test Run",
        "problem_id": problem.id,
        "config_json": json.dumps(make_run_config()),
    }

    # 1. Create
    response = await client.post("/api/v1/runs", json=run_data)
    assert response.status_code == 201
    assert response.json()["status"] == "configured"
    run_id = response.json()["id"]

    # 2. Get (config parsed correctly)
    response = await client.get(f"/api/v1/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["config"]["models"][0]["slug"] == "openrouter/free"

    # 3. Update name
    response = await client.put(f"/api/v1/runs/{run_id}", json={"name": "Updated Run"})
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Run"

    # 4. Start
    response = await client.post(f"/api/v1/runs/{run_id}/start", json={"num_rounds": 5})
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    mock_background.assert_called_once_with(run_id, 5)

    # 5. Pause
    response = await client.post(f"/api/v1/runs/{run_id}/pause")
    assert response.status_code == 200
    assert response.json()["status"] == "pausing"

    # 6. Delete
    response = await client.delete(f"/api/v1/runs/{run_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_run_invalid_json(client, setup_problem):
    """Malformed config_json returns 422."""
    run_data = {
        "name": "Bad JSON Run",
        "problem_id": setup_problem.id,
        "config_json": "{ invalid_json: true }",
    }
    assert (await client.post("/api/v1/runs", json=run_data)).status_code == 422


@pytest.mark.asyncio
async def test_run_missing_required_config_keys(client, setup_problem):
    """Config missing required keys returns 422."""
    bad_config = {"models": [{"slug": "test"}]}  # missing judge_model, prompts, etc.
    run_data = {
        "name": "Bad Config Run",
        "problem_id": setup_problem.id,
        "config_json": json.dumps(bad_config),
    }
    assert (await client.post("/api/v1/runs", json=run_data)).status_code == 422


@pytest.mark.asyncio
async def test_run_nonexistent_problem(client):
    """Creating a run for a non-existent problem returns 400 (FK validation)."""
    run_data = {
        "name": "Orphan Run",
        "problem_id": 999999,
        "config_json": json.dumps(make_run_config()),
    }
    response = await client.post("/api/v1/runs", json=run_data)
    assert response.status_code == 400


# =============================================================================
# Verify Example Solution endpoint
# =============================================================================


@pytest.mark.asyncio
async def test_verify_example_not_found(client):
    """Non-existent problem returns 404."""
    response = await client.post("/api/v1/problems/999999/verify-example")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_verify_example_no_source_url(client, db_session):
    """Problem without source_url returns 400."""
    problem = Problem(
        name="No Source",
        slug="no-source-url",
        description_md="x",
        scoring_mode="binary",
        tests_downloaded=True,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
    assert response.status_code == 400
    assert "source URL" in response.json()["detail"]


@pytest.mark.asyncio
async def test_verify_example_tests_not_downloaded(client, db_session):
    """Problem with tests_downloaded=False returns 400."""
    problem = Problem(
        name="No Tests",
        slug="no-tests-downloaded",
        description_md="x",
        scoring_mode="binary",
        tests_downloaded=False,
        source_url="https://github.com/owner/repo/tree/main/problem",
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
    assert response.status_code == 400
    assert "Tests have not been downloaded" in response.json()["detail"]


@pytest.mark.asyncio
@patch("backend.sandbox.benchmark.compile_and_benchmark")
async def test_verify_example_compile_failure(mock_cab, client, setup_problem_with_tests):
    """When compilation fails the response still returns 200 with compile_success=False."""
    mock_cab.return_value = (False, "error: missing semicolon", None)
    problem = setup_problem_with_tests

    response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
    assert response.status_code == 200
    data = response.json()
    assert data["compile_success"] is False
    assert "missing semicolon" in data["compile_log"]
    assert data["tests_passed"] == 0
    assert data["test_results"] == []


@pytest.mark.asyncio
@patch("backend.sandbox.benchmark.compile_and_benchmark")
async def test_verify_example_all_pass(mock_cab, client, setup_problem_with_tests):
    """Successful verification returns correct aggregated results."""
    from backend.sandbox.benchmark import BenchmarkSummary, TestCaseResult

    mock_summary = BenchmarkSummary(
        solution_id=-1,
        tests_passed=2,
        tests_total=2,
        avg_time_ms=45.2,
        max_time_ms=67.1,
        max_memory_kb=8192,
        all_passed=True,
        test_results=[
            TestCaseResult(test_index=1, test_name="1.in", passed=True, actual_output="1", time_ms=40.0, memory_kb=4096, exit_code=0, error="", verdict="AC"),
            TestCaseResult(test_index=2, test_name="2.in", passed=True, actual_output="2", time_ms=50.4, memory_kb=8192, exit_code=0, error="", verdict="AC"),
        ],
    )
    mock_cab.return_value = (True, "Compilation and benchmarking successful", mock_summary)
    problem = setup_problem_with_tests

    response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
    assert response.status_code == 200
    data = response.json()
    assert data["compile_success"] is True
    assert data["all_passed"] is True
    assert data["tests_passed"] == 2
    assert data["tests_total"] == 2
    assert data["avg_time_ms"] == pytest.approx(45.2)
    assert data["max_time_ms"] == pytest.approx(67.1)
    assert len(data["test_results"]) == 2
    assert data["test_results"][0]["verdict"] == "AC"
    assert "solution_file" in data


@pytest.mark.asyncio
@patch("backend.sandbox.benchmark.compile_and_benchmark")
async def test_verify_example_partial_pass(mock_cab, client, setup_problem_with_tests):
    """Partial pass sets all_passed=False and reports per-test verdicts."""
    from backend.sandbox.benchmark import BenchmarkSummary, TestCaseResult

    mock_summary = BenchmarkSummary(
        solution_id=-1,
        tests_passed=1,
        tests_total=2,
        avg_time_ms=40.0,
        max_time_ms=40.0,
        max_memory_kb=4096,
        all_passed=False,
        test_results=[
            TestCaseResult(test_index=1, test_name="1.in", passed=True,  actual_output="1", time_ms=40.0, memory_kb=4096, exit_code=0,  error="",                  verdict="AC"),
            TestCaseResult(test_index=2, test_name="2.in", passed=False, actual_output="",  time_ms=0.0,  memory_kb=0,    exit_code=-1, error="Time limit exceeded", verdict="TLE"),
        ],
    )
    mock_cab.return_value = (True, "ok", mock_summary)
    problem = setup_problem_with_tests

    response = await client.post(f"/api/v1/problems/{problem.id}/verify-example")
    assert response.status_code == 200
    data = response.json()
    assert data["all_passed"] is False
    assert data["tests_passed"] == 1
    verdicts = [r["verdict"] for r in data["test_results"]]
    assert "TLE" in verdicts
    assert data["test_results"][1]["error"] == "Time limit exceeded"