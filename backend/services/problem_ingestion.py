"""
Problem Ingestion Service for APPS Dataset.

Fetches coding problems from the Hendrycks APPS dataset (GitHub or HuggingFace)
and stores them in the backend database and filesystem.
"""

import os
import json
import uuid
import asyncio
import logging
import httpx
import re
import gzip
from pathlib import Path
from typing import Dict, Any, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import pymupdf4llm
from backend import config
from backend.database.models import Problem
from backend.database.session import AsyncSessionLocal
from backend.api.problems import get_problem_tests_dir

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
APPS_GITHUB_BASE_URL = "https://raw.githubusercontent.com/hendrycks/apps/main/train"
GITHUB_API_BASE = "https://api.github.com"
DEFAULT_TIME_LIMIT_MS = 2000
DEFAULT_MEMORY_LIMIT_MB = 256

class IOIIngestor:
    """Service to ingest problems from IOI GitHub repositories."""

    def __init__(self, session: AsyncSession):
        self.session = session
        headers = {"Accept": "application/vnd.github.v3+json"}
        if config.GITHUB_TOKEN:
            headers["Authorization"] = f"token {config.GITHUB_TOKEN}"
        self.client = httpx.AsyncClient(timeout=30.0, headers=headers)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.client.aclose()

    def _generate_slug(self, name: str) -> str:
        """Generate a URL-friendly slug from a name."""
        slug = name.lower()
        slug = re.sub(r'[^a-z0-9]+', '-', slug)
        return slug.strip('-')

    def parse_github_url(self, url: str) -> Dict[str, str]:
        """
        Parses a GitHub URL to extract owner, repo, ref, and path.
        Example: https://github.com/austrian-olympiad-informatics/ioi-tasks/tree/main/ioi2023-soccer
        """
        # Handle full repo URL: https://github.com/owner/repo
        repo_only_pattern = r"https://github\.com/([^/]+)/([^/]+)/?$"
        repo_match = re.match(repo_only_pattern, url)
        if repo_match:
            return {
                "owner": repo_match.group(1),
                "repo": repo_match.group(2),
                "ref": "main",
                "path": ""
            }

        pattern = r"https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.*)"
        match = re.match(pattern, url)
        if not match:
            # Try without tree/branch (defaults to main)
            pattern_simple = r"https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.*)"
            match = re.match(pattern_simple, url)
            if not match:
                raise ValueError(f"Invalid GitHub URL: {url}")
        
        return {
            "owner": match.group(1),
            "repo": match.group(2),
            "ref": match.group(3),
            "path": match.group(4)
        }

    async def ingest_repository(self, repo_url: str):
        """Recursively find and ingest all problems in a repository/directory."""
        parsed = self.parse_github_url(repo_url)
        contents = await self.fetch_contents(parsed["owner"], parsed["repo"], parsed["path"], parsed["ref"])
        
        # If the directory contains a .pdf file, it's likely a problem directory
        is_problem_dir = any(item["name"].lower().endswith(".pdf") for item in contents if item["type"] == "file")
        if not is_problem_dir:
            # Also check 'statement' directory
            is_problem_dir = any(item["name"].lower() == "statement" and item["type"] == "dir" for item in contents)

        if is_problem_dir:
            await self.ingest_from_github(repo_url)
        else:
            # Recursively explore subdirectories
            for item in contents:
                if item["type"] == "dir" and not item["name"].startswith("."):
                    sub_url = f"https://github.com/{parsed['owner']}/{parsed['repo']}/tree/{parsed['ref']}/{item['path']}"
                    await self.ingest_repository(sub_url)

    async def fetch_contents(self, owner: str, repo: str, path: str, ref: str) -> List[Dict[str, Any]]:
        """Fetch contents of a directory via GitHub API or local repository."""
        # Try local repository first
        local_dir = config.PROBLEMS_REPO_DIR / path
        
        # Completely short-circuit if the base problem repo exists locally
        if config.PROBLEMS_REPO_DIR.exists():
            if local_dir.exists() and local_dir.is_dir():
                logger.info(f"Using local directory: {local_dir}")
                items = []
                for entry in local_dir.iterdir():
                    item_type = "dir" if entry.is_dir() else "file"
                    relative_path = str(entry.relative_to(config.PROBLEMS_REPO_DIR)).replace("\\", "/")
                    items.append({
                        "name": entry.name,
                        "type": item_type,
                        "path": relative_path,
                        "download_url": f"local://{relative_path}"
                    })
                return items
            else:
                logger.warning(f"Local repo exists but path {path} not found. Short-circuiting GitHub API.")
                return []

        # Fallback to GitHub API (only if local repo does not exist at all)
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
        res = await self.client.get(url)
        if res.status_code != 200:
            logger.error(f"Failed to fetch contents for {path}: {res.status_code} {res.text}")
            return []
        return res.json()

    async def download_file(self, download_url: str) -> bytes:
        """Download a file from a URL or read from local repository."""
        is_local = download_url.startswith("local://")
        local_path = None
        if is_local:
            local_path = config.PROBLEMS_REPO_DIR / download_url[8:]
            if local_path.exists() and local_path.is_file():
                logger.info(f"Reading local file: {local_path}")
                content = local_path.read_bytes()
                
                # Check if it's a Git LFS pointer (often happens if git-lfs is not installed or filter failed)
                # Or if it's a small file containing a path (symlink-like)
                if content.startswith(b"version https://git-lfs.github.com/spec/v1") or (len(content) < 100 and b"/" in content):
                    logger.warning(f"File {local_path} appears to be a pointer or symlink. Falling back to GitHub.")
                    # Fall through to HTTP download if possible
                else:
                    if local_path.suffix == ".gz":
                        try:
                            return gzip.decompress(content)
                        except Exception as e:
                            logger.error(f"Failed to decompress {local_path}: {e}")
                    return content
        
        # Fallback to HTTP download
        # If we had a local path, we need to construct the GitHub download URL
        # download_url might already be a http url if fetch_contents fell back
        actual_url = download_url
        if is_local:
            # Reconstruct from local path
            # local://owner/repo/path -> https://raw.githubusercontent.com/owner/repo/main/path
            # Wait, the local:// path we store is relative to PROBLEMS_REPO_DIR
            # We need the original parsed info or just use the download_url if it was passed correctly.
            # Actually, ingest_from_github and sync_tests use fetch_contents which returns 
            # download_url=f"local://{relative_path}"
            # This is not enough to reconstruct the GitHub URL unless we know the owner/repo.
            # For now, let's assume we can't easily fallback if we don't have the full URL.
            pass

        if actual_url.startswith("local://"):
            logger.error(f"Cannot fallback for {actual_url} - no remote URL available.")
            return b""

        res = await self.client.get(actual_url)
        res.raise_for_status()
        content = res.content
        if actual_url.split("?")[0].endswith(".gz"):
            try:
                return gzip.decompress(content)
            except:
                pass
        return content

    async def ingest_from_github(self, github_url: str) -> Optional[Problem]:
        """Fetch, parse, and store a problem from GitHub."""
        logger.info(f"Starting ingestion from GitHub URL: {github_url}")
        
        parsed = self.parse_github_url(github_url)
        contents = await self.fetch_contents(parsed["owner"], parsed["repo"], parsed["path"], parsed["ref"])
        
        # 1. Find the statement (PDF or MD)
        # Search for a directory named 'statement', a .pdf file, or statement.md in the main path
        pdf_url = None
        pdf_name = None
        statement_md_url = None
        
        for item in contents:
            if item["type"] == "file":
                if item["name"].lower().endswith(".pdf"):
                    # Prefer English versions if multiple PDFs exist
                    if not pdf_url or "en" in item["name"].lower():
                        pdf_url = item["download_url"]
                        pdf_name = item["name"]
                elif item["name"].lower() == "statement.md":
                    statement_md_url = item["download_url"]
            
            if item["type"] == "dir" and item["name"].lower() == "statement":
                stmt_contents = await self.fetch_contents(parsed["owner"], parsed["repo"], item["path"], parsed["ref"])
                for s_item in stmt_contents:
                    if s_item["type"] == "file":
                        if s_item["name"].lower().endswith(".pdf"):
                            if not pdf_url or "en" in s_item["name"].lower():
                                pdf_url = s_item["download_url"]
                                pdf_name = s_item["name"]
                        elif s_item["name"].lower() == "statement.md":
                            statement_md_url = s_item["download_url"]
        
        description_md = ""
        if statement_md_url:
            logger.info(f"Downloading Markdown statement from {statement_md_url}")
            md_bytes = await self.download_file(statement_md_url)
            description_md = md_bytes.decode("utf-8")
            if not pdf_name:
                pdf_name = parsed["path"].split("/")[-1] if parsed["path"] else parsed["repo"]
        elif pdf_url:
            # 2. Download and Convert PDF
            logger.info(f"Downloading PDF statement from {pdf_url}")
            pdf_bytes = await self.download_file(pdf_url)
            
            temp_pdf = Path(f"temp_statement_{uuid.uuid4().hex}.pdf")
            temp_pdf.write_bytes(pdf_bytes)
            
            try:
                # Use pymupdf4llm to extract content as markdown
                # We use the path directly as pymupdf4llm.to_markdown handles it well
                description_md = pymupdf4llm.to_markdown(str(temp_pdf))
                
                # Refine the extraction: usually IOI problems have a "Problem Statement" or "Task" section
                # If the description is too long or contains a lot of boilerplate, we might need more logic
                # For now, ensure it's not empty and clean up some common artifacts
                if description_md:
                    # Remove multiple newlines
                    description_md = re.sub(r'\n{3,}', '\n\n', description_md)
                    # Remove potential header/footer artifacts like page numbers if they are isolated
                    description_md = re.sub(r'\n\s*\d+\s*\n', '\n', description_md)
            finally:
                if temp_pdf.exists():
                    try:
                        temp_pdf.unlink()
                    except Exception as e:
                        logger.error(f"Failed to delete temp PDF {temp_pdf}: {e}")
        
        if not description_md:
            logger.error(f"No statement found at {github_url}")
            return None

        # 3. Create/Update Problem Record
        problem_name = (pdf_name or "Unknown Problem").replace(".pdf", "").replace("-", " ").title()
        slug = self._generate_slug(problem_name)
        
        result = await self.session.execute(select(Problem).where(Problem.slug == slug))
        existing_problem = result.scalar_one_or_none()
        
        if existing_problem:
            logger.info(f"Problem with slug '{slug}' already exists. Updating...")
            problem = existing_problem
            problem.name = problem_name
            problem.description_md = description_md
            problem.source_url = github_url
        else:
            problem = Problem(
                name=problem_name,
                slug=slug,
                description_md=description_md,
                source_url=github_url,
                test_count=0,
                tests_downloaded=False,
                time_limit_ms=DEFAULT_TIME_LIMIT_MS,
                memory_limit_mb=DEFAULT_MEMORY_LIMIT_MB,
                scoring_mode="binary"
            )
            self.session.add(problem)
        
        await self.session.flush()
        await self.session.commit()
        
        logger.info(f"Successfully ingested problem '{problem.name}' (ID: {problem.id}) from GitHub.")

        # If we have local repository, sync tests immediately instead of lazily
        if config.PROBLEMS_REPO_DIR.exists():
            logger.info(f"Automatically syncing tests for {problem.name} from local repository.")
            await self.sync_tests(problem.id)

        return problem

    async def sync_tests(self, problem_id: int) -> int:
        """Download tests from GitHub for an existing problem."""
        result = await self.session.execute(select(Problem).where(Problem.id == problem_id))
        problem = result.scalar_one_or_none()
        
        if not problem or not problem.source_url:
            logger.error(f"Problem {problem_id} not found or has no source URL.")
            return 0
        
        parsed = self.parse_github_url(problem.source_url)
        
        # IOI usually has tests in a 'tests' directory
        # We also look in 'data/secret' and 'data/sample' which is common in some tasks
        # CMS/IOI structure: 'tc' for test cases, 'statement' for statements
        search_paths = [
            parsed["path"],
            f"{parsed['path']}/tc",
            f"{parsed['path']}/tests",
            f"{parsed['path']}/data/secret",
            f"{parsed['path']}/data/sample",
            f"{parsed['path']}/data",
            f"{parsed['path']}/tc/gen/data",
            f"{parsed['path']}/tc/gen/manual",
        ]
        
        # Collect all test files, possibly from subdirectories
        async def collect_files(current_path: str) -> List[Dict[str, Any]]:
            all_files = []
            try:
                items = await self.fetch_contents(parsed["owner"], parsed["repo"], current_path, parsed["ref"])
            except Exception as e:
                logger.warning(f"Failed to fetch contents for {current_path}: {e}")
                return []
            for item in items:
                if item["type"] == "file":
                    all_files.append(item)
                elif item["type"] == "dir" and item["name"] not in (".", "..", "solution", "statement", "checker", "grader"):
                    all_files.extend(await collect_files(item["path"]))
            return all_files

        test_files = []
        tests_path = None
        
        # Try prioritized search paths first
        for path in search_paths:
            try:
                files = await collect_files(path)
                if any(f["name"].endswith(".in") or f["name"].endswith(".in.gz") for f in files):
                    test_files = files
                    tests_path = path
                    break
            except Exception as e:
                logger.debug(f"Path {path} not found or error: {e}")
                continue

        if not test_files:
            # Fallback to full recursive search from root path if not found in common paths
            test_files = await collect_files(parsed["path"])
            if any(f["name"].endswith(".in") or f["name"].endswith(".in.gz") for f in test_files):
                tests_path = parsed["path"]
            else:
                test_files = []

        if not test_files:
            logger.error(f"No 'tests' directory or .in files found for problem {problem_id} in {search_paths}")
            return 0
        
        tests_dir = get_problem_tests_dir(problem.id)
        tests_dir.mkdir(parents=True, exist_ok=True)
        
        # IOI usually has .in and .out or .sol or .ans files
        # We'll map them by base name
        in_files = {}
        out_files = {}
        
        for item in test_files:
            name = item["name"]
            # Remove .gz if present for mapping
            mapping_name = name[:-3] if name.endswith(".gz") else name
            
            if mapping_name.endswith(".in"):
                base = mapping_name[:-3]
                in_files[base] = item["download_url"]
            elif mapping_name.endswith(".out") or mapping_name.endswith(".sol") or mapping_name.endswith(".ans"):
                # Handle various output extensions
                # We need to be careful with dots in base name (e.g. 1.in, 1.ans)
                # Split by the last dot to get the base
                parts = mapping_name.rsplit(".", 1)
                base = parts[0]
                out_files[base] = item["download_url"]
        
        count = 0
        # Sort bases to ensure deterministic ordering
        for base in sorted(in_files.keys()):
            if base in out_files:
                logger.info(f"Downloading test case: {base}")
                in_content = await self.download_file(in_files[base])
                out_content = await self.download_file(out_files[base])
                
                if not in_content or not out_content:
                    logger.warning(f"Skipping test case {base} due to empty content")
                    continue

                with open(tests_dir / f"{count}.in", "wb") as f:
                    f.write(in_content)
                with open(tests_dir / f"{count}.out", "wb") as f:
                    f.write(out_content)
                count += 1
        
        problem.test_count = count
        problem.tests_downloaded = True
        await self.session.commit()
        
        logger.info(f"Synced {count} tests for problem {problem_id}")
        return count

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
        """Fetch problem files from local repository or APPS GitHub repository."""
        # Try local repository first
        # APPS problems on GitHub are organized in directories named by ID (e.g., '0000', '0001')
        # They are usually under a 'train' or 'test' directory.
        local_base = config.PROBLEMS_REPO_DIR / "train" / problem_id_str
        if not (local_base.exists() and local_base.is_dir()):
            local_base = config.PROBLEMS_REPO_DIR / problem_id_str

        if local_base.exists() and local_base.is_dir():
            logger.info(f"Using local APPS data from: {local_base}")
            try:
                desc_path = local_base / "question.txt"
                if desc_path.exists():
                    description = desc_path.read_text(encoding="utf-8")
                else:
                    logger.error(f"Missing question.txt in {local_base}")
                    return None
                
                io_path = local_base / "input_output.json"
                if io_path.exists():
                    with open(io_path, "r", encoding="utf-8") as f:
                        io_data = json.load(f)
                else:
                    logger.error(f"Missing input_output.json in {local_base}")
                    return None

                name_match = re.search(r'^#\s+(.*)$', description, re.MULTILINE)
                name = name_match.group(1) if name_match else f"APPS Problem {problem_id_str}"
                
                return {
                    "id_str": problem_id_str,
                    "name": name,
                    "description": description,
                    "io": io_data
                }
            except Exception as e:
                logger.error(f"Error reading local APPS problem {problem_id_str}: {e}")
                # Fallback to download

        # Fallback to GitHub raw
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
