"""
Security analyzer for the AI Optimization Arena.

This module provides the SecurityAnalyzer class which uses LLMs to analyze
submitted C++ code and compiler flags for security risks.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

from backend.orchestrator.model_client import ModelClient, ModelResponse
from backend.orchestrator.prompts import PromptFormatter

logger = logging.getLogger(__name__)

@dataclass
class SecurityAnalysisResult:
    """Result of a security analysis."""
    is_safe: bool
    risk_level: str  # "LOW", "MEDIUM", "HIGH"
    details: str
    flags_safe: bool
    code_safe: bool
    model_responses: List[ModelResponse] = field(default_factory=list)

class SecurityAnalyzer:
    """
    Analyzes code and flags for security risks using AI models.
    Supports multi-model consensus.
    """
    
    def __init__(
        self,
        model_client: ModelClient,
        security_models: Optional[List[str]] = None
    ):
        """
        Initialize the SecurityAnalyzer.
        
        Args:
            model_client: ModelClient instance
            security_models: List of models to use for analysis.
                             If None, defaults to a standard capable model.
        """
        self.model_client = model_client
        # Default to a fast, capable model if none provided
        # using a widely available model as default
        self.security_models = security_models or ["google/gemini-3-flash-preview"]
        self.prompt_formatter = PromptFormatter()
        
    async def analyze_solution(
        self,
        source_code: str,
        compiler_flags: str,
        run_id: int,
        solution_id: int
    ) -> SecurityAnalysisResult:
        """
        Analyze a solution for security risks.
        
        Args:
            source_code: The C++ source code
            compiler_flags: The compiler flags string
            run_id: Run ID for logging
            solution_id: Solution ID for logging
            
        Returns:
            SecurityAnalysisResult
        """
        logger.info(f"Running security analysis for solution {solution_id} with models: {self.security_models}")
        
        tasks = []
        for model in self.security_models:
            tasks.append(self._analyze_single_model(model, source_code, compiler_flags))
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        is_safe = True
        max_risk_level = "LOW"
        reasons = []
        all_responses = []
        
        for i, result in enumerate(results):
            model_slug = self.security_models[i]
            
            if isinstance(result, Exception):
                logger.error(f"Security analysis failed for model {model_slug}: {result}")
                # Fail closed on error
                is_safe = False
                reasons.append(f"Analysis failed for {model_slug}: {str(result)}")
                continue
                
            response, parsed_json = result
            all_responses.append(response)
            
            if not parsed_json:
                logger.warning(f"Model {model_slug} returned invalid JSON")
                is_safe = False
                reasons.append(f"Invalid response from {model_slug}")
                continue
                
            model_safe = parsed_json.get("safe", False)
            risk = parsed_json.get("risk_level", "HIGH").upper()
            reason = parsed_json.get("reason", "No reason provided")
            
            logger.info(f"Security model {model_slug} verdict: safe={model_safe}, risk={risk}")
            
            if not model_safe or risk in ["HIGH", "MEDIUM"]:
                is_safe = False
                reasons.append(f"{model_slug}: {reason} (Risk: {risk})")
            
            # Update max risk logic
            if risk == "HIGH":
                max_risk_level = "HIGH"
            elif risk == "MEDIUM" and max_risk_level == "LOW":
                max_risk_level = "MEDIUM"

        return SecurityAnalysisResult(
            is_safe=is_safe,
            risk_level=max_risk_level,
            details="\n".join(reasons) if reasons else "All checks passed",
            flags_safe=True, # Simplified
            code_safe=True, # Simplified
            model_responses=all_responses
        )

    async def _analyze_single_model(
        self,
        model_slug: str,
        code: str,
        flags: str
    ) -> Tuple[ModelResponse, Optional[Dict]]:
        """Run analysis with a single model."""
        system_prompt = self.prompt_formatter.get_security_system_prompt()
        user_prompt = self.prompt_formatter.format_security_user_prompt(code=code, flags=flags)
        
        return await self.model_client.call_model_with_retry(
            model_slug=model_slug,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=1024,
            temperature=0.0, # Deterministic for security
            json_parse_retries=2
        )
