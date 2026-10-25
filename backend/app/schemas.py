"""Pydantic stage outputs. The LLM returns these via JSON-schema tool calls.

Spec: docs/PROJECT_SPEC.md "Data model" (core schemas), extended per T02 with the edge-case
fields used by cases 4, 5, 7 and 10.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

SignalType = Literal[
    "funding",
    "acquisition",
    "hiring",
    "product_launch",
    "leadership_change",
    "person_content",
    "expansion",
    "partnership",
    "press",
    "negative_event",
    "company_focus",  # what they sell, to whom, stated goals: evergreen, from their own site
    "ai_screening",  # case 11: never a hook
    "other",
]


class Signal(BaseModel):
    type: SignalType
    about: Literal["person", "company"]
    summary: str = Field(description="One line, in our words")
    evidence: str = Field(description="Short snippet from the source")
    # Set by code from the page the signal came from, never by the model ("csv:public_bio" for
    # a finding in the uploaded bio). Every claim in a draft traces back to it.
    source_url: str
    published_at: date | None = None
    sensitive: bool = Field(description="Layoffs, lawsuits, deaths, breaches, scandals")
    speaker: str | None = Field(default=None, description="Who said it (case 7)")
    is_reshare: bool = Field(default=False, description="Reshared, not authored (case 7)")
    time_sensitive: bool = Field(
        default=False, description="Open-role counts, prices, dated events (case 10)"
    )
    coverage: int = Field(default=1, ge=1, description="Distinct outlet domains (case 5)")
    ghost_suspect: bool = Field(default=False, description="Likely ghost job posting (case 4)")


class SignalScore(BaseModel):
    relevance: int = Field(ge=0, le=5, description="Ties to what we sell")
    specificity: int = Field(ge=0, le=5, description="About this person > company > industry")
    reasoning: str


class HookChoice(BaseModel):
    signal_id: int | None = Field(description="None = no usable hook")
    angle: str = Field(description="Why it matters to this person")
    runners_up: list[int]
    why_not_others: str


class ClaimRef(BaseModel):
    sentence: str
    signal_id: int


class Draft(BaseModel):
    subject: str
    body: str
    claims: list[ClaimRef] = Field(description="Each factual sentence -> signal_id")
