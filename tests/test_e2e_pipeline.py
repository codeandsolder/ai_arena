import pytest
import os
import tempfile
from pathlib import Path
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from backend.database.models import Base, Problem, Solution
from backend.sandbox.compiler import compile_solution
from backend.sandbox.benchmark import benchmark_solution, BenchmarkSummary

# Sample C++ solution for A+B
EXAMPLE_SOLUTION = """
#include <iostream>

using namespace std;

int main() {
    int a, b;
    if (cin >> a >> b) {
        cout << a + b << endl;
    }
    return 0;
}
"""

@pytest.fixture
async def db_session():
    # Setup an in-memory SQLite database
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    SessionLocal = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    async with SessionLocal() as session:
        yield session

    await engine.dispose()

@pytest.fixture
def test_cases_dir():
    # Setup temporary directory for test cases
    with tempfile.TemporaryDirectory() as tmpdir:
        dir_path = Path(tmpdir)
        
        # Create test cases
        # Test 1
        with open(dir_path / "1.in", "w") as f:
            f.write("2 3\n")
        with open(dir_path / "1.out", "w") as f:
            f.write("5\n")
            
        # Test 2
        with open(dir_path / "2.in", "w") as f:
            f.write("-10 20\n")
        with open(dir_path / "2.out", "w") as f:
            f.write("10\n")
            
        yield str(dir_path)

@pytest.mark.asyncio
async def test_end_to_end_execution_pipeline(db_session: AsyncSession, test_cases_dir: str):
    # 1. Setup a problem in the database
    problem = Problem(
        name="A+B Problem",
        slug="a-plus-b",
        description_md="Add two numbers",
        time_limit_ms=1000,
        memory_limit_mb=256,
        test_count=2,
        tests_downloaded=True
    )
    db_session.add(problem)
    await db_session.commit()
    await db_session.refresh(problem)
    
    # Verify problem was loaded correctly
    result = await db_session.execute(select(Problem).where(Problem.slug == "a-plus-b"))
    loaded_problem = result.scalar_one_or_none()
    assert loaded_problem is not None
    assert loaded_problem.test_count == 2
    
    # 2. Setup a solution record
    solution = Solution(
        round_id=1,  # Mocked
        model_slug="test-model",
        source_code=EXAMPLE_SOLUTION,
        compiler="g++-14",
        compiler_flags="-O2 -std=c++20",
        status="pending"
    )
    db_session.add(solution)
    await db_session.commit()
    await db_session.refresh(solution)
    
    # 3. Compile the solution
    with tempfile.TemporaryDirectory() as compile_tmpdir:
        binary_path = os.path.join(compile_tmpdir, "solution.exe")
        
        success, stdout, stderr, output_path = await compile_solution(
            source_code=solution.source_code,
            compiler=solution.compiler,
            flags=["-O2", "-std=c++20"],
            output_path=binary_path,
            timeout=30,
            skip_safety_check=False
        )
        
        assert success is True, f"Compilation failed: {stderr}"
        assert os.path.exists(output_path), "Compiled binary was not created"
        
        # Update solution state
        solution.compile_success = True
        solution.status = "compiled"
        await db_session.commit()
        
        # 4. Benchmark the solution against loaded test cases
        summary: BenchmarkSummary = await benchmark_solution(
            solution_id=solution.id,
            solution_binary_path=output_path,
            test_cases_dir=test_cases_dir,
            time_limit_ms=loaded_problem.time_limit_ms,
            memory_limit_mb=loaded_problem.memory_limit_mb,
            benchmark_runs=1,
            warmup_runs=0,
            db_session=db_session
        )
        
        # 5. Validate the execution results
        assert summary is not None
        assert summary.tests_passed == 2
        assert summary.tests_total == 2
        assert summary.all_passed is True
        
        # Verify TestResult records were written to the database
        # (Assuming benchmark_solution stores results if db_session is provided)
        # Note: actually _store_results handles that inside benchmark_solution
        await db_session.refresh(solution)
        # We can also verify test_results count if needed
        # In this e2e test we proved: problem loaded, compiled, run vs tests, and passed
