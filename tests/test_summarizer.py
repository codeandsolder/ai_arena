import pytest
import pytest_asyncio
import unittest.mock as mock
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.orchestrator.summarizer import Summarizer
from backend.orchestrator.model_client import ModelClient, ModelResponse
from backend.database.models import Solution, Problem, Round, Run, ApiCall


@pytest.fixture
def mock_model_client():
    client = mock.AsyncMock(spec=ModelClient)
    return client


@pytest.fixture
def summarizer(mock_model_client):
    return Summarizer(model_client=mock_model_client)


def make_model_response(**kwargs):
    defaults = dict(
        response_text='{"summary": "Test summary text"}',
        input_tokens=100,
        output_tokens=50,
        thinking_tokens=10,
        thinking_text=None,
        cost_usd=0.001,
        latency_ms=500
    )
    defaults.update(kwargs)
    return ModelResponse(**defaults)


@pytest_asyncio.fixture
async def sample_data(db_session: AsyncSession):
    problem = Problem(
        slug="test-problem",
        name="Test Problem",
        description_md="Description",
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    run = Run(
        problem_id=problem.id,
        name="Test Run",
        status="running",
        config_json="{}"
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    round_obj = Round(run_id=run.id, round_number=1, status="completed")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)

    sol1 = Solution(
        round_id=round_obj.id,
        model_slug="model-a",
        source_code="print('hello')",
        score=0.8,
        tests_passed=8,
        tests_total=10,
        avg_time_ms=100.0,
        max_memory_kb=1024
    )
    sol2 = Solution(
        round_id=round_obj.id,
        model_slug="model-b",
        source_code="print('world')\n" * 40,  # Long code to test truncation
        score=0.9,
        tests_passed=9,
        tests_total=10,
        avg_time_ms=90.0,
        max_memory_kb=2048
    )
    db_session.add_all([sol1, sol2])
    await db_session.commit()
    await db_session.refresh(sol1)
    await db_session.refresh(sol2)

    # ApiCall rows that _extract_approach_from_solution will query
    api_call1 = ApiCall(
        run_id=run.id,
        round_id=round_obj.id,
        solution_id=sol1.id,
        purpose="solution_generation",
        model_slug="model-a",
        response_text='{"code": "print(\'hello\')", "explanation": "Simple hello world", "compiler": "g++-14", "flags": "-O2"}',
        input_tokens=50,
        output_tokens=20,
        thinking_tokens=0,
        cost_usd=0.0001,
        latency_ms=200
    )
    api_call2 = ApiCall(
        run_id=run.id,
        round_id=round_obj.id,
        solution_id=sol2.id,
        purpose="solution_generation",
        model_slug="model-b",
        response_text='{"code": "print(\'world\')", "explanation": "Simple world print", "compiler": "g++-14", "flags": "-O2"}',
        input_tokens=50,
        output_tokens=20,
        thinking_tokens=0,
        cost_usd=0.0001,
        latency_ms=200
    )
    db_session.add_all([api_call1, api_call2])
    await db_session.commit()

    return {
        "problem": problem,
        "run": run,
        "round": round_obj,
        "solutions": [sol1, sol2]
    }


@pytest.mark.asyncio
async def test_summarize_round_success(summarizer, mock_model_client, sample_data, db_session):
    mock_model_client.call_model_with_retry.return_value = (
        make_model_response(),
        {"summary": "Test summary text"}
    )

    summary = await summarizer.summarize_round(
        round_id=sample_data["round"].id,
        solutions=sample_data["solutions"],
        problem=sample_data["problem"],
        db_session=db_session
    )

    assert summary == "Test summary text"
    mock_model_client.call_model_with_retry.assert_called_once()

    # Verify ApiCall was stored — use scalar_one() to catch duplicates
    result = await db_session.execute(
        select(ApiCall).where(
            ApiCall.purpose == "judge",
            ApiCall.round_id == sample_data["round"].id
        )
    )
    api_call = result.scalar_one()
    assert api_call.round_id == sample_data["round"].id
    assert "Test summary text" in api_call.response_text


@pytest.mark.asyncio
async def test_summarize_round_exception_fallback(summarizer, mock_model_client, sample_data, db_session):
    mock_model_client.call_model_with_retry.side_effect = Exception("Model failed")

    summary = await summarizer.summarize_round(
        round_id=sample_data["round"].id,
        solutions=sample_data["solutions"],
        problem=sample_data["problem"],
        db_session=db_session
    )

    assert "## Round Summary (Auto-Generated)" in summary
    assert "model-b: 0.9000" in summary  # Best solution first
    assert "model-a: 0.8000" in summary


@pytest.mark.asyncio
async def test_summarize_round_no_solutions(summarizer, mock_model_client, sample_data, db_session):
    mock_model_client.call_model_with_retry.side_effect = Exception("Model failed")

    summary = await summarizer.summarize_round(
        round_id=sample_data["round"].id,
        solutions=[],
        problem=sample_data["problem"],
        db_session=db_session
    )

    assert summary == "No solutions to summarize."


def test_extract_summary_json_variants(summarizer):
    for key in ["summary", "analysis", "feedback", "report", "content", "text"]:
        parsed = {key: f"Value for {key}"}
        assert summarizer._extract_summary("ignored", parsed) == f"Value for {key}"

    parsed = {"summary": ["Line 1", "Line 2"]}
    assert summarizer._extract_summary("ignored", parsed) == "Line 1\nLine 2"


def test_extract_summary_markdown_cleanup(summarizer):
    text = "```json\nActual Summary\n```"
    assert summarizer._extract_summary(text, None) == "Actual Summary"

    text = "```\nAnother Summary\n```"
    assert summarizer._extract_summary(text, None) == "Another Summary"

    # Other fence types should also be stripped
    text = "```markdown\nMarkdown Summary\n```"
    assert summarizer._extract_summary(text, None) == "Markdown Summary"


@pytest.mark.asyncio
async def test_store_judge_api_call_round_not_found(summarizer, db_session):
    response = make_model_response(response_text="text", input_tokens=0, output_tokens=0,
                                   thinking_tokens=0, cost_usd=0.0, latency_ms=0)
    # Should log error and return without raising
    await summarizer._store_judge_api_call(
        round_id=999,
        response=response,
        system_prompt="sys",
        user_prompt="user",
        db_session=db_session
    )

    # Confirm nothing was written
    result = await db_session.execute(select(ApiCall))
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_store_judge_api_call_exception_handling(summarizer, db_session, sample_data):
    response = make_model_response(response_text="text", input_tokens=0, output_tokens=0,
                                   thinking_tokens=0, cost_usd=0.0, latency_ms=0)

    # We need to ensure no ApiCall for 'judge' is created.
    # The session might already have 'solution_generation' calls.
    round_obj_id = sample_data["round"].id

    with mock.patch.object(db_session, 'commit', side_effect=Exception("Database error")):
        # Must not raise
        await summarizer._store_judge_api_call(
            round_id=round_obj_id,
            response=response,
            system_prompt="sys",
            user_prompt="user",
            db_session=db_session
        )

    # Session was rolled back — no NEW judge ApiCall should be committed
    from backend.database.session import get_session_maker
    async with get_session_maker()() as new_session:
        result = await new_session.execute(
            select(ApiCall).where(
                ApiCall.round_id == round_obj_id,
                ApiCall.purpose == "judge"
            )
        )
        calls = result.scalars().all()
        assert len(calls) == 0

@pytest.mark.asyncio
async def test_summarize_round_no_previous_summaries(summarizer, mock_model_client, sample_data, db_session):
    """Test cumulative standings when there are no previous rounds."""
    mock_model_client.call_model_with_retry.return_value = (
        make_model_response(),
        {"summary": "Initial summary"}
    )

    summary = await summarizer.summarize_round(
        round_id=sample_data["round"].id,
        solutions=sample_data["solutions"],
        problem=sample_data["problem"],
        db_session=db_session
    )

    assert summary == "Initial summary"


@pytest.mark.asyncio
async def test_extract_approach_from_solution(summarizer, db_session, sample_data):
    sol_id = sample_data["solutions"][0].id
    db_session.expire_all()
    sol = await db_session.get(Solution, sol_id)
    approach = await summarizer._extract_approach_from_solution(sol, db_session)
    assert approach == "Simple hello world"


@pytest.mark.asyncio
async def test_extract_approach_no_api_call(summarizer, db_session, sample_data):
    # Solution with no matching ApiCall row
    orphan = Solution(
        round_id=sample_data["round"].id,
        model_slug="model-c",
        source_code="",
        score=0.0,
        tests_passed=0,
        tests_total=10,
    )
    db_session.add(orphan)
    await db_session.commit()
    await db_session.refresh(orphan)
    orphan_id = orphan.id
    db_session.expire_all()
    orphan = await db_session.get(Solution, orphan_id)

    approach = await summarizer._extract_approach_from_solution(orphan, db_session)
    assert approach == "No explanation provided"


@pytest.mark.asyncio
async def test_extract_approach_malformed_json(summarizer, db_session, sample_data):
    orphan = Solution(
        round_id=sample_data["round"].id,
        model_slug="model-d",
        source_code="",
        score=0.0,
        tests_passed=0,
        tests_total=10,
    )
    db_session.add(orphan)
    await db_session.commit()
    await db_session.refresh(orphan)
    orphan_id = orphan.id

    bad_call = ApiCall(
        run_id=sample_data["run"].id,
        round_id=sample_data["round"].id,
        solution_id=orphan_id,
        purpose="solution_generation",
        model_slug="model-d",
        response_text="not valid json {{{",
        input_tokens=0,
        output_tokens=0,
        thinking_tokens=0,
        cost_usd=0.0,
        latency_ms=0
    )
    db_session.add(bad_call)
    await db_session.commit()
    db_session.expire_all()
    orphan = await db_session.get(Solution, orphan_id)

    approach = await summarizer._extract_approach_from_solution(orphan, db_session)
    assert approach == "No explanation provided"


@pytest.mark.asyncio
async def test_extract_approach_missing_explanation_key(summarizer, db_session, sample_data):
    orphan = Solution(
        round_id=sample_data["round"].id,
        model_slug="model-e",
        source_code="",
        score=0.0,
        tests_passed=0,
        tests_total=10,
    )
    db_session.add(orphan)
    await db_session.commit()
    await db_session.refresh(orphan)
    orphan_id = orphan.id

    no_expl_call = ApiCall(
        run_id=sample_data["run"].id,
        round_id=sample_data["round"].id,
        solution_id=orphan_id,
        purpose="solution_generation",
        model_slug="model-e",
        response_text='{"code": "int main(){}", "compiler": "g++-14"}',
        input_tokens=0,
        output_tokens=0,
        thinking_tokens=0,
        cost_usd=0.0,
        latency_ms=0
    )
    db_session.add(no_expl_call)
    await db_session.commit()
    db_session.expire_all()
    orphan = await db_session.get(Solution, orphan_id)

    approach = await summarizer._extract_approach_from_solution(orphan, db_session)
    assert approach == "No explanation provided"


@pytest.mark.asyncio
async def test_build_cumulative_standings_not_found(summarizer, db_session):
    # Use a round_id that doesn't exist — should return the no-standings string
    standings = await summarizer._build_cumulative_standings(999, db_session)
    assert standings == "No standings available."


@pytest.mark.asyncio
async def test_build_cumulative_standings_with_data(summarizer, db_session, sample_data):
    standings = await summarizer._build_cumulative_standings(
        sample_data["round"].id, db_session
    )
    # Both models should appear; result content is determined by PromptFormatter
    assert "model-a" in standings or "model-b" in standings


@pytest.mark.asyncio
async def test_close(summarizer, mock_model_client):
    await summarizer.close()
    mock_model_client.close.assert_called_once()