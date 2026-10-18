"""SQLModel tables and the status enums.

Spec: docs/GUIDE.md "Data model" and "Account context: data and tools".
Run statuses come only from RunStatus; nothing else may set a run's status string.
"""

from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

from sqlalchemy import JSON, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus(str, Enum):
    running = "running"
    needs_input = "needs_input"
    low_signal = "low_signal"
    ready_for_review = "ready_for_review"
    blocked = "blocked"
    suppressed = "suppressed"
    held = "held"
    approved = "approved"
    rejected = "rejected"
    failed = "failed"
    # CSV-only: rows that never start a run.
    invalid = "invalid"
    skipped = "skipped"


class BatchStatus(str, Enum):
    validating = "validating"
    running = "running"
    done = "done"


class StageState(str, Enum):
    started = "started"
    done = "done"
    failed = "failed"
    skipped = "skipped"


class MessageType(str, Enum):
    cold = "cold"
    follow_up = "follow_up"


class ReviewerAction(str, Enum):
    approve = "approve"
    edit = "edit"
    reject = "reject"


class OpportunityStatus(str, Enum):
    open = "open"
    won = "won"
    lost = "lost"


class SequenceOutcome(str, Enum):
    no_reply = "no_reply"
    not_now = "not_now"
    not_interested = "not_interested"
    unsubscribed = "unsubscribed"


# --- Pipeline tables ---------------------------------------------------------


class Batch(SQLModel, table=True):
    __tablename__ = "batches"

    id: int | None = Field(default=None, primary_key=True)
    filename: str
    uploaded_at: datetime = Field(default_factory=utcnow)
    row_count: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    duplicate_count: int = 0
    column_mapping: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    status: BatchStatus = BatchStatus.validating
    finished_at: datetime | None = None


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: int | None = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="batches.id", index=True)
    row_number: int
    raw_row: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)

    first_name: str
    last_name: str
    company: str | None = None
    title: str | None = None
    domain: str | None = Field(default=None, index=True)
    linkedin_url: str | None = None
    email: str | None = Field(default=None, index=True)
    rep_notes: str | None = None  # trusted: written by the rep
    public_bio: str | None = None  # untrusted: treated like scraped web text

    account_key: str | None = Field(default=None, index=True)
    message_type: MessageType = MessageType.cold
    status: RunStatus = Field(default=RunStatus.running, index=True)
    final_reason: str | None = None
    confidence: float | None = None
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    cost_credits: float = 0.0


class StageEvent(SQLModel, table=True):
    __tablename__ = "stage_events"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    stage: str
    state: StageState
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    duration_ms: int | None = None
    ts: datetime = Field(default_factory=utcnow)


class SignalRecord(SQLModel, table=True):
    """A stored Signal (see app.schemas.Signal) plus its scores and selection."""

    __tablename__ = "signals"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    type: str
    about: str
    summary: str
    evidence: str
    source_url: str
    published_at: date | None = None
    sensitive: bool = False
    speaker: str | None = None
    is_reshare: bool = False
    time_sensitive: bool = False
    coverage: int = 1
    ghost_suspect: bool = False
    scores: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    selected: bool = False
    rejected_reason: str | None = None


class DraftRecord(SQLModel, table=True):
    __tablename__ = "drafts"

    id: int | None = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="runs.id", index=True)
    version: int = 1
    subject: str
    body: str
    hook_signal_id: int | None = Field(default=None, foreign_key="signals.id")
    claims: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    critic_report: dict[str, Any] = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(default_factory=utcnow)
    reviewer_action: ReviewerAction | None = None
    edited_body: str | None = None
    reviewed_at: datetime | None = None


# --- Account context (stand-ins for CRM and sales engagement tools) ---------


class CrmAccount(SQLModel, table=True):
    __tablename__ = "crm_accounts"

    domain: str = Field(primary_key=True)
    name: str
    parent_domain: str | None = None
    owner: str | None = None


class Opportunity(SQLModel, table=True):
    __tablename__ = "opportunities"
    __table_args__ = (UniqueConstraint("account_domain", "owner"),)

    id: int | None = Field(default=None, primary_key=True)
    account_domain: str = Field(index=True)
    stage: int = Field(ge=1, le=5)
    owner: str
    status: OpportunityStatus = OpportunityStatus.open
    updated_at: datetime = Field(default_factory=utcnow)


class SequenceHistory(SQLModel, table=True):
    __tablename__ = "sequence_history"
    __table_args__ = (UniqueConstraint("email", "sequence"),)

    id: int | None = Field(default=None, primary_key=True)
    email: str = Field(index=True)
    domain: str = Field(index=True)
    sequence: str
    last_touch_at: datetime
    outcome: SequenceOutcome
    reply_snippet: str | None = None
    follow_up_after: date | None = None
