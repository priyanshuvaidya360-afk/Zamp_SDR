"""T06: stages 1-3 (intake, resolve, gather) with search and LLM mocked."""

import hashlib
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app import ingest, llm, search
from app.config import settings
from app.llm import Usage
from app.models import Batch, DraftRecord, Run, RunStatus, utcnow
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
        self.site_up: bool | None = True
        self.reachable_checked: list[str] = []

    async def site_reachable(self, domain):
        self.reachable_checked.append(domain)
        return self.site_up

    async def search(self, query, depth="basic", days=180, max_results=5, topic="general", free_first=False):
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
    monkeypatch.setattr(search, "site_reachable", fake.site_reachable)
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


async def test_intake_collects_reply_outcome_and_crm_owner(session):
    seed(session)
    out = await intake.run(make_ctx(session, PRIYA))
    assert out.reply_outcomes[0].outcome == "not_now" and out.reply_outcomes[0].same_person
    assert "replied 'not_now'" in out.message and out.crm_account.owner == "Asha Menon"
    # The rules that act on this context (follow-up, suppressed, held) are in tests/test_rules.py.


async def test_intake_blocks_a_person_researched_recently(session):
    first = make_ctx(session, PRIYA)
    second = make_ctx(session, PRIYA)  # another batch, same person
    with pytest.raises(StopRun) as stop:
        await intake.run(second)
    assert stop.value.status == RunStatus.blocked  # the earlier run has no draft (yet)
    assert stop.value.reason.startswith("Contacted recently")
    assert stop.value.payload == {"earlier_run_id": first.run_id, "earlier_status": "running"}


async def test_a_person_drafted_recently_shows_that_draft_instead(session):
    first = make_ctx(session, PRIYA)
    session.add(DraftRecord(run_id=first.run_id, version=1, subject="s", body="b", claims=[]))
    _update_run(session, first.run_id, status=RunStatus.ready_for_review)
    with pytest.raises(StopRun) as stop:
        await intake.run(make_ctx(session, PRIYA))  # another batch, same person
    draft = session.exec(select(DraftRecord)).one()
    assert stop.value.status == RunStatus.already_drafted
    assert stop.value.reason.startswith(f"Already drafted on ") and f"run #{first.run_id} (ready for review)" in stop.value.reason
    assert stop.value.payload == {"earlier_run_id": first.run_id, "earlier_status": "ready_for_review",
                                  "draft_id": draft.id}


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


async def test_resolve_runs_without_a_linkedin_url(session, fake_search, monkeypatch):
    # Work email only: the domain comes from the email, no LinkedIn query, and a missing URL is no failure.
    fake_identity(monkeypatch, person())
    ctx = make_ctx(session, "Priya,Raman,,Acme Fintech,VP Finance,,,priya.raman@acmefintech.com")
    assert ctx.row.get("linkedin_url") is None and ctx.row["domain"] == "acmefintech.com"
    out = await resolve.run(ctx)
    assert not any("linkedin" in q.lower() for q in out.queries)
    assert out.person.name == "Priya Raman" and out.checks == []

    fake_identity(monkeypatch)  # nobody found: the rep is asked; the diagnosis never blames a missing URL
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, "Priya,Raman,,Acme Fintech,VP Finance,acmefintech.com,,"))
    assert stop.value.status == RunStatus.needs_input
    assert not any("linkedin" in c["label"].lower() for c in stop.value.payload["diagnosis"])


async def test_one_strong_match_continues(session, fake_search, monkeypatch):
    calls = fake_identity(monkeypatch, person(), person("Priya R. (other)", confidence=0.2))
    ctx = make_ctx(session, PRIYA)
    out = await resolve.run(ctx)
    # Same first + last name and the CSV's company domain, evidence checked: certain.
    assert out.person.name == "Priya Raman" and ctx.confidence == 1.0
    assert out.message == "Found 1 match, confidence 1.00: Priya Raman (VP Finance, Acme Fintech)"
    assert calls[0]["role"] == "fast" and "<untrusted_source>" in calls[0]["user"]
    assert len(fake_search.queries) == 3 and ctx.tokens == 120 and ctx.credits == 3


async def test_two_plausible_people_ask_the_rep(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, person(confidence=0.85), person("Rahul Sharma (Pune)", confidence=0.7))
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, PRIYA))
    assert stop.value.status == RunStatus.needs_input
    assert stop.value.reason == "2 people could match. Pick the right one."
    assert len(stop.value.payload["candidates"]) == 2


async def test_the_same_person_found_twice_is_not_a_rival(session, fake_search, monkeypatch):
    # Her LinkedIn and a RocketReach page: one person, two sources (the Hanna case from a real run).
    fake_identity(monkeypatch, person(confidence=0.95),
                  person("Priya Raman (Acme)", confidence=0.85, evidence_url="https://acmefintech.com/team"))
    out = await resolve.run(make_ctx(session, PRIYA))
    assert out.person.confidence == 1.0 and len(out.candidates) == 1


def test_same_name_at_different_companies_stays_ambiguous():
    a = Candidate(**person())
    b = Candidate(**person(company="Other Co", company_domain="other.co"))
    assert len(resolve.merge_same_person([a, b])) == 2


@pytest.mark.parametrize(("candidates", "reason"), [
    # No candidate, but results name her with Acme (the fake's titles echo the query): the rep confirms.
    ([], "Priya Raman appears in 10 results, but none confirms who they are. Confirm it's the right person."),
    # No employer domain on the candidate, so the name + domain rule can't make it certain.
    ([person(confidence=0.6, company_domain=None)], "Best match is only 0.60 confident. Confirm it's the right person."),
    ([person(confidence=0.3, company_domain=None)], "No confident match in public search results. Confirm who they are."),
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
    assert out.person.confidence == 1.0
    assert (out.company, out.domain, ctx.account_key) == ("GetResponse", "getresponse.com", "getresponse.com")
    session.expire_all()
    stored = session.get(Run, ctx.run_id)
    assert (stored.company, stored.domain, stored.account_key) == ("GetResponse", "getresponse.com", "getresponse.com")


def _profile_person(slug, title="VP Finance", **extra) -> dict:
    return person(title=title, evidence_url=f"https://in.linkedin.com/in/{slug}",
                  evidence=f"Priya Raman, {title} at Acme Fintech", **extra)


async def test_two_people_with_the_same_name_at_the_same_company_ask_the_rep(session, fake_search, monkeypatch):
    # Two different LinkedIn profiles, both Priya Raman at acmefintech.com, and no LinkedIn URL in the CSV.
    fake_identity(monkeypatch, _profile_person("priya-raman-1", confidence=0.95),
                  _profile_person("priya-raman-2", title="Analyst", confidence=0.9))
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, PRIYA))
    assert (stop.value.status, stop.value.reason) == (RunStatus.needs_input, "2 people could match. Pick the right one.")
    assert [c["confidence"] for c in stop.value.payload["candidates"]] == [0.5, 0.5]
    assert "2 people named Priya Raman at acmefintech.com: ask the rep" in stop.value.payload["checks"]


async def test_the_csv_linkedin_url_settles_a_namesake(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, _profile_person("priya-raman-2", title="Analyst", confidence=0.9),
                  _profile_person("priya-raman-1", confidence=0.6))
    out = await resolve.run(make_ctx(session, PRIYA.replace(",,priya", ",linkedin.com/in/priya-raman-1,priya")))
    assert out.person.evidence_url.endswith("priya-raman-1") and out.person.confidence == 1.0
    assert [c.confidence for c in out.candidates] == [1.0, 0.3]


async def test_name_and_domain_are_not_enough_without_matching_names(session, fake_search, monkeypatch):
    # Right company and domain, but a different first name: not certain.
    fake_identity(monkeypatch, person("Priyanka Raman", confidence=0.6, evidence="Priyanka Raman, VP Finance"))
    with pytest.raises(StopRun) as stop:
        await resolve.run(make_ctx(session, PRIYA))
    assert stop.value.payload["candidates"][0]["confidence"] == 0.6


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


async def test_details_the_rep_typed_replace_the_csv(session, fake_search, monkeypatch):
    # No candidates found: the rep types who the person is, and that beats the CSV.
    fake_identity(monkeypatch)
    ctx = make_ctx(session, PRIYA)
    ctx.resume_input = {"candidate": person(title="CFO", company="Acme Pay", company_domain="https://acmepay.io/",
                                            location=None, country=None, evidence_url="", confidence=1)}
    await resolve.run(ctx)
    assert (ctx.row["title"], ctx.row["company"], ctx.row["domain"]) == ("CFO", "Acme Pay", "acmepay.io")
    run = session.get(Run, ctx.run_id)
    session.refresh(run)
    assert (run.title, run.company, run.domain) == ("CFO", "Acme Pay", "acmepay.io")


# --- Stage 2: why identity failed (diagnose) -------------------------------------------


def answer_searches(monkeypatch, *results: SearchResult, provider="tavily", fallbacks=()):
    async def fake(query, depth="basic", days=180, max_results=5, topic="general", free_first=False):
        return SearchResponse(provider=provider, query=query, results=list(results), fallbacks=list(fallbacks))

    monkeypatch.setattr(search, "search", fake)


async def stop_of(ctx) -> StopRun:
    with pytest.raises(StopRun) as stop:
        await resolve.run(ctx)
    return stop.value


async def test_search_outage_is_not_blamed_on_the_prospect(session, fake_search, monkeypatch):
    fake_identity(monkeypatch)
    answer_searches(monkeypatch, provider="none", fallbacks=["tavily: HTTPError", "duckduckgo: no results"])
    ctx = make_ctx(session, PRIYA)
    stop = await stop_of(ctx)
    assert (stop.status, stop.payload["outcome"]) == (RunStatus.needs_input, "search_failed")
    assert stop.reason.startswith("Web search wasn't available")
    assert stop.payload["diagnosis"] == [{"label": "Search unavailable (tavily)", "ok": False}]
    assert session.get(Run, ctx.run_id).identity_outcome == "search_failed"


async def test_nothing_found_because_nothing_exists_is_still_search_working(session, fake_search, monkeypatch):
    # Every provider answered "no results": search worked, the person just isn't there.
    fake_identity(monkeypatch)
    answer_searches(monkeypatch, provider="none", fallbacks=["tavily: no results", "duckduckgo: no results"])
    stop = await stop_of(make_ctx(session, PRIYA))
    assert stop.payload["outcome"] == "not_public"
    assert stop.reason == "Acme Fintech is real, but Priya Raman doesn't appear anywhere online with it."
    assert fake_search.reachable_checked == ["acmefintech.com"]


@pytest.mark.parametrize(("first", "last", "why"), [
    ("Test", "User", "'Test User' looks like a placeholder"),
    ("John", "Doe", "'John Doe' is a placeholder name"),
    ("Priya", "Raman2", "'Priya Raman2' contains digits"),
])
async def test_placeholder_names_are_bad_data(session, fake_search, monkeypatch, first, last, why):
    fake_identity(monkeypatch)
    stop = await stop_of(make_ctx(session, f"{first},{last},,Acme Fintech,VP Finance,acmefintech.com,,"))
    assert stop.payload["outcome"] == "bad_data"
    assert stop.reason == f"This row looks like bad data: {why}. Fix the row or dismiss it."


def test_unusual_real_names_are_not_called_fake():
    for first, last in [("Na", "Li"), ("Bo", "Bo"), ("Ng", "Ng"), ("Mary-Ann", "O'Neil"), ("X", "Æ")]:
        assert resolve.placeholder_name(first, last) is None, (first, last)


async def test_dead_domain_with_no_mentions_is_bad_data(session, fake_search, monkeypatch):
    fake_identity(monkeypatch)
    answer_searches(monkeypatch, SearchResult(url="https://elsewhere.com/x", title="Something else", content="unrelated"))
    fake_search.site_up = False
    stop = await stop_of(make_ctx(session, PRIYA))
    assert stop.payload["outcome"] == "bad_data"
    assert "acmefintech.com doesn't load" in stop.reason
    assert {"label": "acmefintech.com doesn't load", "ok": False} in stop.payload["diagnosis"]


async def test_real_company_without_the_person_is_not_public(session, fake_search, monkeypatch):
    fake_identity(monkeypatch)
    answer_searches(monkeypatch, SearchResult(url="https://acmefintech.com/about", title="About Acme Fintech",
                                              content="Acme Fintech builds payments software."))
    stop = await stop_of(make_ctx(session, PRIYA))
    assert stop.payload["outcome"] == "not_public"
    assert fake_search.reachable_checked == []  # the company was found: no need to load its site
    labels = {c["label"]: c["ok"] for c in stop.payload["diagnosis"]}
    assert labels == {"Search working (3 queries, 1 result)": True, "Name looks real": True,
                      "Email is at acmefintech.com": True, "Acme Fintech named in 1 result": True,
                      "Raman named with Acme Fintech in 0 results": False}


async def test_two_rivals_are_ambiguous(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, person(confidence=0.85), person("Priya Raman (Pune)", confidence=0.7,
                                                                            company="Other", company_domain="other.com"))
    stop = await stop_of(make_ctx(session, PRIYA))
    assert (stop.payload["outcome"], stop.reason) == ("ambiguous", "2 people could match. Pick the right one.")


async def test_company_level_draft_continues_unverified(session, fake_search, monkeypatch):
    calls = fake_identity(monkeypatch)
    ctx = make_ctx(session, PRIYA)
    ctx.resume_input = {"unverified": True}
    out = await resolve.run(ctx)
    assert out.unverified and not out.rep_confirmed
    assert out.person.confidence == ctx.confidence == settings.identity_unverified
    assert (out.person.company, out.person.company_domain) == ("Acme Fintech", "acmefintech.com")
    assert fake_search.queries == [] and calls == []
    assert session.get(Run, ctx.run_id).identity_unverified


async def test_company_level_draft_needs_a_company(session, fake_search, monkeypatch):
    fake_identity(monkeypatch)
    ctx = make_ctx(session, "Hanna,Andrzejewska,,,,,linkedin.com/in/hanna-a,")
    ctx.resume_input = {"unverified": True}
    assert (await stop_of(ctx)).status == RunStatus.needs_input


async def test_a_fixed_row_is_searched_again_with_the_new_details(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, person(company="Acme Pay", company_domain="acmepay.io", evidence_url="https://acmefintech.com/team"))
    ctx = make_ctx(session, PRIYA)
    ctx.resume_input = {"fix": {"company": "Acme Pay", "company_domain": "https://www.acmepay.io/", "title": "CFO"}}
    with pytest.raises(StopRun):  # the fake's evidence doesn't name Acme Pay: not auto-accepted, but searched
        await resolve.run(ctx)
    assert fake_search.queries[0] == '"Priya Raman" site:acmepay.io'
    assert (ctx.row["company"], ctx.row["domain"], ctx.row["title"]) == ("Acme Pay", "acmepay.io", "CFO")


async def test_rep_can_dismiss(session, fake_search, monkeypatch):
    fake_identity(monkeypatch)
    ctx = make_ctx(session, PRIYA)
    ctx.resume_input = {"dismiss": True}
    stop = await stop_of(ctx)
    assert stop.status == RunStatus.dismissed and fake_search.queries == []


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
    assert all(q.relevant == q.results - 2 for q in out.queries if q.about == "company" and q.category not in research.SITE_QUERIES)
    # The own-site search keeps only pages on acmefintech.com: the team page.
    assert by_category["company_site"].relevant == 1 and by_category["company_site"].query == "site:acmefintech.com Acme Fintech"
    # Person results need the full name AND the company: only the team page has both.
    assert by_category["person_content"].relevant == 1
    assert {q.category: q.query for q in out.queries}["funding"] == '"Acme Fintech" raises funding round'


async def test_company_research_runs_once_per_account(session, fake_search):
    priya = make_ctx(session, PRIYA)
    dev = make_ctx(session, "Dev,Rao,,Acme Fintech,Controller,acmefintech.com,,")
    dev.batch = priya.batch  # same batch -> shared research
    await research.run(priya)
    per_person = len(research.PERSON_QUERIES) + len(research.PERSON_WIDE_QUERIES)
    company_searches = len(fake_search.queries) - per_person
    await research.run(dev)
    assert len(fake_search.queries) == company_searches + 2 * per_person
    assert dev.credits < priya.credits  # company research charged once, to Priya's run


@pytest.mark.parametrize(("raw", "clean"), [
    ("CredWise Finserv India | Home Loan Experts", "CredWise Finserv India"),
    ("Fast VPS & FinTech Services (FVFS)", "Fast VPS & FinTech Services"),
    ("Acme - The payments company", "Acme"),
    ("Coca-Cola", "Coca-Cola"),
    ("Acme Fintech", "Acme Fintech"),
])
def test_company_names_are_cleaned_for_search(raw, clean):
    assert research.search_name(raw) == clean


async def test_own_site_is_searched_without_a_date_window(session, fake_search, monkeypatch):
    windows = {}
    original = fake_search.search

    async def spy(query, depth="basic", days=180, max_results=5, topic="general", free_first=False):
        windows[query] = (days, free_first)
        return await original(query, depth, days, max_results, topic)

    monkeypatch.setattr(search, "search", spy)
    out = await research.run(make_ctx(session, "Ana,Lima,,CredWise Finserv | Home Loans,CFO,acmefintech.com,,"))
    assert windows["site:acmefintech.com CredWise Finserv"] == (research.SITE_DAYS, False)
    assert windows['"CredWise Finserv" raises funding round'] == (settings.signal_window_days, False)  # no tagline
    # The wider nets try the free provider first.
    assert windows['"CredWise Finserv" reviews G2 OR Capterra OR Trustpilot'][1] is True
    assert windows['"Ana Lima" article OR blog OR wrote OR webinar'][1] is True
    assert any(s.url == "https://acmefintech.com/team" for s in out.sources)
    assert len(windows) <= settings.gather_max_searches


async def test_no_domain_means_no_site_search(session, fake_search):
    await research.run(make_ctx(session, "Hanna,Andrzejewska,,GetResponse,,,linkedin.com/in/hanna-a,"))
    assert not any(q.startswith("site:") for q in fake_search.queries)


async def test_a_page_both_searches_found_is_kept_once(session, fake_search):
    # acmefintech.com/team names Priya and Acme: the own-site search and the person search both keep it.
    out = await research.run(make_ctx(session, PRIYA))
    urls = [s.url for s in out.sources]
    assert len(urls) == len(set(urls))
    assert [s.about for s in out.sources if s.url == "https://acmefintech.com/team"] == ["person"]


async def test_every_search_result_is_kept_for_the_sources_tab(session, fake_search, monkeypatch):
    fake_identity(monkeypatch, person())
    ctx = make_ctx(session, PRIYA)
    resolved = await resolve.run(ctx)
    assert {"url": "https://www.linkedin.com/in/someone", "title": "LinkedIn"} in resolved.found
    assert len({f["url"] for f in resolved.found}) == len(resolved.found)
    gathered = await research.run(ctx)
    funding = next(q for q in gathered.queries if q.category == "funding")
    assert len(funding.found) == funding.results == 6 and funding.relevant < 6  # dropped results are listed too



def test_company_named_inside_the_article_counts_but_one_passing_mention_does_not():
    terms = research.company_terms("Varicash", "varicash.com")
    inside = SearchResult(url="https://news.example/fintech-week", title="Fintech week",
                          content="Varicash, a lending startup, said... Varicash will also hire.")
    domain = SearchResult(url="https://events.example/sponsors", title="Sponsors", content="Silver: varicash.com")
    listicle = SearchResult(url="https://list.example/top-50", title="Top 50 startups", content="... Varicash ...")
    assert research.is_about_company(inside, terms) and research.is_about_company(domain, terms)
    assert not research.is_about_company(listicle, terms)


@pytest.mark.parametrize(("query", "accepted"), [
    ('"Acme Fintech" RBI payment aggregator licence', True),
    ("site:acmefintech.com pricing", True),
    ("best payment startups India", False),          # doesn't name the company
    ('"Acme Fintech" site:linkedin.com', False),     # never LinkedIn
    ('"Acme Fintech" site:crunchbase.com', False),   # site: only on their own domain
    ('"Acme Fintech" raises funding round', False),  # already planned
])
def test_code_checks_every_planned_query(query, accepted):
    existing = {'"acme fintech" raises funding round'}
    result = research.accept_planned(research.PlannedQuery(query=query), "Acme Fintech", "acmefintech.com", existing)
    assert (result is not None) == accepted


def planner(monkeypatch, *queries: str) -> list[dict]:
    calls = []

    async def structured(role, system, user, schema, providers=None):
        calls.append({"role": role, "user": user})
        plan = research._Plan(queries=[research.PlannedQuery(query=q, why="test") for q in queries])
        return plan, Usage(provider="groq", model="m", role=role, prompt_tokens=50, completion_tokens=10)

    monkeypatch.setattr(llm, "structured", structured)
    return calls


async def test_planned_queries_run_free_first_and_count_toward_the_cap(session, fake_search, monkeypatch):
    monkeypatch.setattr(settings, "gather_planned_queries", 3)
    calls = planner(monkeypatch, '"Acme Fintech" RBI licence', "top fintech startups")
    ctx = make_ctx(session, PRIYA)
    out = await research.run(ctx)
    assert calls[0]["role"] == "fast" and '"Acme Fintech" raises funding round' in calls[0]["user"]
    assert [q.query for q in out.planned] == ['"Acme Fintech" RBI licence']  # the off-topic one is dropped
    assert {q.query: q.about for q in out.queries if q.category.startswith("explore_")} == {
        '"Acme Fintech" RBI licence': "company"}
    assert out.searches <= settings.gather_max_searches and ctx.tokens == 60
    assert "1 planned for Acme Fintech" in out.message


async def test_colleagues_share_one_plan_whoever_runs_first(session, fake_search, monkeypatch):
    # Company research runs once per account, inside whichever colleague's run gets there first.
    # The plan must not depend on who that is, or a replay could ask for searches never recorded.
    monkeypatch.setattr(settings, "gather_planned_queries", 2)
    calls = planner(monkeypatch, '"Acme Fintech" RBI licence')
    priya = make_ctx(session, PRIYA)
    dev = make_ctx(session, "Dev,Rao,,Acme Fintech,Controller,acmefintech.com,,")
    dev.batch = priya.batch
    first = await research.run(dev)
    second = await research.run(priya)
    assert len(calls) == 1  # planned once for the account
    assert "Dev Rao" not in calls[0]["user"] and "Priya Raman" not in calls[0]["user"]  # the company alone
    assert first.planned == second.planned and [q.query for q in second.planned] == ['"Acme Fintech" RBI licence']


async def test_a_failed_planner_never_fails_the_run(session, fake_search, monkeypatch):
    monkeypatch.setattr(settings, "gather_planned_queries", 3)

    async def down(*args, **kwargs):
        raise llm.LLMUnavailable("both providers down")

    monkeypatch.setattr(llm, "structured", down)
    out = await research.run(make_ctx(session, PRIYA))
    assert out.planned == [] and out.searches == 14


def test_the_prospect_list_says_who_was_researched_before_drafting(session, client):
    # Priya was researched in an earlier batch; Dev is new. The list says so before any Draft click.
    earlier = make_ctx(session, PRIYA)
    _update_run(session, earlier.run_id, status=RunStatus.ready_for_review)
    csv = (HEADER + PRIYA + "\nDev,Rao,,Acme Fintech,Controller,acmefintech.com,,\n").encode()
    batch_id = client.post("/batches", files={"file": ("again.csv", csv, "text/csv")}).json()["batch_id"]
    rows = {p["name"]: p["researched_before"] for p in client.get(f"/batches/{batch_id}").json()["prospects"]}
    assert rows["Priya Raman"]["run_id"] == earlier.run_id and rows["Priya Raman"]["status"] == "ready_for_review"
    assert rows["Dev Rao"] is None  # a fresh prospect


def test_a_row_whose_own_research_is_done_is_already_researched(session, client):
    from app.models import StageEvent, StageState
    csv = (HEADER + PRIYA + "\n").encode()
    batch_id = client.post("/batches", files={"file": ("one.csv", csv, "text/csv")}).json()["batch_id"]
    assert client.get(f"/batches/{batch_id}").json()["prospects"][0]["researched_before"] is None  # not drafted yet
    [run_id] = runner.create_runs(session, session.get(Batch, batch_id))
    _update_run(session, run_id, status=RunStatus.low_signal)
    session.add(StageEvent(run_id=run_id, stage="extract", state=StageState.done))
    session.commit()
    [row] = client.get(f"/batches/{batch_id}").json()["prospects"]
    assert row["researched_before"]["run_id"] == run_id  # drafting again won't search again


def test_a_stopped_rerun_never_counts_as_research(session):
    # A run that stopped as a duplicate did no research: the next upload points at the real one.
    first = make_ctx(session, PRIYA)
    _update_run(session, first.run_id, status=RunStatus.ready_for_review)
    second = make_ctx(session, PRIYA)
    _update_run(session, second.run_id, status=RunStatus.blocked)
    third = make_ctx(session, PRIYA)
    assert intake.find_recent_run(third).id == first.run_id


def test_a_stopped_duplicate_row_points_at_the_earlier_research(session, client):
    # The run stopped at intake; the stages after it are recorded as "not run", so the intake event
    # (which names the earlier run) is not the latest one.
    from app.models import StageEvent, StageState
    first = make_ctx(session, PRIYA)
    _update_run(session, first.run_id, status=RunStatus.ready_for_review)
    batch_id = client.post("/batches", files={"file": ("dup.csv", (HEADER + PRIYA + "\n").encode(), "text/csv")}).json()["batch_id"]
    [run_id] = runner.create_runs(session, session.get(Batch, batch_id))
    _update_run(session, run_id, status=RunStatus.blocked)
    session.add(StageEvent(run_id=run_id, stage="intake", state=StageState.done,
                           payload={"stopped": "blocked", "earlier_run_id": first.run_id}))
    session.add(StageEvent(run_id=run_id, stage="resolve", state=StageState.skipped))
    session.commit()
    [row] = client.get(f"/batches/{batch_id}").json()["prospects"]
    assert row["researched_before"]["run_id"] == first.run_id
