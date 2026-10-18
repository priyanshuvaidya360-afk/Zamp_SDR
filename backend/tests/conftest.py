from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine
from tenacity import wait_none

from app import llm, search
from app.config import settings
from app.db import create_db_and_tables, get_session
from app.main import app


@pytest.fixture
def session() -> Iterator[Session]:
    """A fresh in-memory SQLite database per test."""
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    create_db_and_tables(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """The API wired to the per-test in-memory database."""
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    """Tests never read real fixtures, write the real cache, or wait between retries."""
    monkeypatch.setattr(settings, "demo_mode", "live")
    monkeypatch.setattr(settings, "fixtures_dir", tmp_path / "fixtures")
    monkeypatch.setattr(settings, "cache_dir", tmp_path / "cache")
    monkeypatch.setattr(settings, "groq_api_key", "test-groq")
    monkeypatch.setattr(settings, "gemini_api_key", "test-gemini")
    monkeypatch.setattr(llm, "RETRY_WAIT", wait_none())
    monkeypatch.setattr(search, "RETRY_WAIT", wait_none())
