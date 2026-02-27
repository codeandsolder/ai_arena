"""
Compiler module for the AI Optimization Arena sandbox system.

Handles compilation of C++ code with security checks including:
- Compiler whitelist validation
- Compiler flag whitelist validation
- Static safety checks for dangerous code patterns
"""

import asyncio
import logging
import re
import tempfile
import os
from pathlib import Path
from typing import List, Tuple, Optional, Set

logger = logging.getLogger(__name__)


# Whitelisted compilers
WHITELISTED_COMPILERS: Set[str] = {
    "g++-12",
    "g++-13",
    "g++-14",
    "clang++-16",
    "clang++-17",
    "clang++-18",
}

# Default compiler if requested one is not whitelisted
DEFAULT_COMPILER = "g++-14"

# Whitelisted compiler flags
WHITELISTED_FLAGS: Set[str] = {
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
    "-std=gnu++17",
    "-std=gnu++20",
    "-std=gnu++23",
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
    "-DNDEBUG",
    "-pipe",
    # Additional common safe flags
    "-Wall",
    "-Wextra",
    "-Werror",
    "-static",
    "-static-libgcc",
    "-static-libstdc++",
}

# Dangerous patterns to detect in source code
# These patterns indicate potential security risks
DANGEROUS_PATTERNS: List[Tuple[str, str]] = [
    (r"system\s*\(", "system() function call"),
    (r"popen\s*\(", "popen() function call"),
    (r"exec\s*\(", "exec() function call"),
    (r"execl\s*\(", "execl() function call"),
    (r"execlp\s*\(", "execlp() function call"),
    (r"execv\s*\(", "execv() function call"),
    (r"execvp\s*\(", "execvp() function call"),
    (r"fork\s*\(", "fork() function call"),
    (r"#pragma\s+comment", "pragma comment directive"),
    (r"asm\s*\(", "inline assembly"),
    (r"__asm", "inline assembly (GCC style)"),
    (r"__asm__", "inline assembly (GCC extended)"),
    (r'#include\s*<\s*fstream\s*>', "file stream header"),
    (r'#include\s*<\s*filesystem\s*>', "filesystem header"),
    (r'#include\s*<\s*dlfcn\.h\s*>', "dynamic loading header"),
    (r"dlopen\s*\(", "dlopen() function call"),
    (r"dlsym\s*\(", "dlsym() function call"),
    (r"socket\s*\(", "socket() function call"),
    (r"connect\s*\(", "connect() function call"),
    (r"bind\s*\(", "bind() function call"),
    (r"listen\s*\(", "listen() function call"),
    (r"accept\s*\(", "accept() function call"),
]


class SafetyCheckError(Exception):
    """Raised when source code fails safety checks."""
    pass


class CompilerError(Exception):
    """Raised when compilation fails."""
    pass


def validate_compiler(compiler: str) -> str:
    """
    Validate that the requested compiler is in the whitelist.
    
    Args:
        compiler: The compiler executable name
        
    Returns:
        The validated compiler name (or default if not whitelisted)
    """
    if compiler in WHITELISTED_COMPILERS:
        return compiler
    
    logger.warning(
        f"Compiler '{compiler}' is not in whitelist. "
        f"Using default compiler '{DEFAULT_COMPILER}'"
    )
    return DEFAULT_COMPILER


def validate_flags(flags: List[str]) -> List[str]:
    """
    Filter compiler flags against the whitelist.
    
    Non-whitelisted flags are silently removed with a warning logged.
    
    Args:
        flags: List of compiler flags
        
    Returns:
        List of validated flags
    """
    validated_flags = []
    
    for flag in flags:
        # Check exact match
        if flag in WHITELISTED_FLAGS:
            validated_flags.append(flag)
            continue
        
        # Check for flags with values (e.g., -DFOO, -I/path)
        # Allow -D definitions (macro definitions) as they're generally safe
        # Only allow alphanumeric characters and underscores in macro name and value
        if flag.startswith("-D"):
            # Extract macro definition part (everything after -D)
            macro_def = flag[2:]
            # Validate macro name and optional value
            # Format: NAME or NAME=value
            # Allow alphanumeric, underscores, dots, commas, hex (0x), and basic operators
            # Block shell injection and whitespace chars: ; | & $ ` < > ( ) \s
            if re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*(?:=[^;|&$`<>()\s]*)?$', macro_def):
                validated_flags.append(flag)
                continue
            
        # Log warning for non-whitelisted flag
        logger.warning(f"Flag '{flag}' is not in whitelist and will be removed")
    
    return validated_flags


def perform_safety_check(source_code: str) -> List[str]:
    """
    Perform static safety analysis on source code.
    
    Scans for dangerous patterns that could indicate malicious code.
    
    Args:
        source_code: The C++ source code to analyze
        
    Returns:
        List of violations found (empty if code passes all checks)
    """
    violations = []
    
    for pattern, description in DANGEROUS_PATTERNS:
        if re.search(pattern, source_code, re.IGNORECASE):
            violations.append(f"Detected: {description} (pattern: {pattern})")
    
    return violations


async def compile_solution(
    source_code: str,
    compiler: str,
    flags: List[str],
    output_path: str,
    timeout: int = 60,
    skip_safety_check: bool = False
) -> Tuple[bool, str, str, Optional[str]]:
    """
    Compile a C++ solution with security checks.
    
    Args:
        source_code: The C++ source code to compile
        compiler: The compiler executable name
        flags: List of compiler flags
        output_path: Path where the compiled binary should be written
        timeout: Compilation timeout in seconds (default: 60)
        skip_safety_check: If True, skip the static safety check (e.g., if AI analysis already done)
        
    Returns:
        Tuple of (success, stdout, stderr, output_binary_path or None)
    """
    # Validate compiler
    validated_compiler = validate_compiler(compiler)
    
    # Validate flags
    validated_flags = validate_flags(flags)
    
    # Perform safety check (unless skipped)
    if not skip_safety_check:
        violations = perform_safety_check(source_code)
        if violations:
            error_msg = "Source code failed safety check:\n" + "\n".join(violations)
            logger.error(error_msg)
            return False, "", error_msg, None
    
    # Create temporary directory for compilation
    with tempfile.TemporaryDirectory() as temp_dir:
        # Force output_path to be within temp_dir to prevent directory traversal
        output_filename = Path(output_path).name
        safe_output_path = str(Path(temp_dir) / output_filename)
        
        # Write source code to temporary file
        source_path = Path(temp_dir) / "solution.cpp"
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                f.write(source_code)
        except Exception as e:
            logger.error(f"Failed to write source file: {e}")
            return False, "", f"Failed to write source file: {e}", None
        
        # Build compilation command
        cmd = [
            validated_compiler,
            str(source_path),
            "-o", safe_output_path,
        ] + validated_flags
        
        logger.info(f"Compiling with command: {' '.join(cmd)}")
        
        try:
            # Run compilation process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=temp_dir
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error(f"Compilation timed out after {timeout} seconds")
                return False, "", f"Compilation timed out after {timeout} seconds", None
            
            stdout_str = stdout.decode("utf-8", errors="replace")
            stderr_str = stderr.decode("utf-8", errors="replace")
            
            if process.returncode == 0:
                # Compilation successful
                # Copy binary from temp to output_path before returning
                # (output_path is the intended persistent location)
                try:
                    # Ensure destination directory exists
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    # Copy binary to the requested output path
                    with open(safe_output_path, 'rb') as src, open(output_path, 'wb') as dst:
                        dst.write(src.read())
                    logger.info(f"Compilation successful: {output_path}")
                    return True, stdout_str, stderr_str, output_path
                except Exception as e:
                    logger.error(f"Failed to copy binary to {output_path}: {e}")
                    return False, stdout_str, f"Failed to copy binary: {e}", None
            else:
                # Compilation failed
                error_msg = f"Compilation failed with exit code {process.returncode}"
                logger.error(error_msg)
                return False, stdout_str, stderr_str, None
                
        except Exception as e:
            logger.error(f"Compilation error: {e}")
            return False, "", f"Compilation error: {e}", None


async def compile_in_container(
    source_code: str,
    compiler: str,
    flags: List[str],
    container_runner,
    timeout: int = 60
) -> Tuple[bool, str, str, Optional[str]]:
    """
    Compile a C++ solution inside a Docker container.
    
    This is a higher-level function that uses a container runner
    to compile code in an isolated environment.
    
    Args:
        source_code: The C++ source code to compile
        compiler: The compiler executable name
        flags: List of compiler flags
        container_runner: Callable that runs commands in container
        timeout: Compilation timeout in seconds
        
    Returns:
        Tuple of (success, stdout, stderr, output_binary_path or None)
    """
    # Validate compiler and flags first
    validated_compiler = validate_compiler(compiler)
    validated_flags = validate_flags(flags)
    
    # Perform safety check
    violations = perform_safety_check(source_code)
    if violations:
        error_msg = "Source code failed safety check:\n" + "\n".join(violations)
        logger.error(error_msg)
        return False, "", error_msg, None
    
    # Create temporary source file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".cpp", delete=False) as f:
        f.write(source_code)
        source_path = f.name
    
    try:
        output_binary = "/workspace/solution"
        
        # Build compilation command
        cmd_parts = [validated_compiler, "/workspace/solution.cpp", "-o", output_binary]
        cmd_parts.extend(validated_flags)
        
        logger.info(f"Compiling in container with command: {' '.join(cmd_parts)}")
        
        # Run compilation in container
        # Execute directly instead of via sh -c to prevent shell injection
        success, stdout, stderr = await container_runner(
            cmd_parts,
            timeout=timeout,
            volumes={
                source_path: {"bind": "/workspace/solution.cpp", "mode": "ro"}
            }
        )
        
        if success:
            logger.info("Compilation in container successful")
            return True, stdout, stderr, output_binary
        else:
            logger.error(f"Compilation in container failed: {stderr}")
            return False, stdout, stderr, None
            
    finally:
        # Clean up temporary file
        try:
            os.unlink(source_path)
        except Exception as e:
            logger.warning(f"Failed to clean up temporary source file: {e}")


def get_compiler_info() -> dict:
    """
    Get information about available compilers and flags.
    
    Returns:
        Dictionary with compiler and flag information
    """
    return {
        "compilers": sorted(WHITELISTED_COMPILERS),
        "default_compiler": DEFAULT_COMPILER,
        "flags": sorted(WHITELISTED_FLAGS),
    }
