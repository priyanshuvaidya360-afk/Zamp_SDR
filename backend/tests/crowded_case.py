"""Case 5, the crowded hook: a fictional company whose Series B every outlet (and vendor) covered.

Dana Okafor, Head of Growth at Lumen Analytics (made up; .example domains):
- a $40M Series B on 10 October 2026, read on two outlets and listed by seven more in the same
  search: nine outlets in nine days -> crowded
- a new CRO in August (older, company-wide)
- the follow-up search finds what came after the raise: six SDR roles in Austin (14 October)

Used by tests/test_crowded.py (replaying recorded model answers) and
scripts/record_crowded_case.py (recording them).
"""

import csv
import io
from pathlib import Path

from sqlmodel import Session

from app import ingest
from app.models import Batch
from app.pipeline import runner
from app.pipeline.research import GatherOutput, QueryLog, Source
from app.routes.batches import _store

DATA = Path(__file__).parent / "data" / "crowded"
FIXTURES = Path(__file__).parent / "fixtures" / "crowded_case"

# (file, url, search category, title, listed date)
SOURCES = [
    ("news_techcrunch.txt", "https://techcrunch.example.com/2026/10/10/lumen-analytics-series-b",
     "funding", "Lumen Analytics raises $40M Series B to bring product analytics to mid-market SaaS", "2026-10-10"),
    ("news_sifted.txt", "https://sifted.example.com/articles/lumen-analytics-series-b",
     "funding", "Stockholm's Lumen Analytics lands $40M Series B led by Northzone", "2026-10-10"),
    ("blog_new_cro.txt", "https://lumenanalytics.example/blog/welcome-daniel-reyes",
     "leadership_change", "Welcome, Daniel Reyes: our first Chief Revenue Officer", "2026-08-18"),
]
FOLLOW_UP = ("careers_sdr_austin.txt", "https://lumenanalytics.example/careers/sdr-austin",
             "Sales Development Representative (6 open roles), Austin - Lumen Analytics Careers", "2026-10-14")

_SAME_RAISE = ("Lumen Analytics has raised $40 million in a Series B funding round led by Northzone, "
               "with Point Nine and Seedcamp, to expand sales and marketing.")
# The same raise as listed by other outlets in the funding search (never read: they only count).
OTHER_OUTLETS = [
    ("https://news.example.org/lumen-analytics-40m", "Lumen Analytics raises $40M Series B led by Northzone", "2026-10-10"),
    ("https://saasweekly.example.net/funding/lumen-analytics", "Lumen Analytics secures $40 million Series B", "2026-10-11"),
    ("https://nordictech.example.com/lumen-analytics-series-b", "Lumen Analytics raises $40M from Northzone", "2026-10-10"),
    ("https://vcwire.example.com/deals/lumen-analytics", "Deal: Lumen Analytics, $40M Series B, Northzone", "2026-10-12"),
    ("https://startupdaily.example.io/lumen-analytics-raise", "Lumen Analytics raised $40 million Series B funding", "2026-10-11"),
    ("https://growthnews.example.com/lumen-analytics", "Northzone leads $40M Series B in Lumen Analytics", "2026-10-13"),
    ("https://fintechfeed.example.org/lumen-analytics-b", "Lumen Analytics lands $40M Series B round", "2026-10-14"),
]
# In the same results but another story (a month earlier): must not count towards coverage.
UNRELATED = ("https://news.example.org/lumen-analytics-hubspot", "Lumen Analytics launches HubSpot integration", "2026-09-02")


def _source(name: str, url: str, category: str, title: str, listed: str) -> Source:
    return Source(category=category, about="company", url=url, title=title, published_date=listed,
                  snippet="", text=(DATA / name).read_text())


def gather_output() -> GatherOutput:
    sources = [_source(*s) for s in SOURCES]
    read = [{"url": s.url, "title": s.title, "snippet": _SAME_RAISE, "published_date": s.published_date or ""}
            for s in sources if s.category == "funding"]
    listed = [{"url": u, "title": t, "snippet": _SAME_RAISE, "published_date": d} for u, t, d in OTHER_OUTLETS]
    other = [{"url": UNRELATED[0], "title": UNRELATED[1], "snippet": UNRELATED[1], "published_date": UNRELATED[2]}]
    funding = QueryLog(category="funding", about="company", query='"Lumen Analytics" raises funding round',
                       provider="tavily", results=10, relevant=10, credits=1,
                       relevant_results=read + listed + other)
    return GatherOutput(message="fixture", sources=sources, queries=[funding], searches=1, extracts=len(sources))


calls: list = []  # every follow-up search made (tests check there is exactly one)


async def fake_second_order(ctx: runner.RunContext, after) -> tuple[list[Source], float]:
    """Stands in for research.second_order: the page the follow-up search finds."""
    calls.append(after)
    name, url, title, listed = FOLLOW_UP
    return [_source(name, url, "second_order", title, listed)], 1.0


def make_batch(session: Session) -> list[int]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["first_name", "last_name", "company", "title", "company_domain"])
    writer.writerow(["Dana", "Okafor", "Lumen Analytics", "Head of Growth", "lumenanalytics.example"])
    batch = Batch(filename="crowded_case.csv")
    _store(batch, ingest.parse_upload(buffer.getvalue().encode()))
    session.add(batch)
    session.commit()
    return runner.create_runs(session, batch)


async def _fixture_gather(ctx: runner.RunContext) -> GatherOutput:
    ctx.confidence = 0.9  # stands in for stage 2's identity confidence
    return gather_output()


def pipeline_stages() -> list[runner.Stage]:
    """Stages 4-7 for real, behind a stand-in gather that returns the fixture pages."""
    from app.pipeline.stages import STAGES

    return [runner.Stage("gather", "Gather signals", _fixture_gather, GatherOutput),
            *[s for s in STAGES if s.name in {"extract", "score", "draft", "critic"}]]
