"""
Problem Ingestion Service for APPS Dataset.

Fetches coding problems from the Hendrycks APPS dataset (GitHub or HuggingFace)
and stores them in the backend database and filesystem.
"""

import os
import json
import uuid
import asyncio
from datetime import datetime
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

# Use a module-level logger without reconfiguring the root logger.
# The application entry point should configure logging; doing it here
# caused duplicate handlers and over-verbose output on every import.
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
        self.current_parsed = None
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

        is_problem_dir = any(item["name"].lower().endswith(".pdf") for item in contents if item["type"] == "file")
        if not is_problem_dir:
            is_problem_dir = any(item["name"].lower() == "statement" and item["type"] == "dir" for item in contents)

        if is_problem_dir:
            await self.ingest_from_github(repo_url)
        else:
            for item in contents:
                if item["type"] == "dir" and not item["name"].startswith("."):
                    sub_url = f"https://github.com/{parsed['owner']}/{parsed['repo']}/tree/{parsed['ref']}/{item['path']}"
                    await self.ingest_repository(sub_url)

    async def fetch_contents(self, owner: str, repo: str, path: str, ref: str) -> List[Dict[str, Any]]:
        """Fetch contents of a directory via GitHub API or local repository."""
        local_dir = config.PROBLEMS_REPO_DIR / path

        if config.PROBLEMS_REPO_DIR.exists():
            if local_dir.exists() and local_dir.is_dir():
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
                logger.warning(f"Local repo exists but path '{path}' not found. Skipping.")
                return []

        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
        res = await self.client.get(url)
        if res.status_code != 200:
            logger.error(f"Failed to fetch contents for '{path}': {res.status_code} {res.text}")
            return []
        return res.json()

    async def download_file(self, download_url: str) -> bytes:
        """Download a file from a URL or read from local repository."""
        is_local = download_url.startswith("local://")
        local_path = None
        if is_local:
            local_path = config.PROBLEMS_REPO_DIR / download_url[8:]
            if not local_path.is_absolute():
                local_path = Path(os.getcwd()) / local_path
            if local_path.exists() and local_path.is_file():
                content = local_path.read_bytes()

                # Check if it's a Git LFS pointer
                if content.startswith(b"version https://git-lfs.github.com/spec/v1"):
                    logger.warning(f"Git LFS pointer detected for {local_path}, attempting pull...")
                    if await self._git_lfs_pull(download_url[8:]):
                        content = local_path.read_bytes()
                    else:
                        logger.error(f"Failed to pull LFS file: {local_path}")
                        return b""

                # Check if it's a symlink-as-file (small, path-like content)
                elif len(content) < 255 and b"\n" not in content and not content.startswith(b"\x1f\x8b"):
                    try:
                        target_path = content.decode("utf-8").strip()
                        if (not target_path.startswith("/") and not target_path.startswith("\\") and
                            all(c.isprintable() for c in target_path) and
                            ("/" in target_path or "\\" in target_path or "." in target_path or ".." in target_path)):
                            resolved_path = (local_path.parent / target_path).resolve()
                            if resolved_path.exists() and resolved_path.is_file():
                                logger.debug(f"Resolved symlink {local_path} -> {resolved_path}")
                                content = resolved_path.read_bytes()
                                local_path = resolved_path
                            else:
                                logger.error(f"Failed to resolve symlink target: {resolved_path}")
                    except UnicodeDecodeError:
                        pass  # Binary file, not a symlink

                if local_path.suffix == ".gz":
                    try:
                        return gzip.decompress(content)
                    except Exception as e:
                        logger.error(f"Failed to decompress {local_path}: {e}")
                return content

        # Fallback to HTTP download
        if download_url.startswith("local://"):
            logger.error(f"Local file not found or LFS pull failed: {download_url}")
            return b""

        res = await self.client.get(download_url)
        res.raise_for_status()
        content = res.content
        if download_url.split("?")[0].endswith(".gz"):
            try:
                return gzip.decompress(content)
            except Exception:
                pass
        return content

    async def _git_lfs_pull(self, relative_path: str) -> bool:
        """Pull a specific file via git lfs."""
        try:
            cmd = ["git", "lfs", "pull", "--include", relative_path]
            logger.info(f"Running: {' '.join(cmd)} in {config.PROBLEMS_REPO_DIR}")
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=config.PROBLEMS_REPO_DIR,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                return True
            else:
                logger.error(f"git lfs pull failed: {stderr.decode()}")
                return False
        except Exception as e:
            logger.error(f"Error executing git lfs pull: {e}")
            return False

    async def ingest_from_github(self, github_url: str, force_sync: bool = False) -> Optional[Problem]:
        """Fetch, parse, and store a problem from GitHub."""
        logger.info(f"Ingesting from GitHub: {github_url}")

        parsed = self.parse_github_url(github_url)

        # Early up-to-date check: compare last_synced_at against the single sentinel
        # file mtime (the directory itself) rather than rglob-ing every file.
        slug = self._generate_slug(parsed["path"].split("/")[-1] if parsed["path"] else parsed["repo"])
        result = await self.session.execute(select(Problem).where(Problem.slug == slug))
        existing_problem = result.scalar_one_or_none()

        if existing_problem and not force_sync:
            if config.PROBLEMS_REPO_DIR.exists():
                local_dir = config.PROBLEMS_REPO_DIR / parsed["path"]
                if local_dir.exists():
                    # Use the directory mtime as a cheap sentinel instead of rglob
                    dir_mtime = local_dir.stat().st_mtime
                    if existing_problem.last_synced_at and existing_problem.last_synced_at.timestamp() >= dir_mtime:
                        logger.info(f"Problem '{existing_problem.name}' is up to date, skipping.")
                        return existing_problem

        contents = await self.fetch_contents(parsed["owner"], parsed["repo"], parsed["path"], parsed["ref"])

        # Find the statement (PDF or MD)
        pdf_url = None
        pdf_name = None
        statement_md_url = None

        for item in contents:
            if item["type"] == "file":
                if item["name"].lower().endswith(".pdf"):
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
            logger.info(f"Using Markdown statement: {statement_md_url}")
            md_bytes = await self.download_file(statement_md_url)
            description_md = md_bytes.decode("utf-8")
            if not pdf_name:
                pdf_name = parsed["path"].split("/")[-1] if parsed["path"] else parsed["repo"]
        elif pdf_url:
            logger.info(f"Downloading PDF statement: {pdf_url}")
            pdf_bytes = await self.download_file(pdf_url)

            temp_pdf = Path(f"temp_statement_{uuid.uuid4().hex}.pdf")
            temp_pdf.write_bytes(pdf_bytes)

            try:
                import hashlib
                pdf_hash = hashlib.md5(pdf_bytes).hexdigest()
                cache_dir = config.PROBLEMS_DIR / ".cache" / "pdf_markdown"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file = cache_dir / f"{pdf_hash}.md"

                if cache_file.exists():
                    logger.info(f"PDF cache hit ({pdf_hash[:8]})")
                    description_md = cache_file.read_text(encoding="utf-8")
                else:
                    logger.info("Converting PDF to Markdown...")
                    description_md = pymupdf4llm.to_markdown(str(temp_pdf))

                    if description_md:
                        description_md = re.sub(r'\n{3,}', '\n\n', description_md)
                        description_md = re.sub(r'\n\s*\d+\s*\n', '\n', description_md)
                        cache_file.write_text(description_md, encoding="utf-8")
                        logger.info(f"PDF converted and cached ({pdf_hash[:8]})")
            finally:
                if temp_pdf.exists():
                    try:
                        temp_pdf.unlink()
                    except Exception as e:
                        logger.error(f"Failed to delete temp PDF {temp_pdf}: {e}")

        if not description_md:
            logger.error(f"No statement found at {github_url}")
            return None

        # Create/update problem record
        problem_name = (pdf_name or "Unknown Problem").replace(".pdf", "").replace("-", " ").title()
        slug = self._generate_slug(problem_name)

        result = await self.session.execute(select(Problem).where(Problem.slug == slug))
        existing_problem = result.scalar_one_or_none()

        if existing_problem:
            logger.info(f"Updating existing problem '{slug}'")
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

        problem.last_synced_at = datetime.now()
        await self.session.flush()
        await self.session.commit()

        logger.info(f"Problem '{problem.name}' (ID: {problem.id}) saved.")

        # Auto-sync tests from local repo if available
        if config.PROBLEMS_REPO_DIR.exists():
            await self.sync_tests(problem.id)

        return problem

    async def sync_tests(self, problem_id: int) -> int:
        """Download/copy tests for an existing problem, skipping files already on disk."""
        result = await self.session.execute(select(Problem).where(Problem.id == problem_id))
        problem = result.scalar_one_or_none()

        if not problem or not problem.source_url:
            logger.error(f"Problem {problem_id} not found or has no source URL.")
            return 0

        parsed = self.parse_github_url(problem.source_url)

        # If tests are already on disk and the DB says they're downloaded, skip entirely.
        # A force re-sync can be triggered by resetting tests_downloaded via the API.
        tests_dir = get_problem_tests_dir(problem.id)
        existing_in_files = set(p.stem for p in tests_dir.glob("*.in")) if tests_dir.exists() else set()

        # If the DB already marks tests as downloaded and files exist, skip entirely.
        # Resetting tests_downloaded=False via the API triggers a full re-sync.
        if problem.tests_downloaded and existing_in_files:
            logger.info(f"Tests for problem {problem_id} already cached ({len(existing_in_files)} tests), skipping sync.")
            return problem.test_count

        # Fresh sync: clear any stale files left by previous failed/partial runs so
        # we don't mistake leftover files from another problem for cached content.
        is_resuming = problem.tests_downloaded  # True only if DB says done but files missing
        if not is_resuming and tests_dir.exists():
            for f in tests_dir.glob("*"):
                if f.is_file():
                    f.unlink()

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

        async def collect_files(current_path: str) -> List[Dict[str, Any]]:
            all_files = []
            try:
                items = await self.fetch_contents(parsed["owner"], parsed["repo"], current_path, parsed["ref"])
            except Exception as e:
                logger.warning(f"Failed to fetch contents for '{current_path}': {e}")
                return []
            for item in items:
                if item["type"] == "file":
                    all_files.append(item)
                elif item["type"] == "dir" and item["name"] not in (".", "..", "solution", "statement", "checker", "grader"):
                    all_files.extend(await collect_files(item["path"]))
            return all_files

        test_files = []
        for path in search_paths:
            try:
                files = await collect_files(path)
                if any(f["name"].endswith(".in") or f["name"].endswith(".in.gz") for f in files):
                    test_files = files
                    logger.info(f"Found test files in '{path}'")
                    break
            except Exception as e:
                logger.debug(f"Path '{path}' not usable: {e}")
                continue

        if not test_files:
            test_files = await collect_files(parsed["path"])
            if not any(f["name"].endswith(".in") or f["name"].endswith(".in.gz") for f in test_files):
                test_files = []

        if not test_files:
            logger.error(f"No .in test files found for problem {problem_id}")
            return 0

        tests_dir.mkdir(parents=True, exist_ok=True)

        in_files: Dict[str, str] = {}
        out_files: Dict[str, str] = {}

        for item in test_files:
            name = item["name"]
            mapping_name = name[:-3] if name.endswith(".gz") else name

            if mapping_name.endswith(".in"):
                base = mapping_name[:-3]
                in_files[base] = item["download_url"]
            elif mapping_name.endswith(".out") or mapping_name.endswith(".sol") or mapping_name.endswith(".ans"):
                base = mapping_name.rsplit(".", 1)[0]
                out_files[base] = item["download_url"]

        count = 0
        skipped = 0
        bases = sorted(k for k in in_files if k in out_files)
        logger.info(f"Syncing {len(bases)} test case(s) for problem {problem_id}...")

        for base in bases:
            in_dest = tests_dir / f"{count}.in"
            out_dest = tests_dir / f"{count}.out"

            # Skip individual files only when resuming a partial sync, not on a fresh sync.
            if is_resuming and in_dest.exists() and out_dest.exists() and in_dest.stat().st_size > 0 and out_dest.stat().st_size > 0:
                skipped += 1
                count += 1
                continue

            in_content = await self.download_file(in_files[base])
            out_content = await self.download_file(out_files[base])

            if not in_content or not out_content:
                logger.warning(f"Skipping test '{base}': empty content")
                continue

            if count == 0:
                try:
                    problem.sample_input = in_content.decode("utf-8")[:2000]
                    problem.sample_output = out_content.decode("utf-8")[:2000]
                except UnicodeDecodeError:
                    logger.warning(f"Could not decode sample input/output as UTF-8 for problem {problem_id}")

            in_dest.write_bytes(in_content)
            out_dest.write_bytes(out_content)
            count += 1

        if skipped:
            logger.info(f"Skipped {skipped} already-cached test file(s).")

        problem.test_count = count
        problem.tests_downloaded = True
        problem.last_synced_at = datetime.now()
        await self.session.commit()

        logger.info(f"Synced {count} test(s) for problem {problem_id} ({skipped} from cache).")
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
        local_base = config.PROBLEMS_REPO_DIR / "train" / problem_id_str
        if not (local_base.exists() and local_base.is_dir()):
            local_base = config.PROBLEMS_REPO_DIR / problem_id_str

        if local_base.exists() and local_base.is_dir():
            logger.info(f"Loading APPS problem {problem_id_str} from local cache")
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

        # Fallback to GitHub raw
        base_url = f"{APPS_GITHUB_BASE_URL}/{problem_id_str}"

        try:
            desc_res = await self.client.get(f"{base_url}/question.txt")
            if desc_res.status_code != 200:
                logger.error(f"Failed to fetch question.txt for problem {problem_id_str}")
                return None

            description = desc_res.text

            io_res = await self.client.get(f"{base_url}/input_output.json")
            if io_res.status_code != 200:
                logger.error(f"Failed to fetch input_output.json for problem {problem_id_str}")
                return None

            io_data = io_res.json()

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
        logger.info(f"Ingesting APPS problem {problem_id_str}")

        data = await self.fetch_problem_data(problem_id_str)
        if not data:
            return None

        slug = self._generate_slug(data["name"])

        result = await self.session.execute(select(Problem).where(Problem.slug == slug))
        existing_problem = result.scalar_one_or_none()

        if existing_problem:
            logger.info(f"Updating existing problem '{slug}'")
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

        await self.session.flush()

        tests_dir = get_problem_tests_dir(problem.id)
        tests_dir.mkdir(parents=True, exist_ok=True)

        # Skip re-writing tests if they already exist on disk
        existing_count = sum(1 for _ in tests_dir.glob("*.in"))
        inputs = data["io"].get("inputs", [])
        outputs = data["io"].get("outputs", [])

        if existing_count == len(inputs) and existing_count > 0:
            logger.info(f"Test files for '{slug}' already on disk ({existing_count} tests), skipping write.")
            problem.test_count = existing_count
            await self.session.commit()
            return problem

        # Clear and rewrite
        for f in tests_dir.glob("*"):
            if f.is_file():
                f.unlink()

        test_count = 0
        for i, (inp, out) in enumerate(zip(inputs, outputs)):
            if isinstance(inp, list):
                inp = "\n".join(map(str, inp))
            if isinstance(out, list):
                out = "\n".join(map(str, out))

            if i == 0:
                problem.sample_input = str(inp)
                problem.sample_output = str(out)

            (tests_dir / f"{i}.in").write_text(str(inp), encoding="utf-8")
            (tests_dir / f"{i}.out").write_text(str(out), encoding="utf-8")
            test_count += 1

        problem.test_count = test_count
        await self.session.commit()

        logger.info(f"Ingested '{problem.name}' (ID: {problem.id}) with {test_count} tests.")
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
    import sys

    start_id = 0
    count = 1

    if len(sys.argv) > 1:
        start_id = int(sys.argv[1])
    if len(sys.argv) > 2:
        count = int(sys.argv[2])

    asyncio.run(ingest_batch(start_id, count))