import pytest
import unittest.mock as mock
import asyncio
import json
from pathlib import Path
from backend.orchestrator.engine import (
    OrchestrationEngine, RunConfig, GenerationResult,
)
from backend.orchestrator.security import SecurityAnalysisResult
from backend.orchestrator.model_client import ModelResponse
from backend.database.models import (
    Problem, Run, Solution, Round, TestResult, ApiCall,
)
from backend.sandbox.benchmark import BenchmarkSummary, TestCaseResult
from sqlalchemy import select


# ═══════════════════════════════════════════════════════════════════════════
# Helpers & Fixtures
# ═══════════════════════════════════════════════════════════════════════════

def _model_response(**overrides):
    """Build a ModelResponse with sensible defaults."""
    defaults = dict(
        response_text="ok",
        input_tokens=10,
        output_tokens=20,
        thinking_tokens=0,
        cost_usd=0.0,
        latency_ms=100,
    )
    defaults.update(overrides)
    return ModelResponse(**defaults)


def _benchmark_summary(solution_id, passed=1, total=1, all_passed=True):
    """Build a BenchmarkSummary with sensible defaults."""
    return BenchmarkSummary(
        solution_id=solution_id,
        tests_passed=passed,
        tests_total=total,
        avg_time_ms=50.0,
        max_time_ms=50.0,
        max_memory_kb=1024,
        all_passed=all_passed,
        test_results=[
            TestCaseResult(
                test_index=i, passed=(i < passed), actual_output="",
                time_ms=50.0, memory_kb=1024, exit_code=0,
                verdict="passed" if i < passed else "wrong_answer", error="",
            )
            for i in range(total)
        ],
    )


@pytest.fixture
def mock_orchestrator_deps():
    """Mock external dependencies for OrchestrationEngine."""
    with mock.patch("backend.orchestrator.engine.ModelClient") as mock_client_cls, \
         mock.patch("backend.orchestrator.engine.Summarizer") as mock_summarizer_cls, \
         mock.patch("backend.orchestrator.engine.SecurityAnalyzer") as mock_security_cls, \
         mock.patch("backend.orchestrator.engine.compile_solution") as mock_compile, \
         mock.patch("backend.orchestrator.engine.benchmark_solution") as mock_benchmark:

        # --- SecurityAnalyzer ---
        mock_security = mock_security_cls.return_value
        mock_security.analyze_solution = mock.AsyncMock(
            return_value=SecurityAnalysisResult(
                is_safe=True, risk_level="LOW", details="Safe",
                flags_safe=True, code_safe=True, model_responses=[],
            )
        )
        # expose the *class* mock too so tests can replace security_models attr
        mock_security_cls._instance = mock_security

        # --- ModelClient ---
        mock_client = mock_client_cls.return_value
        mock_client.generate_response = mock.AsyncMock(return_value=_model_response())
        mock_client.get_prices = mock.AsyncMock(return_value={"prompt": 0.0, "completion": 0.0})
        mock_client.call_model_with_retry = mock.AsyncMock(return_value=(
            _model_response(),
            {"code": "int main(){return 0;}", "compiler": "g++", "flags": "-O2"},
        ))
        mock_client.close = mock.AsyncMock()

        # --- Summarizer ---
        mock_summarizer = mock_summarizer_cls.return_value
        mock_summarizer.summarize_round = mock.AsyncMock(return_value="Round Summary")
        mock_summarizer.close = mock.AsyncMock()

        # --- Compile (success by default) ---
        mock_compile.return_value = (True, "Compiled successfully", "", "/tmp/mock_bin")

        # --- Benchmark ---
        async def _benchmark_side_effect(*args, **kwargs):
            sid = kwargs.get("solution_id")
            return _benchmark_summary(sid)
        mock_benchmark.side_effect = _benchmark_side_effect

        yield {
            "client_cls": mock_client_cls,
            "client": mock_client,
            "summarizer_cls": mock_summarizer_cls,
            "summarizer": mock_summarizer,
            "security_cls": mock_security_cls,
            "security": mock_security,
            "compile": mock_compile,
            "benchmark": mock_benchmark,
        }


def _make_run_config(**overrides):
    """Build a standard JSON-serialisable config dict."""
    config = {
        "models": ["openrouter/free"],
        "judge_model": "openrouter/free",
        "security_models": ["openrouter/free"],
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
    config.update(overrides)
    return config


async def _create_problem_and_run(db_session, *, tests_downloaded=True,
                                   config_overrides=None):
    """Create a Problem (with test files) and a Run; return (problem, run)."""
    problem = Problem(
        name="Sum Problem", slug="sum-problem",
        description_md="Add two numbers", test_count=1,
        tests_downloaded=tests_downloaded,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    from backend.api.problems import get_problem_tests_dir
    tests_dir = get_problem_tests_dir(problem.id)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "0.in").write_text("1 2")
    (tests_dir / "0.out").write_text("3")

    run = Run(
        name="Test Run", problem_id=problem.id,
        config_json=json.dumps(_make_run_config(**(config_overrides or {}))),
        status="configured",
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)
    return problem, run


async def _make_round_and_solution(db_session, run, *, round_number=1,
                                    sol_kwargs=None):
    """Create a Round + one Solution; return (round_obj, sol)."""
    round_obj = Round(run_id=run.id, round_number=round_number, status="running")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)

    defaults = dict(
        round_id=round_obj.id, model_slug="model-a", status="pending",
        source_code="int main(){}", compiler="g++", compiler_flags="-O2",
    )
    defaults.update(sol_kwargs or {})
    sol = Solution(**defaults)
    db_session.add(sol)
    await db_session.commit()
    await db_session.refresh(sol)
    return round_obj, sol


def _patch_engine_session(db_session, deps):
    """Patch engine imports so it uses the test db_session and mocked deps."""
    # We want a session maker that returns something that behaves like db_session
    # but doesn't close it when the engine calls await db_session.close()
    
    class SessionProxy:
        def __init__(self, real_session):
            self.real_session = real_session
        def __getattr__(self, name):
            return getattr(self.real_session, name)
        async def close(self):
            # Swallow close to keep the test session alive
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    return mock.patch.multiple(
        "backend.orchestrator.engine",
        get_session_maker=mock.MagicMock(return_value=lambda: SessionProxy(db_session)),
        ModelClient=mock.MagicMock(return_value=deps["client"]),
        Summarizer=mock.MagicMock(return_value=deps["summarizer"]),
        SecurityAnalyzer=mock.MagicMock(return_value=deps["security"]),
    )


def _make_engine(db_session, deps):
    """Create an engine inside the patch context (must be called while
    _patch_engine_session context is active)."""
    return OrchestrationEngine()


# ═══════════════════════════════════════════════════════════════════════════
# 1. Full Pipeline
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_full_pipeline(db_session, mock_orchestrator_deps):
    """End-to-end: generate → compile → benchmark → score → judge → complete."""
    problem, run = await _create_problem_and_run(db_session)
    run_id = run.id  # Store to prevent MissingGreenlet later

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

        async def fake_generate(run_id, round_id, problem, config,
                                previous_summary, db_session):
            sol = Solution(
                round_id=round_id, model_slug="openrouter/free",
                status="pending",
                source_code="#include <iostream>\nint main() { return 0; }",
                compiler="g++", compiler_flags="-O3",
            )
            db_session.add(sol)
            await db_session.commit()
            await db_session.refresh(sol)
            return [sol]

        engine._generate_solutions = mock.AsyncMock(side_effect=fake_generate)
        await engine._run_competition(run_id, num_rounds=1)

    db_session.expire_all()
    run = await db_session.get(Run, run_id)
    assert run.status == "completed"
    assert run.total_rounds == 1

    round_obj = (await db_session.execute(
        select(Round).where(Round.run_id == run_id)
    )).scalar_one()
    assert round_obj.round_number == 1
    assert round_obj.status == "completed"

    sol = (await db_session.execute(
        select(Solution).where(Solution.round_id == round_obj.id)
    )).scalar_one()
    assert sol.tests_passed == 1
    assert sol.tests_total == 1
    assert sol.status == "completed"
    assert sol.compile_success is True
    assert sol.avg_time_ms == 50.0
    assert sol.max_memory_kb == 1024


# ═══════════════════════════════════════════════════════════════════════════
# 2. Multi-round & previous_summary propagation
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_multi_round_summary_propagation(db_session, mock_orchestrator_deps):
    """previous_summary from round N is forwarded to round N+1."""
    problem, run = await _create_problem_and_run(db_session)
    run_id = run.id  # Store to prevent MissingGreenlet
    captured_summaries = []

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

        async def fake_generate(run_id, round_id, problem, config,
                                previous_summary, db_session):
            captured_summaries.append(previous_summary)
            sol = Solution(
                round_id=round_id, model_slug="openrouter/free",
                status="pending", source_code="int main(){}",
                compiler="g++", compiler_flags="-O2",
            )
            db_session.add(sol)
            await db_session.commit()
            await db_session.refresh(sol)
            return [sol]

        engine._generate_solutions = mock.AsyncMock(side_effect=fake_generate)
        await engine._run_competition(run_id, num_rounds=3)

    assert len(captured_summaries) == 3
    # Round 1 has no previous summary
    assert captured_summaries[0] is None
    # Rounds 2+ receive the summarizer output ("Round Summary")
    assert captured_summaries[1] == "Round Summary"
    assert captured_summaries[2] == "Round Summary"

    db_session.expire_all()
    run = await db_session.get(Run, run_id)
    assert run.total_rounds == 3


# ═══════════════════════════════════════════════════════════════════════════
# 3. _run_competition error paths
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_run_competition_run_not_found(db_session, mock_orchestrator_deps):
    """Raises ValueError when run_id doesn't exist."""
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        with pytest.raises(ValueError, match="Run 99999 not found"):
            await engine._run_competition(99999, num_rounds=1)


@pytest.mark.asyncio
async def test_run_competition_problem_not_found(db_session, mock_orchestrator_deps):
    """Raises ValueError when the run's problem doesn't exist."""
    # Create a run pointing at a non-existent problem
    run = Run(
        name="Orphan Run", problem_id=99999,
        config_json=json.dumps(_make_run_config()), status="configured",
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        with pytest.raises(ValueError, match="Problem for run"):
            await engine._run_competition(run.id, num_rounds=1)


@pytest.mark.asyncio
async def test_run_competition_paused_mid_loop(db_session, mock_orchestrator_deps):
    """A run paused between rounds breaks out and gets status 'paused'."""
    problem, run = await _create_problem_and_run(db_session)
    run_id = run.id  # Store to prevent MissingGreenlet
    round_counter = {"n": 0}

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

        async def fake_generate(run_id, round_id, problem, config,
                                previous_summary, db_session):
            round_counter["n"] += 1
            # After round 1 completes, mark as paused before round 2 starts
            if round_counter["n"] == 1:
                engine._paused_runs.add(run_id)
            sol = Solution(
                round_id=round_id, model_slug="openrouter/free",
                status="pending", source_code="int main(){}",
                compiler="g++", compiler_flags="-O2",
            )
            db_session.add(sol)
            await db_session.commit()
            await db_session.refresh(sol)
            return [sol]

        engine._generate_solutions = mock.AsyncMock(side_effect=fake_generate)
        await engine._run_competition(run_id, num_rounds=5)

    db_session.expire_all()
    run = await db_session.get(Run, run_id)
    # Only 1 round should have executed; then paused before round 2
    assert run.total_rounds == 1
    assert run.status == "paused"


@pytest.mark.asyncio
async def test_run_competition_lazy_downloads_tests(db_session, mock_orchestrator_deps):
    """Tests are downloaded lazily when tests_downloaded=False."""
    problem, run = await _create_problem_and_run(
        db_session, tests_downloaded=False,
    )

    with _patch_engine_session(db_session, mock_orchestrator_deps), \
         mock.patch("backend.services.problem_ingestion.IOIIngestor", autospec=True) as mock_ingestor_cls:

        # Make the async-context-manager work
        mock_ingestor = mock.AsyncMock()
        mock_ingestor_cls.return_value.__aenter__ = mock.AsyncMock(return_value=mock_ingestor)
        mock_ingestor_cls.return_value.__aexit__ = mock.AsyncMock(return_value=False)

        engine = _make_engine(db_session, mock_orchestrator_deps)

        async def fake_generate(*a, **kw):
            # Mark tests as downloaded so the rest of the pipeline works
            db_s = a[5] if len(a) > 5 else kw["db_session"]
            sol = Solution(
                round_id=a[1] if len(a) > 1 else kw["round_id"],
                model_slug="openrouter/free", status="pending",
                source_code="int main(){}", compiler="g++",
                compiler_flags="-O2",
            )
            db_s.add(sol)
            await db_s.commit()
            await db_s.refresh(sol)
            return [sol]

        engine._generate_solutions = mock.AsyncMock(side_effect=fake_generate)
        await engine._run_competition(run.id, num_rounds=1)

    mock_ingestor.sync_tests.assert_awaited_once_with(problem.id)


# ═══════════════════════════════════════════════════════════════════════════
# 4. start_run (task lifecycle)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_start_run_success(db_session, mock_orchestrator_deps):
    """start_run creates a task, awaits it, and cleans up _active_runs."""
    problem, run = await _create_problem_and_run(db_session)
    run_id = run.id  # Store to prevent MissingGreenlet

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

        async def fake_generate(run_id, round_id, problem, config,
                                previous_summary, db_session):
            sol = Solution(
                round_id=round_id, model_slug="openrouter/free",
                status="pending", source_code="int main(){}",
                compiler="g++", compiler_flags="-O2",
            )
            db_session.add(sol)
            await db_session.commit()
            await db_session.refresh(sol)
            return [sol]

        engine._generate_solutions = mock.AsyncMock(side_effect=fake_generate)
        await engine.start_run(run_id, num_rounds=1)

    # Task should be cleaned up
    assert run_id not in engine._active_runs
    db_session.expire_all()
    run = await db_session.get(Run, run_id)
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_start_run_failure_marks_failed(db_session, mock_orchestrator_deps):
    """When _run_competition raises, start_run marks run as 'failed'."""
    problem, run = await _create_problem_and_run(db_session)
    run_id = run.id  # Store to prevent MissingGreenlet

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        engine._run_competition = mock.AsyncMock(
            side_effect=RuntimeError("boom"),
        )

        with pytest.raises(RuntimeError, match="boom"):
            await engine.start_run(run_id, num_rounds=1)

        assert run_id not in engine._active_runs
        db_session.expire_all()
        run = await db_session.get(Run, run_id)
        assert run.status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 5. _generate_single_solution
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_single_solution_success(db_session, mock_orchestrator_deps):
    """Happy path: model returns valid JSON with code."""
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    result = await engine._generate_single_solution(
        run_id=1, round_id=1, solution_id=42, model_slug="test-model",
        system_prompt="sys", user_prompt="usr", config=RunConfig(),
    )
    assert result.success is True
    assert result.code == "int main(){return 0;}"
    assert result.compiler == "g++"
    assert result.compiler_flags == "-O2"
    assert result.api_response is not None


@pytest.mark.asyncio
async def test_generate_single_solution_no_json(db_session, mock_orchestrator_deps):
    """When the model returns no parseable JSON, generation fails gracefully."""
    mock_orchestrator_deps["client"].call_model_with_retry = mock.AsyncMock(
        return_value=(_model_response(), None),
    )

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    result = await engine._generate_single_solution(
        run_id=1, round_id=1, solution_id=42, model_slug="test-model",
        system_prompt="sys", user_prompt="usr", config=RunConfig(),
    )
    assert result.success is False
    assert "parse JSON" in result.error_message


@pytest.mark.asyncio
async def test_generate_single_solution_empty_code(db_session, mock_orchestrator_deps):
    """When JSON has an empty 'code' field, generation fails."""
    mock_orchestrator_deps["client"].call_model_with_retry = mock.AsyncMock(
        return_value=(_model_response(), {"code": "", "compiler": "g++"}),
    )

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    result = await engine._generate_single_solution(
        run_id=1, round_id=1, solution_id=42, model_slug="test-model",
        system_prompt="sys", user_prompt="usr", config=RunConfig(),
    )
    assert result.success is False
    assert "No code" in result.error_message


@pytest.mark.asyncio
async def test_generate_single_solution_exception(db_session, mock_orchestrator_deps):
    """When the model call throws, generation returns a failed result."""
    mock_orchestrator_deps["client"].call_model_with_retry = mock.AsyncMock(
        side_effect=ConnectionError("timeout"),
    )

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    result = await engine._generate_single_solution(
        run_id=1, round_id=1, solution_id=42, model_slug="test-model",
        system_prompt="sys", user_prompt="usr", config=RunConfig(),
    )
    assert result.success is False
    assert "timeout" in result.error_message


# ═══════════════════════════════════════════════════════════════════════════
# 6. _generate_solutions (aggregation)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_solutions_stores_api_call(db_session, mock_orchestrator_deps):
    """Successful generation stores an ApiCall record."""
    problem, run = await _create_problem_and_run(db_session)
    round_obj = Round(run_id=run.id, round_number=1, status="generating")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    config = RunConfig(models=["model-a"])
    solutions = await engine._generate_solutions(
        run_id=run.id, round_id=round_obj.id, problem=problem,
        config=config, previous_summary=None, db_session=db_session,
    )

    assert len(solutions) == 1
    assert solutions[0].status == "pending"
    assert solutions[0].source_code == "int main(){return 0;}"

    api_calls = (await db_session.execute(
        select(ApiCall).where(ApiCall.purpose == "solution_generation")
    )).scalars().all()
    assert len(api_calls) == 1
    assert api_calls[0].model_slug == "model-a"


@pytest.mark.asyncio
async def test_generate_solutions_failed_generation_excluded(
    db_session, mock_orchestrator_deps
):
    """A model that fails generation is excluded from the returned list."""
    mock_orchestrator_deps["client"].call_model_with_retry = mock.AsyncMock(
        return_value=(_model_response(), None),  # no JSON → failure
    )

    problem, run = await _create_problem_and_run(db_session)
    round_obj = Round(run_id=run.id, round_number=1, status="generating")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    config = RunConfig(models=["model-a"])
    solutions = await engine._generate_solutions(
        run_id=run.id, round_id=round_obj.id, problem=problem,
        config=config, previous_summary=None, db_session=db_session,
    )

    # No pending solutions returned
    assert solutions == []

    # But the solution record exists in the DB as failed
    all_sols = (await db_session.execute(
        select(Solution).where(Solution.round_id == round_obj.id)
    )).scalars().all()
    assert len(all_sols) == 1
    assert all_sols[0].status == "failed"


@pytest.mark.asyncio
async def test_generate_solutions_exception_marks_failed(
    db_session, mock_orchestrator_deps
):
    """When _generate_single_solution raises, the solution is marked failed."""
    mock_orchestrator_deps["client"].call_model_with_retry = mock.AsyncMock(
        side_effect=RuntimeError("API down"),
    )

    problem, run = await _create_problem_and_run(db_session)
    round_obj = Round(run_id=run.id, round_number=1, status="generating")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    config = RunConfig(models=["model-a"])
    solutions = await engine._generate_solutions(
        run_id=run.id, round_id=round_obj.id, problem=problem,
        config=config, previous_summary=None, db_session=db_session,
    )

    assert solutions == []
    all_sols = (await db_session.execute(
        select(Solution).where(Solution.round_id == round_obj.id)
    )).scalars().all()
    assert len(all_sols) == 1
    assert all_sols[0].status == "failed"
    assert "API down" in all_sols[0].error_message


@pytest.mark.asyncio
async def test_generate_solutions_uses_round_n_prompt(
    db_session, mock_orchestrator_deps
):
    """When previous_summary is provided, the round-N prompt is used."""
    problem, run = await _create_problem_and_run(db_session)
    round_obj = Round(run_id=run.id, round_number=2, status="generating")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    with mock.patch.object(engine.prompt_formatter, "format_round_n_user",
                           return_value="round N prompt") as mock_rn, \
         mock.patch.object(engine.prompt_formatter, "format_round_1_user") as mock_r1:
        config = RunConfig(models=["model-a"])
        await engine._generate_solutions(
            run_id=run.id, round_id=round_obj.id, problem=problem,
            config=config, previous_summary="prev summary",
            db_session=db_session,
        )

    mock_rn.assert_called_once()
    mock_r1.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# 7. RunConfig parsing
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_parse_run_config_defaults(db_session, mock_orchestrator_deps):
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    cfg = engine._parse_run_config("{}")
    assert cfg.models == []
    assert cfg.num_rounds == 5
    assert cfg.correctness_weight == 0.6
    assert cfg.judge_model == "anthropic/claude-3.5-sonnet"


@pytest.mark.asyncio
async def test_parse_run_config_custom(db_session, mock_orchestrator_deps):
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    raw = json.dumps({"models": ["a", "b"], "num_rounds": 3,
                       "correctness_weight": 0.8})
    cfg = engine._parse_run_config(raw)
    assert cfg.models == ["a", "b"]
    assert cfg.num_rounds == 3
    assert cfg.correctness_weight == 0.8


@pytest.mark.asyncio
async def test_parse_run_config_invalid_json(db_session, mock_orchestrator_deps):
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    cfg = engine._parse_run_config("not json")
    assert cfg.models == []
    assert cfg.num_rounds == 5


# ═══════════════════════════════════════════════════════════════════════════
# 8. Scoring
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_score_single_correct(db_session, mock_orchestrator_deps):
    """Single correct solution → score 1.0."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(
            status="completed", compile_success=True,
            tests_passed=5, tests_total=5,
            avg_time_ms=100.0, max_memory_kb=2048,
        ),
    )
    config = RunConfig(correctness_weight=0.6, speed_weight=0.25,
                       memory_weight=0.15)
    await engine._score_solutions(round_obj.id, config, db_session)
    await db_session.refresh(sol)
    assert sol.score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_score_no_correct_solutions(db_session, mock_orchestrator_deps):
    """All partially-passing solutions get score 0."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(
            status="completed", compile_success=True,
            tests_passed=3, tests_total=5, avg_time_ms=100.0,
        ),
    )
    await engine._score_solutions(round_obj.id, RunConfig(), db_session)
    await db_session.refresh(sol)
    assert sol.score == 0.0


@pytest.mark.asyncio
async def test_score_multiple_models(db_session, mock_orchestrator_deps):
    """Faster/leaner solution scores higher."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    round_obj = Round(run_id=run.id, round_number=1, status="scoring")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)

    fast = Solution(round_id=round_obj.id, model_slug="fast", status="completed",
                    compile_success=True, tests_passed=5, tests_total=5,
                    avg_time_ms=50.0, max_memory_kb=1024)
    slow = Solution(round_id=round_obj.id, model_slug="slow", status="completed",
                    compile_success=True, tests_passed=5, tests_total=5,
                    avg_time_ms=200.0, max_memory_kb=4096)
    db_session.add_all([fast, slow])
    await db_session.commit()

    config = RunConfig(correctness_weight=0.6, speed_weight=0.25,
                       memory_weight=0.15)
    await engine._score_solutions(round_obj.id, config, db_session)
    await db_session.refresh(fast)
    await db_session.refresh(slow)

    assert fast.score == pytest.approx(1.0)
    expected_slow = 0.6 * 1.0 + 0.25 * (50 / 200) + 0.15 * (1024 / 4096)
    assert slow.score == pytest.approx(expected_slow)
    assert fast.score > slow.score


@pytest.mark.asyncio
async def test_score_compile_failed(db_session, mock_orchestrator_deps):
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(
            status="compile_failed", compile_success=False,
            tests_passed=0, tests_total=0,
        ),
    )
    await engine._score_solutions(round_obj.id, RunConfig(), db_session)
    await db_session.refresh(sol)
    assert sol.score == 0.0


@pytest.mark.asyncio
async def test_score_empty_round(db_session, mock_orchestrator_deps):
    """Scoring an empty round (no solutions) doesn't crash."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj = Round(run_id=run.id, round_number=1, status="scoring")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)
    # Should not raise
    await engine._score_solutions(round_obj.id, RunConfig(), db_session)


@pytest.mark.asyncio
async def test_score_null_time_and_memory(db_session, mock_orchestrator_deps):
    """Solution with None avg_time_ms / max_memory_kb gets 0 for those axes."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

    round_obj = Round(run_id=run.id, round_number=1, status="scoring")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)

    # One correct solution with metrics (needed so the round has a baseline)
    good = Solution(round_id=round_obj.id, model_slug="good", status="completed",
                    compile_success=True, tests_passed=5, tests_total=5,
                    avg_time_ms=100.0, max_memory_kb=2048)
    # Another correct solution with null metrics
    null_sol = Solution(round_id=round_obj.id, model_slug="null-metrics",
                        status="completed", compile_success=True,
                        tests_passed=5, tests_total=5,
                        avg_time_ms=None, max_memory_kb=None)
    db_session.add_all([good, null_sol])
    await db_session.commit()

    config = RunConfig(correctness_weight=0.6, speed_weight=0.25,
                       memory_weight=0.15)
    await engine._score_solutions(round_obj.id, config, db_session)
    await db_session.refresh(null_sol)
    # correctness=1.0*0.6, speed=0, memory=0
    assert null_sol.score == pytest.approx(0.6)


# ═══════════════════════════════════════════════════════════════════════════
# 9. Compile phase
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_compile_success(db_session, mock_orchestrator_deps):
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)
    compiled = await engine._compile_solutions(
        run.id, round_obj.id, [sol], RunConfig(), db_session,
    )
    assert len(compiled) == 1
    assert compiled[0][0].compile_success is True


@pytest.mark.asyncio
async def test_compile_failure_no_retry(db_session, mock_orchestrator_deps):
    mock_orchestrator_deps["compile"].return_value = (False, "", "error: x", "")
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)
    compiled = await engine._compile_solutions(
        run.id, round_obj.id, [sol],
        RunConfig(allow_error_retry=False), db_session,
    )
    assert compiled == []
    await db_session.refresh(sol)
    assert sol.status == "compile_failed"
    assert sol.compile_success is False


@pytest.mark.asyncio
async def test_compile_security_exception_fails_safe(
    db_session, mock_orchestrator_deps
):
    """When SecurityAnalyzer.analyze_solution raises, solution → security_error."""
    mock_orchestrator_deps["security"].analyze_solution = mock.AsyncMock(
        side_effect=RuntimeError("security service down"),
    )
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)
    compiled = await engine._compile_solutions(
        run.id, round_obj.id, [sol], RunConfig(), db_session,
    )
    assert compiled == []
    await db_session.refresh(sol)
    assert sol.status == "security_error"
    assert "security service down" in sol.error_message


@pytest.mark.asyncio
async def test_compile_malformed_flags_fallback(db_session, mock_orchestrator_deps):
    """Malformed compiler_flags fall back to defaults without crashing."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(compiler_flags="'unterminated"),
    )
    compiled = await engine._compile_solutions(
        run.id, round_obj.id, [sol], RunConfig(), db_session,
    )
    # Should still compile (mock returns success)
    assert len(compiled) == 1


# ═══════════════════════════════════════════════════════════════════════════
# 10. Security failure blocks compilation
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_security_failure_blocks_compilation(db_session, mock_orchestrator_deps):
    mock_orchestrator_deps["security"].analyze_solution = mock.AsyncMock(
        return_value=SecurityAnalysisResult(
            is_safe=False, risk_level="HIGH",
            details="Dangerous syscall detected",
            flags_safe=True, code_safe=False, model_responses=[],
        ),
    )
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(source_code="system('rm -rf /')"),
    )
    compiled = await engine._compile_solutions(
        run.id, round_obj.id, [sol], RunConfig(), db_session,
    )
    assert compiled == []
    await db_session.refresh(sol)
    assert sol.status == "security_failed"
    assert "Dangerous syscall" in sol.error_message


# ═══════════════════════════════════════════════════════════════════════════
# 11. Benchmark phase
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_benchmark_updates_solution(db_session, mock_orchestrator_deps):
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(status="compiled", compile_success=True),
    )
    from backend.config import DATA_DIR
    tcd = str(DATA_DIR / "problems" / problem.slug / "test_cases")
    await engine._benchmark_solutions(
        run.id, round_obj.id, [(sol, "/tmp/mock")], tcd, problem, db_session,
    )
    await db_session.refresh(sol)
    assert sol.tests_passed == 1
    assert sol.tests_total == 1
    assert sol.avg_time_ms == 50.0
    assert sol.max_memory_kb == 1024
    assert sol.status == "completed"


@pytest.mark.asyncio
async def test_benchmark_exception_marks_failed(db_session, mock_orchestrator_deps):
    """When benchmark_solution raises, solution → benchmark_failed."""
    mock_orchestrator_deps["benchmark"].side_effect = RuntimeError("sandbox crash")
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(status="compiled", compile_success=True),
    )
    from backend.config import DATA_DIR
    tcd = str(DATA_DIR / "problems" / problem.slug / "test_cases")
    await engine._benchmark_solutions(
        run.id, round_obj.id, [(sol, "/tmp/mock")], tcd, problem, db_session,
    )
    await db_session.refresh(sol)
    assert sol.status == "benchmark_failed"
    assert "sandbox crash" in sol.error_message


@pytest.mark.asyncio
async def test_benchmark_partial_pass(db_session, mock_orchestrator_deps):
    """Benchmark with some tests failing → status 'failed', metrics still stored."""
    async def _partial(*args, **kwargs):
        return _benchmark_summary(kwargs.get("solution_id"),
                                  passed=2, total=5, all_passed=False)
    mock_orchestrator_deps["benchmark"].side_effect = _partial

    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(status="compiled", compile_success=True),
    )
    from backend.config import DATA_DIR
    tcd = str(DATA_DIR / "problems" / problem.slug / "test_cases")
    await engine._benchmark_solutions(
        run.id, round_obj.id, [(sol, "/tmp/mock")], tcd, problem, db_session,
    )
    await db_session.refresh(sol)
    assert sol.tests_passed == 2
    assert sol.tests_total == 5
    assert sol.status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# 12. _retry_solution_with_error
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_retry_no_source_code(db_session, mock_orchestrator_deps):
    """Early-return None when solution has no source_code."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(
        db_session, run, sol_kwargs=dict(source_code=None),
    )
    result = await engine._retry_solution_with_error(
        run.id, round_obj.id, sol, "error", "/tmp/bin",
        RunConfig(), db_session,
    )
    assert result is None


@pytest.mark.asyncio
async def test_retry_success(db_session, mock_orchestrator_deps):
    """Retry: model returns new code → security passes → compile succeeds."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)

    result = await engine._retry_solution_with_error(
        run.id, round_obj.id, sol, "error: missing semicolon", "/tmp/bin",
        RunConfig(), db_session,
    )
    assert result is not None
    ret_sol, ret_path = result
    assert ret_sol.status == "compiled"
    assert ret_sol.compile_success is True

    # Verify API call stored
    api_calls = (await db_session.execute(
        select(ApiCall).where(ApiCall.purpose == "error_retry")
    )).scalars().all()
    assert len(api_calls) == 1


@pytest.mark.asyncio
async def test_retry_no_json_returns_none(db_session, mock_orchestrator_deps):
    """Retry where model returns no parseable JSON → returns None."""
    mock_orchestrator_deps["client"].call_model_with_retry = mock.AsyncMock(
        return_value=(_model_response(), None),
    )
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)

    result = await engine._retry_solution_with_error(
        run.id, round_obj.id, sol, "error", "/tmp/bin",
        RunConfig(), db_session,
    )
    assert result is None


@pytest.mark.asyncio
async def test_retry_security_failure(db_session, mock_orchestrator_deps):
    """Retry: security check fails on the retried code → None."""
    mock_orchestrator_deps["security"].analyze_solution = mock.AsyncMock(
        return_value=SecurityAnalysisResult(
            is_safe=False, risk_level="HIGH",
            details="Unsafe on retry",
            flags_safe=True, code_safe=False, model_responses=[],
        ),
    )
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)

    result = await engine._retry_solution_with_error(
        run.id, round_obj.id, sol, "error", "/tmp/bin",
        RunConfig(), db_session,
    )
    assert result is None
    assert sol.status == "security_failed"
    assert "on retry" in sol.error_message


@pytest.mark.asyncio
async def test_retry_security_exception(db_session, mock_orchestrator_deps):
    """Retry: security analyzer raises → security_error."""
    mock_orchestrator_deps["security"].analyze_solution = mock.AsyncMock(
        side_effect=RuntimeError("security down"),
    )
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)

    result = await engine._retry_solution_with_error(
        run.id, round_obj.id, sol, "error", "/tmp/bin",
        RunConfig(), db_session,
    )
    assert result is None
    assert sol.status == "security_error"


@pytest.mark.asyncio
async def test_retry_recompile_fails(db_session, mock_orchestrator_deps):
    """Retry: new code compiles unsuccessfully → returns None."""
    mock_orchestrator_deps["compile"].return_value = (False, "", "error: again", "")
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)

    result = await engine._retry_solution_with_error(
        run.id, round_obj.id, sol, "error", "/tmp/bin",
        RunConfig(), db_session,
    )
    assert result is None
    assert sol.compile_success is False


@pytest.mark.asyncio
async def test_retry_model_exception(db_session, mock_orchestrator_deps):
    """Retry: model call throws → returns None (outer except)."""
    mock_orchestrator_deps["client"].call_model_with_retry = mock.AsyncMock(
        side_effect=ConnectionError("timeout"),
    )
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)

    result = await engine._retry_solution_with_error(
        run.id, round_obj.id, sol, "error", "/tmp/bin",
        RunConfig(), db_session,
    )
    assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# 13. Compile → retry integration (via _compile_solutions)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_compile_triggers_retry_on_failure(db_session, mock_orchestrator_deps):
    """Compile failure with allow_error_retry=True triggers _retry_solution_with_error."""
    call_count = {"n": 0}
    original_return = (False, "", "error: oops", "")
    retry_return = (True, "ok", "", "/tmp/retry_bin")

    async def _compile_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return original_return
        return retry_return
    mock_orchestrator_deps["compile"].return_value = None
    mock_orchestrator_deps["compile"].side_effect = _compile_side_effect

    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)

    config = RunConfig(allow_error_retry=True, max_error_retries=2)
    compiled = await engine._compile_solutions(
        run.id, round_obj.id, [sol], config, db_session,
    )

    # First compile failed, retry succeeded
    assert len(compiled) == 1
    assert compiled[0][0].status == "compiled"
    assert call_count["n"] == 2  # initial + retry


# ═══════════════════════════════════════════════════════════════════════════
# 14. Pause / Resume / Close
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_pause_run(db_session, mock_orchestrator_deps):
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        await engine.pause_run(run.id)
    assert run.id in engine._paused_runs
    await db_session.refresh(run)
    assert run.status == "paused"


@pytest.mark.asyncio
async def test_pause_cancels_active_task(db_session, mock_orchestrator_deps):
    """pause_run cancels a tracked active task."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        fake_task = mock.MagicMock()
        engine._active_runs[run.id] = fake_task
        await engine.pause_run(run.id)
    fake_task.cancel.assert_called_once()


@pytest.mark.asyncio
async def test_resume_run(db_session, mock_orchestrator_deps):
    """resume_run clears paused flag, broadcasts, and calls start_run."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        engine._paused_runs.add(run.id)
        engine.start_run = mock.AsyncMock()
        await engine.resume_run(run.id, num_rounds=3)

    assert run.id not in engine._paused_runs
    engine.start_run.assert_awaited_once_with(run.id, 3)


@pytest.mark.asyncio
async def test_close_cancels_and_cleans_up(db_session, mock_orchestrator_deps):
    """close() cancels all active tasks and closes client/summarizer."""
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        engine.summarizer = mock_orchestrator_deps["summarizer"]

    task1 = mock.MagicMock()
    task2 = mock.MagicMock()
    engine._active_runs = {1: task1, 2: task2}

    await engine.close()

    task1.cancel.assert_called_once()
    task2.cancel.assert_called_once()
    engine.model_client.close.assert_awaited_once()
    engine.summarizer.close.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════
# 15. Round-level: empty round, round exception
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_round_no_solutions(db_session, mock_orchestrator_deps):
    """A round where generation returns [] → round status 'failed'."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        engine._generate_solutions = mock.AsyncMock(return_value=[])
        summary = await engine.run_round(
            run.id, 1, problem, RunConfig(models=["m"]), None, db_session,
        )
    assert summary is None
    round_obj = (await db_session.execute(
        select(Round).where(Round.run_id == run.id)
    )).scalar_one()
    assert round_obj.status == "failed"


@pytest.mark.asyncio
async def test_round_stores_summary(db_session, mock_orchestrator_deps):
    """run_round stores the summarizer output in the Round record."""
    problem, run = await _create_problem_and_run(db_session)

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)

        async def fake_generate(run_id, round_id, problem, config,
                                previous_summary, db_session):
            sol = Solution(
                round_id=round_id, model_slug="m", status="pending",
                source_code="int main(){}", compiler="g++",
                compiler_flags="-O2",
            )
            db_session.add(sol)
            await db_session.commit()
            await db_session.refresh(sol)
            return [sol]

        engine._generate_solutions = mock.AsyncMock(side_effect=fake_generate)
        summary = await engine.run_round(
            run.id, 1, problem, RunConfig(models=["m"]), None, db_session,
        )

    assert summary == "Round Summary"
    round_obj = (await db_session.execute(
        select(Round).where(Round.run_id == run.id)
    )).scalar_one()
    assert round_obj.summary_text == "Round Summary"


# ═══════════════════════════════════════════════════════════════════════════
# 16. _update_run_status edge cases
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_update_run_status_without_session(db_session, mock_orchestrator_deps):
    """_update_run_status with no db_session creates and closes its own."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        # Call without providing db_session — it will use get_session_maker()
        await engine._update_run_status(run.id, "running")
    await db_session.refresh(run)
    assert run.status == "running"


# ═══════════════════════════════════════════════════════════════════════════
# 17. Additional Edge Case & Coverage Tests
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_update_run_status_rollback(db_session, mock_orchestrator_deps):
    """If db commit fails during _update_run_status, it should rollback and swallow."""
    problem, run = await _create_problem_and_run(db_session)
    run_id = run.id

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        with mock.patch.object(db_session, "commit", side_effect=RuntimeError("db error")), \
             mock.patch.object(db_session, "rollback") as mock_rollback:
            
            # Should not crash, just logs the error and rolls back
            await engine._update_run_status(run_id, "running", db_session)
            mock_rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_round_status_rollback(db_session, mock_orchestrator_deps):
    """If db commit fails during _update_round_status, it rolls back and raises."""
    problem, run = await _create_problem_and_run(db_session)
    round_obj = Round(run_id=run.id, round_number=1, status="generating")
    db_session.add(round_obj)
    await db_session.commit()
    await db_session.refresh(round_obj)
    round_id = round_obj.id

    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        with mock.patch.object(db_session, "commit", side_effect=RuntimeError("db error")), \
             mock.patch.object(db_session, "rollback") as mock_rollback:
            
            # This method explicitly raises after rollback
            with pytest.raises(RuntimeError, match="db error"):
                await engine._update_round_status(round_id, "failed", db_session)
            mock_rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_malformed_flags_fallback(db_session, mock_orchestrator_deps):
    """Retry falls back to default flags if shlex parsing throws on invalid flags."""
    mock_orchestrator_deps["client"].call_model_with_retry = mock.AsyncMock(
        return_value=(
            _model_response(), 
            {"code": "int main(){}", "flags": "'unterminated string"}
        )
    )
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
    round_obj, sol = await _make_round_and_solution(db_session, run)

    result = await engine._retry_solution_with_error(
        run.id, round_obj.id, sol, "error", "/tmp/bin",
        RunConfig(), db_session
    )
    assert result is not None
    
    # Assert that compile was called with the fallback flags (which are valid)
    called_args, called_kwargs = mock_orchestrator_deps["compile"].call_args
    assert called_kwargs["flags"] == ["-O2", "-std=c++20"]


@pytest.mark.asyncio
async def test_run_round_exception_marks_failed(db_session, mock_orchestrator_deps):
    """A general exception raised mid-round catches the outer block and marks round failed."""
    problem, run = await _create_problem_and_run(db_session)
    with _patch_engine_session(db_session, mock_orchestrator_deps):
        engine = _make_engine(db_session, mock_orchestrator_deps)
        
        # Inject an exception into Phase 1
        engine._generate_solutions = mock.AsyncMock(side_effect=RuntimeError("unexpected pipeline crash"))

        with pytest.raises(RuntimeError, match="unexpected pipeline crash"):
            await engine.run_round(run.id, 1, problem, RunConfig(models=["m"]), None, db_session)

    round_obj = (await db_session.execute(
        select(Round).where(Round.run_id == run.id)
    )).scalar_one()
    
    # The except block in run_round ensures the DB reflects the failure before bubbling up
    assert round_obj.status == "failed"