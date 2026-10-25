"""Fetch a public web page and return its main text (httpx + trafilatura).

Called only from app/search.py. The returned text is untrusted: callers must wrap it in
<untrusted_source> tags before it reaches a model.
"""

import asyncio
from urllib.parse import urlparse

import httpx
import trafilatura

from app.config import settings

USER_AGENT = "Mozilla/5.0 (compatible; SignalResearchBot/0.1; +https://github.com/priyanshuvaidya360-afk/Zamp_SDR)"

# Never scrape LinkedIn (docs/PROJECT_SPEC.md, "LinkedIn: don't scrape it").
BLOCKED_DOMAINS = ("linkedin.com",)


def is_blocked(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


async def fetch_text(url: str, client: httpx.AsyncClient | None = None) -> str | None:
    """Main text of the page, capped at settings.fetch_max_chars; None if unusable."""
    if is_blocked(url):
        return None
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=settings.fetch_timeout_s,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        response = await client.get(url)
        if response.status_code != 200:
            return None
        if "html" not in response.headers.get("content-type", "text/html"):
            return None
        text = trafilatura.extract(response.text, include_comments=False, include_tables=False)
        return text[: settings.fetch_max_chars] if text else None
    except httpx.HTTPError:
        return None
    finally:
        if owns_client:
            await client.aclose()


async def reachable(url: str) -> bool | None:
    """Does a web server answer at this URL? Any HTTP response counts (a 403 from a bot filter is
    still a live site); only a failed connection (DNS, refused) means no. None: couldn't tell."""
    try:
        async with httpx.AsyncClient(timeout=settings.fetch_timeout_s, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT}) as client:
            await client.get(url)
        return True
    except httpx.ConnectError:
        return False
    except httpx.HTTPError:
        return None


async def fetch_many(
    urls: list[str], client: httpx.AsyncClient | None = None, concurrency: int = 5
) -> tuple[dict[str, str], list[str]]:
    """Fetch several pages in parallel. Returns ({url: text}, [failed urls])."""
    semaphore = asyncio.Semaphore(concurrency)
    owns_client = client is None
    client = client or httpx.AsyncClient(
        timeout=settings.fetch_timeout_s,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )

    async def one(url: str) -> tuple[str, str | None]:
        async with semaphore:
            return url, await fetch_text(url, client)

    try:
        results = await asyncio.gather(*(one(u) for u in urls))
    finally:
        if owns_client:
            await client.aclose()
    pages = {url: text for url, text in results if text}
    return pages, [url for url, text in results if not text]
