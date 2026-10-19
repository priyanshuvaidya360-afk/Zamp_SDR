"""T06: stages 1-3 (intake, resolve, gather) with search and LLM mocked."""

import hashlib
from datetime import timedelta

import pytest
from sqlmodel import Session

from app import ingest, llm, search
from app.config import settings
from app.llm import Usage
from app.models import Batch, Run, RunStatus, utcnow
from app.pipeline import intake, research, resolve, runner
from app.pipeline.resolve import Candidate, IdentityCandidates
from app.pipeline.runner import StopRun
from app.routes.batches import _store
from app.search import ExtractResponse, SearchResponse, SearchResult
from scripts.seed_context import seed

HEADER = "first_name,last_name,full_name,company,title,company_domain,linkedin_url,email\n"


def make_ctx(session: Session, row: str):
    batch = Batch(filename="t.csv")
    _store(batch, ingest.parse_upload((HEADER + row + "\n").encode()))
    session.add(batch)
    session.commit()
    [run_id] = runner.create_runs(session, batch)
    return runner._load_context(run_id, None)


PRIYA = "Priya,Raman,,Acme Fintech,VP Finance,acmefintech.com,,priya.raman@acmefintech.com"


class FakeSearch:
    def __init__(self):
        self.queries: list[str] = []
        self.extracted: list[list[str]] = []

    async def search(self, query, depth="basic", days=180, max_results=5, topic="general"):
        self.queries.append(query)
        tag = hashlib.md5(query.encode()).hexdigest()[:6]
        results = [SearchResult(url=f"https://news{i}.example.com/{tag}", title=f"{query} #{i}",
                                content="snippet") for i in range(3)]
        results.append(SearchResult(url="https://www.linkedin.com/in/someone", title="LinkedIn"))
        results.append(SearchResult(url="https://acmefintech.com/team", title="Team | Acme Fintech",
                                    content="Priya Raman, VP Finance at Acme Fintech"))
        results.append(SearchResult(url="https://afropop.org/audio", title="Audio programs",
                                    content="unrelated page"))
        return SearchResponse(provider="tavily", query=query, results=results, credits=1)

    async def extract(self, urls):
        self.extracted.append(list(urls))
        return ExtractResponse(provider="tavily", pages={u: "page text " * 2000 for u in urls},
                               failed=[], credits=1)


@pytest.fixture
def fake_search(monkeypatch) -> FakeSearch:
    fake = FakeSearch()
    monkeypatch.setattr(search, "search", fake.search)
    monkeypatch.setattr(search, "extract", fake.extract)
    return fake


def fake_identity(monkeypatch, *candidates: dict):
    calls = []

    async def structured(role, system, user, schema, providers=None):
        calls.append({"role": role, "user": user})
        found = IdentityCandidates(candidates=[Candidate(**c) for c in candidates], reasoning="test")
        return found, Usage(provider="groq", model="m", role=role, prompt_tokens=100, completion_tokens=20)

    monkeypatch.setattr(llm, "structured", structured)
    return calls


def person(name="Priya Raman", confidence=0.92, **extra) -> dict:
    return {"name": name, "title": "VP Finance", "company": "Acme Fintech", "company_domain": "acmefintech.com",
            "location": "Bengaluru, India", "country": "IN", "evidence_url": "https://acmefintech.com/team",
            "evidence": "Priya Raman, VP Finance", "confidence": confidence, **extra}


# --- Stage 1: intake -------------------------------------------------------------------


async def test_intake_with_no_history(session):
    ctx = make_ctx(session, "Ana,Lima,,Northwind,CFO,northwind.com,,")
    out = await intake.run(ctx)
    assert out.message == "No recent run · no past outreach · no open deals"
    assert out.touches == [] and out.open_opportunities == []


async def test_intake_collects_reply_outcome_and_open_deal(session):
    seed(session)
    out = await intake.run(make_ctx(session, PRIYA))
    assert out.reply_outcomes[0].outcome == "not_now" and out.reply_outcomes[0].same_person
    assert "replied 'not_now'" in out.message and out.crm_account.owner == "Asha Menon"

    deal = await intake.run(make_ctx(session, "Sam,Lee,,Contoso,Controller,contosologistics.com,,"))
    assert deal.open_opportunities[0].owner == "Sam Patel"
    assert "open deal (stage 4, Sam Patel)" in deal.message


async def test_intake_blocks_a_person_researched_recently(session):
    first = make_ctx(session, PRIYA)
    second = make_ctx(session, PRIYA)  # another batch, same person
    with pytest.raises(StopRun) as stop:
        await intake.run(second)
    assert stop.value.status == RunStatus.blocked
    assert stop.value.reason.startswith("Contacted recently")
    assert stop.value.payload == {"earlier_run_id": first.run_id}


def _update_run(session: Session, run_id: int, **fields) -> None:
    run = session.get(Run, run_id)
    for key, value in fields.items():
        setattr(run, key, value)
    session.add(run)
    session.commit()


async def test_intake_ignores_failed_old_or_disabled(session, monkeypatch):
    earlier = make_ctx(session, PRIYA)
    _update_run(session, earlier.run_id, status=RunStatus.failed)
    second = make_ctx(session, PRIYA)
    await intake.run(second)  # a failed earlier run doesn't block

    long_ago = utcnow() - timedelta(days=31)
    _update_run(session, second.run_id, status=RunStatus.ready_for_review, started_at=long_ago)
    third = make_ctx(session, PRIYA)
    await intake.run(third)  # an earlier run outside the 30-day window doesn't block

    monkeypatch.setattr(settings, "recent_run_days", 0)
    await intake.run(make_ctx(session, PRIYA))  # third is recent, but the check is off


# --- Stage 2: resolve ------------------------------------------------------------------


def test_identity_queries_use_domain_company_role_and_linkedin():
    row = {"first_name": "Philipp", "last_name": "Lebron", "full_name": "Philipp Wenger Lebron",
           "company": "SIDES", "domain": "get-sides.de", "title": "Head of Growth",
           "linkedin_url": "linkedin.com/in/philippwenger"}
    assert resolve.build_queries(row) == [
        '"Philipp Lebron" site:get-sides.de',
        '"Philipp Lebron" "SIDES"',
        "Philipp Wenger Lebron Head of Growth SIDES",
        '"linkedin.com/in/philippwenger" Philipp Wenger Lebron',
    ]


async def test_one_strong_match_continues(session, fake_search, monkeypatch):
    calls = fake_identity(monkeypatch, person(), person("Priya R. (other)", confidence=0.2))
    ctx = make_ctx(session, PRIYA)
    out = await resolve.run(ctx)
    assert out.person.name == "Priya Raman" and ctx.confidence == 0.92
    assert out.message == "Found 1 match, confidence 0.92: Priya Raman (VP Finance, Acme Fintech)"
    assert calls[0]["role"] == "fast" and "<untrusted_source>" in calls[0]["user"]
    assert len(fake_search.queries) == 3 and ctx.tokens == 120 and ctx.credits == 3


async def test_two_plausible_people_ask_the_rep(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, person(confidence=0.85), person("Rahul Sharma (Pune)", confidence=0.7))
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, PRIYA))
    assert stop.value.status == RunStatus.needs_input
    assert stop.value.reason == "2 people could match. Pick the right one."
    assert len(stop.value.payload["candidates"]) == 2


@pytest.mark.parametrize(("candidates", "reason"), [
    ([], "Couldn't find this person in public search results. Confirm who they are."),
    ([person(confidence=0.6)], "Best match is only 0.60 confident. Confirm it's the right person."),
    ([person(confidence=0.3)], "No confident match in public search results. Confirm who they are."),
])
async def test_weak_or_no_match_asks_the_rep(session, fake_search, monkeypatch, candidates, reason):
    fake_identity(monkeypatch, *candidates)
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, PRIYA))
    assert (stop.value.status, stop.value.reason) == (RunStatus.needs_input, reason)


async def test_linkedin_url_match_is_strong_evidence_and_fills_company(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, person("Hanna Andrzejewska", confidence=0.6, company="GetResponse",
                                      company_domain="getresponse.com",
                                      evidence_url="https://pl.linkedin.com/in/Hanna-A/"))
    ctx = make_ctx(session, "Hanna,Andrzejewska,,,,,linkedin.com/in/hanna-a,")
    assert ctx.account_key is None
    out = await resolve.run(ctx)
    assert out.person.confidence == 0.9
    assert (out.company, out.domain, ctx.account_key) == ("GetResponse", "getresponse.com", "getresponse.com")
    session.expire_all()
    stored = session.get(Run, ctx.run_id)
    assert (stored.company, stored.domain, stored.account_key) == ("GetResponse", "getresponse.com", "getresponse.com")


async def test_evidence_url_the_model_made_up_is_capped(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, person(evidence_url="https://invented.example.com/priya"))
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, PRIYA))
    assert stop.value.status == RunStatus.needs_input
    assert stop.value.payload["candidates"][0]["confidence"] == 0.5
    assert stop.value.payload["checks"] == ["Priya Raman: evidence URL not in the search results"]


async def test_evidence_that_does_not_name_person_and_company_cannot_auto_accept(session, fake_search, monkeypatch):
    # A real result URL, but its text never mentions Priya or Acme.
    fake_identity(monkeypatch, person(evidence_url="https://afropop.org/audio", evidence="a page"))
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, PRIYA))
    assert stop.value.payload["candidates"][0]["confidence"] == 0.75


async def test_strict_region_is_held_for_compliance(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, person(country="DE", location="Berlin, Germany"))
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, PRIYA))
    assert stop.value.status == RunStatus.held
    assert stop.value.reason.startswith("Compliance check needed")


async def test_resume_with_the_reps_pick_skips_searching(session, fake_search, monkeypatch):
    calls = fake_identity(monkeypatch)
    ctx = make_ctx(session, PRIYA)
    ctx.resume_input = {"candidate": person(confidence=0.4)}
    out = await resolve.run(ctx)
    assert out.rep_confirmed and out.person.confidence == 1.0 and ctx.confidence == 1.0
    assert fake_search.queries == [] and calls == []


# --- Stage 3: gather -------------------------------------------------------------------


async def test_gather_respects_caps_and_never_reads_linkedin(session, fake_search):
    ctx = make_ctx(session, PRIYA)
    out = await research.run(ctx)
    assert out.searches == len(fake_search.queries) <= settings.gather_max_searches
    read = [u for batch in fake_search.extracted for u in batch]
    assert len(read) <= settings.gather_max_extracts and not any("linkedin" in u for u in read)
    assert {s.about for s in out.sources} == {"company", "person"}
    assert all(len(s.text) == settings.source_max_chars for s in out.sources)
    categories = {q.category for q in out.queries}
    assert categories >= {"funding", "hiring", "product_launch", "leadership_change", "expansion", "person_content"}
    assert out.credits == ctx.credits == out.searches + len(fake_search.extracted)
    assert out.message.startswith(f"{out.searches} searches, {len(out.sources)} of {len(out.sources)} pages read")


async def test_gather_skips_results_that_never_mention_the_company_or_person(session, fake_search):
    out = await research.run(make_ctx(session, PRIYA))
    assert not any("afropop" in s.url for s in out.sources)
    by_category = {q.category: q for q in out.queries}
    # Company results name Acme in the title/URL; LinkedIn and afropop are dropped.
    assert all(q.relevant == q.results - 2 for q in out.queries if q.about == "company")
    # Person results need the full name AND the company: only the team page has both.
    assert by_category["person_content"].relevant == 1
    assert {q.category: q.query for q in out.queries}["funding"] == '"Acme Fintech" raises funding round'


async def test_company_research_runs_once_per_account(session, fake_search):
    priya = make_ctx(session, PRIYA)
    dev = make_ctx(session, "Dev,Rao,,Acme Fintech,Controller,acmefintech.com,,")
    dev.batch = priya.batch  # same batch -> shared research
    await research.run(priya)
    company_searches = len(fake_search.queries) - len(research.PERSON_QUERIES)
    await research.run(dev)
    assert len(fake_search.queries) == company_searches + 2 * len(research.PERSON_QUERIES)
    assert dev.credits < priya.credits  # company research charged once, to Priya's run
