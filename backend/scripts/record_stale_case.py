"""Record the real models on the stale-draft case (case 10): stages 4-7, then approve after 12
simulated days (re-check: compare + rewrite), then approve the corrected version.

  cd backend && .venv/bin/python -m scripts.record_stale_case

Search is a stand-in (tests/stale_case.py), so this costs no search credits, only LLM calls.
Writes tests/fixtures/stale_case/*.json (committed); tests/test_review.py replays them.
"""

import asyncio
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from sqlmodel import Session, select

from app import db, review, search
from app.config import settings
from app.fixtures import load_fixture
from app.models import Batch, DraftRecord, ReviewerAction, Run, SignalRecord, utcnow
from app.pipeline import runner
from app.search import ExtractResponse
from tests import stale_case
from tests.extraction_case import TODAY


async def _recorded_extract(urls: list[str], fresh: bool = False) -> ExtractResponse:
    assert fresh and urls == [stale_case.CAREERS], urls
    return ExtractResponse(**load_fixture(stale_case.now_slug()))


async def main() -> None:
    shutil.rmtree(stale_case.FIXTURES, ignore_errors=True)
    settings.demo_mode, settings.today, settings.fixtures_dir = "record", TODAY, stale_case.FIXTURES
    stale_case.write_now_fixture()
    runner.get_stages = stale_case.pipeline_stages
    search.extract = _recorded_extract  # the 'now' page comes from the fixture written above
    workdir = Path(tempfile.mkdtemp(prefix="signal-stale-"))
    db.engine = db.make_engine(f"sqlite:///{workdir / 'run.db'}")
    db.create_db_and_tables()
    runner._batch_contexts.clear()
    with Session(db.engine) as session:
        [run_id] = stale_case.make_batch(session)
    await runner.run_one(run_id)
    with Session(db.engine) as session:
        run = session.get(Run, run_id)
        print(f"\n=== {run.status.value} {run.final_reason or ''}")
        for s in session.exec(select(SignalRecord).where(SignalRecord.run_id == run_id)).all():
            print(f"[{'HOOK' if s.selected else '    '}] {s.type:12} time_sensitive={s.time_sensitive} | {s.summary}")
        draft = review.latest_draft(session, run_id)
        print(f"--- v{draft.version}: {draft.subject}\n{draft.body}\nclaims: {draft.claims}")
        draft.created_at = utcnow() - timedelta(days=12)
        session.add(draft)
        session.commit()
        first = await review.review(session, run_id, ReviewerAction.approve)
        print("\n=== approve after 12 days:", first.outcome, "|", first.message)
        for c in first.checks:
            print(f"  {c.verdict}: {c.reason}\n    before: {c.sentence_before}\n    after:  {c.sentence_after}")
        if first.outcome == "changed":
            second = await review.review(session, run_id, ReviewerAction.approve, draft_id=first.draft_id)
            print("=== approve again:", second.outcome)
        print("\n" + review.export_csv(session, session.get(Batch, run.batch_id)))
    print(f"{len(list(stale_case.FIXTURES.glob('*.json')))} fixtures in {stale_case.FIXTURES}")


if __name__ == "__main__":
    asyncio.run(main())
