"""
Summarizer/Judge module for the AI Optimization Arena.

This module handles the judging logic, analyzing solutions from each round
and generating summaries to help competitors improve in subsequent rounds.
"""

import logging
from typing import List, Dict, Any, Optional
import json

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.orchestrator.model_client import ModelClient, ModelResponse
from backend.orchestrator.prompts import PromptFormatter
from backend.database.models import ApiCall, Round, Solution, Problem

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
        judge_model: str = "google/gemini-3-flash-preview",
        max_tokens: int = 2048,
        temperature: float = 0.7
    ):
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

        # Resolve round_number via DB to avoid lazy-loading the relationship
        round_number = await self._get_round_number(round_id, db_session)

        # Build round results data
        round_results = await self._format_round_results(solutions, db_session)

        # Build cumulative standings
        cumulative_standings = await self._build_cumulative_standings(
            round_id, db_session
        )

        # Format prompts
        system_prompt = self.prompt_formatter.get_judge_system_prompt()
        user_prompt = self.prompt_formatter.format_judge_user(
            problem_description=problem.description_md,
            round_number=round_number,
            round_results=round_results,
            cumulative_standings=cumulative_standings
        )

        try:
            response, parsed_json = await self.model_client.call_model_with_retry(
                model_slug=self.judge_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=self.max_tokens,
                temperature=self.temperature
            )

            await self._store_judge_api_call(
                round_id=round_id,
                response=response,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                db_session=db_session
            )

            summary_text = self._extract_summary(response.response_text, parsed_json)
            logger.info(f"Round {round_id} summary generated: {len(summary_text)} chars")
            return summary_text

        except Exception as e:
            logger.error(f"Failed to generate summary for round {round_id}: {e}")
            return self._generate_fallback_summary(solutions)

    async def _get_round_number(self, round_id: int, db_session: AsyncSession) -> int:
        """Query round_number from DB to avoid lazy relationship access."""
        result = await db_session.execute(
            select(Round.round_number).where(Round.id == round_id)
        )
        round_number = result.scalar_one_or_none()
        if round_number is None:
            logger.warning(f"Round {round_id} not found when fetching round_number, defaulting to 1")
            return 1
        return round_number

    async def _format_round_results(
        self, solutions: List[Solution], db_session: AsyncSession
    ) -> str:
        solutions_data = []

        for sol in solutions:
            approach = await self._extract_approach_from_solution(sol, db_session)

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

    async def _extract_approach_from_solution(
        self, solution: Solution, db_session: AsyncSession
    ) -> str:
        """
        Extract the explanation field from the solution_generation ApiCall
        stored for this solution.
        """
        result = await db_session.execute(
            select(ApiCall.response_text)
            .where(
                ApiCall.solution_id == solution.id,
                ApiCall.purpose == "solution_generation"
            )
            .limit(1)
        )
        response_text = result.scalar_one_or_none()

        if not response_text:
            return "No explanation provided"

        try:
            parsed = json.loads(response_text)
            explanation = parsed.get("explanation", "")
            return explanation if explanation else "No explanation provided"
        except (json.JSONDecodeError, AttributeError):
            return "No explanation provided"

    async def _build_cumulative_standings(
        self,
        round_id: int,
        db_session: AsyncSession
    ) -> str:
        """
        Build cumulative standings across all rounds so far.

        Resolves run_id via DB query on round_id to avoid lazy relationship access.
        """
        # Get run_id from the round directly — avoids lazy-loading solution.round
        round_result = await db_session.execute(
            select(Round.run_id).where(Round.id == round_id)
        )
        run_id = round_result.scalar_one_or_none()

        if run_id is None:
            return "No standings available."

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

        standings = [
            (row.model_slug, float(row.total_score or 0), row.solutions_count or 0)
            for row in result
        ]

        return self.prompt_formatter.format_cumulative_standings(standings)

    async def _store_judge_api_call(
        self,
        round_id: int,
        response: ModelResponse,
        system_prompt: str,
        user_prompt: str,
        db_session: AsyncSession
    ):
        """Store the judge API call in the database."""
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
            solution_id=None,
            purpose="judge",
            model_slug=self.judge_model,
            prompt_text=f"System:\n{system_prompt}\n\nUser:\n{user_prompt}",
            thinking_text=getattr(response, "thinking_text", None),
            response_text=response.response_text,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            thinking_tokens=response.thinking_tokens,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms
        )

        db_session.add(api_call)
        try:
            await db_session.commit()
            logger.debug(f"Stored judge API call for round {round_id}")
        except Exception as e:
            logger.error(f"Failed to store judge API call for round {round_id}: {e}")
            await db_session.rollback()

    def _extract_summary(
        self,
        response_text: str,
        parsed_json: Optional[Dict[str, Any]]
    ) -> str:
        if parsed_json:
            for key in ["summary", "analysis", "feedback", "report", "content", "text"]:
                if key in parsed_json:
                    value = parsed_json[key]
                    if isinstance(value, str):
                        return value
                    elif isinstance(value, list) and value:
                        return "\n".join(str(item) for item in value)

        text = response_text
        if text.startswith("```"):
            lines = text.split("\n")
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        return text.strip()

    def _generate_fallback_summary(self, solutions: List[Solution]) -> str:
        if not solutions:
            return "No solutions to summarize."

        lines = ["## Round Summary (Auto-Generated)", ""]

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

        best = sorted_solutions[0]
        lines.append(f"- Best solution by {best.model_slug} with score {best.score:.4f}")

        if best.avg_time_ms:
            lines.append(f"- Fastest average time: {best.avg_time_ms:.2f}ms")

        passing = sum(1 for s in solutions if s.tests_passed == s.tests_total)
        lines.append(f"- {passing}/{len(solutions)} solutions passed all tests")

        lines.append("")
        lines.append(
            "Focus on improving algorithm efficiency and handling edge cases "
            "in the next round."
        )

        return "\n".join(lines)

    async def close(self):
        if self.model_client:
            await self.model_client.close()