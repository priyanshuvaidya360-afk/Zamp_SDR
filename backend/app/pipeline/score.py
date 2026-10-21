"""Stage 5: score every signal, then pick ONE hook and say why it beat the others.

Spec: docs/GUIDE.md stage 5, the hook score formula, the hook-choice prompt, and T08.
The smart model judges relevance (ties to what we sell) and specificity (about this person >
company > industry). Code computes recency and verifiability, applies the weights, removes
signals that may never be a hook, and decides low_signal. The smart model then picks from the
top few and writes the angle.

Hook points: SCORE_ADJUSTERS (score changes, e.g. the crowded penalty), HOOK_EXCLUSIONS
(signals that may never be used) and CONTEXT_ONLY (signals that may support, but never open).
"""

import math
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from sqlmodel import Session

from app import db, llm, prompts
from app.config import settings
from app.models import RunStatus, SignalRecord
from app.pipeline import competitors, crowded
from app.pipeline.extract import ExtractedSignal, ExtractOutput
from app.pipeline.resolve import display_name
from app.pipeline.runner import RunContext, StopRun
from app.schemas import HookChoice

# Outlets whose reporting counts as reputable (verifiability 1). The company's own site and
# any story covered by 2+ outlets also count; everything else is secondary (0.5).
REPUTABLE_OUTLETS = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com", "forbes.com", "techcrunch.com",
    "theverge.com", "venturebeat.com", "businesswire.com", "prnewswire.com", "globenewswire.com",
    "finance.yahoo.com", "economictimes.indiatimes.com", "livemint.com", "moneycontrol.com",
    "yourstory.com", "inc42.com", "thehindubusinessline.com", "business-standard.com",
    "sifted.eu", "axios.com", "fortune.com", "businessinsider.com", "crunchbase.com",
}


class Adjustment(BaseModel):
    name: str
    delta: float
    reason: str


class ScoredSignal(BaseModel):
    signal_id: int
    type: str
    about: str
    summary: str
    source_url: str
    published_at: date | None = None
    note: str | None = None
    relevance: int = 0
    specificity: int = 0
    reasoning: str = ""
    days_old: int = 0
    recency: float = 0.0  # 5 * e^(-d/60)
    verifiability: float = 0.0  # 0 / 0.5 / 1
    adjustments: list[Adjustment] = []
    score: float = 0.0
    excluded: str | None = None  # why this signal may never be the hook
    context_only: str | None = None  # why it may be context but never the opener (case 5)
    coverage: int = 1  # distinct outlets reporting it
    crowded: bool = False
    rank_before_penalty: int | None = None  # where a crowded signal would have ranked without it


class ScoreOutput(BaseModel):
    message: str
    hook: ScoredSignal | None = None
    angle: str | None = None
    why_not_others: str | None = None
    runner_up_ids: list[int] = []
    ranked: list[ScoredSignal] = []  # every signal, best first, with the full breakdown
    weights: dict[str, float] = {}
    low_signal: bool = False


# --- Model judgement: relevance + specificity ------------------------------------------------


class _Rating(BaseModel):
    signal_id: int
    relevance: int = Field(ge=0, le=5)
    specificity: int = Field(ge=0, le=5)
    reasoning: str = Field(description="One short sentence")


class _Ratings(BaseModel):
    ratings: list[_Rating]


def _seller_brief(seller: dict[str, Any]) -> str:
    icp = seller.get("icp") or {}
    return prompts.get(
        "score", "seller_brief", company=seller.get("company"),
        offer=" ".join(str(seller.get("offer", "")).split()),
        problem=" ".join(str(seller.get("problem_solved", "")).split()),
        buyers=", ".join(icp.get("buyer_titles", [])), triggers="; ".join(icp.get("triggers", [])))


def _prospect_line(ctx: RunContext) -> str:
    title = f", {ctx.row['title']}" if ctx.row.get("title") else ""
    return prompts.get("score", "prospect_line", prospect=display_name(ctx.row), title=title,
                       company=ctx.row.get("company") or ctx.row.get("domain"))


def prompt_ids(ctx: RunContext) -> tuple[dict[int, int], dict[int, int]]:
    """(record id -> prompt id, prompt id -> record id). Prompts number signals 1..n in the
    run's own order, never by database id: ids depend on what else ran at the same time, and a
    prompt that varies with them could never replay from a recording."""
    signals = ctx.outputs["extract"].signals
    to_prompt = {s.id: i for i, s in enumerate(signals, 1)}
    return to_prompt, {i: sid for sid, i in to_prompt.items()}


def _signal_lines(signals: list[ExtractedSignal | ScoredSignal], to_prompt: dict[int, int]) -> str:
    lines = []
    for s in signals:
        sid = to_prompt[s.signal_id if isinstance(s, ScoredSignal) else s.id]
        when = s.published_at.isoformat() if s.published_at else "undated"
        note = " " + prompts.get("score", "signal_note", note=s.note) if s.note else ""
        lines.append(prompts.get("score", "signal_line", id=sid, type=s.type, about=s.about, when=when,
                                 summary=s.summary, note=note, url=s.source_url))
    return "\n".join(lines)


async def rate(ctx: RunContext, signals: list[ExtractedSignal]) -> dict[int, _Rating]:
    to_prompt, to_record = prompt_ids(ctx)
    user = prompts.get("score", "rate_user", seller_brief=_seller_brief(ctx.seller),
                       prospect_line=_prospect_line(ctx), signals=_signal_lines(signals, to_prompt))
    ratings, usage = await llm.structured("smart", prompts.get("score", "rate_system"), user, _Ratings)
    ctx.add_usage(tokens=usage.prompt_tokens + usage.completion_tokens)
    return {to_record[r.signal_id]: r for r in ratings.ratings if r.signal_id in to_record}


# --- Code: recency, verifiability, weights, exclusions --------------------------------------


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def verifiability(signal: ExtractedSignal, company_domain: str | None) -> float:
    """1 = company's own site, reputable outlet, or 2+ outlets; 0.5 = secondary; 0 = unsourced."""
    host = _host(signal.source_url)
    if not host:
        return 0.0
    own = bool(company_domain) and (host == company_domain or host.endswith("." + company_domain))
    reputable = any(host == d or host.endswith("." + d) for d in REPUTABLE_OUTLETS)
    return 1.0 if own or reputable or signal.coverage >= 2 else 0.5


def recency_points(published: date | None, today: date) -> tuple[int, float]:
    days = settings.undated_days if published is None else max(0, (today - published).days)
    return days, 5 * math.exp(-days / settings.recency_scale_days)


def weights() -> dict[str, float]:
    return {"relevance": settings.weight_relevance, "specificity": settings.weight_specificity,
            "recency": settings.weight_recency, "verifiability": settings.weight_verifiability}


def total(s: ScoredSignal) -> float:
    w = weights()
    base = (w["relevance"] * s.relevance + w["specificity"] * s.specificity
            + w["recency"] * s.recency + w["verifiability"] * 5 * s.verifiability)
    return round(base + sum(a.delta for a in s.adjustments), 2)


def _crowd_penalty(s: ScoredSignal, signal: ExtractedSignal, ctx: RunContext) -> Adjustment | None:
    """Case 5: everyone is already writing to them about this event."""
    if not s.crowded:
        return None
    return Adjustment(name="crowded", delta=-settings.crowd_penalty, reason=crowded.reason(s.coverage, s.days_old))


# Each returns an Adjustment (or None) for a scored signal.
SCORE_ADJUSTERS: list[Callable[[ScoredSignal, ExtractedSignal, RunContext], Adjustment | None]] = [_crowd_penalty]


def _never_a_hook(signal: ExtractedSignal, ctx: RunContext) -> str | None:
    if signal.type == "ai_screening":
        return "AI-screening finding: never a hook"
    if signal.sensitive:
        return "Sensitive (layoffs, lawsuits, breaches...): never a hook"
    return None


def _batch_balance(signal: ExtractedSignal, ctx: RunContext) -> str | None:
    """Case 9: a re-run ordered by the batch check excludes an over-used hook source. Case 10's
    re-check uses the same door for a source whose fact is gone (with its own reason)."""
    resume = ctx.resume_input or {}
    if signal.source_url in (resume.get("exclude_sources") or []):
        return resume.get("exclude_reason") or "Batch balance: this source already opens too many drafts in the batch"
    return None


def _competitor_mention(signal: ExtractedSignal, ctx: RunContext) -> str | None:
    """Case 6: news about a competitor (their funding, a comparison) is never our opener."""
    name = competitors.mentions_competitor(signal, ctx.seller)
    return f"Names a competitor ({name}): never the opener" if name else None


# Each returns a reason the signal may never open the email, or None.
HOOK_EXCLUSIONS: list[Callable[[ExtractedSignal, RunContext], str | None]] = [
    _never_a_hook, _batch_balance, _competitor_mention]


def _crowded_event(s: ScoredSignal) -> str | None:
    if s.crowded:
        return (f"{crowded.reason(s.coverage, s.days_old)}: every vendor opens with this, "
                "so it may be context but never the opener")
    return None


# Each returns a reason the signal may support an email but never open it, or None.
CONTEXT_ONLY: list[Callable[[ScoredSignal], str | None]] = [_crowded_event]


def effective_score(ctx: RunContext) -> "ScoreOutput":
    """The hook the draft should use: the account plan's assignment when there is one (case 3),
    otherwise stage 5's own pick."""
    plan = ctx.outputs.get("account_plan")
    return plan.score if plan is not None and getattr(plan, "score", None) is not None else ctx.outputs["score"]


def score_signals(ctx: RunContext, signals: list[ExtractedSignal], ratings: dict[int, _Rating],
                  today: date) -> list[ScoredSignal]:
    scored = []
    for signal in signals:
        rating = ratings.get(signal.id)
        days, recency = recency_points(signal.published_at, today)
        specificity = rating.specificity if rating else 0
        if signal.about == "company":
            specificity = min(specificity, 4)  # only the prospect themselves earns a 5
        s = ScoredSignal(
            signal_id=signal.id, type=signal.type, about=signal.about, summary=signal.summary,
            source_url=signal.source_url, published_at=signal.published_at, note=signal.note,
            relevance=rating.relevance if rating else 0, specificity=specificity,
            reasoning=rating.reasoning if rating else "Not rated by the model.",
            days_old=days, recency=round(recency, 2),
            verifiability=verifiability(signal, ctx.row.get("domain")),
            coverage=signal.coverage,
            crowded=crowded.is_crowded(signal.type, signal.published_at, signal.coverage, today),
        )
        for adjust in SCORE_ADJUSTERS:
            if adjustment := adjust(s, signal, ctx):
                s.adjustments.append(adjustment)
        s.excluded = next((reason for rule in HOOK_EXCLUSIONS if (reason := rule(signal, ctx))), None)
        s.context_only = next((reason for rule in CONTEXT_ONLY if (reason := rule(s))), None)
        s.score = total(s)
        scored.append(s)
    for s in scored:
        if s.crowded and s.excluded is None:
            unpenalised = s.score - sum(a.delta for a in s.adjustments)
            s.rank_before_penalty = 1 + sum(1 for o in scored if o is not s and o.excluded is None and o.score > unpenalised)
    return sorted(scored, key=lambda s: (s.excluded is not None, s.context_only is not None, -s.score))


def can_open(s: ScoredSignal) -> bool:
    return s.excluded is None and s.context_only is None


# --- Model judgement: hook choice ------------------------------------------------------------

def _reply_line(ctx: RunContext) -> str:
    """Case 1: a follow-up should answer what they said last time (trusted CRM data)."""
    if ctx.message_type != "follow_up":
        return ""
    intake = ctx.outputs.get("intake")
    own = [t for t in (intake.reply_outcomes if intake else []) if t.same_person and t.reply_snippet]
    if not own:
        return ""
    return "\n" + prompts.get("score", "follow_up", snippet=own[0].reply_snippet)


def _hook_prompt(ctx: RunContext, candidates: list[ScoredSignal], to_prompt: dict[int, int]) -> str:
    icp = ctx.seller.get("icp") or {}
    return prompts.get(
        "score", "hook_user", offer=" ".join(str(ctx.seller.get("offer", "")).split()),
        buyers=", ".join(icp.get("buyer_titles", [])), company_size=icp.get("company_size", ""),
        prospect_line=_prospect_line(ctx), signals=_signal_lines(candidates, to_prompt),
        follow_up=_reply_line(ctx))


async def choose_hook(ctx: RunContext, candidates: list[ScoredSignal]) -> tuple[HookChoice, str | None]:
    """(choice, override note). Code holds the model to the candidate list."""
    to_prompt, to_record = prompt_ids(ctx)
    choice, usage = await llm.structured("smart", prompts.get("score", "hook_system"),
                                         _hook_prompt(ctx, candidates, to_prompt), HookChoice)
    ctx.add_usage(tokens=usage.prompt_tokens + usage.completion_tokens)
    # Back from prompt ids to record ids (an id the prompt never showed maps to -1: invalid).
    if choice.signal_id is not None:
        choice.signal_id = to_record.get(choice.signal_id, -1)
    choice.runners_up = [to_record.get(i, -1) for i in choice.runners_up]
    ids = [c.signal_id for c in candidates]
    if choice.signal_id is not None and choice.signal_id not in ids:
        top = candidates[0].signal_id
        note = "Model picked a signal that was not a candidate; used the top score."
        choice.signal_id = top
        return choice, note
    choice.runners_up = [i for i in choice.runners_up if i in ids and i != choice.signal_id]
    return choice, None


# --- Persistence and the stage ---------------------------------------------------------------


def _persist(ctx: RunContext, ranked: list[ScoredSignal], hook_id: int | None,
             runner_ups: list[int], why_not: str | None, candidate_ids: list[int],
             no_hook_reason: str | None = None) -> None:
    with Session(db.engine) as session:
        for s in ranked:
            record = session.get(SignalRecord, s.signal_id)
            if record is None:
                continue
            record.scores = s.model_dump(mode="json", include={
                "relevance", "specificity", "reasoning", "days_old", "recency", "verifiability",
                "adjustments", "score", "coverage", "crowded", "context_only", "rank_before_penalty",
            }) | {"weights": weights(), "penalty": -sum(a.delta for a in s.adjustments if a.name == "crowded")}
            record.selected = s.signal_id == hook_id
            if record.selected:
                record.rejected_reason = None
            elif s.excluded:
                record.rejected_reason = s.excluded
            elif s.context_only:
                record.rejected_reason = s.context_only
            elif s.signal_id in runner_ups or s.signal_id in candidate_ids:
                record.rejected_reason = f"Runner-up: {why_not}" if why_not else "Runner-up"
            elif hook_id is None:
                record.rejected_reason = no_hook_reason or f"Too weak (score {s.score} < {settings.low_signal_below})"
            else:
                record.rejected_reason = f"Not in the top {settings.hook_candidates} (score {s.score})"
            session.add(record)
        session.commit()


def _low_signal(ctx: RunContext, ranked: list[ScoredSignal], reason: str,
                no_hook_reason: str | None = None) -> ScoreOutput:
    """No hook: the run ends low_signal; the draft stage offers a clearly labelled role-based draft."""
    ctx.final_status = RunStatus.low_signal
    ctx.final_reason = reason
    _persist(ctx, ranked, None, [], None, [], no_hook_reason)
    return ScoreOutput(message=reason, ranked=ranked, weights=weights(), low_signal=True)


def _competitor_rule(ctx: RunContext, ranked: list[ScoredSignal], usage: competitors.CompetitorFinding) -> ScoreOutput:
    """Case 6: they use a competitor. Recent purchase or no migration story -> held; otherwise the
    usage itself becomes the hook with a displacement angle."""
    when = f" on {usage.adopted_at}" if usage.adopted_at else ""
    payload = {"competitor": usage.model_dump(mode="json")}
    if usage.recent:
        _persist(ctx, ranked, None, [], None, [], f"Held: they adopted {usage.competitor} recently")
        raise StopRun(RunStatus.held,
                      f"Adopted {usage.competitor}{when}, within 12 months: a new contract won't be replaced. "
                      f"Held; renewal reminder {usage.renewal_reminder}.", payload)
    if not usage.migration_story:
        _persist(ctx, ranked, None, [], None, [], f"Held: established {usage.competitor} user")
        raise StopRun(RunStatus.held,
                      f"Established {usage.competitor} user and we have no migration story for it: held.", payload)
    hook = next(r for r in ranked if r.signal_id == usage.signal_id).model_copy(update={"excluded": None})
    ranked = [hook if r.signal_id == hook.signal_id else r for r in ranked]
    angle = f"Displacement: they use {usage.competitor}. {usage.migration_story}"
    _persist(ctx, ranked, hook.signal_id, [], "The competitor displacement angle comes first", [])
    return ScoreOutput(message=f"Displacement angle: they use {usage.competitor}{when}", hook=hook, angle=angle,
                       why_not_others="They already use a competitor; the displacement angle comes first.",
                       ranked=ranked, weights=weights())


async def run(ctx: RunContext) -> ScoreOutput:
    extracted: ExtractOutput = ctx.outputs["extract"]
    today = settings.today or date.today()
    scorable = [s for s in extracted.signals if s.type != "ai_screening"]
    ratings = await rate(ctx, scorable) if scorable else {}
    ranked = score_signals(ctx, extracted.signals, ratings, today)
    if (usage := competitors.find_usage(extracted.signals, ctx.seller, today)) is not None:
        return _competitor_rule(ctx, ranked, usage)
    eligible = [s for s in ranked if can_open(s)]

    if not eligible:
        crowd = [s for s in ranked if s.excluded is None and s.context_only]
        if crowd:
            return _low_signal(ctx, ranked, f"Only a crowded event found ({crowd[0].summary[:60]}): "
                                            "nothing of our own to open with.")
        return _low_signal(ctx, ranked, "No usable signal found: nothing specific to open an email with.")
    best = eligible[0]
    if best.score < settings.low_signal_below:
        return _low_signal(ctx, ranked, f"Signal too weak: best score {best.score} is below "
                                        f"{settings.low_signal_below} ({best.type}: {best.summary[:60]}).")

    candidates = eligible[: settings.hook_candidates]
    choice, override = await choose_hook(ctx, candidates)
    if choice.signal_id is None:
        return _low_signal(ctx, ranked, "No signal links to what we sell, according to the hook review.",
                           f"Hook review: no link to what we sell. {choice.why_not_others}".strip())

    hook = next(c for c in candidates if c.signal_id == choice.signal_id)
    candidate_ids = [c.signal_id for c in candidates]
    _persist(ctx, ranked, hook.signal_id, choice.runners_up, choice.why_not_others, candidate_ids)
    beat = len(candidates) - 1
    message = (f"Hook: {hook.type} (score {hook.score}): {hook.summary[:70]}"
               + (f" · beat {beat} other candidate{'s' if beat != 1 else ''}" if beat else ""))
    if override:
        message += " · model pick overridden"
    return ScoreOutput(
        message=message, hook=hook, angle=choice.angle, why_not_others=choice.why_not_others,
        runner_up_ids=choice.runners_up, ranked=ranked, weights=weights(),
    )
