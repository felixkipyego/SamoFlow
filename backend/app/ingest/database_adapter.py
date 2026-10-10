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
#
# Task 2.8.d: the per-row ingest primitive, ingest_db_row() -- the third
# real caller of finish_ingest() (app/ingest/qdrant_writer.py), per the
# Step 2.7 canonical-contract precedent, following ingest_url()'s/ingest_
# upload()'s own established shape exactly: render the row to text (2.8.c's
# own render_row_to_text()), derive its stable identity (2.8.c's own
# derive_row_identity(), added at this same task), call finish_ingest()
# with that identity and content. `_SOURCE_TYPE = "database"` hardcoded,
# not a parameter -- matching upload_adapter.py's own identical reasoning
# (`ingest_db_row()` has exactly one real caller and one possible value;
# a parameter with only one ever-passed value is a parameter the task
# does not need, rule 11). EMBEDDING_VERSION imported from web_adapter.py
# directly, not redeclared -- matching upload_adapter.py's own identical
# precedent (one project-wide embedding-version constant, not a per-
# adapter copy).
#
# finish_ingest() itself WIDENED at this same task (a real, necessary,
# flagged consequence, not assumed away): it previously accepted only
# `url`/`file_name` as identity dimensions, with NO way to pass a third
# kind of identity through its own hardcoded two-column lookup and
# Document(...) construction -- confirmed by reading its actual code
# before building this, not assumed reusable as-is just because the
# task's own plan described it that way. Resolved by adding a THIRD
# keyword-only parameter, `row_identity: str | None = None` -- the
# default means `ingest_url()`/`ingest_upload()` (2.6.c/2.7.c, both
# completed tasks) needed zero changes of their own and keep their exact
# prior behavior, byte for byte; only this function, the new third real
# caller, ever passes a real value. See qdrant_writer.py's own updated
# docstring for the full widening.
#
# ExtractedContent wrapping: render_row_to_text() (2.8.c) returns a plain
# string, matching that task's own literal instruction ("the rendered
# text string finish_ingest() expects as content") -- but finish_ingest()
# actually takes `content: ExtractedContent` (app/ingest/extract_html.py),
# never a bare string, confirmed by reading its real signature rather
# than assumed from the task's own description. Resolved here, not by
# retroactively changing render_row_to_text()'s own return type (2.8.c is
# a completed task; its own contract, "return the rendered string," is
# correct and unchanged) -- _text_to_extracted_content() below wraps the
# string into the flat, structureless ExtractedContent shape extract_
# text.py's own extract_text() already established for exactly this
# "no heading structure at all" case (one ContentBlock, no heading_path,
# `title=None`, `word_count=len(text.split())`), reused rather than
# reinvented.
#
# `is_nearly_empty`, inherited for free from that same reuse, confirmed
# live and flagged rather than silently accepted: NEARLY_EMPTY_WORD_
# THRESHOLD (extract_html.py) is 50 words, and most real row-to-text
# templates (short, structured sentences) will render well under that --
# meaning `Document.is_nearly_empty` will likely be `True` for the
# common case here, unlike a web page (where it flags a genuine anomaly).
# The underlying boolean MECHANISM is still correctly reused (rule 11 --
# don't invent a second one); only its DASHBOARD-FACING interpretation
# ("may need JavaScript" makes no sense for a product row) would need
# its own per-source-type wording, Phase 6's own job, not touched here.
#
# Task 2.8.e [SECURITY]: the real query-builder row_templates.py's own
# header comment explicitly left unbuilt ("not yet wired... 2.8.d's own
# job" -- actually neither 2.8.c nor 2.8.d built it; this is where it
# lands) -- build_table_select_query(), TableNotAllowlistedError,
# InvalidIdentifierError. Composes is_table_allowlisted() (2.8.c, the
# FOURTH defense layer that module's own header comment named at the time
# -- now the FIFTH, since the strict allow-list identifier check directly
# below is itself a new, independent layer this same task adds; row_
# templates.py's own header comment was updated at the duplication check
# after 2.8.d/e/f to count both, not left to read as a contradiction) with
# a strict allow-list identifier check (schema/table/column names must
# match `^[A-Za-z_][A-Za-z0-9_]*$`) before ever interpolating any
# configured name into a SQL string -- asyncpg has no bind-parameter
# mechanism for identifiers (only for values), so this is the standard,
# well-known mitigation for that specific gap (rule 11), not a custom
# scheme. See app/ingest/job_handlers.py's own header comment for the
# real caller (handle_ingest_db()) and the full per-table/per-row
# partial-failure policy built around this.
import ipaddress
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
from qdrant_client import AsyncQdrantClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DateTimeClock
from app.ingest.extract_html import ContentBlock, ExtractedContent
from app.ingest.host_safety import resolve_and_validate
from app.ingest.ip_safety import is_unsafe_destination_ip
from app.ingest.qdrant_writer import finish_ingest
from app.ingest.row_templates import (
    derive_row_identity,
    is_table_allowlisted,
    render_row_to_text,
)
from app.ingest.web_adapter import EMBEDDING_VERSION


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


class TableNotAllowlistedError(Exception):
    """Raised by build_table_select_query() when `table` is not in this
    connection's own `allowlisted_tables` (app/ingest/row_templates.py's
    `is_table_allowlisted()`) -- e.g. a `row_templates` entry whose own
    table was since removed from `allowlisted_tables` (the two are
    independently-editable JSONB columns on the same `db_connections` row,
    Task 2.1.a, with nothing tying them together at the schema level, so
    this drift is a real, reachable state, not a hypothetical one). Raised
    rather than silently skipped or silently synced -- matching
    WritableConnectionError's/UnsafeDatabaseHostError's own established
    "a security-relevant rejection is definitive and loud" precedent
    (rule 11): a stale/removed table must never be queried just because a
    template for it still happens to exist.
    """


class InvalidIdentifierError(Exception):
    """Raised by build_table_select_query() when `table` is not exactly
    two dot-separated parts, or any schema/table/column name fails the
    strict `^[A-Za-z_][A-Za-z0-9_]*$` allow-list check -- an admin-
    configured JSONB value, but still treated as untrusted input for SQL-
    identifier purposes (rule 11/engineering rule 30: treat all ingested
    configuration as untrusted), since asyncpg (like every SQL driver) has
    no bind-parameter mechanism for identifiers, only for values. Matches
    ip_safety.py's own allow-list-not-deny-list philosophy: reject
    anything that isn't a plain, ordinary identifier, rather than trying
    to enumerate and escape every dangerous character.
    """


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(identifier: str) -> str:
    if not _SAFE_IDENTIFIER.match(identifier):
        raise InvalidIdentifierError(f"not a safe SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def build_table_select_query(
    allowlisted_tables: Mapping[str, Any], table: str, template_config: Mapping[str, Any]
) -> str:
    """Builds the single, already-fully-formed SELECT string `fetch_
    readonly_rows()`'s own one-query guard will accept -- the real query-
    builder row_templates.py's own header comment names as "not yet
    wired" and explicitly assigns to whoever builds the real job handler
    (Task 2.8.e). Requests exactly `template_config["columns"]` plus
    `template_config["primary_key"]` (deduplicated, sorted for a
    deterministic column order) -- the least-privilege column list
    render_row_to_text()/derive_row_identity() both need, never
    `SELECT *`.

    Calls is_table_allowlisted() FIRST, before ever touching identifier
    construction -- one of FIVE independent defense layers this module and
    row_templates.py together provide (alongside fetch_readonly_rows()'s
    own textual one-query guard, ensure_read_only()'s write probe, and the
    real `transaction(readonly=True)` wrapping -- the fifth being the
    identifier allow-list check immediately below, in this same function),
    raising TableNotAllowlistedError if `table` is not currently
    allowlisted. Every identifier (both halves of the schema-qualified
    `table`, and every column name) is then validated via the strict
    allow-list regex and double-quoted, raising InvalidIdentifierError for
    anything that does not look like a plain, ordinary SQL identifier.
    """
    if not is_table_allowlisted(allowlisted_tables, table):
        raise TableNotAllowlistedError(
            f"table {table!r} is not in this connection's own allowlisted_tables "
            "-- refusing to build a query against it"
        )
    schema_part, dot, table_part = table.partition(".")
    if not dot:
        raise InvalidIdentifierError(
            f"table must be schema-qualified as 'schema.table', got {table!r}"
        )
    qualified_table = f"{_quote_identifier(schema_part)}.{_quote_identifier(table_part)}"

    columns = sorted({*template_config["columns"], template_config["primary_key"]})
    select_list = ", ".join(_quote_identifier(column) for column in columns)
    # noqa justified: every identifier above has already been validated by
    # _quote_identifier() (a strict allow-list regex, raising
    # InvalidIdentifierError for anything else) and is_table_allowlisted()
    # -- there is no tenant/request-time value interpolated here, only
    # already-checked, double-quoted identifiers, matching ensure_read_
    # only()'s own identical noqa precedent above.
    return f"SELECT {select_list} FROM {qualified_table}"  # noqa: S608


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


_SOURCE_TYPE = "database"


@dataclass(frozen=True)
class DbRowIngestResult:
    """An equivalent of web_adapter.py's own IngestResult/upload_adapter.py's
    own UploadIngestResult (2.7.b's own "genuinely distinct concerns stay
    separate" precedent, re-confirmed here, not reused directly) -- but
    deliberately only TWO states, not four: `"ingested"`/`"unchanged"`,
    matching finish_ingest()'s own exact, non-nullable return contract.
    Neither `"skipped"` nor `"failed"` applies here. Clarified at the
    duplication check after 2.8.d/e/f (the original wording below read as
    though rendering happens before this function is ever called, which
    is not what the code does -- ingest_db_row() calls row_templates.py's
    own render_row_to_text()/derive_row_identity() ITSELF, internally, as
    its own first two steps): by the time ingest_db_row() is called, the
    row has already been fetched through 2.8.b's own full defense-in-depth
    chain -- but rendering/identity-derivation happens INSIDE this very
    function, via row_templates.py's renderer. A genuine problem there (a
    bad query exposing a stale table shape, a stale template) is a
    configuration problem that propagates UNCAUGHT from this function
    (matching row_templates.py's own "surface loudly" philosophy for
    MissingColumnError/MissingPrimaryKeyError/TemplateRenderError), not a
    per-row outcome this result type needs to represent.
    """

    status: str
    document_id: uuid.UUID


def _text_to_extracted_content(text: str) -> ExtractedContent:
    """Wraps a rendered row's own plain string (render_row_to_text(),
    2.8.c) into the flat, structureless ExtractedContent shape finish_
    ingest() actually requires -- see this module's own header comment
    for why this wrapping lives here, not inside render_row_to_text()
    itself. Mirrors extract_text.py's own extract_text() exactly (the
    identical "no heading structure at all" case): one ContentBlock (none
    if the rendered text is empty), no title, word_count over the
    stripped text.
    """
    stripped = text.strip()
    blocks = (ContentBlock(heading_path=(), text=stripped),) if stripped else ()
    return ExtractedContent(title=None, blocks=blocks, word_count=len(stripped.split()))


async def ingest_db_row(
    session: AsyncSession,
    client: AsyncQdrantClient,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    table: str,
    row: Mapping[str, Any],
    template_config: Mapping[str, Any],
    clock: DateTimeClock = lambda: datetime.now(UTC),
) -> DbRowIngestResult:
    """Renders one already-fetched database row into its own `documents`
    row, creating or updating it via finish_ingest() -- the third real
    caller of that shared tail (web_adapter.py's ingest_url(), upload_
    adapter.py's ingest_upload(), both 2.6.c/2.7.c), per the Step 2.7
    canonical-contract precedent. See this module's own header comment
    for the full design, including the two real, necessary widenings this
    task made to already-completed code (finish_ingest()'s own new
    `row_identity` parameter; the ExtractedContent-wrapping step) and why
    each was necessary rather than assumed away.

    `row`/`table`/`template_config` are taken exactly as already given --
    this function has no idea how `row` was fetched, whether `table` was
    ever checked against `is_table_allowlisted()`, or whether `template_
    config` is really `row_templates[table]` -- all of that is the real,
    not-yet-built caller's job (2.8.e's own job handler). This primitive
    only ever does two things with its three inputs: derive the row's own
    stable identity (row_templates.py's derive_row_identity(), added at
    this same task) and render it to text (row_templates.py's render_row_
    to_text(), 2.8.c) -- then hands both to finish_ingest() exactly like
    ingest_url()/ingest_upload() hand it a url/file_name and an already-
    extracted ExtractedContent.
    """
    row_identity = derive_row_identity(row, table, template_config)
    content = _text_to_extracted_content(render_row_to_text(row, template_config))

    status, document_id = await finish_ingest(
        session,
        client,
        collection_name,
        content,
        tenant_id=tenant_id,
        source_id=source_id,
        source_type=_SOURCE_TYPE,
        url=None,
        file_name=None,
        row_identity=row_identity,
        embedding_version=EMBEDDING_VERSION,
        clock=clock,
    )
    return DbRowIngestResult(status=status, document_id=document_id)
