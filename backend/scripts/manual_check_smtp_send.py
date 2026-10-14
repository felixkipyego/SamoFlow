#!/usr/bin/env python3
# backend/scripts/manual_check_smtp_send.py
# Task 4.3: a MANUAL, OPT-IN script making a REAL SMTP connection and
# sending a REAL email -- NEVER run by pytest/CI (not under tests/, no
# test_ prefix, not imported by anything). Run it by hand once a real
# SMTP server is configured:
#
#   cd backend && python scripts/manual_check_smtp_send.py you@example.com
#
# Needs SMTP_HOST/SMTP_PORT/SMTP_FROM_ADDRESS (and SMTP_USERNAME/
# SMTP_PASSWORD if the server requires auth) set in the environment.
#
# Why this exists, matching manual_check_chat_model_streaming.py's own
# precedent exactly: app/notifications/service.py's own test suite
# (tests/test_notifications_service.py) proves send_email()'s call
# SEQUENCE against a recording fake, deliberately NOT a real socket/TLS
# handshake (building a self-signed-CA test harness for one thin
# wrapper would be a disproportionate side-quest, see that test file's
# own header comment) -- so the REAL network/STARTTLS/auth path against
# a REAL SMTP server has never actually been exercised by anything in
# this repository. This script is that missing real-world check, run by
# hand before trusting SMTP alerting in production, the same role
# manual_check_chat_model_streaming.py plays for the Anthropic secondary
# chat-model provider ("configured but not verified" until this passes).
import asyncio
import sys


async def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <recipient-email-address>")
    to = sys.argv[1]

    from app.config import get_settings
    from app.notifications.service import is_smtp_configured, send_email

    settings = get_settings()
    if not is_smtp_configured(settings):
        raise SystemExit(
            "SMTP is not configured (SMTP_HOST/SMTP_FROM_ADDRESS both required) -- "
            "set them in the environment first."
        )

    print(f"Sending a real test email via {settings.smtp_host}:{settings.smtp_port} to {to}...")
    ok = await send_email(
        to,
        "SamoFlow SMTP check",
        "This is a real, manually-triggered test email from "
        "scripts/manual_check_smtp_send.py -- if you received this, "
        "SMTP sending works end to end.",
    )
    if ok:
        print("Sent successfully.")
    else:
        raise SystemExit("Send failed -- see the logged exception above for the real reason.")


if __name__ == "__main__":
    asyncio.run(main())
