"""A simulated web for fictional demo companies (docs/PROJECT_SPEC.md T17; see ASSUMPTIONS.md).

Some demo cases need content that must not be attached to real people: a flan line in a bio, a
hostile instruction, a "not now" reply, a careers page that changes between two dates. Those
rows use fictional companies whose pages live in seed/simweb/*.yaml. When settings.sim_web_dir
is set (only while recording demo fixtures), this provider answers any search or extract that
names one of them, and its answer is final: the live web is never asked about a fictional
company. Every other query goes to the real providers as usual. Replays need none of this: the
answers are in the recorded fixtures like any other search.

Page file format:
  company: Acme Fintech
  terms: [acme fintech, acmefintech.com]   # a query naming any of these is ours
  pages:
    - url: https://acmefintech.com/team
      title: Our team
      published: 2026-10-05                 # optional
      match: [team, appoints]               # query words this page answers
      people: [priya raman]                 # a query naming them finds this page
      text: |
        ...
      later_text: |                         # optional: the page after the simulated clock moves
        ...
"""

import re
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.config import settings


@lru_cache(maxsize=4)
def _load(directory: str) -> list[dict[str, Any]]:
    companies = []
    for path in sorted(Path(directory).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["terms"] = [t.lower() for t in data.get("terms", [])]
        for page in data.get("pages", []):
            page["match"] = [m.lower() for m in page.get("match", [])]
            page["people"] = [p.lower() for p in page.get("people", [])]
        companies.append(data)
    return companies


def _companies() -> list[dict[str, Any]]:
    return _load(str(settings.sim_web_dir)) if settings.sim_web_dir else []


def _text(page: dict[str, Any]) -> str:
    return (page.get("later_text") if settings.sim_web_later and page.get("later_text") else page["text"]).strip()


class SimulatedWebProvider:
    """Answers only for fictional companies; `authoritative` means an empty answer is final."""

    name = "simweb"
    authoritative = True

    def available(self) -> bool:
        return bool(settings.sim_web_dir)

    def claims(self, query: str) -> dict[str, Any] | None:
        q = query.lower()
        return next((c for c in _companies() if any(t in q for t in c["terms"])), None)

    async def search(self, query, depth, days, max_results, topic="general"):
        from app.search import SearchResponse, SearchResult

        company = self.claims(query)
        if company is None:
            return None
        q = query.lower()
        words = set(re.findall(r"[a-z0-9]+", q))
        today = settings.today or date.today()
        scored = []
        for page in company.get("pages", []):
            published = page.get("published")
            if published and (today - published) > timedelta(days=days):
                continue
            score = sum(m in words or m in q for m in page["match"]) + 2 * sum(p in q for p in page["people"])
            if score:
                scored.append((score, page))
        scored.sort(key=lambda sp: -sp[0])
        results = [SearchResult(url=p["url"], title=p.get("title", ""), content=_text(p)[:400],
                                published_date=p["published"].isoformat() if p.get("published") else None)
                   for _, p in scored[:max_results]]
        return SearchResponse(provider=self.name, query=query, results=results)

    async def extract(self, urls):
        from app.search import ExtractResponse

        pages = {p["url"]: _text(p) for c in _companies() for p in c.get("pages", [])}
        found = {u: pages[u][: settings.fetch_max_chars] for u in urls if u in pages}
        return ExtractResponse(provider=self.name, pages=found, failed=[u for u in urls if u not in found])
