"""Load seed/context.json into crm_accounts, opportunities and sequence_history.

Idempotent: rows are upserted on their natural keys (domain; account_domain + owner;
email + sequence), so running it twice never duplicates anything.

Usage (from backend/):  .venv/bin/python -m scripts.seed_context [path/to/context.json]
"""

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.config import REPO_DIR
from app.db import create_db_and_tables, engine
from app.models import CrmAccount, Opportunity, SequenceHistory

DEFAULT_PATH = REPO_DIR / "seed" / "context.json"


def _days_ago(days: int | None, now: datetime) -> datetime | None:
    return None if days is None else now - timedelta(days=days)


def _date_days_ago(days: int | None, now: datetime) -> date | None:
    moment = _days_ago(days, now)
    return moment.date() if moment else None


def _upsert(session: Session, existing: Any, model: type, values: dict[str, Any]) -> None:
    if existing is None:
        session.add(model(**values))
    else:
        for key, value in values.items():
            setattr(existing, key, value)
        session.add(existing)


def seed(session: Session, path: Path = DEFAULT_PATH, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    data = json.loads(path.read_text(encoding="utf-8"))

    for row in data["crm_accounts"]:
        _upsert(session, session.get(CrmAccount, row["domain"]), CrmAccount, row)

    for row in data["opportunities"]:
        values = {k: v for k, v in row.items() if k != "updated_days_ago"}
        values["updated_at"] = _days_ago(row["updated_days_ago"], now)
        existing = session.exec(
            select(Opportunity).where(
                Opportunity.account_domain == row["account_domain"],
                Opportunity.owner == row["owner"],
            )
        ).first()
        _upsert(session, existing, Opportunity, values)

    for row in data["sequence_history"]:
        values = {
            k: v
            for k, v in row.items()
            if k not in ("last_touch_days_ago", "follow_up_after_days_ago")
        }
        values["last_touch_at"] = _days_ago(row["last_touch_days_ago"], now)
        values["follow_up_after"] = _date_days_ago(row["follow_up_after_days_ago"], now)
        existing = session.exec(
            select(SequenceHistory).where(
                SequenceHistory.email == row["email"],
                SequenceHistory.sequence == row["sequence"],
            )
        ).first()
        _upsert(session, existing, SequenceHistory, values)

    session.commit()
    return {
        "crm_accounts": len(session.exec(select(CrmAccount)).all()),
        "opportunities": len(session.exec(select(Opportunity)).all()),
        "sequence_history": len(session.exec(select(SequenceHistory)).all()),
    }


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    create_db_and_tables()
    with Session(engine) as session:
        counts = seed(session, path)
    print(f"Seeded from {path}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))


if __name__ == "__main__":
    main()
