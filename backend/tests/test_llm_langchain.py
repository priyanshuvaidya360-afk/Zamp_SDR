"""llm.structured through the real LangChain ChatOpenAI client, against a fake HTTP server.

tests/test_llm.py fakes llm._call to test the retry and fallback logic. These tests go one
layer deeper: they let LangChain build and send the request, and check what goes over the wire
and how replies and API errors come back.
"""

import json

import httpx
import pytest
from langchain_openai import ChatOpenAI

from app import llm
from app.config import settings
from app.schemas import HookChoice

HOOK = {"signal_id": 2, "angle": "AP team is scaling", "runners_up": [1], "why_not_others": "older"}


def completion(content: str, prompt=11, completion_tokens=4) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "1", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens, "total_tokens": prompt + completion_tokens}})


def error(status: int, body: dict, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, json={"error": body}, headers=headers or {})


REAL_CALL = llm._call  # conftest replaces llm._call with a network blocker for every test


@pytest.fixture
def server(monkeypatch):
    """Route every provider's ChatOpenAI to `handler(request)`; keep the requests it saw."""
    monkeypatch.setattr(llm, "_call", REAL_CALL)  # the real one, so LangChain builds the request
    seen: list[tuple[str, dict]] = []
    script: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.host, json.loads(request.content)))
        return script.pop(0)

    def chat_model(api_key, base_url, model, timeout):
        return ChatOpenAI(model=model, api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0,
                          use_responses_api=False,
                          http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(llm, "_chat_model", chat_model)
    return seen, script


async def test_request_and_reply_go_through_langchain(server):
    seen, script = server
    script.append(completion(json.dumps(HOOK)))
    out, usage = await llm.structured("smart", "SYS", "USER", HookChoice)
    assert out == HookChoice(**HOOK)
    assert (usage.provider, usage.prompt_tokens, usage.completion_tokens) == ("groq", 11, 4)
    host, body = seen[0]
    assert host == "api.groq.com" and body["model"] == settings.groq_smart_model
    assert body["messages"] == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]
    rf = body["response_format"]["json_schema"]
    assert body["response_format"]["type"] == "json_schema" and rf["strict"] is True and rf["name"] == "HookChoice"
    assert rf["schema"] == llm.strict_schema(HookChoice)  # our strict schema reaches the provider untouched


async def test_groqs_schema_rejection_gets_one_corrective_retry(server):
    seen, script = server
    script += [error(400, {"message": "bad", "type": "invalid_request_error", "code": "json_validate_failed",
                           "failed_generation": '{"signal_id": "two"}'}),
               completion(json.dumps(HOOK))]
    out, _ = await llm.structured("smart", "SYS", "USER", HookChoice)
    assert out.signal_id == 2 and len(seen) == 2
    retry = seen[1][1]["messages"]
    assert retry[2] == {"role": "assistant", "content": '{"signal_id": "two"}'}  # the rejected reply is shown back
    assert retry[3]["content"].startswith("Your reply did not match the required schema:")


async def test_rate_limit_falls_back_to_gemini(server):
    seen, script = server
    script += [error(429, {"message": "slow", "type": "x", "code": "rate_limit_exceeded"}, {"retry-after": "900"}),
               completion(json.dumps(HOOK))]
    _, usage = await llm.structured("smart", "SYS", "USER", HookChoice)
    assert [host for host, _ in seen] == ["api.groq.com", "generativelanguage.googleapis.com"]
    assert usage.provider == "gemini" and usage.fallbacks == ["groq: rate limited"]


async def test_every_provider_failing_is_one_readable_error(server, monkeypatch):
    seen, script = server
    script += [error(401, {"message": "no", "type": "x", "code": "invalid_api_key"}),
               error(429, {"message": "slow", "type": "x", "code": "rate_limit_exceeded"}, {"retry-after": "900"})]
    with pytest.raises(llm.LLMUnavailable) as failed:
        await llm.structured("smart", "SYS", "USER", HookChoice)
    assert str(failed.value) == "No LLM provider could answer. groq: OpenAIAuthenticationError; gemini: rate limited"


async def test_no_keys_means_no_calls(server, monkeypatch):
    seen, _ = server
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    with pytest.raises(llm.LLMUnavailable, match="groq: no API key; gemini: no API key"):
        await llm.structured("fast", "SYS", "USER", HookChoice)
    assert seen == []
