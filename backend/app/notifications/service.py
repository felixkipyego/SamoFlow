# backend/app/notifications/service.py
# Task 4.3: the real send_email(), replacing the Step 1.1b stub
# (`raise NotImplementedError`). docs/SPEC.md §14/§18: "sent through our
# own SMTP server, behind a provider-neutral interface" -- stdlib
# `smtplib` directly, not a new dependency (docs/SPEC.md §14 itself also
# says "v1 records synchronous SMTP results only", confirming a plain
# blocking smtplib call, run off the event loop via `asyncio.to_thread()`,
# is the intended v1 shape, not an async SMTP library).
#
# Returns `bool` (never raises for an ordinary send failure): the CALLER
# (app/ingest/job_handlers.py's handle_usage_alert_sweep()) is the one
# that knows which `email_log` row this attempt corresponds to and
# decides retry timing -- this function's own job is only "did the send
# succeed", logged with the real exception server-side either way.
#
# TLS: `starttls()` is called unconditionally once connected (docs/
# SPEC.md §14's own named requirement) -- no Settings flag to disable it,
# since nothing in this project's own spec names a legitimate reason to
# send unencrypted.
import asyncio
import logging
from email.message import EmailMessage
from smtplib import SMTP

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def is_smtp_configured(settings: Settings) -> bool:
    """`False` is a normal, expected state -- a deployment that has not
    configured a real SMTP server yet (docs/SPEC.md §14's own "our own
    SMTP server" is real infrastructure a fresh deployment may not have
    stood up on day one) must still start and serve chats; alert emails
    are simply not sent, matching Settings.anthropic_api_key's own
    established "optional, opt-in feature" precedent exactly.
    """
    return settings.smtp_host is not None and settings.smtp_from_address is not None


async def send_email(to: str, subject: str, body: str, *, subtype: str = "plain") -> bool:
    """`subtype`: Step 4.4 adds `"html"` (the escalation email -- a real
    transcript needs more than one paragraph of plain text to stay
    readable). `"plain"` stays the default so Task 4.3's own usage-alert
    call site (plain text, no visitor content, no change needed) is
    unaffected. Deliberately NOT a multipart/alternative (plain + html)
    message: this project's whole email mechanism is intentionally
    minimal (one direct stdlib smtplib call, docs/SPEC.md §14's own
    "v1 records synchronous SMTP results only"), and a single-part
    `text/html` message is a complete, widely-supported email on its
    own -- building a second, parallel plain-text rendering of the same
    content for every future HTML email is speculative complexity this
    project does not need yet (rule 11).
    """
    settings = get_settings()
    if not is_smtp_configured(settings):
        logger.warning(
            "SMTP is not configured -- email not sent (subject=%r)", subject
        )
        return False
    return await asyncio.to_thread(_send_email_sync, settings, to, subject, body, subtype)


def _send_email_sync(
    settings: Settings, to: str, subject: str, body: str, subtype: str = "plain"
) -> bool:
    message = EmailMessage()
    message["From"] = settings.smtp_from_address
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body, subtype=subtype)
    try:
        # A real 10s timeout -- an SMTP server that never responds must
        # not hang this call indefinitely (smtplib's own default is no
        # timeout at all), matching this project's own "every blocking
        # call gets an explicit bound" discipline.
        with SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            server.starttls()
            password = settings.smtp_password_str()
            if settings.smtp_username is not None and password is not None:
                server.login(settings.smtp_username, password)
            server.send_message(message)
    except Exception:
        logger.exception("SMTP send failed (subject=%r)", subject)
        return False
    return True
