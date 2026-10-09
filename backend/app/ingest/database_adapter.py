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
#
# Task 2.8.b [SECURITY]: everything from "connection is open" through
# "confirmed safe to run one read-only query" -- ensure_read_only(),
# fetch_readonly_rows(), and the two guards that back them. Row templates,
# allowlisted_tables and the job handler are NOT built here (2.8.c/2.8.d/
# 2.8.e's own jobs) -- see this header comment's own closing paragraph for
# exactly where this task's boundary ends.
#
# Read-only enforcement: a GENERIC rolled-back-transaction write probe, not
# per-engine privilege introspection (confirmed decision, matching our
# earlier one) -- declared grants/roles can lie (a role's own GRANTs don't
# prove what Postgres will actually ENFORCE at execution time, and a
# per-engine privilege query is the one piece of this module that would
# have to branch by engine, defeating the whole point of staying
# engine-agnostic for whenever MySQL arrives). ensure_read_only() instead
# attempts a REAL write -- CREATE TABLE + INSERT, inside a transaction
# that is ALWAYS rolled back, confirmed live with two real roles on the
# real test-db service before choosing this exact shape (see this task's
# own PROJECT_SPEC.md decision-log entry for the full before/after). Two
# shapes considered and rejected first, each for a real, confirmed reason:
# a bare CREATE TEMPORARY TABLE probe was rejected because Postgres grants
# CREATE TEMP to PUBLIC by default -- almost every role, including a
# textbook read-only one, can create temp tables, so this would nearly
# always report "writable" (a false positive on the common case, making
# the probe useless); a no-op UPDATE against a system catalog table (e.g.
# pg_catalog.pg_class) was rejected because catalog writes are blocked for
# every non-superuser role regardless of actual data-write grants -- this
# would nearly always report "read-only" (a false negative, equally
# useless). A real, ordinary user table (CREATE TABLE, not TEMPORARY, in
# the connected database's own default schema) sidesteps both: CREATE on
# a schema is NOT granted to PUBLIC by default since Postgres 15 (the
# version this project targets, `postgres:18.2-trixie`), so a role that
# can do this genuinely has elevated privilege a read-only account
# should not have.
#
# Policy when the probe finds the connection CAN write -- decided here,
# not left to the caller, per rule 11 ("security requirements... never
# optional... win over simplicity"): REJECT OUTRIGHT, via
# WritableConnectionError, propagating uncaught -- never "warn and use it
# anyway." Reasoning: this connection exists so OUR OWN code can run a
# tenant-configured sync query against the tenant's own real production
# database; if that connection can ALSO write, any future bug in this
# step's own query-construction logic (2.8.c/d, not yet built) could
# mutate the tenant's own real data, a severe and hard-to-recover failure
# mode docs/SPEC.md's own "read-only credentials" requirement exists
# specifically to prevent. Matches UnsafeFetchError's/
# UnsafeDatabaseHostError's own established "a security-relevant
# rejection is definitive, not advisory" precedent -- docs/SPEC.md's own
# literal wording ("the connection test... warns") is honored as "warns
# the DEVELOPER/operator, via a loud failure," not "warns the tenant and
# proceeds regardless," which would defeat the requirement's whole point.
# ensure_read_only() is called automatically inside fetch_readonly_rows()
# itself (every invocation, not cached) rather than only once "at claim/
# verification time" -- cheap to re-run, and this project's own
# established precedent (reap_stuck_jobs()/write_heartbeat(), run
# redundantly every loop iteration) already favors re-checking a cheap
# safety property every time over trusting a result from the past. It is
# ALSO exported as its own public function so a future one-time
# "test this connection" action (Phase 6 dashboard, or 2.8.e's own job,
# neither built yet) can call it directly without running a real query.
#
# Statement timeout and row cap -- both enforced via the real connection-
# level mechanism, not an application-level race or a post-fetch
# truncation, confirmed live before choosing: `SELECT set_config
# ('statement_timeout', $1, true)` (the THIRD arg, `is_local`, scopes it
# to the current transaction only -- confirmed live that `SHOW
# statement_timeout` reads back to its own default immediately after the
# transaction ends, so this can never leak onto a later call sharing the
# same connection) is Postgres's own real statement-level timeout, fired
# by the SERVER, not an asyncio.wait_for() race that would leave the
# query still running server-side after Python gave up waiting on it.
# Confirmed live: a transaction with a 500ms timeout against `SELECT
# pg_sleep(5)` raised QueryCanceledError after ~0.5s, not ~5s. The row cap
# is enforced via a real server-side CURSOR (`conn.cursor(query)` +
# `cursor.fetch(row_cap)`) -- confirmed live against a real 50-row
# table that `cursor.fetch(5)` returns exactly 5 rows; by this mechanism's
# own documented semantics, the server never sends more than the
# requested count back to the client in the first place, so there is
# nothing to "truncate after a full pull" because no full pull ever
# happens.
#
# Defense in depth, not relying on any single layer: every real query
# additionally runs inside `conn.transaction(readonly=True)` -- Postgres's
# OWN read-only transaction mode, confirmed live to reject a write
# (ReadOnlySQLTransactionError) at the EXECUTOR level, independent of the
# role's own grants. This is deliberately a SECOND, different kind of
# check from ensure_read_only()'s own probe (which asks "does this ROLE
# have write privilege at all") -- this one asks "does POSTGRES ITSELF
# block a write during THIS specific query," which also catches a
# mutating call hidden inside an otherwise-syntactically-plain SELECT
# (e.g. a VOLATILE function with a side effect) that no text-based guard
# below could ever see.
#
# The one-query guard: TWO independent layers, matching the task's own
# "AST-guard-or-allowlist pattern... adapt, don't invent" instruction --
# (1) STRUCTURAL: test_database_adapter_guard.py confirms, by import
# confinement (the same rule-(a) shape test_qdrant_read_path_guard.py
# already established, simplified -- asyncpg has exactly one real module
# to confine, unlike qdrant-client's own broader read/write/config method
# surface, so there is no equivalent to that guard's own rules b/c/d to
# adapt here), that NOTHING under backend/app besides this one file may
# even import asyncpg at all -- if no other file can ever hold a real
# asyncpg connection object, no other file can ever run ANY query through
# one, read or write. (2) TEXTUAL: _ensure_single_select_statement()
# rejects anything not starting with the literal keyword SELECT (no `WITH
# ... AS (INSERT ...)` writable-CTE smuggling a mutation into a single
# statement that never needs a semicolon) and anything containing a
# second statement after an optional single trailing `;` (stacked-query
# smuggling). Deliberately a cheap, best-effort TEXT filter, not a real
# SQL parser -- stated plainly, not hidden: it would incorrectly reject a
# legitimate query with a literal semicolon inside a quoted string (a
# real, accepted false-positive-only limitation, safe because it rejects
# rather than silently admits; recorded as a new Open marker for 2.8.c/d,
# whichever first needs to build a query containing one). This textual
# guard is explicitly the FIRST, cheapest layer, not the only one --
# `conn.transaction(readonly=True)` above is the real backstop for
# anything a text check alone could miss (e.g. a mutating function call
# inside an otherwise-valid SELECT).
#
# Settings: `Settings.db_sync_row_cap`/`db_sync_statement_timeout_seconds`
# (app/config.py) -- a global default, NOT sourced from `plans.limits`,
# the identical already-established reason as `crawl_page_cap`/
# `upload_max_size_bytes` (`plans.limits` is still an unstructured JSONB
# stub, `get_plan_limits()` an explicit NotImplementedError, Task 4.3's
# own job). Read ONCE by a real future caller (2.8.e's own job handler,
# not yet built) and passed down as plain values -- fetch_readonly_rows()
# itself stays Settings-free, matching run_crawl()'s/ingest_upload()'s own
# established "primitive takes plain parameters, the caller reads
# Settings" precedent exactly.
#
# Where this task's own boundary ends: fetch_readonly_rows() runs exactly
# one already-fully-formed SELECT string and returns rows. It does NOT
# know about `allowlisted_tables`, `row_templates`, or how a real query
# string gets built from either (2.8.c's own job) -- the CALLER is
# responsible for constructing a query this guard will actually accept.
# It does NOT create or manage `sources`/`jobs` rows, and it does NOT
# register a `JOB_HANDLERS` entry (2.8.d/2.8.e's own jobs).
import ipaddress
import re
import uuid

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


class WritableConnectionError(Exception):
    """Raised by ensure_read_only() when a connection that was supposed to
    carry read-only credentials can actually write -- see this module's
    own header comment for the full probe design and the "reject
    outright, never warn-and-proceed" policy reasoning.
    """


class InvalidQueryError(Exception):
    """Raised by fetch_readonly_rows() when `query` is not exactly one
    SELECT statement -- see this module's own header comment for why this
    is a cheap, best-effort textual check, not a real SQL parser, and
    `conn.transaction(readonly=True)` is the real backstop for anything it
    cannot see.
    """


async def ensure_read_only(conn: asyncpg.Connection) -> None:
    """Attempts a real write (CREATE TABLE + INSERT, both inside one
    transaction that is ALWAYS rolled back, confirmed live to leave zero
    trace either way) to confirm `conn`'s own credentials are genuinely
    read-only -- see this module's own header comment for why a generic
    write probe, not per-engine privilege introspection, and why this
    exact shape (not a TEMPORARY table, not a system-catalog no-op).
    Raises WritableConnectionError if the full CREATE+INSERT sequence
    succeeds; returns normally (confirmed read-only) if either step
    raises a PostgresError.
    """
    probe_table = f"_samoflow_write_probe_{uuid.uuid4().hex}"
    # Manual start()/rollback(), not `async with conn.transaction():` (the
    # style fetch_readonly_rows() uses below) -- deliberately different,
    # not an inconsistency to "clean up": a bare `async with` block COMMITS
    # on success and only rolls back on an exception, but this probe must
    # roll back UNCONDITIONALLY, success or failure alike, since a
    # successful probe is exactly the case where something was genuinely
    # written and must never be allowed to persist.
    transaction = conn.transaction()
    await transaction.start()
    try:
        # noqa justified: probe_table is server-generated (uuid4().hex,
        # never tenant/caller input), not a tenant-suppliable string --
        # there is nothing here for an injection to ride in on.
        await conn.execute(f'CREATE TABLE "{probe_table}" (probe int)')
        await conn.execute(f'INSERT INTO "{probe_table}" (probe) VALUES (1)')  # noqa: S608
        confirmed_writable = True
    except asyncpg.PostgresError:
        confirmed_writable = False
    finally:
        await transaction.rollback()

    if confirmed_writable:
        raise WritableConnectionError(
            "this connection's own credentials can write (a real CREATE "
            "TABLE + INSERT both succeeded, then rolled back) -- "
            "docs/SPEC.md's own read-only requirement is not actually "
            "enforced by the tenant's database role"
        )


# Deliberately a cheap textual check, not a real SQL parser -- see this
# module's own header comment for the full reasoning, including the one
# accepted false-positive limitation (a literal ';' inside a quoted
# string literal) and why `conn.transaction(readonly=True)` is the real
# backstop for anything this cannot see (e.g. a mutating function call
# inside an otherwise-valid SELECT). Deliberately rejects a leading `WITH`
# too -- a writable CTE (`WITH x AS (INSERT ... RETURNING ...) SELECT ...`)
# can smuggle a real mutation into a single statement with no semicolon
# at all; this module does not need read-only CTE support yet, so the
# simplest, strictly safer rule is to require a literal SELECT with
# nothing else.
_SELECT_PREFIX = re.compile(r"^\s*select\b", re.IGNORECASE)


def _ensure_single_select_statement(query: str) -> None:
    stripped = query.strip()
    if not _SELECT_PREFIX.match(stripped):
        raise InvalidQueryError(
            "query must be a single SELECT statement (no CTEs, no other "
            "statement type)"
        )
    body = stripped[:-1] if stripped.endswith(";") else stripped
    if ";" in body:
        raise InvalidQueryError(
            "query must be exactly one statement -- a second, "
            "semicolon-separated statement is not allowed"
        )


async def fetch_readonly_rows(
    conn: asyncpg.Connection,
    query: str,
    *,
    row_cap: int,
    timeout_seconds: float,
) -> list[asyncpg.Record]:
    """Runs exactly one already-fully-formed, read-only SELECT against
    `conn` and returns at most `row_cap` rows -- the one real entry point
    this task builds; see this module's own header comment for the full
    defense-in-depth design (the one-query guard, ensure_read_only(), the
    real statement_timeout, the real server-side cursor, and the
    read-only transaction wrapping all of it) and for exactly where this
    function's own responsibility ends (it does not know how `query` was
    built, and it is not a job handler).
    """
    _ensure_single_select_statement(query)
    await ensure_read_only(conn)

    timeout_ms = str(int(timeout_seconds * 1000))
    async with conn.transaction(readonly=True):
        await conn.execute("SELECT set_config('statement_timeout', $1, true)", timeout_ms)
        cursor = await conn.cursor(query)
        return await cursor.fetch(row_cap)
