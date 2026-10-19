"""Internal account context: CRM accounts, open deals, past outreach.

Spec: docs/GUIDE.md "Account context: data and tools". Today this reads the seeded stand-in
tables; a HubSpot / Salesforce / Outreach adapter replaces this one file and nothing else.
"""

from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import or_
from sqlmodel import Session, select

from app import db
from app.models import CrmAccount, Opportunity, OpportunityStatus, SequenceHistory, SequenceOutcome


class Touch(BaseModel):
    email: str
    domain: str
    sequence: str
    last_touch_at: datetime
    outcome: str
    reply_snippet: str | None = None
    follow_up_after: date | None = None
    same_person: bool  # False = a colleague at the same company was contacted


class OpenOpportunity(BaseModel):
    account_domain: str
    stage: int
    owner: str
    updated_at: datetime


class AccountInfo(BaseModel):
    domain: str
    name: str
    parent_domain: str | None = None
    owner: str | None = None


class ContextAdapter(Protocol):
    def check_sequence_history(self, email: str | None, domain: str | None) -> list[Touch]: ...
    def check_reply_outcomes(self, email: str | None, domain: str | None) -> list[Touch]: ...
    def check_open_opportunities(self, domain: str | None) -> list[OpenOpportunity]: ...
    def get_crm_account(self, domain: str | None) -> AccountInfo | None: ...


class SeedTablesAdapter:
    """Reads crm_accounts, opportunities and sequence_history (seed/context.json)."""

    def check_sequence_history(self, email, domain):
        if not (email or domain):
            return []
        email = (email or "").lower()
        with Session(db.engine) as session:
            rows = session.exec(
                select(SequenceHistory).where(or_(SequenceHistory.email == email,
                                                  SequenceHistory.domain == (domain or "")))
                .order_by(SequenceHistory.last_touch_at.desc())
            ).all()
        return [
            Touch(**r.model_dump(exclude={"id", "outcome"}), outcome=r.outcome.value,
                  same_person=bool(email) and r.email.lower() == email)
            for r in rows
        ]

    def check_reply_outcomes(self, email, domain):
        """Touches that got an answer (anything but no_reply), newest first."""
        return [t for t in self.check_sequence_history(email, domain)
                if t.outcome != SequenceOutcome.no_reply.value]

    def check_open_opportunities(self, domain):
        if not domain:
            return []
        with Session(db.engine) as session:
            rows = session.exec(
                select(Opportunity).where(Opportunity.account_domain == domain,
                                          Opportunity.status == OpportunityStatus.open)
            ).all()
        return [OpenOpportunity(**r.model_dump(include={"account_domain", "stage", "owner", "updated_at"}))
                for r in rows]

    def get_crm_account(self, domain):
        if not domain:
            return None
        with Session(db.engine) as session:
            account = session.get(CrmAccount, domain)
        return AccountInfo(**account.model_dump()) if account else None


def get_context_adapter() -> ContextAdapter:
    return SeedTablesAdapter()
