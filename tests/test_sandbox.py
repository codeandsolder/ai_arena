import pytest
from pathlib import Path
from unittest.mock import patch

from backend.sandbox.benchmark import _find_test_cases
from backend.sandbox.compiler import (
    perform_safety_check,
    validate_compiler,
    validate_flags,
    DEFAULT_COMPILER,
)
from backend.api.verify_example import _find_example_solution, _parse_github_url


# =============================================================================
# _find_test_cases
# =============================================================================

def test_find_test_cases_starts_at_zero(tmp_path):
    """Finds .in/.out pairs starting from index 0."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "0.in").write_text("in0")
    (d / "0.out").write_text("out0")
    (d / "1.in").write_text("in1")
    (d / "1.out").write_text("out1")

    cases = _find_test_cases(d)
    assert len(cases) == 2
    assert cases[0][0].name == "0.in"
    assert cases[0][1].name == "0.out"
    assert cases[1][0].name == "1.in"
    assert cases[1][1].name == "1.out"


def test_find_test_cases_stops_at_gap(tmp_path):
    """Stops at the first missing index in a sequence (gap at index 1)."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "0.in").write_text("in0")
    (d / "0.out").write_text("out0")
    (d / "2.in").write_text("in2")
    (d / "2.out").write_text("out2")

    cases = _find_test_cases(d)
    assert len(cases) == 1
    assert cases[0][0].name == "0.in"


def test_find_test_cases_input_output_pattern(tmp_path):
    """Finds input_N.txt / output_N.txt pattern."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "input_0.txt").write_text("in0")
    (d / "output_0.txt").write_text("out0")

    cases = _find_test_cases(d)
    assert len(cases) == 1
    assert cases[0][0].name == "input_0.txt"
    assert cases[0][1].name == "output_0.txt"


def test_find_test_cases_empty_dir(tmp_path):
    """Returns empty list when no test files exist."""
    d = tmp_path / "tests"
    d.mkdir()
    assert _find_test_cases(d) == []


def test_find_test_cases_missing_pair(tmp_path):
    """Ignores .in file with no matching .out."""
    d = tmp_path / "tests"
    d.mkdir()
    (d / "0.in").write_text("in0")
    # No 0.out

    cases = _find_test_cases(d)
    assert len(cases) == 0


def test_find_test_cases_nonexistent_dir(tmp_path):
    """Returns empty list for a directory that doesn't exist (BenchmarkError raised by caller)."""
    # _find_test_cases itself returns [] — benchmark_solution raises BenchmarkError on empty result.
    result = _find_test_cases(tmp_path / "nonexistent")
    assert result == []


# =============================================================================
# _find_example_solution
# =============================================================================

def test_find_example_solution_in_solution_subdir(tmp_path):
    """Finds .cpp in 'solution/' subdirectory."""
    sol_dir = tmp_path / "solution"
    sol_dir.mkdir()
    cpp = sol_dir / "solution.cpp"
    cpp.write_text("int main(){}")

    result = _find_example_solution(tmp_path)
    assert result == cpp


def test_find_example_solution_prefers_named_keywords(tmp_path):
    """Prefers files named sol*/correct*/ac* over generic names."""
    sol_dir = tmp_path / "solution"
    sol_dir.mkdir()
    (sol_dir / "brute.cpp").write_text("// brute")
    preferred = sol_dir / "sol_ac.cpp"
    preferred.write_text("// ac")

    result = _find_example_solution(tmp_path)
    assert result == preferred


def test_find_example_solution_falls_back_to_root(tmp_path):
    """Falls back to root directory if no standard subdir exists."""
    cpp = tmp_path / "main.cpp"
    cpp.write_text("int main(){}")

    result = _find_example_solution(tmp_path)
    assert result == cpp


def test_find_example_solution_searches_sol_dir(tmp_path):
    """Finds .cpp in 'sol/' when 'solution/' is absent."""
    sol_dir = tmp_path / "sol"
    sol_dir.mkdir()
    cpp = sol_dir / "fast.cpp"
    cpp.write_text("int main(){}")

    result = _find_example_solution(tmp_path)
    assert result == cpp


def test_find_example_solution_returns_none_when_no_cpp(tmp_path):
    """Returns None when no .cpp file exists anywhere."""
    (tmp_path / "solution").mkdir()
    (tmp_path / "solution" / "readme.txt").write_text("no cpp here")

    result = _find_example_solution(tmp_path)
    assert result is None


def test_find_example_solution_prefers_subdir_over_root(tmp_path):
    """solution/ subdirectory is searched before the root."""
    root_cpp = tmp_path / "other.cpp"
    root_cpp.write_text("// root")
    sol_dir = tmp_path / "solution"
    sol_dir.mkdir()
    sol_cpp = sol_dir / "solution.cpp"
    sol_cpp.write_text("// subdir")

    result = _find_example_solution(tmp_path)
    assert result == sol_cpp


# =============================================================================
# _parse_github_url
# =============================================================================

def test_parse_github_url_tree(dummy=None):
    """Parses standard tree URL."""
    result = _parse_github_url(
        "https://github.com/owner/repo/tree/main/path/to/problem"
    )
    assert result["owner"] == "owner"
    assert result["repo"] == "repo"
    assert result["ref"] == "main"
    assert result["path"] == "path/to/problem"


def test_parse_github_url_repo_only():
    """Parses bare repo URL, defaults ref to main and path to empty."""
    result = _parse_github_url("https://github.com/owner/repo")
    assert result["owner"] == "owner"
    assert result["repo"] == "repo"
    assert result["ref"] == "main"
    assert result["path"] == ""


def test_parse_github_url_blob():
    """Parses blob URL (file link)."""
    result = _parse_github_url(
        "https://github.com/owner/repo/blob/main/path/file.cpp"
    )
    assert result["path"] == "path/file.cpp"


def test_parse_github_url_invalid():
    """Raises HTTPException for unrecognisable URLs."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _parse_github_url("https://gitlab.com/owner/repo")
    assert exc_info.value.status_code == 400


# =============================================================================
# Compiler safety checks
# =============================================================================

def test_safety_check_clean_code():
    """Clean competitive programming code passes all checks."""
    code = """
#include <iostream>
#include <vector>
#include <algorithm>
int main() {
    int n; std::cin >> n;
    std::cout << n << std::endl;
}
"""
    assert perform_safety_check(code) == []


def test_safety_check_detects_system_call():
    """system() call is flagged."""
    code = '#include<cstdlib>\nint main(){ system("rm -rf /"); }'
    violations = perform_safety_check(code)
    assert any("system()" in v for v in violations)


def test_safety_check_detects_fstream():
    """<fstream> include is flagged."""
    code = "#include <fstream>\nint main(){}"
    violations = perform_safety_check(code)
    assert any("fstream" in v for v in violations)


def test_safety_check_detects_inline_asm():
    """Inline assembly is flagged."""
    code = 'int main(){ __asm__("nop"); }'
    violations = perform_safety_check(code)
    assert any("assembly" in v.lower() for v in violations)


def test_safety_check_detects_socket():
    """socket() call is flagged."""
    code = "#include<sys/socket.h>\nint main(){ int s = socket(AF_INET,SOCK_STREAM,0); }"
    violations = perform_safety_check(code)
    assert any("socket()" in v for v in violations)


# =============================================================================
# Compiler / flag validation
# =============================================================================

def test_validate_compiler_whitelisted():
    """Whitelisted compiler passes through unchanged."""
    assert validate_compiler("g++-14") == "g++-14"
    assert validate_compiler("clang++-17") == "clang++-17"


def test_validate_compiler_unknown_falls_back():
    """Unknown compiler falls back to DEFAULT_COMPILER."""
    result = validate_compiler("evil-compiler")
    assert result == DEFAULT_COMPILER


def test_validate_flags_removes_unknown():
    """Unknown flags are stripped; known flags pass through."""
    flags = ["-O2", "-std=c++17", "--inject-evil", "-Wall"]
    result = validate_flags(flags)
    assert "-O2" in result
    assert "-std=c++17" in result
    assert "-Wall" in result
    assert "--inject-evil" not in result


def test_validate_flags_allows_safe_defines():
    """Simple -D macro definitions are permitted."""
    flags = ["-DNDEBUG", "-DLOCAL=1", "-DMAX_N=100000"]
    result = validate_flags(flags)
    assert flags == result


def test_validate_flags_blocks_shell_injection_in_define():
    """-D with shell metacharacters is rejected."""
    flags = ["-DFOO=bar;rm -rf /"]
    result = validate_flags(flags)
    assert result == []


def test_validate_flags_empty():
    """Empty flag list returns empty list."""
    assert validate_flags([]) == []