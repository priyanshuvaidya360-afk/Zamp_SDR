"""Dashboard numbers. Spec: docs/GUIDE.md "UI: three screens" > 3 Dashboard, and T16.

Definitions (each one is a plain SQL count; see ASSUMPTIONS.md):
- this week = the last 7 days (batches by uploaded_at, prospects by run started_at)
- finished = any run that is no longer running
- reached review = ready_for_review + approved + rejected (a draft reached the queue)
- approval rate = approved / (approved + rejected)
- run time = finished_at - started_at; credits = cost_credits, both over finished runs
"""

import statistics
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from app.db import get_session
from app.models import Batch, Run, RunStatus, utcnow

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
    finished = total - counts.get(RunStatus.running.value, 0)
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
    }
