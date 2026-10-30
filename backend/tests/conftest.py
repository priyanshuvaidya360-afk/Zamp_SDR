from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine
from tenacity import wait_none

from app import db, limits, llm, search
from app.config import settings
from app.db import create_db_and_tables
from app.main import app
from app.pipeline import runner, stubs


@pytest.fixture
def engine(tmp_path, monkeypatch) -> Engine:
    """A fresh SQLite file per test (a file, not :memory:, so background runs get their own
    connections exactly as in dev). Everything that uses db.engine sees it."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False})
    create_db_and_tables(engine)
    monkeypatch.setattr(db, "engine", engine)
    return engine


@pytest.fixture
def session(engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(engine) -> Iterator[TestClient]:
    """The API on the test database. `with` keeps one event loop alive, so background runs
    started by a request keep running after it returns."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Tests never touch the network, read real fixtures, write the real cache, or wait
    between retries. Runs use the stand-in stages unless a test opts into real ones."""
    monkeypatch.setattr(settings, "demo_mode", "live")
    monkeypatch.setattr(settings, "fixtures_dir", tmp_path / "fixtures")
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "groq_api_key", "test-groq")
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini")
    monkeypatch.setattr(settings, "tavily_api_key", "")
    monkeypatch.setattr(settings, "exa_api_key", "")
    monkeypatch.setattr(settings, "gather_planned_queries", 0)  # the planner is an LLM call; its tests turn it on
    limits.reset()
    monkeypatch.setattr(runner, "get_stages", lambda: stubs.STAGES)

    async def no_network(*args, **kwargs):
        raise AssertionError("a test tried to use the network; mock search/llm instead")

    monkeypatch.setattr(llm, "_call", no_network)
    monkeypatch.setattr(search.DuckDuckGoProvider, "search", no_network)
    monkeypatch.setattr(search, "fetch_many", no_network)
    monkeypatch.setattr(settings, "stub_stage_delay_s", 0.01)
    monkeypatch.setattr(llm, "RETRY_WAIT", wait_none())
    monkeypatch.setattr(search, "RETRY_WAIT", wait_none())
    runner._batch_contexts.clear()
