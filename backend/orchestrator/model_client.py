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
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Cost reported directly by OpenRouter (includes cache discounts/surcharges
    # applied by the upstream provider). None if not present in the response.
    reported_cost_usd: Optional[float] = None


class ModelClientError(Exception):
    """Raised when model API call fails."""
    pass


class RateLimitError(ModelClientError):
    """Raised when rate limit is hit and all retries are exhausted."""
    pass


class ModelClient:
    """
    Async client for the OpenRouter API.
    
    Handles model API calls with:
    - Automatic pricing fetching and caching
    - Rate limit handling with exponential backoff
    - Cost calculation
    - Timeout handling with retry
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
        
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://ai-optimization-arena.local",
                "X-Title": "AI Optimization Arena"
            },
            timeout=120.0
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
        
        current_time = time.time()
        if not force_refresh and _PRICING_CACHE_TIMESTAMP > 0:
            if current_time - _PRICING_CACHE_TIMESTAMP < _PRICING_CACHE_TTL_SECONDS:
                logger.debug("Using cached pricing data")
                return _PRICING_CACHE
        
        try:
            logger.info("Fetching model pricing from OpenRouter...")
            response = await self.client.get("/models")
            response.raise_for_status()
            
            data = response.json()
            models = data.get("data", [])
            
            pricing = {}
            for model in models:
                model_id = model.get("id", "")
                pricing_info = model.get("pricing", {})
                entry = {
                    "input_price": float(pricing_info.get("prompt", 0)),
                    "output_price": float(pricing_info.get("completion", 0)),
                    "name": model.get("name", model_id),
                }
                # Cache pricing fields (absent means provider does not expose them;
                # fall back to input_price multiples at cost-calculation time).
                if "cache_read" in pricing_info:
                    entry["cache_read_price"] = float(pricing_info["cache_read"])
                if "cache_write" in pricing_info:
                    entry["cache_write_price"] = float(pricing_info["cache_write"])
                pricing[model_id] = entry
            
            _PRICING_CACHE = pricing
            _PRICING_CACHE_TIMESTAMP = current_time
            
            logger.info(f"Cached pricing for {len(pricing)} models")
            return pricing
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching pricing: {e.response.status_code} - {e.response.text}")
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
        pricing: Dict[str, Any],
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """
        Calculate the cost of an API call, including cached context.

        Cache read/write prices are taken from the pricing dict when available
        (populated from the OpenRouter /models endpoint). If they are absent,
        the method falls back to the most common multiples of the base input
        price (0.1x for reads, 1.25x for writes) so cost estimation degrades
        gracefully rather than silently ignoring cache tokens.

        Args:
            model_slug: The model used
            input_tokens: Number of non-cached input tokens
            output_tokens: Number of output tokens
            pricing: Pricing dictionary from fetch_pricing
            cache_read_tokens: Tokens read from the cache (cache hits)
            cache_write_tokens: Tokens written to the cache (cache misses that
                                were stored for future reuse)

        Returns:
            Estimated cost in USD
        """
        model_pricing = pricing.get(model_slug, {})

        input_price = model_pricing.get("input_price", 0)
        output_price = model_pricing.get("output_price", 0)
        # Use explicit cache prices when the endpoint provides them; otherwise
        # fall back to the Anthropic defaults (most conservative estimate).
        cache_read_price = model_pricing.get("cache_read_price", input_price * 0.1)
        cache_write_price = model_pricing.get("cache_write_price", input_price * 1.25)

        input_cost = (input_tokens / 1_000_000) * input_price
        output_cost = (output_tokens / 1_000_000) * output_price
        cache_read_cost = (cache_read_tokens / 1_000_000) * cache_read_price
        cache_write_cost = (cache_write_tokens / 1_000_000) * cache_write_price

        total_cost = input_cost + output_cost + cache_read_cost + cache_write_cost

        logger.debug(
            f"Cost calculation for {model_slug}: "
            f"input={input_tokens} @ ${input_price}/1M, "
            f"output={output_tokens} @ ${output_price}/1M, "
            f"cache_read={cache_read_tokens} @ ${cache_read_price}/1M, "
            f"cache_write={cache_write_tokens} @ ${cache_write_price}/1M, "
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
            max_retries: Maximum number of attempts (including the first)
            
        Returns:
            ModelResponse with parsed results
            
        Raises:
            ModelClientError: If the API call fails after all retries
            RateLimitError: If rate limited and all retries are exhausted
        """
        pricing = await self.fetch_pricing()
        
        payload = {
            "model": model_slug,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        
        if thinking_budget is not None and thinking_budget > 0:
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget
            }
        
        last_error = None
        rate_limited = False
        base_delay = 2.0
        
        for attempt in range(max_retries):
            start_time = time.time()
            
            try:
                logger.debug(f"Calling model {model_slug} (attempt {attempt + 1}/{max_retries})")
                
                response = await self.client.post("/chat/completions", json=payload)
                
                latency_ms = int((time.time() - start_time) * 1000)
                
                # Handle rate limiting via status code (before raise_for_status)
                if response.status_code == 429:
                    rate_limited = True
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else base_delay * (2 ** attempt)
                    last_error = f"Rate limited (attempt {attempt + 1})"
                    logger.warning(f"Rate limited. Retrying after {delay}s...")
                    await asyncio.sleep(delay)
                    continue
                
                response.raise_for_status()
                
                data = response.json()
                
                choices = data.get("choices", [])
                if not choices:
                    raise ModelClientError("No choices in API response")
                
                message = choices[0].get("message", {})
                content = message.get("content", "")
                
                thinking_text = None
                thinking_tokens = 0
                
                if "thinking" in message:
                    thinking_data = message["thinking"]
                    if isinstance(thinking_data, dict):
                        thinking_text = thinking_data.get("content", "")
                        thinking_tokens = thinking_data.get("tokens", 0)
                    elif isinstance(thinking_data, str):
                        thinking_text = thinking_data
                
                usage = data.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)

                if "total_tokens" in usage and output_tokens == 0:
                    output_tokens = usage["total_tokens"] - input_tokens

                prompt_details = usage.get("prompt_tokens_details", {})
                cache_read_tokens = prompt_details.get("cached_tokens", 0)
                cache_write_tokens = prompt_details.get("cache_write_tokens", 0)

                # OpenRouter reports the actual charged cost in usage.cost (USD
                # cents as a float, or 0 when not present). Use it for
                # transparency; also compute our own estimate for attribution.
                reported_cost_usd: Optional[float] = None
                if "cost" in usage:
                    reported_cost_usd = float(usage["cost"])

                cost_usd = self._calculate_cost(
                    model_slug, input_tokens, output_tokens, pricing,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                )
                
                logger.info(
                    f"Model call successful: {model_slug}, "
                    f"tokens={input_tokens}+{output_tokens}, "
                    f"cache_read={cache_read_tokens}, cache_write={cache_write_tokens}, "
                    f"cost=${cost_usd:.6f}"
                    + (f" (reported: ${reported_cost_usd:.6f})" if reported_cost_usd is not None else "")
                    + f", latency={latency_ms}ms"
                )
                
                return ModelResponse(
                    response_text=content,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    thinking_tokens=thinking_tokens,
                    cost_usd=cost_usd,
                    latency_ms=latency_ms,
                    thinking_text=thinking_text,
                    cache_read_tokens=cache_read_tokens,
                    cache_write_tokens=cache_write_tokens,
                    reported_cost_usd=reported_cost_usd,
                )
                
            except httpx.TimeoutException as e:
                last_error = f"Request timeout: {e}"
                logger.error(f"Timeout calling model {model_slug} (attempt {attempt + 1}): {e}")
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
                
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    rate_limited = True
                    last_error = f"Rate limited: {e.response.text}"
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Rate limited on attempt {attempt + 1}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    last_error = f"HTTP error {e.response.status_code}: {e.response.text}"
                    logger.error(f"HTTP error calling model {model_slug}: {last_error}")
                    if 400 <= e.response.status_code < 500:
                        break
                    delay = base_delay * (2 ** attempt)
                    await asyncio.sleep(delay)
                    continue
                    
            except ModelClientError:
                raise

            except Exception as e:
                last_error = f"Unexpected error: {e}"
                logger.error(f"Error calling model {model_slug}: {e}")
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
                continue
        
        error_msg = f"Failed to call model {model_slug} after {max_retries} attempts: {last_error}"
        logger.error(error_msg)
        if rate_limited:
            raise RateLimitError(error_msg)
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
            json_parse_retries: Number of additional attempts if JSON parsing fails (0 = no retry)
            
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
        
        parsed_json = None
        content = response.response_text
        
        for attempt in range(json_parse_retries + 1):
            try:
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
                    retry_prompt = (
                        user_prompt
                        + "\n\nIMPORTANT: Your previous response was not valid JSON. "
                        "Please ensure your response is a valid JSON object only, with no additional text."
                    )
                    
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
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()