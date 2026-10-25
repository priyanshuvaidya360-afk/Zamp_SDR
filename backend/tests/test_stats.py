"""T16: dashboard numbers. Every figure is checked against a SQL query written by hand."""

from datetime import timedelta

from sqlalchemy import text

from app.models import Batch, BatchStatus, Run, RunStatus, utcnow

NOW = utcnow()


def seed_runs(session) -> None:
    """Two batches (one uploaded 10 days ago) with runs in most statuses."""
    old = Batch(filename="old.csv", uploaded_at=NOW - timedelta(days=10), status=BatchStatus.done, row_count=3,
                diversity_score=0.71)
    new = Batch(filename="new.csv", uploaded_at=NOW - timedelta(hours=2), status=BatchStatus.running, row_count=8)
    session.add(old)
    session.add(new)
    session.commit()
    plan = [  # (batch, status, started days ago, seconds taken, credits)
        (old, RunStatus.approved, 10, 40, 6),
        (old, RunStatus.rejected, 10, 50, 7),
        (old, RunStatus.low_signal, 10, 30, 5),
        (new, RunStatus.ready_for_review, 0, 20, 4),
        (new, RunStatus.ready_for_review, 0, 25, 4),
        (new, RunStatus.approved, 0, 60, 8),
        (new, RunStatus.needs_input, 0, 10, 2),
        (new, RunStatus.blocked, 0, 1, 0),
        (new, RunStatus.failed, 0, 15, 3),
        (new, RunStatus.running, 0, None, 1),
    ]
    for i, (batch, status, days, seconds, credits) in enumerate(plan, 2):
        started = NOW - timedelta(days=days, minutes=5)
        session.add(Run(batch_id=batch.id, row_number=i, first_name=f"P{i}", last_name="Q", full_name=f"Person {i}",
                        company="Acme" if i % 2 else "Globex", status=status, started_at=started,
                        finished_at=started + timedelta(seconds=seconds) if seconds is not None else None,
                        cost_credits=credits))
    session.commit()


def sql(session, query: str):
    return session.exec(text(query)).one()[0]


def test_every_number_matches_a_hand_written_query(client, session):
    seed_runs(session)
    stats = client.get("/stats").json()
    week = f"'{(NOW - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')}'"

    assert stats["batches_this_week"] == sql(session, f"select count(*) from batches where uploaded_at >= {week}") == 1
    assert stats["prospects_this_week"] == sql(session, f"select count(*) from runs where started_at >= {week}") == 7
    finished = sql(session, "select count(*) from runs where status != 'running'")
    reached = sql(session, "select count(*) from runs where status in ('ready_for_review','approved','rejected')")
    assert (stats["runs_finished"], stats["reached_review"]) == (finished, reached) == (9, 5)
    assert stats["ready_for_review_rate"] == round(reached / finished, 4)
    approved = sql(session, "select count(*) from runs where status = 'approved'")
    rejected = sql(session, "select count(*) from runs where status = 'rejected'")
    assert stats["approval_rate"] == round(approved / (approved + rejected), 4) == 0.6667
    assert stats["avg_credits_per_prospect"] == round(
        sql(session, "select avg(cost_credits) from runs where status != 'running'"), 2) == 4.33
    assert stats["median_run_seconds"] == 25.0  # 1, 10, 15, 20, 25, 30, 40, 50, 60 seconds
    for status, n in stats["not_ready"].items():
        assert n == sql(session, f"select count(*) from runs where status = '{status}'"), status
    assert stats["not_ready"] == {"needs_input": 1, "low_signal": 1, "blocked": 1, "suppressed": 0, "held": 0, "failed": 1}


def test_empty_database_has_no_rates(client):
    stats = client.get("/stats").json()
    assert stats["runs_total"] == 0 and stats["approval_rate"] is None and stats["median_run_seconds"] is None


def test_batches_list_is_paginated_with_outcome_counts(client, session):
    seed_runs(session)
    page = client.get("/batches?limit=1").json()
    assert page["total"] == 2 and [b["filename"] for b in page["items"]] == ["new.csv"]
    new = page["items"][0]
    assert (new["runs"], new["done"]) == (7, 6)
    assert new["counts"] == {"ready_for_review": 2, "approved": 1, "needs_input": 1, "blocked": 1, "failed": 1, "running": 1}
    old = client.get("/batches?limit=1&offset=1").json()["items"][0]
    assert old["filename"] == "old.csv" and old["diversity_score"] == 0.71


def test_runs_filter_by_status_and_search_by_name(client, session):
    seed_runs(session)
    ready = client.get("/runs?status=ready_for_review").json()
    assert ready["total"] == 2 and {r["status"] for r in ready["items"]} == {"ready_for_review"}
    assert client.get("/runs?q=person 5").json()["total"] == 1
    globex = client.get("/runs?q=globex&limit=2").json()  # matches the company too
    assert globex["total"] == 5 and len(globex["items"]) == 2
    page = client.get("/runs?limit=3&offset=3").json()
    assert page["total"] == 10 and len(page["items"]) == 3


def test_signal_level_reflects_what_the_research_found(session, client):
    from app.models import SignalRecord, StageEvent, StageState
    from app.routes.runs import signal_levels

    batch = Batch(filename="levels.csv", status=BatchStatus.running, row_count=6)
    session.add(batch)
    session.commit()
    runs = [Run(batch_id=batch.id, row_number=i, first_name=f"P{i}", last_name="Q", status=RunStatus.ready_for_review)
            for i in range(2, 7)]
    session.add_all(runs)
    session.commit()
    high, moderate, low, none, unresearched = (r.id for r in runs)

    def signal(run_id, score, selected=False, type_="funding"):
        session.add(SignalRecord(run_id=run_id, type=type_, about="company", summary="s", evidence="e",
                                 source_url="https://x.example", scores={"score": score}, selected=selected))

    signal(high, 3.4, selected=True)
    signal(moderate, 2.1, selected=True)
    signal(moderate, 1.2)
    signal(low, 1.1)  # a fact, but too weak to open with
    signal(none, 0, type_="ai_screening")  # an AI-screening finding is not a usable fact
    for run_id in (high, moderate, low, none):  # research finished for these four, not the fifth
        session.add(StageEvent(run_id=run_id, stage="extract", state=StageState.done))
    session.commit()

    assert signal_levels(session, [r.id for r in runs]) == {high: "high", moderate: "moderate", low: "low", none: "none"}

    # A re-upload stopped before research shows the level of the run it points to, if it has one.
    again = Run(batch_id=batch.id, row_number=7, first_name="P2", last_name="Q", status=RunStatus.blocked)
    waiting = Run(batch_id=batch.id, row_number=8, first_name="P6", last_name="Q", status=RunStatus.blocked)
    session.add_all([again, waiting])
    session.commit()
    for run, earlier in ((again, high), (waiting, unresearched)):
        session.add(StageEvent(run_id=run.id, stage="intake", state=StageState.done,
                               payload={"stopped": "blocked", "earlier_run_id": earlier}))
    session.commit()
    levels = signal_levels(session, [again.id, waiting.id])
    assert levels == {again.id: "high"}  # the earlier run of `waiting` has no level yet
    by_id = {r["id"]: r["signal_level"] for r in client.get(f"/batches/{batch.id}").json()["runs"]}
    assert by_id[high] == "high" and by_id[unresearched] is None
