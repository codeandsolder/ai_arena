"""
Configuration module for the AI Optimization Arena backend.
"""
import os
from pathlib import Path
from typing import List, Set

# Base paths
BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
RUNS_DIR = DATA_DIR / "runs"
PROBLEMS_DIR = DATA_DIR / "problems"
PROBLEMS_REPO_DIR = DATA_DIR / "problems_repo"

# Problems Repository
PROBLEMS_REPO_URL = os.environ.get("PROBLEMS_REPO_URL", "https://github.com/austrian-olympiad-informatics/ioi-tasks.git")

# Database configuration
DATABASE_PATH = DATA_DIR / "arena.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_PATH}"

# API Configuration
OPENROUTER_API_ENDPOINT = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")

# Default run configuration
DEFAULT_MODELS = [
    "anthropic/claude-3.5-sonnet",
    "anthropic/claude-3-opus",
    "openai/gpt-4o",
    "google/gemini-1.5-pro",
    "meta-llama/llama-3.1-70b-instruct",
]

DEFAULT_MAX_ROUNDS = 5
DEFAULT_TIME_LIMIT_MS = 2000
DEFAULT_MEMORY_LIMIT_MB = 256

# Scoring weights (must sum to 1.0)
SCORING_WEIGHTS = {
    "correctness": 0.6,  # Primary: all tests must pass
    "speed": 0.25,       # Secondary: fastest execution time
    "memory": 0.15,      # Tertiary: lowest memory usage
}

# Execution options
DEFAULT_EXECUTION_OPTIONS = {
    "enable_cache_simulation": True,
    "enable_branch_prediction": True,
    "measure_memory_peak": True,
    "timeout_buffer_ms": 100,  # Extra time buffer for measurement overhead
}

# Compiler whitelist
ALLOWED_COMPILERS: Set[str] = {
    "g++-12",
    "g++-13",
    "g++-14",
    "clang++-16",
    "clang++-17",
    "clang++-18",
}

ALLOWED_COMPILER_FLAGS: Set[str] = {
    # Optimization levels
    "-O0",
    "-O1",
    "-O2",
    "-O3",
    "-Ofast",
    "-Os",
    "-Oz",
    # C++ standards
    "-std=c++17",
    "-std=c++20",
    "-std=c++23",
    # Architecture tuning
    "-march=native",
    "-mtune=native",
    # Optimization flags
    "-funroll-loops",
    "-ffast-math",
    "-flto",
    "-fomit-frame-pointer",
    "-finline-functions",
    "-fprefetch-loop-arrays",
    # Other
    "-DNDEBUG",
    "-pipe",
}

# Default prompt templates
SYSTEM_PROMPT_TEMPLATE = """\
You are an expert C++ competitive programmer competing in an optimization arena. \
Your goal is to write the fastest, most memory-efficient correct solution possible. \
You have deep knowledge of algorithms, data structures, compiler optimizations, \
cache-friendly code, SIMD intrinsics, bit manipulation, and low-level performance techniques.

You must respond with a JSON object in the exact format specified. \
Do not include any text outside the JSON object.\
"""

ROUND_1_USER_PROMPT_TEMPLATE = """\
Solve the following problem. Focus first on correctness, then optimize for speed.

## Problem
{problem_description}

## Constraints
- Time limit: {time_limit_ms}ms per test case
- Memory limit: {memory_limit_mb}MB
- Read from stdin, write to stdout
- No file I/O, no system calls, no inline assembly

## Response Format
{response_format}
"""

RESPONSE_FORMAT_TEMPLATE = """\
```json
{"compiler": "g++-14", "flags": "-O2 -std=c++20", "code": "...your C++ code...", "explanation": "brief explanation of your approach and optimizations"}
```
"""


def ensure_directories() -> None:
    """
    Ensure all required data directories exist.
    Creates directories recursively if they don't exist.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    PROBLEMS_DIR.mkdir(parents=True, exist_ok=True)
    ensure_problems_repo()


def ensure_problems_repo() -> None:
    """
    Ensure the problems repository is cloned and up to date.
    """
    import subprocess
    import shutil

    if not PROBLEMS_REPO_DIR.exists():
        print(f"Cloning problems repository from {PROBLEMS_REPO_URL}...")
        try:
            subprocess.run(
                ["git", "clone", PROBLEMS_REPO_URL, str(PROBLEMS_REPO_DIR)],
                check=True,
                capture_output=True,
                text=True
            )
            print("Successfully cloned problems repository. Pulling LFS files...")
            subprocess.run(
                ["git", "-C", str(PROBLEMS_REPO_DIR), "lfs", "pull"],
                check=True,
                capture_output=True,
                text=True
            )
            print("Successfully pulled LFS files.")
        except subprocess.CalledProcessError as e:
            print(f"Failed to setup problems repository: {e.stderr}")
    else:
        # Check if it's a git repo
        if (PROBLEMS_REPO_DIR / ".git").exists():
            print("Updating problems repository...")
            try:
                subprocess.run(
                    ["git", "-C", str(PROBLEMS_REPO_DIR), "pull"],
                    check=True,
                    capture_output=True,
                    text=True
                )
                print("Successfully updated problems repository. Pulling LFS files...")
                subprocess.run(
                    ["git", "-C", str(PROBLEMS_REPO_DIR), "lfs", "pull"],
                    check=True,
                    capture_output=True,
                    text=True
                )
                print("Successfully updated LFS files.")
            except subprocess.CalledProcessError as e:
                print(f"Failed to update problems repository: {e.stderr}")
        else:
            print(f"Directory {PROBLEMS_REPO_DIR} exists but is not a git repository.")


def validate_compiler(compiler: str) -> bool:
    """Check if a compiler is in the whitelist."""
    return compiler in ALLOWED_COMPILERS


def validate_compiler_flags(flags: str) -> bool:
    """
    Validate that all flags in the given string are in the whitelist.
    Returns True if all flags are allowed, False otherwise.
    """
    import shlex
    try:
        flag_list = shlex.split(flags)
    except ValueError:
        return False
    
    for flag in flag_list:
        # Skip empty strings
        if not flag:
            continue
        # Check if flag is in allowed set
        if flag not in ALLOWED_COMPILER_FLAGS:
            return False
    return True


def get_allowed_flags_list() -> List[str]:
    """Return a sorted list of allowed compiler flags."""
    return sorted(ALLOWED_COMPILER_FLAGS)
