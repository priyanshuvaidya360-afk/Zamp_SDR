"""T05: batch runner, account clustering, SSE. Spec: docs/GUIDE.md T05."""

import asyncio
import json
import time

import pytest
from sqlmodel import Session, select

from app import ingest
from app.config import settings
from app.models import Batch, BatchStatus, Run, RunStatus, StageEvent, StageState
from app.pipeline import runner
from app.pipeline.runner import Stage, StopRun
from app.pipeline.stubs import STAGES, StubOutput
from app.routes.batches import _store

STAGE_NAMES = [s.name for s in STAGES]


def csv_rows(*rows: str) -> str:
    return "first_name,last_name,company,company_domain\n" + "\n".join(rows) + "\n"


FIVE = csv_rows(
    "Priya,Raman,Acme Fintech,acmefintech.com",
    "Dev,Rao,Acme Fintech,acmefintech.com",
    "Sam,Patel,Contoso,contosologistics.com",
    "Ana,Lima,Northwind,northwind.com",
    "Lee,Wong,Globex,globexretail.com",
)


def make_batch(session: Session, text: str) -> Batch:
    batch = Batch(filename="test.csv")
    _store(batch, ingest.parse_upload(text.encode()))
    session.add(batch)
    session.commit()
    session.refresh(batch)
    return batch


def events_for(session: Session, run_id: int) -> list[tuple[str, str]]:
    rows = session.exec(select(StageEvent).where(StageEvent.run_id == run_id).order_by(StageEvent.id)).all()
    return [(e.stage, e.state.value if hasattr(e.state, "value") else e.state) for e in rows]


def stage(name: str, fn) -> Stage:
    return Stage(name, name.title(), fn, StubOutput)


async def ok(ctx):
    return StubOutput(message="ok")


# --- Runner ---------------------------------------------------------------------------


async def test_five_row_batch_runs_end_to_end_in_stage_order(session):
    batch = make_batch(session, FIVE)
    run_ids = runner.create_runs(session, batch)
    assert len(run_ids) == 5
    await runner.run_batch(run_ids)

    session.expire_all()
    expected = [(name, state) for name in STAGE_NAMES for state in ("started", "done")]
    for run_id in run_ids:
        assert events_for(session, run_id) == expected
        run = session.get(Run, run_id)
        assert run.status == RunStatus.ready_for_review and run.finished_at is not None
    assert session.get(Batch, batch.id).status == BatchStatus.done

    done = session.exec(select(StageEvent).where(StageEvent.state == StageState.done)).first()
    assert done.duration_ms is not None and done.message.startswith("Stub")
    assert set(done.payload) >= {"output", "tokens", "credits"}


async def test_account_keys_group_people_at_the_same_company(session):
    batch = make_batch(session, csv_rows(
        "Priya,Raman,Acme Fintech,acmefintech.com",
        "Dev,Rao,Acme Fintech,www.acmefintech.com",  # same company after cleaning
        "Ana,Lima,Northwind Traders Inc.,northwind.com",
    ))
    runner.create_runs(session, batch)
    keys = [r.account_key for r in session.exec(select(Run).order_by(Run.row_number))]
    assert keys == ["acmefintech.com", "acmefintech.com", "northwind.com"]
    assert runner.account_key_for({"company": "Northwind Traders Inc."}) == "name:northwind-traders"
    assert runner.account_key_for({"linkedin_url": "linkedin.com/in/x"}) is None


async def test_concurrency_never_exceeds_the_cap(session, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_runs", 2)
    runner._semaphores.clear()
    active, peak = 0, 0

    async def busy(ctx):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.02)
        active -= 1
        return StubOutput(message="ok")

    monkeypatch.setattr(runner, "get_stages", lambda: [stage("busy", busy)])
    text = csv_rows(*(f"P{i},Q{i},Co{i},co{i}.com" for i in range(7)))
    await runner.run_batch(runner.create_runs(session, make_batch(session, text)))
    assert peak == 2
    runner._semaphores.clear()


async def test_one_failing_run_does_not_stop_the_batch(session, monkeypatch):
    async def flaky(ctx):
        if ctx.row["first_name"] == "Dev":
            raise RuntimeError("Tavily returned 502")
        return StubOutput(message="ok")

    monkeypatch.setattr(runner, "get_stages", lambda: [stage("intake", ok), stage("extract", flaky), stage("draft", ok)])
    batch = make_batch(session, FIVE)
    run_ids = runner.create_runs(session, batch)
    await runner.run_batch(run_ids)

    session.expire_all()
    runs = {r.first_name: r for r in session.exec(select(Run))}
    assert runs["Dev"].status == RunStatus.failed
    assert runs["Dev"].final_reason == "Extract failed: Tavily returned 502"
    assert events_for(session, runs["Dev"].id) == [
        ("intake", "started"), ("intake", "done"), ("extract", "started"), ("extract", "failed"),
        ("draft", "skipped"),
    ]
    assert all(r.status == RunStatus.ready_for_review for n, r in runs.items() if n != "Dev")
    assert session.get(Batch, batch.id).status == BatchStatus.done


async def test_stop_run_sets_status_and_skips_the_rest(session, monkeypatch):
    async def resolve(ctx):
        raise StopRun(RunStatus.needs_input, "2 people match; pick one", {"candidates": ["a", "b"]})

    monkeypatch.setattr(runner, "get_stages", lambda: [stage("intake", ok), stage("resolve", resolve), stage("gather", ok)])
    batch = make_batch(session, csv_rows("Rahul,Sharma,Infosys,infosys.com"))
    [run_id] = runner.create_runs(session, batch)
    await runner.run_batch([run_id])

    session.expire_all()
    run = session.get(Run, run_id)
    assert (run.status, run.final_reason) == (RunStatus.needs_input, "2 people match; pick one")
    assert events_for(session, run_id)[-2:] == [("resolve", "done"), ("gather", "skipped")]
    resolve_done = session.exec(select(StageEvent).where(StageEvent.stage == "resolve",
                                                         StageEvent.state == StageState.done)).one()
    assert resolve_done.payload["stopped"] == "needs_input"
    assert resolve_done.payload["candidates"] == ["a", "b"]


async def test_company_research_is_shared_within_an_account(session, monkeypatch):
    calls = []

    async def gather(ctx):
        async def research():
            calls.append(ctx.account_key)
            await asyncio.sleep(0.02)
            return {"for": ctx.account_key}

        shared = await ctx.batch.company_research(ctx.account_key, research)
        return StubOutput(message="ok", data=shared)

    monkeypatch.setattr(runner, "get_stages", lambda: [stage("gather", gather)])
    await runner.run_batch(runner.create_runs(session, make_batch(session, FIVE)))
    # Priya and Dev share acmefintech.com: 5 people, 4 companies, 4 research calls.
    assert sorted(calls) == sorted(["acmefintech.com", "contosologistics.com", "northwind.com", "globexretail.com"])


async def test_resume_continues_from_the_stage_with_earlier_outputs_restored(session, monkeypatch):
    seen = {}

    async def intake(ctx):
        return StubOutput(message="intake ok", data={"checked": True})

    async def resolve(ctx):
        if not ctx.resume_input:
            raise StopRun(RunStatus.needs_input, "pick one")
        seen["intake"] = ctx.outputs["intake"].data
        seen["picked"] = ctx.resume_input["candidate"]
        return StubOutput(message="resolved")

    monkeypatch.setattr(runner, "get_stages", lambda: [stage("intake", intake), stage("resolve", resolve), stage("gather", ok)])
    [run_id] = runner.create_runs(session, make_batch(session, csv_rows("Rahul,Sharma,Infosys,infosys.com")))
    await runner.run_batch([run_id])
    await runner.run_one(run_id, start_stage="resolve", resume_input={"candidate": 1})

    session.expire_all()
    assert session.get(Run, run_id).status == RunStatus.ready_for_review
    assert seen == {"intake": {"checked": True}, "picked": 1}
    assert events_for(session, run_id)[-4:] == [
        ("resolve", "started"), ("resolve", "done"), ("gather", "started"), ("gather", "done"),
    ]


def test_restart_recovery_fails_stuck_runs(session):
    batch = make_batch(session, FIVE)
    runner.create_runs(session, batch)  # all 'running', nothing executing them
    assert runner.recover_interrupted() == 5
    session.expire_all()
    assert {r.status for r in session.exec(select(Run))} == {RunStatus.failed}
    assert "server restart" in session.exec(select(Run)).first().final_reason
    assert session.get(Batch, batch.id).status == BatchStatus.done


# --- API + SSE ------------------------------------------------------------------------


def upload_and_start(client, text: str = FIVE) -> dict:
    batch_id = client.post("/batches", files={"file": ("p.csv", text.encode(), "text/csv")}).json()["batch_id"]
    response = client.post(f"/batches/{batch_id}/start")
    assert response.status_code == 202
    return response.json()


def wait_until_done(client, batch_id: int, timeout: float = 10) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/batches/{batch_id}").json()
        if body["batch"]["status"] == "done":
            return body
        time.sleep(0.02)
    raise AssertionError("batch did not finish")


def sse_events(client, url: str) -> list[tuple[str, dict]]:
    events = []
    with client.stream("GET", url) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        kind = None
        for line in response.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                events.append((kind, json.loads(line[6:])))
    return events


def test_start_runs_the_batch_and_get_batch_reports_it(client):
    started = upload_and_start(client, FIVE + "Bad,,Acme,acme.com\n")
    assert len(started["run_ids"]) == 5
    body = wait_until_done(client, started["batch_id"])
    assert body["progress"] == {"type": "batch_progress", "batch_id": started["batch_id"], "done": 5,
                                "total": 5, "counts": {"ready_for_review": 5}}
    assert {r["current_stage"] for r in body["runs"]} == {"critic"}
    assert body["not_run"] == [{"row_number": 7, "status": "invalid", "reason": "missing last name", "name": "Bad"}]


def test_start_is_refused_twice_and_without_valid_rows(client):
    started = upload_and_start(client)
    assert client.post(f"/batches/{started['batch_id']}/start").status_code == 409
    empty = client.post("/batches", files={"file": ("p.csv", b"first_name,last_name,company_domain\nA,,a.com\n", "text/csv")}).json()
    assert client.post(f"/batches/{empty['batch_id']}/start").status_code == 422


def test_batch_sse_streams_progress_until_done(client):
    started = upload_and_start(client)
    events = sse_events(client, f"/batches/{started['batch_id']}/events")
    kinds = [k for k, _ in events]
    assert kinds[0] == "batch_progress" and kinds[-1] == "end"
    finals = [e for k, e in events if k == "batch_progress"]
    assert finals[-1]["done"] == finals[-1]["total"] == 5
    assert all("payload" not in e or e["payload"] == {} for k, e in events if k == "stage")  # slim on batch topic


def test_run_sse_replays_stored_events_then_ends(client):
    started = upload_and_start(client)
    wait_until_done(client, started["batch_id"])
    run_id = started["run_ids"][0]
    events = sse_events(client, f"/runs/{run_id}/events")
    stages = [(e["stage"], e["state"]) for k, e in events if k == "stage"]
    assert stages == [(n, s) for n in STAGE_NAMES for s in ("started", "done")]
    assert events[-1] == ("end", {"type": "end", "run_id": run_id, "status": "ready_for_review"})

    # Reconnect after the 4th event: only the rest is replayed.
    fourth = [e for k, e in events if k == "stage"][3]["id"]
    resumed = sse_events(client, f"/runs/{run_id}/events?after={fourth}")
    assert len([1 for k, _ in resumed if k == "stage"]) == len(stages) - 4


def test_run_sse_streams_a_live_run(client, monkeypatch):
    monkeypatch.setattr(settings, "stub_stage_delay_s", 0.05)
    started = upload_and_start(client, csv_rows("Priya,Raman,Acme,acme.com"))
    events = sse_events(client, f"/runs/{started['run_ids'][0]}/events")
    kinds = [k for k, _ in events]
    assert kinds.count("stage") == 2 * len(STAGE_NAMES)
    assert ("run_status", "ready_for_review") in [(k, e.get("status")) for k, e in events]
    assert kinds[-1] == "end"


def test_runs_list_filters_and_detail(client):
    started = upload_and_start(client)
    wait_until_done(client, started["batch_id"])
    listed = client.get("/runs", params={"batch_id": started["batch_id"], "q": "acme"}).json()
    assert listed["total"] == 2 and {r["name"] for r in listed["items"]} == {"Priya Raman", "Dev Rao"}
    assert client.get("/runs", params={"status": "failed"}).json()["total"] == 0

    detail = client.get(f"/runs/{started['run_ids'][0]}").json()
    assert [s["name"] for s in detail["stages"]] == STAGE_NAMES
    assert len(detail["events"]) == 2 * len(STAGE_NAMES)
    assert detail["signals"] == [] and detail["drafts"] == []
    assert client.get("/runs/999").status_code == 404


def test_resume_endpoint_guards(client):
    started = upload_and_start(client)
    wait_until_done(client, started["batch_id"])
    run_id = started["run_ids"][0]
    assert client.post(f"/runs/{run_id}/resume", json={"stage": "resolve"}).status_code == 409
