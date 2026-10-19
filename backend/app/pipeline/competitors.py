"""Case 6: a signal that names one of our competitors.

Spec: docs/GUIDE.md "Account context", the cheap rules. Code only, no model:
- they bought or adopted the competitor within 12 months -> held, with a renewal reminder date
  (a new contract won't be ripped out; come back before it renews)
- they have used it longer (or undated) and we have a migration story -> displacement angle
- they use it and we have no migration story -> held
- the signal only mentions the competitor (their funding, a comparison) -> never a hook
"""

import re
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel

from app.pipeline.extract import ExtractedSignal

# Words that say the prospect company bought / uses the product, not just mentions it.
ADOPTION = re.compile(
    r"\b(adopt(?:s|ed|ing)?|select(?:s|ed)|chose|choos(?:es|ing)|roll(?:s|ed)? out|rolled out|implement(?:s|ed)|"
    r"deploy(?:s|ed)|switch(?:es|ed)? to|migrat(?:es|ed) to|signed (?:with|on)|uses|using|"
    r"customer of|built (?:on|with)|powered by|standardi[sz]ed on)\b", re.I)
RECENT_DAYS = 365
REMIND_BEFORE_RENEWAL_DAYS = 90


class CompetitorFinding(BaseModel):
    competitor: str
    signal_id: int
    summary: str
    adopted_at: date | None = None
    recent: bool = False
    migration_story: str | None = None
    renewal_reminder: date | None = None


def _named(signal: ExtractedSignal, competitors: list[dict[str, Any]]) -> dict[str, Any] | None:
    text = f"{signal.summary} {signal.evidence}"
    return next((c for c in competitors if re.search(rf"\b{re.escape(c['name'])}\b", text, re.I)), None)


def mentions_competitor(signal: ExtractedSignal, seller: dict[str, Any]) -> str | None:
    """The competitor's name if the signal names one."""
    found = _named(signal, seller.get("competitors") or [])
    return found["name"] if found else None


def find_usage(signals: list[ExtractedSignal], seller: dict[str, Any], today: date) -> CompetitorFinding | None:
    """The strongest evidence that the prospect company uses a competitor (most recent first)."""
    findings = []
    for signal in signals:
        competitor = _named(signal, seller.get("competitors") or [])
        if competitor is None or signal.type == "ai_screening":
            continue
        if not ADOPTION.search(f"{signal.summary} {signal.evidence}"):
            continue
        adopted = signal.published_at
        recent = adopted is not None and (today - adopted).days <= RECENT_DAYS
        reminder = None
        if recent:
            months = competitor.get("typical_contract_months") or 12
            reminder = adopted + timedelta(days=round(months * 30.44) - REMIND_BEFORE_RENEWAL_DAYS)
        story = competitor.get("migration_story")
        findings.append(CompetitorFinding(
            competitor=competitor["name"], signal_id=signal.id, summary=signal.summary, adopted_at=adopted,
            recent=recent, renewal_reminder=reminder,
            migration_story=" ".join(story.split()) if story else None,
        ))
    return max(findings, key=lambda f: (f.recent, f.adopted_at or date.min), default=None)
