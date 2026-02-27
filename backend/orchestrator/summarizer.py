"""
Summarizer/Judge module for the AI Optimization Arena.

This module handles the judging logic, analyzing solutions from each round
and generating summaries to help competitors improve in subsequent rounds.
"""

import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.orchestrator.model_client import ModelClient, ModelResponse
from backend.orchestrator.prompts import PromptFormatter
from backend.database.models import ApiCall, Round, Solution, Problem, Run
from backend.database.session import get_session_maker

logger = logging.getLogger(__name__)


class Summarizer:
    """
    Judge/summarizer for analyzing round results.
    
    Uses an LLM to analyze submitted solutions and produce informative
    summaries that help competitors improve in the next round.
    """
    
    def __init__(
        self,
        model_client: Optional[ModelClient] = None,
        judge_model: str = "anthropic/claude-3.5-sonnet",
        max_tokens: int = 2048,
        temperature: float = 0.7
    ):
        """
        Initialize the Summarizer.
        
        Args:
            model_client: ModelClient instance (created if None)
            judge_model: Model slug to use for judging
            max_tokens: Maximum tokens for judge response
            temperature: Temperature for judge model
        """
        self.model_client = model_client or ModelClient()
        self.judge_model = judge_model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.prompt_formatter = PromptFormatter()
        
        logger.info(f"Summarizer initialized with judge model: {judge_model}")
    
    async def summarize_round(
        self,
        round_id: int,
        solutions: List[Solution],
        problem: Problem,
        db_session: AsyncSession
    ) -> str:
        """
        Summarize a completed round and generate feedback for competitors.
        
        Args:
            round_id: The round ID being summarized
            solutions: List of Solution objects from the round
            problem: The Problem being solved
            db_session: Database session for storing API call
            
        Returns:
            Summary text from the judge model
        """
        logger.info(f"Summarizing round {round_id} with {len(solutions)} solutions")
        
        # Build round results data
        round_results = self._format_round_results(solutions)
        
        # Build cumulative standings
        cumulative_standings = await self._build_cumulative_standings(
            round_id, solutions, db_session
        )
        
        # Format prompts
        system_prompt = self.prompt_formatter.get_judge_system_prompt()
        user_prompt = self.prompt_formatter.format_judge_user(
            problem_description=problem.description_md,
            round_number=solutions[0].round.round_number if solutions else 1,
            round_results=round_results,
            cumulative_standings=cumulative_standings
        )
        
        # Call judge model
        try:
            response, parsed_json = await self.model_client.call_model_with_retry(
                model_slug=self.judge_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=self.max_tokens,
                temperature=self.temperature
            )
            
            # Store API call
            await self._store_judge_api_call(
                round_id=round_id,
                response=response,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                db_session=db_session
            )
            
            # Extract summary from response
            summary_text = self._extract_summary(response.response_text, parsed_json)
            
            logger.info(f"Round {round_id} summary generated: {len(summary_text)} chars")
            return summary_text
            
        except Exception as e:
            logger.error(f"Failed to generate summary for round {round_id}: {e}")
            # Return a basic summary on error
            return self._generate_fallback_summary(solutions)
    
    def _format_round_results(self, solutions: List[Solution]) -> str:
        """
        Format solution data for the judge prompt.
        
        Args:
            solutions: List of Solution objects
            
        Returns:
            Formatted round results string
        """
        solutions_data = []
        
        for sol in solutions:
            # Get approach explanation from the solution's API calls
            approach = self._extract_approach_from_solution(sol)
            
            # Truncate code to 30 lines
            code = sol.source_code or ""
            code_lines = code.split('\n')
            if len(code_lines) > 30:
                code = '\n'.join(code_lines[:30]) + f"\n\n... ({len(code_lines) - 30} more lines)"
            
            solutions_data.append({
                'model': sol.model_slug,
                'approach': approach,
                'score': sol.score,
                'tests_passed': sol.tests_passed,
                'tests_total': sol.tests_total,
                'avg_time_ms': sol.avg_time_ms or 0.0,
                'max_memory_kb': sol.max_memory_kb or 0,
                'code': code
            })
        
        return self.prompt_formatter.format_round_results(solutions_data, max_code_lines=30)
    
    def _extract_approach_from_solution(self, solution: Solution) -> str:
        """
        Extract the approach explanation from a solution's API calls.
        
        Args:
            solution: Solution object
            
        Returns:
            Approach explanation or default message
        """
        # Look for the API call that generated this solution
        # In async mode, we cannot safely access relationships like api_calls lazily
        return "No explanation provided"
        
        return "No explanation available"
    
    async def _build_cumulative_standings(
        self,
        round_id: int,
        current_solutions: List[Solution],
        db_session: AsyncSession
    ) -> str:
        """
        Build cumulative standings across all rounds so far.
        
        Args:
            round_id: Current round ID
            current_solutions: Solutions from current round
            db_session: Database session
            
        Returns:
            Formatted standings string
        """
        # Get the run ID
        if not current_solutions:
            return "No standings available."
        
        run_id = current_solutions[0].round.run_id
        
        # Query all rounds for this run
        from sqlalchemy import func
        
        result = await db_session.execute(
            select(
                Solution.model_slug,
                func.sum(Solution.score).label("total_score"),
                func.count(Solution.id).label("solutions_count")
            )
            .join(Round)
            .where(Round.run_id == run_id)
            .group_by(Solution.model_slug)
            .order_by(func.sum(Solution.score).desc())
        )
        
        standings = []
        for row in result:
            standings.append((
                row.model_slug,
                float(row.total_score or 0),
                row.solutions_count or 0
            ))
        
        return self.prompt_formatter.format_cumulative_standings(standings)
    
    async def _store_judge_api_call(
        self,
        round_id: int,
        response: ModelResponse,
        system_prompt: str,
        user_prompt: str,
        db_session: AsyncSession
    ):
        """
        Store the judge API call in the database.
        
        Args:
            round_id: The round ID
            response: ModelResponse from the API call
            system_prompt: System prompt used
            user_prompt: User prompt used
            db_session: Database session
        """
        try:
            # Get run_id from round
            round_result = await db_session.execute(
                select(Round).where(Round.id == round_id)
            )
            round_obj = round_result.scalar_one_or_none()
            
            if not round_obj:
                logger.error(f"Round {round_id} not found for storing API call")
                return
            
            api_call = ApiCall(
                run_id=round_obj.run_id,
                round_id=round_id,
                solution_id=None,  # Judge calls are not associated with a specific solution
                purpose="judge",
                model_slug=self.judge_model,
                prompt_text=f"System:\n{system_prompt}\n\nUser:\n{user_prompt}",
                thinking_text=response.thinking_text,
                response_text=response.response_text,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                thinking_tokens=response.thinking_tokens,
                cost_usd=response.cost_usd,
                latency_ms=response.latency_ms
            )
            
            db_session.add(api_call)
            await db_session.commit()
            
            logger.debug(f"Stored judge API call for round {round_id}")
            
        except Exception as e:
            logger.error(f"Failed to store judge API call: {e}")
            await db_session.rollback()
    
    def _extract_summary(
        self,
        response_text: str,
        parsed_json: Optional[Dict[str, Any]]
    ) -> str:
        """
        Extract the summary text from the model response.
        
        Args:
            response_text: Raw response text
            parsed_json: Parsed JSON if available
            
        Returns:
            Clean summary text
        """
        # If we have parsed JSON, look for common summary fields
        if parsed_json:
            # Try common field names
            for key in ["summary", "analysis", "feedback", "report", "content", "text"]:
                if key in parsed_json:
                    value = parsed_json[key]
                    if isinstance(value, str):
                        return value
                    elif isinstance(value, list) and value:
                        return "\n".join(str(item) for item in value)
        
        # Otherwise, return the raw response text (cleaned up)
        # Remove markdown code blocks if present
        text = response_text
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first and last lines if they're code block markers
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)
        
        return text.strip()
    
    def _generate_fallback_summary(self, solutions: List[Solution]) -> str:
        """
        Generate a basic summary when the judge model fails.
        
        Args:
            solutions: List of solutions
            
        Returns:
            Basic summary string
        """
        if not solutions:
            return "No solutions to summarize."
        
        lines = ["## Round Summary (Auto-Generated)", ""]
        
        # Sort by score
        sorted_solutions = sorted(solutions, key=lambda s: s.score, reverse=True)
        
        lines.append("### Rankings")
        for i, sol in enumerate(sorted_solutions, 1):
            status = "✓" if sol.tests_passed == sol.tests_total else "✗"
            lines.append(
                f"{i}. {sol.model_slug}: {sol.score:.4f} "
                f"({sol.tests_passed}/{sol.tests_total} tests) {status}"
            )
        
        lines.append("")
        lines.append("### Observations")
        
        # Find best solution
        best = sorted_solutions[0]
        lines.append(
            f"- Best solution by {best.model_slug} with score {best.score:.4f}"
        )
        
        if best.avg_time_ms:
            lines.append(f"- Fastest average time: {best.avg_time_ms:.2f}ms")
        
        # Count passing solutions
        passing = sum(1 for s in solutions if s.tests_passed == s.tests_total)
        lines.append(f"- {passing}/{len(solutions)} solutions passed all tests")
        
        lines.append("")
        lines.append(
            "Focus on improving algorithm efficiency and handling edge cases "
            "in the next round."
        )
        
        return "\n".join(lines)
    
    async def close(self):
        """Close the model client."""
        if self.model_client:
            await self.model_client.close()
