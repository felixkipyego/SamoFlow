# backend/app/health.py
# Task 1.1.e: /health is a liveness check only. Must answer even if Postgres
# or Qdrant are unreachable, so health()/health_head() must never import or
# call anything that touches either of them.
#
# Task 1.4.j adds /ready, a SEPARATE, readiness endpoint: it deliberately
# DOES touch Postgres (one lightweight "SELECT 1" through the same
# get_db_session()/get_engine() machinery every other endpoint uses),
# resolving the Postgres-readiness open marker recorded since Step 1.1.
# Bounded by an explicit timeout (below) so a hung or slow database can
# never make this endpoint itself hang -- Docker's own healthcheck already
# has its own outer timeout (deploy/docker-compose.yml), but this endpoint
# must never rely on that alone.
import asyncio
import logging

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db_session

logger = logging.getLogger(__name__)

router = APIRouter()

# Comfortably under the outer 3-second urllib timeout the Compose
# healthcheck itself already uses for this same endpoint (matching
# /health's own existing convention) -- generous enough for a trivial
# "SELECT 1" under normal conditions, short enough that a hung connection
# still fails fast rather than consuming the outer check's own budget.
_READY_TIMEOUT_SECONDS = 2.0

# Fixed bodies, never the raw driver exception, the connection string, or a
# stack trace: the real error (whatever it is) is logged server-side only,
# for operators -- never in what the client receives.
_READY_OK_BODY = {"status": "ready"}
_READY_NOT_READY_BODY = {"status": "not ready"}


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.head("/health", include_in_schema=False)
async def health_head() -> Response:
    return Response(status_code=200)


@router.get("/ready")
async def ready(
    session: AsyncSession = Depends(get_db_session),  # noqa: B008 (FastAPI's own Depends() idiom)
) -> Response:
    try:
        async with asyncio.timeout(_READY_TIMEOUT_SECONDS):
            await session.execute(text("SELECT 1"))
    except (TimeoutError, SQLAlchemyError) as exc:
        # exc's own str() may contain driver/connection detail -- fine for
        # a server-side log line (operators need this to debug), never for
        # the response body below.
        logger.warning("readiness check failed: %s", exc)
        return JSONResponse(status_code=503, content=_READY_NOT_READY_BODY)
    return JSONResponse(status_code=200, content=_READY_OK_BODY)
