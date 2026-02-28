import pytest
import zipfile
import io
import os
import shutil
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock
from backend import config
from backend.database.models import Problem
from backend.api.problems import get_problem_tests_dir, count_test_cases, list_test_cases

# =============================================================================
# Helpers
# =============================================================================

def create_zip_file(files_dict):
    """Create an in-memory ZIP file from a dictionary of filename: content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for filename, content in files_dict.items():
            zf.writestr(filename, content)
    buf.seek(0)
    return buf

# =============================================================================
# Helper Function Tests
# =============================================================================

def test_get_problem_tests_dir():
    problem_id = 123
    expected = config.PROBLEMS_DIR / str(problem_id) / "tests"
    assert get_problem_tests_dir(problem_id) == expected

def test_count_test_cases(tmp_path):
    # Empty directory
    assert count_test_cases(tmp_path / "nonexistent") == 0
    
    # Create some test files
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "001.in").write_text("input1")
    (tmp_path / "001.out").write_text("output1")
    (tmp_path / "002.in").write_text("input2")
    # Missing 002.out
    (tmp_path / "003.out").write_text("output3")
    # Missing 003.in
    
    assert count_test_cases(tmp_path) == 1  # Only 001.in/out pair exists

def test_list_test_cases(tmp_path):
    # Empty directory
    assert list_test_cases(tmp_path / "nonexistent") == []
    
    # Create some files
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "001.in").write_text("in1")
    (tmp_path / "001.out").write_text("out1")
    (tmp_path / "readme.txt").write_text("not a test")
    
    tests = list_test_cases(tmp_path)
    assert len(tests) == 2
    assert tests[0].filename == "001.in"
    assert tests[1].filename == "001.out"

# =============================================================================
# API Endpoint Tests
# =============================================================================

@pytest.mark.asyncio
async def test_get_problem_test_file_invalid_filename(client):
    # The handler checks for ".." in filename.
    response = await client.get("/api/v1/problems/1/tests/some..file")
    assert response.status_code == 400
    assert "Invalid filename" in response.json()["detail"]

@pytest.mark.asyncio
async def test_get_problem_test_file_not_found(client, db_session):
    # Create a problem first
    problem = Problem(name="P1", slug="p1", description_md="D1")
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    response = await client.get(f"/api/v1/problems/{problem.id}/tests/missing.in")
    assert response.status_code == 404
    assert "Test file not found" in response.json()["detail"]

@pytest.mark.asyncio
async def test_get_problem_test_file_success(client, db_session, tmp_path):
    # Create a problem
    problem = Problem(name="P2", slug="p2", description_md="D2")
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    # Manually create a test file in the expected directory
    tests_dir = get_problem_tests_dir(problem.id)
    tests_dir.mkdir(parents=True, exist_ok=True)
    test_file = tests_dir / "001.in"
    test_file.write_text("sample input")

    response = await client.get(f"/api/v1/problems/{problem.id}/tests/001.in")
    assert response.status_code == 200
    assert response.text == "sample input"

@pytest.mark.asyncio
async def test_parse_metadata_endpoint(client, db_session):
    with patch("backend.services.metadata_extraction.parse_all_problems_metadata") as mock_parse:
        payload = {
            "model": "gpt-4",
            "prompt": "Extract metadata",
            "fallback_model": "gpt-3.5-turbo"
        }
        response = await client.post("/api/v1/problems/parse-metadata", json=payload)
        assert response.status_code == 200
        assert "Metadata parsing started" in response.json()["message"]

@pytest.mark.asyncio
async def test_upload_test_cases_not_found(client):
    zip_data = create_zip_file({"001.in": "in", "001.out": "out"})
    files = {"file": ("tests.zip", zip_data, "application/zip")}
    response = await client.post("/api/v1/problems/999/tests", files=files)
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_upload_test_cases_invalid_format(client, db_session):
    problem = Problem(name="P3", slug="p3", description_md="D3")
    db_session.add(problem)
    await db_session.commit()
    
    files = {"file": ("tests.txt", b"not a zip", "text/plain")}
    response = await client.post(f"/api/v1/problems/{problem.id}/tests", files=files)
    assert response.status_code == 400
    assert "File must be a ZIP archive" in response.json()["detail"]

@pytest.mark.asyncio
async def test_upload_test_cases_success(client, db_session):
    problem = Problem(name="P4", slug="p4", description_md="D4")
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    zip_data = create_zip_file({"001.in": "in1", "001.out": "out1", "002.in": "in2"})
    files = {"file": ("tests.zip", zip_data, "application/zip")}
    
    response = await client.post(f"/api/v1/problems/{problem.id}/tests", files=files)
    assert response.status_code == 201
    assert response.json()["test_count"] == 1 # Only one complete pair
    
    # Verify DB update
    await db_session.refresh(problem)
    assert problem.test_count == 1
    
    # Verify files on disk
    tests_dir = get_problem_tests_dir(problem.id)
    assert (tests_dir / "001.in").exists()
    assert (tests_dir / "001.out").exists()
    assert (tests_dir / "002.in").exists()
    assert not (tests_dir / "temp_upload.zip").exists()

@pytest.mark.asyncio
async def test_upload_test_cases_zip_bomb(client, db_session):
    problem = Problem(name="P5", slug="p5", description_md="D5")
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    # Mock the ZipFile in the problems module
    with patch("backend.api.problems.zipfile.ZipFile") as mock_zipfile:
        mock_instance = MagicMock()
        mock_zipfile.return_value.__enter__.return_value = mock_instance
        
        mock_member = MagicMock()
        mock_member.file_size = 200 * 1024 * 1024 # 200 MB
        mock_member.filename = "huge.txt"
        mock_instance.infolist.return_value = [mock_member]
        
        zip_data = create_zip_file({"huge.txt": "small content"}).getvalue()
        files = {"file": ("tests.zip", zip_data, "application/zip")}
        response = await client.post(f"/api/v1/problems/{problem.id}/tests", files=files)
        
        assert response.status_code == 400
        assert "Potential Zip Bomb detected" in response.json()["detail"]

@pytest.mark.asyncio
async def test_upload_test_cases_zip_slip(client, db_session):
    problem = Problem(name="P6", slug="p6", description_md="D6")
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    zip_data = create_zip_file({"../../etc/passwd": "malicious"})
    files = {"file": ("tests.zip", zip_data, "application/zip")}
    
    response = await client.post(f"/api/v1/problems/{problem.id}/tests", files=files)
    assert response.status_code == 400
    assert "Path traversal attempt detected" in response.json()["detail"]

@pytest.mark.asyncio
async def test_list_test_cases_endpoint(client, db_session):
    problem = Problem(name="P7", slug="p7", description_md="D7", test_count=2)
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    tests_dir = get_problem_tests_dir(problem.id)
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "001.in").write_text("in")
    (tests_dir / "001.out").write_text("out")

    response = await client.get(f"/api/v1/problems/{problem.id}/tests")
    assert response.status_code == 200
    data = response.json()
    assert data["problem_id"] == problem.id
    assert data["test_count"] == 2 # From DB
    assert len(data["tests"]) == 2 # Actual files

@pytest.mark.asyncio
async def test_list_test_cases_not_found(client):
    response = await client.get("/api/v1/problems/999/tests")
    assert response.status_code == 404

@pytest.mark.asyncio
async def test_delete_problem_success(client, db_session):
    problem = Problem(name="P8", slug="p8", description_md="D8")
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    problem_dir = config.PROBLEMS_DIR / str(problem.id)
    problem_dir.mkdir(parents=True, exist_ok=True)
    (problem_dir / "something.txt").write_text("content")

    response = await client.delete(f"/api/v1/problems/{problem.id}")
    assert response.status_code == 204
    
    # Verify DB
    p = await db_session.get(Problem, problem.id)
    assert p is None
    
    # Verify disk
    assert not problem_dir.exists()

@pytest.mark.asyncio
async def test_import_problems_apps(client):
    with patch("backend.services.problem_ingestion.ingest_batch") as mock_ingest:
        # Invalid range
        payload = {"start_id": 100, "end_id": 50}
        response = await client.post("/api/v1/problems/import", json=payload)
        assert response.status_code == 400
        assert "end_id must be greater than or equal to start_id" in response.json()["detail"]

        # Too many
        payload = {"start_id": 0, "end_id": 200}
        response = await client.post("/api/v1/problems/import", json=payload)
        assert response.status_code == 400
        assert "Maximum 100 problems" in response.json()["detail"]

        # Success
        payload = {"start_id": 0, "end_id": 10}
        response = await client.post("/api/v1/problems/import", json=payload)
        assert response.status_code == 202
        assert "Import of 11 problems started" in response.json()["message"]

@pytest.mark.asyncio
async def test_import_problem_from_github_success(client, db_session):
    # Use a real problem to satisfy the response model
    problem = Problem(
        name="GH Prob", slug="gh-prob", description_md="GH",
        time_limit_ms=2000, memory_limit_mb=256, scoring_mode="binary",
        test_count=0
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    with patch("backend.services.problem_ingestion.IOIIngestor.ingest_from_github") as mock_ingest:
        mock_ingest.return_value = problem
        
        payload = {"url": "https://github.com/user/repo", "download_tests": True}
        response = await client.post("/api/v1/problems/import/github", json=payload)
        
        assert response.status_code == 201
        assert response.json()["name"] == "GH Prob"

@pytest.mark.asyncio
async def test_import_problem_from_github_failure(client):
    with patch("backend.services.problem_ingestion.IOIIngestor.ingest_from_github") as mock_ingest:
        mock_ingest.return_value = None
        
        payload = {"url": "https://github.com/user/repo"}
        response = await client.post("/api/v1/problems/import/github", json=payload)
        
        assert response.status_code == 400
        assert "Failed to import problem from GitHub" in response.json()["detail"]

@pytest.mark.asyncio
async def test_sync_github_tests_success(client, db_session):
    url = "https://github.com/owner/repo/tree/main/path"
    problem = Problem(name="P9", slug="p9", description_md="D9", source_url=url)
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    with patch("backend.services.problem_ingestion.IOIIngestor.sync_tests") as mock_sync:
        response = await client.post(f"/api/v1/problems/{problem.id}/tests/sync-github")
        assert response.status_code == 200
        assert "Test synchronization for problem" in response.json()["message"]

@pytest.mark.asyncio
async def test_sync_github_tests_no_source_url(client, db_session):
    problem = Problem(name="P10", slug="p10", description_md="D10", source_url=None)
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    response = await client.post(f"/api/v1/problems/{problem.id}/tests/sync-github")
    assert response.status_code == 400
    assert "was not imported from GitHub" in response.json()["detail"]

@pytest.mark.asyncio
async def test_sync_github_tests_not_found(client):
    response = await client.post("/api/v1/problems/999/tests/sync-github")
    assert response.status_code == 404
