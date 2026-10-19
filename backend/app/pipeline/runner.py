"""Batch runner: runs each prospect through the stages and streams every step.

Spec: docs/GUIDE.md "Pipeline stages in detail" (batch layer) and T05.

Code owns the flow; stages only do work. A stage is `async def run(ctx) -> BaseModel`. It may
raise StopRun to end the run with a named status (needs_input, low_signal, blocked, held,
suppressed); any other exception fails only that run. Every stage gets started / done /
failed / skipped events, saved to stage_events and then published for live views.
"""

import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel
from sqlmodel import Session, select

from app import db
from app.config import settings
from app.events import bus
from app.models import Batch, BatchStatus, Run, RunStatus, StageEvent, StageState, utcnow
from app.seller import load_seller_profile

log = logging.getLogger(__name__)


class StopRun(Exception):
    """A stage decided the run ends here, with a named status and a reason for the rep."""

    def __init__(self, status: RunStatus, reason: str, payload: dict[str, Any] | None = None):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.payload = payload or {}


@dataclass
class Stage:
    name: str
    label: str  # shown in the stepper and in failure messages
    fn: Callable[["RunContext"], Awaitable[BaseModel]]
    output: type[BaseModel]  # used to rebuild this stage's result when a run resumes


class BatchContext:
    """State shared by every run in one batch: company research is done once per account."""

    def __init__(self, batch_id: int):
        self.batch_id = batch_id
        self._research: dict[str, asyncio.Future] = {}

    async def company_research(self, account_key: str | None, factory: Callable[[], Awaitable[Any]]) -> Any:
        """Run `factory` once per account. Concurrent callers wait for the same result."""
        if account_key is None:
            return await factory()
        if account_key not in self._research:
            task = asyncio.ensure_future(factory())
            # A failure is not cached: the next run at this company tries again.
            task.add_done_callback(
                lambda t, key=account_key: self._research.pop(key, None)
                if not t.cancelled() and t.exception() else None
            )
            self._research[account_key] = task
        return await asyncio.shield(self._research[account_key])


@dataclass
class RunContext:
    run_id: int
    batch_id: int
    row: dict[str, Any]  # cleaned prospect fields (first/last/full name, company, domain...)
    seller: dict[str, Any]
    batch: BatchContext
    account_key: str | None = None
    outputs: dict[str, BaseModel] = field(default_factory=dict)  # stage name -> result
    resume_input: dict[str, Any] | None = None  # what the rep supplied for needs_input
    message_type: str = "cold"  # "follow_up" when the prospect replied 'not now' before (T13)
    final_status: RunStatus = RunStatus.ready_for_review
    final_reason: str | None = None
    confidence: float | None = None
    tokens: int = 0
    credits: float = 0.0
    _stage_tokens: int = 0
    _stage_credits: float = 0.0

    def add_usage(self, tokens: int = 0, credits: float = 0.0) -> None:
        """Stages report every LLM / search call here; the runner logs it per stage."""
        self._stage_tokens += tokens
        self._stage_credits += credits
        self.tokens += tokens
        self.credits += credits


# --- Wiring ----------------------------------------------------------------------


def get_stages() -> list[Stage]:
    """The pipeline, in order (app/pipeline/stages.py). Tests replace this."""
    from app.pipeline.stages import STAGES

    return STAGES


_semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}
_batch_contexts: dict[int, BatchContext] = {}
_tasks: set[asyncio.Task] = set()


def _semaphore() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _semaphores:
        _semaphores[loop] = asyncio.Semaphore(settings.max_concurrent_runs)
    return _semaphores[loop]


def batch_context(batch_id: int) -> BatchContext:
    if batch_id not in _batch_contexts:
        _batch_contexts[batch_id] = BatchContext(batch_id)
    return _batch_contexts[batch_id]


def spawn(coro: Coroutine) -> asyncio.Task:
    """Start background work and keep a reference so it is never garbage-collected."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


# --- Accounts ----------------------------------------------------------------------


def account_key_for(data: dict[str, Any]) -> str | None:
    """Group people at the same company: normalised domain, else normalised company name.

    LinkedIn-only rows with no company name get a key once stage 2 resolves their domain.
    """
    if data.get("company_domain"):
        return data["company_domain"]
    company = re.sub(r"[^a-z0-9]+", " ", (data.get("company") or "").lower()).strip()
    company = re.sub(r"\b(inc|ltd|llc|gmbh|plc|corp|co|limited|pvt)\b", "", company).strip()
    return f"name:{re.sub(r'\s+', '-', company)}" if company else None


def create_runs(session: Session, batch: Batch) -> list[int]:
    """One run per valid preview row (invalid / duplicate rows stay in batch.rows)."""
    run_ids = []
    for row in batch.rows:
        if row["status"] != "valid":
            continue
        data = row["data"]
        run = Run(
            batch_id=batch.id,
            row_number=row["row_number"],
            raw_row=row["raw"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            full_name=data.get("full_name"),
            company=data.get("company"),
            title=data.get("title"),
            domain=data.get("company_domain"),
            linkedin_url=data.get("linkedin_url"),
            email=data.get("email"),
            rep_notes=data.get("notes"),
            public_bio=data.get("public_bio"),
            account_key=account_key_for(data),
            status=RunStatus.running,
        )
        session.add(run)
        session.flush()
        run_ids.append(run.id)
    batch.status = BatchStatus.running
    session.add(batch)
    session.commit()
    return run_ids


# --- Events ------------------------------------------------------------------------


def stage_event_dict(event: StageEvent, batch_id: int | None = None) -> dict[str, Any]:
    return {
        "type": "stage",
        "id": event.id,
        "run_id": event.run_id,
        "batch_id": batch_id,
        "stage": event.stage,
        "state": event.state.value if hasattr(event.state, "value") else event.state,
        "message": event.message,
        "payload": event.payload,
        "duration_ms": event.duration_ms,
        "ts": event.ts.isoformat(),
    }


def _record(ctx: RunContext, stage: Stage, state: StageState, message: str = "",
            payload: dict[str, Any] | None = None, duration_ms: int | None = None) -> None:
    """Save the event, then publish it (full on the run topic, slim on the batch topic)."""
    with Session(db.engine) as session:
        event = StageEvent(run_id=ctx.run_id, stage=stage.name, state=state, message=message,
                           payload=payload or {}, duration_ms=duration_ms)
        session.add(event)
        session.commit()
        session.refresh(event)
        data = stage_event_dict(event, ctx.batch_id)
    bus.publish(f"run:{ctx.run_id}", data)
    bus.publish(f"batch:{ctx.batch_id}", {**data, "payload": {}})


def _publish_status(run: Run) -> None:
    event = {
        "type": "run_status",
        "run_id": run.id,
        "batch_id": run.batch_id,
        "status": run.status.value,
        "final_reason": run.final_reason,
        "confidence": run.confidence,
        "cost_credits": run.cost_credits,
    }
    bus.publish(f"run:{run.id}", event)
    bus.publish(f"batch:{run.batch_id}", event)


def plain_error(error: Exception) -> str:
    """An error message a rep can read."""
    if isinstance(error, (httpx.TimeoutException, TimeoutError)):
        return "a web service took too long to answer"
    if isinstance(error, httpx.HTTPError):
        return "couldn't reach a web service"
    message = str(error).strip() or type(error).__name__
    return message if len(message) <= 300 else message[:297] + "..."


# --- Running -----------------------------------------------------------------------


def _load_context(run_id: int, resume_input: dict[str, Any] | None) -> RunContext:
    with Session(db.engine) as session:
        run = session.get(Run, run_id)
        run.status = RunStatus.running
        run.final_reason = None
        run.started_at = utcnow()
        run.finished_at = None
        session.add(run)
        session.commit()
        session.refresh(run)
        _publish_status(run)
        row = run.model_dump(include={
            "first_name", "last_name", "full_name", "company", "title", "domain",
            "linkedin_url", "email", "rep_notes", "public_bio",
        })
        return RunContext(
            run_id=run.id, batch_id=run.batch_id, row=row, seller=load_seller_profile(),
            batch=batch_context(run.batch_id), account_key=run.account_key,
            message_type=run.message_type.value if hasattr(run.message_type, "value") else run.message_type,
            resume_input=resume_input,
        )


def _restore_outputs(ctx: RunContext, stages: list[Stage]) -> None:
    """Rebuild earlier stages' results from their saved 'done' events (for resume)."""
    with Session(db.engine) as session:
        for stage in stages:
            event = session.exec(
                select(StageEvent)
                .where(StageEvent.run_id == ctx.run_id, StageEvent.stage == stage.name,
                       StageEvent.state == StageState.done)
                .order_by(StageEvent.id.desc())
            ).first()
            if event and "output" in event.payload:
                ctx.outputs[stage.name] = stage.output.model_validate(event.payload["output"])


async def run_one(run_id: int, start_stage: str | None = None,
                  resume_input: dict[str, Any] | None = None) -> None:
    """Run one prospect through the stages. Never raises: failures become run statuses."""
    stages = get_stages()
    async with _semaphore():
        ctx = _load_context(run_id, resume_input)
        start = 0
        if start_stage:
            start = next(i for i, s in enumerate(stages) if s.name == start_stage)
            _restore_outputs(ctx, stages[:start])

        status, reason = None, None
        for index in range(start, len(stages)):
            stage = stages[index]
            _record(ctx, stage, StageState.started)
            ctx._stage_tokens, ctx._stage_credits = 0, 0.0
            began = time.perf_counter()
            try:
                output = await stage.fn(ctx)
            except StopRun as stop:
                status, reason = stop.status, stop.reason
                _record(ctx, stage, StageState.done, stop.reason,
                        {**stop.payload, "stopped": stop.status.value, **_usage(ctx)},
                        _elapsed_ms(began))
            except Exception as error:  # this run only; the batch carries on
                log.exception("run %s failed in %s", run_id, stage.name)
                message = plain_error(error)
                status, reason = RunStatus.failed, f"{stage.label} failed: {message}"
                _record(ctx, stage, StageState.failed, message,
                        {"error": type(error).__name__, **_usage(ctx)}, _elapsed_ms(began))
            else:
                ctx.outputs[stage.name] = output
                _record(ctx, stage, StageState.done, getattr(output, "message", ""),
                        {"output": output.model_dump(mode="json"), **_usage(ctx)},
                        _elapsed_ms(began))
                continue
            for skipped in stages[index + 1:]:
                _record(ctx, skipped, StageState.skipped, "Not run")
            break
        else:
            status, reason = ctx.final_status, ctx.final_reason

        with Session(db.engine) as session:
            run = session.get(Run, run_id)
            run.status = status
            run.final_reason = reason
            run.confidence = ctx.confidence
            run.cost_credits = (run.cost_credits or 0) + ctx.credits
            run.finished_at = utcnow()
            session.add(run)
            session.commit()
            session.refresh(run)
            _publish_status(run)
            batch_id = run.batch_id
    update_batch_progress(batch_id)


def _usage(ctx: RunContext) -> dict[str, Any]:
    return {"tokens": ctx._stage_tokens, "credits": ctx._stage_credits}


def _elapsed_ms(began: float) -> int:
    return int((time.perf_counter() - began) * 1000)


def batch_progress(session: Session, batch_id: int) -> dict[str, Any]:
    statuses = session.exec(select(Run.status).where(Run.batch_id == batch_id)).all()
    counts = Counter(s.value if hasattr(s, "value") else s for s in statuses)
    return {
        "type": "batch_progress",
        "batch_id": batch_id,
        "done": len(statuses) - counts.get(RunStatus.running.value, 0),
        "total": len(statuses),
        "counts": dict(counts),
    }


def update_batch_progress(batch_id: int) -> None:
    """Publish progress; mark the batch done once no run is still running."""
    with Session(db.engine) as session:
        progress = batch_progress(session, batch_id)
        bus.publish(f"batch:{batch_id}", progress)
        batch = session.get(Batch, batch_id)
        if progress["done"] == progress["total"] and batch.status != BatchStatus.done:
            batch.status = BatchStatus.done
            batch.finished_at = utcnow()
            session.add(batch)
            session.commit()
            bus.publish(f"batch:{batch_id}", {"type": "batch_status", "batch_id": batch_id, "status": "done"})


async def run_batch(run_ids: list[int]) -> None:
    await asyncio.gather(*(run_one(run_id) for run_id in run_ids), return_exceptions=True)


def recover_interrupted() -> int:
    """At startup: runs left 'running' by a crash or restart can never finish, so fail them."""
    with Session(db.engine) as session:
        stuck = session.exec(select(Run).where(Run.status == RunStatus.running)).all()
        for run in stuck:
            run.status = RunStatus.failed
            run.final_reason = "Interrupted by a server restart. Run this prospect again."
            run.finished_at = utcnow()
            session.add(run)
        for batch in session.exec(select(Batch).where(Batch.status == BatchStatus.running)).all():
            batch.status = BatchStatus.done
            batch.finished_at = utcnow()
            session.add(batch)
        session.commit()
        return len(stuck)
