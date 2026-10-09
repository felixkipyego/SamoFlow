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
import asyncpg.cursor
import dns.asyncresolver
import pytest

from app.ingest import database_adapter as database_adapter_module
from app.ingest.database_adapter import (
    InvalidQueryError,
    UnsafeDatabaseHostError,
    WritableConnectionError,
    connect_safely,
    ensure_read_only,
    fetch_readonly_rows,
)
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


# --- Task 2.8.b: read-only enforcement, statement timeout, row cap, and --
# --- the one-query guard. All live against the real test-db service. ----


async def _admin_connection() -> asyncpg.Connection:
    url = require_test_database()
    parts = urlsplit(url)
    return await asyncpg.connect(
        host=parts.hostname,
        port=parts.port,
        user=parts.username,
        password=parts.password,
        database=parts.path.lstrip("/"),
    )


@pytest.fixture
async def readonly_and_writable_roles():
    # Two real roles on the real test-db service, confirmed live before
    # choosing this exact grant shape (see this task's own PROJECT_SPEC.md
    # decision-log entry): `readonly` gets CONNECT + USAGE on the public
    # schema only (no CREATE) -- a role that genuinely cannot write;
    # `writable` additionally gets CREATE on the public schema -- a role
    # that genuinely can. Random suffix (not a fixed name): safe even if a
    # prior run's own teardown was ever interrupted mid-way.
    suffix = uuid.uuid4().hex[:8]
    readonly_role = f"probe_ro_{suffix}"
    writable_role = f"probe_rw_{suffix}"
    # The database name grants are issued against, read from the admin
    # connection's own real target (not hardcoded), so this fixture works
    # regardless of which "_test"-named database TEST_DATABASE_URL points
    # at.
    database_name = urlsplit(require_test_database()).path.lstrip("/")

    admin = await _admin_connection()
    try:
        await admin.execute(f"CREATE ROLE {readonly_role} LOGIN PASSWORD 'ro-pw'")
        await admin.execute(f"CREATE ROLE {writable_role} LOGIN PASSWORD 'rw-pw'")
        await admin.execute(
            f'GRANT CONNECT ON DATABASE "{database_name}" TO {readonly_role}, {writable_role}'
        )
        await admin.execute(f"GRANT USAGE ON SCHEMA public TO {readonly_role}, {writable_role}")
        await admin.execute(f"GRANT CREATE ON SCHEMA public TO {writable_role}")

        yield {
            "readonly": {"user": readonly_role, "password": "ro-pw"},  # noqa: S106
            "writable": {"user": writable_role, "password": "rw-pw"},  # noqa: S106
        }
    finally:
        await admin.execute(f"REVOKE ALL ON SCHEMA public FROM {readonly_role}, {writable_role}")
        await admin.execute(
            f'REVOKE ALL ON DATABASE "{database_name}" FROM {readonly_role}, {writable_role}'
        )
        await admin.execute(f"DROP ROLE {readonly_role}")
        await admin.execute(f"DROP ROLE {writable_role}")
        await admin.close()


async def _connect_as(role: dict) -> asyncpg.Connection:
    url = require_test_database()
    parts = urlsplit(url)
    return await asyncpg.connect(
        host=parts.hostname,
        port=parts.port,
        user=role["user"],
        password=role["password"],
        database=parts.path.lstrip("/"),
    )


# --- (1) read-only enforcement -------------------------------------------


async def test_ensure_read_only_passes_against_a_real_read_only_role(readonly_and_writable_roles):
    conn = await _connect_as(readonly_and_writable_roles["readonly"])
    try:
        await ensure_read_only(conn)  # must not raise
    finally:
        await conn.close()


async def test_ensure_read_only_raises_against_a_real_writable_role(readonly_and_writable_roles):
    conn = await _connect_as(readonly_and_writable_roles["writable"])
    try:
        with pytest.raises(WritableConnectionError):
            await ensure_read_only(conn)
    finally:
        await conn.close()


async def test_ensure_read_only_leaves_no_trace_either_way(readonly_and_writable_roles):
    # The probe's own rollback discipline, confirmed live against both
    # roles in one test: regardless of outcome, nothing it creates ever
    # persists.
    for role in readonly_and_writable_roles.values():
        conn = await _connect_as(role)
        try:
            try:
                await ensure_read_only(conn)
            except WritableConnectionError:
                pass
        finally:
            await conn.close()

    admin = await _admin_connection()
    try:
        leftover = await admin.fetch(
            "SELECT tablename FROM pg_tables WHERE tablename LIKE '_samoflow_write_probe_%'"
        )
        assert leftover == []
    finally:
        await admin.close()


async def test_fetch_readonly_rows_rejects_a_writable_connection_before_running_the_query(
    readonly_and_writable_roles, monkeypatch
):
    # The documented policy from point 1: a writable connection is
    # rejected OUTRIGHT, and the real query is never even attempted --
    # proven by making the query itself something that would raise if
    # ever actually executed (a reference to a table that does not
    # exist), confirming ensure_read_only()'s own rejection happens first.
    conn = await _connect_as(readonly_and_writable_roles["writable"])
    try:
        with pytest.raises(WritableConnectionError):
            await fetch_readonly_rows(
                conn,
                "SELECT * FROM this_table_does_not_exist_at_all",
                row_cap=10,
                timeout_seconds=5.0,
            )
    finally:
        await conn.close()


# --- (2) statement timeout and row cap, enforced via the real connection -


async def test_statement_timeout_actually_fires_against_a_real_slow_query(
    readonly_and_writable_roles,
):
    # The real role (not the admin superuser): ensure_read_only()'s own
    # probe must pass first, exactly like a real sync job's own connection
    # would, before this test's own real subject (the timeout) is reached.
    conn = await _connect_as(readonly_and_writable_roles["readonly"])
    try:
        with pytest.raises(asyncpg.PostgresError):
            await fetch_readonly_rows(
                conn,
                "SELECT pg_sleep(5)",
                row_cap=10,
                timeout_seconds=0.5,
            )
    finally:
        await conn.close()


async def test_row_cap_limits_real_rows_via_the_cursor_not_a_post_fetch_slice(
    readonly_and_writable_roles, monkeypatch
):
    conn = await _connect_as(readonly_and_writable_roles["readonly"])
    try:
        await conn.execute("CREATE TEMPORARY TABLE many_rows (id int)")
        await conn.executemany(
            "INSERT INTO many_rows (id) VALUES ($1)", [(i,) for i in range(50)]
        )

        fetch_calls: list[int] = []
        original_fetch = asyncpg.cursor.Cursor.fetch

        async def _tracking_fetch(self, n):
            fetch_calls.append(n)
            return await original_fetch(self, n)

        monkeypatch.setattr(asyncpg.cursor.Cursor, "fetch", _tracking_fetch)

        rows = await fetch_readonly_rows(
            conn,
            "SELECT id FROM many_rows ORDER BY id",
            row_cap=5,
            timeout_seconds=5.0,
        )

        assert [r["id"] for r in rows] == [0, 1, 2, 3, 4]
        # The decisive structural proof: the server-side cursor was asked
        # for EXACTLY the cap, once -- by this mechanism's own documented
        # semantics, the server never sent more than 5 rows back over the
        # wire in the first place, so there was nothing to "pull fully
        # then truncate".
        assert fetch_calls == [5]
    finally:
        await conn.close()


# --- (3) the one-query guard ----------------------------------------------


async def test_the_one_query_guard_rejects_a_stacked_second_statement():
    conn = await _admin_connection()
    try:
        with pytest.raises(InvalidQueryError):
            await fetch_readonly_rows(
                conn,
                "SELECT 1; DROP TABLE this_must_never_run",
                row_cap=10,
                timeout_seconds=5.0,
            )
    finally:
        await conn.close()


async def test_the_one_query_guard_rejects_a_non_select_statement():
    conn = await _admin_connection()
    try:
        with pytest.raises(InvalidQueryError):
            await fetch_readonly_rows(
                conn,
                "UPDATE pg_settings SET setting = setting",
                row_cap=10,
                timeout_seconds=5.0,
            )
    finally:
        await conn.close()


async def test_the_one_query_guard_rejects_a_writable_cte():
    # The specific smuggling shape named in this module's own header
    # comment: a single statement, no semicolon anywhere, that still
    # performs a real write via a data-modifying CTE.
    conn = await _admin_connection()
    try:
        with pytest.raises(InvalidQueryError):
            await fetch_readonly_rows(
                conn,
                "WITH x AS (DELETE FROM pg_settings RETURNING 1) SELECT * FROM x",
                row_cap=10,
                timeout_seconds=5.0,
            )
    finally:
        await conn.close()


async def test_a_genuine_single_select_statement_is_accepted(readonly_and_writable_roles):
    conn = await _connect_as(readonly_and_writable_roles["readonly"])
    try:
        rows = await fetch_readonly_rows(
            conn, "SELECT 1 AS one;", row_cap=10, timeout_seconds=5.0
        )
        assert [dict(r) for r in rows] == [{"one": 1}]
    finally:
        await conn.close()
