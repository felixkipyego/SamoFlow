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

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ingest.models import DbConnection, Job, VerifiedDomain


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
        job = Job(
            tenant_id=self.tenant_id,
            source_id=source_id,
            job_type=job_type,
            status="pending",
            max_attempts=get_settings().job_max_attempts,
            payload=payload or {},
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def claim_domain(self, domain: str) -> VerifiedDomain:
        # Task 2.2.c. A method here, not a standalone function, for the
        # identical reason enqueue() above is one: claiming always happens
        # on behalf of an already-known tenant. Always starts "pending"
        # with a fresh token -- this method never verifies anything itself
        # (2.2.e's own job) and never returns an existing row.
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
            method="dns",
            status="pending",
            verification_token=_generate_verification_token(),
        )
        self.session.add(verified_domain)
        try:
            await self.session.flush()
        except IntegrityError as exc:
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
