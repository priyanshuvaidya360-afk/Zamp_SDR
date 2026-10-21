"""Stage 2: resolve identity. Make sure we research the right person before anything else.

Spec: docs/GUIDE.md stage 2 and T06. A few targeted searches, then the fast model scores the
candidate people. Code decides: one candidate >= identity_accept with no rival >=
identity_plausible continues; anything else asks the rep (needs_input). Never guess.
LinkedIn is only ever searched for, never fetched.
"""

import asyncio
import re
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session

from app import db, llm, prompts, search
from app.config import settings
from app.ingest import normalise_domain, normalise_linkedin
from app.models import Run, RunStatus
from app.pipeline.runner import RunContext, StopRun, account_key_for
from app.search import SearchResponse

IDENTITY_SEARCH_DAYS = 3650  # identity is not about recent news


class Candidate(BaseModel):
    name: str
    title: str | None = Field(default=None, description="Current job title, if a source states it")
    company: str | None = Field(default=None, description="Current employer")
    company_domain: str | None = Field(default=None, description="Employer's website domain, e.g. acme.com")
    location: str | None = Field(default=None, description="City / region / country as a source states it")
    country: str | None = Field(default=None, description="ISO 3166-1 alpha-2 code for location, e.g. US, DE, IN")
    evidence_url: str = Field(description="The search result URL that best supports this match")
    evidence: str = Field(description="Short quote or paraphrase from that result")
    confidence: float = Field(ge=0, le=1, description="How sure this is the prospect, 0-1")


class IdentityCandidates(BaseModel):
    candidates: list[Candidate] = Field(description="Distinct people, best match first; empty if none")
    reasoning: str = Field(description="One or two sentences on how you decided")


class ResolveOutput(BaseModel):
    message: str
    person: Candidate
    candidates: list[Candidate] = []
    queries: list[str] = []
    checks: list[str] = []  # evidence checks code applied to the model's candidates
    rep_confirmed: bool = False
    company: str | None = None  # filled in when the CSV had no company name
    domain: str | None = None


def display_name(row: dict[str, Any]) -> str:
    return row.get("full_name") or f"{row['first_name']} {row['last_name']}"


def build_queries(row: dict[str, Any]) -> list[str]:
    name = display_name(row)
    short = f"{row['first_name']} {row['last_name']}"
    quoted = f'"{short}"'
    queries = []
    if row.get("domain"):
        queries.append(f"{quoted} site:{row['domain']}")
    if row.get("company"):
        queries.append(f'{quoted} "{row["company"]}"')
    if row.get("title"):
        queries.append(f"{name} {row['title']} {row.get('company') or ''}".strip())
    if row.get("linkedin_url"):
        queries.append(f'"{row["linkedin_url"]}" {name}')
    if not queries or (name != short and len(queries) < 4):
        queries.append(f"{name} {row.get('company') or row.get('domain') or ''}".strip())
    return list(dict.fromkeys(queries))[:4]


def format_results(responses: list[SearchResponse]) -> str:
    lines, seen = [], set()
    for response in responses:
        for result in response.results:
            if result.url in seen:
                continue
            seen.add(result.url)
            lines.append(f"- [{result.title}]({result.url})\n  {result.content[:400]}")
    return "\n".join(lines)


def verify_candidates(candidates: list[Candidate], row: dict[str, Any],
                      responses: list[SearchResponse]) -> list[str]:
    """Code checks the model's evidence instead of trusting its confidence. Returns notes.

    - Evidence URL is the prospect's own LinkedIn profile: strong, at least 0.9.
    - Evidence URL is not among the search results: the model made it up, cap at 0.5.
    - Evidence names neither the person + company/domain nor their LinkedIn: cap at 0.75,
      so it can never be auto-accepted.
    """
    linkedin = row.get("linkedin_url")
    results = {r.url.rstrip("/"): r for response in responses for r in response.results}
    company_terms = [t.lower() for t in (row.get("company"), row.get("domain")) if t]
    last = row["last_name"].lower()
    notes = []
    for c in candidates:
        url_linkedin = normalise_linkedin(c.evidence_url)[0]
        if linkedin and url_linkedin == linkedin:
            c.confidence = max(c.confidence, 0.9)
            continue
        source = results.get(c.evidence_url.rstrip("/"))
        if source is None:
            c.confidence = min(c.confidence, 0.5)
            notes.append(f"{c.name}: evidence URL not in the search results")
            continue
        text = f"{c.evidence} {source.title} {source.content}".lower()
        if last not in text or (company_terms and not any(t in text for t in company_terms)):
            if c.confidence > 0.75:
                notes.append(f"{c.name}: evidence doesn't name both the person and the company")
            c.confidence = min(c.confidence, 0.75)
    return notes


def _person_key(c: Candidate) -> tuple[str, str] | None:
    name = re.sub(r"\(.*?\)", "", c.name).lower()
    name = " ".join(re.sub(r"[^\w\s]", " ", name).split())
    employer = (c.company_domain or c.company or "").lower().strip()
    return (name, employer) if name and employer else None


def merge_same_person(candidates: list[Candidate]) -> list[Candidate]:
    """Several results about ONE person (their LinkedIn, a RocketReach page, a team page) are
    not rivals: same name + same employer = same person, keep the best-evidenced entry.
    Without this, strong evidence from two sources would wrongly look ambiguous."""
    best: dict[tuple[str, str], Candidate] = {}
    merged: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: c.confidence, reverse=True):
        key = _person_key(c)
        if key is None:
            merged.append(c)  # unknown employer: can't tell, keep it as its own person
        elif key not in best:
            best[key] = c
            merged.append(c)
    return merged


def decide(candidates: list[Candidate]) -> tuple[Candidate | None, str]:
    """(accepted person or None, reason). Pure code; the thresholds live in settings."""
    ranked = sorted(candidates, key=lambda c: c.confidence, reverse=True)
    if not ranked:
        return None, "Couldn't find this person in public search results. Confirm who they are."
    top = ranked[0]
    rivals = [c for c in ranked[1:] if c.confidence >= settings.identity_plausible]
    if top.confidence >= settings.identity_accept and not rivals:
        return top, ""
    if rivals:
        return None, f"{len(rivals) + 1} people could match. Pick the right one."
    if top.confidence >= settings.identity_plausible:
        return None, f"Best match is only {top.confidence:.2f} confident. Confirm it's the right person."
    return None, "No confident match in public search results. Confirm who they are."


def _apply_identity(ctx: RunContext, person: Candidate) -> tuple[str | None, str | None]:
    """Fill a missing company / domain from the confirmed person, and group the run by account."""
    company, domain = ctx.row.get("company"), ctx.row.get("domain")
    filled_company = filled_domain = None
    if not company and person.company:
        ctx.row["company"] = filled_company = person.company
    if not domain and person.company_domain:
        normalised, error = normalise_domain(person.company_domain)
        if normalised and not error:
            ctx.row["domain"] = filled_domain = normalised
    if filled_company or filled_domain:
        ctx.account_key = ctx.account_key or account_key_for(
            {"company_domain": ctx.row.get("domain"), "company": ctx.row.get("company")})
        with Session(db.engine) as session:
            run = session.get(Run, ctx.run_id)
            run.company = ctx.row.get("company")
            run.domain = ctx.row.get("domain")
            run.account_key = ctx.account_key
            session.add(run)
            session.commit()
    return filled_company, filled_domain


def _check_region(ctx: RunContext, person: Candidate) -> None:
    """Case 12: strict-consent regions are held for a human compliance check."""
    country = (person.country or "").upper()
    region = (ctx.seller.get("regions") or {}).get(country) or {}
    if region.get("strict"):
        raise StopRun(
            RunStatus.held,
            f"Compliance check needed: {person.name} appears to be in {country}, "
            "a region with strict consent rules for cold email.",
            {"country": country, "person": person.model_dump()},
        )


async def run(ctx: RunContext) -> ResolveOutput:
    # Resume: the rep picked (or typed) the right person; trust it, no new searches.
    if ctx.resume_input and ctx.resume_input.get("candidate"):
        person = Candidate.model_validate({**ctx.resume_input["candidate"], "confidence": 1.0})
        _check_region(ctx, person)
        company, domain = _apply_identity(ctx, person)
        ctx.confidence = 1.0
        return ResolveOutput(message=f"Confirmed by rep: {person.name}"
                             + (f", {person.title}" if person.title else ""),
                             person=person, rep_confirmed=True, company=company, domain=domain)

    queries = build_queries(ctx.row)
    responses = await asyncio.gather(*(
        search.search(q, depth="basic", days=IDENTITY_SEARCH_DAYS, max_results=5) for q in queries
    ))
    ctx.add_usage(credits=sum(r.credits for r in responses))

    row = ctx.row
    prospect = "\n".join(f"{label}: {value}" for label, value in (
        ("Name", display_name(row)), ("First name", row["first_name"]), ("Last name", row["last_name"]),
        ("Company", row.get("company")), ("Company domain", row.get("domain")),
        ("Title", row.get("title")), ("LinkedIn URL", row.get("linkedin_url")),
    ) if value)
    results = format_results(responses) or "(no results)"
    found, usage = await llm.structured(
        "fast", prompts.get("resolve", "system"),
        prompts.get("resolve", "user", prospect=prospect, results=results),
        IdentityCandidates,
    )
    ctx.add_usage(tokens=usage.prompt_tokens + usage.completion_tokens)

    candidates = list(found.candidates)
    checks = verify_candidates(candidates, row, responses)
    candidates = merge_same_person(candidates)
    person, reason = decide(candidates)
    if person is None:
        raise StopRun(RunStatus.needs_input, reason, {
            "candidates": [c.model_dump() for c in candidates[:5]],
            "queries": queries,
            "reasoning": found.reasoning,
            "checks": checks,
        })

    _check_region(ctx, person)
    company, domain = _apply_identity(ctx, person)
    ctx.confidence = person.confidence
    detail = ", ".join(filter(None, (person.title, person.company)))
    return ResolveOutput(
        message=f"Found 1 match, confidence {person.confidence:.2f}: {person.name}"
                + (f" ({detail})" if detail else ""),
        person=person, candidates=candidates[:5], queries=queries, checks=checks,
        company=company, domain=domain,
    )
