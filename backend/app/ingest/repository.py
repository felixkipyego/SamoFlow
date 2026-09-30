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
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ingest.models import DbConnection, Job


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
