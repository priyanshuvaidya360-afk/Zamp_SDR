"""Record the real models on the crowded-hook case (case 5) through stages 4-7.

  cd backend && .venv/bin/python -m scripts.record_crowded_case

Search is a stand-in (tests/crowded_case.py), so this costs no search credits, only LLM calls.
Writes tests/fixtures/crowded_case/*.json (committed); tests/test_crowded.py replays them.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

from sqlmodel import Session, select

from app import db
from app.config import settings
from app.models import DraftRecord, Run, SignalRecord
from app.pipeline import research, runner
from tests import crowded_case
from tests.extraction_case import TODAY


async def main() -> None:
    shutil.rmtree(crowded_case.FIXTURES, ignore_errors=True)
    settings.demo_mode, settings.today, settings.fixtures_dir = "record", TODAY, crowded_case.FIXTURES
    runner.get_stages = crowded_case.pipeline_stages
    research.second_order = crowded_case.fake_second_order
    # A fresh database: prompts number signals per run, but ids must still match on replay.
    workdir = Path(tempfile.mkdtemp(prefix="signal-crowded-"))
    db.engine = db.make_engine(f"sqlite:///{workdir / 'run.db'}")
    db.create_db_and_tables()
    runner._batch_contexts.clear()
    with Session(db.engine) as session:
        [run_id] = crowded_case.make_batch(session)
    await runner.run_one(run_id)
    with Session(db.engine) as session:
        run = session.get(Run, run_id)
        print(f"\n=== {run.status.value} (confidence {run.confidence}) {run.final_reason or ''}")
        for s in session.exec(select(SignalRecord).where(SignalRecord.run_id == run_id)).all():
            sc = s.scores or {}
            print(f"[{'HOOK' if s.selected else '    '}] {s.type:18} cov={s.coverage} score={sc.get('score')} "
                  f"crowded={sc.get('crowded')} | {s.summary[:70]} | {s.rejected_reason or ''}")
        for d in session.exec(select(DraftRecord).where(DraftRecord.run_id == run_id).order_by(DraftRecord.version)).all():
            print(f"--- v{d.version} subject: {d.subject}\n{d.body}")
            print("claims:", [(c['signal_id'], c['sentence'][:60]) for c in d.claims])
            print("problems:", d.critic_report.get("problems"))
    print(f"\n{len(list(crowded_case.FIXTURES.glob('*.json')))} fixtures in {crowded_case.FIXTURES}")


if __name__ == "__main__":
    asyncio.run(main())
