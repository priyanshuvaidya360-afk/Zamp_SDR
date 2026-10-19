import json

import httpx
import openai
import pytest

from app import llm
from app.config import settings
from app.fixtures import FixtureMissing
from app.schemas import HookChoice, Signal

REQUEST = httpx.Request("POST", "https://llm.test/v1/chat/completions")
HOOK = {"signal_id": 2, "angle": "AP team is scaling", "runners_up": [1], "why_not_others": "older"}


def api_error(cls, status):
    return cls("boom", response=httpx.Response(status, request=REQUEST), body=None)


class FakeCall:
    """Stands in for llm._call: replays a script of results or exceptions, records calls."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    async def __call__(self, provider, model, messages, schema_name, schema):
        self.calls.append({"provider": provider, "model": model, "messages": messages})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item, 10, 5


async def test_returns_validated_model_with_usage(monkeypatch):
    fake = FakeCall(json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    out, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert out == HookChoice(**HOOK)
    assert (usage.provider, usage.model, usage.role) == ("groq", settings.groq_smart_model, "smart")
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cost_usd) == (10, 5, 0.0)


async def test_fast_role_uses_fast_model(monkeypatch):
    fake = FakeCall(json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    await llm.structured("fast", "sys", "user", HookChoice)
    assert fake.calls[0]["model"] == settings.groq_fast_model


async def test_retries_transient_errors_then_succeeds(monkeypatch):
    fake = FakeCall(
        api_error(openai.InternalServerError, 500),
        openai.APITimeoutError(request=REQUEST),
        json.dumps(HOOK),
    )
    monkeypatch.setattr(llm, "_call", fake)
    out, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert len(fake.calls) == 3 and usage.provider == "groq" and out.signal_id == 2


async def test_rate_limit_falls_back_to_gemini(monkeypatch):
    fake = FakeCall(api_error(openai.RateLimitError, 429), json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    out, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert [c["provider"] for c in fake.calls] == ["groq", "gemini"]  # 429 is not retried
    assert usage.provider == "gemini" and usage.model == settings.gemini_smart_model
    assert usage.fallbacks == ["groq: rate limited"]


def rate_limited(retry_after: str | None):
    headers = {"retry-after": retry_after} if retry_after else {}
    return openai.RateLimitError("slow down", response=httpx.Response(429, request=REQUEST, headers=headers), body=None)


async def test_short_retry_after_waits_and_stays_on_groq(monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    fake = FakeCall(rate_limited("2"), rate_limited("3"), json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    _, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert usage.provider == "groq" and slept == [2.0, 3.0]


async def test_long_retry_after_or_too_many_waits_fall_back(monkeypatch):
    async def fake_sleep(seconds):
        pass

    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    fake = FakeCall(rate_limited("120"), json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    assert (await llm.structured("smart", "sys", "user", HookChoice))[1].provider == "gemini"

    fake = FakeCall(*[rate_limited("1")] * (settings.llm_rate_limit_waits + 1), json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    assert (await llm.structured("smart", "sys", "user", HookChoice))[1].provider == "gemini"


async def test_groq_prose_instead_of_json_gets_the_corrective_retry(monkeypatch):
    prose = "We need to output JSON object with signal_id..."
    parse_failed = openai.BadRequestError(
        "output_parse_failed", response=httpx.Response(400, request=REQUEST),
        body={"error": {"code": "output_parse_failed", "failed_generation": prose,
                        "message": "Parsing failed."}},
    )
    fake = FakeCall(parse_failed, json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    _, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert usage.provider == "groq" and fake.calls[1]["messages"][-2]["content"] == prose


async def test_exhausted_retries_fall_back(monkeypatch):
    err = api_error(openai.InternalServerError, 503)
    fake = FakeCall(err, err, err, json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    _, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert usage.provider == "gemini" and len(fake.calls) == 4


async def test_provider_without_key_is_skipped(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    fake = FakeCall(json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    _, usage = await llm.structured("fast", "sys", "user", HookChoice)
    assert usage.provider == "gemini" and usage.fallbacks == ["groq: no API key"]


async def test_validation_error_gets_one_retry_with_the_error(monkeypatch):
    fake = FakeCall('{"signal_id": "not-a-number"}', json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    out, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert out.signal_id == 2 and usage.provider == "groq"
    retry_messages = fake.calls[1]["messages"]
    assert "did not match the required schema" in retry_messages[-1]["content"]
    assert usage.prompt_tokens == 20  # both attempts counted


async def test_groq_server_side_schema_rejection_gets_the_corrective_retry(monkeypatch):
    rejected = '{"signal_id": 2, "angle": "x", "runners_up": [], "why_not_others": "y", "type": "revenue"}'
    groq_400 = openai.BadRequestError(
        "json_validate_failed",
        response=httpx.Response(400, request=REQUEST),
        body={"error": {"code": "json_validate_failed", "failed_generation": rejected,
                        "message": "value must be one of 'funding', ..."}},
    )
    fake = FakeCall(groq_400, json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    out, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert usage.provider == "groq" and out.signal_id == 2  # fixed on Groq, no fallback
    retry = fake.calls[1]["messages"]
    assert retry[-2] == {"role": "assistant", "content": rejected}
    assert "value must be one of" in retry[-1]["content"]


async def test_other_bad_requests_fall_back_without_retry(monkeypatch):
    other_400 = openai.BadRequestError(
        "bad", response=httpx.Response(400, request=REQUEST),
        body={"error": {"code": "model_decommissioned"}},
    )
    fake = FakeCall(other_400, json.dumps(HOOK))
    monkeypatch.setattr(llm, "_call", fake)
    _, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert usage.provider == "gemini" and usage.fallbacks == ["groq: BadRequestError"]


async def test_all_providers_failing_raises_plain_message(monkeypatch):
    fake = FakeCall(api_error(openai.RateLimitError, 429), api_error(openai.RateLimitError, 429))
    monkeypatch.setattr(llm, "_call", fake)
    with pytest.raises(llm.LLMUnavailable, match="groq: rate limited; gemini: rate limited"):
        await llm.structured("smart", "sys", "user", HookChoice)


async def test_record_then_replay_fixture_without_network(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", "record")
    monkeypatch.setattr(llm, "_call", FakeCall(json.dumps(HOOK)))
    recorded, _ = await llm.structured("smart", "sys", "user", HookChoice)
    assert len(list(settings.fixtures_dir.glob("llm-smart-hookchoice-*.json"))) == 1

    monkeypatch.setattr(settings, "demo_mode", "fixtures")
    monkeypatch.setattr(llm, "_call", FakeCall())  # any live call would fail: empty script
    replayed, usage = await llm.structured("smart", "sys", "user", HookChoice)
    assert replayed == recorded and usage.from_fixture

    with pytest.raises(FixtureMissing, match="llm-smart-hookchoice-"):
        await llm.structured("smart", "sys", "a prompt never recorded", HookChoice)


async def test_a_failed_call_is_recorded_and_replayed_as_a_failure(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", "record")
    monkeypatch.setattr(llm, "_call", FakeCall(api_error(openai.RateLimitError, 429), api_error(openai.RateLimitError, 429)))
    with pytest.raises(llm.LLMUnavailable):
        await llm.structured("fast", "sys", "user", HookChoice)
    monkeypatch.setattr(settings, "demo_mode", "fixtures")
    with pytest.raises(llm.LLMUnavailable, match="groq: rate limited"):
        await llm.structured("fast", "sys", "user", HookChoice)


def test_strict_schema_is_provider_safe():
    schema = llm.strict_schema(Signal)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    text = json.dumps(schema)
    for banned in ('"$ref"', '"format"', '"default"', '"minimum"', '"title"'):
        assert banned not in text
    # Optional fields keep a null branch so the model can say "unknown".
    assert {"type": "null"} in schema["properties"]["published_at"]["anyOf"]
