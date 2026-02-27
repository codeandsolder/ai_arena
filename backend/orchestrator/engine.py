"""
Main orchestration engine for the AI Optimization Arena.

This module provides the core OrchestrationEngine that manages the multi-round
optimization loop, coordinating solution generation, compilation, benchmarking,
scoring, and judging.
"""

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.orchestrator.model_client import ModelClient, ModelResponse
from backend.orchestrator.prompts import PromptFormatter
from backend.orchestrator.summarizer import Summarizer
from backend.orchestrator.security import SecurityAnalyzer
from backend.database.models import Run, Round, Solution, ApiCall, Problem, TestResult
from backend.database.session import get_session_maker
from backend.sandbox.compiler import compile_solution
from backend.sandbox.benchmark import benchmark_solution, BenchmarkSummary
from backend.config import DATA_DIR, RUNS_DIR
from backend.websocket_manager import get_websocket_manager, WebSocketManager

logger = logging.getLogger(__name__)


@dataclass
class RunConfig:
    """Configuration for a competition run."""
    models: List[str] = field(default_factory=list)
    num_rounds: int = 5
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256
    max_tokens: int = 4096
    temperature: float = 0.7
    thinking_budget: Optional[int] = None
    correctness_weight: float = 0.6
    speed_weight: float = 0.25
    memory_weight: float = 0.15
    penalty_wrong_answer: float = -0.5
    allow_error_retry: bool = True
    max_error_retries: int = 2
    judge_model: str = "anthropic/claude-3.5-sonnet"
    security_models: List[str] = field(default_factory=lambda: ["anthropic/claude-3-haiku"])


@dataclass
class GenerationResult:
    """Result of solution generation."""
    solution_id: int
    success: bool
    code: Optional[str] = None
    compiler: Optional[str] = None
    compiler_flags: Optional[str] = None
    explanation: Optional[str] = None
    error_message: Optional[str] = None
    api_response: Optional[ModelResponse] = None


@dataclass
class CompilationResult:
    """Result of solution compilation."""
    solution_id: int
    success: bool
    binary_path: Optional[str] = None
    stdout: str = ""
    stderr: str = ""
    error_message: Optional[str] = None


class OrchestrationEngine:
    """
    Main orchestration engine for running optimization competitions.
    
    Manages the full competition lifecycle:
    1. Solution generation (parallel across models)
    2. Compilation (parallel)
    3. Benchmarking (parallel per solution)
    4. Scoring
    5. Judging/Summarization
    6. State updates and WebSocket broadcasts
    """
    
    def __init__(
        self,
        model_client: Optional[ModelClient] = None,
        summarizer: Optional[Summarizer] = None,
        websocket_manager: Optional[WebSocketManager] = None
    ):
        """
        Initialize the OrchestrationEngine.
        
        Args:
            model_client: ModelClient for API calls
            summarizer: Summarizer for judging
            websocket_manager: WebSocketManager for status broadcasts
        """
        self.model_client = model_client or ModelClient()
        self.summarizer = summarizer
        self.prompt_formatter = PromptFormatter()
        self.websocket_manager = websocket_manager or get_websocket_manager()
        self.security_analyzer = SecurityAnalyzer(self.model_client)
        
        # Track paused runs
        self._paused_runs: set = set()
        self._active_runs: Dict[int, asyncio.Task] = {}
        
        logger.info("OrchestrationEngine initialized")
    
    async def start_run(self, run_id: int, num_rounds: int) -> None:
        """
        Start a competition run.
        
        Args:
            run_id: The run ID to start
            num_rounds: Number of rounds to run
        """
        logger.info(f"Starting run {run_id} for {num_rounds} rounds")
        
        # Remove from paused if present
        self._paused_runs.discard(run_id)
        
        # Create task for the run
        task = asyncio.create_task(self._run_competition(run_id, num_rounds))
        self._active_runs[run_id] = task
        
        # Broadcast run started
        await self.websocket_manager.broadcast_run_status(
            run_id=run_id,
            status="started",
            message=f"Run started for {num_rounds} rounds"
        )
        
        try:
            await task
        except asyncio.CancelledError:
            logger.info(f"Run {run_id} was cancelled")
            await self._update_run_status(run_id, "paused")
        except Exception as e:
            logger.error(f"Run {run_id} failed: {e}")
            await self._update_run_status(run_id, "failed")
            raise
        finally:
            if run_id in self._active_runs:
                del self._active_runs[run_id]
    
    async def _run_competition(self, run_id: int, num_rounds: int) -> None:
        """
        Run the competition for the specified number of rounds.
        
        Args:
            run_id: The run ID
            num_rounds: Number of rounds to run
        """
        session_maker = get_session_maker()
        
        async with session_maker() as db_session:
            # Load run and problem
            run_result = await db_session.execute(
                select(Run).where(Run.id == run_id)
            )
            run = run_result.scalar_one_or_none()
            
            if not run:
                raise ValueError(f"Run {run_id} not found")
            
            problem_result = await db_session.execute(
                select(Problem).where(Problem.id == run.problem_id)
            )
            problem = problem_result.scalar_one_or_none()
            
            if not problem:
                raise ValueError(f"Problem for run {run_id} not found")
            
            # Parse config
            config = self._parse_run_config(run.config_json)
            
            # Update run status
            await self._update_run_status(run_id, "running", db_session)
            
            # Initialize summarizer if needed
            if not self.summarizer:
                self.summarizer = Summarizer(
                    model_client=self.model_client,
                    judge_model=config.judge_model
                )
            
            # Run rounds
            previous_summary = None
            for round_num in range(1, num_rounds + 1):
                # Check if paused
                if run_id in self._paused_runs:
                    logger.info(f"Run {run_id} is paused at round {round_num}")
                    await self._update_run_status(run_id, "paused", db_session)
                    break
                
                logger.info(f"Starting round {round_num}/{num_rounds} for run {run_id}")
                
                previous_summary = await self.run_round(
                    run_id=run_id,
                    round_number=round_num,
                    problem=problem,
                    config=config,
                    previous_summary=previous_summary,
                    db_session=db_session
                )
                
                # Update total rounds
                await db_session.execute(
                    update(Run)
                    .where(Run.id == run_id)
                    .values(total_rounds=round_num)
                )
                await db_session.commit()
            
            # Mark run as completed if not paused
            if run_id not in self._paused_runs:
                await self._update_run_status(run_id, "completed", db_session)
                await self.websocket_manager.broadcast_run_status(
                    run_id=run_id,
                    status="completed",
                    message="Run completed successfully"
                )
    
    async def run_round(
        self,
        run_id: int,
        round_number: int,
        problem: Problem,
        config: RunConfig,
        previous_summary: Optional[str],
        db_session: AsyncSession
    ) -> Optional[str]:
        """
        Execute a single round of the competition.
        
        Args:
            run_id: The run ID
            round_number: Current round number
            problem: The problem being solved
            config: Run configuration
            previous_summary: Summary from previous round (None for round 1)
            db_session: Database session
            
        Returns:
            Summary text for this round, or None if failed
        """
        logger.info(f"Running round {round_number} for run {run_id}")
        
        # Create round record
        round_obj = Round(
            run_id=run_id,
            round_number=round_number,
            status="generating"
        )
        db_session.add(round_obj)
        await db_session.commit()
        await db_session.refresh(round_obj)
        
        round_id = round_obj.id
        
        # Broadcast round started
        await self.websocket_manager.broadcast_round_status(
            run_id=run_id,
            round_id=round_id,
            status="generating"
        )
        
        try:
            # Phase 1: Generate Solutions
            logger.info(f"Phase 1: Generating solutions for round {round_number}")
            solutions = await self._generate_solutions(
                run_id=run_id,
                round_id=round_id,
                problem=problem,
                config=config,
                previous_summary=previous_summary,
                db_session=db_session
            )
            
            if not solutions:
                logger.error(f"No solutions generated for round {round_number}")
                await self._update_round_status(round_id, "failed", db_session)
                return None
            
            # Phase 2: Compile Solutions
            logger.info(f"Phase 2: Compiling {len(solutions)} solutions")
            await self._update_round_status(round_id, "compiling", db_session)
            await self.websocket_manager.broadcast_round_status(
                run_id=run_id,
                round_id=round_id,
                status="compiling"
            )
            
            compiled = await self._compile_solutions(
                run_id=run_id,
                round_id=round_id,
                solutions=solutions,
                config=config,
                db_session=db_session
            )
            
            # Phase 3: Benchmark Solutions
            logger.info(f"Phase 3: Benchmarking {len(compiled)} compiled solutions")
            await self._update_round_status(round_id, "benchmarking", db_session)
            await self.websocket_manager.broadcast_round_status(
                run_id=run_id,
                round_id=round_id,
                status="benchmarking"
            )
            
            # Get test cases directory
            test_cases_dir = DATA_DIR / "problems" / problem.slug / "test_cases"
            
            await self._benchmark_solutions(
                run_id=run_id,
                round_id=round_id,
                compiled=compiled,
                test_cases_dir=str(test_cases_dir),
                problem=problem,
                db_session=db_session
            )
            
            # Phase 4: Score Solutions
            logger.info(f"Phase 4: Scoring solutions for round {round_number}")
            await self._score_solutions(
                round_id=round_id,
                config=config,
                db_session=db_session
            )
            
            # Phase 5: Judge/Summarize
            logger.info(f"Phase 5: Judging round {round_number}")
            await self._update_round_status(round_id, "judging", db_session)
            await self.websocket_manager.broadcast_round_status(
                run_id=run_id,
                round_id=round_id,
                status="judging"
            )
            
            # Refresh solutions with scores
            solutions_result = await db_session.execute(
                select(Solution).where(Solution.round_id == round_id)
            )
            solutions_with_scores = list(solutions_result.scalars().all())
            
            summary = await self.summarizer.summarize_round(
                round_id=round_id,
                solutions=solutions_with_scores,
                problem=problem,
                db_session=db_session
            )
            
            # Store summary
            await db_session.execute(
                update(Round)
                .where(Round.id == round_id)
                .values(summary_text=summary)
            )
            await db_session.commit()
            
            # Phase 6: Update State and Broadcast
            logger.info(f"Phase 6: Finalizing round {round_number}")
            await self._update_round_status(round_id, "completed", db_session)
            
            # Build scores dict
            scores = {
                sol.model_slug: {
                    "score": sol.score,
                    "tests_passed": sol.tests_passed,
                    "tests_total": sol.tests_total,
                    "avg_time_ms": sol.avg_time_ms,
                    "max_memory_kb": sol.max_memory_kb
                }
                for sol in solutions_with_scores
            }
            
            await self.websocket_manager.broadcast_round_complete(
                run_id=run_id,
                round_id=round_id,
                scores=scores,
                summary=summary[:500] if summary else None  # Truncate for WebSocket
            )
            
            logger.info(f"Round {round_number} completed successfully")
            return summary
            
        except Exception as e:
            logger.error(f"Round {round_number} failed: {e}")
            await self._update_round_status(round_id, "failed", db_session)
            raise
    
    async def _generate_solutions(
        self,
        run_id: int,
        round_id: int,
        problem: Problem,
        config: RunConfig,
        previous_summary: Optional[str],
        db_session: AsyncSession
    ) -> List[Solution]:
        """
        Generate solutions from all models in parallel.
        
        Args:
            run_id: The run ID
            round_id: The round ID
            problem: The problem being solved
            config: Run configuration
            previous_summary: Summary from previous round
            db_session: Database session
            
        Returns:
            List of Solution objects
        """
        system_prompt = self.prompt_formatter.get_system_prompt()
        
        # Choose user prompt based on round
        if previous_summary:
            user_prompt = self.prompt_formatter.format_round_n_user(
                problem_description=problem.description_md,
                time_limit_ms=config.time_limit_ms,
                memory_limit_mb=config.memory_limit_mb,
                previous_round_summary=previous_summary
            )
        else:
            user_prompt = self.prompt_formatter.format_round_1_user(
                problem_description=problem.description_md,
                time_limit_ms=config.time_limit_ms,
                memory_limit_mb=config.memory_limit_mb
            )
        
        # Create solution records and generate in parallel
        solutions: List[Solution] = []
        generation_tasks = []
        
        for model_slug in config.models:
            # Create solution record
            solution = Solution(
                round_id=round_id,
                model_slug=model_slug,
                status="generating",
                score=0.0
            )
            db_session.add(solution)
            await db_session.flush()  # Get ID
            solutions.append(solution)
            
            # Create generation task
            task = self._generate_single_solution(
                run_id=run_id,
                round_id=round_id,
                solution_id=solution.id,
                model_slug=model_slug,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                config=config
            )
            generation_tasks.append(task)
            
            # Broadcast status
            await self.websocket_manager.broadcast_solution_status(
                run_id=run_id,
                solution_id=solution.id,
                model=model_slug,
                status="generating"
            )
        
        await db_session.commit()
        
        # Wait for all generations to complete
        results = await asyncio.gather(*generation_tasks, return_exceptions=True)
        
        # Update solutions with results
        for solution, result in zip(solutions, results):
            if isinstance(result, Exception):
                logger.error(f"Generation failed for {solution.model_slug}: {result}")
                solution.status = "failed"
                solution.error_message = str(result)
            else:
                gen_result: GenerationResult = result
                if gen_result.success:
                    solution.source_code = gen_result.code
                    solution.compiler = gen_result.compiler
                    solution.compiler_flags = gen_result.compiler_flags
                    solution.status = "pending"
                    
                    # Store API call
                    if gen_result.api_response:
                        api_call = ApiCall(
                            run_id=run_id,
                            round_id=round_id,
                            solution_id=solution.id,
                            purpose="solution_generation",
                            model_slug=solution.model_slug,
                            prompt_text=f"System:\n{system_prompt}\n\nUser:\n{user_prompt}",
                            thinking_text=gen_result.api_response.thinking_text,
                            response_text=gen_result.api_response.response_text,
                            input_tokens=gen_result.api_response.input_tokens,
                            output_tokens=gen_result.api_response.output_tokens,
                            thinking_tokens=gen_result.api_response.thinking_tokens,
                            cost_usd=gen_result.api_response.cost_usd,
                            latency_ms=gen_result.api_response.latency_ms
                        )
                        db_session.add(api_call)
                else:
                    solution.status = "failed"
                    solution.error_message = gen_result.error_message
            
            await self.websocket_manager.broadcast_solution_status(
                run_id=run_id,
                solution_id=solution.id,
                model=solution.model_slug,
                status=solution.status
            )
        
        await db_session.commit()
        
        # Return only solutions that are pending (ready for compilation)
        return [s for s in solutions if s.status == "pending"]
    
    async def _generate_single_solution(
        self,
        run_id: int,
        round_id: int,
        solution_id: int,
        model_slug: str,
        system_prompt: str,
        user_prompt: str,
        config: RunConfig
    ) -> GenerationResult:
        """
        Generate a solution from a single model.
        
        Args:
            run_id: The run ID
            round_id: The round ID
            solution_id: The solution ID
            model_slug: The model to use
            system_prompt: System prompt
            user_prompt: User prompt
            config: Run configuration
            
        Returns:
            GenerationResult
        """
        try:
            response, parsed_json = await self.model_client.call_model_with_retry(
                model_slug=model_slug,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                thinking_budget=config.thinking_budget,
                json_parse_retries=1
            )
            
            if not parsed_json:
                return GenerationResult(
                    solution_id=solution_id,
                    success=False,
                    error_message="Failed to parse JSON response after retries",
                    api_response=response
                )
            
            # Extract fields from JSON
            code = parsed_json.get("code", "")
            compiler = parsed_json.get("compiler", "g++-14")
            flags = parsed_json.get("flags", "-O2 -std=c++20")
            explanation = parsed_json.get("explanation", "")
            
            if not code:
                return GenerationResult(
                    solution_id=solution_id,
                    success=False,
                    error_message="No code provided in response",
                    api_response=response
                )
            
            return GenerationResult(
                solution_id=solution_id,
                success=True,
                code=code,
                compiler=compiler,
                compiler_flags=flags,
                explanation=explanation,
                api_response=response
            )
            
        except Exception as e:
            logger.error(f"Generation failed for {model_slug}: {e}")
            return GenerationResult(
                solution_id=solution_id,
                success=False,
                error_message=str(e)
            )
    
    async def _compile_solutions(
        self,
        run_id: int,
        round_id: int,
        solutions: List[Solution],
        config: RunConfig,
        db_session: AsyncSession
    ) -> List[Tuple[Solution, str]]:
        """
        Compile all solutions in parallel.
        
        Args:
            run_id: The run ID
            round_id: The round ID
            solutions: List of solutions to compile
            config: Run configuration
            db_session: Database session
            
        Returns:
            List of (Solution, binary_path) tuples for successful compilations
        """
        # Create run directory for binaries
        run_dir = RUNS_DIR / str(run_id) / f"round_{round_id}"
        run_dir.mkdir(parents=True, exist_ok=True)
        
        # Update security analyzer models if specified
        if config.security_models:
            self.security_analyzer.security_models = config.security_models

        compiled = []
        
        async def compile_with_retry(solution: Solution) -> Tuple[Solution, Optional[str]]:
            """Compile a solution with optional retry on error."""
            binary_path = str(run_dir / f"solution_{solution.id}")
            
            await self.websocket_manager.broadcast_solution_status(
                run_id=run_id,
                solution_id=solution.id,
                model=solution.model_slug,
                status="analyzing_security"
            )

            # Security Analysis
            try:
                security_result = await self.security_analyzer.analyze_solution(
                    source_code=solution.source_code or "",
                    compiler_flags=solution.compiler_flags or "",
                    run_id=run_id,
                    solution_id=solution.id
                )

                # Log API calls
                for response in security_result.model_responses:
                    api_call = ApiCall(
                        run_id=run_id,
                        round_id=round_id,
                        solution_id=solution.id,
                        purpose="security_check",
                        model_slug="security_auditor",
                        prompt_text="[Security Analysis Prompt]",
                        thinking_text=response.thinking_text,
                        response_text=response.response_text,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        thinking_tokens=response.thinking_tokens,
                        cost_usd=response.cost_usd,
                        latency_ms=response.latency_ms
                    )
                    db_session.add(api_call)
                
                if not security_result.is_safe:
                    solution.status = "security_failed"
                    solution.error_message = f"Security Check Failed:\n{security_result.details}"
                    logger.warning(f"Solution {solution.id} failed security check: {security_result.details}")
                    
                    await self.websocket_manager.broadcast_solution_status(
                        run_id=run_id,
                        solution_id=solution.id,
                        model=solution.model_slug,
                        status="security_failed"
                    )
                    return solution, None

            except Exception as e:
                logger.error(f"Security analysis error for solution {solution.id}: {e}")
                # Fail safe? Or allow retry? Let's fail safe.
                solution.status = "security_error"
                solution.error_message = f"Security analysis failed: {e}"
                return solution, None

            await self.websocket_manager.broadcast_solution_status(
                run_id=run_id,
                solution_id=solution.id,
                model=solution.model_slug,
                status="compiling"
            )
            
            # Parse flags
            import shlex
            try:
                flags_list = shlex.split(solution.compiler_flags or "-O2 -std=c++20")
            except ValueError:
                flags_list = ["-O2", "-std=c++20"]
            
            # Try compilation (skip_safety_check=True because we just did it)
            # Note: I need to update compile_solution to accept skip_safety_check
            success, stdout, stderr, output_path = await compile_solution(
                source_code=solution.source_code or "",
                compiler=solution.compiler or "g++-14",
                flags=flags_list,
                output_path=binary_path,
                skip_safety_check=True
            )
            
            # Update solution
            solution.compile_success = success
            solution.compile_log = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"[:10000]
            
            if success:
                solution.status = "compiled"
                await self.websocket_manager.broadcast_solution_status(
                    run_id=run_id,
                    solution_id=solution.id,
                    model=solution.model_slug,
                    status="compiled"
                )
                return solution, output_path
            else:
                # Check for retry
                if config.allow_error_retry and config.max_error_retries > 0:
                    logger.info(f"Retrying compilation for solution {solution.id}")
                    retry_result = await self._retry_solution_with_error(
                        run_id=run_id,
                        round_id=round_id,
                        solution=solution,
                        error_details=f"Compilation failed:\n{stderr}",
                        binary_path=binary_path,
                        config=config,
                        db_session=db_session
                    )
                    if retry_result:
                        return retry_result
                
                solution.status = "compile_failed"
                solution.error_message = f"Compilation failed:\n{stderr[:500]}"
                logger.error(f"Solution {solution.id} compilation failed: {stderr[:200]}")
                
                await self.websocket_manager.broadcast_solution_status(
                    run_id=run_id,
                    solution_id=solution.id,
                    model=solution.model_slug,
                    status="compile_failed"
                )
                return solution, None
        
        # Compile all solutions in parallel
        tasks = [compile_with_retry(sol) for sol in solutions]
        results = await asyncio.gather(*tasks)
        
        await db_session.commit()
        
        # Filter successful compilations
        for solution, binary_path in results:
            if binary_path:
                compiled.append((solution, binary_path))
        
        return compiled
    
    async def _retry_solution_with_error(
        self,
        run_id: int,
        round_id: int,
        solution: Solution,
        error_details: str,
        binary_path: str,
        config: RunConfig,
        db_session: AsyncSession
    ) -> Optional[Tuple[Solution, str]]:
        """
        Retry a solution generation after compilation error.
        
        Args:
            run_id: The run ID
            round_id: The round ID
            solution: The solution to retry
            error_details: Error message from compilation
            binary_path: Path for compiled binary
            config: Run configuration
            db_session: Database session
            
        Returns:
            (Solution, binary_path) if retry successful, None otherwise
        """
        if not solution.source_code:
            return None
        
        # Create error retry prompt
        retry_prompt = self.prompt_formatter.format_error_retry(
            error_details=error_details,
            previous_code=solution.source_code
        )
        
        system_prompt = self.prompt_formatter.get_system_prompt()
        
        try:
            response, parsed_json = await self.model_client.call_model_with_retry(
                model_slug=solution.model_slug,
                system_prompt=system_prompt,
                user_prompt=retry_prompt,
                max_tokens=config.max_tokens,
                temperature=config.temperature
            )
            
            if parsed_json:
                # Update solution with new code
                solution.source_code = parsed_json.get("code", solution.source_code)
                solution.compiler = parsed_json.get("compiler", solution.compiler)
                solution.compiler_flags = parsed_json.get("flags", solution.compiler_flags)
                
                # Store API call
                api_call = ApiCall(
                    run_id=run_id,
                    round_id=round_id,
                    solution_id=solution.id,
                    purpose="error_retry",
                    model_slug=solution.model_slug,
                    prompt_text=f"System:\n{system_prompt}\n\nUser:\n{retry_prompt}",
                    thinking_text=response.thinking_text,
                    response_text=response.response_text,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    thinking_tokens=response.thinking_tokens,
                    cost_usd=response.cost_usd,
                    latency_ms=response.latency_ms
                )
                db_session.add(api_call)
                
                # Security Analysis for retried solution
                try:
                    security_result = await self.security_analyzer.analyze_solution(
                        source_code=solution.source_code or "",
                        compiler_flags=solution.compiler_flags or "",
                        run_id=run_id,
                        solution_id=solution.id
                    )
                    
                    # Log security API calls
                    for sec_response in security_result.model_responses:
                        sec_api_call = ApiCall(
                            run_id=run_id,
                            round_id=round_id,
                            solution_id=solution.id,
                            purpose="security_check_retry",
                            model_slug="security_auditor",
                            prompt_text="[Security Analysis Prompt]",
                            thinking_text=sec_response.thinking_text,
                            response_text=sec_response.response_text,
                            input_tokens=sec_response.input_tokens,
                            output_tokens=sec_response.output_tokens,
                            thinking_tokens=sec_response.thinking_tokens,
                            cost_usd=sec_response.cost_usd,
                            latency_ms=sec_response.latency_ms
                        )
                        db_session.add(sec_api_call)

                    if not security_result.is_safe:
                        solution.status = "security_failed"
                        solution.error_message = f"Security Check Failed (on retry):\n{security_result.details}"
                        logger.warning(f"Retried solution {solution.id} failed security check")
                        return None
                except Exception as e:
                    logger.error(f"Security analysis error on retry for solution {solution.id}: {e}")
                    solution.status = "security_error"
                    solution.error_message = f"Security analysis failed on retry: {e}"
                    return None

                # Retry compilation
                import shlex
                try:
                    flags_list = shlex.split(solution.compiler_flags or "-O2 -std=c++20")
                except ValueError:
                    flags_list = ["-O2", "-std=c++20"]
                
                # Skip safety check as we just did it with SecurityAnalyzer
                success, stdout, stderr, output_path = await compile_solution(
                    source_code=solution.source_code,
                    compiler=solution.compiler or "g++-14",
                    flags=flags_list,
                    output_path=binary_path,
                    skip_safety_check=True
                )
                
                solution.compile_success = success
                solution.compile_log = f"STDOUT:\n{stdout}\n\nSTDERR:\n{stderr}"[:10000]
                
                if success:
                    solution.status = "compiled"
                    return solution, output_path
                
        except Exception as e:
            logger.error(f"Error retry failed for solution {solution.id}: {e}")
        
        return None
    
    async def _benchmark_solutions(
        self,
        run_id: int,
        round_id: int,
        compiled: List[Tuple[Solution, str]],
        test_cases_dir: str,
        problem: Problem,
        db_session: AsyncSession
    ):
        """
        Benchmark all compiled solutions.
        
        Args:
            run_id: The run ID
            round_id: The round ID
            compiled: List of (Solution, binary_path) tuples
            test_cases_dir: Directory containing test cases
            problem: The problem
            db_session: Database session
        """
        async def benchmark_single(solution: Solution, binary_path: str):
            """Benchmark a single solution."""
            await self.websocket_manager.broadcast_solution_status(
                run_id=run_id,
                solution_id=solution.id,
                model=solution.model_slug,
                status="benchmarking"
            )
            
            try:
                summary = await benchmark_solution(
                    solution_id=solution.id,
                    solution_binary_path=binary_path,
                    test_cases_dir=test_cases_dir,
                    time_limit_ms=problem.time_limit_ms,
                    memory_limit_mb=problem.memory_limit_mb,
                    db_session=db_session
                )
                
                # Broadcast test results
                for result in summary.test_results:
                    await self.websocket_manager.broadcast_test_result(
                        run_id=run_id,
                        solution_id=solution.id,
                        test_index=result.test_index,
                        verdict=result.verdict,
                        time_ms=result.time_ms
                    )
                
                solution.status = "completed" if summary.all_passed else "failed"
                
                await self.websocket_manager.broadcast_solution_status(
                    run_id=run_id,
                    solution_id=solution.id,
                    model=solution.model_slug,
                    status=solution.status,
                    details={
                        "tests_passed": summary.tests_passed,
                        "tests_total": summary.tests_total,
                        "avg_time_ms": summary.avg_time_ms
                    }
                )
                
            except Exception as e:
                logger.error(f"Benchmark failed for solution {solution.id}: {e}")
                solution.status = "benchmark_failed"
                solution.error_message = str(e)
                
                await self.websocket_manager.broadcast_solution_status(
                    run_id=run_id,
                    solution_id=solution.id,
                    model=solution.model_slug,
                    status="benchmark_failed"
                )
        
        # Benchmark all solutions in parallel
        tasks = [benchmark_single(sol, path) for sol, path in compiled]
        await asyncio.gather(*tasks)
        
        await db_session.commit()
    
    async def _score_solutions(
        self,
        round_id: int,
        config: RunConfig,
        db_session: AsyncSession
    ):
        """
        Calculate scores for all solutions in a round.
        
        Args:
            round_id: The round ID
            config: Run configuration
            db_session: Database session
        """
        # Get all solutions for this round
        result = await db_session.execute(
            select(Solution).where(Solution.round_id == round_id)
        )
        solutions = list(result.scalars().all())
        
        if not solutions:
            logger.warning(f"No solutions to score for round {round_id}")
            return
        
        # Find best metrics among correct solutions
        correct_solutions = [
            s for s in solutions
            if s.compile_success and s.tests_passed == s.tests_total and s.tests_total > 0
        ]
        
        if not correct_solutions:
            logger.warning(f"No correct solutions in round {round_id}")
            # All solutions get 0 score
            for sol in solutions:
                sol.score = 0.0
            await db_session.commit()
            return
        
        # Find fastest and lowest memory among correct solutions
        min_time_ms = min(
            (s.avg_time_ms for s in correct_solutions if s.avg_time_ms),
            default=1.0
        )
        min_memory_kb = min(
            (s.max_memory_kb for s in correct_solutions if s.max_memory_kb),
            default=1
        )
        
        # Score each solution
        for sol in solutions:
            # Check if solution passed compilation and all tests
            if not sol.compile_success or sol.tests_passed != sol.tests_total:
                sol.score = 0.0
                continue
            
            # Calculate correctness score
            correctness_score = sol.tests_passed / sol.tests_total if sol.tests_total > 0 else 0
            
            # Calculate speed score (normalized against fastest)
            if sol.avg_time_ms and sol.avg_time_ms > 0:
                speed_score = min_time_ms / sol.avg_time_ms
            else:
                speed_score = 0.0
            
            # Calculate memory score (normalized against lowest)
            if sol.max_memory_kb and sol.max_memory_kb > 0:
                memory_score = min_memory_kb / sol.max_memory_kb
            else:
                memory_score = 0.0
            
            # Calculate final score
            final_score = (
                correctness_score * config.correctness_weight +
                speed_score * config.speed_weight +
                memory_score * config.memory_weight +
                (sol.tests_passed - sol.tests_total) * config.penalty_wrong_answer
            )
            
            sol.score = max(0.0, final_score)  # Ensure non-negative
            
            logger.debug(
                f"Solution {sol.id} ({sol.model_slug}): "
                f"score={sol.score:.4f}, "
                f"correctness={correctness_score:.4f}, "
                f"speed={speed_score:.4f}, "
                f"memory={memory_score:.4f}"
            )
        
        await db_session.commit()
    
    def _parse_run_config(self, config_json: str) -> RunConfig:
        """
        Parse run configuration from JSON.
        
        Args:
            config_json: Configuration JSON string
            
        Returns:
            RunConfig object
        """
        try:
            data = json.loads(config_json)
        except json.JSONDecodeError:
            logger.warning("Failed to parse run config, using defaults")
            data = {}
        
        return RunConfig(
            models=data.get("models", []),
            num_rounds=data.get("num_rounds", 5),
            time_limit_ms=data.get("time_limit_ms", 2000),
            memory_limit_mb=data.get("memory_limit_mb", 256),
            max_tokens=data.get("max_tokens", 4096),
            temperature=data.get("temperature", 0.7),
            thinking_budget=data.get("thinking_budget"),
            correctness_weight=data.get("correctness_weight", 0.6),
            speed_weight=data.get("speed_weight", 0.25),
            memory_weight=data.get("memory_weight", 0.15),
            penalty_wrong_answer=data.get("penalty_wrong_answer", -0.5),
            allow_error_retry=data.get("allow_error_retry", True),
            max_error_retries=data.get("max_error_retries", 2),
            judge_model=data.get("judge_model", "anthropic/claude-3.5-sonnet"),
            security_models=data.get("security_models", ["anthropic/claude-3-haiku"])
        )
    
    async def _update_run_status(
        self,
        run_id: int,
        status: str,
        db_session: Optional[AsyncSession] = None
    ):
        """Update run status in database."""
        should_close = False
        if db_session is None:
            session_maker = get_session_maker()
            db_session = session_maker()
            should_close = True
        
        try:
            await db_session.execute(
                update(Run)
                .where(Run.id == run_id)
                .values(status=status)
            )
            await db_session.commit()
        except Exception as e:
            logger.error(f"Failed to update run {run_id} status: {e}")
            await db_session.rollback()
        finally:
            if should_close:
                await db_session.close()
    
    async def _update_round_status(
        self,
        round_id: int,
        status: str,
        db_session: AsyncSession
    ):
        """Update round status in database."""
        await db_session.execute(
            update(Round)
            .where(Round.id == round_id)
            .values(status=status)
        )
        await db_session.commit()
    
    async def pause_run(self, run_id: int):
        """
        Pause a running competition.
        
        Args:
            run_id: The run ID to pause
        """
        logger.info(f"Pausing run {run_id}")
        self._paused_runs.add(run_id)
        
        # Cancel the active task
        if run_id in self._active_runs:
            task = self._active_runs[run_id]
            task.cancel()
        
        await self._update_run_status(run_id, "paused")
        await self.websocket_manager.broadcast_run_status(
            run_id=run_id,
            status="paused",
            message="Run paused by user"
        )
    
    async def resume_run(self, run_id: int, num_rounds: int):
        """
        Resume a paused competition.
        
        Args:
            run_id: The run ID to resume
            num_rounds: Additional number of rounds to run
        """
        logger.info(f"Resuming run {run_id} for {num_rounds} more rounds")
        self._paused_runs.discard(run_id)
        
        await self.websocket_manager.broadcast_run_status(
            run_id=run_id,
            status="resumed",
            message=f"Run resumed for {num_rounds} more rounds"
        )
        
        # Start the run again
        await self.start_run(run_id, num_rounds)
    
    async def close(self):
        """Clean up resources."""
        logger.info("Closing OrchestrationEngine")
        
        # Cancel all active runs
        for run_id, task in list(self._active_runs.items()):
            logger.info(f"Cancelling run {run_id}")
            task.cancel()
        
        # Close model client
        if self.model_client:
            await self.model_client.close()
        
        # Close summarizer
        if self.summarizer:
            await self.summarizer.close()
