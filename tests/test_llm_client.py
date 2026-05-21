def test_openai_compatible_request():
    """Test that OpenAI-compatible request builds correct payload."""
    from aery_plugin.llm_client import OpenAIClient
    client = OpenAIClient(base_url="https://api.openai.com/v1", api_key="test-key")
    messages = [{"role": "user", "content": "hello"}]
    payload = client._build_payload(messages, model="gpt-4o-mini", max_tokens=1000)
    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"] == messages
    assert payload["max_tokens"] == 1000


def test_is_retryable_text():
    from aery_plugin.llm_client import _is_retryable_text
    assert _is_retryable_text("resource has been exhausted")
    assert _is_retryable_text("RESOURCE_EXHAUSTED")
    assert _is_retryable_text("rate limit exceeded")
    assert _is_retryable_text("service unavailable")
    assert _is_retryable_text("quota exceeded")
    assert _is_retryable_text("overloaded")
    assert not _is_retryable_text("invalid request")
    assert not _is_retryable_text("not found")


def test_extract_retry_delay_from_headers():
    from aery_plugin.llm_client import _extract_retry_delay
    assert _extract_retry_delay("", {"Retry-After": "5"}) >= 5
    assert _extract_retry_delay("", {"x-ratelimit-reset": "30"}) >= 30
    assert _extract_retry_delay("", {}) == 0


def test_extract_retry_delay_from_body():
    from aery_plugin.llm_client import _extract_retry_delay
    assert _extract_retry_delay("Your quota will reset after 39s") >= 39
    assert _extract_retry_delay("Please retry in 10s") >= 10
    assert _extract_retry_delay('"retryDelay": "34.074824224s"') >= 34
    assert _extract_retry_delay("Your quota will reset after 1h30m10s") >= 5410
    assert _extract_retry_delay("some random error") == 0


def test_extract_error_message():
    from aery_plugin.llm_client import _extract_error_message
    # JSON error
    assert _extract_error_message('{"error":{"message":"Rate limit exceeded"}}') == "Rate limit exceeded"
    # Non-JSON
    assert _extract_error_message("some error text") == "some error text"
    # Long text truncated
    long = "x" * 300
    assert len(_extract_error_message(long)) == 200


def test_is_retryable_status():
    from aery_plugin.llm_client import _is_retryable
    assert _is_retryable(429)
    assert _is_retryable(500)
    assert _is_retryable(502)
    assert _is_retryable(503)
    assert _is_retryable(504)
    assert not _is_retryable(400)
    assert not _is_retryable(401)
    assert not _is_retryable(404)


def test_api_error():
    from aery_plugin.llm_client import APIError
    e = APIError("test", 429, retryable=True)
    assert e.status_code == 429
    assert e.retryable is True
    assert str(e) == "test"


def test_anthropic_payload():
    from aery_plugin.llm_client import AnthropicClient
    client = AnthropicClient(api_key="test-key")
    msgs = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "hello"},
    ]
    payload = client._build_payload(msgs, model="claude-sonnet-4", max_tokens=4096)
    assert payload["model"] == "claude-sonnet-4"
    assert payload["system"] == "You are helpful"
    assert len(payload["messages"]) == 1
    assert payload["messages"][0]["role"] == "user"


def test_gemini_payload():
    from aery_plugin.llm_client import GeminiClient
    client = GeminiClient(api_key="AIzaSyTest123")
    msgs = [
        {"role": "system", "content": "You are helpful"},
        {"role": "user", "content": "hello"},
    ]
    payload = client._build_payload(msgs, model="gemini-2.0-flash", max_tokens=4096)
    assert "contents" in payload
    assert payload["system_instruction"] == {"parts": [{"text": "You are helpful"}]}
    assert len(payload["contents"]) == 1


def test_gemini_is_api_key():
    from aery_plugin.llm_client import GeminiClient
    assert GeminiClient(api_key="AIzaSyTest123")._is_api_key() is True
    assert GeminiClient(api_key="ya29.longtoken1234567890")._is_api_key() is False


def test_resolve_google_credentials_json():
    from aery_plugin.llm_client import _resolve_google_credentials
    import json
    entry = {"access": json.dumps({"token": "ya29.test", "projectId": "my-proj"})}
    token, pid = _resolve_google_credentials(entry)
    assert token == "ya29.test"
    assert pid == "my-proj"


def test_resolve_google_credentials_raw():
    from aery_plugin.llm_client import _resolve_google_credentials
    entry = {"access": "ya29.raw-token"}
    token, pid = _resolve_google_credentials(entry)
    assert token == "ya29.raw-token"
    assert pid == ""


def test_resolve_api_key_api_key():
    from aery_plugin.llm_client import _resolve_api_key
    assert _resolve_api_key("openai", {"type": "api_key", "key": "sk-test"}) == "sk-test"


def test_resolve_api_key_empty_raises():
    from aery_plugin.llm_client import _resolve_api_key, APIError
    import pytest
    with pytest.raises(APIError):
        _resolve_api_key("openai", {"type": "api_key", "key": ""})


def test_resolve_api_key_expired_raises():
    from aery_plugin.llm_client import _resolve_api_key, APIError
    import time, pytest
    with pytest.raises(APIError):
        _resolve_api_key("anthropic", {"type": "oauth", "access": "expired", "expires": int(time.time() * 1000) - 10000})


def test_create_client_types():
    from aery_plugin.llm_client import create_client, OpenAIClient, AnthropicClient, GeminiClient
    c, m = create_client("openai", {"type": "api_key", "key": "sk-test"}, "gpt-4o")
    assert isinstance(c, OpenAIClient)

    c, m = create_client("anthropic", {"type": "api_key", "key": "sk-ant-test"}, "claude-sonnet-4")
    assert isinstance(c, AnthropicClient)

    c, m = create_client("groq", {"type": "api_key", "key": "gsk-test"}, "llama-3.3-70b")
    assert isinstance(c, OpenAIClient)


def test_cloudflare_url_resolution():
    from aery_plugin.llm_client import create_client, OpenAIClient
    c, m = create_client("cloudflare-workers-ai", {"type": "api_key", "key": "cf-test", "accountId": "my-acct"}, "@cf/moonshotai/kimi-k2.6")
    assert isinstance(c, OpenAIClient)
    assert "my-acct" in c.base_url
