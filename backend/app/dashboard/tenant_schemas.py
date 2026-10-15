# backend/app/dashboard/tenant_schemas.py
# Step 6.2: response models for app/dashboard/tenant_routes.py. Request
# validation for the config-writing endpoints lives in
# app/tenancy/schemas.py instead (shared with anything else that ever
# needs to validate a `tenants.config` write, not dashboard-specific).
import uuid

from pydantic import BaseModel


class TenantConfigResponse(BaseModel):
    # Every dashboard-writable tenants.config field, with the SAME
    # defaults the rest of the codebase already falls back to when a
    # key is absent (confirmed by reading each real reader directly --
    # see app/tenancy/schemas.py's own header comment for the full
    # list) -- so a brand-new tenant with an empty `config` dict shows
    # the dashboard its REAL effective values, not blank fields that
    # silently disagree with what the backend actually does.
    support_email: str | None
    support_phone: str | None
    support_contact_link: str | None
    support_hours: str | None
    support_message: str | None
    alert_email: str | None
    escalation_email: str | None
    title: str
    greeting: str
    colors: dict
    logo_url: str | None
    position: str
    privacy_notice_url: str | None
    retention_days: int
    escalation_retention_months: int
    trace_text_enabled: bool


class SiteKeyResponse(BaseModel):
    id: uuid.UUID
    key: str
    allowed_origins: list[str]
    environment: str
    status: str


class GoLiveCheckResponse(BaseModel):
    # The real server-side draft-to-live gate (docs/SPEC.md §16: "A site
    # key cannot move to live without at least one contact method and
    # one allowed origin, even when the request is made directly to
    # the API") -- surfaced as data, not just enforced, so the Overview
    # screen and the site-key screen can both explain exactly what is
    # still missing before Felix shows this to a real pilot tenant.
    has_contact_method: bool
    has_allowed_origin: bool
    has_verified_domain: bool
    has_enabled_source: bool
    ready_for_go_live: bool


class InstallSnippetResponse(BaseModel):
    site_key: str
    base_url: str | None
