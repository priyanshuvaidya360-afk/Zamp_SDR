"""Stage 3: gather signals. One search per signal category, then read the best pages.

Spec: docs/GUIDE.md stage 3 and T06. Company-level searches run once per account and are
shared by everyone at that company (ctx.batch.company_research). Caps per run:
gather_max_searches searches, gather_max_extracts page reads. Page text is untrusted: it is
only truncated here and stays data; stage 4 scans and extracts it.
"""

import asyncio
import re
from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from app import search
from app.config import settings
from app.pipeline.resolve import ResolveOutput, display_name
from app.pipeline.runner import RunContext
from app.search import SearchResult

About = Literal["company", "person"]

# Category -> (query, Tavily topic). Plain queries: keyword-stuffed ones pulled in job
# boards and listicles. "news" makes Tavily return articles for event-type categories.
COMPANY_QUERIES = {
    "funding": ('"{company}" raises funding round', "news"),
    "hiring": ('"{company}" hiring {hiring_focus}', "general"),  # roles the seller cares about
    "product_launch": ('"{company}" launches', "news"),
    "leadership_change": ('"{company}" appoints', "news"),
    "expansion": ('"{company}" expansion partnership', "news"),
}
PERSON_QUERIES = {
    "person_content": ('"{name}" interview OR podcast OR talk', "general"),
    "person_news": ('"{name}" "{company}"', "general"),
}


def is_about_company(result: SearchResult, terms: list[str]) -> bool:
    """The company is in the title or URL, not just named once inside a listicle. Whole words
    only: "Reco" must not match ReconAfrica or "recognition"."""
    headline = f"{result.url} {result.title}".lower()
    return any(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", headline) for term in terms)


def is_about_person(result: SearchResult, name: str, terms: list[str]) -> bool:
    """Full name and company both appear: drops namesakes (another Ryan Yu, a list of names)."""
    text = f"{result.url} {result.title} {result.content}".lower()
    return name in text and any(term in text for term in terms)


class Source(BaseModel):
    category: str
    about: About
    url: str
    title: str = ""
    snippet: str = ""
    published_date: str | None = None
    text: str | None = None  # page text, truncated; None if the page could not be read


class QueryLog(BaseModel):
    category: str
    about: About
    query: str
    provider: str
    results: int
    relevant: int = 0  # results that passed the relevance gate
    relevant_results: list[dict[str, str]] = []  # url / title / snippet: counts outlets (case 5)
    credits: float
    cached: bool = False
    fallbacks: list[str] = []


class GatherOutput(BaseModel):
    message: str
    sources: list[Source] = []
    queries: list[QueryLog] = []
    searches: int = 0
    extracts: int = 0
    credits: float = 0.0


class _Collected(BaseModel):
    sources: list[Source]
    queries: list[QueryLog]
    extracts: int
    credits: float


async def _search_and_read(queries: dict[str, tuple[str, str]], about: About, max_urls: int,
                           relevant_to: Callable[[SearchResult], bool], days: int | None = None) -> _Collected:
    """Run the queries in parallel, keep relevant results, read the top 1-2 per category."""
    categories = list(queries)
    responses = await asyncio.gather(*(
        search.search(queries[c][0], depth="basic", days=days or settings.signal_window_days,
                      max_results=5, topic=queries[c][1])
        for c in categories
    ))
    relevant = [
        [r for r in response.results if relevant_to(r) and not search.is_blocked(r.url)]
        for response in responses
    ]
    logs = [
        QueryLog(category=c, about=about, query=queries[c][0], provider=r.provider, results=len(r.results),
                 relevant=len(rel), credits=r.credits, cached=r.cached, fallbacks=r.fallbacks,
                 relevant_results=[{"url": x.url, "title": x.title, "snippet": x.content[:300],
                                   "published_date": x.published_date or ""} for x in rel])
        for c, r, rel in zip(categories, responses, relevant)
    ]

    # Round-robin: every category's best result first, then second-best, until the cap.
    picked: dict[str, Source] = {}
    for rank in range(2):
        for category, results in zip(categories, relevant):
            if rank < len(results) and len(picked) < max_urls:
                result = results[rank]
                if result.url not in picked:
                    picked[result.url] = Source(
                        category=category, about=about, url=result.url, title=result.title,
                        snippet=result.content[:500], published_date=result.published_date,
                    )

    credits = sum(r.credits for r in responses)
    extracts = 0
    if picked:
        pages = await search.extract(list(picked))
        credits += pages.credits
        extracts = len(picked)
        for url, text in pages.pages.items():
            if url in picked:
                picked[url].text = text[: settings.source_max_chars]
    return _Collected(sources=list(picked.values()), queries=logs, extracts=extracts, credits=credits)


def _fill(templates: dict[str, tuple[str, str]], values: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {c: (" ".join(q.format(**values).split()), topic) for c, (q, topic) in templates.items()}


def company_terms(company: str, domain: str) -> list[str]:
    """'acme fintech', 'acmefintech.com', 'acmefintech' (domain root, if distinctive)."""
    root = domain.split(".")[0] if domain else ""
    return [t for t in (company.lower().strip(), domain.lower(), root if len(root) >= 4 else "") if t]


async def run(ctx: RunContext) -> GatherOutput:
    resolved: ResolveOutput | None = ctx.outputs.get("resolve")
    person = resolved.person if resolved else None
    company = ctx.row.get("company") or (person.company if person else "") or ""
    domain = ctx.row.get("domain") or ""
    values = {"company": company, "domain": domain, "name": display_name(ctx.row),
              "hiring_focus": ctx.seller.get("hiring_focus", "")}

    person_queries = _fill(PERSON_QUERIES, values)
    company_budget = max(0, settings.gather_max_searches - len(person_queries))
    company_queries = _fill(dict(list(COMPANY_QUERIES.items())[:company_budget]), values)
    terms = company_terms(company, domain)
    full_name = f"{ctx.row['first_name']} {ctx.row['last_name']}".lower()
    person_reads = min(3, settings.gather_max_extracts)
    company_reads = settings.gather_max_extracts - person_reads

    did_company_research = False

    async def company_research() -> _Collected:
        # Runs once per account, inside whichever run got there first: that run pays.
        nonlocal did_company_research
        did_company_research = True
        return await _search_and_read(company_queries, "company", company_reads,
                                      lambda r: is_about_company(r, terms))

    shared, own = await asyncio.gather(
        ctx.batch.company_research(ctx.account_key, company_research),
        _search_and_read(person_queries, "person", person_reads,
                         lambda r: is_about_person(r, full_name, terms)),
    )
    credits = own.credits + (shared.credits if did_company_research else 0)
    ctx.add_usage(credits=credits)

    sources = shared.sources + own.sources
    read = sum(1 for s in sources if s.text)
    searches = len(shared.queries) + len(own.queries)
    return GatherOutput(
        message=(f"{searches} searches, {read} of {len(sources)} pages read "
                 f"({sum(s.about == 'company' for s in sources)} company, "
                 f"{sum(s.about == 'person' for s in sources)} person) · {credits:g} credits"),
        sources=sources,
        queries=shared.queries + own.queries,
        searches=searches,
        extracts=shared.extracts + own.extracts,
        credits=credits,
    )


SECOND_ORDER_QUERY = '"{company}" hiring OR expands OR "new market" OR appoints'


async def second_order(ctx: RunContext, after: date) -> tuple[list[Source], float]:
    """Case 5: one extra search for what happened after a crowded event (hiring, new markets,
    new leaders), limited to the days since it. Returns (pages read, credits)."""
    company = ctx.row.get("company") or ""
    terms = company_terms(company, ctx.row.get("domain") or "")
    today = settings.today or date.today()
    query = {"second_order": (SECOND_ORDER_QUERY.format(company=company), "general")}
    collected = await _search_and_read(query, "company", 2, lambda r: is_about_company(r, terms),
                                       days=max(1, (today - after).days))
    return collected.sources, collected.credits
