"""
OpenRouter API client for the AI Optimization Arena.

This module provides an async HTTP client for interacting with the OpenRouter API,
including rate limiting, retries, cost calculation, and response parsing.
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Dict, Optional, Tuple
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


# Cache for model pricing (1 hour TTL)
_PRICING_CACHE: Dict[str, Any] = {}
_PRICING_CACHE_TIMESTAMP: float = 0
_PRICING_CACHE_TTL_SECONDS = 3600  # 1 hour


@dataclass
class ModelResponse:
    """Response from a model API call."""
    response_text: str
    input_tokens: int
    output_tokens: int
    thinking_tokens: int
    cost_usd: float
    latency_ms: int
    thinking_text: Optional[str] = None


class ModelClientError(Exception):
    """Raised when model API call fails."""
    pass


class RateLimitError(ModelClientError):
    """Raised when rate limit is hit."""
    pass


class ModelClient:
    """
    Async client for the OpenRouter API.
    
    Handles model API calls with:
    - Automatic pricing fetching and caching
    - Rate limit handling with exponential backoff
    - Cost calculation
    - Timeout handling
    - Retry logic for transient failures
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://openrouter.ai/api/v1"
    ):
        """
        Initialize the ModelClient.
        
        Args:
            api_key: OpenRouter API key (defaults to OPENROUTER_API_KEY env var)
            base_url: OpenRouter API base URL
        """
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self.base_url = base_url
        
        if not self.api_key:
            logger.warning("No OpenRouter API key provided. Set OPENROUTER_API_KEY env var.")
        
        # Create async HTTP client
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://ai-optimization-arena.local",
                "X-Title": "AI Optimization Arena"
            },
            timeout=120.0  # 120 second timeout per request
        )
        
        logger.info(f"ModelClient initialized with base URL: {base_url}")
    
    async def fetch_pricing(self, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Fetch model pricing from OpenRouter API.
        
        Prices are cached for 1 hour to avoid repeated API calls.
        
        Args:
            force_refresh: If True, bypass cache and fetch fresh data
            
        Returns:
            Dictionary mapping model slugs to pricing info
        """
        global _PRICING_CACHE, _PRICING_CACHE_TIMESTAMP
        
        # Check if cache is still valid
        current_time = time.time()
        if not force_refresh and _PRICING_CACHE:
            if current_time - _PRICING_CACHE_TIMESTAMP < _PRICING_CACHE_TTL_SECONDS:
                logger.debug("Using cached pricing data")
                return _PRICING_CACHE
        
        # Fetch fresh pricing data
        try:
            logger.info("Fetching model pricing from OpenRouter...")
            response = await self.client.get("/models")
            response.raise_for_status()
            
            data = response.json()
            models = data.get("data", [])
            
            # Build pricing cache
            pricing = {}
            for model in models:
                model_id = model.get("id", "")
                pricing_info = model.get("pricing", {})
                pricing[model_id] = {
                    "input_price": float(pricing_info.get("prompt", 0)),
                    "output_price": float(pricing_info.get("completion", 0)),
                    "name": model.get("name", model_id),
                }
            
            _PRICING_CACHE = pricing
            _PRICING_CACHE_TIMESTAMP = current_time
            
            logger.info(f"Cached pricing for {len(pricing)} models")
            return pricing
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching pricing: {e.response.status_code} - {e.response.text}")
            # Return cached data if available, even if expired
            if _PRICING_CACHE:
                logger.warning("Using expired cached pricing due to fetch error")
                return _PRICING_CACHE
            raise ModelClientError(f"Failed to fetch pricing: {e}")
            
        except Exception as e:
            logger.error(f"Error fetching pricing: {e}")
            if _PRICING_CACHE:
                logger.warning("Using expired cached pricing due to fetch error")
                return _PRICING_CACHE
            raise ModelClientError(f"Failed to fetch pricing: {e}")
    
    def _calculate_cost(
        self,
        model_slug: str,
        input_tokens: int,
        output_tokens: int,
        pricing: Dict[str, Any]
    ) -> float:
        """
        Calculate the cost of an API call.
        
        Args:
            model_slug: The model used
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens
            pricing: Pricing dictionary from fetch_pricing
            
        Returns:
            Cost in USD
        """
        model_pricing = pricing.get(model_slug, {})
        
        # Prices are per 1M tokens
        input_price = model_pricing.get("input_price", 0)
        output_price = model_pricing.get("output_price", 0)
        
        input_cost = (input_tokens / 1_000_000) * input_price
        output_cost = (output_tokens / 1_000_000) * output_price
        
        total_cost = input_cost + output_cost
        
        logger.debug(
            f"Cost calculation for {model_slug}: "
            f"input={input_tokens} tokens @ ${input_price}/1M, "
            f"output={output_tokens} tokens @ ${output_price}/1M, "
            f"total=${total_cost:.6f}"
        )
        
        return total_cost
    
    async def call_model(
        self,
        model_slug: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        thinking_budget: Optional[int] = None,
        max_retries: int = 3
    ) -> ModelResponse:
        """
        Call a model via the OpenRouter API.
        
        Args:
            model_slug: The model identifier (e.g., "anthropic/claude-3.5-sonnet")
            system_prompt: System prompt text
            user_prompt: User prompt text
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature (0.0 - 2.0)
            thinking_budget: Optional thinking budget for models that support it
            max_retries: Maximum number of retries for rate limits
            
        Returns:
            ModelResponse with parsed results
            
        Raises:
            ModelClientError: If the API call fails after all retries
            RateLimitError: If rate limited and retries exhausted
        """
        # Fetch pricing data
        pricing = await self.fetch_pricing()
        
        # Build request payload
        payload = {
            "model": model_slug,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        # Add thinking budget if specified
        if thinking_budget is not None and thinking_budget > 0:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget
            }
        
        # Retry logic with exponential backoff
        last_error = None
        base_delay = 2.0  # Start with 2 seconds
        
        for attempt in range(max_retries):
            start_time = time.time()
            
            try:
                logger.debug(f"Calling model {model_slug} (attempt {attempt + 1}/{max_retries})")
                
                response = await self.client.post(
                    "/chat/completions",
                    json=payload
                )
                
                latency_ms = int((time.time() - start_time) * 1000)
                
                # Handle rate limiting
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else base_delay * (2 ** attempt)
                    logger.warning(f"Rate limited. Retrying after {delay}s...")
                    await asyncio.sleep(delay)
                    continue
                
                # Handle other HTTP errors
                response.raise_for_status()
                
                # Parse response
                data = response.json()
                
                # Extract content
                choices = data.get("choices", [])
                if not choices:
                    raise ModelClientError("No choices in API response")
                
                message = choices[0].get("message", {})
                content = message.get("content", "")
                
                # Extract thinking content if available (Claude extended thinking)
                thinking_text = None
                thinking_tokens = 0
                
                # Check for thinking content in various formats
                if "thinking" in message:
                    thinking_data = message["thinking"]
                    if isinstance(thinking_data, dict):
                        thinking_text = thinking_data.get("content", "")
                        thinking_tokens = thinking_data.get("tokens", 0)
                    elif isinstance(thinking_data, str):
                        thinking_text = thinking_data
                
                # Extract usage
                usage = data.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                
                # Some APIs report total tokens differently
                if "total_tokens" in usage and output_tokens == 0:
                    output_tokens = usage["total_tokens"] - input_tokens
                
                # Calculate cost
                cost_usd = self._calculate_cost(
                    model_slug, input_tokens, output_tokens, pricing
                )
                
                logger.info(
                    f"Model call successful: {model_slug}, "
                    f"tokens={input_tokens}+{output_tokens}, "
                    f"cost=${cost_usd:.6f}, latency={latency_ms}ms"
                )
                
                return ModelResponse(
                    response_text=content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    thinking_tokens=thinking_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    thinking_text=thinking_text
                )
                
            except httpx.TimeoutException as e:
                last_error = f"Request timeout: {e}"
                logger.error(f"Timeout calling model {model_slug}: {e}")
                # Don't retry on timeout, just fail
                break
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    last_error = f"Rate limited: {e.response.text}"
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Rate limited on attempt {attempt + 1}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    last_error = f"HTTP error {e.response.status_code}: {e.response.text}"
                    logger.error(f"HTTP error calling model {model_slug}: {last_error}")
                    # Don't retry on 4xx errors except 429
                    if 400 <= e.response.status_code < 500:
                        break
                    # Retry on 5xx errors
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                    
            except Exception as e:
                last_error = f"Unexpected error: {e}"
                logger.error(f"Error calling model {model_slug}: {e}")
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
        
        # All retries exhausted
        error_msg = f"Failed to call model {model_slug} after {max_retries} attempts: {last_error}"
        logger.error(error_msg)
        raise ModelClientError(error_msg)
    
    async def call_model_with_retry(
        self,
        model_slug: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        thinking_budget: Optional[int] = None,
        json_parse_retries: int = 1
    ) -> Tuple[ModelResponse, Optional[Dict]]:
        """
        Call a model with additional retry logic for JSON parsing failures.
        
        Args:
            model_slug: The model identifier
            system_prompt: System prompt text
            user_prompt: User prompt text
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            thinking_budget: Optional thinking budget
            json_parse_retries: Number of retries for JSON parsing failures
            
        Returns:
            Tuple of (ModelResponse, parsed_json or None)
        """
        response = await self.call_model(
            model_slug=model_slug,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking_budget=thinking_budget
        )
        
        # Try to parse JSON
        parsed_json = None
        content = response.response_text
        
        for attempt in range(json_parse_retries + 1):
            try:
                # Look for JSON object in the response
                json_start = content.find("{")
                json_end = content.rfind("}")
                
                if json_start != -1 and json_end != -1 and json_end > json_start:
                    json_str = content[json_start:json_end + 1]
                    parsed_json = json.loads(json_str)
                    break
                else:
                    raise json.JSONDecodeError("No JSON object found", content, 0)
                    
            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse error (attempt {attempt + 1}): {e}")
                
                if attempt < json_parse_retries:
                    # Retry with a reminder to output valid JSON
                    retry_prompt = user_prompt + "\n\nIMPORTANT: Your previous response was not valid JSON. Please ensure your response is a valid JSON object only, with no additional text."
                    
                    response = await self.call_model(
                        model_slug=model_slug,
                        system_prompt=system_prompt,
                        user_prompt=retry_prompt,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        thinking_budget=thinking_budget
                    )
                    content = response.response_text
                else:
                    logger.error(f"Failed to parse JSON after {json_parse_retries + 1} attempts")
        
        return response, parsed_json
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()
        logger.info("ModelClient closed")
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()
