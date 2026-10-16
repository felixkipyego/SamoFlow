# backend/app/dashboard/escalation_routes.py
# Step 6.4: the Inbox screen (list/transcript/status/delete, tenant-
# scoped, with the sidebar's own open count) and the usage meter. Same
# conventions as every other dashboard route module: require_csrf()
# before any write, tenant scope ALWAYS from the logged-in membership
# (get_dashboard_tenant_repository), never a request parameter.
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.csrf import require_csrf
from app.dashboard.dependencies import get_dashboard_tenant_repository
from app.dashboard.escalation_schemas import (
    EscalationDetailResponse,
    EscalationsListResponse,
    EscalationSummaryResponse,
    OpenEscalationCountResponse,
    UpdateEscalationStatusRequest,
    UsageResponse,
)
from app.db import get_db_session
from app.handoff.models import Escalation
from app.handoff.repository import EscalationRepository
from app.plans import usage
from app.plans.service import get_plan_limits
from app.tenancy.repository import TenantScopedRepository
from app.text_escaping import markdown_unescape

router = APIRouter(prefix="/api/v1/dashboard")

_WARNING_FRACTION = 0.80
_EXCEEDED_FRACTION = 1.0


def _summary(escalation: Escalation) -> EscalationSummaryResponse:
    return EscalationSummaryResponse(
        id=escalation.id,
        status=escalation.status,
        contact_name=escalation.contact_name,
        contact_email=escalation.contact_email,
        contact_phone=escalation.contact_phone,
        created_at=escalation.created_at,
    )


@router.get("/escalations", response_model=EscalationsListResponse)
async def list_escalations(
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> EscalationsListResponse:
    escalation_repo = EscalationRepository(tenant_id=repo.tenant_id, session=session)
    escalations = await escalation_repo.list_escalations()
    return EscalationsListResponse(escalations=[_summary(e) for e in escalations])


@router.get("/escalations/open-count", response_model=OpenEscalationCountResponse)
async def get_open_escalation_count(
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> OpenEscalationCountResponse:
    escalation_repo = EscalationRepository(tenant_id=repo.tenant_id, session=session)
    count = await escalation_repo.count_open()
    return OpenEscalationCountResponse(count=count)


@router.get("/escalations/{escalation_id}", response_model=EscalationDetailResponse)
async def get_escalation(
    escalation_id: uuid.UUID,
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> EscalationDetailResponse:
    escalation_repo = EscalationRepository(tenant_id=repo.tenant_id, session=session)
    escalation = await escalation_repo.get_escalation(escalation_id)
    if escalation is None:
        raise HTTPException(status_code=404, detail="escalation not found")
    return EscalationDetailResponse(
        **_summary(escalation).model_dump(),
        # Step 4.4's own shared helper (app/text_escaping.py), applied
        # to the whole stored snapshot -- that module's own docstring
        # names this exact case ("a visitor's own raw message... there
        # is nothing to invert") as harmless for display purposes, the
        # identical tradeoff app/handoff/routes.py's own email body
        # accepts by applying it only to assistant lines there; here
        # the transcript is a single already-joined Text column with
        # no reliable per-line role boundary to re-split on, so one
        # pass over the whole snapshot is the correct, simpler choice,
        # not an oversight.
        transcript=markdown_unescape(escalation.transcript),
    )


@router.patch("/escalations/{escalation_id}/status", response_model=EscalationSummaryResponse)
async def update_escalation_status(
    escalation_id: uuid.UUID,
    payload: UpdateEscalationStatusRequest,
    request: Request,
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> EscalationSummaryResponse:
    require_csrf(request)
    escalation_repo = EscalationRepository(tenant_id=repo.tenant_id, session=session)
    escalation = await escalation_repo.set_status(escalation_id, payload.status)
    if escalation is None:
        raise HTTPException(status_code=404, detail="escalation not found")
    await session.commit()
    return _summary(escalation)


@router.delete("/escalations/{escalation_id}", status_code=204)
async def delete_escalation(
    escalation_id: uuid.UUID,
    request: Request,
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> None:
    require_csrf(request)
    escalation_repo = EscalationRepository(tenant_id=repo.tenant_id, session=session)
    deleted = await escalation_repo.delete_escalation(escalation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="escalation not found")
    await session.commit()


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> UsageResponse:
    tenant = await repo.get_tenant()
    limits = await get_plan_limits(tenant, session)
    # The AUTHORITATIVE DB-backed count (app/plans/usage.py's own
    # monthly_count_from_db()), not the API process's own in-memory
    # counter -- this is the SAME source of truth the usage-alert
    # sweep itself reads (app/ingest/job_handlers.py's
    # handle_usage_alert_sweep()), so this screen's own numbers can
    # never disagree with whichever alert email the tenant may have
    # already received this month.
    used = await usage.monthly_count_from_db(tenant.id, session, clock=lambda: datetime.now(UTC))

    if limits is None:
        return UsageResponse(
            plan_assigned=False,
            quota=None,
            used_this_month=used,
            fraction_used=None,
            state="no_plan",
        )

    fraction = used / limits.ai_message_quota_per_month
    if fraction >= _EXCEEDED_FRACTION:
        state = "exceeded"
    elif fraction >= _WARNING_FRACTION:
        state = "warning"
    else:
        state = "normal"

    return UsageResponse(
        plan_assigned=True,
        quota=limits.ai_message_quota_per_month,
        used_this_month=used,
        fraction_used=fraction,
        state=state,
    )
