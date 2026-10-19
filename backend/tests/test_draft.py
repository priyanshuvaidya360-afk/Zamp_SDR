"""T09: stages 6-7, draft + critic.

Part 1: Priya's CSV row runs through stages 4-7 on the real models' recorded answers (re-record
with `python -m scripts.record_pipeline_case`) and ends with a stored ready_for_review draft.
Part 2: the critic's code checks and the rewrite flow, with the model mocked.
"""

from datetime import date

import pytest
from sqlmodel import Session, select

from app import llm
from app.config import settings
from app.llm import Usage
from app.models import DraftRecord, Run, RunStatus, SignalRecord
from app.pipeline import draft, runner
from app.pipeline.draft import code_checks
from app.pipeline.extract import ExtractedSignal
from app.schemas import Draft
from tests.extraction_case import TODAY, make_batch, pipeline_stages, variant_dir

# --- Part 1: from CSV row to a stored draft (recorded models) -------------------------------


@pytest.fixture
def replay(session, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", "fixtures")
    monkeypatch.setattr(settings, "today", TODAY)
    monkeypatch.setattr(runner, "get_stages", pipeline_stages)

    async def run(with_bio: bool):
        monkeypatch.setattr(settings, "fixtures_dir", variant_dir(with_bio))
        [run_id] = make_batch(session, with_bio)
        await runner.run_one(run_id)
        session.expire_all()
        drafts = session.exec(select(DraftRecord).where(DraftRecord.run_id == run_id).order_by(DraftRecord.version)).all()
        return session.get(Run, run_id), drafts

    return run


async def test_csv_row_to_a_stored_ready_for_review_draft(replay, session):
    run, drafts = await replay(with_bio=False)
    assert run.status == RunStatus.ready_for_review and 0.5 <= run.confidence <= 0.8
    final = drafts[-1]
    assert final.critic_report["final"] and final.critic_report["passed"]
    assert final.critic_report["mode"] == "hooked" and final.critic_report["banner"] is None
    assert final.hook_signal_id is not None and final.claims
    assert len(final.body.split()) < settings.draft_max_words
    assert "Priya" not in final.body  # the prospect is "you", never named in the third person
    hook = session.get(SignalRecord, final.hook_signal_id)
    if hook.speaker:  # whichever hook this recording picked, borrowed words name their author
        assert hook.speaker.split()[0] in final.body


async def test_a_rewrite_happens_only_when_the_first_draft_failed(replay):
    _, drafts = await replay(with_bio=False)
    assert [d.version for d in drafts] in ([1], [1, 2])
    if len(drafts) == 2:  # depends on the recording; the mocked tests below pin the flow down
        assert drafts[0].critic_report["problems"] and drafts[1].critic_report["final"]
    else:
        assert drafts[0].critic_report["problems"] == []


async def test_screening_trap_gets_an_outline_with_the_banner(replay):
    run, drafts = await replay(with_bio=True)
    assert run.status == RunStatus.ready_for_review
    final = drafts[-1]
    assert final.critic_report["mode"] == "outline"
    assert final.critic_report["warnings"][0] == draft.SCREENING_BANNER
    assert [line[:2] for line in final.body.splitlines() if line.strip()][:3] == ["- ", "- ", "- "]
    assert "flan" not in final.body.lower() and "recipe" not in final.body.lower()
    assert run.confidence < 0.4  # identity halved by the trap, then x hook strength


# --- Part 2: critic code checks ---------------------------------------------------------------

SIGNALS = {
    1: ExtractedSignal(id=1, type="product_launch", about="company", sensitive=False,
                       summary="Abacum launched Modeler on 13 October 2026",
                       evidence="Abacum launches Modeler, an AI agent", source_url="https://abacum.ai/news",
                       published_at=date(2026, 10, 13)),
    2: ExtractedSignal(id=2, type="hiring", about="company", sensitive=False,
                       summary="Acme is hiring 4 SDRs", evidence="4 open SDR roles",
                       source_url="https://acme.com/careers"),
    3: ExtractedSignal(id=3, type="negative_event", about="company", sensitive=True,
                       summary="Acme laid off 20% of staff", evidence="laid off 20%", source_url="https://n.com/a"),
    4: ExtractedSignal(id=4, type="press", about="company", sensitive=False, speaker="Rohan Gupta",
                       summary="The CFO said AP volume tripled", evidence="volume tripled",
                       source_url="https://pod.example.com/42", note="Said by Rohan Gupta, not Amy Asali."),
}
GOOD_BODY = ("Hi Amy,\nAbacum launched Modeler on 13 October. New products need pipeline from scratch, and "
             "hiring SDRs for it is slow. Open to a 15-minute call?\nShivam")
GOOD_CLAIM = {"sentence": "Abacum launched Modeler on 13 October.", "signal_id": 1}


def check(body=GOOD_BODY, claims=(GOOD_CLAIM,), subject="modeler and pipeline", mode="hooked") -> dict[str, str]:
    d = Draft(subject=subject, body=body, claims=list(claims))
    results = code_checks(d, mode, SIGNALS, excluded={3}, prospect_last_name="Asali",
                          banned=["I hope this finds you well", "quick question"])
    return {c.name: c.detail for c in results if not c.passed}


def test_a_good_draft_passes_every_check():
    assert check() == {}


@pytest.mark.parametrize(("body", "failed"), [
    (GOOD_BODY.replace("Open to", "I hope this finds you well. Open to"), "banned_phrases"),
    ("Hi Amy,\nCongrats on the Modeler launch! " + GOOD_BODY.split("\n", 1)[1], "no_flattery_opener"),
    (GOOD_BODY.replace("on 13 October", "last week"), "absolute_dates"),
    (GOOD_BODY + " 🚀", "no_emoji"),
    (GOOD_BODY + " Teams like yours book 3x more meetings.", "numbers_sourced"),
    (GOOD_BODY + " word" * 120, "length"),
])
def test_each_rule_fails_on_its_own(body, failed):
    assert failed in check(body=body)


def test_claims_must_be_known_safe_and_in_the_email():
    assert "claims_known" in check(claims=[{"sentence": GOOD_CLAIM["sentence"], "signal_id": 99}])
    assert "claims_known" in check(claims=[])  # a hooked draft must cite something
    assert "claims_safe" in check(claims=[GOOD_CLAIM, {"sentence": "Abacum launched Modeler", "signal_id": 3}])
    assert "claims_in_email" in check(claims=[{"sentence": "Acme raised a Series D from Tiger.", "signal_id": 1}])


def test_someone_elses_words_must_name_them():
    body = "Hi Amy,\nYour AP volume tripled this year. Open to a 15-minute call?\nShivam"
    failed = check(body=body, claims=[{"sentence": "Your AP volume tripled this year.", "signal_id": 4}])
    assert "Rohan Gupta's words without naming them" in failed["attribution"]

    named = "Hi Amy,\nRohan mentioned AP volume tripled. Open to a 15-minute call?\nShivam"
    assert "attribution" not in check(body=named, claims=[{"sentence": "Rohan mentioned AP volume tripled.", "signal_id": 4}])


def test_numbers_from_signals_and_the_ask_are_fine():
    body = "Hi Amy,\nAcme is hiring 4 SDRs. Open to a 15‑minute call?\nShivam"  # non-breaking hyphen
    assert check(body=body, claims=[{"sentence": "Acme is hiring 4 SDRs.", "signal_id": 2}]) == {}


def test_role_based_draft_may_not_claim_facts():
    assert check(mode="role_based", claims=[]) == {}
    assert "no_claims" in check(mode="role_based")


# --- Part 2: rewrite flow (mocked models) -------------------------------------------------------


@pytest.fixture
def ctx(session):
    from app.pipeline.extract import ExtractOutput
    from app.pipeline.score import ScoredSignal, ScoreOutput

    [run_id] = make_batch(session, with_bio=False)
    c = runner._load_context(run_id, None)
    c.confidence = 0.9
    c.outputs["extract"] = ExtractOutput(message="", signals=list(SIGNALS.values()))
    hook = ScoredSignal(signal_id=1, type="product_launch", about="company", summary=SIGNALS[1].summary,
                        source_url=SIGNALS[1].source_url, score=4.0)
    c.outputs["score"] = ScoreOutput(message="", hook=hook, angle="New product, new pipeline.", ranked=[
        hook, ScoredSignal(signal_id=3, type="negative_event", about="company", summary="", source_url="",
                           excluded="Sensitive")])
    return c


def fake_models(monkeypatch, drafts: list[dict], generic: list[int]):
    prompts = []

    async def structured(role, system, user, schema, providers=None):
        usage = Usage(provider="groq", model="m", role=role, prompt_tokens=10, completion_tokens=5)
        if schema is Draft:
            prompts.append(user)
            return Draft(**drafts.pop(0)), usage
        return draft._Genericness(genericness=generic.pop(0), reason="test"), usage

    monkeypatch.setattr(llm, "structured", structured)
    return prompts


GOOD = {"subject": "modeler and pipeline", "body": GOOD_BODY, "claims": [GOOD_CLAIM]}


async def test_failing_draft_is_rewritten_once_with_the_critics_notes(ctx, monkeypatch, session):
    bad = {**GOOD, "body": GOOD_BODY.replace("on 13 October", "last week")}
    prompts = fake_models(monkeypatch, [bad, GOOD], generic=[1, 1])
    ctx.outputs["draft"] = await draft.run_draft(ctx)
    out = await draft.run_critic(ctx)
    assert out.rewritten and out.passed and out.version == 2 and out.warnings == []
    assert "absolute_dates: relative date: last week" in prompts[1]
    assert ctx.confidence == round(0.9 * 0.8, 2)


async def test_second_failure_still_goes_to_review_with_warnings(ctx, monkeypatch, session):
    prompts = fake_models(monkeypatch, [GOOD, GOOD], generic=[4, 4])  # too generic, twice
    ctx.outputs["draft"] = await draft.run_draft(ctx)
    out = await draft.run_critic(ctx)
    assert out.rewritten and not out.passed and out.warnings[0].startswith("too generic (4/5)")
    assert ctx.final_status == RunStatus.ready_for_review and "warning" in ctx.final_reason
    stored = session.exec(select(DraftRecord).where(DraftRecord.run_id == ctx.run_id, DraftRecord.version == 2)).one()
    assert stored.critic_report["warnings"] and not stored.critic_report["passed"]


async def test_low_signal_gets_a_labelled_role_based_draft(ctx, monkeypatch):
    ctx.outputs["score"].hook = None
    ctx.final_status = RunStatus.low_signal
    prompts = fake_models(monkeypatch, [{**GOOD, "claims": [GOOD_CLAIM]}], generic=[])
    drafted = await draft.run_draft(ctx)
    assert drafted.mode == "role_based" and drafted.claims == [] and drafted.banner == draft.ROLE_BASED_BANNER
    assert "no specific recent signal" in prompts[0] and "Modeler" not in prompts[0]
    ctx.outputs["draft"] = drafted
    out = await draft.run_critic(ctx)
    assert out.genericness is None and out.warnings[0] == draft.ROLE_BASED_BANNER
    assert ctx.final_status == RunStatus.low_signal


async def test_follow_up_quotes_the_earlier_reply_and_never_offers_sensitive_facts(ctx, monkeypatch, session):
    from app.pipeline.intake import IntakeOutput
    from app.context_adapter import Touch

    ctx.message_type = "follow_up"
    ctx.outputs["intake"] = IntakeOutput(message="", reply_outcomes=[Touch(
        email="x", domain="d", sequence="s", last_touch_at="2026-06-15T00:00:00", outcome="not_now",
        reply_snippet="Not this quarter, try me after the ERP migration.", same_person=True)])
    prompts = fake_models(monkeypatch, [GOOD], generic=[])
    await draft.run_draft(ctx)
    assert "follow-up email" in prompts[0] and "Not this quarter, try me after the ERP migration." in prompts[0]
    assert "laid off" not in prompts[0]
