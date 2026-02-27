import pytest
from pathlib import Path
from backend.sandbox.benchmark import _find_test_cases

def test_find_test_cases_starts_at_zero(tmp_path):
    """Test that _find_test_cases finds test cases starting from index 0."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    
    # Create test case 0
    (tests_dir / "0.in").write_text("in0")
    (tests_dir / "0.out").write_text("out0")
    
    # Create test case 1
    (tests_dir / "1.in").write_text("in1")
    (tests_dir / "1.out").write_text("out1")
    
    test_cases = _find_test_cases(tests_dir)
    
    assert len(test_cases) == 2
    assert test_cases[0][0].name == "0.in"
    assert test_cases[0][1].name == "0.out"
    assert test_cases[1][0].name == "1.in"
    assert test_cases[1][1].name == "1.out"

def test_find_test_cases_mixed_patterns(tmp_path):
    """Test that _find_test_cases handles mixed patterns (though usually it stops at first mismatch)."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    
    # Pattern: {}.in / {}.out
    (tests_dir / "0.in").write_text("in0")
    (tests_dir / "0.out").write_text("out0")
    
    # Gap in sequence
    (tests_dir / "2.in").write_text("in2")
    (tests_dir / "2.out").write_text("out2")
    
    test_cases = _find_test_cases(tests_dir)
    
    # Current implementation stops at first missing index for a pattern
    assert len(test_cases) == 1
    assert test_cases[0][0].name == "0.in"

def test_find_test_cases_input_output_pattern(tmp_path):
    """Test that _find_test_cases finds input_N.txt / output_N.txt."""
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    
    (tests_dir / "input_0.txt").write_text("in0")
    (tests_dir / "output_0.txt").write_text("out0")
    
    test_cases = _find_test_cases(tests_dir)
    
    assert len(test_cases) == 1
    assert test_cases[0][0].name == "input_0.txt"
    assert test_cases[0][1].name == "output_0.txt"
