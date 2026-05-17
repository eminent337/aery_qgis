def test_openai_compatible_request():
    """Test that OpenAI-compatible request builds correct payload."""
    from aery_plugin.llm_client import OpenAIClient
    client = OpenAIClient(base_url="https://api.openai.com/v1", api_key="test-key")
    messages = [{"role": "user", "content": "hello"}]
    payload = client._build_payload(messages, model="gpt-4o-mini", max_tokens=1000)
    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"] == messages
    assert payload["max_tokens"] == 1000
