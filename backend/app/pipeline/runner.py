"""Batch runner: runs each prospect through the stages and streams every step.

Spec: docs/PROJECT_SPEC.md "Pipeline stages in detail" (batch layer) and T05.

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
from datetime import timedelta, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import update
from sqlmodel import Session, select

from app import db
from app.config import settings
from app.events import bus
from app.models import Batch, BatchStatus, Run, RunStatus, SignalRecord, StageEvent, StageState, utcnow
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
    releases_slot: bool = False  # waits on other runs (account plan): give up the concurrency slot


class BatchContext:
    """State shared by every run in one batch: company research is done once per account."""

    def __init__(self, batch_id: int):
        self.batch_id = batch_id
        self._research: dict[str, asyncio.Future] = {}
        self.accounts: dict[str, AccountGroup] = {}
        self.running_batch = False  # True while run_batch runs: the batch is not "done" until its batch-level step

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


class AccountGroup:
    """Barrier for a buying committee (case 3): everyone at one company who is still running
    waits here until each colleague has arrived or stopped earlier (needs_input, held...)."""

    def __init__(self, expected: set[int]):
        self.expected = expected
        self.arrived: dict[int, Any] = {}
        self.left: set[int] = set()
        self.complete = asyncio.Event()
        self._check()

    def arrive(self, run_id: int, payload: Any) -> None:
        self.arrived[run_id] = payload
        self._check()

    def leave(self, run_id: int) -> None:
        if run_id not in self.arrived:
            self.left.add(run_id)
            self._check()

    def _check(self) -> None:
        if self.expected <= set(self.arrived) | self.left:
            self.complete.set()


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


def create_runs(session: Session, batch: Batch, row_numbers: set[int] | None = None) -> list[int]:
    """One run per valid preview row (invalid / duplicate rows stay in batch.rows).

    `row_numbers` limits it to those rows (the rep drafts prospects one at a time); rows that
    already have a run are skipped, so asking twice never researches a prospect twice."""
    run_ids = []
    existing = set(session.exec(select(Run.row_number).where(Run.batch_id == batch.id)).all())
    for row in batch.rows:
        if row["status"] != "valid" or row["row_number"] in existing:
            continue
        if row_numbers is not None and row["row_number"] not in row_numbers:
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
    _save_event(ctx.run_id, ctx.batch_id, stage.name, state, message, payload, duration_ms)


def _save_event(run_id: int, batch_id: int, stage: str, state: StageState, message: str = "",
                payload: dict[str, Any] | None = None, duration_ms: int | None = None) -> None:
    """Save the event, then publish it (full on the run topic, slim on the batch topic)."""
    with Session(db.engine) as session:
        event = StageEvent(run_id=run_id, stage=stage, state=state, message=message,
                           payload=payload or {}, duration_ms=duration_ms)
        session.add(event)
        session.commit()
        session.refresh(event)
        data = stage_event_dict(event, batch_id)
    bus.publish(f"run:{run_id}", data)
    bus.publish(f"batch:{batch_id}", {**data, "payload": {}})


def publish_status(run: Run) -> None:
    event = {
        "type": "run_status",
        "run_id": run.id,
        "batch_id": run.batch_id,
        "status": run.status.value,
        "final_reason": run.final_reason,
        "identity_outcome": run.identity_outcome,
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
    if type(error).__name__ == "FixtureMissing":
        return "no recorded research for this prospect (replay mode is on, so nothing live was called)"
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
        publish_status(run)
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
    _restore_context(ctx)


def _restore_context(ctx: RunContext) -> None:
    """What restored stages had set on the context besides their output: the identity confidence
    (halved by a screening trap, case 11) and a low-signal ending (stage 5)."""
    resolved = ctx.outputs.get("resolve")
    if resolved is not None and getattr(resolved, "person", None) is not None:
        ctx.confidence = 1.0 if getattr(resolved, "rep_confirmed", False) else resolved.person.confidence
        if getattr(ctx.outputs.get("extract"), "ai_screening", None) == "screening_trap":
            ctx.confidence = round(ctx.confidence * 0.5, 2)
    scored = ctx.outputs.get("score")
    if getattr(scored, "low_signal", False):
        ctx.final_status = RunStatus.low_signal
        ctx.final_reason = scored.message


async def run_one(run_id: int, start_stage: str | None = None,
                  resume_input: dict[str, Any] | None = None) -> None:
    """Run one prospect through the stages. Never raises: failures become run statuses."""
    stages = get_stages()
    slot = _semaphore()
    await slot.acquire()
    try:
        ctx = _load_context(run_id, resume_input)
        start = 0
        if start_stage:
            start = next(i for i, s in enumerate(stages) if s.name == start_stage)
            _restore_outputs(ctx, stages[:start])

        from app.pipeline.graph import run_graph  # the graph module imports this one

        result = await run_graph(stages, ctx, slot, start)
        status, reason = result["status"], result["reason"]

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
            publish_status(run)
            batch_id = run.batch_id
        _leave_account(ctx)
    finally:
        slot.release()
    update_batch_progress(batch_id)


async def _call_stage(stage: Stage, ctx: RunContext, slot: asyncio.Semaphore) -> BaseModel:
    if not stage.releases_slot:
        return await stage.fn(ctx)
    slot.release()  # waiting on colleagues must not block runs that are queued for a slot
    try:
        return await stage.fn(ctx)
    finally:
        await slot.acquire()


def _leave_account(ctx: RunContext) -> None:
    """A run that ends without reaching its account's barrier must not keep colleagues waiting."""
    group = ctx.batch.accounts.get(ctx.account_key or "")
    if group is not None:
        group.leave(ctx.run_id)


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
    """Publish progress; mark the batch done once no run is still running (and, during a batch
    run, only after its batch-level step: the similarity check)."""
    with Session(db.engine) as session:
        progress = batch_progress(session, batch_id)
        bus.publish(f"batch:{batch_id}", progress)
        batch = session.get(Batch, batch_id)
        if batch_context(batch_id).running_batch:
            return
        # Prospects can be drafted one at a time: the batch is done once every valid row has been.
        if progress["done"] == progress["total"] >= batch.valid_count and batch.status != BatchStatus.done:
            batch.status = BatchStatus.done
            batch.finished_at = utcnow()
            session.add(batch)
            session.commit()
            bus.publish(f"batch:{batch_id}", {"type": "batch_status", "batch_id": batch_id, "status": "done"})


async def run_batch(run_ids: list[int]) -> None:
    """Run every prospect, then the batch-level step (case 9: similarity + hook-source cap)."""
    from app.pipeline.similarity import check_batch

    if not run_ids:
        return
    with Session(db.engine) as session:
        batch_id = session.get(Run, run_ids[0]).batch_id
    context = batch_context(batch_id)
    context.running_batch = True
    try:
        await asyncio.gather(*(run_one(run_id) for run_id in run_ids), return_exceptions=True)
        try:
            await check_batch(batch_id)
        except Exception:  # never leave a batch stuck "running" because of the batch-level step
            log.exception("similarity check failed for batch %s", batch_id)
    finally:
        context.running_batch = False
        update_batch_progress(batch_id)


# --- Retrying a failed run ---------------------------------------------------------------

RESEARCH_STAGES = {"gather", "extract"}  # web research: redone once older than retry_research_max_age_hours


class RetryRefused(Exception):
    """The run can't be retried now; the message says why, for the rep."""


@dataclass
class RetryPlan:
    start_stage: str
    reused: list[str]  # stages whose saved output is used as is
    refreshed: list[str]  # reusable on their own, but redone because they are stale


def _aware(ts):
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _reusable(session: Session, run_id: int, stage: Stage, event: StageEvent | None) -> str | None:
    """None if the stage's saved result can be used again, else why not."""
    if event is None or event.state != StageState.done:
        return "did not finish"
    if event.payload.get("stopped") or "output" not in event.payload:
        return "no saved result"
    try:
        output = stage.output.model_validate(event.payload["output"])
    except ValidationError:
        return "saved result is unreadable"
    if stage.name == "extract":
        ids = {s.id for s in getattr(output, "signals", [])}
        stored = set(session.exec(select(SignalRecord.id).where(SignalRecord.run_id == run_id)).all())
        if not ids <= stored:
            return "stored signals are missing"
    return None


def plan_retry(session: Session, run_id: int) -> RetryPlan:
    """Restart at the first stage whose saved result is missing, unreadable or stale research;
    every stage before it is restored from its saved 'done' event (as a resume does)."""
    stages = get_stages()
    max_age = timedelta(hours=settings.retry_research_max_age_hours)
    reused: list[str] = []
    for i, stage in enumerate(stages):
        event = session.exec(
            select(StageEvent).where(StageEvent.run_id == run_id, StageEvent.stage == stage.name)
            .order_by(StageEvent.id.desc())
        ).first()
        if _reusable(session, run_id, stage, event) is not None:
            return RetryPlan(stage.name, reused, [])
        if stage.name in RESEARCH_STAGES and utcnow() - _aware(event.ts) > max_age:
            # Stale research: redo it and everything after (later stages read it).
            refreshed = [s.name for s in stages[i:] if s.name in RESEARCH_STAGES]
            return RetryPlan(stage.name, reused, refreshed)
        reused.append(stage.name)
    return RetryPlan(stages[-1].name, reused[:-1], [])  # every stage finished: redo the last one


def claim_retry(run_id: int) -> tuple[int, str | None]:
    """Atomically move a failed run back to running (one UPDATE guarded by status and the retry
    cap, so two simultaneous requests can't both start it). Returns (attempt, previous error)."""
    with Session(db.engine) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise LookupError(f"Run {run_id} not found.")
        previous_error = run.final_reason
    with db.engine.begin() as connection:
        claimed = connection.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == RunStatus.failed,
                   Run.retry_count < settings.retry_max_attempts)
            .values(status=RunStatus.running, retry_count=Run.retry_count + 1,
                    final_reason=None, finished_at=None)
        ).rowcount
    with Session(db.engine) as session:
        run = session.get(Run, run_id)
        if claimed:
            return run.retry_count, previous_error
        if run.status == RunStatus.running:
            raise RetryRefused("This run is already running.")
        if run.status != RunStatus.failed:
            label = run.status.value.replace("_", " ")
            raise RetryRefused(f"This run is {label}; only failed runs can be retried.")
        raise RetryRefused(f"This run was retried {run.retry_count} times already (the limit is "
                           f"{settings.retry_max_attempts}). Upload the prospect again to start fresh.")


def start_retry(run_id: int) -> dict[str, Any]:
    """Claim the run, decide where to restart, record the retry as a stage event, then run it in
    the background. Raises LookupError / RetryRefused."""
    with Session(db.engine) as session:
        plan = plan_retry(session, run_id)  # read-only, so it is safe to work out before claiming
    attempt, previous_error = claim_retry(run_id)
    with Session(db.engine) as session:
        run = session.get(Run, run_id)
        batch_id = run.batch_id
        publish_status(run)
    research_reused = any(s in RESEARCH_STAGES for s in plan.reused)
    parts = [f"Retry {attempt} of {settings.retry_max_attempts}", f"restarting at {plan.start_stage}"]
    if plan.refreshed:
        parts.append("saved research is older than "
                     f"{settings.retry_research_max_age_hours:g} h, searching again")
    elif research_reused:
        parts.append("reusing saved research")
    _save_event(run_id, batch_id, "retry", StageState.done, " · ".join(parts), {
        "attempt": attempt, "max_attempts": settings.retry_max_attempts, "start_stage": plan.start_stage,
        "reused": plan.reused, "refreshed": plan.refreshed, "previous_error": previous_error,
    })
    spawn(retry_one(run_id, plan.start_stage))
    return {"run_id": run_id, "status": RunStatus.running.value, "attempt": attempt,
            "max_attempts": settings.retry_max_attempts, "start_stage": plan.start_stage,
            "reused_stages": plan.reused, "research_reused": research_reused}


async def retry_one(run_id: int, start_stage: str) -> None:
    """Run a retried prospect from `start_stage`, then the batch-level step, as run_batch does."""
    from app.pipeline.similarity import check_batch

    await run_one(run_id, start_stage=start_stage)
    with Session(db.engine) as session:
        batch_id = session.get(Run, run_id).batch_id
    try:
        await check_batch(batch_id)
    except Exception:
        log.exception("similarity check failed for batch %s", batch_id)
    update_batch_progress(batch_id)


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
