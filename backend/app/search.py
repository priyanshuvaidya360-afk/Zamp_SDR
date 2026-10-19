"""The only way the app searches the web or extracts pages.

Spec: docs/GUIDE.md T03. Provider order: Tavily (1,000 free credits/month) -> DuckDuckGo
(ddgs, no key, pages fetched by app/fetch.py) -> Exa (only if EXA_API_KEY is set). A provider
that errors, is out of credits, or returns nothing hands over to the next one.

Results are cached for 24 h per provider + query + params. DEMO_MODE=record saves each
response under tests/fixtures/; DEMO_MODE=fixtures replays them with no network at all.
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Literal, Protocol

import httpx
from pydantic import BaseModel
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings
from app.fetch import fetch_many, is_blocked
from app.fixtures import cache_get, cache_put, load_fixture, make_slug, save_fixture

log = logging.getLogger(__name__)

Depth = Literal["basic", "advanced"]
Topic = Literal["general", "news"]
TRANSIENT_ERRORS = (httpx.TransportError, TimeoutError, ConnectionError)
RETRY_WAIT = wait_exponential(multiplier=1, min=1, max=8)  # tests swap this for wait_none()


class SearchResult(BaseModel):
    url: str
    title: str = ""
    content: str = ""  # snippet or page excerpt; untrusted
    published_date: str | None = None
    score: float | None = None


class SearchResponse(BaseModel):
    provider: str
    query: str
    results: list[SearchResult]
    credits: float = 0.0  # Tavily credits; other providers report 0
    fallbacks: list[str] = []
    cached: bool = False


class ExtractResponse(BaseModel):
    provider: str
    pages: dict[str, str]  # url -> main text (untrusted)
    failed: list[str]
    credits: float = 0.0
    fallbacks: list[str] = []


class SearchProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    async def search(self, query: str, depth: Depth, days: int, max_results: int,
                     topic: Topic = "general") -> SearchResponse: ...

    async def extract(self, urls: list[str]) -> ExtractResponse: ...


def _start_date(days: int) -> str:
    return (date.today() - timedelta(days=days)).isoformat()


class TavilyProvider:
    name = "tavily"

    def available(self) -> bool:
        return bool(settings.tavily_api_key)

    def _client(self):
        from tavily import AsyncTavilyClient

        return AsyncTavilyClient(api_key=settings.tavily_api_key)

    async def search(self, query, depth, days, max_results, topic="general"):
        data = await self._client().search(
            query,
            topic=topic,
            search_depth=depth,
            start_date=_start_date(days),
            max_results=max_results,
            include_usage=True,
        )
        results = [
            SearchResult(
                url=r["url"],
                title=r.get("title") or "",
                content=r.get("content") or "",
                published_date=r.get("published_date"),
                score=r.get("score"),
            )
            for r in data.get("results", [])
        ]
        credits = (data.get("usage") or {}).get("credits", 2 if depth == "advanced" else 1)
        return SearchResponse(provider=self.name, query=query, results=results, credits=credits)

    async def extract(self, urls):
        data = await self._client().extract(
            urls, extract_depth="basic", format="text", include_usage=True
        )
        pages = {
            r["url"]: (r.get("raw_content") or "")[: settings.fetch_max_chars]
            for r in data.get("results", [])
            if r.get("raw_content")
        }
        failed = [u for u in urls if u not in pages]
        credits = (data.get("usage") or {}).get("credits", 0)
        return ExtractResponse(provider=self.name, pages=pages, failed=failed, credits=credits)


def _ddgs_timelimit(days: int) -> str:
    if days <= 1:
        return "d"
    if days <= 7:
        return "w"
    if days <= 31:
        return "m"
    return "y"


class DuckDuckGoProvider:
    name = "duckduckgo"

    def available(self) -> bool:
        return True  # no key needed

    def _text(self, query: str, days: int, max_results: int) -> list[dict]:
        """Try each engine on its own, with the date filter first, then without it.

        Measured on ddgs 9.16: combined backends ("duckduckgo,bing") and time filters often
        return nothing for queries that work unfiltered. Unfiltered results are fine as a
        fallback because recency is enforced downstream (stage 4 dates every signal and
        anything older than 180 days is dropped).
        """
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        client = DDGS(timeout=int(settings.fetch_timeout_s))
        last_error: Exception | None = None
        for engine in [e.strip() for e in settings.ddgs_backends.split(",") if e.strip()]:
            for timelimit in (_ddgs_timelimit(days), None):
                try:
                    rows = client.text(
                        query, max_results=max_results, timelimit=timelimit, backend=engine
                    )
                except DDGSException as error:
                    if "no results" not in str(error).lower():
                        last_error = error
                    continue
                if rows:
                    return rows
        if last_error:
            raise last_error
        return []

    async def search(self, query, depth, days, max_results, topic="general"):
        rows = await asyncio.to_thread(self._text, query, days, max_results)
        results = [
            SearchResult(url=r["href"], title=r.get("title") or "", content=r.get("body") or "")
            for r in rows
            if r.get("href")
        ]
        return SearchResponse(provider=self.name, query=query, results=results)

    async def extract(self, urls):
        pages, failed = await fetch_many(urls)
        return ExtractResponse(provider=self.name, pages=pages, failed=failed)


class ExaProvider:
    name = "exa"

    def available(self) -> bool:
        return bool(settings.exa_api_key)

    def _client(self):
        from exa_py import Exa

        return Exa(api_key=settings.exa_api_key)

    async def search(self, query, depth, days, max_results, topic="general"):
        response = await asyncio.to_thread(
            self._client().search,
            query,
            num_results=max_results,
            start_published_date=_start_date(days),
            contents={"text": {"max_characters": 2000}},
        )
        results = [
            SearchResult(
                url=r.url,
                title=r.title or "",
                content=r.text or "",
                published_date=(r.published_date or None),
                score=r.score,
            )
            for r in response.results
        ]
        return SearchResponse(provider=self.name, query=query, results=results)

    async def extract(self, urls):
        response = await asyncio.to_thread(self._client().get_contents, urls, text=True)
        pages = {
            r.url: (r.text or "")[: settings.fetch_max_chars] for r in response.results if r.text
        }
        return ExtractResponse(
            provider=self.name, pages=pages, failed=[u for u in urls if u not in pages]
        )


def get_providers() -> list[SearchProvider]:
    """Providers in fallback order. Tests replace this."""
    return [TavilyProvider(), DuckDuckGoProvider(), ExaProvider()]


async def _with_retry(fn, *args):
    async for attempt in AsyncRetrying(
        retry=retry_if_exception_type(TRANSIENT_ERRORS),
        stop=stop_after_attempt(3),
        wait=RETRY_WAIT,
        reraise=True,
    ):
        with attempt:
            return await fn(*args)


# --- Public API --------------------------------------------------------------


async def search(
    query: str, depth: Depth = "basic", days: int = 180, max_results: int = 5,
    topic: Topic = "general",
) -> SearchResponse:
    """Search the web, newest `days` only. Empty results are a valid answer (thin signal)."""
    params = {"query": query, "depth": depth, "days": days, "max_results": max_results}
    if topic != "general":  # keeps slugs and cache keys of existing recordings unchanged
        params["topic"] = topic
    slug = make_slug("search", query, params)
    if settings.demo_mode == "fixtures":
        return SearchResponse(**load_fixture(slug))

    skipped: list[str] = []
    response = SearchResponse(provider="none", query=query, results=[])
    for provider in get_providers():
        if not provider.available():
            continue
        cache_key = {"provider": provider.name, "op": "search", **params}
        if (hit := cache_get(cache_key)) is not None:
            response = SearchResponse(**{**hit, "credits": 0, "cached": True})
            break
        try:
            result = await _with_retry(provider.search, query, depth, days, max_results, topic)
        except Exception as error:  # provider down, out of credits, bad key...
            skipped.append(f"{provider.name}: {type(error).__name__}")
            log.warning("search via %s failed: %s", provider.name, error)
            continue
        if not result.results:
            skipped.append(f"{provider.name}: no results")
            continue
        cache_put(cache_key, result.model_dump())
        response = result
        break

    response.fallbacks = skipped
    if settings.demo_mode == "record":
        save_fixture(slug, response.model_dump())
    return response


async def extract(urls: list[str]) -> ExtractResponse:
    """Main text for each URL. URLs one provider cannot read are retried on the next."""
    allowed = [u for u in dict.fromkeys(urls) if not is_blocked(u)]
    blocked = [u for u in urls if is_blocked(u)]
    slug = make_slug("extract", " ".join(allowed)[:60], {"urls": allowed})
    if settings.demo_mode == "fixtures":
        return ExtractResponse(**load_fixture(slug))

    pages: dict[str, str] = {}
    credits = 0.0
    skipped: list[str] = []
    used: list[str] = []
    remaining = allowed
    for provider in get_providers():
        if not remaining:
            break
        if not provider.available():
            continue
        cache_key = {"provider": provider.name, "op": "extract", "urls": remaining}
        if (hit := cache_get(cache_key)) is not None:
            result = ExtractResponse(**{**hit, "credits": 0})
        else:
            try:
                result = await _with_retry(provider.extract, remaining)
            except Exception as error:
                skipped.append(f"{provider.name}: {type(error).__name__}")
                log.warning("extract via %s failed: %s", provider.name, error)
                continue
            cache_put(cache_key, result.model_dump())
        if result.pages:
            used.append(provider.name)
        pages.update(result.pages)
        credits += result.credits
        remaining = [u for u in remaining if u not in pages]

    response = ExtractResponse(
        provider="+".join(used) or "none",
        pages=pages,
        failed=remaining + blocked,
        credits=credits,
        fallbacks=skipped,
    )
    if settings.demo_mode == "record":
        save_fixture(slug, response.model_dump())
    return response
