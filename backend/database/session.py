"""Database engine and session handling (spec Module 18).

Two things here are deliberate rather than incidental.

**The URL comes from the environment and never has a hard-coded default with
credentials in it.** `.env` is gitignored; a default like
`postgresql://postgres:postgres@localhost/finverify` in source is a credential
in the repository even when it is only a local one, and it is the kind that
survives into a deployment because it happens to work.

**SQLite is allowed for tests and never for a campaign.** The research artifacts
are files (D4), so nothing about a result depends on which database is running -
but Numeric behaves differently on SQLite, and a money column that silently
becomes a float on one backend and not another is the exact class of bug this
project exists to catch. `create_all` is fine for tests; migrations are how the
Postgres schema actually changes.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.database.models import Base

__all__ = ["database_url", "build_engine", "session_scope", "create_all"]


def database_url(*, default_sqlite: str | None = None) -> str:
    """The configured database URL.

    Raises rather than defaulting to a Postgres URL with embedded credentials.
    `default_sqlite` exists so tests can pass an explicit in-memory URL without
    that path being reachable from a normal run.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    if default_sqlite:
        return default_sqlite
    raise RuntimeError(
        "DATABASE_URL is not set. It lives in .env (gitignored) - see "
        ".env.example. No default is provided here on purpose: a connection "
        "string with credentials in source is a credential in source."
    )


def build_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    resolved = url or database_url()
    connect_args = {}
    if resolved.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    elif resolved.startswith("postgresql"):
        # Fail a connection attempt in seconds, not never. With the database
        # container stopped, attempts through Docker Desktop's port proxy stalled
        # indefinitely: API requests never returned, and every screen sat on
        # "Loading" with no error, which a user cannot tell from a slow page.
        connect_args["connect_timeout"] = 5
    return create_engine(
        resolved,
        echo=echo,
        future=True,
        connect_args=connect_args,
        # A pooled connection outlives a database restart and is dead afterwards;
        # checking it on checkout turns that into a reconnect instead of an error.
        pool_pre_ping=True,
    )


def create_all(engine: Engine) -> None:
    """Create every table directly.

    For tests and a first local bring-up only. Schema changes on a real database
    go through Alembic: spec §26 requires migrations to be version controlled,
    and `create_all` against an existing database silently does nothing rather
    than telling you the schema has drifted.
    """
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """A transactional session. Commits on success, rolls back on any exception.

    Rolling back matters more than usual here: ingestion is idempotent on
    natural keys, so a half-committed ingest would leave rows that a re-run then
    skips, and the database would be quietly missing data it reports as present.
    """
    factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
