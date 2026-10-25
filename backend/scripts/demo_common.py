"""The demo, run end to end: both demo CSVs, then case 10's approve-after-twelve-days.

Shared by scripts/record_demo.py (DEMO_MODE=record, live web + simulated web) and
scripts/check_demo.py (DEMO_MODE=fixtures, network off). Spec: docs/PROJECT_SPEC.md T17.
"""

import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app import db, ingest, review
from app.config import BACKEND_DIR, REPO_DIR, settings
from app.models import Batch, ReviewerAction, Run, SignalRecord
from app.pipeline import runner
from app.routes.batches import _store
from scripts.seed_context import seed

SEED = REPO_DIR / "seed"
CSVS = [SEED / "demo.csv", SEED / "demo_messy.csv"]
CASES = SEED / "demo_cases.json"
FIXTURES = BACKEND_DIR / "tests" / "fixtures" / "demo"
TODAY = date(2026, 10, 19)  # the day the demo was recorded; replays pretend it is still that day
AGED_DAYS = 12  # case 10: the simulated wait in the review queue


def fresh_database() -> None:
    workdir = Path(tempfile.mkdtemp(prefix="signal-demo-"))
    db.engine = db.make_engine(f"sqlite:///{workdir / 'demo.db'}")
    db.create_db_and_tables()
    runner._batch_contexts.clear()
    with Session(db.engine) as session:
        seed(session)


async def run_csv(path: Path) -> list[dict[str, Any]]:
    """Upload + start, as the UI does; returns one result per CSV row (run or not)."""
    with Session(db.engine) as session:
        batch = Batch(filename=path.name)
        _store(batch, ingest.parse_upload(path.read_bytes()))
        session.add(batch)
        session.commit()
        run_ids = runner.create_runs(session, batch)
        batch_id, rows = batch.id, batch.rows
    await runner.run_batch(run_ids)
    with Session(db.engine) as session:
        runs = {r.row_number: r for r in session.exec(select(Run).where(Run.batch_id == batch_id)).all()}
        results = []
        for row in rows:
            run = runs.get(row["row_number"])
            name = " ".join(filter(None, (row["data"].get("first_name"), row["data"].get("last_name"))))
            results.append({
                "file": path.name, "row": row["row_number"], "name": name,
                "status": run.status.value if run else ("invalid" if row["status"] == "invalid" else "skipped"),
                "reason": (run.final_reason if run else row["reason"]) or "",
                "run_id": run.id if run else None,
                **(_details(session, run) if run else {}),
            })
    return results


def _details(session: Session, run: Run) -> dict[str, Any]:
    """What makes each case visible, beyond its status (checked against demo_cases.json)."""
    signals = session.exec(select(SignalRecord).where(SignalRecord.run_id == run.id)).all()
    draft = review.latest_draft(session, run.id)
    hook = next((s for s in signals if s.selected), None)
    return {
        "message_type": run.message_type.value,
        "hook_type": hook.type if hook else None,
        "draft_mode": draft.critic_report.get("mode") if draft else None,
        "screening": sorted({s.summary.split(":")[0].split(" found")[0] for s in signals if s.type == "ai_screening"}),
        "crowded": sorted(s.type for s in signals if (s.scores or {}).get("crowded")),
        "send_day": run.send_day,
    }


async def approve_after_wait(run_id: int) -> dict[str, Any]:
    """Case 10: the draft waited AGED_DAYS in the queue; approving it re-checks its facts."""
    with Session(db.engine) as session:
        draft = review.latest_draft(session, run_id)
        draft.created_at = draft.created_at - timedelta(days=AGED_DAYS)
        session.add(draft)
        session.commit()
        first = await review.review(session, run_id, ReviewerAction.approve)
        out = {"outcome": first.outcome, "checks": [(c.verdict, c.sentence_before, c.sentence_after) for c in first.checks]}
        if first.outcome == "changed":
            second = await review.review(session, run_id, ReviewerAction.approve, draft_id=first.draft_id)
            out["then"] = second.outcome
            batch = session.get(Batch, session.get(Run, run_id).batch_id)
            out["export"] = review.export_csv(session, batch)
    return out


async def run_demo(case10_name: str = "Omar Haddad", before_case10=None) -> dict[str, Any]:
    fresh_database()
    settings.today = TODAY
    settings.require_linkedin = False  # the demo's fictional people have no LinkedIn
    results = []
    for path in CSVS:
        results += await run_csv(path)
    case10 = None
    target = next((r for r in results if r["name"] == case10_name and r["run_id"]), None)
    if target and target["status"] == "ready_for_review":
        if before_case10:
            before_case10()
        case10 = await approve_after_wait(target["run_id"])
    return {"rows": results, "case10": case10}
