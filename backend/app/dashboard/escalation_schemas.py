# backend/app/dashboard/escalation_schemas.py
# Step 6.4: response models for app/dashboard/escalation_routes.py
# (the Inbox screen and usage meter).
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class EscalationSummaryResponse(BaseModel):
    id: uuid.UUID
    status: str
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    created_at: datetime


class EscalationDetailResponse(EscalationSummaryResponse):
    # Already markdown-unescaped server-side (app/text_escaping.py's
    # `markdown_unescape()`, the Step 4.4 shared helper) -- the ONE
    # transformation this field carries. The frontend renders it as a
    # plain text node, never via dangerouslySetInnerHTML, which is
    # itself the "then HTML-escape" half: a React/DOM text node can
    # never be reinterpreted as markup, matching docs/SPEC.md §7's own
    # "always escaped on output" rule by construction, not by a second
    # manual escaping pass this field would otherwise need.
    transcript: str


class EscalationsListResponse(BaseModel):
    escalations: list[EscalationSummaryResponse]


class OpenEscalationCountResponse(BaseModel):
    count: int


class UpdateEscalationStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # "new" deliberately excluded -- that value is set only at creation
    # (app/handoff/routes.py's own `_STATUS_NEW`), never a dashboard
    # write.
    status: Literal["seen", "resolved"]


class UsageResponse(BaseModel):
    # False for a tenant with no plan assigned -- `quota`/`fraction`/
    # `state` all follow from this (matches `get_plan_limits()`'s own
    # `None`-for-no-plan contract, app/plans/service.py).
    plan_assigned: bool
    quota: int | None
    used_this_month: int
    fraction_used: float | None
    # Mirrors the exact two thresholds the usage-alert sweep emails at
    # (app/ingest/job_handlers.py's `handle_usage_alert_sweep()`: 80%
    # and 100%) -- this field and that sweep's own `threshold_label`
    # loop must never drift apart, since this is the dashboard's own
    # read of the SAME real `fraction` that sweep already computed from
    # the SAME authoritative `usage_events` count.
    state: Literal["no_plan", "normal", "warning", "exceeded"]
