"""Case 10, a draft that goes stale in the review queue (fictional company, .example domains).

Omar Haddad, VP Sales at Kestrel Labs. When the draft is written, the careers page lists four
open SDR roles; twelve (simulated) days later it lists two. Approving the aged draft re-checks
the page: the hiring sentence is corrected and shown as a diff to approve again.

The "now" page is a recorded fresh extract (search.extract(..., fresh=True)) in FIXTURES, so the
re-check replays with the network off, in tests and in a fixtures-mode demo alike.
Used by tests/test_review.py and scripts/record_stale_case.py.
"""

import csv
import io
from pathlib import Path

from sqlmodel import Session

from app import ingest
from app.fixtures import make_slug, save_fixture
from app.models import Batch
from app.pipeline import runner
from app.pipeline.research import GatherOutput, Source
from app.routes.batches import _store
from app.search import ExtractResponse

DATA = Path(__file__).parent / "data" / "stale"
FIXTURES = Path(__file__).parent / "fixtures" / "stale_case"
CAREERS = "https://kestrellabs.example/careers/sdr-london"

# (file, url, search category, title, listed date)
SOURCES = [
    ("careers_then.txt", CAREERS, "hiring", "Sales Development Representative, London (4 open roles)", "2026-10-02"),
    ("blog_london.txt", "https://kestrellabs.example/blog/london-office", "expansion",
     "Kestrel Labs opens its first London office", "2026-09-22"),
]


def gather_output() -> GatherOutput:
    sources = [Source(category=c, about="company", url=u, title=t, published_date=d, snippet="",
                      text=(DATA / name).read_text(encoding="utf-8")) for name, u, c, t, d in SOURCES]
    return GatherOutput(message="fixture", sources=sources, searches=2, extracts=len(sources))


def now_slug() -> str:
    """The fixture name search.extract([CAREERS], fresh=True) looks up (see app/search.py)."""
    return make_slug("extract", CAREERS[:60], {"urls": [CAREERS], "fresh": True})


def write_now_fixture() -> None:
    """The careers page as it is 'today': two roles left."""
    page = (DATA / "careers_now.txt").read_text(encoding="utf-8")
    save_fixture(now_slug(), ExtractResponse(provider="tavily", pages={CAREERS: page}, failed=[], credits=1).model_dump())


def make_batch(session: Session) -> list[int]:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["first_name", "last_name", "company", "title", "company_domain", "email"])
    writer.writerow(["Omar", "Haddad", "Kestrel Labs", "VP Sales", "kestrellabs.example", "omar@kestrellabs.example"])
    batch = Batch(filename="stale_case.csv")
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
