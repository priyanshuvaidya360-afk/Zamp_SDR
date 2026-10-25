"""T14: the crowded hook (case 5). Rules in code, plus the recorded crowded-company case."""

import re
from datetime import date, timedelta

import pytest
from sqlmodel import Session, select

from app import db, llm
from app.config import settings
from app.llm import Usage
from app.models import DraftRecord, Run, RunStatus, SignalRecord
from app.pipeline import account, crowded, draft, extract, research, runner, score
from app.pipeline.extract import ExtractedSignal, ExtractOutput
from app.pipeline.score import _Ratings
from app.schemas import ClaimRef, Draft, HookChoice
from tests import crowded_case
from tests.test_stages_1_3 import make_ctx

TODAY = date(2026, 10, 19)
ROW = "Dana,Okafor,,Lumen Analytics,Head of Growth,lumenanalytics.example,,"


def raise_signal(days: int = 9, coverage: int = 9, type_: str = "funding") -> ExtractedSignal:
    return ExtractedSignal(type=type_, about="company", summary="Lumen Analytics raised a $40M Series B",
                           evidence="raised a $40M Series B", source_url="https://techcrunch.example.com/a",
                           sensitive=False, published_at=TODAY - timedelta(days=days), coverage=coverage,
                           category="funding")


def hiring_signal() -> ExtractedSignal:
    return ExtractedSignal(type="hiring", about="company", summary="Lumen Analytics is hiring six SDRs in Austin",
                           evidence="hiring six Sales Development Representatives",
                           source_url="https://lumenanalytics.example/careers", sensitive=False,
                           published_at=TODAY - timedelta(days=40))


# --- Crowdedness, in code ----------------------------------------------------------------------


def test_outlets_in_the_same_search_count_towards_coverage():
    signal = raise_signal(coverage=1)
    signal.published_at = date(2026, 10, 10)
    crowded.add_search_coverage([signal], crowded_case.gather_output().queries)
    assert signal.coverage == 9  # two read + seven listed; the September story is another event
    assert crowded_case.UNRELATED[0] not in signal.cluster_urls


def test_a_raise_read_on_the_own_site_still_counts_the_press_reports():
    # The newsroom page (own-site search) found the raise; the funding search found the press.
    signal = raise_signal(coverage=1)
    signal.published_at = date(2026, 10, 10)
    signal.category = "company_site"
    crowded.add_search_coverage([signal], crowded_case.gather_output().queries)
    assert signal.coverage == 9


@pytest.mark.parametrize("days, coverage, type_, expected", [
    (9, 9, "funding", True),
    (29, 5, "product_launch", True),
    (9, 9, "acquisition", True),
    (30, 9, "funding", False),  # a month old: the crowd has moved on
    (9, 4, "funding", False),   # covered, but not crowded
    (9, 9, "hiring", False),    # hiring is not a press event
])
def test_crowded_needs_a_big_young_widely_covered_event(days, coverage, type_, expected):
    assert crowded.is_crowded(type_, TODAY - timedelta(days=days), coverage, TODAY) is expected


def test_first_sentence_references_the_event():
    event = raise_signal()
    assert crowded.references_event("Congrats on the $40M round.", event)
    assert crowded.references_event("Saw the Series B news.", event)
    assert not crowded.references_event("You're hiring six SDRs in Austin.", event)
    assert not crowded.references_event("You're rounding out the SDR team.", event)


@pytest.fixture
def scoring(session, monkeypatch):
    monkeypatch.setattr(settings, "today", TODAY)

    def build(signals):
        ctx = make_ctx(session, ROW)
        extract._store(ctx, signals)
        ctx.outputs["extract"] = ExtractOutput(message="", signals=signals)

        async def structured(role, system, user, schema, providers=None):
            usage = Usage(provider="groq", model="m", role=role)
            ids = [int(i) for i in re.findall(r"^- id (\d+)", user, re.M)]
            if schema is _Ratings:
                return _Ratings(ratings=[{"signal_id": i, "relevance": 4, "specificity": 3, "reasoning": "t"} for i in ids]), usage
            return HookChoice(signal_id=ids[0], angle="a", runners_up=[], why_not_others="w"), usage

        monkeypatch.setattr(llm, "structured", structured)
        return ctx

    return build


async def test_crowded_event_is_penalised_and_never_opens(scoring):
    ctx = scoring([raise_signal(), hiring_signal()])
    out = await score.run(ctx)
    assert out.hook.type == "hiring"
    funding = next(r for r in out.ranked if r.type == "funding")
    assert funding.crowded and funding.coverage == 9
    assert [a.model_dump() for a in funding.adjustments] == [
        {"name": "crowded", "delta": -1.5, "reason": "Crowded: covered by 9 outlets in 9 days"}]
    assert funding.rank_before_penalty == 1  # it would have been the hook
    with Session(db.engine) as session:
        record = session.get(SignalRecord, funding.signal_id)
    assert record.scores["crowded"] and record.scores["coverage"] == 9 and record.scores["penalty"] == 1.5
    assert record.rejected_reason.startswith("Crowded: covered by 9 outlets in 9 days")


async def test_only_a_crowded_event_means_low_signal(scoring):
    out = await score.run(scoring([raise_signal()]))
    assert out.low_signal and out.hook is None
    assert out.message.startswith("Only a crowded event found")


async def test_a_less_covered_raise_can_still_open(scoring):
    out = await score.run(scoring([raise_signal(coverage=3)]))
    assert out.hook.type == "funding" and not out.hook.crowded and out.hook.adjustments == []


def test_critic_rejects_a_crowded_first_sentence():
    event, hook = raise_signal(), hiring_signal()
    event.id, hook.id = 1, 2
    signals = {1: event, 2: hook}

    def check(body: str, claims: list[ClaimRef]) -> bool:
        checks = draft.code_checks(Draft(subject="sdrs in austin", body=body, claims=claims), "hooked",
                                   signals, set(), "Okafor", [], crowded_ids={1})
        return next(c for c in checks if c.name == "crowded_not_opener").passed

    second = "You're hiring six SDRs in Austin. With the $40M Series B behind it, outbound is the next bet."
    assert check(f"Hi Dana,\n{second}", [ClaimRef(sentence="You're hiring six SDRs in Austin.", signal_id=2),
                                         ClaimRef(sentence="With the $40M Series B behind it, outbound is the next bet.", signal_id=1)])
    first = "Congrats on the $40M Series B. You're hiring six SDRs in Austin."
    assert not check(f"Hi Dana,\n{first}", [ClaimRef(sentence="Congrats on the $40M Series B.", signal_id=1),
                                            ClaimRef(sentence="You're hiring six SDRs in Austin.", signal_id=2)])


def test_account_plan_never_assigns_a_crowded_opener():
    pooled = [account.PooledSignal(pid=1, summary="raise", type="funding", about="company", source_url="u1",
                                   score=4.5, context_only=True),
              account.PooledSignal(pid=2, summary="hiring", type="hiring", about="company", source_url="u2", score=4.0)]
    members = [account.Member(run_id=1, name="Dana Okafor", ranked=[], signals=[])]
    plan = account._Plan(picks=[account._Pick(member=1, opener=1, angle="")], reasoning="")
    assert any("crowded news" in p for p in account.violations(plan, members, pooled))
    assert account.fallback(members, pooled).picks[0].opener == 2


# --- The recorded crowded company (T14 "Done when") -------------------------------------------


async def test_crowded_company_hooks_on_what_followed(session, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", "fixtures")
    monkeypatch.setattr(settings, "today", TODAY)
    monkeypatch.setattr(settings, "fixtures_dir", crowded_case.FIXTURES)
    monkeypatch.setattr(runner, "get_stages", crowded_case.pipeline_stages)
    monkeypatch.setattr(research, "second_order", crowded_case.fake_second_order)
    crowded_case.calls.clear()

    [run_id] = crowded_case.make_batch(session)
    await runner.run_one(run_id)

    session.expire_all()
    assert session.get(Run, run_id).status == RunStatus.ready_for_review
    assert crowded_case.calls == [date(2026, 10, 10)]  # one follow-up search, after the raise
    signals = session.exec(select(SignalRecord).where(SignalRecord.run_id == run_id)).all()
    funding = next(s for s in signals if s.type == "funding")
    hook = next(s for s in signals if s.selected)
    assert hook.type != "funding" and hook.source_url == crowded_case.FOLLOW_UP[1]
    assert funding.coverage == 9 and funding.scores["crowded"] and funding.scores["penalty"] == 1.5

    final = session.exec(select(DraftRecord).where(DraftRecord.run_id == run_id)
                         .order_by(DraftRecord.version.desc())).first()
    first = draft._first_sentence(final.body)
    assert not crowded.references_event(first, ExtractedSignal(**funding.model_dump(
        include={"type", "about", "summary", "evidence", "source_url", "sensitive"})))
    funding_claims = [c for c in final.claims if c["signal_id"] == funding.id]
    assert all(not draft._in_body(c["sentence"], first) for c in funding_claims)  # sentence two at most
