"""Dev-only helpers for demos (docs/PROJECT_SPEC.md T15). Every route answers 404 when DEMO_MODE=live."""

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.config import settings
from app.db import get_session
from app.models import DraftRecord, utcnow

router = APIRouter(prefix="/dev", tags=["dev"])


def _not_live() -> None:
    if settings.demo_mode == "live":
        raise HTTPException(404, "Not found.")


@router.post("/age-draft/{draft_id}", dependencies=[Depends(_not_live)])
def age_draft(draft_id: int, days: int = Query(12, ge=0, le=365),
              session: Session = Depends(get_session)) -> dict[str, Any]:
    """Case 10: simulate a draft that waited `days` in the review queue (the clock is simulated)."""
    draft = session.get(DraftRecord, draft_id)
    if draft is None:
        raise HTTPException(404, f"Draft {draft_id} not found.")
    draft.created_at = utcnow() - timedelta(days=days)
    session.add(draft)
    session.commit()
    return {"draft_id": draft_id, "created_at": draft.created_at.isoformat(), "aged_days": days}
