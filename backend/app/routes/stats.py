"""Dashboard numbers. Spec: docs/PROJECT_SPEC.md "UI: three screens" > 3 Dashboard, and T16.

Definitions (each one is a plain SQL count; see ASSUMPTIONS.md):
- this week = the last 7 days (batches by uploaded_at, prospects by run started_at)
- finished = any run that is no longer running, except already_drafted (a re-upload of someone
  drafted recently: counting it would make the rates look worse for no real reason)
- reached review = ready_for_review + approved + rejected (a draft reached the queue)
- approval rate = approved / (approved + rejected)
- run time = finished_at - started_at; credits = cost_credits, both over finished runs
- retryable = failed runs with retries left (POST /runs/{id}/retry)
- identity = needs_input runs by why identity failed (resolve.diagnose): search_failed / bad_data /
  not_public / ambiguous / unconfirmed; many bad_data means the list is bad, not the agent
- dismissed = runs the rep closed from needs_input (counted as finished, not as not-ready)
- factual_review = drafts waiting for review whose latest version has unsupported facts (can't be
  approved until the rep edits them)
"""

import statistics
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from app.db import get_session
from app.config import settings
from app.models import Batch, DraftRecord, Run, RunStatus, utcnow

router = APIRouter(tags=["stats"])

REACHED_REVIEW = (RunStatus.ready_for_review, RunStatus.approved, RunStatus.rejected)
NOT_READY = (RunStatus.needs_input, RunStatus.low_signal, RunStatus.blocked, RunStatus.suppressed,
             RunStatus.held, RunStatus.failed)


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    week_ago = utcnow() - timedelta(days=7)
    counts = {status.value if hasattr(status, "value") else status: n for status, n in
              session.exec(select(Run.status, func.count()).group_by(Run.status)).all()}
    total = sum(counts.values())
    finished = total - counts.get(RunStatus.running.value, 0) - counts.get(RunStatus.already_drafted.value, 0)
    reached = sum(counts.get(s.value, 0) for s in REACHED_REVIEW)
    approved, rejected = counts.get(RunStatus.approved.value, 0), counts.get(RunStatus.rejected.value, 0)

    done = session.exec(select(Run.started_at, Run.finished_at, Run.cost_credits)
                        .where(Run.status != RunStatus.running, Run.finished_at.is_not(None))).all()
    durations = [(end - start).total_seconds() for start, end, _ in done]
    return {
        "batches_this_week": session.exec(select(func.count()).select_from(Batch)
                                          .where(Batch.uploaded_at >= week_ago)).one(),
        "prospects_this_week": session.exec(select(func.count()).select_from(Run)
                                            .where(Run.started_at >= week_ago)).one(),
        "runs_total": total,
        "runs_finished": finished,
        "running": counts.get(RunStatus.running.value, 0),
        "reached_review": reached,
        "ready_for_review_rate": _ratio(reached, finished),
        "waiting_for_review": counts.get(RunStatus.ready_for_review.value, 0),
        "approved": approved,
        "rejected": rejected,
        "approval_rate": _ratio(approved, approved + rejected),
        "median_run_seconds": round(statistics.median(durations), 1) if durations else None,
        "avg_credits_per_prospect": round(sum(c for *_, c in done) / len(done), 2) if done else None,
        "not_ready": {s.value: counts.get(s.value, 0) for s in NOT_READY},
        "retryable": session.exec(select(func.count()).select_from(Run).where(
            Run.status == RunStatus.failed, Run.retry_count < settings.retry_max_attempts)).one(),
        "factual_review": _factual_review(session),
        "identity": _identity_outcomes(session),
        "dismissed": counts.get(RunStatus.dismissed.value, 0),
    }


def _identity_outcomes(session: Session) -> dict[str, int]:
    """Runs made before the diagnosis existed have no outcome: they count as unconfirmed."""
    found: dict[str, int] = {}
    for outcome, n in session.exec(select(Run.identity_outcome, func.count())
                                   .where(Run.status == RunStatus.needs_input).group_by(Run.identity_outcome)).all():
        key = outcome or "unconfirmed"
        found[key] = found.get(key, 0) + n
    return found


def _factual_review(session: Session) -> int:
    waiting = session.exec(select(Run.id).where(Run.status.in_([RunStatus.ready_for_review, RunStatus.low_signal]))).all()
    latest: dict[int, DraftRecord] = {}
    for d in session.exec(select(DraftRecord).where(DraftRecord.run_id.in_(waiting)).order_by(DraftRecord.id)).all():
        latest[d.run_id] = d
    return sum(1 for d in latest.values() if ((d.critic_report or {}).get("factual") or {}).get("blocking"))
