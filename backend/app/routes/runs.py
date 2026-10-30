"""Runs: list, detail, live events, resume. Spec: docs/PROJECT_SPEC.md T05 and "UI: three screens"."""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app import db, review
from app.config import settings
from app.db import get_session
from app.events import HEARTBEAT, bus, format_sse, sse_response
from app.models import DraftRecord, ReviewerAction, Run, RunStatus, SignalRecord, StageEvent, StageState
from app.pipeline import runner

router = APIRouter(prefix="/runs", tags=["runs"])


class ReviewRequest(BaseModel):
    action: ReviewerAction
    body: str | None = None  # edit: the rep's final text
    reason: str | None = None  # reject: why
    draft_id: int | None = None  # the version the rep was looking at (409 if a newer one exists)
    identity_confirmed: bool = False  # company-level draft: the rep vouches the person exists
    approve_anyway: bool = False  # the rep saw the unsupported-fact warning and approves regardless


class ResumeRequest(BaseModel):
    stage: str
    input: dict[str, Any] = {}


def latest_stage_events(session: Session, run_ids: list[int]) -> dict[int, StageEvent]:
    """Each run's most recent stage event (what the batch view shows as 'current stage')."""
    if not run_ids:
        return {}
    last_ids = select(func.max(StageEvent.id)).where(StageEvent.run_id.in_(run_ids)).group_by(StageEvent.run_id)
    events = session.exec(select(StageEvent).where(StageEvent.id.in_(last_ids))).all()
    return {e.run_id: e for e in events}


def hook_types(session: Session, run_ids: list[int]) -> dict[int, str]:
    """Each run's chosen hook type (the selected signal), for list views."""
    if not run_ids:
        return {}
    rows = session.exec(select(SignalRecord.run_id, SignalRecord.type)
                        .where(SignalRecord.run_id.in_(run_ids), SignalRecord.selected)).all()
    return {run_id: type_ for run_id, type_ in rows}


SignalLevel = str  # "high" | "moderate" | "low" | "none"


def signal_levels(session: Session, run_ids: list[int]) -> dict[int, SignalLevel]:
    """How much the research found, for the lists' status column. Only runs whose research
    finished (extraction done) get a level; the others keep their run status.
    high / moderate: a hook scoring at least settings.high_signal_at / below it;
    low: facts were found but none was good enough to open with; none: no usable fact at all."""
    if not run_ids:
        return {}
    researched = set(session.exec(select(StageEvent.run_id).where(
        StageEvent.run_id.in_(run_ids), StageEvent.stage == "extract", StageEvent.state == StageState.done)).all())
    found: dict[int, list[SignalRecord]] = {}
    for s in session.exec(select(SignalRecord).where(SignalRecord.run_id.in_(researched),
                                                     SignalRecord.type != "ai_screening")).all():
        found.setdefault(s.run_id, []).append(s)
    levels = {}
    for run_id in researched:
        signals = found.get(run_id, [])
        hook = next((s for s in signals if s.selected), None)
        score = (hook.scores or {}).get("score") if hook else None
        if hook is not None:
            levels[run_id] = "high" if (score or 0) >= settings.high_signal_at else "moderate"
        else:
            levels[run_id] = "low" if signals else "none"
    return levels | _earlier_levels(session, [i for i in run_ids if i not in levels])


def _earlier_levels(session: Session, run_ids: list[int]) -> dict[int, SignalLevel]:
    """A re-upload of someone researched recently (blocked / already_drafted) did no research of
    its own: it shows the level of the run it points to, when that run has one."""
    stopped = session.exec(select(Run.id).where(
        Run.id.in_(run_ids), Run.status.in_([RunStatus.blocked, RunStatus.already_drafted]))).all()
    if not stopped:
        return {}
    earlier = {e.run_id: e.payload.get("earlier_run_id") for e in session.exec(select(StageEvent).where(
        StageEvent.run_id.in_(stopped), StageEvent.stage == "intake")).all() if e.payload.get("earlier_run_id")}
    found = signal_levels(session, [i for i in set(earlier.values()) if i not in stopped])
    return {run_id: found[e] for run_id, e in earlier.items() if e in found}


def run_summary(run: Run, latest: StageEvent | None = None, hook_type: str | None = None,
                signal_level: SignalLevel | None = None) -> dict[str, Any]:
    return {
        "id": run.id,
        "batch_id": run.batch_id,
        "row_number": run.row_number,
        "name": run.full_name or f"{run.first_name} {run.last_name}",
        "company": run.company,
        "title": run.title,
        "account_key": run.account_key,
        "status": run.status,
        "final_reason": run.final_reason,
        "identity_outcome": run.identity_outcome,
        "identity_unverified": run.identity_unverified,
        "confidence": run.confidence,
        "cost_credits": run.cost_credits,
        "retry_count": run.retry_count or 0,
        "retries_left": max(0, settings.retry_max_attempts - (run.retry_count or 0)),
        "current_stage": latest.stage if latest else None,
        "current_state": latest.state if latest else None,
        "hook_type": hook_type,
        "signal_level": signal_level,
        "send_day": run.send_day,
        "account_plan": run.account_plan or None,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
    }


def _get_run(session: Session, run_id: int) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise HTTPException(404, f"Run {run_id} not found.")
    return run


@router.get("")
def list_runs(
    batch_id: int | None = None,
    status: RunStatus | None = None,
    q: str | None = Query(None, description="Search name or company"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    query = select(Run)
    if batch_id is not None:
        query = query.where(Run.batch_id == batch_id)
    if status is not None:
        query = query.where(Run.status == status)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Run.full_name.ilike(like), Run.first_name.ilike(like),
                                Run.last_name.ilike(like), Run.company.ilike(like)))
    total = session.exec(select(func.count()).select_from(query.subquery())).one()
    runs = session.exec(query.order_by(Run.id.desc()).offset(offset).limit(limit)).all()
    ids = [r.id for r in runs]
    latest, hooks, levels = latest_stage_events(session, ids), hook_types(session, ids), signal_levels(session, ids)
    return {"total": total, "items": [run_summary(r, latest.get(r.id), hooks.get(r.id), levels.get(r.id)) for r in runs]}


@router.get("/{run_id}")
def get_run(run_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Everything the run view needs: prospect, status, every stage event, signals, drafts."""
    run = _get_run(session, run_id)
    events = session.exec(select(StageEvent).where(StageEvent.run_id == run_id).order_by(StageEvent.id)).all()
    signals = session.exec(select(SignalRecord).where(SignalRecord.run_id == run_id)).all()
    drafts = session.exec(select(DraftRecord).where(DraftRecord.run_id == run_id).order_by(DraftRecord.version)).all()
    return {
        "run": {**run.model_dump(), "retries_left": max(0, settings.retry_max_attempts - (run.retry_count or 0)),
                "signal_level": signal_levels(session, [run.id]).get(run.id)},
        "stages": [{"name": s.name, "label": s.label} for s in runner.get_stages()],
        "events": [runner.stage_event_dict(e, run.batch_id) for e in events],
        "signals": [s.model_dump() for s in signals],
        "drafts": [d.model_dump() for d in drafts],
        "recheck": review.recheck_summary(session, run),
    }


@router.get("/{run_id}/events")
async def run_events(
    run_id: int,
    after: int = Query(0, ge=0, description="Only events with a larger id"),
    last_event_id: int | None = Header(None),
) -> StreamingResponse:
    """SSE: stored stage events first, then live ones; `end` once the run stops running."""
    with Session(db.engine) as session:
        _get_run(session, run_id)
    since = max(after, last_event_id or 0)

    async def stream():
        topic = f"run:{run_id}"
        queue = bus.subscribe(topic)  # before reading the database, so nothing slips between
        try:
            with Session(db.engine) as session:
                stored = session.exec(
                    select(StageEvent).where(StageEvent.run_id == run_id, StageEvent.id > since)
                    .order_by(StageEvent.id)
                ).all()
                run = session.get(Run, run_id)
                batch_id, status = run.batch_id, run.status
            seen = since
            for event in stored:
                yield format_sse(runner.stage_event_dict(event, batch_id))
                seen = event.id
            if status != RunStatus.running:
                yield format_sse({"type": "end", "run_id": run_id, "status": status.value})
                return
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), settings.sse_heartbeat_s)
                except TimeoutError:
                    yield HEARTBEAT
                    continue
                if event["type"] == "stage":
                    if event["id"] <= seen:
                        continue  # already sent from the database
                    seen = event["id"]
                yield format_sse(event)
                if event["type"] == "run_status" and event["status"] != RunStatus.running.value:
                    yield format_sse({"type": "end", "run_id": run_id, "status": event["status"]})
                    return
        finally:
            bus.unsubscribe(topic, queue)

    return sse_response(stream())


@router.post("/{run_id}/resume", status_code=202)
async def resume_run(run_id: int, request: ResumeRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Continue a needs_input run from `stage`, with what the rep supplied (e.g. the right person)."""
    run = _get_run(session, run_id)
    if run.status != RunStatus.needs_input:
        raise HTTPException(409, f"Only runs waiting for input can resume (this one is {run.status.value}).")
    names = [s.name for s in runner.get_stages()]
    if request.stage not in names:
        raise HTTPException(422, f"Unknown stage '{request.stage}'. Stages: {', '.join(names)}.")
    run.status = RunStatus.running
    session.add(run)
    session.commit()
    runner.spawn(runner.run_one(run_id, start_stage=request.stage, resume_input=request.input))
    return {"run_id": run_id, "status": "running", "resumed_at": request.stage}


@router.post("/{run_id}/retry", status_code=202)
async def retry_run(run_id: int) -> dict[str, Any]:
    """Run a failed prospect again without re-uploading it: restart at the first stage whose saved
    result is missing or stale, reusing the rest. At most settings.retry_max_attempts times."""
    try:
        return runner.start_retry(run_id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except runner.RetryRefused as error:
        raise HTTPException(409, str(error)) from error


@router.post("/{run_id}/review")
async def review_run(run_id: int, request: ReviewRequest, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Approve / edit / reject the latest draft. Approving an old draft with time-sensitive facts
    re-checks them first (case 10): the answer may be a corrected version to approve again."""
    try:
        result = await review.review(session, run_id, request.action, request.body, request.reason, request.draft_id,
                                     identity_confirmed=request.identity_confirmed,
                                     approve_anyway=request.approve_anyway)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except review.ReviewError as error:
        raise HTTPException(409, str(error)) from error
    return result.model_dump(mode="json")
