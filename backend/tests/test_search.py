import httpx
import pytest

from app import fetch, search
from app.config import settings
from app.fixtures import FixtureMissing
from app.search import ExtractResponse, SearchResponse, SearchResult


class FakeProvider:
    def __init__(self, name, results=None, error=None, pages=None, available=True):
        self.name = name
        self._results = results or []
        self._error = error
        self._pages = pages or {}
        self._available = available
        self.search_calls = 0
        self.extract_calls: list[list[str]] = []

    def available(self):
        return self._available

    async def search(self, query, depth, days, max_results, topic="general"):
        self.search_calls += 1
        if self._error:
            raise self._error
        results = [SearchResult(url=u, title=u) for u in self._results]
        return SearchResponse(provider=self.name, query=query, results=results,
                              credits=1 if self.name == "tavily" else 0)

    async def extract(self, urls):
        self.extract_calls.append(urls)
        if self._error:
            raise self._error
        pages = {u: t for u, t in self._pages.items() if u in urls}
        return ExtractResponse(provider=self.name, pages=pages,
                               failed=[u for u in urls if u not in pages])


def use(monkeypatch, *providers):
    monkeypatch.setattr(search, "get_providers", lambda: list(providers))


async def test_tavily_answers_first(monkeypatch):
    tavily, ddg = FakeProvider("tavily", ["https://a.com"]), FakeProvider("duckduckgo", ["https://b.com"])
    use(monkeypatch, tavily, ddg)
    response = await search.search("acme funding")
    assert response.provider == "tavily" and response.credits == 1 and ddg.search_calls == 0


async def test_tavily_error_falls_back_to_duckduckgo(monkeypatch):
    use(monkeypatch, FakeProvider("tavily", error=RuntimeError("usage limit exceeded")),
        FakeProvider("duckduckgo", ["https://b.com"]))
    response = await search.search("acme funding")
    assert response.provider == "duckduckgo"
    assert response.fallbacks == ["tavily: RuntimeError"]


async def test_empty_results_fall_back_and_empty_everywhere_is_valid(monkeypatch):
    use(monkeypatch, FakeProvider("tavily"), FakeProvider("duckduckgo", ["https://b.com"]))
    assert (await search.search("q1")).provider == "duckduckgo"

    use(monkeypatch, FakeProvider("tavily"), FakeProvider("duckduckgo"))
    response = await search.search("q2")
    assert response.provider == "none" and response.results == []


async def test_transient_errors_are_retried(monkeypatch):
    class Flaky(FakeProvider):
        attempts = 0

        async def search(self, *args):
            self.attempts += 1
            if self.attempts < 3:
                raise httpx.ConnectError("reset")
            return await super().search(*args)

    flaky = Flaky("tavily", ["https://a.com"])
    use(monkeypatch, flaky)
    assert (await search.search("q")).provider == "tavily" and flaky.attempts == 3


async def test_unavailable_provider_is_skipped(monkeypatch):
    exa = FakeProvider("exa", ["https://c.com"], available=False)
    use(monkeypatch, FakeProvider("tavily"), exa)
    assert (await search.search("q")).provider == "none" and exa.search_calls == 0


async def test_cache_saves_credits(monkeypatch):
    tavily = FakeProvider("tavily", ["https://a.com"])
    use(monkeypatch, tavily)
    await search.search("acme funding")
    again = await search.search("acme funding")
    assert tavily.search_calls == 1 and again.cached and again.credits == 0


async def test_record_then_replay_search_without_network(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", "record")
    use(monkeypatch, FakeProvider("tavily", ["https://a.com"]))
    recorded = await search.search("acme funding")
    assert len(list(settings.fixtures_dir.glob("search-acme-funding-*.json"))) == 1

    monkeypatch.setattr(settings, "demo_mode", "fixtures")
    use(monkeypatch, FakeProvider("tavily", error=AssertionError("network used")))
    replayed = await search.search("acme funding")
    assert replayed.results == recorded.results

    with pytest.raises(FixtureMissing, match="search-never-recorded-"):
        await search.search("never recorded")


async def test_extract_retries_failed_urls_on_next_provider_and_skips_linkedin(monkeypatch):
    tavily = FakeProvider("tavily", pages={"https://a.com": "A text"})
    ddg = FakeProvider("duckduckgo", pages={"https://b.com": "B text"})
    use(monkeypatch, tavily, ddg)
    urls = ["https://a.com", "https://b.com", "https://www.linkedin.com/in/someone"]
    response = await search.extract(urls)
    assert response.pages == {"https://a.com": "A text", "https://b.com": "B text"}
    assert ddg.extract_calls == [["https://b.com"]]  # only what Tavily could not read
    assert response.failed == ["https://www.linkedin.com/in/someone"]
    assert response.provider == "tavily+duckduckgo"
    assert all("linkedin" not in u for call in tavily.extract_calls for u in call)


async def test_fetch_text_extracts_main_content_and_caps_length(monkeypatch):
    article = "<p>" + "Acme is hiring four accounts payable clerks this quarter. " * 30 + "</p>"
    html = f"<html><body><nav>Menu</nav><article><h1>Acme hiring</h1>{article}</article></body></html>"

    def handler(request):
        if request.url.host == "down.example.com":
            return httpx.Response(503)
        return httpx.Response(200, html=html)

    monkeypatch.setattr(settings, "fetch_max_chars", 100)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        text = await fetch.fetch_text("https://acme.example.com/careers", client)
        assert text and "accounts payable" in text and len(text) == 100
        assert await fetch.fetch_text("https://down.example.com/", client) is None
        assert await fetch.fetch_text("https://linkedin.com/in/x", client) is None
