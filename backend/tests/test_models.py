from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.models import (
    Batch,
    CrmAccount,
    DraftRecord,
    Opportunity,
    Run,
    RunStatus,
    SequenceHistory,
    SequenceOutcome,
    SignalRecord,
    StageEvent,
    StageState,
)
from app.schemas import Draft, Signal, SignalScore


def test_insert_and_read_one_row_per_table(session: Session):
    batch = Batch(filename="demo.csv", row_count=1, valid_count=1, column_mapping={"first": "first_name"})
    session.add(batch)
    session.commit()

    run = Run(
        batch_id=batch.id,
        row_number=1,
        raw_row={"first_name": "Priya", "last_name": "Raman"},
        first_name="Priya",
        last_name="Raman",
        company="Acme Fintech",
        domain="acmefintech.com",
        account_key="acmefintech.com",
    )
    session.add(run)
    session.commit()

    session.add(StageEvent(run_id=run.id, stage="resolve", state=StageState.done, payload={"matches": 1}))
    signal = SignalRecord(
        run_id=run.id,
        type="hiring",
        about="company",
        summary="Hiring 4 AP clerks",
        evidence="4 open Accounts Payable roles",
        source_url="https://acmefintech.com/careers",
        published_at=date(2026, 10, 1),
        time_sensitive=True,
        scores={"relevance": 5},
    )
    session.add(signal)
    session.commit()

    session.add(
        DraftRecord(
            run_id=run.id,
            subject="ap hiring at acme",
            body="Saw Acme has four AP roles open.",
            hook_signal_id=signal.id,
            claims=[{"sentence": "Saw Acme has four AP roles open.", "signal_id": signal.id}],
        )
    )
    session.add(CrmAccount(domain="acmefintech.com", name="Acme Fintech", owner="Asha Menon"))
    session.add(Opportunity(account_domain="contosologistics.com", stage=4, owner="Sam Patel"))
    session.add(
        SequenceHistory(
            email="priya.raman@acmefintech.com",
            domain="acmefintech.com",
            sequence="Finance leaders Q2",
            last_touch_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
            outcome=SequenceOutcome.not_now,
        )
    )
    session.commit()
    signal_id = signal.id
    session.expunge_all()  # force reads to come back from the database

    for model in (Batch, Run, StageEvent, SignalRecord, DraftRecord, CrmAccount, Opportunity, SequenceHistory):
        assert len(session.exec(select(model)).all()) == 1, model.__tablename__

    stored_run = session.exec(select(Run)).one()
    assert stored_run.status == RunStatus.running
    assert stored_run.raw_row == {"first_name": "Priya", "last_name": "Raman"}
    assert session.exec(select(DraftRecord)).one().claims[0]["signal_id"] == signal_id
    assert session.exec(select(SequenceHistory)).one().outcome == SequenceOutcome.not_now


def test_run_status_is_the_closed_set_from_the_guide():
    assert {s.value for s in RunStatus} == {
        "running", "needs_input", "low_signal", "ready_for_review", "blocked", "suppressed",
        "held", "approved", "rejected", "failed", "dismissed", "already_drafted", "invalid", "skipped",
    }


def test_schemas_validate_llm_output():
    signal = Signal(
        type="funding",
        about="company",
        summary="Raised a Series C",
        evidence="announced a $40M Series C",
        source_url="https://news.example.com/acme",
        sensitive=False,
    )
    assert signal.coverage == 1 and signal.speaker is None and not signal.is_reshare

    with pytest.raises(ValidationError):
        Signal(type="rumour", about="company", summary="", evidence="", source_url="https://x.io", sensitive=False)
    with pytest.raises(ValidationError):
        SignalScore(relevance=6, specificity=2, reasoning="out of range")

    draft = Draft(subject="hi", body="Body.", claims=[{"sentence": "Body.", "signal_id": 3}])
    assert draft.claims[0].signal_id == 3


def test_old_database_gains_new_columns_with_defaults(tmp_path):
    """A dev database created before a model grew new fields keeps working (no 'no such column')."""
    from sqlalchemy import create_engine, inspect, text

    from app.db import create_db_and_tables

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:  # the signals table as it was before T07 added note / cluster_urls
        c.execute(text("CREATE TABLE signals (id INTEGER PRIMARY KEY, run_id INTEGER, type VARCHAR, about VARCHAR, "
                       "summary VARCHAR, evidence VARCHAR, source_url VARCHAR, published_at DATE, sensitive BOOLEAN, "
                       "speaker VARCHAR, is_reshare BOOLEAN, time_sensitive BOOLEAN, coverage INTEGER, "
                       "ghost_suspect BOOLEAN, scores JSON, selected BOOLEAN, rejected_reason VARCHAR)"))
        c.execute(text("INSERT INTO signals (id, run_id, type, about, summary, evidence, source_url, sensitive, speaker, "
                       "is_reshare, time_sensitive, coverage, ghost_suspect, scores, selected) VALUES "
                       "(1, 1, 'funding', 'company', 's', 'e', 'https://x.io', 0, NULL, 0, 0, 1, 0, '{}', 0)"))
    create_db_and_tables(engine)
    columns = {c["name"] for c in inspect(engine).get_columns("signals")}
    assert {"note", "cluster_urls"} <= columns
    with Session(engine) as session:
        old = session.exec(select(SignalRecord)).one()
        assert old.cluster_urls == [] and old.note is None  # the existing row loads with defaults
    create_db_and_tables(engine)  # running again changes nothing
