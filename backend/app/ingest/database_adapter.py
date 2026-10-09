# backend/app/ingest/database_adapter.py
# Task 2.8.a [SECURITY]: the database-sync adapter's own first real code --
# the credentials shape (documented at app/ingest/repository.py's own
# create_db_connection(), the canonical reference, not restated here) and
# the DB-connection equivalent of the SSRF guard (docs/SPEC.md §5.6's "same
# host checks as the crawler"), plus the Postgres async driver this guard's
# own real connection attempt needs.
#
# Postgres only -- a real, confirmed scope decision, not an oversight.
# `db_connections` has no `engine` column and no CheckConstraint naming any
# engine at all today (confirmed live by reading app/ingest/models.py and
# grepping alembic/versions/ -- the table was built schema-only at 2.1.a,
# deliberately leaving the credential's own internal shape, including
# whether "engine" is even a concept, for whoever builds the real adapter).
# There is therefore nothing to "confirm still permits adding MySQL later
# without a migration" the way the task anticipated it might need to be --
# the premise doesn't hold: an `engine` distinction, if one is ever needed,
# would most likely be a new top-level column (mirroring `host`'s own
# precedent of staying outside the opaque encrypted blob, not a value
# inside it), decided by whoever actually builds that second engine, not
# guessed at here. MySQL/MariaDB support is explicitly deferred to a later
# step: no driver dependency, no engine-branching code, no `engine` field
# anywhere in this module.
#
# The SSRF-equivalent guard: adapted from safe_fetch.py's own
# _validate_and_pick_ip(), not duplicated wholesale -- that function takes
# an httpx.URL and extracts `.host` from it; this one takes a plain
# `host: str` from the start (db_connections.host, already a bare string,
# never a URL). The actual classification logic (try as a literal IP via
# ipaddress.ip_address(); if that fails, treat it as a hostname and call
# host_safety.py's resolve_and_validate(), 2.3.b -- already explicitly
# built anticipating exactly this non-HTTP reuse, confirmed by reading that
# module's own header comment, which names Step 2.8 by number) is
# genuinely the same shape as safe_fetch.py's own, now a second occurrence
# of it -- flagged here as a duplication candidate for the next check
# (matching this project's own "flag, don't fix mid-task" discipline), not
# extracted now: doing so would mean editing safe_fetch.py, a completed
# Step 2.3 task's file, which this task's own scope does not cover.
#
# Pinning, investigated live before deciding, not assumed to work the same
# way httpx's does: safe_fetch.py pins a connection to a validated IP
# LITERAL while separately preserving the original hostname for TLS SNI/
# certificate verification, via httpx/httpcore's own
# extensions={"sni_hostname": ...} mechanism. Reading the installed
# asyncpg==0.32.0's own connect_utils.py (_create_ssl_connection()) shows
# it has NO equivalent: `server_hostname` is always set to the SAME value
# passed as `host=` to asyncpg.connect() -- there is no separate parameter
# to say "connect here, but verify the certificate against a DIFFERENT
# name." connect_safely() below therefore pins by passing the validated IP
# literal as `host=` directly (closing the actual SSRF/DNS-rebinding risk
# this guard exists for -- the connection can only ever reach an address
# already proven safe), but this means `sslmode=verify-full` (hostname-
# checking certificate verification) would, once TLS is actually wired up
# functionally, verify the certificate against the IP literal, not the
# tenant's real configured hostname -- a real, narrower residual limitation
# of this driver, recorded as a new Open marker (see PROJECT_SPEC.md),
# **distinct from the SSRF guard itself, which is unaffected**: the
# connection can still never reach an unvalidated destination; only
# certificate-identity verification against a hostname (not an IP) would be
# weakened once verify-full is genuinely exercised.
import ipaddress

import asyncpg

from app.ingest.host_safety import resolve_and_validate
from app.ingest.ip_safety import is_unsafe_destination_ip


class UnsafeDatabaseHostError(Exception):
    """Raised by connect_safely() when `host` has no safe IP to connect to
    -- an unresolvable hostname, a hostname resolving only to unsafe IPs,
    or a literal IP that is itself unsafe (loopback, private, link-local/
    metadata, reserved, multicast). One exception for every reason, not one
    per cause -- matching UnsafeFetchError's own no-oracle precedent
    (safe_fetch.py, 2.3.c): a caller has no legitimate need to distinguish
    WHY a host was rejected, only that it was.
    """


async def _validate_and_pick_ip(
    host: str,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """The DB-connection equivalent of safe_fetch.py's own
    _validate_and_pick_ip() -- see this module's own header comment for
    why it is a separate, adapted function rather than a shared import.
    """
    try:
        literal_ip = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        # No DNS lookup at all for an already-literal IP -- matching
        # safe_fetch.py's own identical reasoning: resolve_and_validate()
        # exists to resolve a HOSTNAME, and handing it a string that is
        # already an address would issue a real, pointless DNS query.
        safe_ips = [] if is_unsafe_destination_ip(literal_ip) else [literal_ip]
    else:
        safe_ips = await resolve_and_validate(host)

    if not safe_ips:
        raise UnsafeDatabaseHostError(f"no safe IP address to connect to for {host!r}")
    return safe_ips[0]


async def connect_safely(
    host: str,
    credentials: dict[str, str],
    *,
    timeout_seconds: float = 15.0,
) -> asyncpg.Connection:
    """Validates `host` (see this module's own header comment for the
    pinning mechanism and its one known limitation against verify-full)
    BEFORE ever attempting a real connection, raising UnsafeDatabaseHostError
    with zero network activity of any kind if it has no safe IP. On a safe
    host, connects for real via asyncpg, pinned to the validated IP --
    `credentials` is the shape documented at create_db_connection()'s own
    docstring (app/ingest/repository.py), the canonical reference, not
    restated here.

    Returns a live, open asyncpg.Connection -- closing it is the caller's
    own responsibility (matching this project's own "smallest correct
    mechanism" precedent: a context-manager wrapper has no real caller yet
    to justify it, 2.8.b's own read-only probe is the first one). Any
    connection-level failure (wrong password, database does not exist,
    connection refused) propagates completely raw and untranslated --
    matching extract_pdf.py's/embed_dense()'s own "no redundant translation
    layer" precedent: this primitive adds exactly one thing (the SSRF
    guard) and gets out of the way for everything else.
    """
    pinned_ip = await _validate_and_pick_ip(host)
    return await asyncpg.connect(
        host=str(pinned_ip),
        port=int(credentials.get("port", "5432")),
        user=credentials["user"],
        password=credentials["password"],
        database=credentials["database"],
        ssl=credentials.get("sslmode", "prefer"),
        timeout=timeout_seconds,
    )
