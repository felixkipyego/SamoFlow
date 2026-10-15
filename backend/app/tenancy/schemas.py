# backend/app/tenancy/schemas.py
# Step 6.2: validation schemas for the dashboard's own writes into
# `tenants.config` (a raw JSONB dict, app/tenancy/models.py -- no
# Pydantic schema existed for its shape before this task, confirmed by
# reading that file directly: every field is an implicit contract
# between whatever writes it and whatever reads it). Each section below
# matches exactly one dashboard settings "card" (its own PATCH endpoint,
# app/dashboard/tenant_routes.py), and validates ONLY that section's own
# keys -- never the whole config dict -- so saving one card can never
# silently touch another's fields.
#
# Field NAMES below are not invented here: they are the real keys
# already read by production code before this task (confirmed live by
# grepping `tenant.config.get(...)` across app/), matched exactly so
# this task's own new writers agree with every existing reader:
#   retention_days                  app/ingest/job_handlers.py
#   escalation_retention_months     app/ingest/job_handlers.py
#   escalation_email                app/handoff/routes.py
#   alert_email                     app/ingest/job_handlers.py
#   trace_text_enabled              app/telemetry/service.py
#   title/greeting/colors/logo_url/position/privacy_notice_url
#                                   app/auth/routes.py (_PUBLIC_WIDGET_CONFIG_FIELDS)
#
# Widget-look validation (colors/logo_url/privacy_notice_url) MIRRORS,
# field for field, the widget's own client-side defensive validation
# (widget/chat/src/config-validation.js, color.js, url-safety.js) --
# confirmed by reading those files directly before writing the regexes
# and the URL check below, not guessed. Rejecting bad input server-side
# at the trust boundary (rule 11) is strictly better than relying only
# on the widget's own fallback-to-default behavior: a tenant typing a
# bad color should see a form error, not silently get the default color
# with no explanation days later.
import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# Mirrors widget/chat/src/color.js exactly: hex (#rgb/#rgba/#rrggbb/
# #rrggbbaa) or the numeric functional forms (rgb/rgba/hsl/hsla), never
# named colors/url()/var()/calc() -- same scope, same reasoning (a
# dashboard color picker emits hex or rgb() anyway).
_HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_NUMBER = r"-?\d+(?:\.\d+)?%?"
_RGB_COLOR = re.compile(
    rf"^rgba?\(\s*{_NUMBER}\s*,\s*{_NUMBER}\s*,\s*{_NUMBER}\s*(?:,\s*{_NUMBER}\s*)?\)$",
    re.IGNORECASE,
)
_HSL_COLOR = re.compile(
    rf"^hsla?\(\s*{_NUMBER}\s*,\s*{_NUMBER}\s*,\s*{_NUMBER}\s*(?:,\s*{_NUMBER}\s*)?\)$",
    re.IGNORECASE,
)


def is_valid_css_color(value: str) -> bool:
    if not isinstance(value, str) or value != value.strip() or value == "":
        # No leading/trailing whitespace tolerated -- matches color.js's
        # own "a value that only validates after trimming is not the
        # value that would be used verbatim" reasoning exactly.
        return False
    return bool(_HEX_COLOR.match(value) or _RGB_COLOR.match(value) or _HSL_COLOR.match(value))


def is_https_url(value: str) -> bool:
    # Mirrors widget/chat/src/url-safety.js's isHttpsUrl(): rejects
    # javascript:/data:/vbscript: and every other non-https scheme, and
    # protocol-relative/relative values (no netloc). The WHATWG URL
    # parser strips ASCII tab/newline/CR before parsing -- a
    # "jav\tascript:" string that defeats a naive prefix check still
    # normalizes to a live javascript: URL in a real browser; stripping
    # those three characters first here closes the identical gap
    # Python's own urlsplit (which does NOT strip them) would otherwise
    # leave open.
    if not isinstance(value, str) or value == "":
        return False
    cleaned = value.strip().translate({9: None, 10: None, 13: None})
    try:
        parsed = urlsplit(cleaned)
    except ValueError:
        return False
    return parsed.scheme.lower() == "https" and bool(parsed.netloc)


def _https_url_or_none(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if not is_https_url(value):
        raise ValueError("must be an https:// URL")
    return value


class SupportDetailsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    support_email: EmailStr | None = None
    support_phone: str | None = Field(default=None, max_length=40)
    support_contact_link: str | None = None
    support_hours: str | None = Field(default=None, max_length=200)
    support_message: str | None = Field(default=None, max_length=1000)

    @field_validator("support_contact_link")
    @classmethod
    def _validate_contact_link(cls, value: str | None) -> str | None:
        return _https_url_or_none(value)


class AlertEmailsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_email: EmailStr | None = None
    escalation_email: EmailStr | None = None


class WidgetColors(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: str

    @field_validator("primary")
    @classmethod
    def _validate_primary(cls, value: str) -> str:
        if not is_valid_css_color(value):
            raise ValueError("must be a hex (#rgb/#rrggbb/#rrggbbaa) or rgb()/hsl() color value")
        return value


class WidgetLookUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    greeting: str = Field(min_length=1, max_length=200)
    colors: WidgetColors
    logo_url: str | None = None
    position: Literal["left", "right"]
    privacy_notice_url: str | None = None

    @field_validator("logo_url", "privacy_notice_url")
    @classmethod
    def _validate_https_url(cls, value: str | None) -> str | None:
        return _https_url_or_none(value)


class RetentionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Allowed ranges match Settings.default_chat_retention_days (1-90)
    # and Settings.default_escalation_retention_months (1-24) exactly
    # (docs/SPEC.md §4.6/§7) -- those two Settings fields are the
    # platform DEFAULT used when a tenant has no override; these are
    # the per-tenant override this task builds a real UI for.
    retention_days: int = Field(ge=1, le=90)
    escalation_retention_months: int = Field(ge=1, le=24)


class TraceTextUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_text_enabled: bool


class AllowedOriginsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Each entry is normalised server-side (normalize_origin(), app/
    # auth/origin.py -- the SAME function the platform-admin CLI uses,
    # per this task's own direct instruction) rather than validated
    # here: an origin that fails to normalise is a 422 with a message
    # naming which one, not a silent drop.
    allowed_origins: list[str] = Field(max_length=20)
