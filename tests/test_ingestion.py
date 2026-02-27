import pytest
import unittest.mock as mock
import os
import shutil
import json
import hashlib
from pathlib import Path
from backend.services.problem_ingestion import IOIIngestor
from backend.database.models import Problem, Run
from backend.orchestrator.engine import OrchestrationEngine
from backend import config

@pytest.fixture
def mock_local_repo(tmp_path):
    """Create a mock local problems repository."""
    repo_dir = tmp_path / "problems_repo"
    repo_dir.mkdir()
    
    # Create a mock problem
    prob_dir = repo_dir / "local-prob"
    prob_dir.mkdir()
    (prob_dir / "statement.md").write_text("# Local Problem\nDescription here.")
    
    tests_dir = prob_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "0.in").write_text("local in")
    (tests_dir / "0.out").write_text("local out")
    
    with mock.patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        yield repo_dir

@pytest.fixture
def mock_repo_with_symlink(tmp_path):
    """Create a mock local problems repository with a symlink file."""
    repo_dir = tmp_path / "problems_repo_symlink"
    repo_dir.mkdir()
    
    # Create a problem directory
    prob_dir = repo_dir / "symlink-prob"
    prob_dir.mkdir()
    (prob_dir / "statement.md").write_text("# Symlink Problem\nDescription here.")
    
    tests_dir = prob_dir / "tests"
    tests_dir.mkdir()
    
    # Create target file INSIDE the problem directory (or subdir of it)
    # So that relative path like "actual_test.in" or "./actual_test.in" works from tests_dir
    (tests_dir / "actual_test.in").write_text("target input content")
    
    # Create a file that acts as a symlink (contains relative path)
    # The relative path is "actual_test.in" (same directory)
    link_file = tests_dir / "0.in"
    link_file.write_text("actual_test.in")
    
    out_file = tests_dir / "0.out"
    out_file.write_text("expected output")
    
    with mock.patch("backend.config.PROBLEMS_REPO_DIR", repo_dir):
        yield repo_dir

@pytest.fixture
def mock_github(tmp_path):
    """Mock GitHub API calls for IOIIngestor."""
    non_existent_dir = tmp_path / "does_not_exist"
    with mock.patch("backend.config.PROBLEMS_REPO_DIR", non_existent_dir), \
         mock.patch("backend.services.problem_ingestion.IOIIngestor.fetch_contents") as mock_fetch, \
         mock.patch("backend.services.problem_ingestion.IOIIngestor.download_file") as mock_dl:
        
        # Mock directory listing
        def side_effect_fetch(owner, repo, path, ref):
            if not path or path == "":
                return [{"name": "problem1", "type": "dir", "path": "problem1"}]
            if path == "problem1":
                return [
                    {"name": "statement.md", "type": "file", "download_url": "http://mock/statement.md", "path": "problem1/statement.md"},
                    {"name": "tests", "type": "dir", "path": "problem1/tests"}
                ]
            if "tests" in path:
                return [
                    {"name": "0.in", "type": "file", "download_url": "http://mock/0.in", "path": f"{path}/0.in"},
                    {"name": "0.out", "type": "file", "download_url": "http://mock/0.out", "path": f"{path}/0.out"}
                ]
            return []
            
        mock_fetch.side_effect = side_effect_fetch
        
        # Mock file downloads
        def side_effect_dl(url):
            if "statement.md" in url:
                return b"# Mock Problem\nThis is a mock description."
            if "0.in" in url:
                return b"input data"
            if "0.out" in url:
                return b"output data"
            return b""
            
        mock_dl.side_effect = side_effect_dl
        
        yield mock_fetch, mock_dl

@pytest.mark.asyncio
async def test_github_ingestion_phase1(client, db_session, mock_github):
    """Test Phase 1: Problem description ingestion from GitHub."""
    repo_url = "https://github.com/mock/repo/tree/main/problem1"
    
    # Use the endpoint to trigger ingestion
    response = await client.post("/api/v1/problems/import/github", json={"url": repo_url})
    assert response.status_code == 201
    
    data = response.json()
    assert data["slug"] == "problem1"
    assert data["source_url"] == repo_url
    assert data["tests_downloaded"] is False
    assert data["test_count"] == 0
    
    # Check database
    problem = await db_session.get(Problem, data["id"])
    assert problem is not None
    assert "Mock Problem" in problem.description_md

@pytest.mark.asyncio
async def test_lazy_download_phase2(client, db_session, mock_github):
    """Test Phase 2: Lazy downloading of tests when a run is initiated."""
    # 1. Create a problem with source_url but no tests
    problem = Problem(
        name="Mock Problem",
        slug="mock-problem",
        description_md="Desc",
        source_url="https://github.com/mock/repo/tree/main/problem1",
        tests_downloaded=False,
        test_count=0
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)
    
    # 2. Create a run for this problem
    run_config = {
        "models": ["openrouter/free"],
        "judge_model": "openrouter/free",
        "prompts": {"system_prompt": "x", "round_1_prompt": "y"},
        "scoring": {"correctness_weight": 0.6, "speed_weight": 0.25, "memory_weight": 0.15, "penalty_wrong_answer": -0.5},
        "execution": {"timeout_buffer_ms": 100, "enable_cache_simulation": True, "measure_memory_peak": True}
    }
    run = Run(
        name="Lazy Test Run",
        problem_id=problem.id,
        config_json=json.dumps(run_config),
        status="configured"
    )
    db_session.add(run)
    await db_session.commit()
    await db_session.refresh(run)

    # 3. Mock the OrchestrationEngine internal calls to avoid real LLM/Sandbox usage
    # We want to test that sync_tests is called during _run_competition
    with mock.patch("backend.orchestrator.engine.OrchestrationEngine.run_round", return_value="Summary"), \
         mock.patch("backend.orchestrator.engine.get_session_maker", return_value=lambda: db_session):
        
        engine = OrchestrationEngine()
        # We need to bypass the background task and call _run_competition directly for testing
        # or just call start_run and wait
        await engine._run_competition(run.id, num_rounds=1)

        # 4. Verify tests were downloaded
        db_session.expire_all()
        problem = await db_session.get(Problem, problem.id)
        assert problem.tests_downloaded is True
        assert problem.test_count > 0
        
        # Verify files exist in test data dir
        from backend.api.problems import get_problem_tests_dir
        tests_dir = get_problem_tests_dir(problem.id)
        assert (tests_dir / "0.in").exists()
        assert (tests_dir / "0.out").exists()

@pytest.mark.asyncio
async def test_local_repo_ingestion(client, db_session, mock_local_repo):
    """Test ingestion from a local repository instead of GitHub API."""
    repo_url = "https://github.com/any/repo/tree/main/local-prob"
    
    # Trigger ingestion
    response = await client.post("/api/v1/problems/import/github", json={"url": repo_url})
    assert response.status_code == 201
    
    data = response.json()
    assert data["slug"] == "local-prob"
    
    # Check database
    problem = await db_session.get(Problem, data["id"])
    assert problem is not None
    assert "Description here." in problem.description_md
    
    # Since it's from local repo, tests should be synced immediately (as per my change in ingest_from_github)
    assert problem.tests_downloaded is True
    assert problem.test_count == 1
    
    from backend.api.problems import get_problem_tests_dir
    tests_dir = get_problem_tests_dir(problem.id)
    assert (tests_dir / "0.in").read_text() == "local in"
    assert (tests_dir / "0.out").read_text() == "local out"

@pytest.mark.asyncio
async def test_compile_and_run_model_solution(client, db_session, mock_local_repo):
    """
    Integration test:
    1. Ingest a problem from local repo.
    2. Compile a 'model solution' provided in the repo.
    3. Run benchmark against it.
    4. Verify it passes the tests.
    """
    from backend.sandbox.compiler import compile_solution
    from backend.sandbox.benchmark import benchmark_solution
    from backend.api.problems import get_problem_tests_dir
    
    # 1. Ingest problem
    repo_url = "https://github.com/any/repo/tree/main/local-prob"
    # Ensure there is a solution in the local prob dir
    prob_dir = mock_local_repo / "local-prob"
    sol_code = """
#include <iostream>
#include <string>
int main() {
    std::string s;
    std::getline(std::cin, s);
    if (s == "local in") std::cout << "local out" << std::endl;
    else std::cout << "wrong" << std::endl;
    return 0;
}
"""
    sol_path = prob_dir / "solution.cpp"
    sol_path.write_text(sol_code)
    
    response = await client.post("/api/v1/problems/import/github", json={"url": repo_url})
    assert response.status_code == 201
    problem_id = response.json()["id"]
    
    # 2. Compile solution
    # We use compile_solution directly for testing
    output_exe = str(prob_dir / "solution.exe")
    success, stdout, stderr, bin_path = await compile_solution(
        source_code=sol_code,
        compiler="g++-14",
        flags=["-O3"],
        output_path=output_exe,
        skip_safety_check=True
    )
    
    # Check if compilation was successful.
    # NOTE: In CI/Mock environments without g++, this might fail.
    # For the sake of this task, we assume environment has g++ or we mock it.
    if not success:
        pytest.skip(f"Compilation failed, likely no g++ in environment: {stderr}")

    # 3. Run benchmark
    tests_dir = get_problem_tests_dir(problem_id)
    # We need to create a Solution record first
    from backend.database.models import Solution, Round, Run
    import json
    
    run = Run(name="Test Run", problem_id=problem_id, config_json="{}", status="active")
    db_session.add(run)
    await db_session.commit()
    
    round_obj = Round(run_id=run.id, round_number=1, status="active")
    db_session.add(round_obj)
    await db_session.commit()
    
    solution = Solution(
        round_id=round_obj.id,
        model_slug="model-solution",
        source_code=sol_code,
        status="running"
    )
    db_session.add(solution)
    await db_session.commit()
    
    summary = await benchmark_solution(
        solution_id=solution.id,
        solution_binary_path=bin_path,
        test_cases_dir=str(tests_dir),
        db_session=db_session
    )
    
    # 4. Verify results
    assert summary.tests_passed == 1
    assert summary.all_passed is True
    
    # Check database was updated
    await db_session.refresh(solution)
    assert solution.tests_passed == 1
    assert solution.status == "completed"

@pytest.mark.asyncio
async def test_symlink_resolution(db_session, mock_repo_with_symlink):
    """Test that IOIIngestor correctly resolves symlink-like files."""
    ingestor = IOIIngestor(db_session)
    
    # The download_url will be "local://symlink-prob/tests/0.in"
    download_url = "local://symlink-prob/tests/0.in"
    
    content = await ingestor.download_file(download_url)
    assert content == b"target input content"

@pytest.mark.asyncio
async def test_sample_extraction(client, db_session, mock_local_repo):
    """Test that sample_input and sample_output are extracted and saved to Problem model."""
    repo_url = "https://github.com/any/repo/tree/main/local-prob"
    
    # Trigger ingestion
    response = await client.post("/api/v1/problems/import/github", json={"url": repo_url})
    assert response.status_code == 201
    
    problem_id = response.json()["id"]
    
    # Check database
    problem = await db_session.get(Problem, problem_id)
    assert problem.sample_input == "local in"
    assert problem.sample_output == "local out"

@pytest.mark.asyncio
async def test_pdf_parsing_cache(client, db_session, tmp_path):
    """Test that PDF parsing cache works as expected."""
    # Create a mock PDF file in a local repo
    repo_dir = tmp_path / "pdf_repo"
    repo_dir.mkdir()
    prob_dir = repo_dir / "pdf-prob"
    prob_dir.mkdir()
    
    pdf_content = b"fake pdf content"
    pdf_path = prob_dir / "statement.pdf"
    pdf_path.write_bytes(pdf_content)
    
    # Mock pymupdf4llm.to_markdown and sync_tests (since this test is only about PDF parsing)
    with mock.patch("backend.config.PROBLEMS_REPO_DIR", repo_dir), \
         mock.patch("backend.config.PROBLEMS_DIR", tmp_path), \
         mock.patch("pymupdf4llm.to_markdown", return_value="# Parsed Content") as mock_parse, \
         mock.patch("backend.services.problem_ingestion.IOIIngestor.sync_tests", return_value=0) as mock_sync:
        
        ingestor = IOIIngestor(db_session)
        repo_url = "https://github.com/any/repo/tree/main/pdf-prob"
        
        # 1. First ingestion - should call pymupdf4llm
        problem1 = await ingestor.ingest_from_github(repo_url)
        assert problem1.description_md == "# Parsed Content"
        assert mock_parse.call_count == 1
        
        # Check if cache file exists
        pdf_hash = hashlib.md5(pdf_content).hexdigest()
        cache_file = tmp_path / ".cache" / "pdf_markdown" / f"{pdf_hash}.md"
        assert cache_file.exists()
        assert cache_file.read_text(encoding="utf-8") == "# Parsed Content"
        
        # 2. Second ingestion - should use cache
        # We need to make sure it doesn't skip because it's "up to date"
        # Force sync by setting last_synced_at to something old
        problem1.last_synced_at = None
        await db_session.commit()
        
        problem2 = await ingestor.ingest_from_github(repo_url, force_sync=True)
        assert problem2.description_md == "# Parsed Content"
        assert mock_parse.call_count == 1  # Should still be 1
