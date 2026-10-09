# backend/tests/test_database_adapter.py
# Task 2.8.a [SECURITY]: tests for app/ingest/database_adapter.py.
#
# Three groups:
#   - the SSRF-equivalent guard, table-driven over connect_safely() itself
#     (not a private helper), matching test_ip_safety.py's own established
#     style -- offline, asyncpg.connect() mocked to detect whether a
#     connection attempt happened at all, mirroring safe_fetch.py's own
#     "a rejected target causes zero network activity" proof style
#     (test_safe_fetch.py).
#   - one hostname-based case reusing test_host_safety.py's own
#     _fake_resolver_class() (cross-test-file import, matching this
#     project's own established precedent -- test_env_consistency.py from
#     test_env_example.py; test_upload_adapter.py from test_upload_sniff.py)
#     -- proves the non-literal-IP branch really calls resolve_and_validate().
#   - live tests against the real test-db service: the credentials
#     round-trip with the new canonical shape, and a real asyncpg
#     connection succeeding against a real safe target (the guard bypassed
#     for 127.0.0.1 via the same established fake_is_unsafe_except_loopback()
#     pattern test_safe_fetch.py/test_web_adapter.py already use).
import uuid
from urllib.parse import urlsplit

import asyncpg
import dns.asyncresolver
import pytest

from app.ingest import database_adapter as database_adapter_module
from app.ingest.database_adapter import UnsafeDatabaseHostError, connect_safely
from app.tenancy.models import Tenant
from tests.conftest import (
    assert_db_connection_credential_round_trip,
    db_session,
    fake_is_unsafe_except_loopback,
    require_test_database,
)
from tests.test_host_safety import _fake_resolver_class

_FAKE_CREDENTIALS = {
    "database": "irrelevant",
    "user": "irrelevant",
    "password": "irrelevant",  # noqa: S105 (test fixture value, not a real secret)
}


class _ConnectRecorder:
    """Stands in for asyncpg.connect() -- records whether it was ever
    called, and with what host, without making any real network
    connection. Matches test_safe_fetch.py's own "prove zero network
    activity for a rejected target" style, adapted to a plain function
    mock rather than a socket-level assertion (asyncpg.connect() is this
    module's own sole place that could ever open a real connection).
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return "fake-connection"


# --- the guard, table-driven over connect_safely() itself ----------------


@pytest.mark.parametrize(
    "unsafe_host",
    [
        "10.0.0.1",  # RFC 1918 private
        "127.0.0.1",  # loopback
        "169.254.169.254",  # cloud-metadata / link-local
        "224.0.0.1",  # multicast
        "240.0.0.1",  # IANA reserved-for-future-use
        "::1",  # IPv6 loopback
    ],
)
async def test_an_unsafe_literal_ip_is_rejected_with_zero_connection_attempts(
    monkeypatch, unsafe_host
):
    recorder = _ConnectRecorder()
    monkeypatch.setattr(asyncpg, "connect", recorder)

    with pytest.raises(UnsafeDatabaseHostError):
        await connect_safely(unsafe_host, _FAKE_CREDENTIALS)

    assert recorder.calls == []  # the guard ran BEFORE any connection attempt


async def test_a_safe_literal_ip_reaches_the_real_connect_call(monkeypatch):
    # The inverse proof: the guard doesn't reject everything. 8.8.8.8 is
    # globally routable, not multicast/reserved/unspecified -- a genuinely
    # safe address by is_unsafe_destination_ip()'s own allow-list design.
    recorder = _ConnectRecorder()
    monkeypatch.setattr(asyncpg, "connect", recorder)

    result = await connect_safely("8.8.8.8", _FAKE_CREDENTIALS)

    assert result == "fake-connection"
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["host"] == "8.8.8.8"  # pinned to the validated IP literal


async def test_credentials_are_forwarded_with_the_documented_defaults(monkeypatch):
    recorder = _ConnectRecorder()
    monkeypatch.setattr(asyncpg, "connect", recorder)

    await connect_safely(
        "8.8.8.8",
        {"database": "mydb", "user": "myuser", "password": "mypw"},  # noqa: S106
    )

    call = recorder.calls[0]
    assert call["database"] == "mydb"
    assert call["user"] == "myuser"
    assert call["password"] == "mypw"  # noqa: S105 (test fixture value, not a real secret)
    assert call["port"] == 5432  # documented default, absent from the dict above
    assert call["ssl"] == "prefer"  # documented default, absent from the dict above


async def test_explicit_port_and_sslmode_override_the_defaults(monkeypatch):
    recorder = _ConnectRecorder()
    monkeypatch.setattr(asyncpg, "connect", recorder)

    await connect_safely(
        "8.8.8.8",
        {**_FAKE_CREDENTIALS, "port": "5433", "sslmode": "require"},
    )

    call = recorder.calls[0]
    assert call["port"] == 5433
    assert call["ssl"] == "require"


# --- the hostname branch: proves resolve_and_validate() is really called --


async def test_a_hostname_resolving_only_to_unsafe_ips_is_rejected(monkeypatch):
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": ["10.0.0.5"], "AAAA": None}),
    )
    recorder = _ConnectRecorder()
    monkeypatch.setattr(asyncpg, "connect", recorder)

    with pytest.raises(UnsafeDatabaseHostError):
        await connect_safely("evil-looking-db-host.example", _FAKE_CREDENTIALS)

    assert recorder.calls == []


async def test_a_hostname_resolving_to_a_safe_ip_reaches_the_real_connect_call(monkeypatch):
    monkeypatch.setattr(
        dns.asyncresolver,
        "Resolver",
        _fake_resolver_class({"A": ["8.8.8.8"], "AAAA": None}),
    )
    recorder = _ConnectRecorder()
    monkeypatch.setattr(asyncpg, "connect", recorder)

    result = await connect_safely("safe-looking-db-host.example", _FAKE_CREDENTIALS)

    assert result == "fake-connection"
    assert recorder.calls[0]["host"] == "8.8.8.8"


# --- live: the canonical credentials shape round-trips for real ----------


async def test_the_canonical_credentials_shape_round_trips_through_real_encryption(
    reset_test_database,
):
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="DB Adapter Proof Tenant", status="active"))
        await session.commit()

    credentials = {
        "database": "tenant_app_db",
        "user": "samoflow_reader",
        "password": "a-real-looking-tenant-password-123",  # noqa: S105
        "port": "5432",
        "sslmode": "verify-full",
    }
    await assert_db_connection_credential_round_trip(
        tenant_id, credentials, host="tenant-postgres.example.internal"
    )


# --- live: a real asyncpg connection, through the real guard -------------


async def test_a_real_connection_succeeds_against_the_real_test_db_service(monkeypatch):
    # The guard correctly rejects 127.0.0.1 by design (it's loopback) --
    # bypassed FOR TEST PURPOSES ONLY via the same already-established
    # fake_is_unsafe_except_loopback() pattern safe_fetch.py's own tests
    # use, so a real local Postgres can stand in for a real safe target.
    # Patched on database_adapter_module directly (where the name is
    # looked up), matching test_safe_fetch.py's own established convention.
    monkeypatch.setattr(
        database_adapter_module, "is_unsafe_destination_ip", fake_is_unsafe_except_loopback
    )

    url = require_test_database()
    parts = urlsplit(url)
    credentials = {
        "database": parts.path.lstrip("/"),
        "user": parts.username,
        "password": parts.password,
        "port": str(parts.port),
        "sslmode": "disable",  # test-db runs plain TCP, no TLS configured at all
    }

    conn = await connect_safely(parts.hostname, credentials)
    try:
        # A real round trip against the real service -- not merely that a
        # connection object was returned.
        assert await conn.fetchval("SELECT 1") == 1
    finally:
        await conn.close()


async def test_connect_safely_never_attempts_a_connection_for_an_unsafe_host_even_with_real_asyncpg(
    monkeypatch,
):
    # The mirror image of the test above, with asyncpg genuinely
    # unpatched: loopback is rejected by the real, unmodified guard, so
    # this must raise before asyncpg.connect() is ever reached -- proven
    # by using a port nothing listens on, which would otherwise surface as
    # a connection-refused error, not UnsafeDatabaseHostError, if the guard
    # were ever bypassed.
    with pytest.raises(UnsafeDatabaseHostError):
        await connect_safely(
            "127.0.0.1",
            {**_FAKE_CREDENTIALS, "port": "1"},
        )
