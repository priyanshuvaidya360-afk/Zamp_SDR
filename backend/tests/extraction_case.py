"""One realistic prospect whose sources cover every T07 case (docs/GUIDE.md T07 "Done when").

Priya Raman, VP Finance at Acme Fintech:
- public_bio hides a flan-recipe screening trap (case 11)
- a product-launch blog post hides a hostile injection (case 11)
- three outlets report the same Series C raise (duplicate cluster, coverage = 3)
- a podcast quotes the CFO, not Priya (case 7)
- Priya reshared the CEO's post (case 7)
- an AP job listed in four cities, 45 days old (case 4)

Used by tests/test_extract.py (replaying recorded model answers) and
scripts/record_extraction.py (recording them).
"""

import csv
import io
from datetime import date
from pathlib import Path

from sqlmodel import Session

from app import ingest
from app.models import Batch
from app.pipeline import runner
from app.pipeline.research import GatherOutput, Source
from app.routes.batches import _store

DATA = Path(__file__).parent / "data"
FIXTURES = Path(__file__).parent / "fixtures" / "extraction"
TODAY = date(2026, 10, 19)

# (file, url, search category, about, title, listed date)
SOURCES = [
    ("news_techcrunch.txt", "https://techcrunch.example.com/2026/10/02/acme-fintech-series-c",
     "funding", "company", "Acme Fintech raises $40M Series C", "2026-10-02"),
    ("news_economictimes.txt", "https://economictimes.example.com/tech/acme-fintech-40-million",
     "funding", "company", "Acme Fintech bags $40 million in Series C funding", "2026-10-01"),
    ("news_yourstory.txt", "https://yourstory.example.com/2026/10/acme-fintech-series-c",
     "funding", "company", "[Funding alert] Acme Fintech raises $40M", "2026-10-03"),
    ("blog_launch_with_injection.txt", "https://acmefintech.com/blog/introducing-acme-close",
     "product_launch", "company", "Introducing Acme Close", None),
    ("careers_ap_role.txt", "https://acmefintech.com/careers/accounts-payable-specialist",
     "hiring", "company", "Accounts Payable Specialist - Acme Fintech Careers", None),
    ("podcast_cfo.txt", "https://financeleaders.example.com/episodes/42",
     "person_content", "person", "Episode 42: Scaling AP without scaling headcount", "2026-10-08"),
    ("post_reshare.txt", "https://socialfeed.example.com/posts/priya-raman-reshare-1",
     "person_news", "person", "Priya Raman reposted this", "2026-10-10"),
]


def gather_output() -> GatherOutput:
    sources = [
        Source(category=category, about=about, url=url, title=title, published_date=listed,
               snippet="", text=(DATA / "pages" / name).read_text())
        for name, url, category, about, title, listed in SOURCES
    ]
    return GatherOutput(message="fixture", sources=sources, searches=7, extracts=len(sources))


def make_context(session: Session) -> runner.RunContext:
    """A stored batch + run for Priya, with stages 1-3 already 'done'."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["first_name", "last_name", "company", "title", "company_domain", "email", "public_bio"])
    writer.writerow(["Priya", "Raman", "Acme Fintech", "VP Finance", "acmefintech.com",
                     "priya.raman@acmefintech.com", (DATA / "flan_bio.txt").read_text().strip()])
    batch = Batch(filename="extraction_case.csv")
    _store(batch, ingest.parse_upload(buffer.getvalue().encode()))
    session.add(batch)
    session.commit()
    [run_id] = runner.create_runs(session, batch)
    ctx = runner._load_context(run_id, None)
    ctx.outputs["gather"] = gather_output()
    ctx.confidence = 0.9
    return ctx
