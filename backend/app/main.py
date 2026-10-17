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
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from app.admin.routes import router as admin_router
from app.agent.checkpointer import close_checkpointer_pool, get_checkpointer, open_checkpointer_pool
from app.agent.graph import build_graph
from app.auth.cors import WidgetCORSMiddleware
from app.auth.routes import router as auth_router
from app.chat.routes import router as chat_router
from app.config import Settings, get_settings
from app.dashboard.escalation_routes import router as dashboard_escalation_router
from app.dashboard.routes import router as dashboard_router
from app.dashboard.source_routes import router as dashboard_source_router
from app.dashboard.tenant_routes import router as dashboard_tenant_router
from app.db import _session_factory, get_engine
from app.handoff.routes import router as handoff_router
from app.health import router as health_router
from app.plans.usage import sync_usage_counters
from app.qdrant import get_qdrant_client
from app.telemetry.service import init_tracing, instrument_fastapi_app, shutdown_tracing

_TITLE = "widgetplatform API"  # neutral name; no brand name in code

logger = logging.getLogger(__name__)

# Task 4.3: see _lifespan()'s own comment at its first call site for
# the live-tested reasoning (an unreachable-but-not-refused DATABASE_URL
# otherwise blocks for the OS's own TCP connect timeout, often 60-130+
# seconds). Also applied to the PERIODIC sync below, for the identical
# reason -- a single stuck tick must not silently take far longer than
# `usage_counter_sync_interval_seconds` itself.
_USAGE_COUNTER_STARTUP_TIMEOUT_SECONDS = 30.0


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Step 4.5: docs/SPEC.md §13's own "phoenix.otel.register at
    # startup". Before open_checkpointer_pool()/the graph compile below
    # -- LangChainInstrumentor patches LangChain's own base Runnable
    # classes globally, so order relative to graph construction has no
    # functional effect either way, but doing it first keeps every
    # later startup step's own spans (if any ever exist) correctly
    # instrumented from the moment they run, rather than depending on
    # a reader noticing instrumentation happened to land after them.
    # `init_tracing()` never raises (see its own docstring) and returns
    # `None` when Phoenix is not configured -- the identical "optional
    # feature, must still start without it" shape SMTP/Anthropic
    # already established.
    tracer_provider = await init_tracing(get_settings())
    if tracer_provider is not None:
        instrument_fastapi_app(app, tracer_provider)
    app.state.tracer_provider = tracer_provider

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

    # Task 4.3: docs/SPEC.md §9's own quota-reload guarantee -- "On
    # startup the API reloads the current month's usage before serving
    # chats, so a restart cannot reset counters. Until the reload
    # finishes, /api/v1/chat returns 503... Only chat is gated: the
    # container health check stays healthy, and the session, history,
    # escalation and admin endpoints keep working." `app.state.usage_
    # counters_ready` starts unset (falsy via `getattr(..., False)` at
    # the call site, app/chat/routes.py) and is set True only once a
    # reload actually succeeds -- never set True unconditionally, which
    # would defeat the whole guarantee.
    #
    # A REAL regression found live, not merely reasoned about: an
    # EARLIER version of this code let a startup-time reload FAILURE
    # propagate and crash the whole lifespan -- "fail loudly," reasoned
    # by analogy to open_checkpointer_pool()'s own construction-time-
    # error precedent. That reasoning was WRONG, confirmed live by the
    # real CI "image" smoke test: with an unreachable DATABASE_URL
    # (`postgres`, a hostname that genuinely does not resolve outside
    # docker-compose's own network -- the smoke test's own documented,
    # deliberate shape for the api/worker checks), the ENTIRE container
    # exited immediately ("Application startup failed. Exiting."),
    # so `/health` was never reachable AT ALL -- directly contradicting
    # docs/SPEC.md §9's own explicit "the container health check stays
    # healthy" requirement quoted above, which this project established
    # as a hard guarantee back at Task 1.4.j specifically so a liveness
    # probe never depends on database reachability. Fixed: the reload
    # failure is now caught here and logged, never re-raised -- the
    # rest of this lifespan (the periodic retry below, then `yield`)
    # always runs regardless, so the process starts and serves `/health`
    # (and every other non-gated endpoint) normally; only `/chat` stays
    # gated, exactly as the spec names, until a later retry succeeds.
    settings = get_settings()
    app.state.usage_counters_ready = False

    async def _sync_usage_counters_once(label: str) -> None:
        # Shared by the startup attempt and each periodic tick below --
        # both need the identical bounded-timeout-then-flip-ready shape,
        # differing only in what they log on failure.
        try:
            # With no `connect_timeout` set anywhere on this engine
            # (app/db.py's own get_engine(), confirmed by reading it
            # directly), a DATABASE_URL that is unreachable in the
            # "silently dropped" sense (a blackholed/firewalled
            # address, unlike the much more common "connection
            # refused"/"name not resolved" cases, which both fail in
            # milliseconds) would otherwise block this `await` for the
            # OS's own TCP connect timeout -- often 60-130+ seconds.
            # Bounded explicitly, matching this project's own "internal
            # safety ceiling, no corresponding spec number" precedent
            # (e.g. `docx_max_part_size_bytes`).
            async with asyncio.timeout(_USAGE_COUNTER_STARTUP_TIMEOUT_SECONDS):
                async with _session_factory()() as session:
                    await sync_usage_counters(session)
        except Exception:
            logger.exception("usage-counter sync failed (%s)", label)
        else:
            app.state.usage_counters_ready = True

    await _sync_usage_counters_once("startup")

    # The periodic half of the same guarantee -- docs/SPEC.md §9's own
    # "refreshes them from usage_events about every minute". A plain
    # background `asyncio.Task`, cancelled at shutdown (below) -- the
    # same lifecycle shape as every other per-process background loop
    # in this project (app/worker.py's own run()), just on the API side
    # this time. Also the real RECOVERY path if the startup attempt
    # above failed: the very next successful tick flips `usage_
    # counters_ready` True, so a transient DB outage at boot self-heals
    # without a restart, matching worker.py's own "a transient failure
    # must not crash the process" reasoning exactly.
    async def _sync_usage_counters_periodically() -> None:
        while True:
            try:
                await asyncio.sleep(settings.usage_counter_sync_interval_seconds)
            except asyncio.CancelledError:
                raise
            await _sync_usage_counters_once("periodic")

    sync_task = asyncio.create_task(_sync_usage_counters_periodically())

    yield

    sync_task.cancel()
    try:
        await sync_task
    except asyncio.CancelledError:
        pass

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

    # Step 4.5: docs/SPEC.md §13's own "a flush on shutdown" -- last,
    # not first: every other shutdown step above still runs (and still
    # logs/traces normally) even if this one were ever slow, matching
    # this hook's own "one failing disposal must never prevent another"
    # discipline. shutdown_tracing() is a no-op, never raising, when
    # tracing was never configured (app.state.tracer_provider is None).
    shutdown_tracing(app.state.tracer_provider)


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
    # [SECURITY] Step 7-follow-up: the ONE real CORS mechanism for every
    # visitor-facing (widget) endpoint -- see app/auth/cors.py's own
    # header for the full design. Added before any router, matching
    # Starlette's own documented middleware-ordering rule (added-last
    # runs outermost/first on the way in); it is a no-op for every
    # dashboard/admin/health path (D-6.1-cors-same-origin: that surface
    # stays CORS-free by design).
    app.add_middleware(WidgetCORSMiddleware)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(handoff_router)
    app.include_router(dashboard_router)
    app.include_router(dashboard_tenant_router)
    app.include_router(dashboard_source_router)
    app.include_router(dashboard_escalation_router)
    app.include_router(admin_router)
    return app
