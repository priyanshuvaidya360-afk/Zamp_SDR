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
from app.models import MessageType, Run, RunStatus, SequenceOutcome, utcnow
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


OPTED_OUT = {SequenceOutcome.not_interested.value: "said not interested",
             SequenceOutcome.unsubscribed.value: "unsubscribed"}


def apply_rules(ctx: RunContext, output: IntakeOutput) -> None:
    """Cases 1 and 2 (docs/GUIDE.md "Account context", the cheap rules). Opting out wins over
    everything; then an open deal holds the run; then a 'not now' turns it into a follow-up.
    Only this person's own replies count; a colleague's reply says nothing about them."""
    own = [t for t in output.reply_outcomes if t.same_person]  # newest first
    latest = own[0] if own else None
    if latest and latest.outcome in OPTED_OUT:
        raise StopRun(RunStatus.suppressed,
                      f"Opted out: {OPTED_OUT[latest.outcome]} on {latest.last_touch_at:%Y-%m-%d} "
                      f"({latest.sequence}). Do not contact.",
                      {"outcome": latest.outcome, "sequence": latest.sequence})
    if output.open_opportunities:
        deal = max(output.open_opportunities, key=lambda o: o.stage)
        raise StopRun(RunStatus.held,
                      f"Open opportunity with {deal.owner}, stage {deal.stage}. Draft held and routed to "
                      f"{deal.owner}.",
                      {"deal_owner": deal.owner, "deal_stage": deal.stage, "routed_to": deal.owner})
    if latest and latest.outcome == SequenceOutcome.not_now.value:
        ctx.message_type = MessageType.follow_up.value
        with Session(db.engine) as session:
            run = session.get(Run, ctx.run_id)
            run.message_type = MessageType.follow_up
            session.add(run)
            session.commit()


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

    apply_rules(ctx, output)

    parts = ["No recent run"]
    own = [t for t in output.reply_outcomes if t.same_person]
    if own:
        parts.append(f"replied '{own[0].outcome}' on {own[0].last_touch_at:%Y-%m-%d}"
                     + (" → follow-up" if ctx.message_type == MessageType.follow_up.value else ""))
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
