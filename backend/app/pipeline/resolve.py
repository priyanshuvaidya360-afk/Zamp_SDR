"""Stage 2: resolve identity. Make sure we research the right person before anything else.

Spec: docs/PROJECT_SPEC.md stage 2 and T06. A few targeted searches, then the fast model scores the
candidate people. Code decides: one candidate >= identity_accept with no rival >=
identity_plausible continues; anything else asks the rep (needs_input). Never guess.
LinkedIn is only ever searched for, never fetched.

When no one is accepted, diagnose() says why, from code checks: search_failed (search again),
bad_data (fix the row or dismiss it), not_public (a real company, but the person never appears
online: the rep can vouch, ask for a company-level draft, or dismiss), ambiguous, unconfirmed.
"""

import asyncio
import re
from typing import Any, Literal

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
    found: list[dict[str, str]] = []  # every identity search result (url / title): the Sources tab
    rep_confirmed: bool = False
    unverified: bool = False  # company-level draft: the person was never found, the rep went ahead
    company: str | None = None  # filled in when the CSV had no company name
    domain: str | None = None


def found_results(responses: list[SearchResponse]) -> list[dict[str, str]]:
    """Every distinct result, for the run's Sources tab (links only; nothing here is fetched)."""
    return list({r.url: {"url": r.url, "title": r.title[:150]} for resp in responses for r in resp.results}.values())


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

    - Evidence URL is the prospect's own LinkedIn profile: certain, 1.0.
    - Evidence URL is not among the search results: the model made it up, cap at 0.5.
    - Evidence names neither the person + company/domain nor their LinkedIn: cap at 0.75,
      so it can never be auto-accepted.
    - Same first + last name and the prospect's company domain, evidence checked: certain, 1.0,
      unless check_namesakes() finds a second person with that name at that company.
    """
    linkedin = row.get("linkedin_url")
    domain = normalise_domain(row.get("domain") or "")
    results = {r.url.rstrip("/"): r for response in responses for r in response.results}
    company_terms = [t.lower() for t in (row.get("company"), row.get("domain")) if t]
    last = row["last_name"].lower()
    notes = []
    for c in candidates:
        url_linkedin = normalise_linkedin(c.evidence_url)[0]
        if linkedin and url_linkedin == linkedin:
            c.confidence = 1.0
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
            continue
        if domain and _same_name(c, row) and normalise_domain(c.company_domain or "") == domain:
            c.confidence = 1.0
    return notes + check_namesakes(candidates, row)


def _same_name(c: Candidate, row: dict[str, Any]) -> bool:
    words = _plain_name(c.name).split()
    return bool(words) and words[0] == _plain_name(row["first_name"]) and words[-1] == _plain_name(row["last_name"])


def _plain_name(name: str) -> str:
    name = re.sub(r"\(.*?\)", "", name).lower()
    return " ".join(re.sub(r"[^\w\s]", " ", name).split())


def _profile(c: Candidate) -> str | None:
    """The LinkedIn profile a candidate's evidence is, if it is one."""
    return normalise_linkedin(c.evidence_url)[0] if "linkedin.com/in/" in c.evidence_url.lower() else None


def check_namesakes(candidates: list[Candidate], row: dict[str, Any]) -> list[str]:
    """Two people with the prospect's name at the same company (different LinkedIn profiles)
    can't be told apart by name + domain: none of them is certain,
    the rep picks. A match on the prospect's own LinkedIn URL still settles it."""
    linkedin = row.get("linkedin_url")
    groups: dict[tuple[str, str], list[Candidate]] = {}
    for c in candidates:
        if _same_name(c, row) and (key := _person_key(c)):
            groups.setdefault(key, []).append(c)
    notes = []
    for (_, employer), group in groups.items():
        if not _different_people(group):
            continue
        exact = [c for c in group if linkedin and _profile(c) == linkedin]
        for c in group:
            if exact and c not in exact:
                c.confidence = min(c.confidence, 0.3)  # a namesake: not the LinkedIn URL in the CSV
            elif not exact:
                c.confidence = min(c.confidence, settings.identity_plausible)
        notes.append(f"{len(group)} people named {group[0].name} at {employer}"
                     + (": kept the one whose LinkedIn matches" if exact else ": ask the rep"))
    return notes


def _different_people(group: list[Candidate]) -> bool:
    """Several entries with one name + employer are usually one person found twice (merged later).
    They are different people when their LinkedIn profiles differ. Titles alone don't count:
    'VP Finance' on one page and 'Vice President, Finance' on another is one person."""
    return len({p for c in group if (p := _profile(c))}) > 1


def _person_key(c: Candidate) -> tuple[str, str] | None:
    name = _plain_name(c.name)
    employer = (c.company_domain or c.company or "").lower().strip()
    return (name, employer) if name and employer else None


def merge_same_person(candidates: list[Candidate]) -> list[Candidate]:
    """Several results about ONE person (their LinkedIn, a RocketReach page, a team page) are
    not rivals: same name + same employer = same person, keep the best-evidenced entry.
    Without this, strong evidence from two sources would wrongly look ambiguous.
    Namesakes at one company (_different_people) stay apart, so the rep can pick."""
    kept: dict[tuple[str, str], list[Candidate]] = {}
    merged: list[Candidate] = []
    for c in sorted(candidates, key=lambda c: c.confidence, reverse=True):
        key = _person_key(c)
        if key is None:
            merged.append(c)  # unknown employer: can't tell, keep it as its own person
        elif all(_different_people([k, c]) for k in kept.get(key, [])):
            kept.setdefault(key, []).append(c)
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


Outcome = Literal["search_failed", "bad_data", "not_public", "ambiguous", "unconfirmed"]


class IdentityCheck(BaseModel):
    label: str
    ok: bool | None  # None: couldn't be checked


class Diagnosis(BaseModel):
    outcome: Outcome
    reason: str
    checks: list[IdentityCheck]


# Names that are test data, not people. Matched against the whole first or last name.
PLACEHOLDER_NAMES = {"test", "testing", "tester", "asdf", "qwerty", "foo", "xxx", "n/a", "none", "null",
                     "unknown", "sample", "example", "dummy", "fake", "lorem", "ipsum"}


def placeholder_name(first: str, last: str) -> str | None:
    """Why the name looks like test data, or None. Code rules only, kept strict: a real but
    unusual name must never be called fake."""
    full = f"{first} {last}".strip()
    if any(ch.isdigit() for ch in full):
        return f"'{full}' contains digits"
    if {first.lower(), last.lower()} & PLACEHOLDER_NAMES:
        return f"'{full}' looks like a placeholder"
    if (first.lower(), last.lower()) in {("john", "doe"), ("jane", "doe")}:
        return f"'{full}' is a placeholder name"
    return None


def _search_failed(response: SearchResponse) -> bool:
    """Every provider errored (or none was available): no answer, as opposed to 'nothing found'."""
    if response.provider != "none":
        return False
    return not response.fallbacks or any(not f.endswith(": no results") for f in response.fallbacks)


def _texts(responses: list[SearchResponse]) -> list[str]:
    """Each distinct result once, as one lowercase string (URL, title and snippet)."""
    return list({r.url: f"{r.url} {r.title} {r.content}".lower() for resp in responses for r in resp.results}.values())


def _results(n: int) -> str:
    return f"{n} result{'s' if n != 1 else ''}"


async def diagnose(row: dict[str, Any], responses: list[SearchResponse],
                   candidates: list[Candidate], reason: str) -> Diagnosis:
    """Why identity failed, from checks code can make. `reason` is decide()'s, kept for the
    ambiguous / unconfirmed outcomes, where the candidate cards already explain it. We can never
    prove a person doesn't exist, so the worst verdict is 'looks like bad data'."""
    name, company, domain = display_name(row), row.get("company"), row.get("domain")
    checks: list[IdentityCheck] = []

    if all(_search_failed(r) for r in responses):
        errors = sorted({f.split(":")[0] for r in responses for f in r.fallbacks if not f.endswith(": no results")})
        checks.append(IdentityCheck(label="Search unavailable" + (f" ({', '.join(errors)})" if errors else ""), ok=False))
        return Diagnosis(outcome="search_failed", checks=checks,
                         reason="Web search wasn't available, so nothing could be checked. Search again.")
    results = len({r.url for resp in responses for r in resp.results})
    checks.append(IdentityCheck(label=f"Search working ({len(responses)} queries, {_results(results)})", ok=True))

    fake = placeholder_name(row["first_name"], row["last_name"])
    checks.append(IdentityCheck(label=f"Name looks like test data: {fake}" if fake else "Name looks real", ok=not fake))

    if (email := row.get("email")) and domain:
        same = email.rsplit("@", 1)[-1] == domain
        checks.append(IdentityCheck(label=f"Email is at {domain}" if same else f"Email is not at {domain}", ok=same))

    texts = _texts(responses)
    company_terms = [t.lower() for t in (company, domain) if t]
    about_company = [t for t in texts if any(c in t for c in company_terms)]
    company_hits = len(about_company)
    if company_terms:
        checks.append(IdentityCheck(label=f"{company or domain} named in {_results(company_hits)}", ok=company_hits > 0))
    site_up = None
    if domain and not candidates and not company_hits:  # one cheap request, only when it decides something
        site_up = await search.site_reachable(domain)
        checks.append(IdentityCheck(label={True: f"{domain} loads", False: f"{domain} doesn't load",
                                           None: f"Couldn't check whether {domain} loads"}[site_up], ok=site_up))
    last = re.compile(rf"\b{re.escape(row['last_name'].lower())}\b")
    if company_terms:  # the person together with the company, not anyone with that surname
        together = sum(1 for t in about_company if last.search(t))
        checks.append(IdentityCheck(label=f"{row['last_name']} named with {company or domain} in {_results(together)}",
                                    ok=together > 0))
    else:
        together = sum(1 for t in texts if last.search(t))
        checks.append(IdentityCheck(label=f"{row['last_name']} named in {_results(together)}", ok=together > 0))

    if fake:
        return Diagnosis(outcome="bad_data", checks=checks,
                         reason=f"This row looks like bad data: {fake}. Fix the row or dismiss it.")
    if site_up is False:
        return Diagnosis(outcome="bad_data", checks=checks,
                         reason=f"This row looks like bad data: {domain} doesn't load and no search result "
                                "mentions it. Fix the row or dismiss it.")
    if candidates:
        rivals = [c for c in candidates if c.confidence >= settings.identity_plausible]
        return Diagnosis(outcome="ambiguous" if len(rivals) > 1 else "unconfirmed", checks=checks, reason=reason)
    if together:  # mentioned, but the model couldn't tie a page to them: the rep confirms
        return Diagnosis(outcome="unconfirmed", checks=checks,
                         reason=f"{name} appears in {_results(together)}, but none confirms who they are. "
                                "Confirm it's the right person.")
    if company_hits or site_up:
        reason = f"{company or domain} is real, but {name} doesn't appear anywhere online with it."
    else:
        reason = f"{name} doesn't appear in any public search result."
    return Diagnosis(outcome="not_public", checks=checks, reason=reason)


def _unverified_person(ctx: RunContext) -> Candidate:
    """The rep asked for a company-level draft: the CSV's details, explicitly not verified."""
    row = ctx.row
    if not (row.get("company") or row.get("domain")):
        raise StopRun(RunStatus.needs_input, "A company-level draft needs the company: enter their details instead.",
                      {"outcome": "not_public"})
    return Candidate(name=display_name(row), title=row.get("title"), company=row.get("company"),
                     company_domain=row.get("domain"), evidence_url="",
                     evidence="Not found online; the rep asked for a company-level draft",
                     confidence=settings.identity_unverified)


def _save_run(ctx: RunContext, **fields: Any) -> None:
    with Session(db.engine) as session:
        run = session.get(Run, ctx.run_id)
        for key, value in fields.items():
            setattr(run, key, value)
        session.add(run)
        session.commit()


def _apply_identity(ctx: RunContext, person: Candidate, rep: bool = False) -> tuple[str | None, str | None]:
    """Fill a missing company / domain from the confirmed person, and group the run by account.
    What the rep typed (`rep`) is trusted over the CSV: it replaces company, domain and title."""
    company, domain = ctx.row.get("company"), ctx.row.get("domain")
    filled_company = filled_domain = filled_title = None
    if person.company and (not company or (rep and person.company != company)):
        ctx.row["company"] = filled_company = person.company
    if person.company_domain and (not domain or rep):
        normalised, error = normalise_domain(person.company_domain)
        if normalised and not error and normalised != domain:
            ctx.row["domain"] = filled_domain = normalised
    if rep and person.title and person.title != ctx.row.get("title"):
        ctx.row["title"] = filled_title = person.title
    if filled_company or filled_domain or filled_title:
        key = account_key_for({"company_domain": ctx.row.get("domain"), "company": ctx.row.get("company")})
        ctx.account_key = key if rep else (ctx.account_key or key)
        with Session(db.engine) as session:
            run = session.get(Run, ctx.run_id)
            run.company = ctx.row.get("company")
            run.domain = ctx.row.get("domain")
            run.title = ctx.row.get("title")
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
    resume = ctx.resume_input or {}
    if resume.get("dismiss"):
        raise StopRun(RunStatus.dismissed, "Dismissed by the rep: identity couldn't be verified.")
    if resume.get("unverified"):
        person = _unverified_person(ctx)
        _save_run(ctx, identity_unverified=True)
        ctx.confidence = person.confidence
        return ResolveOutput(message=f"Not verified: {person.name}. Writing a company-level draft "
                                     f"for {person.company or person.company_domain}",
                             person=person, unverified=True)
    # Resume: the rep picked (or typed) the right person; trust it, no new searches.
    if resume.get("candidate"):
        person = Candidate.model_validate({**ctx.resume_input["candidate"], "confidence": 1.0})
        _check_region(ctx, person)
        company, domain = _apply_identity(ctx, person, rep=True)
        ctx.confidence = 1.0
        return ResolveOutput(message=f"Confirmed by rep: {person.name}"
                             + (f", {person.title}" if person.title else ""),
                             person=person, rep_confirmed=True, company=company, domain=domain)

    if fix := resume.get("fix"):
        # The rep corrected a bad-data row: search again with the new details, trusting nothing yet.
        corrected = Candidate.model_validate({**fix, "name": display_name(ctx.row), "evidence_url": "",
                                              "evidence": "", "confidence": 0})
        _apply_identity(ctx, corrected, rep=True)

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
        diagnosis = await diagnose(row, responses, candidates, reason)
        _save_run(ctx, identity_outcome=diagnosis.outcome)
        raise StopRun(RunStatus.needs_input, diagnosis.reason, {
            "candidates": [c.model_dump() for c in candidates[:5]],
            "queries": queries,
            "reasoning": found.reasoning,
            "checks": checks,
            "outcome": diagnosis.outcome,
            "diagnosis": [c.model_dump() for c in diagnosis.checks],
            "found": found_results(responses),
        })

    _check_region(ctx, person)
    company, domain = _apply_identity(ctx, person)
    ctx.confidence = person.confidence
    detail = ", ".join(filter(None, (person.title, person.company)))
    return ResolveOutput(
        message=f"Found 1 match, confidence {person.confidence:.2f}: {person.name}"
                + (f" ({detail})" if detail else ""),
        person=person, candidates=candidates[:5], queries=queries, checks=checks, found=found_results(responses),
        company=company, domain=domain,
    )
