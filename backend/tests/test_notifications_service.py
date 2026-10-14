# backend/tests/test_notifications_service.py
# Task 4.3: tests for app/notifications/service.py's real send_email()
# (replacing the Step 1.1b stub).
#
# NOT a live, real-socket SMTP test: a real STARTTLS handshake needs a
# real TLS certificate on the server side, which this project has no
# self-signed-CA tooling for and no real SMTP credentials configured
# anywhere (confirmed: no smtp_* value exists in any .env in this repo)
# -- building one would be a disproportionate side-quest for a thin
# wrapper around three sequential smtplib calls. Tested instead by
# monkeypatching `smtplib.SMTP` itself with a small recording fake that
# proves the real CALL SEQUENCE and ARGUMENTS (starttls, then login only
# when credentials are configured, then send_message, in that exact
# order) -- the same pragmatic approach this exact problem gets in the
# wider Python ecosystem (e.g. Django's own email backend tests), not a
# weaker proof chosen out of laziness. The real network/TLS path against
# a REAL SMTP server is a separate, genuine manual opt-in script
# (backend/scripts/manual_check_smtp_send.py), matching this project's
# own established "configured but not verified until the manual script
# passes" precedent for the Anthropic secondary chat-model provider.
import pytest

from app.config import get_settings
from app.notifications import service as notifications_service
from app.notifications.service import is_smtp_configured, send_email
from tests.conftest import VALID_ENV, set_valid_env


class _FakeSMTP:
    """Records the exact call sequence, matching the real smtplib.SMTP's
    own context-manager protocol -- `with SMTP(...) as server:` calls
    __enter__/__exit__, never a bare constructor.
    """

    calls: list[str] = []
    raise_on: str | None = None

    def __init__(self, host, port, timeout=None):
        _FakeSMTP.calls.append(f"__init__:{host}:{port}")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def starttls(self):
        _FakeSMTP.calls.append("starttls")
        if _FakeSMTP.raise_on == "starttls":
            raise RuntimeError("simulated starttls failure")

    def login(self, username, password):
        _FakeSMTP.calls.append(f"login:{username}:{password}")
        if _FakeSMTP.raise_on == "login":
            raise RuntimeError("simulated login failure")

    def send_message(self, message):
        _FakeSMTP.calls.append(f"send_message:{message['To']}:{message['Subject']}")
        if _FakeSMTP.raise_on == "send_message":
            raise RuntimeError("simulated send failure")


@pytest.fixture(autouse=True)
def _reset_fake_smtp():
    _FakeSMTP.calls = []
    _FakeSMTP.raise_on = None
    yield


def test_is_smtp_configured_false_when_host_is_unset(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()
    assert is_smtp_configured(get_settings()) is False


def test_is_smtp_configured_false_when_from_address_is_unset(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, SMTP_HOST="smtp.example.com")
    get_settings.cache_clear()
    assert is_smtp_configured(get_settings()) is False


def test_is_smtp_configured_true_when_both_are_set(monkeypatch):
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        SMTP_HOST="smtp.example.com",
        SMTP_FROM_ADDRESS="alerts@example.com",
    )
    get_settings.cache_clear()
    assert is_smtp_configured(get_settings()) is True


async def test_send_email_returns_false_and_makes_no_connection_when_not_configured(
    monkeypatch, caplog
):
    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()
    monkeypatch.setattr(notifications_service, "SMTP", _FakeSMTP)

    result = await send_email("tenant@example.com", "Quota alert", "body")

    assert result is False
    assert _FakeSMTP.calls == []
    assert "SMTP is not configured" in caplog.text


async def test_send_email_real_call_sequence_without_credentials(monkeypatch):
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        SMTP_HOST="smtp.example.com",
        SMTP_PORT="2525",
        SMTP_FROM_ADDRESS="alerts@example.com",
    )
    get_settings.cache_clear()
    monkeypatch.setattr(notifications_service, "SMTP", _FakeSMTP)

    result = await send_email("tenant@example.com", "Quota alert", "80% used")

    assert result is True
    assert _FakeSMTP.calls == [
        "__init__:smtp.example.com:2525",
        "starttls",
        "send_message:tenant@example.com:Quota alert",
    ]


async def test_send_email_logs_in_when_credentials_are_configured(monkeypatch):
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        SMTP_HOST="smtp.example.com",
        SMTP_FROM_ADDRESS="alerts@example.com",
        SMTP_USERNAME="alerts-bot",
        SMTP_PASSWORD="hunter2",  # noqa: S106 (test fixture value, not a real secret)
    )
    get_settings.cache_clear()
    monkeypatch.setattr(notifications_service, "SMTP", _FakeSMTP)

    result = await send_email("tenant@example.com", "Quota alert", "100% used")

    assert result is True
    assert _FakeSMTP.calls == [
        "__init__:smtp.example.com:587",
        "starttls",
        "login:alerts-bot:hunter2",
        "send_message:tenant@example.com:Quota alert",
    ]


async def test_send_email_returns_false_on_a_real_send_failure_not_raises(monkeypatch, caplog):
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        SMTP_HOST="smtp.example.com",
        SMTP_FROM_ADDRESS="alerts@example.com",
    )
    get_settings.cache_clear()
    monkeypatch.setattr(notifications_service, "SMTP", _FakeSMTP)
    _FakeSMTP.raise_on = "send_message"

    result = await send_email("tenant@example.com", "Quota alert", "body")

    assert result is False
    assert "SMTP send failed" in caplog.text
