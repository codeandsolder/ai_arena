import pytest
import json
from unittest.mock import patch
from backend.database.models import Problem, Run

# =============================================================================
# Problem API Tests
# =============================================================================

@pytest.mark.asyncio
async def test_problem_crud(client, db_session):
    """Test full CRUD lifecycle for problems."""
    # 1. Create a problem
    problem_data = {
        "name": "Test Problem",
        "slug": "test-problem",
        "description_md": "# Test Description",
        "time_limit_ms": 1000,
        "memory_limit_mb": 128,
        "scoring_mode": "binary"
    }
    response = await client.post("/api/v1/problems", json=problem_data)
    assert response.status_code == 201
    problem_id = response.json()["id"]
    
    # 2. Verify database fields
    problem = await db_session.get(Problem, problem_id)
    assert problem.name == "Test Problem"
    assert problem.tests_downloaded is False
    assert problem.source_url is None
    
    # 3. List problems
    response = await client.get("/api/v1/problems")
    assert response.status_code == 200
    assert len(response.json()) >= 1
    
    # 4. Get specific problem
    response = await client.get(f"/api/v1/problems/{problem_id}")
    assert response.status_code == 200
    assert response.json()["slug"] == "test-problem"

    # 5. Update problem
    update_data = {"name": "Updated Test Problem", "time_limit_ms": 2000}
    response = await client.put(f"/api/v1/problems/{problem_id}", json=update_data)
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Test Problem"
    assert response.json()["time_limit_ms"] == 2000

    # 6. Delete problem
    response = await client.delete(f"/api/v1/problems/{problem_id}")
    assert response.status_code == 204

    # 7. Verify deletion
    response = await client.get(f"/api/v1/problems/{problem_id}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_problem_duplicate_slug(client):
    """Test that creating a problem with a duplicate slug fails."""
    problem_data = {
        "name": "Slug Test",
        "slug": "duplicate-slug",
        "description_md": "Desc"
    }
    # First creation should succeed
    response = await client.post("/api/v1/problems", json=problem_data)
    assert response.status_code == 201

    # Second creation should fail
    response = await client.post("/api/v1/problems", json=problem_data)
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]


@pytest.mark.asyncio
async def test_problem_not_found(client):
    """Test handling of non-existent problems."""
    response = await client.get("/api/v1/problems/999999")
    assert response.status_code == 404


# =============================================================================
# Run API Tests
# =============================================================================

@pytest.fixture
async def setup_problem(db_session):
    """Helper fixture to create a prerequisite problem for runs."""
    problem = Problem(
        name="Run Problem",
        slug="run-problem-fixture",
        description_md="Desc",
        test_count=1
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)
    return problem


import pytest
import json
from unittest.mock import patch
from backend.database.models import Problem, Run

# ... [keep problem tests] ...

@pytest.mark.asyncio
@patch("backend.api.runs.execute_run_background")
async def test_run_crud_and_states(mock_background, client, setup_problem):
    """Test full CRUD and state transition lifecycle for runs."""
    problem = setup_problem
    
    # 1. Create a run with correct JSON structure corresponding to RunConfig schemas
    valid_config = {
        "models": [{"slug": "openrouter/free", "enabled": True}],
        "judge_model": {"slug": "openrouter/free"},
        "prompts": {"system_prompt": "x", "round_1_prompt": "y"},
        "scoring": {"correctness_weight": 0.6, "speed_weight": 0.25, "memory_weight": 0.15, "penalty_wrong_answer": -0.5},
        "execution": {"timeout_buffer_ms": 100, "enable_cache_simulation": True, "measure_memory_peak": True}
    }
    
    run_data = {
        "name": "Test Run",
        "problem_id": problem.id,
        "config_json": json.dumps(valid_config)
    }
    
    response = await client.post("/api/v1/runs", json=run_data)
    assert response.status_code == 201
    assert response.json()["status"] == "configured"
    run_id = response.json()["id"]

    # 2. Get Run
    response = await client.get(f"/api/v1/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["config"]["models"][0]["slug"] == "openrouter/free"

    # 3. Update Run
    update_data = {"name": "Updated Test Run"}
    response = await client.put(f"/api/v1/runs/{run_id}", json=update_data)
    assert response.status_code == 200
    assert response.json()["name"] == "Updated Test Run"

    # 4. Start Run
    response = await client.post(f"/api/v1/runs/{run_id}/start", json={"num_rounds": 5})
    assert response.status_code == 200
    assert response.json()["status"] == "running"
    
    # Verify our background task was actually called
    mock_background.assert_called_once_with(run_id, 5)

    # 5. Pause Run (Now this will pass because the mock prevented the status from changing to 'error')
    response = await client.post(f"/api/v1/runs/{run_id}/pause")
    assert response.status_code == 200
    assert response.json()["status"] == "pausing"

    # 6. Delete Run
    response = await client.delete(f"/api/v1/runs/{run_id}")
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_run_invalid_json(client, setup_problem):
    """Test that creating a run with malformed config_json fails."""
    problem = setup_problem
    
    run_data = {
        "name": "Bad JSON Run",
        "problem_id": problem.id,
        "config_json": "{ invalid_json: true }"
    }
    
    response = await client.post("/api/v1/runs", json=run_data)
    assert response.status_code == 422 # Pydantic validation error


@pytest.mark.asyncio
async def test_run_missing_required_config_keys(client, setup_problem):
    """Test that creating a run missing required top-level config keys fails."""
    problem = setup_problem
    
    # Missing 'judge_model'
    bad_config = {"models": [{"slug": "test"}]}
    run_data = {
        "name": "Bad Config Run",
        "problem_id": problem.id,
        "config_json": json.dumps(bad_config)
    }
    
    response = await client.post("/api/v1/runs", json=run_data)
    assert response.status_code == 422 # Pydantic validation error