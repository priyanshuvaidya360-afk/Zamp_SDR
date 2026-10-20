"""T13: the cheap rules, cases 1, 2, 6 and 7, against the seed data (seed/context.json)."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest

from app import llm
from app.config import settings
from app.llm import Usage
from app.models import MessageType, Run, RunStatus, SequenceHistory, SequenceOutcome
from app.pipeline import draft, extract, intake, score
from app.pipeline.extract import ExtractedSignal, ExtractOutput
from app.pipeline.runner import StopRun
from app.pipeline.score import _Ratings
from app.schemas import HookChoice
from scripts.seed_context import seed
from tests.test_stages_1_3 import make_ctx

TODAY = date(2026, 10, 19)
PRIYA = "Priya,Raman,,Acme Fintech,VP Finance,acmefintech.com,,priya.raman@acmefintech.com"
TOM = "Tom,Becker,,Globex Retail,CFO,globexretail.com,,tom.becker@globexretail.com"
COLLEAGUE_OF_TOM = "Nina,Park,,Globex Retail,VP Sales,globexretail.com,,nina.park@globexretail.com"
CONTOSO = "Sam,Lee,,Contoso Logistics,Head of Growth,contosologistics.com,,"


# --- Case 1: warm prospect treated as cold ----------------------------------------------------


async def test_not_now_becomes_a_follow_up_with_the_reply_verbatim(session):
    seed(session)
    ctx = make_ctx(session, PRIYA)
    out = await intake.run(ctx)
    assert ctx.message_type == "follow_up" and "→ follow-up" in out.message
    session.expire_all()
    assert session.get(Run, ctx.run_id).message_type == MessageType.follow_up

    ctx.outputs["intake"] = out
    ctx.outputs["extract"] = ExtractOutput(message="", signals=[])
    ctx.outputs["score"] = score.ScoreOutput(message="")
    prompt = draft.build_prompt(ctx, "role_based")
    snippet = "Not a priority this quarter, we're in the middle of a CRM migration. Try me again after it goes live."
    assert "follow-up email" in prompt and f'"{snippet}"' in prompt


async def test_unsubscribed_is_suppressed(session):
    seed(session)
    with pytest.raises(StopRun) as stop:
        await intake.run(make_ctx(session, TOM))
    assert stop.value.status == RunStatus.suppressed
    assert stop.value.reason.startswith("Opted out: unsubscribed on")


async def test_not_interested_is_suppressed(session):
    seed(session)
    session.add(SequenceHistory(email="priya.raman@acmefintech.com", domain="acmefintech.com", sequence="Q3",
                                last_touch_at=datetime.now(timezone.utc) - timedelta(days=5),
                                outcome=SequenceOutcome.not_interested, reply_snippet="Please stop."))
    session.commit()
    with pytest.raises(StopRun) as stop:  # the newest reply wins over the older 'not now'
        await intake.run(make_ctx(session, PRIYA))
    assert stop.value.status == RunStatus.suppressed and "said not interested" in stop.value.reason


async def test_a_colleagues_opt_out_does_not_suppress_this_person(session):
    seed(session)
    ctx = make_ctx(session, COLLEAGUE_OF_TOM)
    out = await intake.run(ctx)  # no StopRun
    assert ctx.message_type == "cold" and "1 past touch(es) at this company" in out.message


# --- Case 2: live deal collision --------------------------------------------------------------


async def test_open_deal_holds_the_run_and_routes_it_to_the_owner(session):
    seed(session)
    with pytest.raises(StopRun) as stop:
        await intake.run(make_ctx(session, CONTOSO))
    assert stop.value.status == RunStatus.held
    assert stop.value.reason == "Open opportunity with Sam Patel, stage 4. Draft held and routed to Sam Patel."
    assert stop.value.payload == {"deal_owner": "Sam Patel", "deal_stage": 4, "routed_to": "Sam Patel"}


# --- Case 6: competitor signal disguised as a hook ----------------------------------------------


def comp_signal(summary: str, days: int | None, evidence: str | None = None) -> ExtractedSignal:
    return ExtractedSignal(type="other", about="company", summary=summary, evidence=evidence or summary,
                           source_url="https://news.example.com/x", sensitive=False,
                           published_at=TODAY - timedelta(days=days) if days is not None else None)


@pytest.fixture
def scoring(session, monkeypatch):
    monkeypatch.setattr(settings, "today", TODAY)

    def build(signals):
        ctx = make_ctx(session, PRIYA)
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


async def test_recent_competitor_purchase_is_held_with_a_renewal_reminder(scoring):
    ctx = scoring([comp_signal("Acme Fintech adopted Apollo for its SDR team", days=60)])
    with pytest.raises(StopRun) as stop:
        await score.run(ctx)
    assert stop.value.status == RunStatus.held
    adopted = TODAY - timedelta(days=60)
    reminder = adopted + timedelta(days=round(12 * 30.44) - 90)
    assert stop.value.reason == (f"Adopted Apollo on {adopted}, within 12 months: a new contract won't be replaced. "
                                 f"Held; renewal reminder {reminder}.")


async def test_established_user_with_a_migration_story_gets_a_displacement_angle(scoring):
    ctx = scoring([comp_signal("Acme Fintech uses Apollo for outbound", days=None),
                   comp_signal("Acme Fintech is hiring 4 SDRs", days=10)])
    out = await score.run(ctx)
    assert out.hook.summary == "Acme Fintech uses Apollo for outbound" and out.hook.excluded is None
    assert out.angle.startswith("Displacement: they use Apollo. Teams keep Apollo's database")


async def test_established_user_without_a_story_is_held(scoring):
    ctx = scoring([comp_signal("Acme Fintech uses Clay to enrich leads", days=None)])
    with pytest.raises(StopRun) as stop:
        await score.run(ctx)
    assert stop.value.status == RunStatus.held and "no migration story" in stop.value.reason


async def test_news_about_a_competitor_is_never_the_opener(scoring):
    ctx = scoring([comp_signal("Apollo raised a $100M Series D", days=5),  # mentions, not usage
                   comp_signal("Acme Fintech is hiring 4 SDRs", days=10)])
    out = await score.run(ctx)
    assert out.hook.summary == "Acme Fintech is hiring 4 SDRs"
    news = next(r for r in out.ranked if "Apollo" in r.summary)
    assert news.excluded == "Names a competitor (Apollo): never the opener"


# --- Case 7: borrowed words --------------------------------------------------------------------


def test_someone_elses_words_are_not_the_prospects():
    quote = ExtractedSignal(type="person_content", about="person", summary="We are drowning in invoices",
                            evidence="drowning in invoices", source_url="https://pod.example.com/42",
                            sensitive=False, speaker="Rohan Gupta")
    extract.apply_speaker_rules(quote, "Priya Raman", "Raman")
    assert quote.about == "company" and quote.note.startswith("Said by Rohan Gupta, not Priya Raman")

    reshare = ExtractedSignal(type="person_content", about="person", summary="Acme opened a Singapore office",
                              evidence="opened our Singapore office", source_url="https://feed.example.com/1",
                              sensitive=False, speaker="Arjun Mehta", is_reshare=True)
    extract.apply_speaker_rules(reshare, "Priya Raman", "Raman")
    assert reshare.about == "company" and reshare.note.startswith("Reshared by Priya Raman (originally by Arjun Mehta)")
