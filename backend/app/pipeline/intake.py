"""Stage 1: intake and guardrails (code only, no LLM, no credits).

Spec: docs/GUIDE.md stage 1 and "Account context". Blocks a person already researched in the
last settings.recent_run_days days; otherwise collects internal context (past outreach,
replies, open deals, CRM owner). The rules that act on that context come in T13.
"""

from datetime import timedelta

from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlmodel import Session, select

from app import db
from app.config import settings
from app.context_adapter import AccountInfo, OpenOpportunity, Touch, get_context_adapter
from app.models import Run, RunStatus, utcnow
from app.pipeline.runner import RunContext, StopRun


class IntakeOutput(BaseModel):
    message: str
    touches: list[Touch] = []
    reply_outcomes: list[Touch] = []
    open_opportunities: list[OpenOpportunity] = []
    crm_account: AccountInfo | None = None
    rep_notes: str | None = None


def find_recent_run(ctx: RunContext) -> Run | None:
    """The same person in another batch within the window (failed runs don't count)."""
    row = ctx.row
    same_person = []
    if row.get("linkedin_url"):
        same_person.append(Run.linkedin_url == row["linkedin_url"])
    if row.get("email"):
        same_person.append(Run.email == row["email"])
    if row.get("domain"):
        same_person.append((func.lower(Run.first_name) == row["first_name"].lower())
                           & (func.lower(Run.last_name) == row["last_name"].lower())
                           & (Run.domain == row["domain"]))
    if not same_person or settings.recent_run_days <= 0:
        return None
    since = utcnow() - timedelta(days=settings.recent_run_days)
    with Session(db.engine) as session:
        return session.exec(
            select(Run).where(or_(*same_person), Run.batch_id != ctx.batch_id, Run.id != ctx.run_id,
                              Run.started_at >= since, Run.status != RunStatus.failed)
            .order_by(Run.started_at.desc())
        ).first()


async def run(ctx: RunContext) -> IntakeOutput:
    earlier = find_recent_run(ctx)
    if earlier:
        raise StopRun(
            RunStatus.blocked,
            f"Contacted recently: already researched on {earlier.started_at:%Y-%m-%d} (run #{earlier.id})",
            {"earlier_run_id": earlier.id},
        )

    adapter = get_context_adapter()
    email, domain = ctx.row.get("email"), ctx.row.get("domain")
    output = IntakeOutput(
        message="",
        touches=adapter.check_sequence_history(email, domain),
        reply_outcomes=adapter.check_reply_outcomes(email, domain),
        open_opportunities=adapter.check_open_opportunities(domain),
        crm_account=adapter.get_crm_account(domain),
        rep_notes=ctx.row.get("rep_notes"),
    )

    parts = ["No recent run"]
    own = [t for t in output.reply_outcomes if t.same_person]
    if own:
        parts.append(f"replied '{own[0].outcome}' on {own[0].last_touch_at:%Y-%m-%d}")
    elif output.touches:
        parts.append(f"{len(output.touches)} past touch(es) at this company")
    else:
        parts.append("no past outreach")
    if output.open_opportunities:
        opp = output.open_opportunities[0]
        parts.append(f"open deal (stage {opp.stage}, {opp.owner})")
    else:
        parts.append("no open deals")
    output.message = " · ".join(parts)
    return output
