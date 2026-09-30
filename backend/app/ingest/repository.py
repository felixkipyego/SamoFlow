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
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.ingest.models import DbConnection


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
        encrypted = (
            await self.session.execute(select(func.pgp_sym_encrypt(json.dumps(credentials), key)))
        ).scalar_one()
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
        result = await self.session.execute(
            select(func.pgp_sym_decrypt(DbConnection.encrypted_credentials, key)).where(
                DbConnection.id == db_connection_id,
                DbConnection.tenant_id == self.tenant_id,
            )
        )
        raw = result.scalar_one_or_none()
        if raw is None:
            raise ValueError(
                f"db_connection {db_connection_id} does not belong to tenant {self.tenant_id}"
            )
        return json.loads(raw)
