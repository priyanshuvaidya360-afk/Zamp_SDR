"""Stages 6-7: write the email, then critique it (one rewrite if it fails).

Spec: docs/PROJECT_SPEC.md stages 6-7, the draft prompt, case 7 (attribution), case 11 (outline +
banner) and T09. The smart model writes; code checks everything checkable (length, banned
phrases, dates, numbers, every claim maps to a real and safe signal, someone else's words name
the speaker), and one fast-model audit reads the whole email: every factual sentence, declared in
`claims` or not, is checked against the facts the writer was given (unsupported or contradicted
ones block approval until a rep fixes them), and it scores how generic the email is. Nothing is
ever sent.

Three modes:
- hooked: the normal email, built on the one hook from stage 5
- role_based: no usable signal (low_signal); an honest, clearly labelled role-based draft
- outline: the prospect filters AI-written outreach; bullet points for the rep to write by hand
"""

import re
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session

from app import db, llm, prompts
from app.config import settings
from app.models import DraftRecord, RunStatus
from app.pipeline import crowded
from app.pipeline.extract import ExtractedSignal, ExtractOutput
from app.pipeline.intake import IntakeOutput
from app.pipeline.resolve import display_name
from app.pipeline.runner import RunContext
from app.pipeline.score import ScoreOutput, effective_score, prompt_ids
from app.schemas import ClaimRef, Draft

Mode = Literal["hooked", "role_based", "outline"]

SCREENING_BANNER = "This person filters AI-written outreach. Rewrite by hand or use another channel."
ROLE_BASED_BANNER = ("Role-based draft: no specific signal was found for this person. Personalise it "
                     "before sending, or skip them.")
UNVERIFIED_BANNER = ("Identity not verified: this person was never found online with this company, so the "
                     "draft speaks to the role and company only. Confirm they exist before approving.")
DEFAULT_BANNED = ["I hope this finds you well", "I came across your profile"]


class DraftOutput(BaseModel):
    message: str
    draft_id: int
    version: int = 1
    mode: Mode
    message_type: str = "cold"
    subject: str
    body: str
    claims: list[ClaimRef] = []
    hook_signal_id: int | None = None
    banner: str | None = None


# --- Writing ---------------------------------------------------------------------------------

def _attribution_hint(s: ExtractedSignal) -> str:
    """Spell out how to attribute borrowed words (case 7); the model follows examples best."""
    if s.is_reshare and s.speaker:
        return " " + prompts.get("draft", "attribution_reshare_by", speaker=s.speaker)
    if s.is_reshare:
        return " " + prompts.get("draft", "attribution_reshare")
    if s.speaker and s.note and s.note.startswith("Said by"):
        return " " + prompts.get("draft", "attribution_quote", speaker=s.speaker)
    return " " + prompts.get("draft", "note", note=s.note) if s.note else ""


def _fact_line(s: ExtractedSignal, to_prompt: dict[int, int]) -> str:
    when = s.published_at.strftime("%d %B %Y").lstrip("0") if s.published_at else "undated"
    return prompts.get("draft", "fact_line", id=to_prompt[s.id], type=s.type, when=when, summary=s.summary,
                       evidence=s.evidence, hint=_attribution_hint(s))


def pick_facts(ctx: RunContext) -> tuple[ExtractedSignal | None, list[ExtractedSignal]]:
    """The hook and up to two supporting facts; never an excluded (sensitive / AI-screening) one.
    A crowded event (case 5) comes first among the supporting facts: it is the natural context."""
    scored: ScoreOutput = effective_score(ctx)
    extracted: ExtractOutput = ctx.outputs["extract"]
    by_id = {s.id: s for s in extracted.signals}
    usable = [r.signal_id for r in scored.ranked if r.excluded is None]
    if scored.hook is None:
        return None, []
    hook = by_id[scored.hook.signal_id]
    context_only = [r.signal_id for r in scored.ranked if r.excluded is None and r.context_only]
    plan = ctx.outputs.get("account_plan")
    if plan is not None and getattr(plan, "plan", None) is not None:
        # Case 3: the account plan hands out company-wide context (each event to one person at most),
        # so besides the planned context only this person's own facts may support the hook.
        own = [i for i in usable if by_id[i].about == "person"]
        preferred = [i for i in scored.runner_up_ids if i in usable] + own
    else:
        preferred = context_only + [i for i in scored.runner_up_ids if i in usable] + usable
    support = [by_id[i] for i in dict.fromkeys(preferred) if i != hook.id][:2]
    return hook, support


def _crowded_ids(ctx: RunContext) -> set[int]:
    return {r.signal_id for r in effective_score(ctx).ranked if r.crowded}


def mode_for(ctx: RunContext) -> Mode:
    if ctx.outputs["extract"].ai_screening == "screening_trap":
        return "outline"
    return "role_based" if effective_score(ctx).hook is None else "hooked"


def unverified(ctx: RunContext) -> bool:
    """The rep asked for a company-level draft for a person never found online (resolve)."""
    return bool(getattr(ctx.outputs.get("resolve"), "unverified", False))


def _reply_snippet(ctx: RunContext) -> str | None:
    intake: IntakeOutput | None = ctx.outputs.get("intake")
    own = [t for t in (intake.reply_outcomes if intake else []) if t.same_person and t.reply_snippet]
    return own[0].reply_snippet if own else None


def build_prompt(ctx: RunContext, mode: Mode) -> str:
    """Assemble the writer's instructions from the sections of agents/stage6_write_and_check_email.md."""
    seller, rep = ctx.seller, ctx.seller.get("rep") or {}
    banned = (seller.get("tone") or {}).get("banned_phrases") or DEFAULT_BANNED
    title = f", {ctx.row['title']}" if ctx.row.get("title") else ""
    follow_up = ctx.message_type == "follow_up"
    lines = [
        prompts.get("draft", "intro", kind=prompts.get("draft", "kind_follow_up" if follow_up else "kind_first_touch"),
                    rep_name=rep.get("name", "the rep"), rep_title=rep.get("title", ""), company=seller.get("company"),
                    prospect=display_name(ctx.row), title=title,
                    prospect_company=ctx.row.get("company") or ctx.row.get("domain")),
        prompts.get("draft", "offer", offer=" ".join(str(seller.get("offer", "")).split())),
        prompts.get("draft", "problem", problem=" ".join(str(seller.get("problem_solved", "")).split())),
    ]
    if follow_up and (snippet := _reply_snippet(ctx)):
        lines.append(prompts.get("draft", "follow_up_reply", snippet=snippet))
    if unverified(ctx):
        lines.append(prompts.get("draft", "unverified", prospect=display_name(ctx.row),
                                 prospect_company=ctx.row.get("company") or ctx.row.get("domain")))

    hook, support = pick_facts(ctx)
    if mode == "role_based":
        lines.append(prompts.get("draft", "role_based"))
    else:
        angle = effective_score(ctx).angle or ""
        to_prompt, _ = prompt_ids(ctx)
        facts = "\n".join(_fact_line(s, to_prompt) for s in [hook, *support])
        lines += [prompts.get("draft", "hook", hook_id=to_prompt[hook.id], angle=angle),
                  prompts.get("draft", "facts", facts=facts)]
        crowded_ids = _crowded_ids(ctx) & {s.id for s in support}
        if crowded_ids:
            ids = ", ".join(f"[{to_prompt[i]}]" for i in sorted(crowded_ids, key=to_prompt.get))
            lines.append(prompts.get("draft", "context_only", ids=ids))
        if mode == "outline":
            lines.append(prompts.get("draft", "outline"))
    lines.append(prompts.get(
        "draft", "rules", max_words=settings.draft_max_words, subject_words=settings.subject_max_words + 1,
        their_company=ctx.row.get("company") or "their company", company=seller.get("company"),
        signoff=str(rep.get("name", "")).split(" ")[0] or "the rep", banned="; ".join(banned)))
    return "\n".join(lines)


async def write(ctx: RunContext, mode: Mode, previous: Draft | None = None,
                problems: list[str] | None = None) -> Draft:
    prompt = build_prompt(ctx, mode)
    if previous is not None:
        prompt += "\n\n" + prompts.get("draft", "rewrite", subject=previous.subject, body=previous.body,
                                        problems="\n".join(f"- {p}" for p in problems or []))
    draft, usage = await llm.structured("smart", prompts.get("draft", "system"), prompt, Draft)
    ctx.add_usage(tokens=usage.prompt_tokens + usage.completion_tokens)
    _, to_record = prompt_ids(ctx)
    for claim in draft.claims:  # claims come back in prompt numbering; store record ids
        claim.signal_id = to_record.get(claim.signal_id, -1)
    # Models sometimes double-escape newlines inside JSON ("\\n" as text): show real line breaks.
    draft.body = draft.body.replace("\\n", "\n").strip()
    if mode == "role_based":
        draft.claims = []
    return draft


def _store_draft(ctx: RunContext, draft: Draft, version: int, hook_id: int | None, report: dict) -> int:
    with Session(db.engine) as session:
        record = DraftRecord(run_id=ctx.run_id, version=version, subject=draft.subject, body=draft.body,
                             hook_signal_id=hook_id, claims=[c.model_dump() for c in draft.claims],
                             critic_report=report)
        session.add(record)
        session.commit()
        session.refresh(record)
        return record.id


async def run_draft(ctx: RunContext) -> DraftOutput:
    mode = mode_for(ctx)
    hook, _ = pick_facts(ctx)
    draft = await write(ctx, mode)
    banner = {"outline": SCREENING_BANNER, "role_based": ROLE_BASED_BANNER}.get(mode)
    if unverified(ctx):
        banner = " ".join(filter(None, (UNVERIFIED_BANNER, banner)))
    draft_id = _store_draft(ctx, draft, 1, hook.id if hook else None, {"mode": mode, "banner": banner})
    label = {"hooked": "Wrote", "role_based": "Wrote a role-based", "outline": "Wrote an outline for a"}[mode]
    return DraftOutput(
        message=f"{label} {len(draft.body.split())}-word draft: \"{draft.subject}\"",
        draft_id=draft_id, mode=mode, message_type=ctx.message_type, subject=draft.subject,
        body=draft.body, claims=draft.claims, hook_signal_id=hook.id if hook else None, banner=banner,
    )


# --- Critic ----------------------------------------------------------------------------------


class Check(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class _Genericness(BaseModel):
    genericness: int = Field(ge=0, le=5)
    reason: str = Field(description="One sentence")


class CriticOutput(BaseModel):
    message: str
    final_draft_id: int
    version: int
    passed: bool
    checks: list[Check] = []
    genericness: int | None = None
    genericness_reason: str | None = None
    rewritten: bool = False
    warnings: list[str] = []
    confidence: float | None = None
    confidence_formula: str = ""
    banner: str | None = None


RELATIVE_DATES = re.compile(r"\b(?:last|this|next|past)\s+(?:week|month|quarter|year)\b|\b(?:yesterday|tomorrow|"
                            r"recently|lately|the other day|a few (?:days|weeks) ago)\b", re.I)
FLATTERY = re.compile(r"^\W*(?:hi|hello|hey)?\W*(?:[\w.'-]+\W*)?(?:congrat\w*|kudos|i love|i loved|"
                      r"great job|amazing|impressive|what an?)\b", re.I)
EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
NUMBER = re.compile(r"\$?\d[\d,.]*\s?(?:%|[kmb]\b|million|billion|x\b)?", re.I)
LINK = re.compile(r"https?://\S+|\bwww\.\S+", re.I)
YOU_SAID = re.compile(r"\byou(?:'ve)? (?:said|wrote|posted|mentioned|shared your)\b|\byour (?:words|post)\b", re.I)


def _words(text: str) -> list[str]:
    return [w for w in re.sub(r"[^\w$%]+", " ", text.lower()).split() if len(w) > 2]


def _in_body(sentence: str, body: str) -> bool:
    words = _words(sentence)
    body_words = set(_words(body))
    return bool(words) and sum(w in body_words for w in words) / len(words) >= 0.8


def _numbers_in(text: str) -> set[str]:
    text = spell_out_numbers(text)
    return {re.sub(r"[^\d.]", "", m.group()).rstrip(".") for m in NUMBER.finditer(text)} - {""}


# Written-out quantities count as numbers ("two new SDRs" = "2 new SDRs"). "one" is left out: it is
# far more often "one question" or "one of" than a count.
_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen".split()) if i != 1}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i >= 2}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_NUMBER_WORD = re.compile(r"\b(?:(?:%s|%s|dozen|one)(?:[\s-]+(?:%s|%s|%s|and))*)\b" % (
    "|".join(_UNITS), "|".join(_TENS), "|".join(_UNITS), "|".join(_TENS), "|".join(_SCALES)), re.I)


def _word_value(words: list[str]) -> int | None:
    total = current = 0
    for w in words:
        if w in _UNITS:
            current += _UNITS[w]
        elif w == "one":
            current += 1
        elif w in _TENS:
            current += _TENS[w]
        elif w == "dozen":
            current = (current or 1) * 12
        elif w in _SCALES:
            current = (current or 1) * _SCALES[w]
            if _SCALES[w] >= 1000:
                total, current = total + current, 0
        elif w != "and":
            return None
    return total + current


def spell_out_numbers(text: str) -> str:
    """'two hundred' -> '200', 'a dozen' -> '12'; a lone 'one' stays a word."""
    def put(match: re.Match[str]) -> str:
        words = re.split(r"[\s-]+", match.group().lower())
        if words == ["one"]:
            return match.group()
        value = _word_value(words)
        return str(value) if value is not None else match.group()

    return _NUMBER_WORD.sub(put, text)


# Our own signal-type names must never reach a prospect ("(person_content, 8 October)").
INTERNAL_LABEL = re.compile(r"\b(?:person_content|product_launch|leadership_change|negative_event|ai_screening|company_focus|"
                            r"person_news|second_order|signal_id)\b")


def _first_sentence(body: str) -> str:
    lines = [l for l in body.strip().splitlines() if l.strip()]
    # Skip a greeting line ("Hi Amy,") to judge the actual opener.
    if lines and len(lines[0].split()) <= 3 and lines[0].rstrip().endswith(","):
        lines = lines[1:]
    return re.split(r"(?<=[.!?])\s", " ".join(lines), maxsplit=1)[0] if lines else ""


def code_checks(draft: Draft, mode: Mode, signals: dict[int, ExtractedSignal], excluded: set[int],
                prospect_last_name: str, banned: list[str],
                crowded_ids: set[int] | None = None, prospect_name: str | None = None) -> list[Check]:
    body, checks = draft.body, []
    words = len(body.split())
    checks.append(Check(name="length", passed=words < settings.draft_max_words,
                        detail=f"{words} words (limit {settings.draft_max_words})"))
    subject_words = len(draft.subject.split())
    checks.append(Check(name="subject_length", passed=subject_words <= settings.subject_max_words,
                        detail=f"subject has {subject_words} words"))
    found = [p for p in banned if p.lower() in body.lower()]
    checks.append(Check(name="banned_phrases", passed=not found, detail="; ".join(found)))
    opener = _first_sentence(body)
    checks.append(Check(name="no_flattery_opener", passed=not FLATTERY.match(opener),
                        detail=opener[:80] if FLATTERY.match(opener) else ""))
    relative = RELATIVE_DATES.findall(body)
    checks.append(Check(name="absolute_dates", passed=not relative,
                        detail=f"relative date: {', '.join(relative)}" if relative else ""))
    checks.append(Check(name="no_emoji", passed=not EMOJI.search(body)))
    links = LINK.findall(body)
    checks.append(Check(name="no_links", passed=not links, detail=f"links: {', '.join(links)}" if links else ""))
    named = bool(prospect_name) and len(prospect_name.split()) > 1 and prospect_name.lower() in body.lower()
    checks.append(Check(name="addressed_as_you", passed=not named,
                        detail=f"calls the prospect '{prospect_name}' in the third person; write 'you'" if named else ""))
    labels = sorted(set(INTERNAL_LABEL.findall(body)))
    checks.append(Check(name="no_internal_labels", passed=not labels,
                        detail=f"pipeline labels in the email: {', '.join(labels)}" if labels else ""))

    if mode == "role_based":
        checks.append(Check(name="no_claims", passed=not draft.claims,
                            detail="a role-based draft must not claim specific facts" if draft.claims else ""))
        return checks

    unknown = [c.signal_id for c in draft.claims if c.signal_id not in signals]
    checks.append(Check(name="claims_known", passed=bool(draft.claims) and not unknown,
                        detail=f"unknown signal ids {unknown}" if unknown else ("no claims" if not draft.claims else "")))
    unsafe = [c.signal_id for c in draft.claims if c.signal_id in excluded]
    checks.append(Check(name="claims_safe", passed=not unsafe,
                        detail=f"uses sensitive or AI-screening signals {unsafe}" if unsafe else ""))
    missing = [c.sentence[:60] for c in draft.claims if not _in_body(c.sentence, body)]
    checks.append(Check(name="claims_in_email", passed=not missing,
                        detail=f"claimed sentences not in the email: {missing}" if missing else ""))

    cited = [signals[c.signal_id] for c in draft.claims if c.signal_id in signals]
    allowed = set()
    for s in signals.values():
        allowed |= _numbers_in(f"{s.summary} {s.evidence}")
        if s.published_at:
            allowed |= {str(s.published_at.day), str(s.published_at.year)}
    # "a 15-minute call" is the ask, not a fact (models use -, a non-breaking hyphen, or a space).
    body_numbers = _numbers_in(re.sub(r"\b\d+\s?[-\u2010-\u2014]?\s?min(?:ute)?s?\b", "", body, flags=re.I))
    invented = sorted(body_numbers - allowed)
    checks.append(Check(name="numbers_sourced", passed=not invented,
                        detail=f"numbers not in any signal: {', '.join(invented)}" if invented else ""))

    misattributed = []
    for claim in draft.claims:
        s = signals.get(claim.signal_id)
        if not s:
            continue
        someone_else = s.speaker and prospect_last_name.lower() not in s.speaker.lower()
        if someone_else and not any(n.lower() in claim.sentence.lower() for n in s.speaker.split() if len(n) > 2):
            misattributed.append(f"'{claim.sentence[:50]}' uses {s.speaker}'s words without naming them")
        elif (someone_else or s.is_reshare) and YOU_SAID.search(claim.sentence):
            misattributed.append(f"'{claim.sentence[:50]}' presents {s.speaker or 'a reshare'} as the prospect's words")
    checks.append(Check(name="attribution", passed=not misattributed, detail="; ".join(misattributed)))

    # Case 5: a crowded event may be context, never the opener.
    events = [signals[i] for i in crowded_ids or set() if i in signals]
    claimed_first = {c.signal_id for c in draft.claims if _in_body(c.sentence, opener) or _in_body(opener, c.sentence)}
    opened_with = [e for e in events if e.id in claimed_first or crowded.references_event(opener, e)]
    checks.append(Check(name="crowded_not_opener", passed=not opened_with,
                        detail=(f"the first sentence references a crowded event ({opened_with[0].summary[:60]}); "
                                "open with the hook, use the event as context later") if opened_with else ""))
    return checks


# --- Factual audit: read the whole email, never just the claims the writer declared ----------

FactStatus = Literal["supported", "unsupported", "contradicted", "uncertain", "not_verifiable"]
BLOCKING = ("unsupported", "contradicted")


class Evidence(BaseModel):
    """One fact the auditor may accept: a verified signal (prompt id), the seller profile, or the
    rep's notes. Stored with the critic report so a rep's later edit is checked against the same."""

    id: str
    text: str
    source_url: str | None = None


class _AuditClaim(BaseModel):
    sentence: str = Field(description="The sentence (or subject) exactly as it appears in the email")
    about: Literal["prospect", "company", "market", "seller", "other"]
    status: FactStatus
    fact_id: str | int | None = Field(default=None, description="Id of the fact that supports or contradicts it, else null")
    reason: str = Field(description="One short sentence")
    suggestion: str | None = Field(default=None, description="The sentence restated using only the facts, or null")


class _Audit(BaseModel):
    claims: list[_AuditClaim]
    genericness: int = Field(ge=0, le=5)
    genericness_reason: str = Field(description="One sentence")


def evidence_for(ctx: RunContext, mode: Mode) -> list[Evidence]:
    """The facts the writer was given (hook + support, never excluded ones), our offer, and the rep's
    trusted notes. Nothing the models wrote themselves (angle, reasoning) counts as evidence."""
    items = []
    if mode != "role_based":
        hook, support = pick_facts(ctx)
        to_prompt, _ = prompt_ids(ctx)
        for s in [hook, *support] if hook else []:
            when = s.published_at.strftime("%d %B %Y").lstrip("0") if s.published_at else "undated"
            items.append(Evidence(id=str(to_prompt[s.id]), source_url=s.source_url,
                                  text=f"{s.type}, {when}: {s.summary} Evidence: \"{s.evidence}\""))
    seller = ctx.seller
    items.append(Evidence(id="seller", text=prompts.get(
        "draft", "audit_seller", company=seller.get("company"), offer=" ".join(str(seller.get("offer", "")).split()),
        problem=" ".join(str(seller.get("problem_solved", "")).split()))))
    if ctx.row.get("rep_notes"):
        items.append(Evidence(id="notes", text=prompts.get("draft", "audit_notes", notes=ctx.row["rep_notes"])))
    return items


async def audit(evidence: list[Evidence], prospect: str, company: str | None, subject: str,
                body: str) -> tuple[_Audit, int]:
    """One fast-model pass: every factual sentence in the email against the evidence, plus the
    genericness score. Returns (audit, tokens)."""
    facts = "\n".join(f"[{e.id}] {e.text}" for e in evidence)
    user = prompts.get("draft", "audit_user", prospect=prospect, company=company or "their company",
                       facts=facts, subject=subject, body=body)
    result, usage = await llm.structured("fast", prompts.get("draft", "audit_system"), user, _Audit)
    return result, usage.prompt_tokens + usage.completion_tokens


def factual_report(result: _Audit, evidence: list[Evidence], subject: str, body: str,
                   check_numbers: bool = False) -> dict:
    """Code owns the verdict: sentences that aren't in the email are dropped, and 'supported' must
    name a fact that was actually given. check_numbers adds the digits / written-out quantities
    check for text no code check has seen yet (a rep's edit)."""
    by_id = {e.id: e for e in evidence}
    seller = by_id.get("seller")
    facts = [e for e in evidence if e.id != "seller"]
    text = f"{subject}\n{body}"
    issues, counts = [], {status: 0 for status in FactStatus.__args__}
    seen: set[str] = set()
    for claim in result.claims:
        if not claim.sentence.strip() or not _in_body(claim.sentence, text) or claim.sentence in seen:
            continue
        seen.add(claim.sentence)
        status, reason = claim.status, claim.reason
        fact = by_id.get(_fact_key(claim.fact_id))
        if (claim.about == "seller" or (status == "supported" and fact is None)) and status != "contradicted" \
                and seller and _coverage(claim.sentence, seller.text) >= 0.6:
            # What we sell is checked against seller_profile.yaml by code, not the model's say-so.
            status, fact, reason = "supported", seller, "Matches what we sell (seller profile)."
        elif status == "supported" and fact is None:
            best = max(facts, key=lambda e: _coverage(claim.sentence, e.text), default=None)
            cover = _coverage(claim.sentence, best.text) if best else 0.0
            if cover >= 0.5:  # the model forgot the id, but one fact clearly says it
                fact = best
            elif claim.about == "seller":  # our own product: only seller_profile.yaml can back it
                status, reason = "unsupported", "Says something about what we sell that the seller profile doesn't."
            elif _fact_key(claim.fact_id):  # an id that was never given: a made-up citation
                status, reason = "unsupported", "It cites no fact that was given to the writer."
            else:  # the model thinks it's fine but named nothing; code can't confirm it, so it only warns
                status, reason = "uncertain", "Marked supported, but no fact was named; check it against the sources."
        elif status == "unsupported" and claim.about != "seller":
            # The fast model is not consistent run to run: if a given fact says the same thing in
            # nearly the same words, with the same numbers, warn instead of blocking approval.
            best = max(facts, key=lambda e: _coverage(claim.sentence, e.text), default=None)
            if best and _coverage(claim.sentence, best.text) >= 0.7 \
                    and _numbers_in(claim.sentence) <= _numbers_in(best.text):
                status, fact = "uncertain", best
                reason = f"{reason} But fact [{best.id}] says much the same; check it against the source."
        counts[status] += 1
        if status in (*BLOCKING, "uncertain"):
            issues.append({"sentence": claim.sentence, "status": status, "reason": reason,
                           "evidence": fact.text if fact else None, "source_url": fact.source_url if fact else None,
                           "suggestion": claim.suggestion})
    if check_numbers:
        allowed = set().union(*(_numbers_in(e.text) for e in evidence)) if evidence else set()
        stripped = re.sub(r"\b\d+\s?[-‐-—]?\s?min(?:ute)?s?\b", "", body, flags=re.I)
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", stripped):
            invented = sorted(_numbers_in(sentence) - allowed)
            flagged = any(_in_body(i["sentence"], sentence) or _in_body(sentence, i["sentence"]) for i in issues)
            if invented and sentence.strip() and not flagged:
                counts["unsupported"] += 1
                issues.append({"sentence": sentence.strip(), "status": "unsupported", "source_url": None,
                               "reason": f"Numbers not in any fact: {', '.join(invented)}", "evidence": None,
                               "suggestion": None})
    blocking = [i for i in issues if i["status"] in BLOCKING]
    return {"checked": sum(v for k, v in counts.items() if k != "not_verifiable"),
            **{k: v for k, v in counts.items() if k != "not_verifiable"},
            "blocking": bool(blocking), "issues": issues,
            "evidence": [e.model_dump() for e in evidence]}


_STOP = set("your you our the and for with that this are can will from its their they them has have was were "
             "into about more most not but all any "
             # the ask ("would you be open to a 15-minute call to see how...") is not a claim
             "would open minute call chat see how explore help interested worth quick".split())


def _fact_key(fact_id: str | int | None) -> str:
    """'[1]', 'Fact 1', 1, 'Seller' -> '1', '1', '1', 'seller' (models vary how they write an id)."""
    key = str(fact_id if fact_id is not None else "").strip().strip("[]").strip().lower()
    return re.sub(r"^(?:fact|id)\s*", "", key)


def _coverage(sentence: str, text: str) -> float:
    """Share of the sentence's content words that appear in `text`."""
    def stem(w: str) -> str:  # "sdrs" = "sdr", "signals" = "signal"
        return w[:-1] if len(w) > 3 and w.endswith("s") else w

    words = [stem(w) for w in _words(sentence) if w not in _STOP]
    have = {stem(w) for w in _words(text)}
    return sum(w in have for w in words) / len(words) if words else 0.0


def blocking_message(report: dict | None) -> str | None:
    """The rep-facing reason a draft can't be approved yet, or None."""
    if not report or not report.get("blocking"):
        return None
    n = sum(1 for i in report["issues"] if i["status"] in BLOCKING)
    return f"{n} unsupported factual claim{'s' if n != 1 else ''}: edit {'them' if n != 1 else 'it'} before approving"


async def review(ctx: RunContext, draft: Draft, mode: Mode) -> tuple[list[Check], _Genericness | None, dict]:
    extracted: ExtractOutput = ctx.outputs["extract"]
    scored: ScoreOutput = effective_score(ctx)
    signals = {s.id: s for s in extracted.signals if s.type != "ai_screening"}
    excluded = {r.signal_id for r in scored.ranked if r.excluded}
    banned = (ctx.seller.get("tone") or {}).get("banned_phrases") or DEFAULT_BANNED
    checks = code_checks(draft, mode, signals, excluded, ctx.row["last_name"], banned, _crowded_ids(ctx),
                         display_name(ctx.row))
    evidence = evidence_for(ctx, mode)
    result, tokens = await audit(evidence, display_name(ctx.row), ctx.row.get("company"), draft.subject, draft.body)
    ctx.add_usage(tokens=tokens)
    generic = _Genericness(genericness=result.genericness, reason=result.genericness_reason) if mode == "hooked" else None
    return checks, generic, factual_report(result, evidence, draft.subject, draft.body)


def _problems(checks: list[Check], generic: _Genericness | None, factual: dict | None = None) -> list[str]:
    problems = [f"{c.name}: {c.detail}" if c.detail else c.name for c in checks if not c.passed]
    if generic and generic.genericness >= settings.genericness_fail_at:
        problems.append(f"too generic ({generic.genericness}/5): {generic.reason}")
    for issue in (factual or {}).get("issues", []):
        if issue["status"] in BLOCKING:
            problems.append(f"{issue['status']}_fact: \"{issue['sentence']}\" ({issue['reason']}) "
                            "Remove it or restate it using only the facts above.")
    return problems


def _uncertain(factual: dict) -> list[str]:
    return [f"check this fact: \"{i['sentence']}\" ({i['reason']})" for i in factual.get("issues", [])
            if i["status"] == "uncertain"]


def _confidence(ctx: RunContext, mode: Mode, warnings: list[str]) -> tuple[float | None, str]:
    identity = ctx.confidence if ctx.confidence is not None else 0.5
    scored: ScoreOutput = effective_score(ctx)
    hook = min(1.0, scored.hook.score / 5) if scored.hook else 0.3
    penalty = 0.8 if warnings else 1.0
    value = round(identity * hook * penalty, 2)
    return value, f"identity {identity} x hook strength {round(hook, 2)} x warnings {penalty}"


def _update_report(draft_id: int, report: dict) -> None:
    with Session(db.engine) as session:
        record = session.get(DraftRecord, draft_id)
        record.critic_report = {**record.critic_report, **report}
        session.add(record)
        session.commit()


async def run_critic(ctx: RunContext) -> CriticOutput:
    drafted: DraftOutput = ctx.outputs["draft"]
    mode = drafted.mode
    draft = Draft(subject=drafted.subject, body=drafted.body, claims=drafted.claims)
    checks, generic, factual = await review(ctx, draft, mode)
    problems = _problems(checks, generic, factual)
    first_report = {"checks": [c.model_dump() for c in checks], "problems": problems,
                    "genericness": generic.model_dump() if generic else None, "factual": factual}
    _update_report(drafted.draft_id, first_report)
    first_blocking = factual["blocking"]

    final_id, version, rewritten = drafted.draft_id, 1, False
    if problems:  # at most one rewrite, whatever it still gets wrong
        draft = await write(ctx, mode, previous=draft, problems=problems)
        checks, generic, factual = await review(ctx, draft, mode)
        problems = _problems(checks, generic, factual)
        final_id = _store_draft(ctx, draft, 2, drafted.hook_signal_id, {"mode": mode, "banner": drafted.banner})
        version, rewritten = 2, True

    passed = not problems
    warnings = problems + _uncertain(factual)  # shown in review, never hidden
    if mode == "role_based":
        warnings = [ROLE_BASED_BANNER, *warnings]
    elif mode == "outline":
        warnings = [SCREENING_BANNER, *warnings]
    confidence, formula = _confidence(ctx, mode, problems)
    ctx.confidence = confidence
    blocked = blocking_message(factual)
    if blocked:  # stays in the queue, but can't be approved until the rep fixes those sentences
        ctx.final_reason = blocked[0].upper() + blocked[1:]
    elif ctx.final_status == RunStatus.ready_for_review and not ctx.final_reason and problems:
        ctx.final_reason = f"Draft has {len(problems)} warning(s) to review"
    _update_report(final_id, {"checks": [c.model_dump() for c in checks], "problems": problems,
                              "genericness": generic.model_dump() if generic else None, "factual": factual,
                              "passed": passed, "warnings": warnings, "final": True,
                              "confidence": confidence, "confidence_formula": formula})

    if passed:
        message = "Draft passed all checks" + (" after one rewrite" if rewritten else "")
    elif blocked:
        message = f"Manual fix needed: {blocked}"
    else:
        message = f"Draft still has {len(problems)} issue(s) after a rewrite; flagged for review"
    message += f" · facts: {factual['supported']} verified, {factual['unsupported'] + factual['contradicted']} unsupported"
    if first_blocking and not blocked:
        message += " (unsupported ones removed by the rewrite)"
    if generic:
        message += f" · genericness {generic.genericness}/5"
    return CriticOutput(
        message=message, final_draft_id=final_id, version=version, passed=passed, checks=checks,
        genericness=generic.genericness if generic else None,
        genericness_reason=generic.reason if generic else None, rewritten=rewritten,
        warnings=warnings, confidence=confidence, confidence_formula=formula, banner=drafted.banner,
    )
