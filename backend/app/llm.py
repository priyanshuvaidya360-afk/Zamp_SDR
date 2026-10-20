"""The only way the app calls an LLM.

Spec: docs/GUIDE.md T03. Every provider is reached through the OpenAI-compatible API, so
switching provider is a settings change. Callers ask for a role ("smart" or "fast"), never a
model ID, and always get back a validated Pydantic object.

Flow per call: fixtures (if DEMO_MODE=fixtures) -> for each provider in settings.llm_providers
that has a key: up to 3 tries on transient errors -> on 429 / exhausted retries / bad output,
fall through to the next provider.
"""

import asyncio
import copy
import logging
import time
from typing import Any, Literal, TypeVar

import openai
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.fixtures import load_fixture, make_slug, replay_pause, reusable, save_fixture

log = logging.getLogger(__name__)

Role = Literal["smart", "fast"]
T = TypeVar("T", bound=BaseModel)

TRANSIENT_ERRORS = (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError)
RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=8)  # tests swap this for wait_none()


class LLMUnavailable(Exception):
    """Every configured provider failed. The message lists why, in plain words."""


class Usage(BaseModel):
    provider: str
    model: str
    role: Role
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0  # free tiers only
    fallbacks: list[str] = []  # providers tried first, with the reason they were skipped
    from_fixture: bool = False


# --- Schema ------------------------------------------------------------------

# Keywords strict mode rejects or ignores; Pydantic re-checks them after the reply anyway.
_DROP_KEYS = {
    "title", "default", "format", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "minLength", "maxLength", "pattern", "minItems", "maxItems",
}


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema rewritten for strict structured output.

    Inlines $refs, marks every property required, forbids extra properties and drops
    validation keywords that providers reject. Optional fields keep their null branch.
    """
    root = model.model_json_schema()
    defs = root.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(copy.deepcopy(defs[node["$ref"].rsplit("/", 1)[-1]]))
        out = {k: walk(v) for k, v in node.items() if k not in _DROP_KEYS}
        if out.get("type") == "object" and "properties" in out:
            out["required"] = list(out["properties"])
            out["additionalProperties"] = False
        return out

    return walk(root)


# --- Providers ---------------------------------------------------------------


def _provider_config(name: str) -> tuple[str, str, dict[str, str]]:
    """(api_key, base_url, {role: model}) for a provider name."""
    if name == "groq":
        return settings.groq_api_key, settings.groq_base_url, {
            "smart": settings.groq_smart_model, "fast": settings.groq_fast_model}
    if name == "gemini":
        return settings.gemini_api_key, settings.gemini_base_url, {
            "smart": settings.gemini_smart_model, "fast": settings.gemini_fast_model}
    raise ValueError(f"Unknown LLM provider '{name}'")


_clients: dict[tuple[str, str], AsyncOpenAI] = {}


def _client(api_key: str, base_url: str) -> AsyncOpenAI:
    key = (api_key, base_url)
    if key not in _clients:
        # Retries are ours (tenacity), so the SDK's own retries are off.
        _clients[key] = AsyncOpenAI(
            api_key=api_key, base_url=base_url, timeout=settings.llm_timeout_s, max_retries=0
        )
    return _clients[key]


async def _call(
    provider: str, model: str, messages: list[dict[str, str]], schema_name: str, schema: dict
) -> tuple[str, int, int]:
    """One raw completion. Returns (content, prompt_tokens, completion_tokens)."""
    api_key, base_url, _ = _provider_config(provider)
    response = await _client(api_key, base_url).chat.completions.create(
        model=model,
        messages=messages,
        response_format={
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": schema},
        },
    )
    usage = response.usage
    return (
        response.choices[0].message.content or "",
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
    )


def _schema_rejection(error: openai.BadRequestError) -> tuple[str, str] | None:
    """(rejected reply, what was wrong) when a provider refused output that broke the schema.

    Groq checks strict schemas server-side and answers 400 json_validate_failed with the
    rejected text in `failed_generation`. That is a fixable model mistake, not an outage.
    """
    body = error.body if isinstance(error.body, dict) else {}
    body = body.get("error", body)
    # json_validate_failed: valid JSON that broke the schema. output_parse_failed: the model
    # wrote prose (gpt-oss sometimes narrates its reasoning) instead of JSON.
    if body.get("code") not in ("json_validate_failed", "output_parse_failed"):
        return None
    return body.get("failed_generation", ""), body.get("message", str(error))


def _retry_after_s(error: openai.RateLimitError) -> float | None:
    """Seconds the provider asked us to wait, if it said."""
    headers = getattr(error.response, "headers", {}) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


async def _call_with_retry(provider: str, model: str, messages, schema_name, schema):
    """Transient errors: 3 tries with backoff. 429 with a short retry-after: wait and try
    again (free-tier per-minute limits clear quickly), at most rate_limit_waits times."""
    waits = 0
    while True:
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type(TRANSIENT_ERRORS),
                stop=stop_after_attempt(3),
                wait=RETRY_WAIT,
                reraise=True,
            ):
                with attempt:
                    return await _call(provider, model, messages, schema_name, schema)
        except openai.RateLimitError as error:
            delay = _retry_after_s(error)
            if delay is None or delay > settings.llm_max_rate_limit_wait_s or waits >= settings.llm_rate_limit_waits:
                raise
            waits += 1
            log.info("LLM %s rate limited; waiting %.1fs as asked", provider, delay)
            await asyncio.sleep(delay)


# --- Public API --------------------------------------------------------------


async def structured(
    role: Role,
    system: str,
    user: str,
    schema: type[T],
    providers: list[str] | None = None,
) -> tuple[T, Usage]:
    """Ask the model for a reply that must validate as `schema`."""
    providers = providers or settings.llm_providers
    slug = make_slug(
        "llm", f"{role} {schema.__name__}",
        {"role": role, "system": system, "user": user, "schema": schema.__name__},
    )
    if settings.demo_mode == "fixtures":
        await replay_pause()
        saved = load_fixture(slug)
        if "error" in saved:  # the recorded call failed: replay the failure faithfully
            raise LLMUnavailable(saved["error"])
        usage = Usage(**{**saved["usage"], "from_fixture": True})
        return schema.model_validate(saved["output"]), usage
    if (saved := reusable(slug)) is not None:
        return schema.model_validate(saved["output"]), Usage(**{**saved["usage"], "from_fixture": True})

    json_schema = strict_schema(schema)
    skipped: list[str] = []

    for provider in providers:
        api_key, _, models = _provider_config(provider)
        if not api_key:
            skipped.append(f"{provider}: no API key")
            continue
        model = models[role]
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        started = time.perf_counter()
        prompt_tokens = completion_tokens = 0
        try:
            for validation_try in range(2):
                try:
                    content, p_tok, c_tok = await _call_with_retry(
                        provider, model, messages, schema.__name__, json_schema
                    )
                    prompt_tokens += p_tok
                    completion_tokens += c_tok
                    output = schema.model_validate_json(content)
                    break
                except ValidationError as error:
                    if validation_try == 1:
                        raise
                    problem = str(error)
                except openai.BadRequestError as error:
                    rejected = _schema_rejection(error)
                    if rejected is None or validation_try == 1:
                        raise
                    content, problem = rejected
                # One retry, telling the model exactly what was wrong.
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": (
                        "Your reply did not match the required schema:\n"
                        f"{problem}\nReturn corrected JSON only, using only allowed values."
                    )},
                ]
        except openai.RateLimitError as error:
            skipped.append(f"{provider}: rate limited")
            log.warning("LLM %s rate limited (retry-after %s), falling back", provider, _retry_after_s(error))
            continue
        except (openai.APIError, ValidationError) as error:
            skipped.append(f"{provider}: {type(error).__name__}")
            log.warning("LLM %s failed (%s), falling back", provider, error)
            continue

        usage = Usage(
            provider=provider,
            model=model,
            role=role,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            fallbacks=skipped,
        )
        if settings.demo_mode == "record":
            save_fixture(slug, {"output": output.model_dump(mode="json"), "usage": usage.model_dump()})
        return output, usage

    message = "No LLM provider could answer. " + "; ".join(skipped)
    if settings.demo_mode == "record":
        save_fixture(slug, {"error": message})
    raise LLMUnavailable(message)
