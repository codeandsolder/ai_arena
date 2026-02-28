import json
import pytest
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

@pytest.mark.asyncio
async def test_get_problem_dir(mock_model_client, monkeypatch):
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", Path("/fake/dir"))
    extractor = MetadataExtractor(mock_model_client)
    assert extractor._get_problem_dir(42) == Path("/fake/dir/42")

def test_read_file_content_md(tmp_path, mock_model_client):
    extractor = MetadataExtractor(mock_model_client)
    md_file = tmp_path / "test.md"
    md_file.write_text("Markdown content")
    assert extractor._read_file_content(md_file) == "Markdown content"

def test_read_file_content_pdf(tmp_path, mock_model_client):
    extractor = MetadataExtractor(mock_model_client)
    pdf_file = tmp_path / "test.pdf"
    pdf_file.write_bytes(b"Fake PDF")  # Not real PDF, but for mocking

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

def test_read_file_content_other_extension(tmp_path, mock_model_client, caplog):
    extractor = MetadataExtractor(mock_model_client)
    txt_file = tmp_path / "test.txt"
    txt_file.write_text("Text content")
    assert extractor._read_file_content(txt_file) == "Text content"  # It tries to read as text

@pytest.mark.parametrize(
    "files_present, expected_statement, expected_editorial",
    [
        (["problem.md"], "problem content", ""),
        (["statement.pdf"], "parsed statement", ""),
        (["editorial.md"], "", "editorial content"),
        (["solution.pdf"], "parsed solution", "parsed solution"),
        (["random.md", "editorial_random.pdf"], "random content", "parsed editorial"),
        (["solution_random.md", "random.pdf"], "random content", "solution content"),
        ([], "", ""),
    ]
)
def test_find_problem_files(tmp_path, mock_model_client, files_present, expected_statement, expected_editorial, monkeypatch):
    monkeypatch.setattr("backend.services.metadata_extraction.config.PROBLEMS_DIR", tmp_path)
    extractor = MetadataExtractor(mock_model_client)
    
    problem_dir = tmp_path / "1"
    problem_dir.mkdir()
    
    content_map = {
        "problem.md": "problem content",
        "statement.pdf": "parsed statement",
        "editorial.md": "editorial content",
        "solution.pdf": "parsed solution",
        "random.md": "random content",
        "random.pdf": "random content",
        "editorial_random.pdf": "parsed editorial",
        "solution_random.md": "solution content",
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
            # Create empty placeholder for PDF files so path.exists() returns True
            path.write_bytes(b"placeholder")
    
    with patch.object(extractor, "_read_file_content", side_effect=mock_read):
        statement, editorial = extractor._find_problem_files(1)
        assert statement == expected_statement
        assert editorial == expected_editorial

@pytest.mark.asyncio
async def test_extract_metadata_success(mock_model_client):
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)
    
    mock_response = MagicMock()
    mock_response.response_text = '{"short_description": "Short desc", "tags": ["tag1", "tag2"]}'
    mock_model_client.call_model_with_retry = AsyncMock(return_value=(mock_response, {"short_description": "Short desc", "tags": ["tag1", "tag2"]}))
    
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
    problem = Problem(id=1, slug="test-slug", description_md="DB desc")
    extractor = MetadataExtractor(mock_model_client)
    
    mock_response = MagicMock()
    mock_response.response_text = '{"short_description": "Short", "tags": []}'
    mock_model_client.call_model_with_retry = AsyncMock(return_value=(mock_response, {"short_description": "Short", "tags": []}))
    
    with patch.object(extractor, "_find_problem_files", return_value=("", "")):
        success = await extractor.extract_metadata(problem, "model", "prompt")
        assert success
        assert problem.short_description == "Short"

@pytest.mark.asyncio
async def test_extract_metadata_llm_failure(mock_model_client, caplog):
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)
    
    mock_model_client.call_model_with_retry = AsyncMock(return_value=(MagicMock(response_text="invalid"), None))
    
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
    problem = Problem(id=1, slug="test-slug")
    extractor = MetadataExtractor(mock_model_client)
    
    mock_response = MagicMock()
    mock_response.response_text = '{"short_description": "Desc", "tags": []}'
    mock_model_client.call_model_with_retry = AsyncMock(return_value=(mock_response, {"short_description": "Desc", "tags": []}))
    
    with patch.object(extractor, "_find_problem_files", return_value=("statement", "")):
        success = await extractor.extract_metadata(problem, "main_model", "prompt", fallback_model="fallback")
        assert success
        mock_model_client.call_model_with_retry.assert_called_with(
            model_slug="fallback",
            system_prompt=ANY,
            user_prompt=ANY,
            json_parse_retries=2
        )
        assert "using fallback model:" in caplog.text

@pytest.mark.asyncio
async def test_parse_all_problems_metadata():
    mock_db = AsyncMock(spec=AsyncSession)
    mock_model_client = MagicMock(spec=ModelClient)
    mock_extractor = MagicMock(spec=MetadataExtractor)
    mock_extractor.extract_metadata = AsyncMock(side_effect=[True, False, True])
    
    mock_problems = [Problem(id=i, slug=f"p{i}") for i in range(3)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = mock_problems
    mock_db.execute.return_value = mock_result
    
    with patch("backend.services.metadata_extraction.MetadataExtractor", return_value=mock_extractor):
        await parse_all_problems_metadata(mock_db, mock_model_client, "model", "prompt")
    
    assert mock_extractor.extract_metadata.call_count == 3
    assert mock_db.commit.call_count == 1  # Only final commit at end