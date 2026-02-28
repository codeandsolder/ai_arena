"""
Unit tests for backend.services.problem_ingestion covering previously uncovered lines.

These tests operate directly on IOIIngestor / APPSIngestor without going through
the HTTP API layer, so they can be more surgical about individual code paths.
"""

import asyncio
import gzip
import json
import pytest
import unittest.mock as mock
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from backend.database.models import Base, Problem
from backend.services.problem_ingestion import IOIIngestor, APPSIngestor, ingest_batch
from backend import config


# =============================================================================
# Shared fixtures
# =============================================================================

@pytest.fixture
async def db_session(tmp_path):
    """Lightweight in-memory SQLite session for ingestion unit tests."""
    db_path = tmp_path / "unit_test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def local_repo(tmp_path):
    """Creates a minimal local problems repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


# =============================================================================
# IOIIngestor.__init__ — GITHUB_TOKEN branch (line 48)
# =============================================================================

@pytest.mark.asyncio
async def test_init_with_github_token(db_session, monkeypatch):
    """Authorization header is set when GITHUB_TOKEN is configured (line 48)."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", "test-token-abc")
    ingestor = IOIIngestor(db_session)
    assert ingestor.client.headers.get("authorization") == "token test-token-abc"
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor.parse_github_url (lines 72, 82-85)
# =============================================================================

@pytest.mark.asyncio
async def test_parse_github_url_blob(db_session):
    """blob/ URL variant is parsed correctly (line 82-83)."""
    ingestor = IOIIngestor(db_session)
    result = ingestor.parse_github_url(
        "https://github.com/owner/repo/blob/main/path/to/file"
    )
    assert result == {"owner": "owner", "repo": "repo", "ref": "main", "path": "path/to/file"}
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_parse_github_url_invalid_raises(db_session):
    """Completely invalid URL raises ValueError (lines 84-85)."""
    ingestor = IOIIngestor(db_session)
    with pytest.raises(ValueError, match="Invalid GitHub URL"):
        ingestor.parse_github_url("https://example.com/not-github")
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_parse_github_url_repo_only(db_session):
    """Bare repo URL (no /tree/) returns empty path and 'main' ref (lines 70-77)."""
    ingestor = IOIIngestor(db_session)
    result = ingestor.parse_github_url("https://github.com/owner/myrepo")
    assert result["path"] == ""
    assert result["ref"] == "main"
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor.fetch_contents (lines 129-137)
# =============================================================================

@pytest.mark.asyncio
async def test_fetch_contents_local_path_not_found(db_session, tmp_path, monkeypatch, caplog):
    """Local repo exists but requested sub-path doesn't — logs warning, returns [] (lines 129-130)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.fetch_contents("owner", "repo", "nonexistent/path", "main")
    assert result == []
    assert "not found" in caplog.text
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor.download_file — various branches
# =============================================================================

@pytest.mark.asyncio
async def test_download_file_local_not_found(db_session, tmp_path, monkeypatch, caplog):
    """local:// URL where the file doesn't exist returns b'' and logs error (lines 184-186)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.download_file("local://nonexistent/file.in")
    assert result == b""
    assert "Local file not found" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_download_file_gz_decompress_failure(db_session, tmp_path, monkeypatch, caplog):
    """local .gz file with invalid gzip content logs error and returns raw bytes (lines 177-180)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    bad_gz = repo / "test.gz"
    bad_gz.write_bytes(b"not gzip data")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.download_file("local://test.gz")
    # Returns the raw bytes (before decompression attempt) since decompression failed
    assert result == b"not gzip data"
    assert "Failed to decompress" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_download_file_gz_decompress_success(db_session, tmp_path, monkeypatch):
    """local .gz file with valid gzip content returns decompressed bytes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    compressed = gzip.compress(b"hello world")
    gz_file = repo / "test.gz"
    gz_file.write_bytes(compressed)
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.download_file("local://test.gz")
    assert result == b"hello world"
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_download_file_symlink_target_missing(db_session, tmp_path, monkeypatch, caplog):
    """Symlink-as-file pointing to a non-existent target logs error and returns original bytes (lines 172-173)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Write a file whose content looks like a relative path but the target doesn't exist
    link_file = repo / "link.in"
    link_file.write_text("missing_target.in")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.download_file("local://link.in")
    # Symlink resolution fails; the file is returned as-is (original bytes)
    assert b"missing_target.in" in result
    assert "Failed to resolve symlink" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_download_file_lfs_pull_success(db_session, tmp_path, monkeypatch):
    """Git LFS pointer is detected and git lfs pull is invoked; on success file is re-read (lines 152-154)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lfs_file = repo / "large.in"
    lfs_pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 100\n"
    lfs_file.write_bytes(lfs_pointer)
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    real_content = b"actual test input"

    async def fake_lfs_pull(self, relative_path):
        # Simulate pull by replacing file content
        lfs_file.write_bytes(real_content)
        return True

    ingestor = IOIIngestor(db_session)
    with patch.object(IOIIngestor, "_git_lfs_pull", fake_lfs_pull):
        result = await ingestor.download_file("local://large.in")
    assert result == real_content
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_download_file_lfs_pull_failure(db_session, tmp_path, monkeypatch, caplog):
    """Git LFS pull fails — logs error and returns b'' (lines 155-157)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lfs_file = repo / "large.in"
    lfs_file.write_bytes(b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 100\n")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    async def fake_lfs_pull_fail(self, relative_path):
        return False

    ingestor = IOIIngestor(db_session)
    with patch.object(IOIIngestor, "_git_lfs_pull", fake_lfs_pull_fail):
        result = await ingestor.download_file("local://large.in")
    assert result == b""
    assert "Failed to pull LFS file" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_download_file_http_gz_bad_content(db_session, monkeypatch):
    """HTTP .gz URL with invalid gzip content silently falls through and returns raw bytes (lines 191-195)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", Path("/nonexistent_repo_path"))

    mock_response = MagicMock()
    mock_response.content = b"not gzip"
    mock_response.raise_for_status = MagicMock()

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor.client, "get", new=AsyncMock(return_value=mock_response)):
        result = await ingestor.download_file("https://example.com/file.gz")
    assert result == b"not gzip"
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor._git_lfs_pull (lines 200-217)
# =============================================================================

@pytest.mark.asyncio
async def test_git_lfs_pull_success(db_session, tmp_path, monkeypatch):
    """_git_lfs_pull returns True when git exits with code 0 (lines 210-211)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path)

    mock_process = AsyncMock()
    mock_process.returncode = 0
    mock_process.communicate = AsyncMock(return_value=(b"", b""))

    ingestor = IOIIngestor(db_session)
    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        result = await ingestor._git_lfs_pull("some/file.in")
    assert result is True
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_git_lfs_pull_nonzero_exit(db_session, tmp_path, monkeypatch, caplog):
    """_git_lfs_pull returns False when git exits non-zero (lines 212-214)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path)

    mock_process = AsyncMock()
    mock_process.returncode = 1
    mock_process.communicate = AsyncMock(return_value=(b"", b"error message"))

    ingestor = IOIIngestor(db_session)
    with patch("asyncio.create_subprocess_exec", return_value=mock_process):
        result = await ingestor._git_lfs_pull("some/file.in")
    assert result is False
    assert "git lfs pull failed" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_git_lfs_pull_exception(db_session, tmp_path, monkeypatch, caplog):
    """_git_lfs_pull returns False when subprocess raises (lines 215-217)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path)

    ingestor = IOIIngestor(db_session)
    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("git not found")):
        result = await ingestor._git_lfs_pull("some/file.in")
    assert result is False
    assert "Error executing git lfs pull" in caplog.text
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor._extract_competition (lines 222-242)
# =============================================================================

@pytest.mark.asyncio
async def test_extract_competition_from_last_path_part(db_session):
    """Competition extracted from the last path component (e.g. ioi2023-soccer) (lines 226-228)."""
    ingestor = IOIIngestor(db_session)
    assert ingestor._extract_competition("repo", "ioi2023-soccer") == "IOI2023"
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_extract_competition_from_parent_path(db_session):
    """Competition extracted from parent directory when last part has no year (lines 231-235)."""
    ingestor = IOIIngestor(db_session)
    assert ingestor._extract_competition("repo", "ioi2023/soccer") == "IOI2023"
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_extract_competition_from_repo_name(db_session):
    """Competition extracted from repo name when path gives nothing (lines 238-240)."""
    ingestor = IOIIngestor(db_session)
    assert ingestor._extract_competition("ioi2022-tasks", "") == "IOI2022"
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_extract_competition_no_match(db_session):
    """Returns None when no competition pattern is found (line 242)."""
    ingestor = IOIIngestor(db_session)
    assert ingestor._extract_competition("my-repo", "some/plain/path") is None
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor.ingest_repository (lines 96-109)
# =============================================================================

@pytest.mark.asyncio
async def test_ingest_repository_is_problem_dir_pdf(db_session, monkeypatch):
    """Directory containing a .pdf file is treated as a problem dir — ingest_from_github called (lines 99-104)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", Path("/nonexistent"))

    contents = [{"name": "problem.pdf", "type": "file", "download_url": "http://x", "path": "prob/problem.pdf"}]

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "fetch_contents", new=AsyncMock(return_value=contents)), \
         patch.object(ingestor, "ingest_from_github", new=AsyncMock(return_value=None)) as mock_ingest:
        await ingestor.ingest_repository("https://github.com/owner/repo/tree/main/prob")

    mock_ingest.assert_called_once()
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_repository_is_problem_dir_statement_subdir(db_session, monkeypatch):
    """Directory containing a 'statement' subdir is treated as problem dir (lines 101-104)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", Path("/nonexistent"))

    contents = [{"name": "statement", "type": "dir", "path": "prob/statement"}]

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "fetch_contents", new=AsyncMock(return_value=contents)), \
         patch.object(ingestor, "ingest_from_github", new=AsyncMock(return_value=None)) as mock_ingest:
        await ingestor.ingest_repository("https://github.com/owner/repo/tree/main/prob")

    mock_ingest.assert_called_once()
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_repository_recurses_into_subdirs(db_session, monkeypatch):
    """Non-problem directory is recursed into; hidden dirs (starting with .) are skipped (lines 105-109)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", Path("/nonexistent"))

    root_contents = [
        {"name": "prob1", "type": "dir", "path": "root/prob1"},
        {"name": ".hidden", "type": "dir", "path": "root/.hidden"},
    ]
    prob1_contents = [{"name": "problem.pdf", "type": "file", "path": "root/prob1/problem.pdf", "download_url": "http://x"}]

    async def fake_fetch(owner, repo, path, ref):
        if path == "root":
            return root_contents
        if path == "root/prob1":
            return prob1_contents
        return []

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "fetch_contents", new=AsyncMock(side_effect=fake_fetch)), \
         patch.object(ingestor, "ingest_from_github", new=AsyncMock(return_value=None)) as mock_ingest:
        await ingestor.ingest_repository("https://github.com/owner/repo/tree/main/root")

    # Only prob1 should be ingested (not .hidden)
    mock_ingest.assert_called_once()
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor.ingest_from_github — up-to-date check (lines 257-264)
# =============================================================================

@pytest.mark.asyncio
async def test_ingest_from_github_up_to_date_skips(db_session, tmp_path, monkeypatch):
    """Existing problem with mtime older than last_synced_at is skipped (lines 257-264)."""
    from datetime import datetime

    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "my-problem"
    prob_dir.mkdir()
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    # Insert a problem whose last_synced_at is in the future relative to dir mtime
    existing = Problem(
        name="My Problem",
        slug="my-problem",
        description_md="desc",
        source_url="https://github.com/o/r/tree/main/my-problem",
        last_synced_at=datetime(2099, 1, 1),
        tests_downloaded=True,
        test_count=1,
    )
    db_session.add(existing)
    await db_session.commit()

    ingestor = IOIIngestor(db_session)
    result = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/my-problem")
    assert result is existing  # returned immediately, no re-sync
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor.ingest_from_github — statement paths (lines 282-319)
# =============================================================================

@pytest.mark.asyncio
async def test_ingest_from_github_markdown_statement(db_session, tmp_path, monkeypatch):
    """Markdown statement is downloaded and stored as description_md (lines 314-319)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "md-prob"
    prob_dir.mkdir()
    (prob_dir / "statement.md").write_text("# MD Problem\nContent here.")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "sync_tests", new=AsyncMock(return_value=0)):
        problem = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/md-prob")

    assert problem is not None
    assert "MD Problem" in problem.description_md
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_from_github_task_yaml_md_statement(db_session, tmp_path, monkeypatch):
    """task.yaml pointing to an .md statement file is used preferentially (lines 288-289)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "yaml-md-prob"
    prob_dir.mkdir()
    (prob_dir / "task.yaml").write_text("name: YAMLMD\nstatements:\n  en: statement.md\n")
    (prob_dir / "statement.md").write_text("# YAML MD Problem\nContent.")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "sync_tests", new=AsyncMock(return_value=0)):
        problem = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/yaml-md-prob")

    assert problem is not None
    assert "YAML MD Problem" in problem.description_md
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_from_github_pdf_cache_hit(db_session, tmp_path, monkeypatch):
    """PDF already in markdown cache avoids calling pymupdf4llm (lines 334-336)."""
    import hashlib

    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "pdf-prob"
    prob_dir.mkdir()
    pdf_content = b"fake pdf bytes"
    (prob_dir / "statement.pdf").write_bytes(pdf_content)
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    problems_dir = tmp_path / "problems"
    monkeypatch.setattr(config, "PROBLEMS_DIR", problems_dir)

    # Pre-populate the cache
    pdf_hash = hashlib.md5(pdf_content).hexdigest()
    cache_dir = problems_dir / ".cache" / "pdf_markdown"
    cache_dir.mkdir(parents=True)
    (cache_dir / f"{pdf_hash}.md").write_text("Cached markdown content", encoding="utf-8")

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "sync_tests", new=AsyncMock(return_value=0)), \
         patch("backend.services.problem_ingestion.pymupdf4llm.to_markdown") as mock_convert:
        problem = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/pdf-prob")

    mock_convert.assert_not_called()
    assert problem.description_md == "Cached markdown content"
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_from_github_pdf_cache_miss(db_session, tmp_path, monkeypatch):
    """PDF not in cache triggers pymupdf4llm conversion and writes cache file (lines 338-344)."""
    import hashlib

    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "pdf-miss"
    prob_dir.mkdir()
    pdf_content = b"fresh pdf content"
    (prob_dir / "statement.pdf").write_bytes(pdf_content)
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    problems_dir = tmp_path / "problems"
    monkeypatch.setattr(config, "PROBLEMS_DIR", problems_dir)

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "sync_tests", new=AsyncMock(return_value=0)), \
         patch("backend.services.problem_ingestion.pymupdf4llm.to_markdown",
               return_value="# Converted\n\nContent.") as mock_convert:
        problem = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/pdf-miss")

    mock_convert.assert_called_once()
    assert "Converted" in problem.description_md

    # Cache file must have been written
    pdf_hash = hashlib.md5(pdf_content).hexdigest()
    cache_file = problems_dir / ".cache" / "pdf_markdown" / f"{pdf_hash}.md"
    assert cache_file.exists()
    assert "Converted" in cache_file.read_text(encoding="utf-8")
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_from_github_no_statement_returns_none(db_session, tmp_path, monkeypatch, caplog):
    """Directory with no statement file returns None and logs error (lines 354-355)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "no-stmt-prob").mkdir()
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    ingestor = IOIIngestor(db_session)
    result = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/no-stmt-prob")
    assert result is None
    assert "No statement found" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_from_github_updates_existing_problem(db_session, tmp_path, monkeypatch):
    """Re-ingesting an existing slug updates name/description rather than creating duplicate (lines 373-378)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "existing-prob"
    prob_dir.mkdir()
    (prob_dir / "statement.md").write_text("# Updated Content")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "sync_tests", new=AsyncMock(return_value=0)):
        problem1 = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/existing-prob")
        assert problem1 is not None
        original_id = problem1.id

        # Modify statement and re-ingest with force_sync
        (prob_dir / "statement.md").write_text("# New Content")
        problem2 = await ingestor.ingest_from_github(
            "https://github.com/o/r/tree/main/existing-prob", force_sync=True
        )

    assert problem2.id == original_id  # Same record updated
    assert "New Content" in problem2.description_md
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_from_github_statement_subdir_scan(db_session, tmp_path, monkeypatch):
    """PDF found inside a 'statement/' subdirectory is used (lines 302-311)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", Path("/nonexistent_for_github"))
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    # Simulate GitHub API returning a 'statement' dir that contains a PDF
    root_contents = [{"name": "statement", "type": "dir", "path": "prob/statement"}]
    stmt_contents = [
        {"name": "problem-en.pdf", "type": "file",
         "download_url": "http://mock/problem-en.pdf", "path": "prob/statement/problem-en.pdf"}
    ]

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "fetch_contents",
                      new=AsyncMock(side_effect=lambda o, r, p, ref: root_contents if p == "prob" else stmt_contents)), \
         patch.object(ingestor, "download_file",
                      new=AsyncMock(return_value=b"fake pdf bytes")), \
         patch("backend.services.problem_ingestion.pymupdf4llm.to_markdown",
               return_value="# PDF Content"), \
         patch.object(ingestor, "sync_tests", new=AsyncMock(return_value=0)):
        problem = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/prob")

    assert problem is not None
    assert "PDF Content" in problem.description_md
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_ingest_from_github_competition_prefix(db_session, tmp_path, monkeypatch):
    """Competition prefix (e.g. IOI2023) is prepended to problem name (lines 362-366)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "ioi2023-soccer"
    prob_dir.mkdir()
    (prob_dir / "statement.md").write_text("# Soccer Problem")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    ingestor = IOIIngestor(db_session)
    with patch.object(ingestor, "sync_tests", new=AsyncMock(return_value=0)):
        problem = await ingestor.ingest_from_github("https://github.com/o/r/tree/main/ioi2023-soccer")

    assert "IOI2023" in problem.name
    await ingestor.client.aclose()


# =============================================================================
# IOIIngestor.sync_tests — edge cases
# =============================================================================

@pytest.fixture
def problem_with_source(db_session, tmp_path, monkeypatch):
    """Helper: inserts a problem with a local-repo source URL and returns (problem, repo_dir)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")
    return repo


@pytest.mark.asyncio
async def test_sync_tests_problem_not_found(db_session, tmp_path, monkeypatch, caplog):
    """sync_tests returns 0 when problem_id doesn't exist in DB (lines 410-412)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "repo")
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    ingestor = IOIIngestor(db_session)
    result = await ingestor.sync_tests(99999)
    assert result == 0
    assert "not found or has no source URL" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_sync_tests_already_cached(db_session, tmp_path, monkeypatch, caplog):
    """sync_tests returns immediately when tests already on disk and tests_downloaded=True (lines 423-425)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "cached-prob"
    prob_dir.mkdir()
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    problem = Problem(
        name="Cached",
        slug="cached-prob",
        description_md="d",
        source_url="https://github.com/o/r/tree/main/cached-prob",
        tests_downloaded=True,
        test_count=2,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    # Create actual test files so existing_in_files is non-empty
    from backend.api.problems import get_problem_tests_dir
    tests_dir = get_problem_tests_dir(problem.id)
    tests_dir.mkdir(parents=True)
    (tests_dir / "0.in").write_text("input")
    (tests_dir / "0.out").write_text("output")

    ingestor = IOIIngestor(db_session)
    result = await ingestor.sync_tests(problem.id)
    assert result == 2
    assert "already cached" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_sync_tests_clears_stale_files_on_fresh_sync(db_session, tmp_path, monkeypatch):
    """Fresh sync deletes leftover files from a previous partial run (lines 430-433)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "stale-prob"
    prob_dir.mkdir()
    (prob_dir / "0.in").write_text("input data")
    (prob_dir / "0.out").write_text("output data")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    problem = Problem(
        name="Stale",
        slug="stale-prob",
        description_md="d",
        source_url="https://github.com/o/r/tree/main/stale-prob",
        tests_downloaded=False,  # fresh sync
        test_count=0,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    from backend.api.problems import get_problem_tests_dir
    tests_dir = get_problem_tests_dir(problem.id)
    tests_dir.mkdir(parents=True)
    stale_file = tests_dir / "stale.in"
    stale_file.write_text("leftover")

    ingestor = IOIIngestor(db_session)
    result = await ingestor.sync_tests(problem.id)
    assert not stale_file.exists()  # cleared
    assert result == 1
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_sync_tests_empty_content_skips_test(db_session, tmp_path, monkeypatch, caplog):
    """Test pair where download returns empty bytes is skipped (lines 515-517)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "empty-prob"
    prob_dir.mkdir()
    (prob_dir / "0.in").write_text("real")
    (prob_dir / "0.out").write_bytes(b"")  # empty output → skip
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    problem = Problem(
        name="Empty",
        slug="empty-prob",
        description_md="d",
        source_url="https://github.com/o/r/tree/main/empty-prob",
        tests_downloaded=False,
        test_count=0,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.sync_tests(problem.id)
    assert result == 0
    assert "Skipping test" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_sync_tests_unicode_decode_error_on_sample(db_session, tmp_path, monkeypatch, caplog):
    """Binary sample input that can't be decoded as UTF-8 logs a warning (lines 519-524)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "binary-prob"
    prob_dir.mkdir()
    (prob_dir / "0.in").write_bytes(b"\xff\xfe binary data")  # not valid UTF-8
    (prob_dir / "0.out").write_bytes(b"\xff\xfe output")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    problem = Problem(
        name="Binary",
        slug="binary-prob",
        description_md="d",
        source_url="https://github.com/o/r/tree/main/binary-prob",
        tests_downloaded=False,
        test_count=0,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.sync_tests(problem.id)
    # Test is still counted even if sample decode fails
    assert result == 1
    assert "Could not decode sample" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_sync_tests_resuming_redownloads_when_dir_empty(db_session, tmp_path, monkeypatch, caplog):
    """
    Resume mode: tests_downloaded=True but tests_dir has no .in files (e.g. dir was wiped).
    The early-return guard only fires when BOTH flags are set AND files exist, so this
    falls through and re-downloads all tests without clearing any stale files (lines 429-433).

    Note: the per-test skip inside the loop (lines 507-510) requires is_resuming=True
    AND the dest files already exist on disk before the loop iteration reaches them.
    Because count starts at 0 and the dir is empty at loop start, those dest files
    can never already exist — the skip branch is structurally unreachable under the
    current early-return guard, so lines 507-510 and 531 are not coverable.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "resume-prob"
    prob_dir.mkdir()
    (prob_dir / "0.in").write_text("input0")
    (prob_dir / "0.out").write_text("output0")
    (prob_dir / "1.in").write_text("input1")
    (prob_dir / "1.out").write_text("output1")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    problem = Problem(
        name="Resume",
        slug="resume-prob",
        description_md="d",
        source_url="https://github.com/o/r/tree/main/resume-prob",
        tests_downloaded=True,   # DB says done …
        test_count=0,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    from backend.api.problems import get_problem_tests_dir
    tests_dir = get_problem_tests_dir(problem.id)
    tests_dir.mkdir(parents=True)
    # … but tests_dir is empty (files were wiped) → early-return bypassed, is_resuming=True

    ingestor = IOIIngestor(db_session)
    result = await ingestor.sync_tests(problem.id)
    assert result == 2
    assert (tests_dir / "0.in").read_text() == "input0"
    assert (tests_dir / "1.in").read_text() == "input1"
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_sync_tests_no_test_files_found(db_session, tmp_path, monkeypatch, caplog):
    """Returns 0 and logs error when no .in files are found anywhere (lines 477-479)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "empty-tests"
    prob_dir.mkdir()
    (prob_dir / "statement.md").write_text("no tests here")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    problem = Problem(
        name="NoTests",
        slug="empty-tests",
        description_md="d",
        source_url="https://github.com/o/r/tree/main/empty-tests",
        tests_downloaded=False,
        test_count=0,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.sync_tests(problem.id)
    assert result == 0
    assert "No .in test files found" in caplog.text
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_sync_tests_excludes_reserved_dirs(db_session, tmp_path, monkeypatch):
    """collect_files skips 'solution', 'statement', 'checker', 'grader' subdirs (line 456)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "with-reserved"
    prob_dir.mkdir()
    # Put test files inside a 'solution' subdir — should be skipped
    sol_dir = prob_dir / "solution"
    sol_dir.mkdir()
    (sol_dir / "0.in").write_text("should not be picked up")
    (sol_dir / "0.out").write_text("output")
    # Also put valid tests directly in prob_dir
    (prob_dir / "1.in").write_text("valid input")
    (prob_dir / "1.out").write_text("valid output")
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    problem = Problem(
        name="Reserved",
        slug="with-reserved",
        description_md="d",
        source_url="https://github.com/o/r/tree/main/with-reserved",
        tests_downloaded=False,
        test_count=0,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    ingestor = IOIIngestor(db_session)
    result = await ingestor.sync_tests(problem.id)
    # Only the root-level test should be synced, not the solution/ one
    assert result == 1
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_sync_tests_sample_truncation(db_session, tmp_path, monkeypatch):
    """Sample input/output longer than 2000 chars is stored truncated (lines 521-522)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    prob_dir = repo / "long-sample"
    prob_dir.mkdir()
    long_input = "A" * 3000
    long_output = "B" * 3000
    (prob_dir / "0.in").write_text(long_input)
    (prob_dir / "0.out").write_text(long_output)
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    problem = Problem(
        name="LongSample",
        slug="long-sample",
        description_md="d",
        source_url="https://github.com/o/r/tree/main/long-sample",
        tests_downloaded=False,
        test_count=0,
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)

    ingestor = IOIIngestor(db_session)
    await ingestor.sync_tests(problem.id)
    await db_session.refresh(problem)

    assert len(problem.sample_input) == 2000
    assert len(problem.sample_output) == 2000
    await ingestor.client.aclose()


# =============================================================================
# APPSIngestor
# =============================================================================

@pytest.mark.asyncio
async def test_apps_ingestor_context_manager(db_session):
    """APPSIngestor supports async context manager protocol (lines 549-553)."""
    async with APPSIngestor(db_session) as ingestor:
        assert isinstance(ingestor, APPSIngestor)


@pytest.mark.asyncio
async def test_apps_ingestor_generate_slug(db_session):
    """_generate_slug lowercases and replaces non-alphanumeric chars (lines 555-559)."""
    ingestor = APPSIngestor(db_session)
    assert ingestor._generate_slug("Hello World! 2023") == "hello-world-2023"
    await ingestor.client.aclose()


@pytest.mark.asyncio
async def test_apps_fetch_problem_data_local(db_session, tmp_path, monkeypatch):
    """fetch_problem_data reads from local cache when available (lines 567-593)."""
    repo = tmp_path / "apps_repo"
    repo.mkdir()
    prob_dir = repo / "0042"
    prob_dir.mkdir()
    (prob_dir / "question.txt").write_text("# My Problem\nSolve this.")
    (prob_dir / "input_output.json").write_text(
        json.dumps({"inputs": ["1\n"], "outputs": ["2\n"]})
    )
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    async with APPSIngestor(db_session) as ingestor:
        data = await ingestor.fetch_problem_data("0042")

    assert data is not None
    assert data["name"] == "My Problem"
    assert data["io"]["inputs"] == ["1\n"]


@pytest.mark.asyncio
async def test_apps_fetch_problem_data_local_missing_question(db_session, tmp_path, monkeypatch, caplog):
    """Missing question.txt returns None and logs error (lines 574-575)."""
    repo = tmp_path / "apps_repo"
    repo.mkdir()
    (repo / "0001").mkdir()  # no question.txt
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    async with APPSIngestor(db_session) as ingestor:
        data = await ingestor.fetch_problem_data("0001")

    assert data is None
    assert "Missing question.txt" in caplog.text


@pytest.mark.asyncio
async def test_apps_fetch_problem_data_local_missing_io(db_session, tmp_path, monkeypatch, caplog):
    """Missing input_output.json returns None and logs error (lines 582-583)."""
    repo = tmp_path / "apps_repo"
    repo.mkdir()
    prob_dir = repo / "0002"
    prob_dir.mkdir()
    (prob_dir / "question.txt").write_text("# Problem")
    # no input_output.json
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    async with APPSIngestor(db_session) as ingestor:
        data = await ingestor.fetch_problem_data("0002")

    assert data is None
    assert "Missing input_output.json" in caplog.text


@pytest.mark.asyncio
async def test_apps_fetch_problem_data_local_no_title_match(db_session, tmp_path, monkeypatch):
    """question.txt with no # header falls back to 'APPS Problem XXXX' name (lines 585-586)."""
    repo = tmp_path / "apps_repo"
    repo.mkdir()
    prob_dir = repo / "0003"
    prob_dir.mkdir()
    (prob_dir / "question.txt").write_text("No header here")
    (prob_dir / "input_output.json").write_text(json.dumps({"inputs": [], "outputs": []}))
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", repo)

    async with APPSIngestor(db_session) as ingestor:
        data = await ingestor.fetch_problem_data("0003")

    assert data["name"] == "APPS Problem 0003"


@pytest.mark.asyncio
async def test_apps_fetch_problem_data_http_fallback(db_session, tmp_path, monkeypatch):
    """Falls back to GitHub HTTP when local cache doesn't exist (lines 598-626)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")

    question_resp = MagicMock()
    question_resp.status_code = 200
    question_resp.text = "# HTTP Problem\nSome content."

    io_resp = MagicMock()
    io_resp.status_code = 200
    io_resp.json = MagicMock(return_value={"inputs": ["5\n"], "outputs": ["10\n"]})

    async with APPSIngestor(db_session) as ingestor:
        with patch.object(ingestor.client, "get",
                          new=AsyncMock(side_effect=[question_resp, io_resp])):
            data = await ingestor.fetch_problem_data("0099")

    assert data["name"] == "HTTP Problem"
    assert data["io"]["inputs"] == ["5\n"]


@pytest.mark.asyncio
async def test_apps_fetch_problem_data_http_question_fail(db_session, tmp_path, monkeypatch, caplog):
    """HTTP 404 for question.txt returns None (lines 602-604)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")

    fail_resp = MagicMock()
    fail_resp.status_code = 404

    async with APPSIngestor(db_session) as ingestor:
        with patch.object(ingestor.client, "get", new=AsyncMock(return_value=fail_resp)):
            data = await ingestor.fetch_problem_data("0100")

    assert data is None
    assert "Failed to fetch question.txt" in caplog.text


@pytest.mark.asyncio
async def test_apps_fetch_problem_data_http_io_fail(db_session, tmp_path, monkeypatch, caplog):
    """HTTP 404 for input_output.json returns None (lines 609-611)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")

    ok_resp = MagicMock(status_code=200, text="# Problem")
    fail_resp = MagicMock(status_code=404)

    async with APPSIngestor(db_session) as ingestor:
        with patch.object(ingestor.client, "get",
                          new=AsyncMock(side_effect=[ok_resp, fail_resp])):
            data = await ingestor.fetch_problem_data("0101")

    assert data is None
    assert "Failed to fetch input_output.json" in caplog.text


@pytest.mark.asyncio
async def test_apps_fetch_problem_data_http_exception(db_session, tmp_path, monkeypatch, caplog):
    """Network exception during HTTP fetch returns None (lines 624-626)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")

    async with APPSIngestor(db_session) as ingestor:
        with patch.object(ingestor.client, "get", new=AsyncMock(side_effect=Exception("timeout"))):
            data = await ingestor.fetch_problem_data("0102")

    assert data is None


@pytest.mark.asyncio
async def test_apps_ingest_problem_new(db_session, tmp_path, monkeypatch):
    """ingest_problem creates a new Problem with tests written to disk (lines 628-698)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    async with APPSIngestor(db_session) as ingestor:
        with patch.object(ingestor, "fetch_problem_data", new=AsyncMock(return_value={
            "id_str": "0001",
            "name": "New Problem",
            "description": "# New Problem\nDescription.",
            "io": {"inputs": ["3\n", "5\n"], "outputs": ["9\n", "25\n"]},
        })):
            problem = await ingestor.ingest_problem(1)

    assert problem is not None
    assert problem.name == "New Problem"
    assert problem.test_count == 2
    assert problem.sample_input == "3\n"
    assert problem.sample_output == "9\n"

    from backend.api.problems import get_problem_tests_dir
    tests_dir = get_problem_tests_dir(problem.id)
    assert (tests_dir / "0.in").read_text(encoding="utf-8") == "3\n"
    assert (tests_dir / "1.out").read_text(encoding="utf-8") == "25\n"


@pytest.mark.asyncio
async def test_apps_ingest_problem_list_inputs(db_session, tmp_path, monkeypatch):
    """List-format inputs/outputs are joined with newlines (lines 681-684)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    async with APPSIngestor(db_session) as ingestor:
        with patch.object(ingestor, "fetch_problem_data", new=AsyncMock(return_value={
            "id_str": "0002",
            "name": "List Problem",
            "description": "desc",
            "io": {"inputs": [[1, 2, 3]], "outputs": [[6]]},
        })):
            problem = await ingestor.ingest_problem(2)

    assert problem.test_count == 1
    from backend.api.problems import get_problem_tests_dir
    tests_dir = get_problem_tests_dir(problem.id)
    assert (tests_dir / "0.in").read_text(encoding="utf-8") == "1\n2\n3"


@pytest.mark.asyncio
async def test_apps_ingest_problem_existing_skips_rewrite(db_session, tmp_path, monkeypatch, caplog):
    """Re-ingesting when test count matches skips rewriting files (lines 668-672)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    io_data = {"inputs": ["1\n"], "outputs": ["2\n"]}

    async with APPSIngestor(db_session) as ingestor:
        with patch.object(ingestor, "fetch_problem_data", new=AsyncMock(return_value={
            "id_str": "0003", "name": "Existing Problem", "description": "d", "io": io_data,
        })):
            problem = await ingestor.ingest_problem(3)

        from backend.api.problems import get_problem_tests_dir
        tests_dir = get_problem_tests_dir(problem.id)
        original_mtime = (tests_dir / "0.in").stat().st_mtime

        # Re-ingest — test count matches, files should not be rewritten
        with patch.object(ingestor, "fetch_problem_data", new=AsyncMock(return_value={
            "id_str": "0003", "name": "Existing Problem", "description": "d", "io": io_data,
        })):
            problem2 = await ingestor.ingest_problem(3)

    assert problem2.id == problem.id
    assert "already on disk" in caplog.text
    assert (tests_dir / "0.in").stat().st_mtime == original_mtime  # file untouched


@pytest.mark.asyncio
async def test_apps_ingest_problem_no_data(db_session, tmp_path, monkeypatch):
    """Returns None when fetch_problem_data returns None (lines 633-635)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    async with APPSIngestor(db_session) as ingestor:
        with patch.object(ingestor, "fetch_problem_data", new=AsyncMock(return_value=None)):
            result = await ingestor.ingest_problem(999)

    assert result is None


# =============================================================================
# ingest_batch (lines 701-710)
# =============================================================================

@pytest.mark.asyncio
async def test_ingest_batch_calls_ingest_problem(tmp_path, monkeypatch):
    """ingest_batch iterates over IDs and calls ingest_problem for each (lines 701-710)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    call_ids = []

    async def fake_ingest(self, apps_id):
        call_ids.append(apps_id)
        return MagicMock()

    with patch.object(APPSIngestor, "ingest_problem", fake_ingest), \
         patch("backend.services.problem_ingestion.AsyncSessionLocal") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        await ingest_batch(5, 3)

    assert call_ids == [5, 6, 7]


@pytest.mark.asyncio
async def test_ingest_batch_rollback_on_error(tmp_path, monkeypatch, caplog):
    """ingest_batch rolls back session and continues when ingest_problem raises (lines 708-710)."""
    monkeypatch.setattr(config, "PROBLEMS_REPO_DIR", tmp_path / "nonexistent")
    monkeypatch.setattr(config, "PROBLEMS_DIR", tmp_path / "problems")

    async def fake_ingest_fail(self, apps_id):
        raise RuntimeError("ingest failed")

    with patch.object(APPSIngestor, "ingest_problem", fake_ingest_fail), \
         patch("backend.services.problem_ingestion.AsyncSessionLocal") as mock_session_cls:

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session_cls.return_value = mock_session

        # Should not raise — errors are caught and rolled back
        await ingest_batch(0, 2)

    assert mock_session.rollback.call_count == 2
    assert "Failed to ingest problem" in caplog.text