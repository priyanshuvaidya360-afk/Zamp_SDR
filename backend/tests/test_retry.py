"""Retrying a failed run (POST /runs/{id}/retry) without re-uploading the CSV."""

import asyncio
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.config import settings
from app.models import Run, RunStatus, StageEvent, StageState, utcnow
from app.pipeline import runner, stubs
from app.pipeline.resolve import Candidate, ResolveOutput
from app.pipeline.score import ScoreOutput
from tests.test_runner import FIVE, csv_rows, events_for, make_batch

ONE = csv_rows("Priya,Raman,Acme Fintech,acmefintech.com")


def flaky_stages(fail_in: str, failures: int = 1):
    """The stand-in stages, but `fail_in` raises the first `failures` times; counts every call."""
    calls: dict[str, int] = {}

    def wrap(stage: runner.Stage) -> runner.Stage:
        async def fn(ctx):
            calls[stage.name] = calls.get(stage.name, 0) + 1
            if stage.name == fail_in and calls[stage.name] <= failures:
                raise TimeoutError("provider timed out")
            return await stage.fn(ctx)

        return runner.Stage(stage.name, stage.label, fn, stage.output, stage.releases_slot)

    return [wrap(s) for s in stubs.STAGES], calls


async def settle() -> None:
    while runner._tasks:
        await asyncio.gather(*list(runner._tasks), return_exceptions=True)


async def failed_run(session, monkeypatch, fail_in="extract", failures=1) -> tuple[int, dict]:
    stages, calls = flaky_stages(fail_in, failures)
    monkeypatch.setattr(runner, "get_stages", lambda: stages)
    batch = make_batch(session, ONE)
    run_id = runner.create_runs(session, batch)[0]
    await runner.run_batch([run_id])
    session.expire_all()
    assert session.get(Run, run_id).status == RunStatus.failed
    return run_id, calls


async def test_retry_restarts_at_the_failed_stage_and_reuses_earlier_work(session, monkeypatch):
    run_id, calls = await failed_run(session, monkeypatch, fail_in="extract")

    result = runner.start_retry(run_id)
    await settle()

    assert result["attempt"] == 1 and result["start_stage"] == "extract"
    assert result["reused_stages"] == ["intake", "resolve", "gather"] and result["research_reused"]
    assert calls["gather"] == 1 and calls["extract"] == 2  # research not repeated
    session.expire_all()
    run = session.get(Run, run_id)
    assert run.status == RunStatus.ready_for_review and run.retry_count == 1
    history = events_for(session, run_id)
    assert ("extract", "failed") in history  # the first attempt is kept
    assert ("retry", "done") in history
    assert history[-1] == ("critic", "done")
    retry = session.exec(select(StageEvent).where(StageEvent.run_id == run_id, StageEvent.stage == "retry")).one()
    assert retry.payload["previous_error"].startswith("Extract signals failed")
    assert "reusing saved research" in retry.message


async def test_stale_research_is_searched_again(session, monkeypatch):
    run_id, calls = await failed_run(session, monkeypatch, fail_in="score")
    for event in session.exec(select(StageEvent).where(StageEvent.run_id == run_id)).all():
        event.ts = utcnow() - timedelta(hours=settings.retry_research_max_age_hours + 1)
        session.add(event)
    session.commit()

    result = runner.start_retry(run_id)
    await settle()

    assert result["start_stage"] == "gather" and not result["research_reused"]
    assert calls["gather"] == 2 and calls["resolve"] == 1
    session.expire_all()
    assert session.get(Run, run_id).status == RunStatus.ready_for_review


async def test_simultaneous_retries_start_the_run_once(session, monkeypatch):
    run_id, calls = await failed_run(session, monkeypatch, fail_in="score")

    first = runner.claim_retry(run_id)
    with pytest.raises(runner.RetryRefused, match="already running"):
        runner.claim_retry(run_id)
    assert first[0] == 1
    session.expire_all()
    assert session.get(Run, run_id).retry_count == 1


async def test_retry_limit(session, monkeypatch):
    run_id, _ = await failed_run(session, monkeypatch, fail_in="score", failures=99)
    for attempt in range(1, settings.retry_max_attempts + 1):
        assert runner.start_retry(run_id)["attempt"] == attempt
        await settle()
    with pytest.raises(runner.RetryRefused, match="retried 3 times"):
        runner.start_retry(run_id)


async def test_interrupted_run_is_retried_from_where_it_stopped(session, monkeypatch):
    stages, calls = flaky_stages(fail_in="none")
    monkeypatch.setattr(runner, "get_stages", lambda: stages)
    batch = make_batch(session, ONE)
    run_id = runner.create_runs(session, batch)[0]
    await runner.run_one(run_id)
    # Pretend the server died during score: its last event is "started", the run still "running".
    for event in session.exec(select(StageEvent).where(StageEvent.run_id == run_id,
                                                       StageEvent.stage.in_(["score", "draft", "critic"]))).all():
        session.delete(event)
    session.add(StageEvent(run_id=run_id, stage="score", state=StageState.started))
    run = session.get(Run, run_id)
    run.status = RunStatus.running
    session.add(run)
    session.commit()

    assert runner.recover_interrupted() == 1
    result = runner.start_retry(run_id)
    await settle()

    assert result["start_stage"] == "score"
    session.expire_all()
    assert session.get(Run, run_id).status == RunStatus.ready_for_review


def test_restored_context_keeps_low_signal_and_confidence():
    ctx = runner.RunContext(run_id=1, batch_id=1, row={}, seller={}, batch=runner.BatchContext(1))
    person = Candidate(name="Priya Raman", evidence_url="https://a.example", evidence="x", confidence=0.86)
    ctx.outputs["resolve"] = ResolveOutput(message="", person=person)
    ctx.outputs["score"] = ScoreOutput(message="No usable signal found.", low_signal=True)
    runner._restore_context(ctx)
    assert ctx.confidence == 0.86
    assert ctx.final_status == RunStatus.low_signal and ctx.final_reason == "No usable signal found."


# --- API ---------------------------------------------------------------------------------


def _set_status(engine, run_id: int, status: RunStatus, retry_count: int = 0) -> None:
    with Session(engine) as session:
        run = session.get(Run, run_id)
        run.status, run.retry_count = status, retry_count
        session.add(run)
        session.commit()


@pytest.mark.parametrize("status", [RunStatus.running, RunStatus.approved, RunStatus.ready_for_review,
                                    RunStatus.held, RunStatus.suppressed, RunStatus.needs_input])
def test_retry_endpoint_refuses_runs_that_did_not_fail(client, engine, status):
    batch_id = client.post("/batches", files={"file": ("p.csv", FIVE)}).json()["batch_id"]
    with Session(engine) as session:
        from app.models import Batch
        run_id = runner.create_runs(session, session.get(Batch, batch_id), {2})[0]
    _set_status(engine, run_id, status)
    response = client.post(f"/runs/{run_id}/retry")
    assert response.status_code == 409
    assert "only failed runs" in response.json()["detail"] or "already running" in response.json()["detail"]


def test_retry_endpoint(client, engine):
    batch_id = client.post("/batches", files={"file": ("p.csv", FIVE)}).json()["batch_id"]
    with Session(engine) as session:
        from app.models import Batch
        run_id = runner.create_runs(session, session.get(Batch, batch_id), {2})[0]
    assert client.post("/runs/999/retry").status_code == 404

    _set_status(engine, run_id, RunStatus.failed, retry_count=settings.retry_max_attempts)
    assert "retried 3 times" in client.post(f"/runs/{run_id}/retry").json()["detail"]

    _set_status(engine, run_id, RunStatus.failed)
    response = client.post(f"/runs/{run_id}/retry")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running" and body["attempt"] == 1 and body["start_stage"] == "intake"
    summary = client.get(f"/runs/{run_id}").json()["run"]
    assert summary["retry_count"] == 1 and summary["retries_left"] == settings.retry_max_attempts - 1
