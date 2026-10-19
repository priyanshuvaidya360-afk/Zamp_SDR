"""T08: stage 5, scoring + hook choice, with the model's judgements mocked."""

import math
from datetime import date, timedelta

import pytest
from sqlmodel import select

from app import llm
from app.config import settings
from app.llm import Usage
from app.models import RunStatus, SignalRecord
from app.pipeline import extract, score
from app.pipeline.extract import ExtractedSignal, ExtractOutput
from app.pipeline.score import Adjustment, _Ratings
from app.schemas import HookChoice
from tests.extraction_case import TODAY, make_context


def signal(summary: str, type="funding", about="company", days=10, url="https://techcrunch.com/x", **extra):
    return ExtractedSignal(type=type, about=about, summary=summary, evidence=summary,
                           source_url=url, published_at=TODAY - timedelta(days=days) if days is not None else None,
                           sensitive=False, **extra)


@pytest.fixture
def setup(session, monkeypatch):
    """Returns build(signals, ratings, pick) -> ctx; records what the model was asked."""
    monkeypatch.setattr(settings, "today", TODAY)
    asked = {"rate": [], "hook": []}

    def build(signals, ratings: dict[str, tuple[int, int]], pick=None):
        ctx = make_context(session)
        extract._store(ctx, signals)
        ctx.outputs["extract"] = ExtractOutput(message="", signals=signals)
        by_summary = {s.summary: s.id for s in signals}

        async def structured(role, system, user, schema, providers=None):
            usage = Usage(provider="groq", model="m", role=role, prompt_tokens=50, completion_tokens=10)
            if schema is _Ratings:
                asked["rate"].append(user)
                return _Ratings(ratings=[
                    {"signal_id": by_summary[s], "relevance": r, "specificity": sp, "reasoning": "test"}
                    for s, (r, sp) in ratings.items()]), usage
            asked["hook"].append(user)
            ids = [int(line.split("id ")[1].split(" ")[0]) for line in user.splitlines() if line.startswith("- id ")]
            chosen = pick(ids, by_summary) if pick else ids[0]
            return HookChoice(signal_id=chosen, angle="It matters because...", runners_up=ids,
                              why_not_others="Less specific."), usage

        monkeypatch.setattr(llm, "structured", structured)
        return ctx

    build.asked = asked
    return build


def stored(session, ctx) -> dict[str, SignalRecord]:
    session.expire_all()
    return {r.summary: r for r in session.exec(select(SignalRecord).where(SignalRecord.run_id == ctx.run_id))}


# --- Done when ---------------------------------------------------------------------------------


async def test_expected_winner_and_everything_is_persisted(setup, session):
    ctx = setup(
        [signal("Priya spoke about her AP backlog", type="person_content", about="person", days=5,
                url="https://acmefintech.com/blog/priya"),
         signal("Acme raised a $40M Series C", days=30, coverage=3),
         signal("Acme won a design award", type="press", days=150, url="https://blog.example.com/award")],
        {"Priya spoke about her AP backlog": (5, 5), "Acme raised a $40M Series C": (4, 2),
         "Acme won a design award": (1, 1)},
    )
    out = await score.run(ctx)
    assert out.hook.summary == "Priya spoke about her AP backlog" and not out.low_signal
    assert [s.summary for s in out.ranked] == ["Priya spoke about her AP backlog", "Acme raised a $40M Series C",
                                               "Acme won a design award"]
    assert out.angle == "It matters because..." and out.message.startswith("Hook: person_content (score ")
    assert ctx.final_status == RunStatus.ready_for_review

    rows = stored(session, ctx)
    assert rows["Priya spoke about her AP backlog"].selected
    assert rows["Acme raised a $40M Series C"].rejected_reason == "Runner-up: Less specific."
    assert set(rows["Acme won a design award"].scores) >= {"relevance", "specificity", "recency",
                                                           "verifiability", "score", "weights"}


async def test_sensitive_signal_never_wins_and_is_never_offered(setup, session):
    ctx = setup(
        [signal("Acme laid off 20% of staff", type="negative_event", days=2),
         signal("Acme is hiring 4 AP clerks", type="hiring", days=20)],
        {"Acme laid off 20% of staff": (5, 4), "Acme is hiring 4 AP clerks": (3, 3)},
    )
    ctx.outputs["extract"].signals[0].sensitive = True
    out = await score.run(ctx)
    assert out.hook.summary == "Acme is hiring 4 AP clerks"
    layoffs = next(s for s in out.ranked if s.type == "negative_event")
    assert layoffs.score > out.hook.score  # it scored higher, and still can't be the hook
    assert layoffs.excluded.startswith("Sensitive")
    assert "laid off" not in setup.asked["hook"][0]  # the hook model never even sees it
    assert stored(session, ctx)["Acme laid off 20% of staff"].rejected_reason.startswith("Sensitive")


async def test_weak_set_ends_low_signal_without_asking_for_a_hook(setup, session):
    ctx = setup(
        [signal("Acme posted a generic blog", type="other", days=170, url="https://medium.example.com/a"),
         signal("Acme appears in a vendor list", type="press", days=None, url="https://list.example.com/b")],
        {"Acme posted a generic blog": (1, 1), "Acme appears in a vendor list": (1, 0)},
    )
    out = await score.run(ctx)
    assert out.low_signal and out.hook is None and setup.asked["hook"] == []
    assert ctx.final_status == RunStatus.low_signal and ctx.final_reason.startswith("Signal too weak")
    assert all(r.rejected_reason.startswith("Too weak") for r in stored(session, ctx).values())


# --- Code rules --------------------------------------------------------------------------------


def test_formula_and_verifiability(monkeypatch):
    monkeypatch.setattr(settings, "today", TODAY)
    assert score.recency_points(TODAY, TODAY) == (0, 5.0)
    days, points = score.recency_points(None, TODAY)
    assert days == 120 and points == pytest.approx(5 * math.exp(-2))

    own = signal("x", url="https://acmefintech.com/news")
    assert score.verifiability(own, "acmefintech.com") == 1.0
    assert score.verifiability(signal("x", url="https://www.reuters.com/a"), "acme.com") == 1.0
    assert score.verifiability(signal("x", url="https://smallblog.example/a"), "acme.com") == 0.5
    assert score.verifiability(signal("x", url="https://smallblog.example/a", coverage=2), "acme.com") == 1.0
    assert score.verifiability(signal("x", url="csv:public_bio"), "acme.com") == 0.0

    s = score.ScoredSignal(signal_id=1, type="funding", about="company", summary="", source_url="",
                           relevance=4, specificity=2, recency=5.0, verifiability=1.0)
    assert score.total(s) == round(0.35 * 4 + 0.25 * 2 + 0.25 * 5 + 0.15 * 5, 2)


async def test_company_level_specificity_is_capped_below_the_person(setup):
    ctx = setup([signal("Acme raised a $40M Series C")], {"Acme raised a $40M Series C": (4, 5)})
    out = await score.run(ctx)
    assert out.ranked[0].specificity == 4


async def test_model_pick_outside_the_candidates_is_overridden(setup):
    ctx = setup(
        [signal("Acme raised a $40M Series C", coverage=3), signal("Acme is hiring 4 AP clerks", type="hiring")],
        {"Acme raised a $40M Series C": (5, 3), "Acme is hiring 4 AP clerks": (3, 3)},
        pick=lambda ids, by: 9999,
    )
    out = await score.run(ctx)
    assert out.hook.summary == "Acme raised a $40M Series C" and "overridden" in out.message


async def test_model_saying_nothing_links_ends_low_signal(setup, session):
    ctx = setup([signal("Acme raised a $40M Series C", coverage=3)], {"Acme raised a $40M Series C": (4, 3)},
                pick=lambda ids, by: None)
    out = await score.run(ctx)
    assert out.low_signal and ctx.final_status == RunStatus.low_signal
    reason = stored(session, ctx)["Acme raised a $40M Series C"].rejected_reason
    assert reason.startswith("Hook review: no link") and "Too weak" not in reason


async def test_no_usable_signals_skips_the_model(setup):
    trap = signal("Filters AI-written outreach", type="ai_screening", url="csv:public_bio")
    ctx = setup([trap], {})
    out = await score.run(ctx)
    assert out.low_signal and setup.asked["rate"] == [] and ctx.final_reason.startswith("No usable signal")


async def test_score_adjusters_hook_point(setup, monkeypatch):
    def crowded(scored, original, ctx):
        if scored.type == "funding":
            return Adjustment(name="crowded", delta=-1.5, reason="Covered by 9 outlets")
        return None

    monkeypatch.setattr(score, "SCORE_ADJUSTERS", [crowded])
    ctx = setup(
        [signal("Acme raised a $40M Series C", coverage=9), signal("Acme is hiring 4 AP clerks", type="hiring")],
        {"Acme raised a $40M Series C": (4, 3), "Acme is hiring 4 AP clerks": (4, 3)},
    )
    out = await score.run(ctx)
    assert out.hook.type == "hiring"
    raise_ = next(s for s in out.ranked if s.type == "funding")
    assert raise_.adjustments[0].delta == -1.5
