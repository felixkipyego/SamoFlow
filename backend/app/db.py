# backend/app/db.py
# Task 1.2.a: async DB engine and session plumbing (no models yet -- those
# arrive in 1.2.b). Nothing here reads the environment or calls
# get_settings() at import time (module import must succeed with an empty
# environment, matching app/config.py's own rule): get_engine() and
# _session_factory() are @lru_cache-wrapped, same lazy-singleton pattern as
# get_settings() itself, so the engine is built once, on first real use, not
# at import.
#
# DATABASE_URL uses the "postgresql+psycopg" scheme (psycopg 3), built for
# the sync driver. Verified against the installed sqlalchemy==2.0.54 /
# psycopg==3.3.6: psycopg 3 (unlike psycopg2) natively supports both sync and
# async connections through the one driver package, and SQLAlchemy's psycopg
# dialect exposes this via PGDialect_psycopg.get_async_dialect_cls() -- so
# create_async_engine() accepts the exact same "postgresql+psycopg://" URL
# and transparently resolves to PGDialectAsync_psycopg (confirmed by hand:
# engine.dialect.is_async is True, engine.dialect.driver == "psycopg", with
# no real connection attempted). No scheme change needed.
from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    # Task 1.2.b: the one shared declarative base every models.py imports,
    # so alembic/env.py's target_metadata (Base.metadata) covers all tables
    # regardless of which domain subpackage defines them. Lives here rather
    # than a new file: this module is already the shared DB-infra module
    # from 1.2.a, and a second one for just this class would be
    # over-engineering for a two-line addition (rule 11).
    pass


@lru_cache
def get_engine() -> AsyncEngine:
    # create_async_engine() does not open a connection itself (confirmed by
    # hand): the pool is created lazily and a real connection is only
    # acquired on first checkout. Pool size is left at SQLAlchemy's own
    # default (rule 11; Phase 7 tunes it with real load-test numbers -- see
    # PROJECT_SPEC.md's decision log).
    # NOTE: nothing calls get_engine().dispose() at process shutdown yet --
    # no app lifecycle hook exists until a real consumer (an endpoint or the
    # worker) wires one up. See PROJECT_SPEC.md's open markers.
    return create_async_engine(get_settings().database_url_str())


@lru_cache
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    # Standard FastAPI "dependency with yield" shape: one session per call,
    # closed (connection returned to the pool) as soon as the caller is done
    # -- never held open across a stream (engineering rule, PROJECT_SPEC.md
    # §3). Usable directly with FastAPI's Depends(), or driven manually via
    # contextlib.aclosing() elsewhere (e.g. the tenant-scoped repository,
    # 1.2.d).
    async with _session_factory()() as session:
        yield session
