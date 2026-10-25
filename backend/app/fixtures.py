"""Recorded responses (DEMO_MODE record/fixtures) and the 24 h response cache.

Spec: docs/PROJECT_SPEC.md T03. Fixtures are committed so the demo and tests run with the network
off; the cache is git-ignored and only saves credits during development.
"""

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from app.config import settings


class FixtureMissing(Exception):
    """DEMO_MODE=fixtures asked for a response that was never recorded."""

    def __init__(self, slug: str):
        super().__init__(
            f"No fixture '{slug}.json' in {settings.fixtures_dir}. "
            "Run once with DEMO_MODE=record to save it."
        )
        self.slug = slug


def _digest(payload: Any) -> str:
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def make_slug(kind: str, label: str, payload: Any) -> str:
    """Readable, stable file name: '<kind>-<label words>-<hash of the full request>'."""
    words = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:40].strip("-")
    return f"{kind}-{words or 'request'}-{_digest(payload)[:8]}"


async def replay_pause() -> None:
    """Make a replayed call take a moment, like a real one (settings.fixture_delay_s)."""
    if settings.fixture_delay_s > 0:
        await asyncio.sleep(settings.fixture_delay_s)


used: set[str] = set()  # slugs replayed in this process (scripts/record_demo.py prunes the rest)


def load_fixture(slug: str) -> Any:
    used.add(slug)
    path = settings.fixtures_dir / f"{slug}.json"
    if not path.exists():
        raise FixtureMissing(slug)
    return json.loads(path.read_text(encoding="utf-8"))


def reusable(slug: str) -> Any | None:
    """Record mode with settings.record_reuse: a successful earlier recording of the same request
    (here or in settings.record_reuse_dirs) instead of a new live call. Makes a long recording
    resumable after a rate limit, and keeps yesterday's live-web answers when today's differ.
    Recorded failures and empty search results are never reused: those calls are tried again."""
    if settings.demo_mode != "record" or not settings.record_reuse:
        return None
    for directory in [settings.fixtures_dir, *settings.record_reuse_dirs]:
        path = directory / f"{slug}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if "error" in data or data.get("results") == [] or data.get("pages") == {}:
            continue
        if directory != settings.fixtures_dir:
            save_fixture(slug, data)
        return data
    return None


def save_fixture(slug: str, data: Any) -> Path:
    settings.fixtures_dir.mkdir(parents=True, exist_ok=True)
    path = settings.fixtures_dir / f"{slug}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def _cache_path(key_payload: Any) -> Path:
    return settings.cache_dir / f"{_digest(key_payload)}.json"


def cache_get(key_payload: Any) -> Any | None:
    path = _cache_path(key_payload)
    if not path.exists():
        return None
    entry = json.loads(path.read_text(encoding="utf-8"))
    if time.time() - entry["ts"] > settings.cache_ttl_hours * 3600:
        return None
    return entry["data"]


def cache_put(key_payload: Any, data: Any) -> None:
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(key_payload).write_text(
        json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
