"""
Problem Ingestion Service for APPS Dataset.

Fetches coding problems from the Hendrycks APPS dataset (GitHub or HuggingFace)
and stores them in the backend database and filesystem.
"""

import os
import json
import asyncio
import logging
import httpx
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import Problem
from backend.database.session import AsyncSessionLocal
from backend.api.problems import get_problem_tests_dir, count_test_cases

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
APPS_GITHUB_BASE_URL = "https://raw.githubusercontent.com/hendrycks/apps/main/train"
DEFAULT_TIME_LIMIT_MS = 2000
DEFAULT_MEMORY_LIMIT_MB = 256

class APPSIngestor:
    """Service to ingest problems from the APPS dataset."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.client = httpx.AsyncClient(timeout=30.0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-friendly slug from a name."""
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        return slug.strip('-')

    async def fetch_problem_data(self, problem_id_str: str) -> Optional[Dict[str, Any]]:
        """Fetch problem files from APPS GitHub repository."""
        # Note: APPS problems on GitHub are organized in directories named by ID (e.g., '0000', '0001')
        # We need question.txt and input_output.json
        
        base_url = f"{APPS_GITHUB_BASE_URL}/{problem_id_str}"
        
        try:
            # Fetch question description
            desc_res = await self.client.get(f"{base_url}/question.txt")
            if desc_res.status_code != 200:
                logger.error(f"Failed to fetch question.txt for problem {problem_id_str}")
                return None
            
            description = desc_res.text
            
            # Fetch metadata/test cases
            io_res = await self.client.get(f"{base_url}/input_output.json")
            if io_res.status_code != 200:
                logger.error(f"Failed to fetch input_output.json for problem {problem_id_str}")
                return None
            
            io_data = io_res.json()
            
            # Metadata (if metadata.json exists, we could fetch that too, but question.txt usually has it)
            # For simplicity, we'll derive the name from the first line of question.txt or the ID
            name_match = re.search(r'^#\s+(.*)$', description, re.MULTILINE)
            name = name_match.group(1) if name_match else f"APPS Problem {problem_id_str}"
            
            return {
                "id_str": problem_id_str,
                "name": name,
                "description": description,
                "io": io_data
            }
        except Exception as e:
            logger.exception(f"Error fetching problem {problem_id_str}: {e}")
            return None

    async def ingest_problem(self, apps_id: int) -> Optional[Problem]:
        """Fetch, parse, and store a single APPS problem."""
        problem_id_str = f"{apps_id:04d}"
        logger.info(f"Starting ingestion for APPS problem {problem_id_str}")
        
        data = await self.fetch_problem_data(problem_id_str)
        if not data:
            return None
        
        slug = self._generate_slug(data["name"])
        
        # Check if problem already exists by slug
        result = await self.session.execute(select(Problem).where(Problem.slug == slug))
        existing_problem = result.scalar_one_or_none()
        
        if existing_problem:
            logger.info(f"Problem with slug '{slug}' already exists. Updating...")
            problem = existing_problem
            problem.name = data["name"]
            problem.description_md = data["description"]
        else:
            problem = Problem(
                name=data["name"],
                slug=slug,
                description_md=data["description"],
                time_limit_ms=DEFAULT_TIME_LIMIT_MS,
                memory_limit_mb=DEFAULT_MEMORY_LIMIT_MB,
                scoring_mode="binary"
            )
            self.session.add(problem)
        
        # We need to flush to get the internal ID for filesystem storage
        await self.session.flush()
        
        # Process test cases
        tests_dir = get_problem_tests_dir(problem.id)
        tests_dir.mkdir(parents=True, exist_ok=True)
        
        # Clear existing tests if any
        for f in tests_dir.glob("*"):
            if f.is_file():
                f.unlink()
        
        inputs = data["io"].get("inputs", [])
        outputs = data["io"].get("outputs", [])
        
        # Some APPS problems have different structures for inputs/outputs
        # Ensure we have a list of strings
        test_count = 0
        for i, (inp, out) in enumerate(zip(inputs, outputs)):
            # Convert to string if they aren't (APPS can sometimes have lists here)
            if isinstance(inp, list):
                inp = "\n".join(map(str, inp))
            if isinstance(out, list):
                out = "\n".join(map(str, out))
                
            in_file = tests_dir / f"{i}.in"
            out_file = tests_dir / f"{i}.out"
            
            with open(in_file, "w", encoding="utf-8") as f:
                f.write(str(inp))
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(str(out))
            test_count += 1
            
        problem.test_count = test_count
        await self.session.commit()
        
        logger.info(f"Successfully ingested problem '{problem.name}' (ID: {problem.id}) with {test_count} tests.")
        return problem

async def ingest_batch(start_id: int, count: int):
    """Batch ingestion helper."""
    async with AsyncSessionLocal() as session:
        async with APPSIngestor(session) as ingestor:
            for i in range(start_id, start_id + count):
                try:
                    await ingestor.ingest_problem(i)
                except Exception as e:
                    logger.error(f"Failed to ingest problem {i}: {e}")
                    await session.rollback()

if __name__ == "__main__":
    # Example usage: python -m backend.services.problem_ingestion
    import sys
    
    start_id = 0
    count = 1
    
    if len(sys.argv) > 1:
        start_id = int(sys.argv[1])
    if len(sys.argv) > 2:
        count = int(sys.argv[2])
        
    asyncio.run(ingest_batch(start_id, count))
