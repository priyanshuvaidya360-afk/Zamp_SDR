"""Case 5: the crowded hook. A big public event (funding, launch, acquisition) that every
vendor is already writing about is a weak opener: it gets a penalty and may only be context.

Spec: docs/GUIDE.md case 5 and T14. Code only, no model:
- crowded = type in CROWDED_TYPES, younger than settings.crowded_max_days, and reported by
  settings.crowded_min_coverage+ distinct outlets
- coverage counts the outlets whose pages we read (extract.cluster_events) plus the outlets in
  the same search's results that report the same event, which cost nothing extra
- one extra search then looks for what followed the event (research.second_order)
"""

import re
from datetime import date
from urllib.parse import urlparse

from app.config import settings
from app.pipeline.extract import ExtractedSignal, _overlap, amounts, parse_date
from app.pipeline.research import QueryLog

CROWDED_TYPES = {"funding", "product_launch", "acquisition"}
SAME_EVENT_DAYS = 7
SAME_EVENT_OVERLAP = 0.6

# Words that put a crowded event in a sentence, by type (the critic's first-sentence check).
EVENT_WORDS = {
    "funding": re.compile(r"\b(rais(?:e|ed|es|ing)|fund(?:ing|ed|raise)?|series [a-h]|round|investors?|valuation)\b", re.I),
    "product_launch": re.compile(r"\b(launch(?:ed|es|ing)?|unveil(?:ed|s)?|releas(?:e|ed|es))\b", re.I),
    "acquisition": re.compile(r"\b(acqui(?:re|red|res|sition)|bought|merg(?:e|ed|er))\b", re.I),
}
# Amounts like "$40M", "€12 million", "40m": distinctive enough to spot the event in a sentence.
AMOUNT = re.compile(r"[$€£₹]\s?\d[\d.,]*\s?(?:[mbk]n?|million|billion)?\b|\b\d[\d.,]*\s?(?:[mb]n?|million|billion)\b", re.I)


def _domain(url: str) -> str:
    return (urlparse(url).hostname or url).removeprefix("www.")


def add_search_coverage(signals: list[ExtractedSignal], queries: list[QueryLog]) -> None:
    """Count outlets in the search results that report the same event as an extracted signal
    (same search category, and either a similar headline within a week or the same amount, e.g.
    "$55 million" in the summary and "$55M" in a headline, within the crowded window).
    Adds them to cluster_urls."""
    for s in signals:
        if s.type not in CROWDED_TYPES or s.about != "company":
            continue
        urls = list(dict.fromkeys([s.source_url, *s.cluster_urls]))
        money = amounts(s.summary)
        for log in queries:
            if log.category != s.category:
                continue
            for result in log.relevant_results:
                when = parse_date(result.get("published_date"))
                gap = abs((when - s.published_at).days) if when and s.published_at else 0
                headline = f"{result.get('title', '')} {result.get('snippet', '')}"
                similar = gap <= SAME_EVENT_DAYS and _overlap(s.summary, headline) >= SAME_EVENT_OVERLAP
                # The same amount from the same company is the same raise, even in a later write-up.
                same_amount = gap < settings.crowded_max_days and bool(money & amounts(headline))
                if similar or same_amount:
                    urls.append(result["url"])
        urls = list(dict.fromkeys(urls))
        s.cluster_urls = [u for u in urls if u != s.source_url]
        s.coverage = max(s.coverage, len({_domain(u) for u in urls}))


def is_crowded(type_: str, published_at: date | None, coverage: int, today: date) -> bool:
    return (type_ in CROWDED_TYPES and published_at is not None
            and (today - published_at).days < settings.crowded_max_days
            and coverage >= settings.crowded_min_coverage)


def crowded_events(signals: list[ExtractedSignal], today: date) -> list[ExtractedSignal]:
    return [s for s in signals if is_crowded(s.type, s.published_at, s.coverage, today)]


def reason(coverage: int, days_old: int) -> str:
    return f"Crowded: covered by {coverage} outlets in {days_old} days"


def references_event(sentence: str, event: ExtractedSignal) -> bool:
    """The sentence talks about the crowded event: its typical words, or one of its numbers."""
    words = EVENT_WORDS.get(event.type)
    if words and words.search(sentence):
        return True
    amounts = {re.sub(r"\s", "", a.lower()) for a in AMOUNT.findall(event.summary)}
    flat = re.sub(r"\s", "", sentence.lower())
    return any(a in flat for a in amounts)
