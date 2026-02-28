import pytest
import asyncio
import json
import os
import time
from unittest.mock import patch, MagicMock, AsyncMock, call
import httpx
from backend.orchestrator.model_client import (
    ModelClient, 
    ModelResponse, 
    ModelClientError, 
    RateLimitError,
    _PRICING_CACHE,
    _PRICING_CACHE_TIMESTAMP
)

@pytest.fixture(autouse=True)
def reset_pricing_cache():
    """Reset the global pricing cache before each test."""
    import backend.orchestrator.model_client
    backend.orchestrator.model_client._PRICING_CACHE = {}
    backend.orchestrator.model_client._PRICING_CACHE_TIMESTAMP = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_chat_response(
    content="Hello world",
    prompt_tokens=20,
    completion_tokens=30,
    cache_read_tokens=0,
    cache_write_tokens=0,
    reported_cost=None,
):
    mock = MagicMock()
    mock.status_code = 200
    usage: dict = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cache_read_tokens or cache_write_tokens:
        usage["prompt_tokens_details"] = {
            "cached_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
        }
    if reported_cost is not None:
        usage["cost"] = reported_cost
    mock.json.return_value = {
        "choices": [{"message": {"content": content}}],
        "usage": usage,
    }
    return mock


def _make_http_error(status_code, text="Error"):
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = text
    mock_resp.headers = {}
    error = httpx.HTTPStatusError("Error", request=MagicMock(spec=httpx.Request), response=mock_resp)
    mock_resp.raise_for_status.side_effect = error
    return mock_resp, error


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_client_init_explicit_key():
    client = ModelClient(api_key="test_key")
    assert client.api_key == "test_key"
    assert client.base_url == "https://openrouter.ai/api/v1"
    await client.close()


@pytest.mark.asyncio
async def test_model_client_init_env_key():
    with patch.dict(os.environ, {"OPENROUTER_API_KEY": "env_key"}):
        client = ModelClient()
        assert client.api_key == "env_key"
        await client.close()


@pytest.mark.asyncio
async def test_model_client_init_no_key_warns():
    with patch.dict(os.environ, {}, clear=True):
        with patch("backend.orchestrator.model_client.logger") as mock_logger:
            client = ModelClient()
            assert client.api_key == ""
            mock_logger.warning.assert_called_with(
                "No OpenRouter API key provided. Set OPENROUTER_API_KEY env var."
            )
            await client.close()


# ---------------------------------------------------------------------------
# fetch_pricing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_pricing_success():
    client = ModelClient(api_key="test_key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [
            {"id": "model1", "name": "Model 1", "pricing": {"prompt": "1.0", "completion": "2.0"}},
            {"id": "model2", "pricing": {"prompt": "0.5", "completion": "0.5"}}
        ]
    }
    mock_response.raise_for_status = MagicMock()
    
    with patch.object(client.client, "get", AsyncMock(return_value=mock_response)):
        pricing = await client.fetch_pricing()
        
        assert len(pricing) == 2
        assert pricing["model1"]["input_price"] == 1.0
        assert pricing["model1"]["output_price"] == 2.0
        assert pricing["model1"]["name"] == "Model 1"
        assert pricing["model2"]["name"] == "model2"  # falls back to id when name absent

    await client.close()


@pytest.mark.asyncio
async def test_fetch_pricing_cache_hit():
    client = ModelClient(api_key="test_key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": []}
    mock_response.raise_for_status = MagicMock()
    
    mock_get = AsyncMock(return_value=mock_response)
    with patch.object(client.client, "get", mock_get):
        await client.fetch_pricing()
        await client.fetch_pricing()
        # Second call should use cache, not make another request
        assert mock_get.call_count == 1

    await client.close()


@pytest.mark.asyncio
async def test_fetch_pricing_cache_expired():
    import backend.orchestrator.model_client as m
    client = ModelClient(api_key="test_key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": []}
    mock_response.raise_for_status = MagicMock()
    
    mock_get = AsyncMock(return_value=mock_response)
    with patch.object(client.client, "get", mock_get):
        # Seed with expired cache
        m._PRICING_CACHE = {"old": {}}
        m._PRICING_CACHE_TIMESTAMP = time.time() - m._PRICING_CACHE_TTL_SECONDS - 1
        
        await client.fetch_pricing()
        # Should have fetched fresh data
        assert mock_get.call_count == 1

    await client.close()


@pytest.mark.asyncio
async def test_fetch_pricing_force_refresh():
    client = ModelClient(api_key="test_key")
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": []}
    mock_response.raise_for_status = MagicMock()
    
    mock_get = AsyncMock(return_value=mock_response)
    with patch.object(client.client, "get", mock_get):
        await client.fetch_pricing()
        await client.fetch_pricing(force_refresh=True)
        assert mock_get.call_count == 2

    await client.close()


@pytest.mark.asyncio
async def test_fetch_pricing_http_error_no_cache_raises():
    client = ModelClient(api_key="test_key")
    
    mock_resp, _ = _make_http_error(500, "Internal Server Error")
    
    with patch.object(client.client, "get", AsyncMock(return_value=mock_resp)):
        with pytest.raises(ModelClientError, match="Failed to fetch pricing"):
            await client.fetch_pricing()

    await client.close()


@pytest.mark.asyncio
async def test_fetch_pricing_http_error_returns_stale_cache():
    import backend.orchestrator.model_client as m
    client = ModelClient(api_key="test_key")
    
    mock_resp, _ = _make_http_error(500)
    
    m._PRICING_CACHE = {"stale_model": {"input_price": 1.0}}
    m._PRICING_CACHE_TIMESTAMP = 1.0  # very old
    
    with patch.object(client.client, "get", AsyncMock(return_value=mock_resp)), \
         patch("backend.orchestrator.model_client.logger") as mock_logger:
        pricing = await client.fetch_pricing(force_refresh=True)
        assert pricing == {"stale_model": {"input_price": 1.0}}
        mock_logger.warning.assert_called_with("Using expired cached pricing due to fetch error")

    await client.close()


@pytest.mark.asyncio
async def test_fetch_pricing_generic_exception_no_cache_raises():
    client = ModelClient(api_key="test_key")
    
    with patch.object(client.client, "get", AsyncMock(side_effect=Exception("Generic error"))):
        with pytest.raises(ModelClientError, match="Failed to fetch pricing"):
            await client.fetch_pricing()

    await client.close()


@pytest.mark.asyncio
async def test_fetch_pricing_generic_exception_returns_stale_cache():
    import backend.orchestrator.model_client as m
    client = ModelClient(api_key="test_key")
    
    m._PRICING_CACHE = {"stale_model": {}}
    m._PRICING_CACHE_TIMESTAMP = 1.0
    
    with patch.object(client.client, "get", AsyncMock(side_effect=Exception("boom"))):
        pricing = await client.fetch_pricing()
        assert pricing == {"stale_model": {}}

    await client.close()


# ---------------------------------------------------------------------------
# _calculate_cost
# ---------------------------------------------------------------------------

def test_calculate_cost_basic():
    client = ModelClient(api_key="test_key")
    pricing = {"model1": {"input_price": 10.0, "output_price": 20.0}}
    
    # 100k input @ $10/1M = $1.00, 200k output @ $20/1M = $4.00
    cost = client._calculate_cost("model1", 100_000, 200_000, pricing)
    assert cost == 5.0


def test_calculate_cost_unknown_model():
    client = ModelClient(api_key="test_key")
    pricing = {"model1": {"input_price": 10.0, "output_price": 20.0}}
    
    cost = client._calculate_cost("unknown_model", 100, 100, pricing)
    assert cost == 0.0


def test_calculate_cost_zero_tokens():
    client = ModelClient(api_key="test_key")
    pricing = {"model1": {"input_price": 10.0, "output_price": 20.0}}
    
    cost = client._calculate_cost("model1", 0, 0, pricing)
    assert cost == 0.0


def test_calculate_cost_empty_pricing():
    client = ModelClient(api_key="test_key")
    
    cost = client._calculate_cost("model1", 1000, 1000, {})
    assert cost == 0.0


# ---------------------------------------------------------------------------
# call_model — success paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_model_success():
    client = ModelClient(api_key="test_key")
    
    mock_pricing = MagicMock()
    mock_pricing.status_code = 200
    mock_pricing.json.return_value = {"data": []}
    mock_pricing.raise_for_status = MagicMock()
    
    mock_chat = MagicMock()
    mock_chat.status_code = 200
    mock_chat.json.return_value = {
        "choices": [{
            "message": {
                "content": "Hello world",
                "thinking": {"content": "Thinking...", "tokens": 10}
            }
        }],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30}
    }
    
    with patch.object(client.client, "get", AsyncMock(return_value=mock_pricing)), \
         patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):
        
        response = await client.call_model(
            model_slug="test-model",
            system_prompt="sys",
            user_prompt="user",
            thinking_budget=100
        )
        
        assert response.response_text == "Hello world"
        assert response.input_tokens == 20
        assert response.output_tokens == 30
        assert response.thinking_tokens == 10
        assert response.thinking_text == "Thinking..."
        assert response.latency_ms >= 0

    await client.close()


@pytest.mark.asyncio
async def test_call_model_sends_thinking_budget_in_payload():
    client = ModelClient(api_key="test_key")
    
    mock_post = AsyncMock(return_value=_make_chat_response())
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post):
        
        await client.call_model("model", "sys", "user", thinking_budget=512)
        
        payload = mock_post.call_args.kwargs.get("json", {})
        assert "thinking" in payload
        assert payload["thinking"]["budget_tokens"] == 512
        assert payload["thinking"]["type"] == "enabled"

    await client.close()


@pytest.mark.asyncio
async def test_call_model_no_thinking_budget_omits_field():
    client = ModelClient(api_key="test_key")
    
    mock_post = AsyncMock(return_value=_make_chat_response())
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post):
        
        await client.call_model("model", "sys", "user")
        
        payload = mock_post.call_args.kwargs.get("json", {})
        assert "thinking" not in payload

    await client.close()


@pytest.mark.asyncio
async def test_call_model_usage_total_tokens_fallback():
    """When completion_tokens is absent, derive it from total_tokens - prompt_tokens."""
    client = ModelClient(api_key="test_key")
    
    mock_chat = MagicMock()
    mock_chat.status_code = 200
    mock_chat.json.return_value = {
        "choices": [{"message": {"content": "Ok"}}],
        "usage": {"prompt_tokens": 10, "total_tokens": 25}
    }
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):
        
        response = await client.call_model("model", "sys", "user")
        assert response.output_tokens == 15

    await client.close()


@pytest.mark.asyncio
async def test_call_model_thinking_string_format():
    """thinking field as plain string (not dict) should set thinking_text, tokens=0."""
    client = ModelClient(api_key="test_key")
    
    mock_chat = MagicMock()
    mock_chat.status_code = 200
    mock_chat.json.return_value = {
        "choices": [{"message": {"content": "Ok", "thinking": "Thinking text"}}],
        "usage": {}
    }
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):
        
        response = await client.call_model("model", "sys", "user")
        assert response.thinking_text == "Thinking text"
        assert response.thinking_tokens == 0

    await client.close()


@pytest.mark.asyncio
async def test_call_model_no_choices_raises():
    client = ModelClient(api_key="test_key")
    
    mock_chat = MagicMock()
    mock_chat.status_code = 200
    mock_chat.json.return_value = {"choices": []}
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):
        
        with pytest.raises(ModelClientError, match="No choices in API response"):
            await client.call_model("model", "sys", "user")

    await client.close()


# ---------------------------------------------------------------------------
# call_model — rate limiting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_model_rate_limit_via_status_code_retries_and_succeeds():
    client = ModelClient(api_key="test_key")
    
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.headers = {"Retry-After": "0.1"}
    
    mock_post = AsyncMock(side_effect=[mock_429, _make_chat_response("Success")])
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post), \
         patch("asyncio.sleep", AsyncMock()) as mock_sleep:
        
        response = await client.call_model("model", "sys", "user")
        assert response.response_text == "Success"
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(0.1)

    await client.close()


@pytest.mark.asyncio
async def test_call_model_rate_limit_via_http_status_error_retries():
    """HTTPStatusError(429) raised by raise_for_status should also retry."""
    client = ModelClient(api_key="test_key")
    
    mock_resp, error = _make_http_error(429, "Rate Limited")
    mock_post = AsyncMock(side_effect=[error, _make_chat_response("Success")])
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post), \
         patch("asyncio.sleep", AsyncMock()):
        
        response = await client.call_model("model", "sys", "user")
        assert response.response_text == "Success"
        assert mock_post.call_count == 2

    await client.close()


@pytest.mark.asyncio
async def test_call_model_rate_limit_all_retries_exhausted_raises_rate_limit_error():
    client = ModelClient(api_key="test_key")
    
    mock_429 = MagicMock()
    mock_429.status_code = 429
    mock_429.headers = {}
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", AsyncMock(return_value=mock_429)), \
         patch("asyncio.sleep", AsyncMock()):
        
        with pytest.raises(RateLimitError):
            await client.call_model("model", "sys", "user", max_retries=3)

    await client.close()


# ---------------------------------------------------------------------------
# call_model — HTTP errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_model_http_400_does_not_retry():
    client = ModelClient(api_key="test_key")
    
    mock_resp, _ = _make_http_error(400, "Bad Request")
    mock_post = AsyncMock(return_value=mock_resp)
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post):
        
        with pytest.raises(ModelClientError, match="HTTP error 400"):
            await client.call_model("model", "sys", "user", max_retries=3)
        
        # Should not retry on 4xx
        assert mock_post.call_count == 1

    await client.close()


@pytest.mark.asyncio
async def test_call_model_http_500_retries_then_succeeds():
    client = ModelClient(api_key="test_key")
    
    mock_resp, _ = _make_http_error(500, "Server Error")
    mock_post = AsyncMock(side_effect=[mock_resp, _make_chat_response("Success")])
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post), \
         patch("asyncio.sleep", AsyncMock()):
        
        response = await client.call_model("model", "sys", "user")
        assert response.response_text == "Success"
        assert mock_post.call_count == 2

    await client.close()


@pytest.mark.asyncio
async def test_call_model_http_500_all_retries_exhausted():
    client = ModelClient(api_key="test_key")
    
    mock_resp, _ = _make_http_error(500, "Server Error")
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", AsyncMock(return_value=mock_resp)), \
         patch("asyncio.sleep", AsyncMock()):
        
        with pytest.raises(ModelClientError, match="HTTP error 500"):
            await client.call_model("model", "sys", "user", max_retries=3)

    await client.close()


# ---------------------------------------------------------------------------
# call_model — timeout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_model_timeout_retries():
    client = ModelClient(api_key="test_key")
    
    mock_post = AsyncMock(
        side_effect=[httpx.TimeoutException("Timeout"), _make_chat_response("Success")]
    )
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post), \
         patch("asyncio.sleep", AsyncMock()):
        
        response = await client.call_model("model", "sys", "user")
        assert response.response_text == "Success"
        assert mock_post.call_count == 2

    await client.close()


@pytest.mark.asyncio
async def test_call_model_timeout_all_retries_exhausted():
    client = ModelClient(api_key="test_key")
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", AsyncMock(side_effect=httpx.TimeoutException("Timeout"))), \
         patch("asyncio.sleep", AsyncMock()):
        
        with pytest.raises(ModelClientError, match="Request timeout"):
            await client.call_model("model", "sys", "user", max_retries=3)

    await client.close()


# ---------------------------------------------------------------------------
# call_model — generic exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_model_generic_exception_retries_and_succeeds():
    client = ModelClient(api_key="test_key")
    
    mock_post = AsyncMock(
        side_effect=[Exception("Boom"), _make_chat_response("Ok")]
    )
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post), \
         patch("asyncio.sleep", AsyncMock()):
        
        response = await client.call_model("model", "sys", "user")
        assert response.response_text == "Ok"
        assert mock_post.call_count == 2

    await client.close()


@pytest.mark.asyncio
async def test_call_model_model_client_error_does_not_retry():
    """ModelClientError (e.g. from no choices) should propagate immediately without retry."""
    client = ModelClient(api_key="test_key")
    
    mock_chat = MagicMock()
    mock_chat.status_code = 200
    mock_chat.json.return_value = {"choices": []}
    mock_post = AsyncMock(return_value=mock_chat)
    
    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})), \
         patch.object(client.client, "post", mock_post):
        
        with pytest.raises(ModelClientError, match="No choices"):
            await client.call_model("model", "sys", "user", max_retries=3)
        
        assert mock_post.call_count == 1

    await client.close()


# ---------------------------------------------------------------------------
# call_model_with_retry (JSON parsing)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_call_model_with_retry_json_success_first_try():
    client = ModelClient(api_key="test_key")
    
    mock_response = ModelResponse(
        response_text='Here is the data: {"key": "value"} and some text.',
        input_tokens=10, output_tokens=10, thinking_tokens=0, cost_usd=0.1, latency_ms=100
    )
    
    mock_call = AsyncMock(return_value=mock_response)
    with patch.object(client, "call_model", mock_call):
        resp, parsed = await client.call_model_with_retry("model", "sys", "user")
        assert parsed == {"key": "value"}
        assert resp == mock_response
        assert mock_call.call_count == 1

    await client.close()


@pytest.mark.asyncio
async def test_call_model_with_retry_json_fail_then_success():
    client = ModelClient(api_key="test_key")
    
    r_fail = ModelResponse(response_text="No JSON here",
                           input_tokens=10, output_tokens=10, thinking_tokens=0, cost_usd=0.1, latency_ms=100)
    r_ok = ModelResponse(response_text='{"key": "value"}',
                         input_tokens=10, output_tokens=10, thinking_tokens=0, cost_usd=0.1, latency_ms=100)
    
    mock_call = AsyncMock(side_effect=[r_fail, r_ok])
    with patch.object(client, "call_model", mock_call):
        resp, parsed = await client.call_model_with_retry("model", "sys", "user", json_parse_retries=1)
        assert parsed == {"key": "value"}
        assert resp == r_ok
        assert mock_call.call_count == 2

    await client.close()


@pytest.mark.asyncio
async def test_call_model_with_retry_json_permanent_fail():
    client = ModelClient(api_key="test_key")
    
    r_fail = ModelResponse(response_text="No JSON here",
                           input_tokens=10, output_tokens=10, thinking_tokens=0, cost_usd=0.1, latency_ms=100)
    
    mock_call = AsyncMock(return_value=r_fail)
    with patch.object(client, "call_model", mock_call):
        resp, parsed = await client.call_model_with_retry("model", "sys", "user", json_parse_retries=2)
        assert parsed is None
        assert resp == r_fail
        # 1 initial + 2 retries
        assert mock_call.call_count == 3

    await client.close()


@pytest.mark.asyncio
async def test_call_model_with_retry_zero_retries():
    """json_parse_retries=0 means only one attempt, no retries."""
    client = ModelClient(api_key="test_key")
    
    r_fail = ModelResponse(response_text="No JSON here",
                           input_tokens=10, output_tokens=10, thinking_tokens=0, cost_usd=0.1, latency_ms=100)
    
    mock_call = AsyncMock(return_value=r_fail)
    with patch.object(client, "call_model", mock_call):
        resp, parsed = await client.call_model_with_retry("model", "sys", "user", json_parse_retries=0)
        assert parsed is None
        assert mock_call.call_count == 1

    await client.close()


# ---------------------------------------------------------------------------
# Cache token parsing and cost calculation
# ---------------------------------------------------------------------------

def test_calculate_cost_with_cache_read():
    client = ModelClient(api_key="test_key")
    pricing = {"model1": {"input_price": 10.0, "output_price": 20.0,
                           "cache_read_price": 1.0, "cache_write_price": 12.5}}

    # 100k input + 50k cache_read + 0 cache_write + 200k output
    cost = client._calculate_cost("model1", 100_000, 200_000, pricing,
                                  cache_read_tokens=50_000)
    expected = (100_000 / 1e6 * 10.0) + (200_000 / 1e6 * 20.0) + (50_000 / 1e6 * 1.0)
    assert abs(cost - expected) < 1e-9


def test_calculate_cost_with_cache_write():
    client = ModelClient(api_key="test_key")
    pricing = {"model1": {"input_price": 10.0, "output_price": 20.0,
                           "cache_read_price": 1.0, "cache_write_price": 12.5}}

    cost = client._calculate_cost("model1", 0, 0, pricing, cache_write_tokens=80_000)
    expected = 80_000 / 1e6 * 12.5
    assert abs(cost - expected) < 1e-9


def test_calculate_cost_cache_fallback_prices():
    """When cache prices are absent from pricing, defaults of 0.1x read / 1.25x write are used."""
    client = ModelClient(api_key="test_key")
    pricing = {"model1": {"input_price": 10.0, "output_price": 20.0}}

    cost = client._calculate_cost("model1", 0, 0, pricing,
                                  cache_read_tokens=100_000,
                                  cache_write_tokens=100_000)
    expected_read = 100_000 / 1e6 * (10.0 * 0.1)
    expected_write = 100_000 / 1e6 * (10.0 * 1.25)
    assert abs(cost - (expected_read + expected_write)) < 1e-9


@pytest.mark.asyncio
async def test_call_model_parses_cache_read_tokens():
    client = ModelClient(api_key="test_key")

    mock_chat = _make_chat_response(
        prompt_tokens=100, completion_tokens=50,
        cache_read_tokens=80, cache_write_tokens=0,
    )

    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})),          patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):

        response = await client.call_model("model", "sys", "user")
        assert response.cache_read_tokens == 80
        assert response.cache_write_tokens == 0

    await client.close()


@pytest.mark.asyncio
async def test_call_model_parses_cache_write_tokens():
    client = ModelClient(api_key="test_key")

    mock_chat = _make_chat_response(
        prompt_tokens=200, completion_tokens=30,
        cache_read_tokens=0, cache_write_tokens=150,
    )

    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})),          patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):

        response = await client.call_model("model", "sys", "user")
        assert response.cache_write_tokens == 150

    await client.close()


@pytest.mark.asyncio
async def test_call_model_parses_reported_cost():
    client = ModelClient(api_key="test_key")

    mock_chat = _make_chat_response(reported_cost=0.00042)

    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})),          patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):

        response = await client.call_model("model", "sys", "user")
        assert response.reported_cost_usd == pytest.approx(0.00042)

    await client.close()


@pytest.mark.asyncio
async def test_call_model_reported_cost_none_when_absent():
    client = ModelClient(api_key="test_key")

    mock_chat = _make_chat_response()  # no reported_cost

    with patch.object(client, "fetch_pricing", AsyncMock(return_value={})),          patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):

        response = await client.call_model("model", "sys", "user")
        assert response.reported_cost_usd is None

    await client.close()


@pytest.mark.asyncio
async def test_call_model_cache_cost_included_in_cost_usd():
    """cost_usd should include cache read/write costs when pricing data is present."""
    client = ModelClient(api_key="test_key")

    pricing = {
        "model1": {
            "input_price": 10.0,
            "output_price": 20.0,
            "cache_read_price": 1.0,
            "cache_write_price": 12.5,
        }
    }
    mock_chat = _make_chat_response(
        prompt_tokens=0, completion_tokens=0,
        cache_read_tokens=1_000_000, cache_write_tokens=0,
    )

    with patch.object(client, "fetch_pricing", AsyncMock(return_value=pricing)),          patch.object(client.client, "post", AsyncMock(return_value=mock_chat)):

        response = await client.call_model("model1", "sys", "user")
        assert response.cost_usd == pytest.approx(1.0)  # 1M tokens @ /1M

    await client.close()


def test_fetch_pricing_stores_cache_prices():
    """Verify cache_read_price and cache_write_price are stored when the endpoint returns them."""
    import asyncio
    import backend.orchestrator.model_client as m

    client = ModelClient(api_key="test_key")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": [{
            "id": "model1",
            "name": "Model 1",
            "pricing": {
                "prompt": "1.0",
                "completion": "2.0",
                "cache_read": "0.1",
                "cache_write": "1.25",
            }
        }]
    }
    mock_response.raise_for_status = MagicMock()

    async def run():
        with patch.object(client.client, "get", AsyncMock(return_value=mock_response)):
            pricing = await client.fetch_pricing()
            assert pricing["model1"]["cache_read_price"] == 0.1
            assert pricing["model1"]["cache_write_price"] == 1.25
        await client.close()

    asyncio.get_event_loop().run_until_complete(run())


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_context_manager_closes_client():
    async with ModelClient(api_key="test_key") as client:
        assert not client.client.is_closed
    
    assert client.client.is_closed


# ---------------------------------------------------------------------------
# Integration (skipped without real API key)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_openrouter_free():
    """
    Integration test using OpenRouter free model router.
    Only runs if OPENROUTER_API_KEY is set.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not set")
        
    async with ModelClient(api_key=api_key) as client:
        model = "openrouter/free"
        
        try:
            response = await client.call_model(
                model_slug=model,
                system_prompt="You are a helpful assistant.",
                user_prompt="Say 'Hello'",
                max_tokens=10
            )
            
            # openrouter/free routes to whichever free model is available;
            # content may be empty string on some providers, so only check
            # that the response object is well-formed and tokens were counted.
            assert isinstance(response.response_text, str)
            assert response.input_tokens > 0
            assert response.input_tokens + response.output_tokens > 0
        except (ModelClientError, RateLimitError) as e:
            err = str(e).lower()
            if any(code in err for code in ["400", "404", "429", "500", "503", "timeout"]):
                pytest.skip(f"Integration test skipped due to external API issue: {e}")
            raise