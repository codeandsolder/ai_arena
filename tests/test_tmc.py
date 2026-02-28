import os
import tempfile
import pytest
import asyncio
from backend.sandbox.container import ContainerManager 
from backend.sandbox.benchmark import compile_and_benchmark


@pytest.mark.asyncio
async def test_tmc_execution():
    """
    Verifies that 'tmc' is installed and executable in the sandbox container.
    This test ensures that the Docker image build correctly included tmc.
    """
    manager = ContainerManager()
    
    # Skip if docker is not available or if we want to avoid building during tests
    # unless explicitly requested. But here we want to verify the fix.
    try:
        # Check if tmc exists and get its version
        # We use a simple command that should return 0 if tmc is found
        command = ["tmc", "--version"]
        
        # We need to ensure the image is built/available
        await manager.ensure_image_available()
        
        container = await manager.create_container(
            command=command,
            read_only=True,
            network_disabled=True
        )
        
        try:
            result = await manager.run_container(container, timeout=30)
            
            assert result.success, f"tmc --version failed with exit code {result.exit_code}. Output: {result.stdout}"
            if result.stdout:
                assert "tmc" in result.stdout.lower() or "task-maker" in result.stdout.lower(), f"Unexpected output from tmc --version: {result.stdout}"
            print(f"tmc version check successful: {result.stdout.strip() if result.stdout else '<no logs>'}")
            
        finally:
            # Clean up container
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: container.remove(force=True))
            
    except Exception as e:
        pytest.fail(f"Test failed due to exception: {e}")

import shutil

@pytest.mark.asyncio
async def test_tmc_task_grading():
    """
    Verifies actual task execution in tmc with a small example task.
    """
    manager = ContainerManager()
    await manager.ensure_image_available()

    with tempfile.TemporaryDirectory() as temp_dir:
        # Copy base config and task from problems_repo
        repo_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend", "data", "problems_repo")
        if not os.path.exists(repo_dir):
            pytest.skip(f"Problems repo not found at {repo_dir}")
            
        shutil.copy2(os.path.join(repo_dir, "base-batch.yaml"), os.path.join(temp_dir, "base-batch.yaml"))
        
        task_dir = os.path.join(temp_dir, "task")
        shutil.copytree(os.path.join(repo_dir, "ceoi2022-abracadabra"), task_dir)
        
        # task-maker-rust expects a float for time_limit, not a string with 's' suffix
        task_yaml_path = os.path.join(task_dir, "task.yaml")
        with open(task_yaml_path, "r") as f:
            content = f.read()
            content = content.replace("time_limit: 3.0s", "time_limit: 3.0")
            content = content.replace("memory_limit: 512MiB", "memory_limit: 512")
            content = content.replace("long_name: Abracadabra", "title: Abracadabra")
        with open(task_yaml_path, "w") as f:
            f.write(content)
        
        # Clean up all test cases except 0-01.in.gz and 0-01.out.gz to make it fast
        tc_dir = os.path.join(task_dir, "tc")
        for f in os.listdir(tc_dir):
            if f not in ["0-01.in.gz", "0-01.out.gz"]:
                os.remove(os.path.join(tc_dir, f))
                
        # Clean up all solutions except deu-lukas-michel.cpp
        sol_dir = os.path.join(task_dir, "solution")
        for f in os.listdir(sol_dir):
            if f != "deu-lukas-michel.cpp":
                os.remove(os.path.join(sol_dir, f))

        # Mount the task directory
        volumes = {
            os.path.abspath(temp_dir): {
                "bind": "/repo",
                "mode": "rw"
            }
        }

        # Run tmc with UI json in the task directory
        command = [
            "tmc",
            "-s", "solution/deu-lukas-michel.cpp",
            "-W", "StatementPresent",
            "-W", "StatementValid",
            "-W", "StatementCompiledOrGit",
            "-W", "StatementSubtasks",
            "-W", "AttNoDirectory",
            "-W", "AttSampleFiles",
            "--ui", "json"
        ]

        container = await manager.create_container(
            command=command,
            volumes=volumes,
            working_dir="/repo/task",
            read_only=False, # TMC needs to write compilation outputs
            network_disabled=True,
            cap_add=["SYS_ADMIN"] # Isolate requires SYS_ADMIN capability
        )
        
        try:
            result = await manager.run_container(container, timeout=60)
            
            assert result.success, f"tmc execution failed with exit code {result.exit_code}. Output: {result.stdout}"
            if result.stdout:
                assert "IOITaskScore" in result.stdout, f"Did not find expected JSON output from tmc. Output: {result.stdout}"
            print(f"tmc grading test passed!")
            
        finally:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: container.remove(force=True))


@pytest.mark.asyncio
async def test_official_workflow_tmc_integration():
    """
    Integration test for the official pipeline.
    Passes source code to `compile_and_benchmark`, which should detect task.yaml, 
    route to `_run_task_maker`, patch the YAML, and execute successfully via Docker.
    """
    import os
    import shutil
    import tempfile
    
    repo_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend", "data", "problems_repo")
    if not os.path.exists(repo_dir):
        pytest.skip(f"Problems repo not found at {repo_dir}")

    with tempfile.TemporaryDirectory() as temp_dir:
        # 1. Setup a realistic problem structure
        shutil.copy2(os.path.join(repo_dir, "base-batch.yaml"), os.path.join(temp_dir, "base-batch.yaml"))
        
        task_dir = os.path.join(temp_dir, "task")
        shutil.copytree(os.path.join(repo_dir, "ceoi2022-abracadabra"), task_dir)
        
        # Clean up all test cases except 0-01 to make the test run fast
        tc_dir = os.path.join(task_dir, "tc")
        for f in os.listdir(tc_dir):
            if f not in ["0-01.in.gz", "0-01.out.gz"]:
                os.remove(os.path.join(tc_dir, f))
                
        # Read the known correct solution
        sol_path = os.path.join(task_dir, "solution", "deu-lukas-michel.cpp")
        with open(sol_path, "r", encoding="utf-8") as f:
            source_code = f.read()

        # compile_and_benchmark requires a test_cases_dir argument (even though TMC ignores it)
        dummy_tests_dir = os.path.join(temp_dir, "dummy_tests")
        os.makedirs(dummy_tests_dir)

        # 2. Execute the official workflow!
        success, msg, summary = await compile_and_benchmark(
            solution_id=-999,
            source_code=source_code,
            compiler="g++-14",
            compiler_flags=["-O2", "-std=c++17"],
            test_cases_dir=dummy_tests_dir,
            problem_id=1,
            time_limit_ms=3000,
            memory_limit_mb=512,
            benchmark_runs=1,
            warmup_runs=0,
            db_session=None,
            problem_dir=task_dir
        )

        # 3. Verify the pipeline handled everything automatically
        assert success is True, f"Benchmark failed with message: {msg}"
        assert summary is not None, "Benchmark summary is None"
        
        # We kept exactly 1 test case
        assert summary.tests_total == 1, f"Expected 1 test total, got {summary.tests_total}"
        assert summary.tests_passed == 1, "Expected the test case to pass"
        assert summary.all_passed is True, "all_passed should be True"
        
        # Verify JSON parsed correctly into TestCaseResult
        test_result = summary.test_results[0]
        assert test_result.passed is True
        assert test_result.verdict == "AC"
        assert test_result.time_ms > 0, "Time tracking failed"
        assert test_result.memory_kb > 0, "Memory tracking failed"
        
        print("Official TMC Workflow test passed seamlessly!")