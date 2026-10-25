"""T15: review queue actions, revalidate on approve (case 10), export, the dev age-draft route."""

import csv
import io
import re
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app import llm, review, search
from app.llm import Usage
from app.config import settings
from app.models import Batch, DraftRecord, ReviewerAction, Run, RunStatus, SignalRecord, utcnow
from app.pipeline import runner
from app.pipeline.score import _Ratings
from app.schemas import HookChoice
from app.search import ExtractResponse
from tests import stale_case
from tests.extraction_case import TODAY

USAGE = Usage(provider="groq", model="m", role="smart")


@pytest.fixture
def stale_run(session, monkeypatch):
    """The recorded Kestrel Labs run (stages 4-7 replayed), ready for review."""
    monkeypatch.setattr(settings, "demo_mode", "fixtures")
    monkeypatch.setattr(settings, "today", TODAY)
    monkeypatch.setattr(settings, "fixtures_dir", stale_case.FIXTURES)
    monkeypatch.setattr(runner, "get_stages", stale_case.pipeline_stages)

    async def make() -> int:
        [run_id] = stale_case.make_batch(session)
        await runner.run_one(run_id)
        session.expire_all()
        assert session.get(Run, run_id).status == RunStatus.ready_for_review
        return run_id

    return make


def age(session: Session, run_id: int, days: int = 12) -> DraftRecord:
    draft = review.latest_draft(session, run_id)
    draft.created_at = utcnow() - timedelta(days=days)
    session.add(draft)
    session.commit()
    return draft


def export_rows(session: Session, run_id: int, everything: bool = False) -> list[dict]:
    batch = session.get(Batch, session.get(Run, run_id).batch_id)
    return list(csv.DictReader(io.StringIO(review.export_csv(session, batch, everything))))


# --- Case 10, recorded (T15 "Done when") -------------------------------------------------------


async def test_aged_draft_shows_the_diff_and_exports_the_corrected_text(session, stale_run):
    run_id = await stale_run()
    old = age(session, run_id)
    assert "four" in old.body
    assert review.recheck_summary(session, session.get(Run, run_id))["facts"] == 1

    first = await review.review(session, run_id, ReviewerAction.approve)
    assert first.outcome == "changed" and first.run_status == RunStatus.ready_for_review
    [check] = first.checks
    assert check.verdict == "changed" and "four" in check.sentence_before and "two" in check.sentence_after
    assert first.diff["before"] == old.body and "two Sales Development" in first.diff["after"]
    assert export_rows(session, run_id) == []  # nothing approved yet

    with pytest.raises(review.ReviewError, match="newer version"):  # the stale version can't be approved
        await review.review(session, run_id, ReviewerAction.approve, draft_id=old.id)
    second = await review.review(session, run_id, ReviewerAction.approve, draft_id=first.draft_id)
    assert second.outcome == "approved" and second.checks == []  # the new version is fresh

    [row] = export_rows(session, run_id)
    assert row["status"] == "approved" and row["source_url"] == stale_case.CAREERS
    assert "hiring two Sales Development Representatives" in row["body"] and "four" not in row["body"]
    assert row["first_name"] == "Omar" and row["email"] == "omar@kestrellabs.example"  # original columns kept
    signal = session.get(SignalRecord, check.signal_id)
    # The model may write the new count as a word or a digit; either states the current fact.
    assert re.search(r"\b(two|2)\b", signal.summary) and signal.note.startswith("Updated on re-check")


async def test_a_fresh_draft_is_approved_without_a_re_check(session, stale_run):
    run_id = await stale_run()
    assert review.recheck_summary(session, session.get(Run, run_id))["facts"] == 0
    result = await review.review(session, run_id, ReviewerAction.approve)  # network is off: no fetch happened
    assert result.outcome == "approved" and result.checks == []


async def test_a_company_level_draft_needs_the_rep_to_vouch_for_the_person(session, stale_run):
    run_id = await stale_run()
    run = session.get(Run, run_id)
    run.identity_unverified = True  # resolve never found them; the rep asked for a company-level draft
    session.add(run)
    session.commit()
    for action, body in ((ReviewerAction.approve, None), (ReviewerAction.edit, "Hi Omar, edited.")):
        with pytest.raises(review.ReviewError, match="never found online"):
            await review.review(session, run_id, action, body=body)
    result = await review.review(session, run_id, ReviewerAction.approve, identity_confirmed=True)
    assert result.outcome == "approved"
    assert review.latest_draft(session, run_id).review["identity_confirmed_by_rep"] is True


async def test_a_company_level_draft_can_be_rejected_without_vouching(session, stale_run):
    run_id = await stale_run()
    run = session.get(Run, run_id)
    run.identity_unverified = True
    session.add(run)
    session.commit()
    assert (await review.review(session, run_id, ReviewerAction.reject)).outcome == "rejected"


async def test_a_gone_fact_sends_the_run_back_with_the_runner_up(session, stale_run, monkeypatch):
    run_id = await stale_run()
    age(session, run_id)

    async def page_gone(urls, fresh=False):
        return ExtractResponse(provider="tavily", pages={}, failed=urls)

    spawned = []
    monkeypatch.setattr(search, "extract", page_gone)
    monkeypatch.setattr(runner, "spawn", lambda coro: (spawned.append(coro), coro.close()))
    result = await review.review(session, run_id, ReviewerAction.approve)
    assert result.outcome == "gone" and result.run_status == RunStatus.running
    assert result.checks[0].reason == "The page could not be read any more."
    assert len(spawned) == 1 and session.get(Run, run_id).status == RunStatus.running


async def test_the_re_run_scores_without_the_gone_source(session, stale_run, monkeypatch):
    run_id = await stale_run()

    async def structured(role, system, user, schema, providers=None):  # the re-run's new prompts
        ids = [int(i) for i in re.findall(r"^- id (\d+)", user, re.M)]
        if schema is _Ratings:
            return _Ratings(ratings=[{"signal_id": i, "relevance": 4, "specificity": 3, "reasoning": "t"} for i in ids]), USAGE
        return HookChoice(signal_id=ids[0], angle="a", runners_up=[], why_not_others="w"), USAGE

    monkeypatch.setattr(llm, "structured", structured)
    monkeypatch.setattr(runner, "get_stages", lambda: stale_case.pipeline_stages()[:3])  # through score
    await runner.run_one(run_id, start_stage="score", resume_input={
        "exclude_sources": [stale_case.CAREERS], "exclude_reason": "Gone on re-check: the source no longer states this"})
    session.expire_all()
    signals = session.exec(select(SignalRecord).where(SignalRecord.run_id == run_id)).all()
    careers = next(s for s in signals if s.source_url == stale_case.CAREERS)
    assert not careers.selected and careers.rejected_reason.startswith("Gone on re-check")
    assert next(s for s in signals if s.selected).type == "expansion"  # the runner-up opens now


# --- Edit, reject, export ---------------------------------------------------------------------


async def test_edit_approves_the_reps_text_and_keeps_the_diff(session, stale_run):
    run_id = await stale_run()
    draft = review.latest_draft(session, run_id)
    edited = draft.body.replace("Would you be open", "Open")
    result = await review.review(session, run_id, ReviewerAction.edit, body=edited)
    assert result.outcome == "edited" and result.run_status == RunStatus.approved
    session.refresh(draft)
    assert draft.edited_body == edited and draft.reviewer_action == ReviewerAction.edit
    assert any(line.startswith("+") and "Open" in line for line in draft.review["edit_diff"])
    assert export_rows(session, run_id)[0]["body"] == edited


async def test_reject_keeps_the_reason_and_review_needs_a_waiting_draft(session, stale_run):
    run_id = await stale_run()
    result = await review.review(session, run_id, ReviewerAction.reject, reason="Wrong persona")
    assert result.outcome == "rejected"
    run = session.get(Run, run_id)
    assert run.status == RunStatus.rejected and run.final_reason == "Rejected by reviewer: Wrong persona"
    with pytest.raises(review.ReviewError, match="waiting for review"):
        await review.review(session, run_id, ReviewerAction.approve)
    assert export_rows(session, run_id) == []
    assert [r["status"] for r in export_rows(session, run_id, everything=True)] == ["rejected"]


async def test_export_all_lists_rows_that_never_ran(session):
    from app import ingest
    from app.routes.batches import _store

    batch = Batch(filename="x.csv")
    _store(batch, ingest.parse_upload(b"first_name,last_name,company_domain\nAna,Diaz,ana.example\nBo,,\n"))
    session.add(batch)
    session.commit()
    rows = list(csv.DictReader(io.StringIO(review.export_csv(session, batch, everything=True))))
    assert [(r["first_name"], r["status"]) for r in rows] == [("Ana", "skipped"), ("Bo", "invalid")]  # no runs started
    assert list(rows[0]) == ["first_name", "last_name", "company_domain", "status", "hook", "source_url", "subject", "body"]


# --- HTTP -------------------------------------------------------------------------------------


def test_review_route_validates(client):
    assert client.post("/runs/999/review", json={"action": "approve"}).status_code == 404
    assert client.post("/runs/1/review", json={"action": "ship"}).status_code == 422


def test_age_draft_is_dev_only(client, session, monkeypatch):
    run = Run(batch_id=1, row_number=2, first_name="A", last_name="B", status=RunStatus.ready_for_review)
    session.add(Batch(id=1, filename="x.csv"))
    session.add(run)
    session.commit()
    draft = DraftRecord(run_id=run.id, subject="s", body="b")
    session.add(draft)
    session.commit()
    monkeypatch.setattr(settings, "demo_mode", "live")
    assert client.post(f"/dev/age-draft/{draft.id}?days=12").status_code == 404
    monkeypatch.setattr(settings, "demo_mode", "fixtures")
    assert client.post(f"/dev/age-draft/{draft.id}?days=12").json()["aged_days"] == 12
    session.refresh(draft)
    assert 11 <= (utcnow() - review._aware(draft.created_at)).days <= 12
    detail = client.get(f"/runs/{run.id}").json()
    assert detail["recheck"] == {"facts": 0, "draft_age_days": 12}
    csv_response = client.get("/batches/1/export.csv?all=true")
    assert csv_response.status_code == 200 and csv_response.headers["content-type"].startswith("text/csv")



def test_a_rewrite_may_keep_the_sentences_own_date_but_not_the_old_count():
    before = "I saw that Kestrel Labs is hiring four SDRs in London (2 October 2026)."
    old, new = "Kestrel Labs is hiring four SDRs in London", "Kestrel Labs is hiring two SDRs in London"
    good = "I saw that Kestrel Labs is hiring two SDRs in London (2 October 2026)."
    assert review.invented_numbers(before, good, old, new) == set()
    kept_old = "I saw that Kestrel Labs is hiring four SDRs in London (2 October 2026)."
    assert review.invented_numbers(before, kept_old, old, new) == {"4"}
    added = "I saw that Kestrel Labs is hiring two SDRs in London, up 50% (2 October 2026)."
    assert review.invented_numbers(before, added, old, new) == {"50"}
