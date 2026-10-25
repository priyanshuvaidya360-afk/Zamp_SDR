"""Stage 3: gather signals. Search widely, keep what is about this company or person, read the best pages.

Spec: docs/PROJECT_SPEC.md stage 3 and T06. Company-level searches run once per account and are
shared by everyone at that company (ctx.batch.company_research). Caps per run:
gather_max_searches searches, gather_max_extracts page reads. Page text is untrusted: it is
only truncated here and stays data; stage 4 scans and extracts it.

Four kinds of search, in priority order (the budget trims from the end):
- core: funding, hiring, launches, leaders, expansion; the person's talks and news (Tavily first)
- own site: the company's site and blog (site:domain). For a small company that rarely makes the
  news, its product, pricing, customer and careers pages are often the only real information,
  and they become company_focus signals in stage 4
- wide: customers and case studies, reviews, awards, general news; the person's own writing
  (DuckDuckGo first, so exploring more doesn't spend Tavily credits)
- planned: a few company searches the fast model writes (agents/stage3_plan_extra_searches.md), once per
  company inside the shared research, from the company alone: colleagues share one plan, so it
  doesn't depend on whose run got there first. Checked by code (must name the company or its
  domain, never LinkedIn) and run DuckDuckGo first
"""

import asyncio
import re
from collections.abc import Callable
from datetime import date
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app import llm, prompts, search
from app.config import settings
from app.pipeline.resolve import ResolveOutput, display_name
from app.pipeline.runner import RunContext
from app.search import SearchResult

About = Literal["company", "person"]
Query = tuple[str, str, bool]  # (query, Tavily topic, free_first)

# Category -> (query, Tavily topic). Plain queries: keyword-stuffed ones pulled in job
# boards and listicles. "news" makes Tavily return articles for event-type categories.
COMPANY_QUERIES = {
    "funding": ('"{company}" raises funding round', "news"),
    "hiring": ('"{company}" hiring {hiring_focus}', "general"),  # roles the seller cares about
    "product_launch": ('"{company}" launches', "news"),
    "leadership_change": ('"{company}" appoints', "news"),
    "expansion": ('"{company}" expansion partnership', "news"),
}
# Wider nets for companies the news rarely covers.
COMPANY_WIDE_QUERIES = {
    "customers": ('"{company}" customer OR "case study" OR clients', "general"),
    "reviews": ('"{company}" reviews G2 OR Capterra OR Trustpilot', "general"),
    "recognition": ('"{company}" award OR recognized OR featured OR ranked', "general"),
    "news": ('"{company}" news {domain}', "news"),
}
# The company's own pages, read without a date window (product and about pages are rarely dated;
# stage 4 still drops dated facts older than signal_window_days).
SITE_QUERIES = {
    "company_site": ("site:{domain} {company}", "general"),
    "company_blog": ("site:{domain} blog OR news OR announcement", "general"),
}
SITE_READS = 2
SITE_DAYS = 3650
PERSON_QUERIES = {
    "person_content": ('"{name}" interview OR podcast OR talk', "general"),
    "person_news": ('"{name}" "{company}"', "general"),
}
PERSON_WIDE_QUERIES = {
    "person_writing": ('"{name}" article OR blog OR wrote OR webinar', "general"),
}
PERSON_READS = 4


def _named(term: str, text: str) -> list[str]:
    """Whole-word occurrences: "Reco" must not match ReconAfrica or "recognition"."""
    return re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text)


def is_about_company(result: SearchResult, terms: list[str]) -> bool:
    """The company is in the title or URL, or the text keeps naming it (its domain once, or its
    name twice): small companies are often covered inside an article, not in its headline. A
    single passing mention in a listicle still doesn't count."""
    headline = f"{result.url} {result.title}".lower()
    if any(_named(term, headline) for term in terms):
        return True
    body = result.content.lower()
    return any(len(_named(term, body)) >= (1 if "." in term else 2) for term in terms)


def on_site(url: str, domain: str) -> bool:
    """The page is on the company's own website (any subdomain)."""
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return bool(domain) and (host == domain or host.endswith("." + domain))


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
    found: list[dict[str, str]] = []  # every result (url / title), kept or not: the run's Sources tab
    credits: float
    cached: bool = False
    fallbacks: list[str] = []


class PlannedQuery(BaseModel):
    query: str = Field(description="The search, under 12 words, naming the company or its domain")
    why: str = Field(default="", description="One short clause: what it might find")


class _Plan(BaseModel):
    queries: list[PlannedQuery] = Field(description="The extra searches, most promising first")


class GatherOutput(BaseModel):
    message: str
    sources: list[Source] = []
    queries: list[QueryLog] = []
    planned: list[PlannedQuery] = []  # the planner's searches that code accepted (shared by the account)
    searches: int = 0
    extracts: int = 0
    credits: float = 0.0


class _Collected(BaseModel):
    sources: list[Source]
    queries: list[QueryLog]
    extracts: int
    credits: float
    planned: list[PlannedQuery] = []

    def __add__(self, other: "_Collected") -> "_Collected":
        seen = {s.url for s in self.sources}
        return _Collected(sources=self.sources + [s for s in other.sources if s.url not in seen],
                          queries=self.queries + other.queries, extracts=self.extracts + other.extracts,
                          credits=self.credits + other.credits, planned=self.planned + other.planned)


EMPTY = _Collected(sources=[], queries=[], extracts=0, credits=0)


async def _search_and_read(queries: dict[str, Query], about: About, max_urls: int,
                           relevant_to: Callable[[SearchResult], bool], days: int | None = None) -> _Collected:
    """Run the queries in parallel, keep relevant results, read the top 1-2 per category."""
    if not queries:
        return EMPTY
    categories = list(queries)
    responses = await asyncio.gather(*(
        search.search(queries[c][0], depth="basic", days=days or settings.signal_window_days,
                      max_results=5, topic=queries[c][1], free_first=queries[c][2])
        for c in categories
    ))
    relevant = [
        [r for r in response.results if relevant_to(r) and not search.is_blocked(r.url)]
        for response in responses
    ]
    logs = [
        QueryLog(category=c, about=about, query=queries[c][0], provider=r.provider, results=len(r.results),
                 relevant=len(rel), credits=r.credits, cached=r.cached, fallbacks=r.fallbacks,
                 found=[{"url": x.url, "title": x.title[:150]} for x in r.results],
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


def _fill(templates: dict[str, tuple[str, str]], values: dict[str, Any], free_first: bool = False) -> dict[str, Query]:
    return {c: (" ".join(q.format(**values).split()), topic, free_first) for c, (q, topic) in templates.items()}


def search_name(company: str) -> str:
    """The company name as people write it, without LinkedIn-style extras that no headline uses:
    'CredWise Finserv India | Home Loan Experts' -> 'CredWise Finserv India',
    'Fast VPS & FinTech Services (FVFS)' -> 'Fast VPS & FinTech Services'."""
    name = re.split(r"\s+[|–—-]\s+|\s*\|\s*", company.strip())[0]
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return name or company.strip()


def company_terms(company: str, domain: str) -> list[str]:
    """'acme fintech', 'acmefintech.com', 'acmefintech' (domain root, if distinctive)."""
    root = domain.split(".")[0] if domain else ""
    return [t for t in (company.lower().strip(), domain.lower(), root if len(root) >= 4 else "") if t]


def _within(budget: int, *groups: dict[str, Query]) -> list[dict[str, Query]]:
    """Trim the groups, in priority order, to `budget` searches in total."""
    trimmed = []
    for group in groups:
        keep = dict(list(group.items())[:max(0, budget)])
        budget -= len(keep)
        trimmed.append(keep)
    return trimmed


# --- The planner: a few searches written for this company -------------------------------------


def accept_planned(q: PlannedQuery, company: str, domain: str, existing: set[str]) -> str | None:
    """The query, cleaned, if code accepts it; None otherwise. It must name the company or its
    domain (so it can't wander off to unrelated searches), never touch LinkedIn, and be new."""
    query = " ".join(q.query.split())
    lowered = query.lower()
    subject = [t for t in (company.lower(), domain.lower()) if t]
    if not 3 <= len(query) <= 150 or not any(t in lowered for t in subject):
        return None
    if "linkedin" in lowered or lowered in existing:
        return None
    site = re.search(r"site:(\S+)", lowered)
    if site and not (domain and site.group(1).removeprefix("www.").endswith(domain.lower())):
        return None  # site: only on their own domain; anywhere else is just a normal search
    return query


async def plan_queries(ctx: RunContext, company: str, domain: str,
                       existing: list[str], count: int) -> list[PlannedQuery]:
    """Ask the fast model for `count` extra company searches. The prompt holds only the company
    and the seller, never the prospect, so every colleague's run would ask the same question.
    Any failure means no extra searches, never a failed run."""
    if count <= 0 or not company:
        return []
    seller, icp = ctx.seller, ctx.seller.get("icp") or {}
    user = prompts.get(
        "research", "user", offer=" ".join(str(seller.get("offer", "")).split()),
        buyers=", ".join(icp.get("buyer_titles", [])), triggers="; ".join(icp.get("triggers", [])),
        company=company, domain=f" ({domain})" if domain else "",
        planned="\n".join(f"- {q}" for q in existing), count=count)
    try:
        plan, usage = await llm.structured("fast", prompts.get("research", "system"), user, _Plan)
    except llm.LLMUnavailable:
        return []
    ctx.add_usage(tokens=usage.prompt_tokens + usage.completion_tokens)
    seen = {q.lower() for q in existing}
    accepted = []
    for q in plan.queries:
        if len(accepted) == count:
            break
        if (query := accept_planned(q, company, domain, seen)) is not None:
            seen.add(query.lower())
            accepted.append(q.model_copy(update={"query": query}))
    return accepted


async def run(ctx: RunContext) -> GatherOutput:
    resolved: ResolveOutput | None = ctx.outputs.get("resolve")
    person = resolved.person if resolved else None
    company = search_name(ctx.row.get("company") or (person.company if person else "") or "")
    domain = ctx.row.get("domain") or ""
    name = display_name(ctx.row)
    values = {"company": company, "domain": domain, "name": name,
              "hiring_focus": ctx.seller.get("hiring_focus", "")}

    fixed = [
        _fill(PERSON_QUERIES, values),
        _fill(COMPANY_QUERIES, values),
        _fill(SITE_QUERIES, values) if domain else {},
        _fill(PERSON_WIDE_QUERIES, values, free_first=True),
        _fill(COMPANY_WIDE_QUERIES, values, free_first=True),
    ]
    person_core, company_core, site, person_wide, company_wide = _within(settings.gather_max_searches, *fixed)
    used = sum(len(g) for g in (person_core, company_core, site, person_wide, company_wide))
    plan_count = min(settings.gather_planned_queries, settings.gather_max_searches - used)

    terms = company_terms(company, domain)
    full_name = f"{ctx.row['first_name']} {ctx.row['last_name']}".lower()
    person_reads = min(PERSON_READS, settings.gather_max_extracts)
    company_reads = settings.gather_max_extracts - person_reads

    did_company_research = False

    async def company_research() -> _Collected:
        # Runs once per account, inside whichever run got there first: that run pays.
        nonlocal did_company_research
        did_company_research = True
        site_reads = min(SITE_READS, company_reads) if site else 0
        planned = await plan_queries(ctx, company, domain,
                                     [q for g in (company_core, site, company_wide) for q, _, _ in g.values()],
                                     plan_count)
        explore = {f"explore_{i}": (q.query, "general", True) for i, q in enumerate(planned, 1)}
        about = lambda r: is_about_company(r, terms)  # noqa: E731
        news, own_site = await asyncio.gather(
            _search_and_read({**company_core, **company_wide, **explore}, "company",
                             company_reads - site_reads, about),
            _search_and_read(site, "company", site_reads, lambda r: on_site(r.url, domain), days=SITE_DAYS),
        )
        return (news + own_site).model_copy(update={"planned": planned})

    shared, own = await asyncio.gather(
        ctx.batch.company_research(ctx.account_key, company_research),
        _search_and_read({**person_core, **person_wide}, "person", person_reads,
                         lambda r: is_about_person(r, full_name, terms)),
    )
    credits = own.credits + (shared.credits if did_company_research else 0)
    ctx.add_usage(credits=credits)

    # A page both searches found is extracted once, as the person's: that reading also sees the
    # prospect, and a second copy would only produce duplicate signals.
    person_urls = {s.url for s in own.sources}
    sources = [s for s in shared.sources if s.url not in person_urls] + own.sources
    read = sum(1 for s in sources if s.text)
    searches = len(shared.queries) + len(own.queries)
    return GatherOutput(
        message=(f"{searches} searches, {read} of {len(sources)} pages read "
                 f"({sum(s.about == 'company' for s in sources)} company, "
                 f"{sum(s.about == 'person' for s in sources)} person)"
                 + (f" · {len(shared.planned)} planned for {company}" if shared.planned else "")
                 + f" · {credits:g} credits"),
        sources=sources,
        queries=shared.queries + own.queries,
        planned=shared.planned,
        searches=searches,
        extracts=shared.extracts + own.extracts,
        credits=credits,
    )


SECOND_ORDER_QUERY = '"{company}" hiring OR expands OR "new market" OR appoints'


async def second_order(ctx: RunContext, after: date) -> tuple[list[Source], float]:
    """Case 5: one extra search for what happened after a crowded event (hiring, new markets,
    new leaders), limited to the days since it. Returns (pages read, credits)."""
    company = search_name(ctx.row.get("company") or "")
    terms = company_terms(company, ctx.row.get("domain") or "")
    today = settings.today or date.today()
    query = {"second_order": (SECOND_ORDER_QUERY.format(company=company), "general", False)}
    collected = await _search_and_read(query, "company", 2, lambda r: is_about_company(r, terms),
                                       days=max(1, (today - after).days))
    return collected.sources, collected.credits
