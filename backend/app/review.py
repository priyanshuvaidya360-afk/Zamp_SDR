"""Stage 8: human review, revalidate on approve (case 10), export.

Spec: docs/GUIDE.md case 10, "UI: three screens" (review queue, export) and T15.
- approve: if the draft is older than settings.revalidate_after_days and a claim relies on a
  time-sensitive signal, re-fetch those sources first. The fast model compares then vs now:
  unchanged -> approved; changed -> the smart model rewrites only that sentence, which becomes a
  new draft version to approve again (with a diff); gone -> the hook is invalid: the run goes
  back through scoring without that source, so the runner-up becomes the hook.
- edit: the rep's text is final (approved as edited); the diff is stored.
- reject: with an optional reason.
Nothing is ever sent.
"""

import csv
import difflib
import io
import re
from datetime import timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app import llm, prompts, search
from app.config import settings
from app.models import Batch, DraftRecord, ReviewerAction, Run, RunStatus, SignalRecord, utcnow
from app.pipeline.draft import _numbers_in, _words
from app.pipeline.extract import evidence_on_page

Verdict = Literal["unchanged", "changed", "gone"]
REVIEWABLE = {RunStatus.ready_for_review, RunStatus.low_signal}


class ReviewError(Exception):
    """A request the current state doesn't allow (the route turns it into a 409)."""


class FactCheck(BaseModel):
    signal_id: int
    source_url: str
    verdict: Verdict
    reason: str = ""
    old_fact: str
    new_fact: str | None = None
    sentence_before: str | None = None
    sentence_after: str | None = None


class ReviewResult(BaseModel):
    outcome: Literal["approved", "edited", "rejected", "changed", "gone"]
    message: str
    run_status: RunStatus
    draft_id: int  # the draft to show next (a new version when facts changed)
    checks: list[FactCheck] = []
    diff: dict[str, str] | None = None  # {"before", "after"} bodies, when a sentence was rewritten


# --- Which drafts need a re-check -------------------------------------------------------------


def latest_draft(session: Session, run_id: int) -> DraftRecord | None:
    return session.exec(select(DraftRecord).where(DraftRecord.run_id == run_id)
                        .order_by(DraftRecord.version.desc())).first()


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes


def stale_claims(session: Session, draft: DraftRecord) -> list[tuple[dict[str, Any], SignalRecord]]:
    """Claims that rely on a time-sensitive signal, if the draft is old enough to re-check."""
    if utcnow() - _aware(draft.created_at) <= timedelta(days=settings.revalidate_after_days):
        return []
    out, seen = [], set()
    for claim in draft.claims:
        signal = session.get(SignalRecord, claim.get("signal_id"))
        if signal is not None and signal.time_sensitive and signal.id not in seen:
            seen.add(signal.id)
            out.append((claim, signal))
    return out


def recheck_summary(session: Session, run: Run) -> dict[str, Any]:
    """For the UI: how many facts an approve would re-check (the "Re-checking N facts" step)."""
    draft = latest_draft(session, run.id)
    if draft is None or run.status not in REVIEWABLE:
        return {"facts": 0, "draft_age_days": None}
    age = (utcnow() - _aware(draft.created_at)).days
    return {"facts": len(stale_claims(session, draft)), "draft_age_days": age}


# --- Model judgements -------------------------------------------------------------------------

class _Compare(BaseModel):
    verdict: Verdict
    reason: str = Field(description="One short sentence")
    new_fact: str | None = Field(default=None, description="changed only: the current fact, one sentence")
    new_evidence: str | None = Field(default=None, description="changed only: exact quote from the current page")


class _Sentence(BaseModel):
    sentence: str


async def compare(signal: SignalRecord, sentence: str, page: str) -> tuple[_Compare, int]:
    user = prompts.get("recheck", "compare_user", summary=signal.summary, evidence=signal.evidence,
                       sentence=sentence, page=page[: settings.source_max_chars])
    result, usage = await llm.structured("fast", prompts.get("recheck", "compare_system"), user, _Compare)
    return result, usage.prompt_tokens + usage.completion_tokens


async def rewrite_sentence(sentence: str, old_fact: str, new_fact: str, evidence: str) -> tuple[str, int]:
    user = prompts.get("recheck", "rewrite_user", sentence=sentence, old_fact=old_fact, new_fact=new_fact,
                       evidence=evidence)
    result, usage = await llm.structured("smart", prompts.get("recheck", "rewrite_system"), user, _Sentence)
    return " ".join(result.sentence.split()), usage.prompt_tokens + usage.completion_tokens


# --- Sentence surgery -------------------------------------------------------------------------


def find_sentence(body: str, claimed: str) -> str | None:
    """The sentence of `body` the claim refers to: exact if possible, else the closest one."""
    if claimed and claimed in body:
        return claimed
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body) if s.strip()]
    want = set(_words(claimed))
    best = max(sentences, key=lambda s: len(want & set(_words(s))) / max(1, len(want)), default=None)
    if best is None or not want or len(want & set(_words(best))) / len(want) < 0.6:
        return None
    return best


# --- The review actions -----------------------------------------------------------------------


def _get(session: Session, run_id: int) -> tuple[Run, DraftRecord]:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"Run {run_id} not found.")
    draft = latest_draft(session, run_id)
    if draft is None:
        raise ReviewError("This run has no draft to review.")
    if run.status not in REVIEWABLE:
        raise ReviewError(f"Only drafts waiting for review can be reviewed (this run is {run.status.value}).")
    return run, draft


def _close(session: Session, run: Run, draft: DraftRecord, action: ReviewerAction, status: RunStatus,
           final_reason: str | None = None, **review: Any) -> None:
    draft.reviewer_action = action
    draft.reviewed_at = utcnow()
    draft.review = {**(draft.review or {}), **review}
    run.status = status
    run.final_reason = final_reason
    session.add(draft)
    session.add(run)
    session.commit()


def _facts(n: int) -> str:
    return f"{n} fact{'s' if n != 1 else ''}"


def _announce(run: Run) -> None:
    """Tell the run and batch views (and the batch's progress counts) about the new status."""
    from app.pipeline import runner

    runner.publish_status(run)
    runner.update_batch_progress(run.batch_id)


def edit_diff(before: str, after: str) -> list[str]:
    return list(difflib.unified_diff(before.splitlines(), after.splitlines(), "draft", "edited", lineterm=""))


async def review(session: Session, run_id: int, action: ReviewerAction, body: str | None = None,
                 reason: str | None = None, draft_id: int | None = None) -> ReviewResult:
    from app.pipeline import runner

    run, draft = _get(session, run_id)
    if draft_id is not None and draft_id != draft.id:
        raise ReviewError("A newer version of this draft exists; review that one.")

    if action == ReviewerAction.reject:
        _close(session, run, draft, action, RunStatus.rejected,
               f"Rejected by reviewer: {reason}" if reason else "Rejected by reviewer", reason=reason)
        _announce(run)
        return ReviewResult(outcome="rejected", message="Rejected", run_status=run.status, draft_id=draft.id)

    if action == ReviewerAction.edit:
        if not body or not body.strip():
            raise ReviewError("An edit needs the edited body.")
        draft.edited_body = body.strip()
        _close(session, run, draft, action, RunStatus.approved, None, edit_diff=edit_diff(draft.body, draft.edited_body))
        _announce(run)
        return ReviewResult(outcome="edited", message="Approved with your edits", run_status=run.status,
                            draft_id=draft.id)

    stale = stale_claims(session, draft)
    if not stale:
        _close(session, run, draft, action, RunStatus.approved)
        _announce(run)
        return ReviewResult(outcome="approved", message="Approved", run_status=run.status, draft_id=draft.id)
    return await revalidate(session, run, draft, stale)


async def revalidate(session: Session, run: Run, draft: DraftRecord,
                     stale: list[tuple[dict[str, Any], SignalRecord]]) -> ReviewResult:
    """Case 10: re-check each time-sensitive fact against its page as it is now."""
    from app.pipeline import runner

    urls = list(dict.fromkeys(s.source_url for _, s in stale))
    pages = await search.extract(urls, fresh=True)
    credits, tokens = pages.credits, 0
    checks: list[FactCheck] = []
    new_body = draft.body
    for claim, signal in stale:
        sentence = find_sentence(new_body, claim.get("sentence", ""))
        page = pages.pages.get(signal.source_url)
        check = FactCheck(signal_id=signal.id, source_url=signal.source_url, verdict="gone",
                          old_fact=signal.summary, sentence_before=sentence)
        if not page:
            check.reason = "The page could not be read any more."
        else:
            verdict, used = await compare(signal, sentence or claim.get("sentence", ""), page)
            tokens += used
            check.verdict, check.reason, check.new_fact = verdict.verdict, verdict.reason, verdict.new_fact
            if verdict.verdict == "changed":
                evidence = verdict.new_evidence or ""
                if not (verdict.new_fact and evidence_on_page(evidence, page) and sentence):
                    check.verdict = "gone"  # can't state the new fact with a quote behind it
                    check.reason += " (the new fact could not be verified on the page)"
                else:
                    after, used = await rewrite_sentence(sentence, signal.summary, verdict.new_fact, evidence)
                    tokens += used
                    invented = _numbers_in(after) - _numbers_in(f"{verdict.new_fact} {evidence}")
                    if invented:  # the rewrite added a number the page doesn't give
                        check.verdict = "gone"
                        check.reason += f" (the rewrite added unsourced numbers: {', '.join(sorted(invented))})"
                    else:
                        check.sentence_after = after
                        new_body = new_body.replace(sentence, after, 1)
                        _update_signal(session, signal, verdict.new_fact, evidence)
        checks.append(check)
    run.cost_credits = (run.cost_credits or 0) + credits
    report = {"checks": [c.model_dump() for c in checks], "tokens": tokens, "credits": credits,
              "checked_at": utcnow().isoformat()}

    gone = [c for c in checks if c.verdict == "gone"]
    if gone:
        # The hook (or a fact behind it) no longer holds: score again without that source, so the
        # runner-up becomes the hook, and the run returns to the review queue.
        draft.review = {**(draft.review or {}), "revalidation": report}
        run.status = RunStatus.running
        run.final_reason = None
        session.add(draft)
        session.add(run)
        session.commit()
        _announce(run)
        runner.spawn(runner.run_one(run.id, start_stage="score", resume_input={
            "exclude_sources": [c.source_url for c in gone],
            "exclude_reason": "Gone on re-check: the source no longer states this"}))
        return ReviewResult(outcome="gone", run_status=RunStatus.running, draft_id=draft.id, checks=checks,
                            message=f"{_facts(len(gone))} no longer on the page: re-drafting with the runner-up hook")

    changed = [c for c in checks if c.verdict == "changed"]
    if not changed:
        draft.review = {**(draft.review or {}), "revalidation": report}
        _close(session, run, draft, ReviewerAction.approve, RunStatus.approved)
        _announce(run)
        return ReviewResult(outcome="approved", run_status=run.status, draft_id=draft.id, checks=checks,
                            message=f"Re-checked {_facts(len(checks))}: unchanged. Approved")

    claims = [dict(c) for c in draft.claims]
    for c in claims:
        for check in changed:
            if c.get("signal_id") == check.signal_id and check.sentence_before and check.sentence_after:
                c["sentence"] = check.sentence_after
    new = DraftRecord(run_id=run.id, version=draft.version + 1, subject=draft.subject, body=new_body,
                      hook_signal_id=draft.hook_signal_id, claims=claims,
                      critic_report={**draft.critic_report, "revalidated_from": draft.id},
                      review={"revalidation": report})
    session.add(new)
    session.commit()
    session.refresh(new)
    draft.review = {**(draft.review or {}), "revalidation": report, "superseded_by": new.id}
    session.add(draft)
    session.add(run)
    session.commit()
    return ReviewResult(outcome="changed", run_status=run.status, draft_id=new.id, checks=checks,
                        diff={"before": draft.body, "after": new_body},
                        message=f"{_facts(len(changed))} changed since the draft was written: approve the corrected version")


def _update_signal(session: Session, signal: SignalRecord, fact: str, evidence: str) -> None:
    """The signal now states what the page says today; the note keeps what it said before."""
    was = signal.summary
    signal.summary, signal.evidence = fact, evidence[:200]
    signal.note = f"Updated on re-check {utcnow():%Y-%m-%d}; was: {was}"
    session.add(signal)
    session.commit()


# --- Export -----------------------------------------------------------------------------------

EXPORT_COLUMNS = ["status", "hook", "source_url", "subject", "body"]


def export_csv(session: Session, batch: Batch, everything: bool = False) -> str:
    """The original columns plus status, hook, source_url, subject, body (approved rows only by
    default). The body is the approved text: the rep's edit if they edited it."""
    runs = {r.row_number: r for r in session.exec(select(Run).where(Run.batch_id == batch.id)).all()}
    headers = list(batch.headers)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=headers + [c for c in EXPORT_COLUMNS if c not in headers],
                            extrasaction="ignore")
    writer.writeheader()
    for row in sorted(batch.rows, key=lambda r: r["row_number"]):
        run = runs.get(row["row_number"])
        status = run.status.value if run else ("invalid" if row["status"] == "invalid" else "skipped")
        if not everything and status != RunStatus.approved.value:
            continue
        extra = {"status": status, "hook": "", "source_url": "", "subject": "", "body": ""}
        if run is not None:
            draft = _final_draft(session, run)
            if draft is not None:
                extra["subject"] = draft.subject
                extra["body"] = draft.edited_body or draft.body
                hook = session.get(SignalRecord, draft.hook_signal_id) if draft.hook_signal_id else None
                if hook is not None:
                    extra["hook"], extra["source_url"] = hook.summary, hook.source_url
        writer.writerow({**{h: (row.get("raw") or {}).get(h, "") for h in headers}, **extra})
    return out.getvalue()


def _final_draft(session: Session, run: Run) -> DraftRecord | None:
    """The approved version if there is one, else the latest."""
    drafts = session.exec(select(DraftRecord).where(DraftRecord.run_id == run.id)
                          .order_by(DraftRecord.version.desc())).all()
    approved = [d for d in drafts if d.reviewer_action in (ReviewerAction.approve, ReviewerAction.edit)]
    return (approved or drafts or [None])[0]
