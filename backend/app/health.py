# backend/app/health.py
# Task 1.1.e: liveness check only. Must answer even if Postgres or Qdrant
# are unreachable, so this module must never import or call anything that
# touches either of them.
from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.head("/health", include_in_schema=False)
async def health_head() -> Response:
    return Response(status_code=200)
