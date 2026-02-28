import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database.models import Problem, Run, ApiCall

@pytest.mark.asyncio
async def test_get_run_api_calls(client: AsyncClient, db_session: AsyncSession):
    # Setup: Create a problem, a run, and some API calls
    problem = Problem(
        name="Test Problem",
        slug="test-problem",
        description_md="Test description",
        time_limit_ms=1000,
        memory_limit_mb=256
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    run = Run(
        name="Test Run",
        problem_id=problem.id,
        status="completed",
        config_json="{}",
        total_rounds=1
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    api_call1 = ApiCall(
        run_id=run.id,
        purpose="generation1",
        model_slug="test-model",
        prompt_text="Test prompt 1",
        response_text="Test response 1"
    )
    api_call2 = ApiCall(
        run_id=run.id,
        purpose="generation2",
        model_slug="test-model",
        prompt_text="Test prompt 2",
        response_text="Test response 2"
    )
    db_session.add_all([api_call1, api_call2])
    await db_session.commit()

    # Test: Get API calls for the run
    response = await client.get(f"/api/v1/runs/{run.id}/api-calls")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    # Ordered by id desc, so api_call2 should be first
    assert data["items"][0]["purpose"] == "generation2"

    # Test: Pagination
    response = await client.get(f"/api/v1/runs/{run.id}/api-calls?page=1&page_size=1")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 1
    assert data["pages"] == 2

    # Test: Run not found
    response = await client.get("/api/v1/runs/999/api-calls")
    assert response.status_code == 404
    assert "Run with id 999 not found" in response.json()["detail"]

@pytest.mark.asyncio
async def test_get_api_call_by_id(client: AsyncClient, db_session: AsyncSession):
    # Setup: Create a problem, a run, and an API call
    problem = Problem(
        name="Test Problem 2",
        slug="test-problem-2",
        description_md="Test description",
        time_limit_ms=1000,
        memory_limit_mb=256
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    run = Run(
        name="Test Run 2",
        problem_id=problem.id,
        status="completed",
        config_json="{}",
        total_rounds=1
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    api_call = ApiCall(
        run_id=run.id,
        purpose="generation",
        model_slug="test-model",
        prompt_text="Test prompt",
        response_text="Test response"
    )
    db_session.add(api_call)
    await db_session.commit()
    await db_session.refresh(api_call)

    # Test: Get API call by ID
    response = await client.get(f"/api/v1/api-calls/{api_call.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == api_call.id
    assert data["prompt_text"] == "Test prompt"

    # Test: API call not found
    response = await client.get("/api/v1/api-calls/999")
    assert response.status_code == 404
    assert "API call with id 999 not found" in response.json()["detail"]
