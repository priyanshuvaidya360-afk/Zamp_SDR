"""T17: the simulated web for fictional demo companies (app/simweb.py)."""

from datetime import date

import pytest

from app import search
from app.config import REPO_DIR, settings
from app.simweb import SimulatedWebProvider


@pytest.fixture
def simweb(monkeypatch):
    monkeypatch.setattr(settings, "sim_web_dir", REPO_DIR / "seed" / "simweb")
    monkeypatch.setattr(settings, "today", date(2026, 10, 19))
    monkeypatch.setattr(settings, "sim_web_later", False)
    return SimulatedWebProvider()


async def test_answers_only_for_fictional_companies(simweb):
    assert await simweb.search('"Reco" raises funding round', "basic", 180, 5) is None  # real: not ours
    team = await simweb.search('"Priya Raman" site:acmefintech.com', "basic", 180, 5)
    assert team.results[0].url == "https://acmefintech.com/about/team"
    hiring = await simweb.search('"Acme Fintech" hiring SDR BDR sales development', "basic", 180, 5)
    assert [r.url for r in hiring.results] == ["https://acmefintech.com/careers/sdr"]


async def test_an_empty_answer_is_final(simweb, monkeypatch):
    """A fictional company with no funding news must not fall through to the live web."""
    called = []

    class Live:
        name, authoritative = "tavily", False

        def available(self):
            return True

        async def search(self, *args):
            called.append(args)

    monkeypatch.setattr(search, "get_providers", lambda: [simweb, Live()])
    response = await search.search('"Acme Fintech" raises funding round')
    assert response.provider == "simweb" and response.results == [] and called == []


async def test_the_clock_moves_for_case_10(simweb, monkeypatch):
    url = "https://kestrellabs.example/careers/sdr-london"
    assert "four Sales" in (await simweb.extract([url])).pages[url]
    monkeypatch.setattr(settings, "sim_web_later", True)
    assert "two Sales" in (await simweb.extract([url])).pages[url]


def test_off_unless_configured(monkeypatch):
    monkeypatch.setattr(settings, "sim_web_dir", None)
    assert [p.name for p in search.get_providers()] == ["tavily", "duckduckgo", "exa"]
