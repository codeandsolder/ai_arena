import yaml
import re
from pathlib import Path
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class PermissiveLoader(yaml.SafeLoader):
    """YAML loader that ignores unknown tags."""
    def ignore_unknown(self, node):
        return None

PermissiveLoader.add_multi_constructor('!', lambda loader, suffix, node: None)

class TaskConfig:
    def __init__(self, data: Dict[str, Any], problem_dir: Path):
        self.data = data
        self.problem_dir = problem_dir

    @property
    def name(self) -> Optional[str]:
        return self.data.get("name")

    @property
    def long_name(self) -> Optional[str]:
        return self.data.get("long_name")

    @property
    def time_limit(self) -> Optional[float]:
        tl = self.data.get("time_limit")
        if tl and isinstance(tl, str) and tl.endswith("s"):
            try:
                return float(tl[:-1])
            except ValueError:
                return None
        return tl

    @property
    def memory_limit(self) -> Optional[int]:
        ml = self.data.get("memory_limit")
        if ml and isinstance(ml, str):
            if ml.endswith("MiB"):
                try:
                    return int(ml[:-3])
                except ValueError:
                    return None
            if ml.endswith("MB"):
                try:
                    return int(ml[:-2])
                except ValueError:
                    return None
        return ml

    def get_grader_files(self) -> List[Path]:
        """
        Locate grader files.
        Checks for a 'grader' key in task.yaml, otherwise looks for a 'grader' directory.
        """
        grader_paths = []
        
        # 1. Check if explicitly defined in task.yaml
        grader_info = self.data.get("grader")
        if grader_info:
            if isinstance(grader_info, str):
                p = self.problem_dir / grader_info
                if p.exists():
                    grader_paths.append(p)
            elif isinstance(grader_info, dict):
                for key in ["public", "secret", "common"]:
                    rel_path = grader_info.get(key)
                    if rel_path:
                        p = self.problem_dir / rel_path
                        if p.exists():
                            grader_paths.append(p)
        
        # 2. Fallback: look for a 'grader' directory (legacy/standard behavior)
        grader_dir = self.problem_dir / "grader"
        if grader_dir.is_dir():
            for f in grader_dir.glob("*"):
                if f.is_file() and f not in grader_paths:
                    grader_paths.append(f)
                    
        return grader_paths

    def get_solutions(self) -> List[Path]:
        """
        Locate solution files.
        Checks for a 'solutions' key in task.yaml.
        """
        solution_paths = []
        solutions = self.data.get("solutions")
        if isinstance(solutions, list):
            for sol in solutions:
                if isinstance(sol, dict):
                    rel_path = sol.get("path")
                    if rel_path:
                        p = self.problem_dir / rel_path
                        if p.exists():
                            solution_paths.append(p)
                elif isinstance(sol, str):
                    p = self.problem_dir / sol
                    if p.exists():
                        solution_paths.append(p)
        
        return solution_paths

    def get_correct_solution(self) -> Optional[Path]:
        """
        Try to find the 'correct' solution from task.yaml.
        """
        solutions = self.data.get("solutions")
        if isinstance(solutions, list):
            # Prefer ones marked as 'correct' or 'ac'
            for sol in solutions:
                if isinstance(sol, dict):
                    name = sol.get("name", "").lower()
                    if any(kw in name for kw in ["correct", "ac", "model", "reference"]):
                        rel_path = sol.get("path")
                        if rel_path:
                            p = self.problem_dir / rel_path
                            if p.exists():
                                return p
            # If none explicitly marked, take the first one if it exists
            for sol in solutions:
                if isinstance(sol, dict):
                    rel_path = sol.get("path")
                else:
                    rel_path = sol
                if rel_path:
                    p = self.problem_dir / rel_path
                    if p.exists():
                        return p
        return None

    def get_subtasks(self) -> List[Dict[str, Any]]:
        """
        Extract subtasks definition including scores and testcase patterns.
        Returns a list of dictionaries with 'score' and 'testcases' keys.
        """
        subtasks_data = self.data.get("subtasks", [])
        if not isinstance(subtasks_data, list):
            return []
            
        parsed_subtasks = []
        for st in subtasks_data:
            if not isinstance(st, dict):
                continue
                
            score = st.get("score", 0.0)
            try:
                score = float(score)
            except (ValueError, TypeError):
                score = 0.0
                
            testcases = st.get("testcases", [])
            patterns = []
            if isinstance(testcases, list):
                for tc in testcases:
                    if isinstance(tc, dict):
                        inp = tc.get("input")
                        outp = tc.get("output")
                        if inp and outp:
                            patterns.append({"input": inp, "output": outp})
                            
            if patterns:
                parsed_subtasks.append({
                    "score": score,
                    "patterns": patterns
                })
                
        return parsed_subtasks

    def get_test_patterns(self) -> List[Dict[str, str]]:
        """
        Extract test case patterns from subtasks in task.yaml.
        Returns a list of dictionaries with 'input' and 'output' keys.
        """
        patterns = []
        subtasks = self.data.get("subtasks")
        if isinstance(subtasks, list):
            for subtask in subtasks:
                if isinstance(subtask, dict):
                    testcases = subtask.get("testcases")
                    if isinstance(testcases, list):
                        for tc in testcases:
                            if isinstance(tc, dict):
                                inp = tc.get("input")
                                outp = tc.get("output")
                                if inp and outp:
                                    patterns.append({"input": inp, "output": outp})
        return patterns

    def get_statement_file(self, lang: str = "en") -> Optional[Path]:
        """
        Locate the statement file for a given language.
        """
        # 1. Check 'statements' dictionary for specific language
        statements = self.data.get("statements")
        if isinstance(statements, dict):
            rel_path = statements.get(lang)
            if rel_path:
                p = self.problem_dir / rel_path
                if p.exists():
                    return p
        
        # 2. Check for a single 'statement' key (common in some formats)
        statement = self.data.get("statement")
        if isinstance(statement, str):
            p = self.problem_dir / statement
            if p.exists():
                return p
                
        return None

    def get_test_submissions(self) -> List[str]:
        """
        Extract test_submissions (and solutions as fallback) from task.yaml
        using regex to catch even commented lines.
        """
        submissions = []
        yaml_path = None
        for filename in ["task.yaml", "tasks.yaml", "task.yml", "tasks.yml"]:
            p = self.problem_dir / filename
            if p.exists():
                yaml_path = p
                break

        if not yaml_path:
            return []

        try:
            content = yaml_path.read_text(encoding="utf-8")

            # Look for test_submissions: or solutions: blocks
            # This captures the block until the next top-level key (start of line with [a-z])
            sections = list(re.finditer(r"^(test_submissions|solutions):\s*(.*?)(?=\n[a-z]|\Z)", content, re.DOTALL | re.MULTILINE))
            
            # Special case: if test_submissions: {} is present and empty, it's explicitly empty.
            # But the regex above might not catch it if it's on one line and the next line is a key.
            # Let's also check for explicit empty dict/list if no sections found.
            if not sections:
                if re.search(r"^test_submissions:\s*\{\}\s*$", content, re.MULTILINE):
                    return []

            for section in sections:
                block = section.group(2)
                # If it's just {}, it's explicitly empty
                if block.strip() == "{}":
                    continue

                # Extract all items, including commented ones
                # Handles:
                # - path/to/file.cpp
                # # - path/to/file.cpp
                # - {path: path/to/file.cpp, ...}
                # # - {path: path/to/file.cpp, ...}
                item_matches = re.findall(r"^\s*(?:#\s*)?-\s*(?:\{\s*path:\s*)?([^\s,\}]+)", block, re.MULTILINE)
                for item in item_matches:
                    # Clean up the path and check if it's a C++ file
                    path = item.strip().strip("'").strip('"')
                    if path.endswith(".cpp") and path not in submissions:
                        submissions.append(path)
        except Exception as e:
            logger.error(f"Error extracting test_submissions from {yaml_path}: {e}")

        return submissions

def parse_task_yaml(problem_dir: Path) -> Optional[TaskConfig]:
    """
    Find and parse task.yaml or task.yml in the problem directory.
    """
    for filename in ["task.yaml", "tasks.yaml", "task.yml", "tasks.yml"]:
        yaml_path = problem_dir / filename
        if yaml_path.exists():
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    data = yaml.load(f, Loader=PermissiveLoader)
                    if data:
                        return TaskConfig(data, problem_dir)
            except Exception as e:
                logger.error(f"Failed to parse {yaml_path}: {e}")
    return None
