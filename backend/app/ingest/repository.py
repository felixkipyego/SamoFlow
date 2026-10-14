# backend/app/ingest/repository.py
# Task 2.1.a: db_connections' credential accessor. Schema lives in
# app/ingest/models.py; this is the ONE sanctioned path that ever turns an
# encrypted db_connections row back into a usable credential -- mirrors
# app/config.py's own "one explicit call that unwraps the secret, never
# log this" pattern (qdrant_api_key_str(), etc.), translated one layer
# down: that pattern lives on Settings (an in-process secret); this one
# lives on a repository method (a database-stored secret), since SecretStr
# itself is a pydantic BaseModel feature that does not apply to a
# SQLAlchemy column (see PROJECT_SPEC.md's Step 2.1.a decision entry for
# the research behind this).
#
# Encryption: pgcrypto's pgp_sym_encrypt/pgp_sym_decrypt -- verified live
# against the pinned postgres:18.2-trixie image before choosing this over
# application-level encryption (CREATE EXTENSION IF NOT EXISTS pgcrypto
# succeeds with zero new runtime dependencies; a real encrypt/decrypt
# round trip was proven, not assumed). The passphrase
# (Settings.db_connection_encryption_key) is always passed as a bound
# parameter via sqlalchemy.func, never string-interpolated into SQL --
# avoids both SQL injection and the key ending up in a query log if
# log_statement is ever enabled.
#
# Same frozen-dataclass shape as TenantScopedRepository
# (app/tenancy/repository.py): one shared discipline for every
# tenant-scoped repository in this codebase, not a second scheme.
import json
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from qdrant_client import AsyncQdrantClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import DateTimeClock, violated_constraint_name
from app.ingest import upload_storage
from app.ingest.domain_verification import (
    check_dns_verification,
    check_file_verification,
    check_meta_tag_verification,
)
from app.ingest.models import (
    JOB_TYPE_FOR_SOURCE_TYPE,
    REFRESH_INTERVAL_DELTAS,
    AuditLog,
    DbConnection,
    Job,
    Source,
    VerifiedDomain,
)
from app.ingest.qdrant_writer import (
    delete_points_for_source,
    hide_points_for_source,
    unhide_points_for_source,
)
from app.ingest.web_adapter import is_domain_verified

# Task 2.9.g duplication-check fix [SECURITY]: this module had zero
# logging anywhere, confirmed by grep before adding this -- meaning
# delete_source()/disable_source()/enable_source() (all three
# irreversible-or-deletion-adjacent) emitted no log line at all for
# their own mutations, diverging from the per-deletion logging
# convention `_cleanup_stale_crawl_documents()` (Task 2.9.d) and
# `reconcile_tenant()` (Task 2.9.f) both already established.
logger = logging.getLogger(__name__)

# Task 2.6.b: the first REAL job-type vocabulary -- a real caller
# (adapter, scheduler) could legitimately enqueue(). Deliberately real-
# types-only, never polluted with the worker's own test-scaffolding
# handler names ("noop"/"sleep", app/worker.py's JOB_HANDLERS) -- a
# future reader checking this constant should see exactly what real job
# types exist, nothing else. Confirmed live (not assumed) that this is
# NOT the same thing as JOB_HANDLERS's own key set: JOB_HANDLERS answers
# "which function processes this job_type" (a worker-dispatch concern,
# and today also includes "noop"/"sleep", pure loop-mechanics test
# scaffolding with no real caller and no place in this vocabulary) while
# VALID_JOB_TYPES answers "may a real caller ever enqueue this at all" (a
# write-boundary concern). The relationship going forward: JOB_HANDLERS
# must register a handler for every value here (so a real job never hits
# the worker's own "unknown job_type" permanent-failure path) but may
# always have additional test-only entries this vocabulary never lists.
# Matches the `urls`/`crawl` config shapes already decided at the Step
# 2.6 breakdown (`sources.config = {"urls": [...]}` / `{"seed_url": ...}`)
# -- "ingest_" prefix distinguishes a job_type string from a sources.type
# string, since the two are deliberately not required to match 1:1
# (Step 2.9's own reconcile/refresh-scheduling jobs, 2.1.a's own decision
# entry, are not tied to one source row at all).
#
# Task 2.7.d: "ingest_upload" added -- `sources.config = {"uploads": [...]}`
# (app/ingest/job_handlers.py's own header comment has the full shape and
# reasoning for why a list of objects, not a bare list of strings like
# `urls`'s own).
#
# Task 2.8.e [SECURITY]: "ingest_db" added -- `sources.config =
# {"db_connection_id": "<uuid-string>"}`, pointing at the already-encrypted
# `db_connections` row (2.8.a) rather than duplicating its own
# `allowlisted_tables`/`row_templates` (2.8.c) a second time here. Matches
# `ingest_upload`'s own `upload_id`-round-trips-as-a-string precedent
# exactly (JSON has no native UUID type); see app/ingest/job_handlers.py's
# own header comment for the full shape/policy.
#
# Task 2.9.a: "scheduler_tick" added -- a genuinely different kind of job
# from the four `ingest_*` adapters above: it has no `source_id` (every
# tenant's own scheduler_tick decides which SOURCES are due, it is not
# itself about one source) and no real `sources.config` to read at all --
# `payload` stays `{}`. See app/ingest/scheduler.py's own header comment
# for the full design (per-tenant, self-re-enqueuing, the bootstrap/
# self-healing sweep) and app/ingest/job_handlers.py's own header comment
# for the real handler.
#
# Task 2.9.f [SECURITY]: "reconcile" added -- the identical shape as
# "scheduler_tick" immediately above (no `source_id`, no real
# `sources.config` to read, `payload` stays `{}`), a per-tenant self-re-
# enqueuing job mirroring that one's own mechanism exactly, just a
# different job_type and a much coarser interval. See
# app/ingest/scheduler.py's own `ensure_reconcile_ticks_seeded()` and
# app/ingest/job_handlers.py's own `handle_reconcile()` for the real
# design/handler.
VALID_JOB_TYPES: frozenset[str] = frozenset(
    {
        "ingest_url",
        "ingest_crawl",
        "ingest_upload",
        "ingest_db",
        "scheduler_tick",
        "reconcile",
        # Task 4.2: "retention_sweep" added -- the identical shape as
        # "scheduler_tick"/"reconcile" above (no source_id, no real
        # sources.config, payload stays {}), a per-tenant self-re-
        # enqueuing job mirroring both's own mechanism exactly. See
        # app/ingest/scheduler.py's own `ensure_retention_sweep_ticks_
        # seeded()` and app/ingest/job_handlers.py's own
        # `handle_retention_sweep()` for the real design/handler.
        "retention_sweep",
    }
)


class CredentialEncryptionError(Exception):
    """Raised by create_db_connection()/get_decrypted_credentials() for any
    failure of the pgp_sym_encrypt()/pgp_sym_decrypt() statement itself
    (e.g. a wrong or rotated db_connection_encryption_key). The message is
    always this fixed, generic description -- never the original
    exception's string form. SQLAlchemy's default exception formatting
    embeds every bound parameter of the failing statement, which includes
    the key itself, so the original exception is never re-raised, chained,
    or logged; it is only caught and replaced (duplication check after
    2.1.a/b, item C1 -- found live: a wrong-key pgp_sym_decrypt() failure's
    own str(exc) contained the literal key value).
    """


class DomainAlreadyClaimedError(Exception):
    """Raised by claim_domain() when the domain is already actively claimed
    (status pending or verified, PROJECT_SPEC.md's Step 2.2 breakdown
    decision (e)) by any tenant -- including this same one; see that
    method's own comment for why a same-tenant re-claim is treated
    identically to a cross-tenant one, not specially allowed through. The
    application-level translation of verified_domains' own partial unique
    index (ix_verified_domains_domain_active_unique, Task 2.2.b) rejecting
    the insert.

    Unlike CredentialEncryptionError above, this needs none of that
    class's own deferred-raise/from-None discipline: the message names
    only the domain the caller just supplied (not a secret), so the
    original IntegrityError can stay chained via a normal "raise ... from
    exc" without leaking anything.
    """


class DomainRevokedError(Exception):
    """Raised by confirm_verification() when the domain's own claim is
    "revoked" -- a revoked claim is never re-verified in place: the
    platform admin revoked it for a reason (2.2.g's own revoke_domain(),
    not yet built), and silently letting a confirm call resurrect it
    would undermine that action. The tenant must call claim_domain()
    again, which generates a brand-new per-attempt token, never resuming
    the old, revoked one. Matches DomainAlreadyClaimedError's own shape
    directly: a plain chained `raise ... from None` is not even needed
    here since there is no underlying exception to chain -- this is a
    normal state-check, not a caught database error.
    """


class UnknownJobTypeError(Exception):
    """Raised by enqueue() when `job_type` is not in VALID_JOB_TYPES --
    checked UP FRONT, before any database interaction at all (see
    enqueue()'s own comment for why this ordering, relative to the
    source_id FK check below, is deliberate). Not a caught database
    error (no IntegrityError involved) -- a plain pre-check, matching
    DomainRevokedError's own shape: no chaining needed.
    """


class UnknownSourceError(Exception):
    """Raised by enqueue() when `source_id` does not reference a real row
    in `sources` -- the application-level translation of jobs.source_id's
    own `jobs_source_id_fkey` foreign key rejecting the insert. Matches
    DomainAlreadyClaimedError's own shape: `source_id` is not a secret
    (the caller just supplied it), so the original IntegrityError stays
    chained via a plain `raise ... from exc`.

    Task 2.9.b: also raised by update_source()/disable_source() (via
    `IngestRepository._get_owned_source()` below) when `source_id` belongs
    to no row at all, OR to a different tenant -- the two cases are
    deliberately never distinguished (matching get_domain_by_id()'s own
    enumeration-safety discipline below: a caller must not be able to tell
    "wrong id" from "someone else's id" apart), just raised here instead of
    returned as `None` -- update/disable are action verbs, not lookups, and
    silently no-oping on a bad id would hide a real caller mistake.
    """


class UnknownRefreshIntervalError(Exception):
    """Raised by create_source()/update_source() when `refresh_interval` is
    not one of REFRESH_INTERVAL_DELTAS's own keys (app/ingest/models.py) --
    a plain pre-check, no database interaction involved, matching
    UnknownJobTypeError's own shape exactly (checked up front, before
    anything else). Distinct from RefreshIntervalExceedsPlanError below:
    this is "not a real interval at all" (a typo/config-drift case),
    whereas that one is "a real interval, just not one this plan allows."
    """


class RefreshIntervalExceedsPlanError(Exception):
    """Raised by create_source()/update_source() when `refresh_interval` IS
    one of REFRESH_INTERVAL_DELTAS's own keys, but is more frequent than
    `Settings.refresh_interval_min_allowed` permits (app/config.py's own
    Settings-stand-in for not-yet-built per-plan limit reading, Task 4.3).
    A plain pre-check, same shape as UnknownRefreshIntervalError above --
    see `_refresh_interval_violates_plan()`'s own comment for the exact
    comparison, shared with `flag_source_if_exceeds_plan()` below (the
    downgrade-flagging primitive uses the identical comparison, just reacts
    to it differently: flags an EXISTING row instead of rejecting a new
    write).
    """


class DomainNotVerifiedError(Exception):
    """Task 2.10.b [SECURITY]: raised by create_source() when a `crawl`-
    type source's own `config["seed_url"]` names a domain that is not
    `verified_domains`-verified for this tenant. Matches `DomainAlready
    ClaimedError`/`DomainRevokedError`'s own naming convention above.

    The real, confirmed gap this closes: before this fix, `create_source()`
    never checked domain verification at all for ANY source type --
    `ingest_url()` (`app/ingest/web_adapter.py`) only ever rejects the
    per-page WRITE, strictly AFTER `run_crawl()`'s own discovery phase
    (robots.txt fetch, sitemap.xml fetch, then BFS link-following, up to
    `Settings.crawl_page_cap` pages) has already issued real, SSRF-guarded
    but ownership-unproven HTTP traffic against an arbitrary tenant-named
    domain. docs/SPEC.md §16's own "Crawling an unverified domain is
    rejected" is a claim about the FETCH, not merely the write -- the
    `urls` adapter already satisfies it for free (`ingest_url()`'s own
    verification check runs strictly before its own `fetch_with_
    redirects()` call, so a single unverified URL never generates any
    network activity at all), but nothing stopped a `crawl`-type source
    from being created, scheduled and run against a domain the tenant
    never proved ownership of.

    Checked only for `source_type == "crawl"` -- the `urls` adapter's own
    per-URL check already covers the `urls` source type with zero
    network activity, and `upload`/`database` sources have no domain
    concept at all. Reuses `is_domain_verified()` (`app/ingest/
    web_adapter.py`) rather than a second, parallel query -- the exact
    same check `ingest_url()` already runs, just moved one step earlier
    (source creation, not per-page fetch) for this one source type. See
    also `handle_ingest_crawl()`'s own matching handler-level backstop
    (`app/ingest/job_handlers.py`) for a source verified at creation but
    revoked before its first scheduled run.
    """


class RefreshNowCooldownError(Exception):
    """Raised by request_refresh_now() when `source.last_refresh_requested_at`
    is too recent -- fewer than `Settings.refresh_now_cooldown_seconds`
    have elapsed since the last accepted request. A plain pre-check (the
    comparison needs only the already-fetched `source` row, no further
    database interaction) -- matches UnknownRefreshIntervalError's own
    shape. Distinct from RefreshNowAllowanceExhaustedError below: this is
    "too soon since the LAST request," a time-based gate, independent of
    how many requests remain in the current day's allowance.
    """


class RefreshNowAllowanceExhaustedError(Exception):
    """Raised by request_refresh_now() when `source.refresh_now_count`
    has already reached `Settings.refresh_now_daily_allowance` for the
    current rolling 24h window (`source.refresh_now_window_started_at`).
    A plain pre-check, same shape as RefreshNowCooldownError above.
    """


class TenantSuspendedError(Exception):
    """Task 2.9.g [SECURITY], Finding 1 (Part 2 close-out review): raised
    by request_refresh_now() when the owning tenant is no longer
    `"active"`. The scheduled path (`scheduler_tick`) is protected by
    THREE independent layers against a suspended tenant's background
    ingestion continuing (the seeding query, `_check_active_and_
    reenqueue_self()`, and `get_due_sources()` itself -- all Task 2.9.a,
    app/ingest/scheduler.py's own header comment has the full reasoning)
    -- `request_refresh_now()` went through none of them, since it
    enqueues the adapter job directly rather than via any of those three.
    Checked FIRST, before even `source.enabled` below: "is the tenant
    this source belongs to even active" is a broader, more fundamental
    gate than "is this one source enabled," matching the scheduler
    path's own tenant-then-source layering exactly. Reuses
    `is_tenant_active()` (app/ingest/scheduler.py) rather than a second,
    parallel tenant-status query -- imported inside the method, not at
    module level, since scheduler.py already imports `IngestRepository`
    from this module; a top-level import here would be circular (the
    same resolution already used for `REFRESH_INTERVAL_DELTAS`/
    `JOB_TYPE_FOR_SOURCE_TYPE`, both relocated to models.py for the
    identical reason -- not done here since `is_tenant_active()` is a
    query FUNCTION, not shared data, and a deferred import is this
    project's own other established fix for exactly this shape, see
    job_handlers.py's handle_ingest_crawl()'s own `from app.worker
    import ...`).  See also `_skip_if_source_disabled_or_tenant_
    inactive()` (app/ingest/job_handlers.py) -- the matching handler-
    level defense-in-depth backstop for any OTHER path that might
    someday enqueue a job for a now-suspended tenant, not just this one.
    """


class SourceDisabledError(Exception):
    """Task 2.9.g [SECURITY] duplication-check fix: raised by
    request_refresh_now() when `source.enabled` is `False`. A real,
    confirmed cross-subtask gap found while reviewing Step 2.9 as a
    whole, not merely a theoretical one: before this fix, calling
    request_refresh_now() on a disabled (soft-hidden, Task 2.9.e) source
    enqueued a real adapter job anyway -- `_get_owned_source()` has never
    filtered on `enabled` -- and that job's own handler would write
    FRESH Qdrant points with no `HIDDEN_FIELD` set (the normal write
    path, build_payload(), never sets that flag), making the source's
    content answerable again WITHOUT ever calling enable_source(). A
    plain pre-check, same shape as RefreshNowCooldownError/
    RefreshNowAllowanceExhaustedError above -- checked right after
    TenantSuspendedError above, still before either of those two, since
    "is this source even eligible for refresh at all" is more
    fundamental than "how recently was it refreshed." See also
    `_skip_if_source_disabled_or_tenant_inactive()`
    (app/ingest/job_handlers.py) -- the matching handler-level defense-
    in-depth backstop for any OTHER path that might someday enqueue a
    job for a disabled source, not just this one.
    """


def _refresh_interval_violates_plan(refresh_interval: str) -> bool:
    # Task 2.9.b. The ONE comparison both create_source()/update_source()'s
    # own hard-reject check (a NEW write exceeding the current plan) and
    # flag_source_if_exceeds_plan() (an EXISTING row re-checked after some
    # future plan downgrade) share -- written once here so the two call
    # sites can never drift apart on what "exceeds the plan" even means
    # (rule 11). Takes a plain string, not a Source object: this keeps the
    # create_source() path trivial (checks the caller's own candidate
    # value before ever constructing a row) and keeps update_source() from
    # needing to mutate-then-maybe-undo an already-attached ORM attribute
    # just to ask this question.
    #
    # "Exceeds the plan" means "refreshes MORE often than
    # Settings.refresh_interval_min_allowed permits" -- a smaller
    # timedelta is a more frequent refresh, so a real violation is
    # `requested < floor`, not `>`. Both sides are looked up in the exact
    # same REFRESH_INTERVAL_DELTAS dict (app/ingest/models.py) -- no
    # separate ordering/comparison vocabulary invented for this.
    #
    # Assumes `refresh_interval` is already a recognized key -- every real
    # caller below calls _validate_refresh_interval() first, which checks
    # that separately and raises UnknownRefreshIntervalError before this
    # function is ever reached with a bad value.
    requested = REFRESH_INTERVAL_DELTAS[refresh_interval]
    floor = REFRESH_INTERVAL_DELTAS[get_settings().refresh_interval_min_allowed]
    return requested < floor


def _validate_refresh_interval(refresh_interval: str) -> None:
    # Task 2.9.b. Shared by create_source() and update_source() for
    # "anything changed, applied consistently" (this task's own decision
    # 1) -- one function, not two copies of the same two checks in two
    # methods. Order matters: the vocabulary check first (a config-drift/
    # typo case, same as UnknownJobTypeError's own up-front placement in
    # enqueue()) before even asking whether a REAL interval is plan-
    # exceeding -- an unrecognized string has no timedelta to compare at
    # all.
    if refresh_interval not in REFRESH_INTERVAL_DELTAS:
        raise UnknownRefreshIntervalError(
            f"refresh_interval {refresh_interval!r} is not one of "
            f"{sorted(REFRESH_INTERVAL_DELTAS)}"
        )
    if _refresh_interval_violates_plan(refresh_interval):
        raise RefreshIntervalExceedsPlanError(
            f"refresh_interval {refresh_interval!r} is more frequent than "
            f"this plan allows ({get_settings().refresh_interval_min_allowed!r} or coarser)"
        )


def flag_source_if_exceeds_plan(source: Source) -> bool:
    """The downgrade-flagging primitive (Task 2.9.b, decision 2): checks
    ONE already-existing `source` against the CURRENT plan limit (the same
    Settings-stand-in `_refresh_interval_violates_plan()` uses) and sets
    `source.exceeds_plan` to match -- never disables it, never touches
    `last_run_at`/scheduling. Returns the new flag value for a caller that
    wants to know without a second query.

    Deliberately NOT a one-way "flag and never clear" latch: re-running
    this after a later un-downgrade correctly clears a stale flag too (the
    identical comparison, just now false) -- the same self-healing
    "recurring check, not a one-time latch" shape this project already
    uses for ensure_scheduler_ticks_seeded()'s own suspended/un-suspended
    handling (app/ingest/scheduler.py).

    Does NOT flush/commit -- matches every other method in this module
    that mutates an already-attached row (revoke_domain() above): the
    caller owns the transaction boundary. create_source()/update_source()
    below never actually need this for their OWN writes (they hard-reject
    a violation before it is ever persisted, see _validate_refresh_interval()
    above) -- this function exists for a source that was fine when
    written and only later stopped being fine, which neither of those two
    methods' own code paths can ever produce by construction. Confirmed
    live (not assumed): no code anywhere in this codebase currently reacts
    to `tenants.plan_id` changing (grep across app/ finds that column only
    in its own definition, app/tenancy/models.py) -- there is no
    plan-change hook yet for this primitive to be wired into. It is built
    here, unwired, exactly as Task 2.9.b's own instructions asked for.
    """
    exceeds = _refresh_interval_violates_plan(source.refresh_interval)
    source.exceeds_plan = exceeds
    return exceeds


def _generate_verification_token() -> str:
    # Task 2.2.c. A fresh, unguessable value per claim attempt -- never
    # reused across attempts or tenants (PROJECT_SPEC.md's Step 2.2
    # breakdown, decision (b)) -- that the tenant publishes in DNS (2.2.d)
    # to prove control of the domain. Deliberately NOT
    # app.auth.secrets.generate_visitor_secret(), despite using the
    # identical underlying mechanism: a visitor secret is stored only as a
    # SHA-256 hash and never meant to leave the visitor's own browser,
    # whereas this token is stored in PLAINTEXT
    # (verified_domains.verification_token) and is meant to be published
    # publicly, in a DNS TXT record -- reusing that function's name would
    # conflate two different threat models that happen to share a
    # CSPRNG-backed generator underneath (secrets.token_urlsafe, rule 11 --
    # a well-known library mechanism, not a hand-rolled one). 32 bytes
    # (256 bits) matches generate_visitor_secret()'s own strength for
    # consistency, not because this value defends against the same kind of
    # online-guessing attack a visitor secret does.
    return secrets.token_urlsafe(32)


async def revoke_domain(
    session: AsyncSession,
    domain_id: uuid.UUID,
    actor: str,
    clock: DateTimeClock = lambda: datetime.now(UTC),
) -> VerifiedDomain | None:
    # Task 2.2.g. Deliberately module-level, never an IngestRepository
    # method -- same structural reasoning as create_tenant()/
    # get_site_key_by_key() (app/tenancy/repository.py): a platform admin
    # revoking a domain has no tenant_id of their own to construct
    # IngestRepository(tenant_id=..., session=...) with, and the whole
    # point of this function is that it is NOT scoped to any one tenant --
    # it must be able to reach every tenant's own domains. Looks up
    # directly (no tenant filter at all, unlike get_domain_by_id()), across
    # every tenant.
    #
    # `actor` is a required parameter, not hardcoded: this function has no
    # opinion on identity, and shouldn't -- the temporary admin-key
    # mechanism (2.2.f) carries none (AdminKeyVerified is deliberately
    # empty), so whatever calls this today passes a generic placeholder
    # string (e.g. "admin"); once Step 6.1's real platform_admin role
    # exists, its caller passes a real identity instead, with zero change
    # needed here. Hardcoding a value inside this function would bake
    # today's identity-less stopgap into the one place meant to outlive it.
    result = await session.execute(select(VerifiedDomain).where(VerifiedDomain.id == domain_id))
    domain = result.scalar_one_or_none()
    if domain is None:
        return None

    # Idempotent for the DOMAIN's own state, not an error -- matching
    # confirm_verification()'s own "verified" case (the identical shape of
    # problem: the row is already in the state the caller asked for). This
    # is NOT the same situation as confirm_verification()'s "revoked" case,
    # which raises DomainRevokedError: that case is a caller trying to move
    # a revoked domain to "verified" (a conflicting transition to a
    # DIFFERENT state), whereas this is a caller asking to revoke an
    # already-revoked domain (the SAME state, requested twice) --
    # re-running an admin action that already succeeded should be safe,
    # not a surprise exception. status/revoked_at never move again below.
    #
    # An audit_log row IS still written either way (design decision, 2.2.g/h
    # duplication check, item B1): this table's own purpose is admin-action
    # ACCOUNTABILITY, not only a record of state transitions that changed
    # something. Two different admins racing to revoke the same domain
    # (or one admin re-running the action, unsure whether it already took
    # effect) is itself a real event worth a trace -- silently returning
    # the existing row with no audit trace of the SECOND attempt would hide
    # exactly the kind of "who touched this and when" question this table
    # exists to answer. `already_revoked` in `details` below distinguishes
    # a genuine first revocation from a repeat attempt that changed nothing.
    already_revoked = domain.status == "revoked"
    if not already_revoked:
        domain.status = "revoked"
        domain.revoked_at = clock()

    # The (conditional) domain update and the audit_log insert below are
    # only flush()ed, never committed, here in the SAME call to
    # session.flush() -- matching every other method in this codebase
    # (claim_domain(), confirm_verification(), etc.): the caller owns the
    # transaction boundary, not this function. This is what makes the two
    # writes succeed or fail together: a single flush() sends every pending
    # change in the session as one unit of work, and a single caller-level
    # commit() (or rollback()) afterward applies to both rows at once --
    # there is no way for one to land without the other, by construction
    # of how AsyncSession/flush/commit works, not by anything extra this
    # function needs to add. On the already-revoked path, the domain row
    # has no pending change at all -- only the audit_log insert is new --
    # so the same flush() call simply has one row to send instead of two.
    session.add(
        AuditLog(
            actor=actor,
            action="revoke_domain",
            target_type="verified_domains",
            target_id=domain.id,
            # tenant_id stored as str, not the raw uuid.UUID: this project's
            # JSON serialization (the default json.dumps, no custom
            # engine-level serializer configured in app/db.py) does not
            # know how to encode a UUID object -- confirmed live before
            # relying on this, not assumed; str(uuid.UUID) round-trips
            # back through uuid.UUID(...) if ever needed for a real query.
            details={
                "domain": domain.domain,
                "tenant_id": str(domain.tenant_id),
                "already_revoked": already_revoked,
            },
        )
    )
    await session.flush()
    return domain


@dataclass(frozen=True)
class IngestRepository:
    tenant_id: uuid.UUID
    session: AsyncSession

    def __post_init__(self) -> None:
        if self.tenant_id is None:
            raise ValueError("IngestRepository requires a tenant_id, got None")

    async def create_db_connection(
        self,
        host: str,
        credentials: dict[str, str],
        allowlisted_tables: dict | None = None,
        row_templates: dict | None = None,
    ) -> DbConnection:
        """`credentials` is a fully opaque `dict[str, str]` as far as this
        method is concerned (it only ever JSON-serializes and encrypts
        whatever is passed) -- but Task 2.8.a decided its real shape for
        the one engine Step 2.8 actually supports (Postgres; MySQL/MariaDB
        explicitly deferred, see PROJECT_SPEC.md's Step 2.8 decision log).
        This docstring is the ONE canonical place to look for that shape --
        point future readers/callers here, don't re-derive it:

        - `"database"`, `"user"`, `"password"` -- required, no defaults.
        - `"port"` -- optional, defaults to `"5432"` if absent.
        - `"sslmode"` -- optional, defaults to `"prefer"` if absent. One of
          libpq's own standard vocabulary: `disable`/`allow`/`prefer`/
          `require`/`verify-ca`/`verify-full` (confirmed live against the
          installed `asyncpg`'s own `SSLMode.parse()`, which accepts these
          exact strings).
        - `"host"` is deliberately NOT a key in this dict -- `host` is
          already this method's own separate parameter, stored unencrypted
          on the `DbConnection` row itself (needed to run the SSRF-
          equivalent guard, `app/ingest/database_adapter.py`'s
          `validate_database_host()`, BEFORE any credential ever needs
          decrypting). Storing it a second time inside the encrypted blob
          too would create two sources of truth for the same fact, with no
          mechanism to keep them in sync.
        - No `"engine"` key: with exactly one supported engine, a field
          with only one ever-written value would be a parameter this
          step does not need (rule 11). Whoever adds a second engine later
          must decide how it is distinguished then (a new column is the
          likely shape, matching `host`'s own precedent of staying outside
          the opaque blob) -- not decided speculatively here.

        See `app/ingest/database_adapter.py`'s own `connect_safely()` for
        the one real consumer of this shape today.
        """
        # The encryption happens in Postgres itself (pgcrypto), computed
        # via a scalar SELECT before the row is constructed -- there is no
        # client-side implementation of pgp_sym_encrypt to call directly.
        key = get_settings().db_connection_encryption_key_str()
        encryption_error: CredentialEncryptionError | None = None
        try:
            encrypted = (
                await self.session.execute(
                    select(func.pgp_sym_encrypt(json.dumps(credentials), key))
                )
            ).scalar_one()
        except SQLAlchemyError:
            # Raised below, outside this except block, so Python never
            # implicitly chains __context__/__cause__ to the original
            # exception (matching app/auth/tokens.py's decode_session_token()
            # convention) -- confirmed live that `raise ... from None` alone
            # does NOT clear __context__, only __suppress_context__.
            encryption_error = CredentialEncryptionError(
                "failed to encrypt credentials for storage"
            )
        if encryption_error is not None:
            raise encryption_error
        db_connection = DbConnection(
            tenant_id=self.tenant_id,
            host=host,
            encrypted_credentials=encrypted,
            allowlisted_tables=allowlisted_tables or {},
            row_templates=row_templates or {},
        )
        self.session.add(db_connection)
        await self.session.flush()
        return db_connection

    async def create_source(
        self,
        source_type: str,
        config: dict,
        refresh_interval: str,
    ) -> Source:
        """Task 2.9.b, the first real source-lifecycle primitive.
        `refresh_interval` is hard-rejected up front (before any row is
        ever constructed) by `_validate_refresh_interval()` above -- an
        unrecognized value raises UnknownRefreshIntervalError, one more
        frequent than `Settings.refresh_interval_min_allowed` allows raises
        RefreshIntervalExceedsPlanError. `source_type` is NOT pre-validated
        the same way: `ck_sources_type` (the model's own CheckConstraint)
        already owns that closed vocabulary, matching claim_domain()'s own
        established "trust the CheckConstraint, don't re-validate in
        Python" precedent for `method` above -- an invalid value still
        fails safely, just as an IntegrityError, not specially translated
        here (no real caller constructs one today; this mirrors existing
        practice, not a gap introduced by this task).

        Deliberately does NOT call flag_source_if_exceeds_plan() or write
        `exceeds_plan` at all: a row that violates the current plan is
        rejected above before construction, so a freshly created source can
        never start out already exceeding it -- the column's own
        `server_default=false` (app/ingest/models.py) is correct as-is.

        Deliberately does NOT seed any scheduler due-state of its own:
        `scheduler_tick` is a per-TENANT job (Task 2.9.a), not a per-SOURCE
        one, and get_due_sources() already selects every one of this
        tenant's own enabled sources by `tenant_id` alone, with no
        per-source registration step -- a brand-new source's own
        `last_run_at IS NULL` already makes it due the very next time
        this tenant's existing (or self-healing, see
        ensure_scheduler_ticks_seeded()'s own docstring) scheduler_tick
        chain runs. Inventing a second seeding mechanism here would
        duplicate that already-working path (rule 11); proven live, not
        assumed, by test_ingest_repository.py's own
        test_create_source_is_immediately_due_with_no_extra_seeding_call.

        Task 2.10.b [SECURITY]: for `source_type == "crawl"` only, checked
        BEFORE any row is constructed -- see `DomainNotVerifiedError`'s
        own docstring above for the real, confirmed gap this closes.
        `update_source()` deliberately does NOT get a matching check: a
        later `update_source()` call changing `config["seed_url"]` to a
        DIFFERENT, unverified domain is instead caught by `handle_ingest_
        crawl()`'s own matching backstop (`app/ingest/job_handlers.py`),
        which re-checks THIS source's own current seed_url right before
        every real run regardless of how it got that way -- adding a
        second check here would duplicate that one, not close a gap it
        misses.
        """
        _validate_refresh_interval(refresh_interval)
        if source_type == "crawl":
            hostname = httpx.URL(config["seed_url"]).host
            if not await is_domain_verified(self.session, self.tenant_id, hostname):
                raise DomainNotVerifiedError(
                    f"domain {hostname!r} is not verified for this tenant"
                )
        source = Source(
            tenant_id=self.tenant_id,
            type=source_type,
            config=config,
            refresh_interval=refresh_interval,
            enabled=True,
            status="active",
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def _get_owned_source(self, source_id: uuid.UUID) -> Source:
        # Shared by update_source()/disable_source() below -- the one real
        # place both look up a source scoped to this repository's own
        # tenant_id. A source_id belonging to a different tenant and one
        # that doesn't exist at all are deliberately never distinguished
        # (UnknownSourceError's own updated comment above has the full
        # enumeration-safety reasoning), matching get_domain_by_id()'s own
        # tenant-scoping precedent below, just raising instead of
        # returning None (these two callers are actions, not lookups).
        result = await self.session.execute(
            select(Source).where(Source.id == source_id, Source.tenant_id == self.tenant_id)
        )
        source = result.scalar_one_or_none()
        if source is None:
            raise UnknownSourceError(f"source {source_id} does not exist")
        return source

    async def update_source(
        self,
        source_id: uuid.UUID,
        *,
        config: dict | None = None,
        refresh_interval: str | None = None,
    ) -> Source:
        """Task 2.9.b. `config`/`refresh_interval` are both optional and
        independently applied -- `None` means "leave unchanged," matching
        this project's own partial-update convention elsewhere (e.g.
        `create_db_connection()`'s own `allowlisted_tables=None` ->
        "unchanged" shape, just applied here to an UPDATE instead of an
        INSERT). `source_type`/`tenant_id` are deliberately not updatable
        here at all: changing a source's own fundamental adapter type isn't
        a real "edit an existing source" operation this task's own
        instructions describe (create_source()/update_source()/
        disable_source(), nothing about re-typing one) -- not built
        speculatively.

        `refresh_interval`, when provided, goes through the EXACT SAME
        `_validate_refresh_interval()` hard-reject path as create_source()
        above (this task's own decision 1: "applied consistently") --
        checked against the plain candidate string BEFORE it ever touches
        `source.refresh_interval`, so a rejected update leaves the
        already-persistent row completely untouched (no dirty attribute to
        roll back).
        """
        source = await self._get_owned_source(source_id)
        if refresh_interval is not None:
            _validate_refresh_interval(refresh_interval)
            source.refresh_interval = refresh_interval
        if config is not None:
            source.config = config
        await self.session.flush()
        return source

    async def disable_source(
        self,
        source_id: uuid.UUID,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
    ) -> Source:
        """Task 2.9.b, decision 3. Sets `enabled = False` -- NOT
        `status` (confirmed live before building, not assumed:
        get_due_sources()'s own real WHERE clause, app/ingest/scheduler.py,
        already filters on `Source.enabled.is_(True)` alone; `status` has
        no CheckConstraint and no vocabulary defined anywhere in this
        codebase, and docs/SPEC.md's own dashboard wording (§6.3: "a table
        with type, status, last and next refresh") describes `status` as a
        separate, not-yet-built ingestion-health DISPLAY concern -- "ok" /
        "syncing" / "error" -- unrelated to the enable/disable toggle this
        task needs). This makes get_due_sources()'s own existing filter the
        structural backstop with ZERO query changes needed: reusing that
        filter exactly, per this task's own instruction, rather than
        extending it or adding a second independent check. A disabled
        source is proven live, via a real get_due_sources() call, to never
        come back as due -- even with a NULL/stale `last_run_at` -- by
        test_ingest_repository.py's own
        test_disable_source_stops_it_from_being_returned_as_due.

        Task 2.9.e, decision 1: widened to also soft-HIDE this source's
        own Qdrant points (`hide_points_for_source()`) -- disabling is
        reversible by design, so points are flagged, never deleted. The
        Qdrant call happens BEFORE the `enabled = False` flush, not
        after: matching Task 2.9.d's own established "the worse failure
        mode is content staying answerable when it shouldn't be"
        precedent, applied in the direction that fits THIS trigger -- if
        the later flush ever failed, the worst surviving state is points
        already hidden for a source the database still shows as enabled
        (strictly MORE hidden than the database claims, never less),
        rather than the reverse.
        """
        source = await self._get_owned_source(source_id)
        await hide_points_for_source(
            client, collection_name, tenant_id=self.tenant_id, source_id=source.id
        )
        source.enabled = False
        await self.session.flush()
        logger.info(
            "disable_source: tenant %s source %s disabled, points hidden",
            self.tenant_id,
            source.id,
        )
        return source

    async def enable_source(
        self,
        source_id: uuid.UUID,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
    ) -> Source:
        """Task 2.9.e: `disable_source()`'s own reversal -- implied by
        decision 2's "the mechanism to set/clear [the hidden flag]" and
        this task's own required test (c), not separately named as a
        numbered build item, but a natural, necessary counterpart built
        here rather than left half-built (matching this project's own
        `mark_job_succeeded()`/`mark_job_failed()` precedent, Task 2.1.c:
        a state transition is never built in only one direction when the
        other is obviously needed too). Un-hides via
        `unhide_points_for_source()` -- zero re-ingestion, by construction:
        the points were never deleted, only flagged, so removing the flag
        is the entire operation.

        Ordering is the SAME "Qdrant call before the flush" rule
        `disable_source()` above uses, for consistency (rule 11 -- one
        convention, not two), even though re-enabling has no asymmetric
        security risk either way yet (no retrieval-time consumer of the
        hidden flag exists until Step 3.1, see this task's own new Open
        marker): if the later flush ever failed, the worst surviving
        state is points already un-hidden for a source the database
        still shows as disabled -- harmless today, and still the more
        conservative direction once a real consumer exists.
        """
        source = await self._get_owned_source(source_id)
        await unhide_points_for_source(
            client, collection_name, tenant_id=self.tenant_id, source_id=source.id
        )
        source.enabled = True
        await self.session.flush()
        logger.info(
            "enable_source: tenant %s source %s enabled, points unhidden",
            self.tenant_id,
            source.id,
        )
        return source

    async def delete_source(
        self,
        source_id: uuid.UUID,
        *,
        client: AsyncQdrantClient,
        collection_name: str,
    ) -> None:
        """Task 2.9.e, decision 1/3: DELETING a source is final and
        irreversible -- hard-deletes its Qdrant points and the `Source`
        row itself (which cascades to its own `documents` and `jobs`
        rows automatically, see below). No return value: unlike every
        other method on this class, there is no surviving row to hand
        back once this returns.

        Cascade investigation, confirmed live before building, not
        assumed (this task's own build item 3 asked for this explicitly):
        `documents.source_id` and `jobs.source_id` both declare
        `ForeignKey("sources.id", ondelete="CASCADE")` (app/ingest/
        models.py) -- a real DATABASE-level cascade, not an ORM-level
        `cascade=` relationship option (this class uses no ORM
        relationships at all, only explicit queries). Deleting the
        `Source` row and flushing is therefore sufficient on its own for
        both tables; no explicit `DELETE FROM documents`/`DELETE FROM
        jobs` is written here, and adding one would be a redundant,
        independently-maintained second copy of what Postgres already
        guarantees (rule 11). `db_connections` is DIFFERENT: confirmed by
        reading its own model that it has no FK to `sources` at all in
        EITHER direction (`sources.config["db_connection_id"]` is a plain
        string pointer, not a real FK, per Task 2.8.e's own decision) --
        deleting a source therefore leaves its own referenced
        `db_connections` row completely untouched, neither cascaded nor
        cleaned up here. Building a new cleanup mechanism for it was
        considered and declined: nothing in this task's own instructions
        asks for it, a `db_connections` row is not proven to belong to
        exactly one source (the schema does not enforce that), and
        inventing one here would be solving a problem this task was not
        asked to solve (rule 11).

        Upload bytes (decision 4): for an upload-type source, every
        referenced upload's own on-disk file is deleted via
        `upload_storage.delete()` (Task 2.7.a, already idempotent --
        `Path.unlink(missing_ok=True)`) -- resolving the FIRST 2.7.c-
        recorded Open marker ("nothing deletes on-disk bytes on source
        deletion"). The SECOND 2.7.c marker (orphaned bytes when a
        re-upload REPLACES an upload_id under the same source/filename --
        a config-transition trigger, not a source-deletion one) is
        explicitly NOT touched here, per this task's own instruction --
        confirmed left open below (PROJECT_SPEC.md's own Open markers).

        Ordering: upload-file deletion, THEN Qdrant, THEN the `Source`
        row -- deliberately the safest-first order, not an arbitrary one.
        `upload_storage.delete()` and `delete_points_for_source()` are
        both confirmed idempotent (missing_ok=True; a zero-match Qdrant
        filter completes successfully, Task 2.9.d's own live finding), so
        a failure at any step leaves every prior step safely re-runnable
        on a retry -- the only real asymmetry is that a `Source` row
        deleted first, with the Qdrant delete failing afterward, would
        leave content answerable with no owning row left to re-run this
        method against at all (`_get_owned_source()` would raise
        UnknownSourceError on retry). Qdrant before Postgres mirrors Task
        2.9.d's own established reasoning exactly: the worse failure mode
        is content staying answerable when it should not be, so the
        store visitors can actually retrieve from is cleared first.
        """
        source = await self._get_owned_source(source_id)
        upload_count = 0
        if source.type == "upload":
            storage_dir = Path(get_settings().upload_storage_path)
            uploads = source.config.get("uploads", [])
            for entry in uploads:
                upload_storage.delete(uuid.UUID(entry["upload_id"]), storage_dir=storage_dir)
            upload_count = len(uploads)
        await delete_points_for_source(
            client, collection_name, tenant_id=self.tenant_id, source_id=source.id
        )
        await self.session.delete(source)
        await self.session.flush()
        logger.info(
            "delete_source: tenant %s source %s deleted (type=%s, %d upload file(s) removed)",
            self.tenant_id,
            source_id,
            source.type,
            upload_count,
        )

    async def request_refresh_now(
        self,
        source_id: uuid.UUID,
        *,
        clock: DateTimeClock = lambda: datetime.now(UTC),
    ) -> Job:
        """Task 2.9.c. An on-demand, ahead-of-schedule refresh trigger --
        gated by a cooldown and a daily allowance, both checked BEFORE any
        mutation at all: either both pass and the full effect (job
        enqueued, `last_refresh_requested_at` updated, the daily count
        advanced) applies together in one flush, or neither check's own
        exception is raised with the row left completely untouched. No
        silent no-op path exists -- a failing check always raises.

        Cooldown (decision 2, confirmed before building): gated on
        `source.last_refresh_requested_at`, a field DEDICATED to this
        check -- deliberately NOT `last_run_at` (`Source`'s own comment,
        app/ingest/models.py, has the full reasoning: `last_run_at`
        answers "did this source's own sync actually complete," the right
        signal for get_due_sources()'s due-query, but the wrong one for
        "was refresh-now INVOKED too recently," which this field alone
        answers). `None` (never requested) always passes.

        Daily allowance (decision 1, confirmed before building): a
        Settings-stand-in, `Settings.refresh_now_daily_allowance` --
        deliberately NOT real `plans.limits` reading, the same deferral
        already established for `crawl_page_cap`/`upload_max_size_bytes`/
        `db_sync_row_cap`/`refresh_interval_min_allowed` (see the matching
        Open marker). Counted via a per-window counter that resets
        (`source.refresh_now_count`/`refresh_now_window_started_at`, see
        `Source`'s own comment for why this shape over a rolling-window
        event history), not plan-aware beyond that global ceiling.

        Mechanically (decision 3, confirmed before building): just an
        immediate `enqueue()` of the source's OWN existing adapter job
        type/config, the exact same `JOB_TYPE_FOR_SOURCE_TYPE` mapping and
        `payload={}`-plus-`source_id` shape `handle_scheduler_tick()`
        (job_handlers.py) already uses for a scheduled run -- no new
        ingestion logic, this method is the trigger and gating only.
        `enqueue()`'s own default `next_run_at=None` (claimable
        immediately) is exactly what "on-demand" needs, no override.

        Scheduler interaction (decision 4, confirmed live, not assumed):
        no new coupling code exists, or is needed. Because this still
        enqueues the identical adapter job type a scheduled run would,
        that job's own handler writes `last_run_at` at completion
        regardless of trigger source (the canonical per-item contract,
        Step 2.7/2.9.a) -- get_due_sources() then naturally sees this
        source as recently-run and excludes it, exactly as it would for
        any other completed run, with zero code of its own aware that
        refresh-now exists. Proven live, not assumed, by
        test_job_handlers.py's own
        test_a_source_refreshed_via_refresh_now_is_not_returned_as_due_afterward
        (through the REAL worker.run() loop, not called in isolation --
        matches every other job-handler task's own proof requirement).

        No exemption for a new/never-run source (build item 3): a never-
        requested source (`last_refresh_requested_at IS NULL`) always
        clears the cooldown check, and create_source()'s own scheduler-
        seeding (2.9.b) already covers getting it scheduled at all -- there
        is nothing left for this method to special-case.

        Task 2.9.g [SECURITY] duplication-check fix: `source.enabled`
        checked before cooldown or allowance -- see
        `SourceDisabledError`'s own docstring above for the real,
        confirmed gap this closes (a disabled source's content could
        previously be made answerable again via this method alone,
        without ever calling enable_source()).

        Task 2.9.g [SECURITY], Finding 1: tenant-active checked FIRST,
        before even `source.enabled` -- see `TenantSuspendedError`'s own
        docstring above for the real, confirmed gap this closes (a
        suspended tenant's source could previously still be refreshed on
        demand via this method alone, bypassing all three layers that
        protect the scheduled path).
        """
        source = await self._get_owned_source(source_id)
        # Deferred import: circular at module level otherwise, see
        # TenantSuspendedError's own docstring above for why.
        from app.ingest.scheduler import is_tenant_active

        if not await is_tenant_active(self.session, self.tenant_id):
            raise TenantSuspendedError(f"tenant {self.tenant_id} is not active")
        if not source.enabled:
            raise SourceDisabledError(f"source {source_id} is disabled")
        now = clock()
        settings = get_settings()

        if source.last_refresh_requested_at is not None:
            elapsed = (now - source.last_refresh_requested_at).total_seconds()
            if elapsed < settings.refresh_now_cooldown_seconds:
                raise RefreshNowCooldownError(
                    f"source {source_id} was refreshed {elapsed:.0f}s ago; "
                    f"the cooldown is {settings.refresh_now_cooldown_seconds:.0f}s"
                )

        window_expired = source.refresh_now_window_started_at is None or (
            now - source.refresh_now_window_started_at >= timedelta(hours=24)
        )
        count_in_window = 0 if window_expired else source.refresh_now_count
        if count_in_window >= settings.refresh_now_daily_allowance:
            raise RefreshNowAllowanceExhaustedError(
                f"source {source_id} has used its daily refresh-now allowance "
                f"({settings.refresh_now_daily_allowance})"
            )

        job_type = JOB_TYPE_FOR_SOURCE_TYPE[source.type]
        job = await self.enqueue(job_type=job_type, source_id=source.id, payload={})
        source.last_refresh_requested_at = now
        source.refresh_now_window_started_at = now if window_expired else (
            source.refresh_now_window_started_at
        )
        source.refresh_now_count = count_in_window + 1
        await self.session.flush()
        return job

    async def get_decrypted_credentials(self, db_connection_id: uuid.UUID) -> dict[str, str]:
        # The one explicit call that unwraps the credential. Never log
        # this, and never log its return value.
        key = get_settings().db_connection_encryption_key_str()
        encryption_error: CredentialEncryptionError | None = None
        try:
            result = await self.session.execute(
                select(func.pgp_sym_decrypt(DbConnection.encrypted_credentials, key)).where(
                    DbConnection.id == db_connection_id,
                    DbConnection.tenant_id == self.tenant_id,
                )
            )
            raw = result.scalar_one_or_none()
        except SQLAlchemyError:
            # See create_db_connection()'s identical comment above: raised
            # outside this except block on purpose.
            encryption_error = CredentialEncryptionError("failed to decrypt stored credentials")
        if encryption_error is not None:
            raise encryption_error
        if raw is None:
            raise ValueError(
                f"db_connection {db_connection_id} does not belong to tenant {self.tenant_id}"
            )
        return json.loads(raw)

    async def enqueue(
        self,
        job_type: str,
        payload: dict | None = None,
        source_id: uuid.UUID | None = None,
        next_run_at: datetime | None = None,
    ) -> Job:
        # Task 2.1.c: a method here, not a standalone function like
        # create_tenant()/get_site_key_by_key() (app/tenancy/repository.py)
        # -- those are standalone specifically because no tenant_id exists
        # yet at the point they're called; enqueueing always happens on
        # behalf of a specific tenant's own already-known action (a source
        # being added, a scheduled refresh), so it fits this class's own
        # invariant (a real tenant_id, already required to construct this
        # repository) exactly the way create_visitor()/create_conversation()
        # do on TenantScopedRepository. status="pending" and next_run_at
        # are not set explicitly here: status has no column default (every
        # other status column in this codebase -- sources.status,
        # documents.status -- is set explicitly too), but next_run_at and
        # attempts both already default correctly via the model's own
        # TIMESTAMP_NOW/server_default (now, and 0) -- see
        # app/ingest/models.py. max_attempts IS set explicitly, from
        # Settings, rather than left to the column's own server_default=5:
        # see PROJECT_SPEC.md's Step 2.1.c decision entry for why (the
        # column default stays only as an inert defensive floor for any
        # insert that bypasses this method).
        #
        # Task 2.9.a: `next_run_at` widened in -- a real, necessary
        # consequence, not speculative. Every caller before this one wanted
        # "eligible to claim right away", exactly the model's own
        # TIMESTAMP_NOW default -- but `scheduler_tick`'s own self-re-
        # enqueue (app/ingest/scheduler.py) genuinely needs the OPPOSITE: a
        # job that is NOT yet claimable, due only `Settings.
        # scheduler_tick_interval_seconds` in the future, or the tick would
        # claim its own successor again immediately, in a tight loop with
        # no real interval at all. Defaults to `None` -- passed through to
        # `Job(...)` only when given, so every existing caller's own exact
        # prior behavior (the model's TIMESTAMP_NOW default applying) is
        # unchanged, byte for byte; only this new real caller ever passes a
        # value.
        #
        # Task 2.6.b: job_type validated FIRST, before any database
        # interaction at all -- deliberately this order, not the reverse,
        # relative to the source_id FK check below. Checking job_type
        # membership in VALID_JOB_TYPES is a plain, free, in-process
        # lookup (no query needed either way); checking whether source_id
        # exists has NO equivalent cheap pre-check -- the only ways to
        # know are a redundant SELECT before the insert (an extra round
        # trip, paid on every call, including the overwhelming majority
        # that already pass a real id) or letting the database's own FK
        # constraint be the single source of truth (what this method
        # already does below). Cheapest, fastest-failing check first.
        if job_type not in VALID_JOB_TYPES:
            raise UnknownJobTypeError(
                f"job_type {job_type!r} is not a registered job type "
                f"(expected one of {sorted(VALID_JOB_TYPES)})"
            )
        job = Job(
            tenant_id=self.tenant_id,
            source_id=source_id,
            job_type=job_type,
            status="pending",
            max_attempts=get_settings().job_max_attempts,
            payload=payload or {},
            **({"next_run_at": next_run_at} if next_run_at is not None else {}),
        )
        self.session.add(job)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Task 2.6.b: the same catch+rollback+translate pattern
            # already established twice (claim_domain()'s own original
            # fix; create_site_key()'s matching fix, duplication check
            # after 2.2.a/b/c, item D1) -- narrowed to the one specific
            # constraint a caller-supplied source_id can actually violate
            # (confirmed live against the real test database:
            # "jobs_source_id_fkey"), not a blanket catch. tenant_id is
            # this repository's own trusted identity and job_type is
            # already validated above, so no other constraint on this
            # table is reachable through this method's own inputs today;
            # anything else re-raises completely unchanged, matching
            # every sibling method's own precedent for an unexpected
            # violation.
            if violated_constraint_name(exc) != "jobs_source_id_fkey":
                raise
            await self.session.rollback()
            raise UnknownSourceError(f"source {source_id} does not exist") from exc
        return job

    async def claim_domain(self, domain: str, method: str = "dns") -> VerifiedDomain:
        # Task 2.2.c. A method here, not a standalone function, for the
        # identical reason enqueue() above is one: claiming always happens
        # on behalf of an already-known tenant. Always starts "pending"
        # with a fresh token -- this method never verifies anything itself
        # (2.2.e's own job) and never returns an existing row.
        #
        # Task 2.6.a: `method` is now a real, caller-supplied parameter,
        # not hardcoded -- 2.2.c's own decision entry explicitly deferred
        # this exact change ("added back once 2.3 lands and file/meta-tag
        # return"), which is now true. default="dns" keeps every existing
        # caller's own behavior byte-for-byte unchanged with zero edits
        # needed anywhere else. No Python-side validation against
        # ('dns','file','meta_tag') added here -- the DB's own
        # ck_verified_domains_method CheckConstraint (2.3.e) already owns
        # that vocabulary; duplicating it here would be a second,
        # independently-maintained copy of the same rule (rule 11),
        # matching this project's own precedent of trusting a closed-
        # vocabulary CheckConstraint rather than re-validating in Python
        # (sources.type, documents.status -- neither is pre-checked here
        # either). An invalid value still fails safely: it raises
        # IntegrityError, caught below, and (see that except block's own
        # updated comment) re-raised unchanged since it is not the one
        # specific constraint this method already knows how to translate.
        #
        # Same-tenant double-claim is deliberately NOT special-cased into
        # an idempotent "return the existing row" -- PROJECT_SPEC.md's
        # Step 2.2 breakdown decision (e). ix_verified_domains_domain_
        # active_unique (Task 2.2.b) is scoped to `domain` alone, not
        # `(tenant_id, domain)`, so it cannot distinguish "this tenant's
        # own earlier pending claim" from "a different tenant's" -- and
        # this method doesn't try to either, matching this project's own
        # enumeration-oracle discipline elsewhere (never revealing to a
        # caller whether a collision is their own or someone else's).
        # Returning the old row silently would also contradict decision
        # (b)'s own "a fresh token per attempt, never reused" rule: a
        # caller asking to claim again is asking for a new attempt, not a
        # cache hit. A genuine "regenerate my own pending claim's token"
        # feature, if ever needed, is a different operation (an UPDATE on
        # the existing row, not a second INSERT) and is not this method's
        # job to build speculatively.
        verified_domain = VerifiedDomain(
            tenant_id=self.tenant_id,
            domain=domain,
            method=method,
            status="pending",
            verification_token=_generate_verification_token(),
        )
        self.session.add(verified_domain)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            # Narrowed to the specific constraint, not a blanket catch
            # (duplication check after 2.2.a/b/c, item B1): status is
            # hardcoded above and tenant_id is this repository's own
            # trusted identity; `method` became a real caller-supplied
            # parameter at Task 2.6.a, so ck_verified_domains_method is
            # now also reachable through this method's own inputs (an
            # invalid method string) -- correctly handled by the existing
            # structure below with no change needed: it re-raises
            # anything that isn't this one specific partial-unique-index
            # violation, unchanged, rather than silently mis-translating
            # a different constraint's own failure. But a bare
            # `except IntegrityError` doesn't know that, and would
            # silently mis-report any future violation (e.g. a new
            # caller-reachable constraint added later) as "already
            # claimed." Anything else re-raises completely unchanged, no
            # rollback performed for it either -- that path is not this
            # method's own expected case to handle, matching how every
            # other method in this codebase already leaves an unexpected
            # constraint violation for its own caller to deal with.
            if violated_constraint_name(exc) != "ix_verified_domains_domain_active_unique":
                raise
            # Rolled back explicitly, not left for the caller to discover:
            # Postgres aborts the whole transaction on a constraint
            # violation until rollback, so without this, any later query
            # on this same session (even one unrelated to domains) would
            # fail with "current transaction is aborted" -- confirmed live
            # against the real test database before relying on it, not
            # assumed from general Postgres knowledge. This makes the
            # method safe to call from any caller's own session lifecycle,
            # not reliant on it discarding the session afterward.
            await self.session.rollback()
            raise DomainAlreadyClaimedError(
                f"domain {domain!r} is already actively claimed"
            ) from exc
        return verified_domain

    async def list_domains(self) -> list[VerifiedDomain]:
        # No arguments beyond self: tenant scoping is automatic, matching
        # TenantScopedRepository.list_site_keys()'s own shape exactly.
        result = await self.session.execute(
            select(VerifiedDomain).where(VerifiedDomain.tenant_id == self.tenant_id)
        )
        return list(result.scalars().all())

    async def get_domain_by_id(self, domain_id: uuid.UUID) -> VerifiedDomain | None:
        # Same automatic-filter pattern as get_visitor_by_id/
        # get_conversation_by_id (app/tenancy/repository.py): a domain_id
        # belonging to a different tenant matches no row and returns
        # None, exactly like a domain_id that doesn't exist at all --
        # never distinguished, never raises.
        result = await self.session.execute(
            select(VerifiedDomain).where(
                VerifiedDomain.id == domain_id,
                VerifiedDomain.tenant_id == self.tenant_id,
            )
        )
        return result.scalar_one_or_none()

    async def confirm_verification(
        self,
        domain_id: uuid.UUID,
        clock: DateTimeClock = lambda: datetime.now(UTC),
    ) -> VerifiedDomain | None:
        # Task 2.2.e. Looks up through get_domain_by_id() -- reused, not
        # re-implemented -- so a domain_id belonging to a different tenant
        # returns None, the identical hostile-caller contract every other
        # get_*_by_id method on this class/TenantScopedRepository already
        # has, never distinguished from "doesn't exist at all." This also
        # means a wrong-tenant call never reaches the DNS check or the
        # state-machine logic below at all -- there is no row to check or
        # mutate once get_domain_by_id() has already returned None.
        domain = await self.get_domain_by_id(domain_id)
        if domain is None:
            return None

        # "verified" is a safe no-op, not a re-check -- docs/SPEC.md
        # §5.5's own "verified once... not re-checked" rule, literally:
        # returns the SAME row completely unchanged. check_dns_verification()
        # is never called for this case -- not merely "the result would be
        # discarded," the DNS query itself never happens (proven live by
        # 2.2.e's own tests via a monkeypatch that asserts zero calls, not
        # just inferred from the unchanged timestamp).
        if domain.status == "verified":
            return domain

        # "revoked" is rejected outright, not re-verified in place -- see
        # DomainRevokedError's own docstring for why. No DNS check, no
        # mutation.
        if domain.status == "revoked":
            raise DomainRevokedError(f"domain {domain_id} has been revoked")

        # Only "pending" reaches here and triggers a real check -- which
        # one depends on this row's own `method` (Task 2.6.a: previously
        # DNS was the only method that existed at all, so this was
        # unconditional; now dispatches on the row's own column). Each
        # branch calls its own check function by its plain, bare,
        # module-level name (not through a dict/registry indirection) --
        # deliberately, so test_ingest_repository.py's own existing
        # `monkeypatch.setattr(repository_module, "check_dns_verification",
        # ...)` tests keep working completely unchanged: a dict built
        # from these names at import time would capture the ORIGINAL
        # function object, making that exact monkeypatch style silently
        # no-op (confirmed by reading those tests before choosing this
        # shape, not assumed).
        if domain.method == "dns":
            verified = await check_dns_verification(domain.domain, domain.verification_token)
        elif domain.method == "file":
            verified = await check_file_verification(domain.domain, domain.verification_token)
        elif domain.method == "meta_tag":
            verified = await check_meta_tag_verification(
                domain.domain, domain.verification_token
            )
        else:
            # Unreachable through this class's own public inputs today --
            # ck_verified_domains_method (2.3.e) already rejects anything
            # outside ('dns', 'file', 'meta_tag') before a row with any
            # other value could ever exist. Fails loudly rather than
            # silently treating an unrecognized method as "not verified",
            # matching this project's own "fail closed, not silently" rule
            # for anything that should be structurally impossible.
            raise ValueError(f"unknown verification method {domain.method!r}")

        if verified:
            # Deliberately a Python-side clock() value, NOT func.now() --
            # found live, before committing to this, not assumed: unlike
            # mark_job_succeeded()/mark_job_failed() (app/ingest/queue.py),
            # which set their own timestamp columns to func.now() and then
            # return None, THIS method must hand back the row with
            # verified_at immediately readable by its own caller.
            # Assigning func.now() marks the attribute expired after
            # flush, and reading an expired attribute on an AsyncSession
            # object outside of a greenlet context raises
            # sqlalchemy.exc.MissingGreenlet -- reproduced live before
            # switching to this. A literal Python datetime has no such
            # problem: SQLAlchemy already knows its exact value, so
            # nothing is marked expired. clock is injectable, matching
            # mark_job_failed()'s own identical precedent, so a test can
            # assert the exact resulting verified_at against a fixed,
            # known value.
            domain.status = "verified"
            domain.verified_at = clock()
            # flush() only -- never commit() here, matching every other
            # method on this class/codebase without exception
            # (claim_domain(), enqueue(), create_db_connection(), and
            # queue.py's own mark_job_succeeded()/mark_job_failed()): the
            # caller owns the transaction boundary, not the repository.
            await self.session.flush()
            return domain

        # A clean "False" from whichever check function ran -- whether the
        # fetch/lookup resolved fine but the token simply isn't there yet,
        # or the fetch/lookup itself failed outright (NXDOMAIN for DNS;
        # a 404, an UnsafeFetchError/SSRF rejection, or a connection
        # failure for file/meta_tag, per each check function's own design,
        # Tasks 2.2.d/2.6.a) -- is structurally INDISTINGUISHABLE from
        # here, by construction: all of these collapse to the identical
        # boolean False before this method ever sees them. That is
        # deliberate, not a gap: none of these is an application error,
        # all mean exactly "not verified yet" from the tenant's own
        # perspective, and conflating them is what lets this branch stay
        # a single, simple no-op -- the row is returned completely
        # unchanged (still "pending", verified_at still null), never an
        # exception.
        return domain
