"""Record the real model's answers for the T07 extraction case (tests/extraction_case.py).

  cd backend && .venv/bin/python -m scripts.record_extraction

Writes tests/fixtures/extraction/*.json (committed). tests/test_extract.py replays them, so the
tests check the real prompts and models, offline. Re-record after changing a stage-4 prompt.
"""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path

from sqlmodel import Session

from app import db
from app.config import settings
from app.pipeline import extract
from tests.extraction_case import FIXTURES, TODAY, make_context


async def main() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="signal-extract-"))
    db.engine = db.make_engine(f"sqlite:///{workdir / 'run.db'}")
    db.create_db_and_tables()
    shutil.rmtree(FIXTURES, ignore_errors=True)
    settings.demo_mode = "record"
    settings.fixtures_dir = FIXTURES
    settings.today = TODAY

    with Session(db.engine) as session:
        ctx = make_context(session)
    out = await extract.run(ctx)

    print(out.message, f"\nconfidence: 0.9 -> {ctx.confidence}\n")
    for hit in out.instruction_hits:
        print(f"HIT [{hit.label}] ({hit.origin}) {hit.text[:90]}")
    for s in out.signals:
        print(f"SIG {s.type:<17} {s.about:<7} cov={s.coverage} ghost={s.ghost_suspect} "
              f"speaker={s.speaker} reshare={s.is_reshare} {s.published_at} | {s.summary[:70]}"
              + (f"\n    note: {s.note}" if s.note else ""))
    for d in out.dropped:
        print(f"DROPPED {d.reason}: {d.summary[:80]}")
    print(f"\n{len(list(FIXTURES.glob('*.json')))} fixtures written to {FIXTURES}")
    (workdir / "output.json").write_text(json.dumps(out.model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
