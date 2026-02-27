"""
Prompt templates for the AI Optimization Arena orchestrator.

This module contains all prompt templates used for LLM interactions,
including solution generation, error handling, and judging.
"""


# =============================================================================
# System Prompts
# =============================================================================

SYSTEM_PROMPT = """\
You are an expert C++ competitive programmer competing in an optimization arena. Your goal is to write the fastest, most memory-efficient correct solution possible. You have deep knowledge of algorithms, data structures, compiler optimizations, cache-friendly code, SIMD intrinsics, bit manipulation, and low-level performance techniques.

You must respond with a JSON object in the exact format specified. Do not include any text outside the JSON object.
"""

JUDGE_SYSTEM_PROMPT = """\
You are judging a code optimization competition. Analyze the submitted solutions and produce a concise, informative summary that will help competitors improve in the next round.
"""

SECURITY_SYSTEM_PROMPT = """\
You are an expert security auditor for C++ code running in a competitive programming sandbox.
Your job is to analyze code and compiler flags for malicious intent, security bypasses, or dangerous patterns.

You must ignore standard competitive programming patterns (like infinite loops or memory usage) unless they are clearly malicious attacks (like fork bombs).
Focus on:
1. System calls (exec, system, etc.)
2. Network access (socket, connect, etc.)
3. File system access outside standard I/O (opening random files, /etc/passwd, etc.)
4. Dangerous compiler flags (macro injection, linker flags, plugin loading)
5. Inline assembly that might try to bypass sandbox
6. Preprocessor abuse to hide malicious code

Respond with a JSON object in this exact format:
```json
{
  "safe": true,
  "risk_level": "LOW",
  "reason": "Code uses standard algorithms and safe flags."
}
```
"safe" should be false if risk_level is HIGH or MEDIUM.
"""


# =============================================================================
# Response Format Template
# =============================================================================

RESPONSE_FORMAT = """\
Respond with a JSON object in this exact format:
```json
{
  "compiler": "g++-14",
  "flags": "-O2 -std=c++20 -march=native",
  "code": "#include <bits/stdc++.h>\\nusing namespace std;\\n...",
  "explanation": "Brief explanation of your approach, algorithm choice, and optimizations used"
}
```

Requirements:
- "compiler": Must be one of: g++-12, g++-13, g++-14, clang++-16, clang++-17, clang++-18
- "flags": Compiler flags as a single string (space-separated)
- "code": Complete, compilable C++ source code with proper escaping for JSON
- "explanation": Concise description of your algorithm and optimizations

Important:
- Read from stdin and write to stdout
- No file I/O, no system calls, no inline assembly
- Ensure your solution handles all edge cases
- Optimize for both time and memory within the given limits
"""


# =============================================================================
# User Prompt Templates
# =============================================================================

ROUND_1_USER_TEMPLATE = """\
Solve the following optimization problem. Focus first on correctness, then aggressively optimize for speed and memory efficiency.

## Problem
{problem_description}

## Constraints
- Time limit: {time_limit_ms}ms per test case
- Memory limit: {memory_limit_mb}MB
- Read from stdin, write to stdout
- No file I/O, no system calls, no inline assembly

## Response Format
{response_format}

This is Round 1. Write the best solution you can. Consider using:
- Optimal algorithms with lowest time complexity
- Cache-friendly data structures and access patterns
- Compiler optimizations via appropriate flags
- Bit manipulation and SIMD where applicable
- Fast I/O techniques (ios::sync_with_stdio(false), etc.)
"""

ROUND_N_USER_TEMPLATE = """\
Solve the following optimization problem. Learn from the previous round's results to improve your solution.

## Problem
{problem_description}

## Constraints
- Time limit: {time_limit_ms}ms per test case
- Memory limit: {memory_limit_mb}MB
- Read from stdin, write to stdout
- No file I/O, no system calls, no inline assembly

## Previous Round Analysis
{previous_round_summary}

## Response Format
{response_format}

Use the analysis above to write an improved solution. Consider:
- What approaches worked well for other competitors?
- What optimizations can you apply based on the feedback?
- Can you use different algorithms or data structures?
- Are there micro-optimizations that could help?
"""

JUDGE_USER_TEMPLATE = """\
Analyze the following solutions from Round {round_number} and provide a summary to help competitors improve.

## Problem
{problem_description}

## Round Results
{round_results}

## Cumulative Standings
{cumulative_standings}

Please provide:
1. A brief overview of the different approaches used
2. What worked well and what didn't
3. Specific optimization techniques that showed promise
4. Suggestions for improvements in the next round
5. Any patterns or insights that could help competitors

Keep your summary concise but informative (300-500 words).
"""

ERROR_RETRY_TEMPLATE = """\
Your previous solution failed with the following error. Please fix it and resubmit.

## Error Details
{error_details}

## Your Previous Code
```cpp
{previous_code}
```

## Response Format
{response_format}

Please carefully fix the error and submit a corrected solution. Ensure:
- All syntax errors are resolved
- The code compiles with the specified compiler and flags
- Logic errors are corrected
- Edge cases are properly handled
"""

SECURITY_USER_TEMPLATE = """\
Analyze the following C++ code and compiler flags for security risks.

## Compiler Flags
{flags}

## Source Code
```cpp
{code}
```

Is this submission safe to compile and run in a sandboxed environment?
"""


# =============================================================================
# PromptFormatter Class
# =============================================================================

class PromptFormatter:
    """
    Formatter for generating prompts with variable substitution.
    
    This class provides methods to format all prompt types used in the
    AI Optimization Arena, handling variable substitution and ensuring
    proper formatting.
    """
    
    @staticmethod
    def get_system_prompt() -> str:
        """Get the base system prompt for solution generation."""
        return SYSTEM_PROMPT
    
    @staticmethod
    def get_judge_system_prompt() -> str:
        """Get the system prompt for the judge model."""
        return JUDGE_SYSTEM_PROMPT

    @staticmethod
    def get_security_system_prompt() -> str:
        """Get the system prompt for the security auditor."""
        return SECURITY_SYSTEM_PROMPT
    
    @staticmethod
    def get_response_format() -> str:
        """Get the JSON response format specification."""
        return RESPONSE_FORMAT
    
    @staticmethod
    def format_round_1_user(
        problem_description: str,
        time_limit_ms: int,
        memory_limit_mb: int
    ) -> str:
        """
        Format the first round user prompt.
        
        Args:
            problem_description: The problem description in markdown
            time_limit_ms: Time limit per test case in milliseconds
            memory_limit_mb: Memory limit in megabytes
            
        Returns:
            Formatted user prompt string
        """
        return ROUND_1_USER_TEMPLATE.format(
            problem_description=problem_description,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            response_format=RESPONSE_FORMAT
        )
    
    @staticmethod
    def format_round_n_user(
        problem_description: str,
        time_limit_ms: int,
        memory_limit_mb: int,
        previous_round_summary: str
    ) -> str:
        """
        Format the subsequent rounds user prompt.
        
        Args:
            problem_description: The problem description in markdown
            time_limit_ms: Time limit per test case in milliseconds
            memory_limit_mb: Memory limit in megabytes
            previous_round_summary: Summary from the previous round's judge
            
        Returns:
            Formatted user prompt string
        """
        return ROUND_N_USER_TEMPLATE.format(
            problem_description=problem_description,
            time_limit_ms=time_limit_ms,
            memory_limit_mb=memory_limit_mb,
            previous_round_summary=previous_round_summary,
            response_format=RESPONSE_FORMAT
        )
    
    @staticmethod
    def format_judge_user(
        problem_description: str,
        round_number: int,
        round_results: str,
        cumulative_standings: str
    ) -> str:
        """
        Format the judge user prompt.
        
        Args:
            problem_description: The problem description in markdown
            round_number: The current round number
            round_results: Formatted string of round results
            cumulative_standings: Formatted string of cumulative standings
            
        Returns:
            Formatted judge prompt string
        """
        return JUDGE_USER_TEMPLATE.format(
            problem_description=problem_description,
            round_number=round_number,
            round_results=round_results,
            cumulative_standings=cumulative_standings
        )
    
    @staticmethod
    def format_error_retry(
        error_details: str,
        previous_code: str
    ) -> str:
        """
        Format the error retry prompt.
        
        Args:
            error_details: Description of the error that occurred
            previous_code: The previous code that failed
            
        Returns:
            Formatted error retry prompt string
        """
        return ERROR_RETRY_TEMPLATE.format(
            error_details=error_details,
            previous_code=previous_code,
            response_format=RESPONSE_FORMAT
        )
    
    @staticmethod
    def format_security_user_prompt(
        code: str,
        flags: str
    ) -> str:
        """
        Format the security analysis user prompt.
        
        Args:
            code: The source code to analyze
            flags: The compiler flags to analyze
            
        Returns:
            Formatted security prompt string
        """
        return SECURITY_USER_TEMPLATE.format(
            code=code,
            flags=flags
        )

    @staticmethod
    def format_round_results(
        solutions_data: list,
        max_code_lines: int = 30
    ) -> str:
        """
        Format solution data for the judge prompt.
        
        Args:
            solutions_data: List of dictionaries containing solution info:
                - model: Model name
                - approach: Approach explanation
                - score: Final score
                - tests_passed: Number of tests passed
                - tests_total: Total number of tests
                - avg_time_ms: Average execution time
                - max_memory_kb: Maximum memory usage
                - code: Source code
            max_code_lines: Maximum number of code lines to include
            
        Returns:
            Formatted round results string
        """
        lines = []
        
        for i, sol in enumerate(solutions_data, 1):
            lines.append(f"### Solution {i}: {sol.get('model', 'Unknown')}")
            lines.append(f"- **Score**: {sol.get('score', 0):.4f}")
            lines.append(f"- **Tests Passed**: {sol.get('tests_passed', 0)}/{sol.get('tests_total', 0)}")
            lines.append(f"- **Avg Time**: {sol.get('avg_time_ms', 0):.2f}ms")
            lines.append(f"- **Max Memory**: {sol.get('max_memory_kb', 0)}KB")
            lines.append(f"- **Approach**: {sol.get('approach', 'No explanation provided')}")
            
            # Include truncated code
            code = sol.get('code', '')
            if code:
                code_lines = code.split('\n')
                if len(code_lines) > max_code_lines:
                    truncated_code = '\n'.join(code_lines[:max_code_lines])
                    truncated_code += f"\n\n... ({len(code_lines) - max_code_lines} more lines)"
                else:
                    truncated_code = code
                lines.append(f"- **Code Snippet**:\n```cpp\n{truncated_code}\n```")
            
            lines.append("")
        
        return '\n'.join(lines)
    
    @staticmethod
    def format_cumulative_standings(
        standings: list
    ) -> str:
        """
        Format cumulative standings for the judge prompt.
        
        Args:
            standings: List of tuples (model, total_score, rounds_won)
            
        Returns:
            Formatted standings string
        """
        lines = ["| Rank | Model | Total Score | Rounds Won |"]
        lines.append("|------|-------|-------------|------------|")
        
        for rank, (model, total_score, rounds_won) in enumerate(standings, 1):
            lines.append(f"| {rank} | {model} | {total_score:.4f} | {rounds_won} |")
        
        return '\n'.join(lines)
