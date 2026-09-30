# backend/tests/test_ingest_repository.py
# Task 2.1.a: tests for db_connections' credential accessor
# (backend/app/ingest/repository.py). All live, against the real test
# database -- pgcrypto's own behavior cannot be proven any other way.
#
# Two groups:
#   - the encryption proof: a credential written through
#     create_db_connection() is genuinely encrypted at rest (a raw SQL
#     SELECT bypassing the accessor never returns the plaintext, and
#     neither does the ORM row's own default repr()/str()/vars() once
#     fetched), and is read back correctly through
#     get_decrypted_credentials() -- proving encryption is real, not just
#     declared.
#   - the tenant-isolation proof, matching TenantScopedRepository's own
#     hostile-caller discipline (test_tenancy_repository.py): a real
#     db_connection belonging to a different tenant, and a nonexistent id,
#     must produce the identical outcome (both raise), never distinguished.
import uuid

import pytest
import sqlalchemy as sa

from app.ingest.repository import IngestRepository
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Tenant
from tests.conftest import db_session

DISTINCTIVE_PASSWORD = "sUpEr-DiStInCtIvE-fake-password-123-never-leak-me"  # noqa: S105 (test fixture value, not a real secret)


@pytest.fixture
async def _seeded_tenants(reset_test_database):
    ids = {"tenant_a": uuid.uuid4(), "tenant_b": uuid.uuid4()}
    async with db_session() as session:
        session.add_all(
            [
                Tenant(id=ids["tenant_a"], name="Tenant A", status="active"),
                Tenant(id=ids["tenant_b"], name="Tenant B", status="active"),
            ]
        )
        await session.commit()
    yield ids


async def test_create_and_get_decrypted_credentials_round_trip(_seeded_tenants):
    ids = _seeded_tenants
    credentials = {"username": "dbuser", "password": DISTINCTIVE_PASSWORD}
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        db_connection = await repo.create_db_connection(
            host="db.example.internal", credentials=credentials
        )
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        decrypted = await repo.get_decrypted_credentials(db_connection_id)

    assert decrypted == credentials


async def test_raw_sql_select_never_returns_plaintext_credentials(_seeded_tenants):
    # Proves encryption is real, not just declared: bypassing
    # get_decrypted_credentials() entirely with a raw SQL SELECT of the
    # stored column must never return the plaintext password.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        db_connection = await repo.create_db_connection(
            host="db.example.internal",
            credentials={"username": "dbuser", "password": DISTINCTIVE_PASSWORD},
        )
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        raw = (
            await session.execute(
                sa.text("SELECT encrypted_credentials FROM db_connections WHERE id = :id"),
                {"id": db_connection_id},
            )
        ).scalar_one()

    assert DISTINCTIVE_PASSWORD.encode() not in bytes(raw)


async def test_fetched_db_connection_row_never_leaks_plaintext_via_repr_or_str(_seeded_tenants):
    # The genuine leak proof, using REAL ciphertext (not a hand-built fake
    # bytes value): once a real, encrypted row is fetched via a plain
    # ORM select, the plaintext must not appear in that instance's own
    # repr()/str()/vars() -- only ciphertext ever sits in
    # encrypted_credentials.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        db_connection = await repo.create_db_connection(
            host="db.example.internal",
            credentials={"username": "dbuser", "password": DISTINCTIVE_PASSWORD},
        )
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        result = await session.execute(
            sa.select(type(db_connection)).where(type(db_connection).id == db_connection_id)
        )
        fetched = result.scalar_one()

        assert DISTINCTIVE_PASSWORD.encode() not in repr(fetched).encode()
        assert DISTINCTIVE_PASSWORD.encode() not in str(fetched).encode()
        assert DISTINCTIVE_PASSWORD.encode() not in repr(vars(fetched)).encode()


async def test_get_decrypted_credentials_rejects_another_tenants_real_id(_seeded_tenants):
    # The hostile-caller case: tenant B's db_connection id is a real,
    # valid row -- it must still be rejected for a repository scoped to A.
    ids = _seeded_tenants
    async with db_session() as session:
        repo_b = IngestRepository(tenant_id=ids["tenant_b"], session=session)
        db_connection = await repo_b.create_db_connection(
            host="tenant-b-db.internal", credentials={"username": "u", "password": "p"}
        )
        await session.commit()
        db_connection_id = db_connection.id

    async with db_session() as session:
        repo_a = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError):
            await repo_a.get_decrypted_credentials(db_connection_id)


async def test_get_decrypted_credentials_raises_the_same_way_for_a_nonexistent_id(
    _seeded_tenants,
):
    # Must produce the identical outcome as the foreign-tenant case above
    # -- not a different error, not a crash -- otherwise the two are
    # distinguishable and a caller could learn "that id is real, just not
    # yours" (the same enumeration-oracle concern
    # TenantScopedRepository's own reads already guard against).
    ids = _seeded_tenants
    async with db_session() as session:
        repo = IngestRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError):
            await repo.get_decrypted_credentials(uuid.uuid4())
