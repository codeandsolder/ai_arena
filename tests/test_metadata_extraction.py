import json
import pytest
from contextlib import asynccontextmanager
from unittest.mock import patch, MagicMock, AsyncMock, ANY
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.metadata_extraction import MetadataExtractor, parse_all_problems_metadata
from backend.database.models import Problem
from backend.orchestrator.model_client import ModelClient

@pytest.fixture
def mock_model_client():
    return MagicMock(spec=ModelClient)


# =============================================================================
# MetadataExtractor._get_problem_dir
# =============================================================================

@pytest.mark.asyncio
async def test_get_problem_dir(mock_model_client, monkeypatch):
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", Path("/fake/dir"))
    extractor = MetadataExtractor(mock_model_client)
    assert extractor._get_problem_dir(42) == Path("/fake/dir/42")


# =============================================================================
# MetadataExtractor._read_file_content
# =============================================================================

def test_read_file_content_md(tmp_path, mock_model_client):
    extractor = MetadataExtractor(mock_model_client)
    md_file = tmp_path / "test.md"
    md_file.write_text("Markdown content")
    assert extractor._read_file_content(md_file) == "Markdown content"

def test_read_file_content_pdf(tmp_path, mock_model_client):
    extractor = MetadataExtractor(mock_model_client)
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"Fake PDF")

    with patch("backend.services.metadata_extraction.pymupdf4llm.to_markdown") as mock_to_md:
        mock_to_md.return_value = "Parsed PDF"
        assert extractor._read_file_content(pdf_file) == "Parsed PDF"

def test_read_file_content_pdf_error(tmp_path, mock_model_client, caplog):
    extractor = MetadataExtractor(mock_model_client)
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"Invalid PDF")

    with patch("backend.services.metadata_extraction.pymupdf4llm.to_markdown") as mock_to_md:
        mock_to_md.side_effect = Exception("PDF error")
        assert extractor._read_file_content(pdf_file) == ""
        assert "Error converting PDF" in caplog.text

def test_read_file_content_nonexistent(mock_model_client):
    extractor = MetadataExtractor(mock_model_client)
    assert extractor._read_file_content(Path("/nonexistent")) == ""

def test_read_file_content_non_pdf_extension(tmp_path, mock_model_client):
    """Non-PDF files (e.g. .txt) are read as plain text."""
    extractor = MetadataExtractor(mock_model_client)
    txt_file = tmp_path / "test.txt"
    txt_file.write_text("Text content")
    assert extractor._read_file_content(txt_file) == "Text content"


# =============================================================================
# MetadataExtractor._find_problem_files
# =============================================================================

@pytest.mark.parametrize(
    "files_present, expected_statement, expected_editorial",
    [
        # Known pattern names — picked up deterministically via the patterns list
        (["problem.md"],    "problem content",   ""),
        (["statement.pdf"], "parsed statement",  ""),
        (["editorial.md"],  "",                  "editorial content"),
        # solution.pdf matches both "solution.pdf" in editorial_patterns AND the
        # iterdir statement fallback (name has no "editorial"), so both are filled.
        (["solution.pdf"],  "parsed solution",   "parsed solution"),
        # editorial_random.pdf → iterdir editorial fallback ("editorial" in name)
        # random.md → iterdir statement fallback (no "editorial" in name)
        (["random.md", "editorial_random.pdf"], "random content", "parsed editorial"),
        # solution_random.md has "solution" in name → editorial fallback.
        # It also passes the statement iterdir filter (no "editorial" in name).
        # To make the statement winner deterministic we put it in a subdirectory
        # so only random.pdf remains at the top level for the statement scan.
        # See the custom test below for that case.
        ([], "", ""),
    ]
)
def test_find_problem_files(
    tmp_path, mock_model_client, files_present,
    expected_statement, expected_editorial, monkeypatch
):
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()

    content_map = {
        "problem.md":          "problem content",
        "statement.pdf":       "parsed statement",
        "editorial.md":        "editorial content",
        "solution.pdf":        "parsed solution",
        "random.md":           "random content",
        "random.pdf":          "random content",
        "editorial_random.pdf":"parsed editorial",
        "solution_random.md":  "solution content",
    }

    def mock_read(path):
        if path.suffix == ".pdf":
            return content_map.get(path.name, "")
        else:
            if path.exists():
                return path.read_text()
            return ""

    for file in files_present:
        path = problem_dir / file
        if path.suffix != ".pdf":
            path.write_text(content_map.get(file, ""))
        else:
            path.write_bytes(b"placeholder")

    with patch.object(extractor, "_read_file_content", side_effect=mock_read):
        statement, editorial = extractor._find_problem_files(1)
        assert statement == expected_statement
        assert editorial == expected_editorial


def test_find_problem_files_ambiguous_iterdir(tmp_path, mock_model_client, monkeypatch):
    """
    The statement iterdir fallback excludes only files with "editorial" in the name —
    files with "solution" in the name still pass the filter, so when multiple files
    match the statement is whichever iterdir yields first.

    We force a deterministic iteration order by patching iterdir on the specific
    problem_dir Path instance, then verify both the statement and editorial outcomes.
    """
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()

    plain_file = problem_dir / "plain.md"
    plain_file.write_text("plain content")
    sol_file = problem_dir / "solution_extra.md"
    sol_file.write_text("solution content")

    content_map = {
        "plain.md":          "plain content",
        "solution_extra.md": "solution content",
    }

    def mock_read(path):
        return content_map.get(path.name, "") if path.exists() else ""

    # Wrap _find_problem_files so that iterdir on the problem_dir specifically
    # returns files in sorted (alphabetical) order, making the test deterministic.
    original_find = extractor._find_problem_files.__func__
    original_iterdir = Path.iterdir

    def sorted_iterdir(self):
        results = list(original_iterdir(self))
        if self == problem_dir:
            results = sorted(results, key=lambda p: p.name)
        return iter(results)

    with patch.object(extractor, "_read_file_content", side_effect=mock_read), \
         patch.object(Path, "iterdir", sorted_iterdir):
        statement, editorial = extractor._find_problem_files(1)

    # Alphabetical order: plain.md < solution_extra.md
    # Statement iterdir scan: plain.md hits first ("editorial" not in name) → statement
    # Editorial iterdir scan: solution_extra.md ("solution" in name) → editorial
    assert statement == "plain content"
    assert editorial == "solution content"


def test_find_problem_files_with_task_yaml(tmp_path, mock_model_client, monkeypatch):
    """When task.yaml declares the statement, it takes priority over filename patterns."""
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()
    stmt_dir = problem_dir / "statement"
    stmt_dir.mkdir()
    stmt_file = stmt_dir / "problem-en.pdf"
    stmt_file.write_bytes(b"placeholder")

    task_yaml = problem_dir / "task.yaml"
    task_yaml.write_text("name: TEST\nstatements:\n  en: statement/problem-en.pdf\n")

    def mock_read(path):
        if path == stmt_file:
            return "yaml-guided statement content"
        return ""

    with patch.object(extractor, "_read_file_content", side_effect=mock_read):
        statement, editorial = extractor._find_problem_files(1)

    assert statement == "yaml-guided statement content"
    assert editorial == ""


def test_find_problem_files_nonexistent_dir(tmp_path, mock_model_client, monkeypatch):
    """Missing problem directory returns empty strings without raising."""
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)
    statement, editorial = extractor._find_problem_files(999)
    assert statement == ""
    assert editorial == ""


# =============================================================================
# MetadataExtractor.extract_metadata
# =============================================================================

@pytest.mark.asyncio
async def test_extract_metadata_success(mock_model_client):
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)

    mock_response = MagicMock()
    mock_response.response_text = '{"short_description": "Short desc", "tags": ["tag1", "tag2"]}'
    mock_model_client.call_model_with_retry = AsyncMock(
        return_value=(mock_response, {"short_description": "Short desc", "tags": ["tag1", "tag2"]})
    )

    with patch.object(extractor, "_find_problem_files", return_value=("statement", "editorial")):
        success = await extractor.extract_metadata(problem, "model", "prompt")
        assert success
        assert problem.short_description == "Short desc"
        assert problem.tags == json.dumps(["tag1", "tag2"])


@pytest.mark.asyncio
async def test_extract_metadata_no_statement(mock_model_client, caplog):
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)

    with patch.object(extractor, "_find_problem_files", return_value=("", "")):
        success = await extractor.extract_metadata(problem, "model", "prompt")
        assert not success
        assert "No statement found" in caplog.text


@pytest.mark.asyncio
async def test_extract_metadata_use_db_description(mock_model_client):
    """Falls back to description_md from the DB when no file is found."""
    problem = Problem(id=1, slug="test-slug", description_md="DB desc")
    extractor = MetadataExtractor(mock_model_client)

    mock_response = MagicMock()
    mock_response.response_text = '{"short_description": "Short", "tags": []}'
    mock_model_client.call_model_with_retry = AsyncMock(
        return_value=(mock_response, {"short_description": "Short", "tags": []})
    )

    with patch.object(extractor, "_find_problem_files", return_value=("", "")):
        success = await extractor.extract_metadata(problem, "model", "prompt")
        assert success
        assert problem.short_description == "Short"


@pytest.mark.asyncio
async def test_extract_metadata_llm_failure(mock_model_client, caplog):
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)

    mock_model_client.call_model_with_retry = AsyncMock(
        return_value=(MagicMock(response_text="invalid"), None)
    )

    with patch.object(extractor, "_find_problem_files", return_value=("statement", "")):
        success = await extractor.extract_metadata(problem, "model", "prompt")
        assert not success
        assert "LLM failed to provide required JSON structure" in caplog.text


@pytest.mark.asyncio
async def test_extract_metadata_exception(mock_model_client, caplog):
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)

    mock_model_client.call_model_with_retry = AsyncMock(side_effect=Exception("LLM error"))

    with patch.object(extractor, "_find_problem_files", return_value=("statement", "")):
        success = await extractor.extract_metadata(problem, "model", "prompt")
        assert not success
        assert "Error calling LLM" in caplog.text


@pytest.mark.asyncio
async def test_extract_metadata_fallback_model(mock_model_client, caplog):
    """Uses fallback_model when no editorial is available."""
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)

    mock_response = MagicMock()
    mock_response.response_text = '{"short_description": "Desc", "tags": []}'
    mock_model_client.call_model_with_retry = AsyncMock(
        return_value=(mock_response, {"short_description": "Desc", "tags": []})
    )

    with patch.object(extractor, "_find_problem_files", return_value=("statement", "")):
        success = await extractor.extract_metadata(
            problem, "main_model", "prompt", fallback_model="fallback"
        )
        assert success
        mock_model_client.call_model_with_retry.assert_called_with(
            model_slug="fallback",
            system_prompt=ANY,
            user_prompt=ANY,
            json_parse_retries=2,
        )
        assert "using fallback model:" in caplog.text


@pytest.mark.asyncio
async def test_extract_metadata_no_fallback_when_editorial_present(mock_model_client):
    """Does NOT switch to fallback_model when an editorial is available."""
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)

    mock_response = MagicMock()
    mock_response.response_text = '{"short_description": "Desc", "tags": []}'
    mock_model_client.call_model_with_retry = AsyncMock(
        return_value=(mock_response, {"short_description": "Desc", "tags": []})
    )

    with patch.object(extractor, "_find_problem_files", return_value=("statement", "editorial")):
        await extractor.extract_metadata(
            problem, "main_model", "prompt", fallback_model="fallback"
        )
        mock_model_client.call_model_with_retry.assert_called_with(
            model_slug="main_model",
            system_prompt=ANY,
            user_prompt=ANY,
            json_parse_retries=2,
        )


@pytest.mark.asyncio
async def test_extract_metadata_missing_tags_key(mock_model_client, caplog):
    """JSON response missing 'tags' key is treated as a failure."""
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)

    mock_model_client.call_model_with_retry = AsyncMock(
        return_value=(
            MagicMock(response_text='{"short_description": "ok"}'),
            {"short_description": "ok"},   # no "tags" key
        )
    )

    with patch.object(extractor, "_find_problem_files", return_value=("statement", "")):
        success = await extractor.extract_metadata(problem, "model", "prompt")
        assert not success
        assert "LLM failed to provide required JSON structure" in caplog.text


# =============================================================================
# parse_all_problems_metadata
# =============================================================================

@pytest.mark.asyncio
async def test_parse_all_problems_metadata():
    """
    parse_all_problems_metadata expects a session_factory (an async context manager
    factory), not a bare session.  We build a minimal factory that hands out the
    same mock DB object via `async with`.
    """
    mock_db = AsyncMock(spec=AsyncSession)
    mock_model_client = MagicMock(spec=ModelClient)
    mock_extractor = MagicMock(spec=MetadataExtractor)
    # side_effect: problem 0 → success, problem 1 → failure (returns False, no exception),
    # problem 2 → success
    mock_extractor.extract_metadata = AsyncMock(side_effect=[True, False, True])

    mock_problems = [Problem(id=i, slug=f"p{i}") for i in range(3)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_problems
    mock_db.execute.return_value = mock_result

    # Build a proper async context-manager factory so that
    # `async with session_factory() as db` returns mock_db.
    @asynccontextmanager
    async def session_factory():
        yield mock_db

    with patch("backend.services.metadata_extraction.MetadataExtractor", return_value=mock_extractor):
        await parse_all_problems_metadata(session_factory, mock_model_client, "model", "prompt")

    assert mock_extractor.extract_metadata.call_count == 3

    # With 3 problems none of the periodic checkpoints (every 5) fire;
    # only the final commit at the end should be called once.
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_parse_all_problems_metadata_periodic_commit():
    """
    The periodic commit fires when (success_count + error_count) % 5 == 0,
    i.e. after processing 5, 10, 15 … problems.  Verify with exactly 5 problems
    all succeeding: the checkpoint fires after the 5th (total=5, 5%5==0) AND
    a final commit fires at the end → 2 commits total.
    """
    mock_db = AsyncMock(spec=AsyncSession)
    mock_model_client = MagicMock(spec=ModelClient)
    mock_extractor = MagicMock(spec=MetadataExtractor)
    mock_extractor.extract_metadata = AsyncMock(return_value=True)

    mock_problems = [Problem(id=i, slug=f"p{i}") for i in range(5)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_problems
    mock_db.execute.return_value = mock_result

    @asynccontextmanager
    async def session_factory():
        yield mock_db

    with patch("backend.services.metadata_extraction.MetadataExtractor", return_value=mock_extractor):
        await parse_all_problems_metadata(session_factory, mock_model_client, "model", "prompt")

    assert mock_extractor.extract_metadata.call_count == 5
    assert mock_db.commit.call_count == 2  # 1 periodic + 1 final


# =============================================================================
# Additional coverage tests for _read_file_content and _find_problem_files
# =============================================================================

def test_read_file_content_read_text_error(tmp_path, mock_model_client, caplog):
    """Non-PDF file that raises on read_text() returns '' and logs the error (lines 45-47)."""
    extractor = MetadataExtractor(mock_model_client)
    md_file = tmp_path / "test.md"
    md_file.write_text("content")
    with patch.object(Path, "read_text", side_effect=OSError("disk error")):
        result = extractor._read_file_content(md_file)
    assert result == ""
    assert "Error reading file" in caplog.text


def test_find_problem_files_task_yaml_no_en_statement(tmp_path, mock_model_client, monkeypatch):
    """task.yaml has no 'en' statement entry — get_statement_file returns None, falls through (branch 62->66)."""
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()
    (problem_dir / "task.yaml").write_text("name: TEST\nstatements:\n  de: statement/de.pdf\n")
    (problem_dir / "problem.md").write_text("fallback content")

    statement, _ = extractor._find_problem_files(1)
    assert statement == "fallback content"


def test_find_problem_files_task_yaml_statement_file_missing(tmp_path, mock_model_client, monkeypatch):
    """task.yaml points to a path that doesn't exist on disk — falls through to pattern search (branch 62->66)."""
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()
    (problem_dir / "task.yaml").write_text("name: TEST\nstatements:\n  en: statement/missing.pdf\n")
    (problem_dir / "problem.md").write_text("fallback content")

    statement, _ = extractor._find_problem_files(1)
    assert statement == "fallback content"


def test_find_problem_files_statement_pattern_empty_then_found(tmp_path, mock_model_client, monkeypatch):
    """statement_patterns: first file exists but _read_file_content returns '' — loop continues (branch 72->68)."""
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()
    (problem_dir / "problem.md").write_bytes(b"")          # first pattern — exists but empty
    (problem_dir / "statement.md").write_text("real content")  # second pattern

    def mock_read(path):
        return path.read_text(encoding="utf-8") if path.stat().st_size > 0 else ""

    with patch.object(extractor, "_read_file_content", side_effect=mock_read):
        statement, _ = extractor._find_problem_files(1)
    assert statement == "real content"


def test_find_problem_files_iterdir_statement_empty_then_found(tmp_path, mock_model_client, monkeypatch):
    """iterdir statement fallback: first file returns '' — scan continues to next (branch 80->77)."""
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()
    (problem_dir / "a_random.md").write_text("")       # sorts first, returns empty
    (problem_dir / "b_random.md").write_text("real statement")  # picked on second iteration

    original_iterdir = Path.iterdir

    def sorted_iterdir(self):
        results = list(original_iterdir(self))
        if self == problem_dir:
            results = sorted(results, key=lambda p: p.name)
        return iter(results)

    def mock_read(path):
        return path.read_text(encoding="utf-8") if path.stat().st_size > 0 else ""

    with patch.object(extractor, "_read_file_content", side_effect=mock_read), \
         patch.object(Path, "iterdir", sorted_iterdir):
        statement, _ = extractor._find_problem_files(1)
    assert statement == "real statement"


def test_find_problem_files_editorial_pattern_empty_then_found(tmp_path, mock_model_client, monkeypatch):
    """editorial_patterns: first match returns '' — loop continues to next pattern (branch 89->85)."""
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()
    (problem_dir / "problem.md").write_text("stmt")
    (problem_dir / "editorial.md").write_bytes(b"")        # first editorial pattern — empty
    (problem_dir / "solution.md").write_text("editorial content")  # second pattern

    def mock_read(path):
        return path.read_text(encoding="utf-8") if path.stat().st_size > 0 else ""

    with patch.object(extractor, "_read_file_content", side_effect=mock_read):
        _, editorial = extractor._find_problem_files(1)
    assert editorial == "editorial content"


def test_find_problem_files_editorial_iterdir_empty_then_found(tmp_path, mock_model_client, monkeypatch):
    """editorial iterdir fallback: first solution file returns '' — scan continues (branch 98->94)."""
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)

    problem_dir = tmp_path / "1"
    problem_dir.mkdir()
    (problem_dir / "problem.md").write_text("stmt")
    (problem_dir / "a_solution_extra.md").write_text("")       # sorts first, returns empty
    (problem_dir / "b_editorial_extra.md").write_text("real editorial")  # picked next

    original_iterdir = Path.iterdir

    def sorted_iterdir(self):
        results = list(original_iterdir(self))
        if self == problem_dir:
            results = sorted(results, key=lambda p: p.name)
        return iter(results)

    def mock_read(path):
        if path.name == "problem.md":
            return "stmt"
        return path.read_text(encoding="utf-8") if path.stat().st_size > 0 else ""

    with patch.object(extractor, "_read_file_content", side_effect=mock_read), \
         patch.object(Path, "iterdir", sorted_iterdir):
        _, editorial = extractor._find_problem_files(1)
    assert editorial == "real editorial"


# =============================================================================
# Additional parse_all_problems_metadata coverage
# =============================================================================

@pytest.mark.asyncio
async def test_parse_all_problems_metadata_exception_in_loop(caplog):
    """extract_metadata raises (not returns False) — error_count incremented, loop continues (lines 197-199)."""
    mock_db = AsyncMock(spec=AsyncSession)
    mock_model_client = MagicMock(spec=ModelClient)
    mock_extractor = MagicMock(spec=MetadataExtractor)
    # First problem raises, second succeeds
    mock_extractor.extract_metadata = AsyncMock(side_effect=[RuntimeError("boom"), True])

    mock_problems = [Problem(id=i, slug=f"p{i}") for i in range(2)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_problems
    mock_db.execute.return_value = mock_result

    @asynccontextmanager
    async def session_factory():
        yield mock_db

    with patch("backend.services.metadata_extraction.MetadataExtractor", return_value=mock_extractor):
        await parse_all_problems_metadata(session_factory, mock_model_client, "model", "prompt")

    assert mock_extractor.extract_metadata.call_count == 2
    assert "Error processing problem" in caplog.text
    # Final commit still fires
    assert mock_db.commit.call_count == 1


@pytest.mark.asyncio
async def test_parse_all_problems_metadata_final_commit_error(caplog):
    """Final db.commit() raises — error is logged and swallowed (lines 205-206)."""
    mock_db = AsyncMock(spec=AsyncSession)
    mock_model_client = MagicMock(spec=ModelClient)
    mock_extractor = MagicMock(spec=MetadataExtractor)
    mock_extractor.extract_metadata = AsyncMock(return_value=True)

    mock_problems = [Problem(id=0, slug="p0")]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_problems
    mock_db.execute.return_value = mock_result
    mock_db.commit.side_effect = Exception("db gone")

    @asynccontextmanager
    async def session_factory():
        yield mock_db

    with patch("backend.services.metadata_extraction.MetadataExtractor", return_value=mock_extractor):
        # Should not raise even though commit fails
        await parse_all_problems_metadata(session_factory, mock_model_client, "model", "prompt")

    assert "Error committing final changes" in caplog.text