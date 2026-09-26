# backend/tests/test_db.py
# Tests for Task 1.2.a's async DB engine/session plumbing (backend/app/db.py).
import subprocess
import sys
from contextlib import aclosing
from pathlib import Path

import pytest
import sqlalchemy as sa

from app import db
from tests.conftest import VALID_ENV, minimal_subprocess_env, require_test_database, set_valid_env

# NOTE: byte-identical to test_alembic.py's/test_worker.py's own BACKEND_DIR
# (not consolidated here -- out of scope for this task's file list).
BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
async def _fresh_engine(monkeypatch):
    # db.get_engine()/_session_factory() are process-lifetime singletons
    # (@lru_cache, same pattern as get_settings()) -- correct for the real
    # app (one event loop for the process), but pytest-asyncio's default
    # "function"-scoped event loop means each test gets a new loop, and an
    # AsyncEngine cached from a previous test's loop cannot be reused in a
    # new one. So each DB-touching test here clears the cache to build a
    # fresh engine bound to its own loop, and disposes it afterward -- this
    # is test-harness plumbing, not something app code needs to do.
    database_url = require_test_database()
    set_valid_env(monkeypatch, VALID_ENV, DATABASE_URL=database_url)
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()
    yield
    await db.get_engine().dispose()
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


async def test_get_db_session_executes_select_1(_fresh_engine):
    async with aclosing(db.get_db_session()) as session_gen:
        session = await anext(session_gen)
        result = await session.execute(sa.text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_many_sequential_sessions_do_not_leak_connections(_fresh_engine):
    # 20 sessions, opened and closed one at a time (never concurrently), then
    # the pool must report zero checked-out connections -- proving
    # get_db_session() actually returns each connection instead of leaking
    # it. This does not cover concurrent/overlapping sessions (out of scope
    # for this plumbing-only task; no code yet holds more than one open).
    for _ in range(20):
        async with aclosing(db.get_db_session()) as session_gen:
            session = await anext(session_gen)
            await session.execute(sa.text("SELECT 1"))

    assert db.get_engine().pool.checkedout() == 0


def test_importing_db_module_has_no_side_effects():
    # Subprocess with no environment at all (not even the five Settings
    # variables): proves the module can be imported before Settings could
    # possibly validate, i.e. get_settings() is never called at import time.
    result = subprocess.run(  # noqa: S603 (fixed args, not user input)
        [sys.executable, "-c", "import app.db"],
        cwd=BACKEND_DIR,
        env=minimal_subprocess_env(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
