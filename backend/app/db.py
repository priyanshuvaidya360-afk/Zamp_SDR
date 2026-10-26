"""Database engine and sessions. SQLite locally, Postgres when DATABASE_URL says so."""

import enum
import json
import logging
from collections.abc import Iterator

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.schema import Column
from sqlmodel import Session, SQLModel, create_engine

from app import models  # noqa: F401  (registers tables on SQLModel.metadata)
from app.config import settings

log = logging.getLogger(__name__)


def normalise_url(url: str) -> str:
    """Hosts (Neon, Supabase, Render) hand out postgres:// or postgresql:// URLs; SQLAlchemy
    needs the driver named, and psycopg (v3) is the one installed."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


def make_engine(url: str) -> Engine:
    url = normalise_url(url)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    # pool_pre_ping: free Postgres tiers (Neon scales to zero) drop idle connections.
    return create_engine(url, pool_pre_ping=True, connect_args=postgres_connect_args(url))


def postgres_connect_args(url: str) -> dict:
    """Neon's pooled host ("-pooler") runs PgBouncer in transaction mode: a server-side prepared
    statement made in one transaction may be gone in the next, so psycopg must not make any."""
    return {"prepare_threshold": None} if "-pooler." in url else {}


engine = make_engine(settings.database_url)


# Callers use `db.engine` / `get_session` at call time (not a captured default), so tests can
# swap in their own engine.


def create_db_and_tables(bind: Engine | None = None) -> None:
    """Startup schema work. On Neon it runs over the direct (unpooled) connection, as Neon
    advises for schema changes; the app's own queries use the pooled `engine`."""
    direct = None
    if bind is None and settings.database_url_unpooled:
        bind = direct = make_engine(settings.database_url_unpooled)
    bind = bind or engine
    try:
        SQLModel.metadata.create_all(bind)
        if added := add_missing_columns(bind):
            log.info("database: added columns %s", ", ".join(added))
    finally:
        if direct is not None:
            direct.dispose()


def _default_sql(column: Column) -> str:
    """SQL DEFAULT for a new column, so rows that already exist get the model's default."""
    default = column.default
    if default is None:
        return ""
    value = default.arg(None) if default.is_callable else default.arg  # default_factory -> call it
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, bool):
        return " DEFAULT TRUE" if value else " DEFAULT FALSE"
    if isinstance(value, (int, float)):
        return f" DEFAULT {value}"
    if isinstance(value, str):
        return " DEFAULT '" + value.replace("'", "''") + "'"
    if isinstance(value, (list, dict)):
        return " DEFAULT '" + json.dumps(value) + "'"
    return ""  # e.g. timestamps: existing rows keep NULL


def add_missing_columns(bind: Engine) -> list[str]:
    """Add columns a model has gained since its table was created (create_all only creates
    missing tables). Additive only, never drops or changes anything, so existing data stays.
    A lightweight stand-in for migrations at demo scale."""
    inspector = inspect(bind)
    added = []
    with bind.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl_type = column.type.compile(dialect=bind.dialect)
                connection.execute(text(
                    f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl_type}{_default_sql(column)}'
                ))
                added.append(f"{table.name}.{column.name}")
    return added


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    with Session(engine) as session:
        yield session
