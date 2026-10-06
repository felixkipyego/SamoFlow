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
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import DateTimeClock, violated_constraint_name
from app.ingest.domain_verification import (
    check_dns_verification,
    check_file_verification,
    check_meta_tag_verification,
)
from app.ingest.models import AuditLog, DbConnection, Job, VerifiedDomain

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
VALID_JOB_TYPES: frozenset[str] = frozenset({"ingest_url", "ingest_crawl"})


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
    """


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
