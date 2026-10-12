# backend/app/agent/checkpointer.py
# Task 3.2.b: the dedicated checkpointer pool + AsyncPostgresSaver (docs/
# SPEC.md §6.3's "State" bullet: "MessagesState with thread_id ... persisted
# by AsyncPostgresSaver (dedicated pool, autocommit=True, row_factory=
# dict_row; setup() runs at deploy)"). The guarded entry point itself and
# its AST guard are Task 3.2.c's own job, not built here.
#
# Deliberately NOT the get_engine()/get_qdrant_client() lazy-@lru_cache-
# singleton pattern (Task 3.2.a's own finding): psycopg_pool's own async
# pool emits a RuntimeWarning if it auto-opens inside its constructor
# ("opening the async pool ... in the constructor is deprecated ... Please
# use `await pool.open()`", confirmed by reading psycopg_pool/pool_async.py
# directly) -- opening it requires an async step, which a plain sync
# @lru_cache factory cannot provide. Instead: open_checkpointer_pool() is
# called once, explicitly, from app/main.py's own lifespan startup (never
# lazily on first use); close_checkpointer_pool() mirrors it at shutdown.
# get_checkpointer() raises loudly if called before the pool has been
# opened -- it never silently opens one itself.
#
# get_checkpointer() builds a FRESH AsyncPostgresSaver on every call rather
# than caching one alongside the pool: AsyncPostgresSaver.__init__() calls
# asyncio.get_running_loop() and stores it on self.loop (confirmed by
# reading langgraph/checkpoint/postgres/aio.py directly) -- caching one
# instance would tie it to whichever event loop happened to be running the
# first time it was built, the exact cross-event-loop trap app/db.py's own
# AsyncEngine already hit once (see PROJECT_SPEC.md's open markers). The
# pool itself has no such binding (confirmed by reading psycopg_pool's own
# source: AsyncConnectionPool holds no cached running loop), so only the
# pool is a real singleton; the thin AsyncPostgresSaver wrapper around it
# is cheap to rebuild per call.
import asyncio
import logging
import sys
from urllib.parse import urlsplit, urlunsplit

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import POSTGRES_SCHEME, SettingsError, get_settings

logger = logging.getLogger(__name__)

# Shared by both the live app pool (open_checkpointer_pool() below) and the
# migrate-time setup() call (run_checkpointer_setup() below) -- Task 3.2.b
# decision 2: one constant, not two near-identical copies. Each kwarg is
# individually load-bearing, confirmed live at Task 3.2.a by reading
# langgraph-checkpoint-postgres's own source directly, not a style choice:
#   - autocommit=True: AsyncPostgresSaver.setup()'s own migration list
#     (langgraph/checkpoint/postgres/base.py's MIGRATIONS) includes
#     `CREATE INDEX CONCURRENTLY IF NOT EXISTS` statements, which cannot
#     run inside any transaction, implicit or explicit -- without this,
#     setup() itself would fail.
#   - row_factory=dict_row: the library indexes every row it reads by
#     column name (e.g. `value["thread_id"]`, `row["v"]` in aio.py), a
#     plain tuple-factory connection would break it.
#   - prepare_threshold=0: no PgBouncer exists in this stack yet, but
#     docs/SPEC.md §14 already names this as required "once PgBouncer
#     lands" for the checkpointer specifically (transaction-mode pooling
#     is incompatible with server-side prepared statements) -- setting it
#     now is zero-cost and matches the library's own from_conn_string()
#     reference configuration exactly.
CHECKPOINTER_CONNECTION_KWARGS = {
    "autocommit": True,
    "row_factory": dict_row,
    "prepare_threshold": 0,
}

# docs/SPEC.md §14: "Database connections: separate pools (about 10 app, 10
# checkpointer, 5 worker)". No new Settings field (Task 3.2.b decision 3,
# consistent with decision 4): an unmeasured starting point, matching
# get_engine()'s own existing precedent of leaving pool sizing at a plain
# default until Phase 7's real load-test numbers exist.
CHECKPOINTER_POOL_MAX_SIZE = 10

_checkpointer_pool: AsyncConnectionPool | None = None


def checkpointer_conninfo() -> str:
    """Settings.database_url is validated (app/config.py's own
    _require_postgres_psycopg_scheme) to always carry the SQLAlchemy
    dialect-prefixed scheme, `postgresql+psycopg://` -- but psycopg's own
    conninfo parser rejects that outright (confirmed live at Task 3.2.a:
    `psycopg.conninfo.conninfo_to_dict("postgresql+psycopg://...")` raises
    "missing \"=\" after ..."). This is the one place that conversion
    happens, reused by both the pool and the migrate-time setup() call,
    rather than duplicated at each call site.
    """
    url = get_settings().database_url_str()
    parts = urlsplit(url)
    if parts.scheme != POSTGRES_SCHEME:
        # Guaranteed not to happen by Settings' own validator -- a defensive
        # check, not a real runtime path, so it never needs to leak the URL.
        raise RuntimeError(f"expected the '{POSTGRES_SCHEME}' scheme on DATABASE_URL")
    return urlunsplit(parts._replace(scheme="postgresql"))


async def open_checkpointer_pool() -> None:
    """Called exactly once, from app/main.py's own lifespan startup (Task
    3.2.b decision 1) -- never lazily. `open=False` at construction, then
    an explicit `await pool.open()`: constructing the async pool with the
    default (`open=None`, which auto-opens) or `open=True` both emit a
    RuntimeWarning on this specific pool class (confirmed live at Task
    3.2.a); this is the library's own documented non-deprecated path.

    `pool.open()`'s own default is `wait=False` (confirmed by reading its
    signature directly) -- it starts background connection workers and
    returns immediately, it does not block this hook on a real connection
    succeeding. A genuinely unreachable DATABASE_URL therefore does NOT
    fail startup here; it surfaces the first time get_checkpointer()'s
    returned saver actually tries to use a connection, the same
    lazy-until-first-real-use behavior get_engine() already has.
    """
    global _checkpointer_pool
    pool = AsyncConnectionPool(
        checkpointer_conninfo(),
        kwargs=CHECKPOINTER_CONNECTION_KWARGS,
        max_size=CHECKPOINTER_POOL_MAX_SIZE,
        open=False,
    )
    await pool.open()
    _checkpointer_pool = pool


async def close_checkpointer_pool() -> None:
    """Mirrors open_checkpointer_pool(), called from app/main.py's own
    lifespan shutdown. A no-op if the pool was never opened (e.g. a test
    that builds the app without running the full lifespan) -- the same
    "created yet?" guard style as get_engine()/get_qdrant_client()'s own
    shutdown checks, just against a plain module global instead of an
    lru_cache, since this singleton is deliberately not one (decision 1).
    """
    global _checkpointer_pool
    if _checkpointer_pool is not None:
        await _checkpointer_pool.close()
        _checkpointer_pool = None


def get_checkpointer() -> AsyncPostgresSaver:
    """Fail loudly if called before open_checkpointer_pool() has run --
    never silently opens the pool itself (Task 3.2.b decision 1). See this
    module's own header comment for why a fresh AsyncPostgresSaver is
    returned on every call rather than caching one alongside the pool.
    """
    if _checkpointer_pool is None:
        raise RuntimeError(
            "the checkpointer pool has not been opened yet -- "
            "open_checkpointer_pool() must run during app startup "
            "(app/main.py's own lifespan hook) before get_checkpointer() "
            "can be called"
        )
    return AsyncPostgresSaver(_checkpointer_pool)


async def run_checkpointer_setup() -> None:
    """The migrate-time setup() call (docs/SPEC.md §6.3: "setup() runs at
    deploy"), mirroring app/qdrant.py's own ensure_collection() CLI
    entrypoint pattern -- a short-lived single connection, not the long-
    lived pool above, since this runs once per deploy from entrypoint.sh's
    "migrate" case, not from the long-running api/worker process. Genuinely
    idempotent, confirmed live at Task 3.2.a by reading
    AsyncPostgresSaver.setup()'s own migration-version bookkeeping and its
    11 real migrations directly (every one of them individually written
    `IF NOT EXISTS`/version-tracked) -- safe to run on every deploy.
    """
    async with await AsyncConnection.connect(
        checkpointer_conninfo(), **CHECKPOINTER_CONNECTION_KWARGS
    ) as conn:
        await AsyncPostgresSaver(conn).setup()
    logger.info("checkpointer setup: checkpoint tables are up to date")


def main() -> None:
    """`python -m app.agent.checkpointer`. Mirrors app/qdrant.py's own
    main(): a clean one-line message on a configuration problem, no
    traceback -- SettingsError's own message is already proven free of
    secrets (see get_settings()'s own docstring), so printing it directly
    is safe.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    try:
        asyncio.run(run_checkpointer_setup())
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
