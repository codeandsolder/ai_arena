"""
Service for extracting metadata (short description and tags) from problem statements and editorials using LLMs.
"""

import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pymupdf4llm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend import config
from backend.database.models import Problem
from backend.orchestrator.model_client import ModelClient
from backend.utils.task_yaml import parse_task_yaml

logger = logging.getLogger(__name__)

class MetadataExtractor:
    """Extracts metadata from problem files using LLMs."""

    def __init__(self, model_client: ModelClient):
        self.model_client = model_client

    def _get_problem_dir(self, problem_id: int) -> Path:
        """Get the base directory for a problem."""
        return config.PROBLEMS_DIR / str(problem_id)

    def _read_file_content(self, path: Path) -> str:
        """Read content from Markdown or PDF file."""
        if not path.exists():
            return ""
        
        if path.suffix.lower() == ".pdf":
            try:
                return pymupdf4llm.to_markdown(str(path))
            except Exception as e:
                logger.error(f"Error converting PDF {path} to markdown: {e}")
                return ""
        else:
            try:
                return path.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"Error reading file {path}: {e}")
                return ""

    def _find_problem_files(self, problem_id: int) -> Tuple[str, str]:
        """Find problem statement and editorial files and return their content."""
        problem_dir = self._get_problem_dir(problem_id)
        if not problem_dir.exists():
            return "", ""

        statement_content = ""
        editorial_content = ""

        # 1. Try to use task.yaml if it exists
        task_config = parse_task_yaml(problem_dir)
        if task_config:
            statement_path = task_config.get_statement_file("en")
            if statement_path and statement_path.exists():
                statement_content = self._read_file_content(statement_path)

        # 2. Look for statement files using default patterns
        if not statement_content:
            statement_patterns = ["problem.md", "statement.md", "problem.pdf", "statement.pdf"]
            for pattern in statement_patterns:
                path = problem_dir / pattern
                if path.exists():
                    statement_content = self._read_file_content(path)
                    if statement_content:
                        break
        
        # If no direct file, look for any .md or .pdf that might be the statement
        if not statement_content:
            for path in problem_dir.iterdir():
                if path.is_file() and path.suffix.lower() in [".md", ".pdf"] and "editorial" not in path.name.lower():
                    statement_content = self._read_file_content(path)
                    if statement_content:
                        break

        # Look for editorial files
        editorial_patterns = ["editorial.md", "solution.md", "editorial.pdf", "solution.pdf"]
        for pattern in editorial_patterns:
            path = problem_dir / pattern
            if path.exists():
                editorial_content = self._read_file_content(path)
                if editorial_content:
                    break
        
        # If no direct file, look for any .md or .pdf with "editorial" or "solution" in name
        if not editorial_content:
            for path in problem_dir.iterdir():
                if path.is_file() and path.suffix.lower() in [".md", ".pdf"]:
                    if "editorial" in path.name.lower() or "solution" in path.name.lower():
                        editorial_content = self._read_file_content(path)
                        if editorial_content:
                            break

        return statement_content, editorial_content

    async def extract_metadata(
        self, 
        problem: Problem, 
        model: str, 
        prompt: str, 
        fallback_model: Optional[str] = None
    ) -> bool:
        """
        Extract short_description and tags for a problem.
        Updates the problem in the database if successful.
        """
        statement, editorial = self._find_problem_files(problem.id)
        
        if not statement:
            # Try to use description_md from DB if file not found
            statement = problem.description_md
            
        if not statement:
            logger.warning(f"No statement found for problem {problem.id} ({problem.slug})")
            return False

        # Determine which model to use
        current_model = model
        if not editorial and fallback_model:
            current_model = fallback_model
            logger.info(f"No editorial found for {problem.slug}, using fallback model: {current_model}")

        # Construct LLM prompt
        full_user_prompt = f"{prompt}\n\nPROBLEM STATEMENT:\n{statement}\n"
        if editorial:
            full_user_prompt += f"\nEDITORIAL:\n{editorial}\n"
        
        system_prompt = "You are a competitive programming expert. Extract metadata from the provided problem in JSON format."

        try:
            response, parsed_json = await self.model_client.call_model_with_retry(
                model_slug=current_model,
                system_prompt=system_prompt,
                user_prompt=full_user_prompt,
                json_parse_retries=2
            )
            
            if not parsed_json or "short_description" not in parsed_json or "tags" not in parsed_json:
                logger.error(f"LLM failed to provide required JSON structure for {problem.slug}. Response: {response.response_text}")
                return False

            # Update problem
            problem.short_description = parsed_json["short_description"]
            problem.tags = json.dumps(parsed_json["tags"])
            
            logger.info(f"Successfully extracted metadata for {problem.slug}")
            return True

        except Exception as e:
            logger.error(f"Error calling LLM for problem {problem.slug}: {e}")
            return False

async def parse_all_problems_metadata(
    session_factory: Callable[[], AsyncSession],
    model_client: ModelClient,
    model: str,
    prompt: str,
    fallback_model: Optional[str] = None
):
    """
    Background task to parse metadata for all problems.
    
    Args:
        session_factory: A callable that returns a new AsyncSession.
        model_client: The model client for LLM calls.
        model: The model to use for parsing.
        prompt: The prompt to use.
        fallback_model: Optional fallback model if no editorial is found.
    """
    # Create our own session for the background task
    async with session_factory() as db:
        extractor = MetadataExtractor(model_client)
        
        # Fetch all problems
        result = await db.execute(select(Problem))
        problems = result.scalars().all()
        
        success_count = 0
        error_count = 0
        
        for problem in problems:
            try:
                # Skip if already has metadata? (Maybe an option later, for now re-parse if requested)
                success = await extractor.extract_metadata(problem, model, prompt, fallback_model)
                if success:
                    success_count += 1
                # Commit periodically to avoid holding locks for too long
                if (success_count + error_count) % 5 == 0:
                    await db.commit()
            except Exception as e:
                error_count += 1
                logger.error(f"Error processing problem {problem.id} ({problem.slug}): {e}")
                # Continue processing other problems instead of crashing the entire job
        
        # Final commit for any remaining changes
        try:
            await db.commit()
        except Exception as e:
            logger.error(f"Error committing final changes: {e}")
        
        logger.info(f"Finished parsing metadata. Successfully updated {success_count}/{len(problems)} problems. Errors: {error_count}")
