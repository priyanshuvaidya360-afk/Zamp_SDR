"""Database engine and sessions. SQLite locally, Postgres when DATABASE_URL says so."""

from collections.abc import Iterator

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (registers tables on SQLModel.metadata)
from app.config import settings


def make_engine(url: str) -> Engine:
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine(settings.database_url)


# Callers use `db.engine` / `get_session` at call time (not a captured default), so tests can
# swap in their own engine.


def create_db_and_tables(bind: Engine | None = None) -> None:
    SQLModel.metadata.create_all(bind or engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with Session(engine) as session:
        yield session
