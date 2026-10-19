"""Record the real models on Priya's case through stages 4-7 (extract -> critic).

  cd backend && .venv/bin/python -m scripts.record_pipeline_case

Two variants: without the flan bio (a normal, hooked email) and with it (an outline + banner).
Writes tests/fixtures/pipeline_case/*.json (committed); tests/test_draft.py replays them.
"""

import asyncio
import shutil
import tempfile
from pathlib import Path

from sqlmodel import Session, select

from app import db
from app.config import settings
from app.models import DraftRecord, Run
from app.pipeline import runner
from tests.extraction_case import PIPELINE_FIXTURES, TODAY, make_batch, pipeline_stages, variant_dir


async def main() -> None:
    shutil.rmtree(PIPELINE_FIXTURES, ignore_errors=True)
    settings.demo_mode, settings.today = "record", TODAY
    runner.get_stages = pipeline_stages

    for with_bio in (False, True):
        # One folder per variant: both send identical extraction prompts, so a shared folder
        # would let the second recording overwrite the first's answers.
        settings.fixtures_dir = variant_dir(with_bio)
        # And one fresh database per variant: prompts carry signal ids, which must match the
        # ids a fresh test database will assign on replay.
        workdir = Path(tempfile.mkdtemp(prefix="signal-pipeline-"))
        db.engine = db.make_engine(f"sqlite:///{workdir / 'run.db'}")
        db.create_db_and_tables()
        runner._batch_contexts.clear()  # the new database reuses batch id 1: no stale shared research
        with Session(db.engine) as session:
            [run_id] = make_batch(session, with_bio)
        await runner.run_one(run_id)
        with Session(db.engine) as session:
            run = session.get(Run, run_id)
            drafts = session.exec(select(DraftRecord).where(DraftRecord.run_id == run_id)
                                  .order_by(DraftRecord.version)).all()
            print(f"\n=== {'WITH' if with_bio else 'WITHOUT'} flan bio: {run.status.value} "
                  f"(confidence {run.confidence}) {run.final_reason or ''}")
            for d in drafts:
                report = d.critic_report
                print(f"--- v{d.version} [{report.get('mode')}] subject: {d.subject}\n{d.body}")
                print("claims:", [(c['signal_id'], c['sentence'][:50]) for c in d.claims])
                print("problems:", report.get("problems"), "| genericness:", (report.get("genericness") or {}).get("genericness"))
    print(f"\n{len(list(PIPELINE_FIXTURES.glob('*/*.json')))} fixtures in {PIPELINE_FIXTURES}")


if __name__ == "__main__":
    asyncio.run(main())
