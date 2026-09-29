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
# separate, still-open concern owned by Step 2.1). Startup does nothing
# new -- get_engine()/get_qdrant_client() both stay lazy, exactly as they
# already are (Tasks 1.2.a/1.3.b): this hook only disposes what THIS
# process actually created, never forces creation just to immediately
# tear it down.
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

from app.auth.routes import router as auth_router
from app.config import Settings, get_settings
from app.db import _session_factory, get_engine
from app.health import router as health_router
from app.qdrant import get_qdrant_client

_TITLE = "widgetplatform API"  # neutral name; no brand name in code

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
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
    return app
