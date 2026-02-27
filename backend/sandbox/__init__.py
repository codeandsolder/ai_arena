"""
Sandbox module for the AI Optimization Arena.

Provides secure compilation and execution of untrusted C++ code using Docker containers.
"""

from backend.sandbox.compiler import (
    compile_solution,
    compile_in_container,
    validate_compiler,
    validate_flags,
    perform_safety_check,
    get_compiler_info,
    WHITELISTED_COMPILERS,
    WHITELISTED_FLAGS,
    DEFAULT_COMPILER,
    SafetyCheckError,
    CompilerError,
)

from backend.sandbox.container import (
    ContainerManager,
    ContainerResult,
    create_container,
    run_container,
    cleanup_container,
    execute_command,
    get_container_manager,
    DEFAULT_IMAGE,
    DEFAULT_MEMORY_LIMIT,
    DEFAULT_TIMEOUT,
)

from backend.sandbox.benchmark import (
    benchmark_solution,
    compile_and_benchmark,
    BenchmarkConfig,
    TestCaseResult,
    BenchmarkSummary,
    BenchmarkError,
)

__all__ = [
    # Compiler
    "compile_solution",
    "compile_in_container",
    "validate_compiler",
    "validate_flags",
    "perform_safety_check",
    "get_compiler_info",
    "WHITELISTED_COMPILERS",
    "WHITELISTED_FLAGS",
    "DEFAULT_COMPILER",
    "SafetyCheckError",
    "CompilerError",
    # Container
    "ContainerManager",
    "ContainerResult",
    "create_container",
    "run_container",
    "cleanup_container",
    "execute_command",
    "get_container_manager",
    "DEFAULT_IMAGE",
    "DEFAULT_MEMORY_LIMIT",
    "DEFAULT_TIMEOUT",
    # Benchmark
    "benchmark_solution",
    "compile_and_benchmark",
    "BenchmarkConfig",
    "TestCaseResult",
    "BenchmarkSummary",
    "BenchmarkError",
]
