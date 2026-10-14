# backend/app/main.py
# Task 1.1.e: FastAPI app factory. Nothing at module level reads the
# environment or calls get_settings(): importing this module must succeed
# even with an empty environment (config errors must surface from
# create_app(), i.e. at startup, not at import time).
#
# ASSUMPTION: importlib.metadata.version("widgetplatform") reads the
# version pinned in backend/pyproject.toml (Task 1.1.a). This only works
# because Task 1.1.b's `pip install -e .` check made the package
# importable-as-installed; a plain `python -m app.main` without that
# install step would raise importlib.metadata.PackageNotFoundError here.
#
# Task 1.4.k: the lifespan hook. Resolves the API half of the
# shutdown-disposal open marker (the worker's own lifecycle hook stays a
# separate, still-open concern owned by Step 2.1). get_engine()/
# get_qdrant_client() both stay lazy, exactly as they already are (Tasks
# 1.2.a/1.3.b): this hook only disposes what THIS process actually
# created, never forces creation just to immediately tear it down.
#
# Task 3.2.b: the checkpointer pool is the one exception to "startup does
# nothing new" -- it cannot be lazy the way get_engine()/get_qdrant_client()
# are (psycopg_pool's own async pool must be opened via an explicit
# `await pool.open()`, confirmed at Task 3.2.a; a plain sync @lru_cache
# factory cannot provide that), so open_checkpointer_pool() runs here,
# unconditionally, before the yield.
#
# Deliberately NOT wrapped in try/except like the shutdown disposals below
# -- but this does NOT mean an unreachable database fails startup: `pool.
# open()`'s own default is `wait=False` (confirmed live by reading its
# signature/docstring directly), so it starts background connection
# workers and returns immediately, regardless of whether the database is
# actually reachable. What staying unwrapped here really protects against
# is a genuine CONSTRUCTION-time error -- e.g. a malformed conninfo
# string, or get_settings() raising SettingsError -- propagating loudly
# at startup rather than being silently swallowed. A merely-unreachable
# database surfaces later instead, the first time get_checkpointer()'s
# returned saver actually tries to use a connection (see that module's
# own open_checkpointer_pool() docstring for the full detail).
#
# "Created yet?" without creating: get_engine.cache_info().currsize (an
# lru_cache-wrapped, zero-arg function's own cache introspection -- pure,
# never calls the wrapped function) is already this project's own
# established mechanism for exactly this check, first used in
# app/qdrant.py's own test fixture (Task 1.3.b, test_qdrant.py's
# _close_the_shared_live_client_once). Reused here rather than inventing a
# second one.
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from app.agent.checkpointer import close_checkpointer_pool, get_checkpointer, open_checkpointer_pool
from app.agent.graph import build_graph
from app.auth.routes import router as auth_router
from app.chat.routes import router as chat_router
from app.config import Settings, get_settings
from app.db import _session_factory, get_engine
from app.health import router as health_router
from app.qdrant import get_qdrant_client

_TITLE = "widgetplatform API"  # neutral name; no brand name in code

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    await open_checkpointer_pool()
    # Task 4.1: resolves the §9 marker recorded at the Step 3.3 breakdown
    # (decision Q5) -- "compile the graph once, in the lifespan hook,
    # after open_checkpointer_pool(), store it, and pass it into the
    # entry point." Compiled exactly once per process lifetime (never
    # per-request, unlike get_checkpointer() itself, which deliberately
    # builds a fresh AsyncPostgresSaver per call because pytest's own
    # event-loop churn makes a cached one unsafe across tests -- a real
    # uvicorn process has exactly one event loop for its whole lifetime,
    # so that concern does not apply here). Stored on app.state, the same
    # place Settings/StatusCache/RateLimiter instances already live --
    # app/chat/routes.py reads it back via request.app.state.graph.
    app.state.graph = build_graph(get_checkpointer())
    yield
    # Each disposal in its own try/except: one failing must never prevent
    # the other from being attempted, and must never crash the shutdown
    # sequence itself -- logged (same logging.getLogger(__name__) + the
    # standard library's own logging convention Task 1.4.j established),
    # never raised further.
    #
    # Duplication check after 1.4.f/g/h/k (accepted decision C1): each
    # successful disposal also clears that singleton's own cache -- a
    # disposed AsyncEngine is safe to reuse (SQLAlchemy recreates its pool
    # on next use, confirmed live), but a closed AsyncQdrantClient is not
    # (confirmed live: any call after close() raises
    # "Cannot send a request, as the client has been closed"). Without
    # clearing the cache, a second lifespan cycle in the same process would
    # hand back that permanently-broken client. Clearing both, not just
    # Qdrant's, keeps the two disposal blocks symmetric rather than
    # special-casing only the one that currently needs it.
    #
    # _session_factory is cleared alongside get_engine, not on its own:
    # left stale, it would keep binding sessions to the just-disposed
    # engine object instead of the fresh one the next get_engine() call
    # creates -- every test fixture that touches DATABASE_URL already
    # clears this exact pair together; this is the first production call
    # site to need it.
    if get_engine.cache_info().currsize:
        try:
            await get_engine().dispose()
        except Exception:
            logger.exception("failed to dispose the database engine during shutdown")
        else:
            get_engine.cache_clear()
            _session_factory.cache_clear()
            logger.info("database engine disposed")

    if get_qdrant_client.cache_info().currsize:
        try:
            await get_qdrant_client().close()
        except Exception:
            logger.exception("failed to close the Qdrant client during shutdown")
        else:
            get_qdrant_client.cache_clear()
            logger.info("Qdrant client closed")

    # Task 3.2.b: unlike the two disposals above, the checkpointer pool was
    # unconditionally opened at the top of this hook (not lazily), so no
    # "created yet?" guard is needed here -- close_checkpointer_pool()'s
    # own no-op-if-never-opened check (app/agent/checkpointer.py) covers
    # the one case that still matters: a test building the app without
    # running this hook's startup half at all.
    try:
        await close_checkpointer_pool()
    except Exception:
        logger.exception("failed to close the checkpointer pool during shutdown")
    else:
        logger.info("checkpointer pool closed")


def create_app(settings: Settings | None = None) -> FastAPI:
    # Discovered live while verifying the lifespan hook's own log lines
    # against the real Compose stack: uvicorn's default logging config
    # (entrypoint.sh invokes plain `uvicorn ... --factory`, no
    # --log-config) only attaches handlers to its OWN "uvicorn"/
    # "uvicorn.access" loggers, never the root logger -- confirmed by
    # reading the installed uvicorn's own uvicorn.config.LOGGING_CONFIG.
    # Without this, logger.info()/.exception() calls anywhere in this
    # application (this module's own lifespan hook included) are silently
    # dropped by Python logging's WARNING-level "handler of last resort",
    # not printed anywhere -- the API process had no equivalent of
    # worker.py's own logging.basicConfig() call in its main(). basicConfig
    # is a no-op once the root logger already has a handler, so calling it
    # on every create_app() (including from tests) is safe.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    # Never Settings() directly here: the guard test (test_config_guard.py)
    # only allows app/config.py to construct Settings(), so a missing or
    # invalid environment must fail fast through get_settings() -> SettingsError.
    if settings is None:
        settings = get_settings()

    docs_enabled = settings.app_env != "production"
    app = FastAPI(
        title=_TITLE,
        version=version("widgetplatform"),
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    return app
