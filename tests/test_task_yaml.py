import pytest
from pathlib import Path
import yaml
from backend.utils.task_yaml import TaskConfig, parse_task_yaml, PermissiveLoader

def test_permissive_loader():
    # Test that unknown tags are ignored
    yaml_data = "key: !unknown_tag value\nkey2: !another_tag value"
    parsed = yaml.load(yaml_data, Loader=PermissiveLoader)
    assert parsed == {"key": None, "key2": None}
    
    # Cover the ignore_unknown method
    loader = PermissiveLoader("")
    assert loader.ignore_unknown(None) is None

def test_task_config_properties(tmp_path):
    data = {
        "name": "test_task",
        "long_name": "Test Task Long",
        "time_limit": "2.5s",
        "memory_limit": "256MiB",
    }
    config = TaskConfig(data, tmp_path)
    
    assert config.name == "test_task"
    assert config.long_name == "Test Task Long"
    assert config.time_limit == 2.5
    assert config.memory_limit == 256
    
    # Test different memory formats and missing values
    config.data["memory_limit"] = "1024MB"
    assert config.memory_limit == 1024
    
    config.data["time_limit"] = 1.0
    assert config.time_limit == 1.0

    config.data["memory_limit"] = 512
    assert config.memory_limit == 512
    
    # Test invalid time_limit
    config.data["time_limit"] = "invalid_s"
    assert config.time_limit is None
    
    config.data["memory_limit"] = "invalidMiB"
    assert config.memory_limit is None

    config.data["memory_limit"] = "invalidMB"
    assert config.memory_limit is None

    # Missing
    config.data = {}
    assert config.name is None
    assert config.long_name is None
    assert config.time_limit is None
    assert config.memory_limit is None

def test_get_grader_files_explicit_str(tmp_path):
    grader_file = tmp_path / "my_grader.cpp"
    grader_file.touch()
    
    config = TaskConfig({"grader": "my_grader.cpp"}, tmp_path)
    assert config.get_grader_files() == [grader_file]

def test_get_grader_files_explicit_dict(tmp_path):
    public_grader = tmp_path / "public.cpp"
    secret_grader = tmp_path / "secret.cpp"
    public_grader.touch()
    secret_grader.touch()
    
    data = {
        "grader": {
            "public": "public.cpp",
            "secret": "secret.cpp",
            "common": "missing.cpp"
        }
    }
    config = TaskConfig(data, tmp_path)
    graders = config.get_grader_files()
    assert len(graders) == 2
    assert public_grader in graders
    assert secret_grader in graders

def test_get_grader_files_fallback_dir(tmp_path):
    grader_dir = tmp_path / "grader"
    grader_dir.mkdir()
    grader_file1 = grader_dir / "grader1.cpp"
    grader_file2 = grader_dir / "grader2.cpp"
    grader_file1.touch()
    grader_file2.touch()
    
    config = TaskConfig({}, tmp_path)
    graders = config.get_grader_files()
    assert len(graders) == 2
    assert grader_file1 in graders
    assert grader_file2 in graders

def test_get_solutions(tmp_path):
    sol1 = tmp_path / "sol1.cpp"
    sol2 = tmp_path / "sol2.cpp"
    sol3 = tmp_path / "missing.cpp"
    sol1.touch()
    sol2.touch()
    
    data = {
        "solutions": [
            {"path": "sol1.cpp"},
            "sol2.cpp",
            "missing.cpp",
            {"path": "missing2.cpp"}
        ]
    }
    config = TaskConfig(data, tmp_path)
    sols = config.get_solutions()
    assert len(sols) == 2
    assert sol1 in sols
    assert sol2 in sols

def test_get_correct_solution(tmp_path):
    correct_sol = tmp_path / "correct.cpp"
    ac_sol = tmp_path / "ac.cpp"
    other_sol = tmp_path / "other.cpp"
    correct_sol.touch()
    ac_sol.touch()
    other_sol.touch()
    
    # Explicit correct
    data1 = {
        "solutions": [
            {"name": "wrong", "path": "other.cpp"},
            {"name": "Correct Solution", "path": "correct.cpp"},
        ]
    }
    config1 = TaskConfig(data1, tmp_path)
    assert config1.get_correct_solution() == correct_sol

    # Explicit AC
    data2 = {
        "solutions": [
            {"name": "other", "path": "other.cpp"},
            {"name": "my ac sol", "path": "ac.cpp"},
        ]
    }
    config2 = TaskConfig(data2, tmp_path)
    assert config2.get_correct_solution() == ac_sol

    # Fallback to first existing
    data3 = {
        "solutions": [
            {"path": "missing.cpp"},
            "other.cpp",
            "ac.cpp"
        ]
    }
    config3 = TaskConfig(data3, tmp_path)
    assert config3.get_correct_solution() == other_sol

    # No solutions
    config4 = TaskConfig({}, tmp_path)
    assert config4.get_correct_solution() is None

def test_get_subtasks_and_patterns(tmp_path):
    data = {
        "subtasks": [
            {
                "score": "30",
                "testcases": [
                    {"input": "1.in", "output": "1.out"},
                    {"input": "2.in", "output": "2.out"}
                ]
            },
            {
                "score": 70.5,
                "testcases": [
                    {"input": "3.in"}  # Missing output
                ]
            },
            {
                "score": "invalid",
                "testcases": [
                    {"input": "4.in", "output": "4.out"}
                ]
            },
            "not a dict"
        ]
    }
    config = TaskConfig(data, tmp_path)
    subtasks = config.get_subtasks()
    
    assert len(subtasks) == 2
    assert subtasks[0]["score"] == 30.0
    assert len(subtasks[0]["patterns"]) == 2
    assert subtasks[0]["patterns"][0] == {"input": "1.in", "output": "1.out"}
    
    assert subtasks[1]["score"] == 0.0
    assert len(subtasks[1]["patterns"]) == 1
    assert subtasks[1]["patterns"][0] == {"input": "4.in", "output": "4.out"}

    # Also test get_test_patterns (similar logic but flat)
    patterns = config.get_test_patterns()
    assert len(patterns) == 3
    assert patterns[0] == {"input": "1.in", "output": "1.out"}
    assert patterns[1] == {"input": "2.in", "output": "2.out"}
    assert patterns[2] == {"input": "4.in", "output": "4.out"}

def test_get_subtasks_invalid(tmp_path):
    config = TaskConfig({"subtasks": "not a list"}, tmp_path)
    assert config.get_subtasks() == []

def test_get_statement_file(tmp_path):
    stmt_en = tmp_path / "en.pdf"
    stmt_pl = tmp_path / "pl.pdf"
    stmt_en.touch()
    
    data = {
        "statements": {
            "en": "en.pdf",
            "pl": "pl.pdf"
        }
    }
    config = TaskConfig(data, tmp_path)
    
    assert config.get_statement_file("en") == stmt_en
    assert config.get_statement_file("pl") is None
    assert config.get_statement_file("fr") is None

def test_get_statement_file_single(tmp_path):
    stmt = tmp_path / "problem.pdf"
    stmt.touch()
    
    data = {
        "statement": "problem.pdf"
    }
    config = TaskConfig(data, tmp_path)
    
    assert config.get_statement_file("en") == stmt
    assert config.get_statement_file("any") == stmt

def test_get_test_submissions(tmp_path):
    # Test file regex parsing logic
    yaml_file = tmp_path / "task.yaml"
    yaml_content = '''
name: test
test_submissions:
  - path/to/sol1.cpp
  # - path/to/sol2.cpp
  - {path: path/to/sol3.cpp}
  # - {path: path/to/sol4.cpp}
  - other.py
solutions:
  - sol5.cpp

other_key: value
'''
    yaml_file.write_text(yaml_content)
    
    config = TaskConfig({}, tmp_path)
    submissions = config.get_test_submissions()
    
    assert len(submissions) == 5
    assert "path/to/sol1.cpp" in submissions
    assert "path/to/sol2.cpp" in submissions
    assert "path/to/sol3.cpp" in submissions
    assert "path/to/sol4.cpp" in submissions
    assert "sol5.cpp" in submissions
    assert "other.py" not in submissions

def test_get_test_submissions_explicit_empty(tmp_path):
    yaml_file = tmp_path / "task.yaml"
    yaml_file.write_text("test_submissions: {}")
    config = TaskConfig({}, tmp_path)
    assert config.get_test_submissions() == []
    
    yaml_file.write_text("test_submissions:\n  {}\nnext: val")
    assert config.get_test_submissions() == []

def test_get_test_submissions_no_file(tmp_path):
    config = TaskConfig({}, tmp_path)
    assert config.get_test_submissions() == []

def test_get_test_submissions_error(tmp_path, caplog):
    yaml_file = tmp_path / "task.yaml"
    yaml_file.mkdir() # directory instead of file will cause error when read_text is called
    
    config = TaskConfig({}, tmp_path)
    submissions = config.get_test_submissions()
    assert submissions == []
    assert "Error extracting test_submissions" in caplog.text

from unittest.mock import patch

def test_get_test_submissions_mock_unreachable(tmp_path):
    yaml_file = tmp_path / "task.yaml"
    yaml_file.write_text("test_submissions: {}")
    config = TaskConfig({}, tmp_path)
    
    with patch("backend.utils.task_yaml.re.finditer", return_value=[]):
        assert config.get_test_submissions() == []

def test_parse_task_yaml(tmp_path):
    yaml_file = tmp_path / "task.yaml"
    yaml_file.write_text("name: test_task\ntime_limit: 1.0s")
    
    config = parse_task_yaml(tmp_path)
    assert config is not None
    assert config.name == "test_task"
    assert config.time_limit == 1.0

def test_parse_task_yaml_no_file(tmp_path):
    assert parse_task_yaml(tmp_path) is None

def test_parse_task_yaml_invalid_yaml(tmp_path, caplog):
    yaml_file = tmp_path / "task.yaml"
    yaml_file.write_text("name: : : invalid yaml")
    
    config = parse_task_yaml(tmp_path)
    assert config is None
    assert "Failed to parse" in caplog.text

def test_parse_task_yaml_empty_file(tmp_path):
    yaml_file = tmp_path / "task.yaml"
    yaml_file.write_text("")
    
    config = parse_task_yaml(tmp_path)
    assert config is None
