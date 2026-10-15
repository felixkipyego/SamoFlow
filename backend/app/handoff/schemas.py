# backend/app/handoff/schemas.py
# Step 4.4 [SECURITY]: the request/response shapes for
# POST /api/v1/conversations/{cid}/escalate (docs/SPEC.md §7: "the
# visitor may add optional contact details"). Pure data shape only --
# no database/settings access, matching app/chat/schemas.py's own
# layering.
#
# [SECURITY] Contact fields and email header injection: app/notifications/
# service.py's own send_email() never puts ANY of these three fields
# into an email HEADER (confirmed by reading that module directly -- see
# app/handoff/routes.py's own header comment for the full reasoning) --
# they only ever appear in the HTML-escaped BODY. The control-character
# rejection below is still real, deliberate defense in depth, not
# theater: it protects a FUTURE caller that might one day put one of
# these fields into a header (a Reply-To, say) without having to
# rediscover this exact risk from scratch, and it rejects visitor input
# that could never render sensibly anyway (a literal newline inside a
# "name" field). Python's own stdlib already raises loudly if a CRLF
# ever DID reach a header (`email.message.EmailMessage.__setitem__`
# raises `ValueError` on embedded CR/LF, confirmed live) -- this is a
# second, independent layer, not the only one.
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

# C0 controls (0x00-0x1F) and DEL (0x7F) -- covers CR/LF plus every
# other non-printable character a contact field has no legitimate
# reason to contain. Allows normal Unicode text (names/phones in any
# script), unlike an ASCII-only allowlist, which would reject real
# visitor names outright.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
# Deliberately NOT a full RFC 5322 validator (that is a famously large,
# low-value surface to hand-roll correctly, and this field is optional
# contact info for a human to read, never used to authenticate or send
# anything automated to) -- a pragmatic "looks like an email" shape,
# matching this project's own "validate at the boundary, proportionate
# to what the field is actually used for" discipline.
_LOOKS_LIKE_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _reject_control_characters(value: str | None) -> str | None:
    if value is not None and _CONTROL_CHARS.search(value):
        raise ValueError("must not contain control characters (including CR/LF)")
    return value


class EscalationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_name: str | None = Field(default=None, max_length=200)
    contact_email: str | None = Field(default=None, max_length=320)
    contact_phone: str | None = Field(default=None, max_length=40)

    @field_validator("contact_name", "contact_phone")
    @classmethod
    def _no_control_chars(cls, value: str | None) -> str | None:
        return _reject_control_characters(value)

    @field_validator("contact_email")
    @classmethod
    def _valid_email_shape(cls, value: str | None) -> str | None:
        value = _reject_control_characters(value)
        if value is not None and not _LOOKS_LIKE_EMAIL.match(value):
            raise ValueError("does not look like a valid email address")
        return value


class EscalationResponse(BaseModel):
    escalation_id: uuid.UUID
    status: str
