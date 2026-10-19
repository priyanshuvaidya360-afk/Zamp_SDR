"""Run chosen CSV rows through the pipeline (optionally up to one stage) and dump the outputs.

Used to record fixtures and to check that replaying them reproduces the same stage outputs:

  DEMO_MODE=record   .venv/bin/python -m scripts.run_prospects prospects.csv --names "Amy Asali" "Ryan Yu" --until gather --out rec.json
  DEMO_MODE=fixtures .venv/bin/python -m scripts.run_prospects prospects.csv --names "Amy Asali" "Ryan Yu" --until gather --out rep.json

Runs against a throwaway SQLite file, never the dev database. The "recently researched"
block is off, so the same people can be run again.
"""

import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from sqlmodel import Session, select

from app import db, ingest
from app.config import settings
from app.models import Batch, Run, StageEvent
from app.pipeline import runner
from app.routes.batches import _store
from scripts.seed_context import seed


def load_rows(path: Path, names: list[str]) -> ingest.ParseResult:
    headers, rows = _read_uncapped(path)
    mapping = ingest.detect_mapping(headers)
    def full(r):
        return f"{r.get(mapping['first_name'] or '', '')} {r.get(mapping['last_name'] or '', '')}".strip().lower()

    wanted = [r for r in rows if not names or any(full(r) == n.lower() for n in names)]
    return ingest.ParseResult(headers=headers, source_rows=wanted, mapping=mapping,
                              rows=ingest.validate_rows(wanted, mapping))


def _read_uncapped(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """read_csv without the 50-row cap: we pick a few rows out of a bigger export."""
    cap = ingest.MAX_ROWS
    ingest.MAX_ROWS = 10_000
    try:
        return ingest.read_csv(path.read_bytes(), path.name)
    finally:
        ingest.MAX_ROWS = cap


def stage_outputs(session: Session, run: Run) -> dict:
    events = session.exec(select(StageEvent).where(StageEvent.run_id == run.id).order_by(StageEvent.id)).all()
    stages = {}
    for e in events:
        if e.state.value in ("done", "failed"):
            stages[e.stage] = {"state": e.state.value, "message": e.message, "payload": e.payload}
    return {"name": run.full_name, "company": run.company, "status": run.status.value,
            "final_reason": run.final_reason, "confidence": run.confidence, "stages": stages}


async def main(args: argparse.Namespace) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="signal-run-"))
    db.engine = db.make_engine(f"sqlite:///{workdir / 'run.db'}")
    db.create_db_and_tables()
    settings.recent_run_days = 0
    if args.until:
        stages = runner.get_stages()
        names = [s.name for s in stages]
        cut = names.index(args.until) + 1
        runner.get_stages = lambda: stages[:cut]

    with Session(db.engine) as session:
        seed(session)
        parsed = load_rows(Path(args.csv), args.names)
        batch = Batch(filename=Path(args.csv).name)
        _store(batch, parsed)
        session.add(batch)
        session.commit()
        run_ids = runner.create_runs(session, batch)

    await runner.run_batch(run_ids)

    with Session(db.engine) as session:
        runs = session.exec(select(Run).order_by(Run.row_number)).all()
        return {"demo_mode": settings.demo_mode, "runs": [stage_outputs(session, r) for r in runs]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("csv")
    parser.add_argument("--names", nargs="*", default=[], help='full names to pick, e.g. "Amy Asali" (default: all)')
    parser.add_argument("--until", help="last stage to run, e.g. gather")
    parser.add_argument("--out", help="write the outputs as JSON here")
    result = asyncio.run(main(parser.parse_args()))
    text = json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if parser.parse_args().out:
        Path(parser.parse_args().out).write_text(text)
    for run in result["runs"]:
        print(f"\n{run['name']} ({run['company']}): {run['status']}"
              + (f" — {run['final_reason']}" if run["final_reason"] else ""))
        for name, stage in run["stages"].items():
            print(f"  {name:<8} {stage['state']:<6} {stage['message']}")
