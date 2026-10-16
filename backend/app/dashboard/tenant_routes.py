# backend/app/dashboard/tenant_routes.py
# Step 6.2: tenant settings (support details, alert/escalation email,
# widget look, retention, trace-text switch) and site keys/allowed
# origins. Same conventions as app/dashboard/routes.py (Step 6.1):
# require_csrf() before any write, cheapest-first ordering, tenant
# scope ALWAYS from the logged-in membership
# (get_dashboard_tenant_repository), never from a request parameter.
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.origin import normalize_origin
from app.config import get_settings
from app.dashboard.csrf import require_csrf
from app.dashboard.dependencies import (
    DashboardIdentity,
    get_dashboard_tenant_repository,
    require_recent_auth,
)
from app.dashboard.tenant_schemas import (
    GoLiveCheckResponse,
    InstallSnippetResponse,
    SiteKeyResponse,
    TenantConfigResponse,
)
from app.db import get_db_session
from app.ingest.repository import IngestRepository
from app.tenancy.repository import (
    SiteKeyAlreadyExistsError,
    TenantScopedRepository,
    generate_site_key,
)
from app.tenancy.schemas import (
    AlertEmailsUpdate,
    AllowedOriginsUpdate,
    RetentionUpdate,
    SupportDetailsUpdate,
    TraceTextUpdate,
    WidgetLookUpdate,
)

router = APIRouter(prefix="/api/v1/dashboard/tenant")

# Mirrors app/auth/routes.py's own _PUBLIC_WIDGET_CONFIG_FIELDS
# defaults exactly -- a tenant with no override sees the same values
# the widget itself would actually render, never blank fields that
# disagree with production behavior.
_WIDGET_LOOK_DEFAULTS = {
    "title": "Chat",
    "greeting": "Hi! How can we help?",
    "colors": {"primary": "#4f46e5"},
    "logo_url": None,
    "position": "right",
    "privacy_notice_url": None,
}


def _config_response(config: dict) -> TenantConfigResponse:
    settings = get_settings()
    return TenantConfigResponse(
        support_email=config.get("support_email"),
        support_phone=config.get("support_phone"),
        support_contact_link=config.get("support_contact_link"),
        support_hours=config.get("support_hours"),
        support_message=config.get("support_message"),
        alert_email=config.get("alert_email"),
        escalation_email=config.get("escalation_email"),
        title=config.get("title", _WIDGET_LOOK_DEFAULTS["title"]),
        greeting=config.get("greeting", _WIDGET_LOOK_DEFAULTS["greeting"]),
        colors=config.get("colors", _WIDGET_LOOK_DEFAULTS["colors"]),
        logo_url=config.get("logo_url"),
        position=config.get("position", _WIDGET_LOOK_DEFAULTS["position"]),
        privacy_notice_url=config.get("privacy_notice_url"),
        retention_days=config.get("retention_days", settings.default_chat_retention_days),
        escalation_retention_months=config.get(
            "escalation_retention_months", settings.default_escalation_retention_months
        ),
        trace_text_enabled=config.get("trace_text_enabled", True),
    )


@router.get("", response_model=TenantConfigResponse)
async def get_tenant_config(
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
) -> TenantConfigResponse:
    tenant = await repo.get_tenant()
    return _config_response(tenant.config)


@router.patch("/support-details", response_model=TenantConfigResponse)
async def update_support_details(
    payload: SupportDetailsUpdate,
    request: Request,
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TenantConfigResponse:
    require_csrf(request)
    tenant = await repo.update_config(payload.model_dump())
    await session.commit()
    return _config_response(tenant.config)


@router.patch("/alert-emails", response_model=TenantConfigResponse)
async def update_alert_emails(
    payload: AlertEmailsUpdate,
    request: Request,
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TenantConfigResponse:
    require_csrf(request)
    tenant = await repo.update_config(payload.model_dump())
    await session.commit()
    return _config_response(tenant.config)


@router.patch("/widget-look", response_model=TenantConfigResponse)
async def update_widget_look(
    payload: WidgetLookUpdate,
    request: Request,
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TenantConfigResponse:
    require_csrf(request)
    patch = payload.model_dump()
    patch["colors"] = {"primary": payload.colors.primary}
    tenant = await repo.update_config(patch)
    await session.commit()
    return _config_response(tenant.config)


@router.patch("/retention", response_model=TenantConfigResponse)
async def update_retention(
    payload: RetentionUpdate,
    request: Request,
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TenantConfigResponse:
    require_csrf(request)
    tenant = await repo.update_config(payload.model_dump())
    await session.commit()
    return _config_response(tenant.config)


@router.patch("/trace-text", response_model=TenantConfigResponse)
async def update_trace_text(
    payload: TraceTextUpdate,
    request: Request,
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> TenantConfigResponse:
    require_csrf(request)
    tenant = await repo.update_config(payload.model_dump())
    await session.commit()
    return _config_response(tenant.config)


@router.get("/site-keys", response_model=list[SiteKeyResponse])
async def list_site_keys(
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
) -> list[SiteKeyResponse]:
    site_keys = await repo.list_site_keys()
    return [SiteKeyResponse.model_validate(sk, from_attributes=True) for sk in site_keys]


@router.post("/site-keys", response_model=SiteKeyResponse, status_code=201)
async def create_site_key(
    request: Request,
    identity: DashboardIdentity = Depends(require_recent_auth()),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> SiteKeyResponse:
    # docs/SPEC.md §12: "changing site keys or allowed origins" requires
    # recent authentication -- creating a new key is the clearest case
    # of "changing site keys" there is.
    require_csrf(request)
    repo = TenantScopedRepository(tenant_id=identity.tenant_id, session=session)
    try:
        site_key = await repo.create_site_key(
            key=generate_site_key(), allowed_origins=[], environment="production"
        )
    except SiteKeyAlreadyExistsError:
        # Vanishingly unlikely (a 256-bit collision) -- the honest 409 is
        # still correct, matching this repository's own exception shape,
        # rather than retrying silently with a second random value.
        raise HTTPException(status_code=409, detail="could not allocate a new site key") from None
    await session.commit()
    return SiteKeyResponse.model_validate(site_key, from_attributes=True)


@router.patch("/site-keys/{site_key_id}/origins", response_model=SiteKeyResponse)
async def update_site_key_origins(
    site_key_id: uuid.UUID,
    payload: AllowedOriginsUpdate,
    request: Request,
    identity: DashboardIdentity = Depends(require_recent_auth()),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> SiteKeyResponse:
    require_csrf(request)
    normalized: list[str] = []
    for raw in payload.allowed_origins:
        parsed = normalize_origin(raw)
        if parsed is None:
            raise HTTPException(
                status_code=422, detail=f"{raw!r} is not a valid single origin (scheme://host[:port])"
            )
        normalized.append(parsed)

    repo = TenantScopedRepository(tenant_id=identity.tenant_id, session=session)
    site_key = await repo.set_site_key_allowed_origins(site_key_id, normalized)
    if site_key is None:
        raise HTTPException(status_code=404, detail="site key not found")
    await session.commit()
    return SiteKeyResponse.model_validate(site_key, from_attributes=True)


@router.get("/go-live-check", response_model=GoLiveCheckResponse)
async def get_go_live_check(
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> GoLiveCheckResponse:
    # Read-only mirror of docs/SPEC.md's own real gate ("a site key
    # moves from draft to live only through a server-side check: at
    # least one contact method and one allowed origin"). This endpoint
    # computes that SAME pair server-side and surfaces it as data, so
    # the Overview screen never duplicates the rule client-side and can
    # never drift from it -- but it does not itself perform, or gate,
    # any status transition (that stays "done elsewhere", exactly as
    # `create_site_key()`'s own header comment already says); issuing a
    # live site key remains a platform-admin CLI action, out of this
    # task's scope (6.5 Platform admin is explicitly POST-PILOT).
    tenant = await repo.get_tenant()
    config = tenant.config
    has_contact_method = bool(
        config.get("support_email")
        or config.get("support_phone")
        or config.get("support_contact_link")
    )

    site_keys = await repo.list_site_keys()
    has_allowed_origin = any(sk.allowed_origins for sk in site_keys)

    ingest_repo = IngestRepository(tenant_id=repo.tenant_id, session=session)
    domains = await ingest_repo.list_domains()
    has_verified_domain = any(d.status == "verified" for d in domains)

    sources = await ingest_repo.list_sources()
    has_enabled_source = any(s.enabled for s in sources)

    return GoLiveCheckResponse(
        has_contact_method=has_contact_method,
        has_allowed_origin=has_allowed_origin,
        has_verified_domain=has_verified_domain,
        has_enabled_source=has_enabled_source,
        # The real gate (docs/SPEC.md, "Going live" row + §12/§6.1's own
        # line) names exactly two conditions -- contact method and
        # allowed origin. Verified domain and enabled source are real,
        # useful pilot-readiness signals (surfaced above for the
        # checklist), but are NOT part of the actual draft-to-live gate
        # itself; folding them into `ready_for_go_live` would make this
        # field claim a stricter rule than the server actually enforces.
        ready_for_go_live=has_contact_method and has_allowed_origin,
    )


@router.get("/install-snippet", response_model=InstallSnippetResponse)
async def get_install_snippet(
    repo: TenantScopedRepository = Depends(get_dashboard_tenant_repository),  # noqa: B008
) -> InstallSnippetResponse:
    site_keys = await repo.list_site_keys()
    live_or_first = next((sk for sk in site_keys if sk.status == "live"), None) or (
        site_keys[0] if site_keys else None
    )
    if live_or_first is None:
        raise HTTPException(status_code=404, detail="no site key exists for this tenant yet")
    settings = get_settings()
    return InstallSnippetResponse(
        site_key=live_or_first.key, base_url=settings.public_api_base_url
    )
