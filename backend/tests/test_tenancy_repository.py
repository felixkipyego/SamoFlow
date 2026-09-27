# backend/tests/test_tenancy_repository.py
# Task 1.2.d: tests for the tenant-scoped repository core
# (backend/app/tenancy/repository.py).
#
# Two groups:
#   - structural tests (no database): the class cannot be constructed
#     without a tenant_id, rejects an explicit tenant_id=None, and its
#     fields cannot be reassigned after construction (dataclasses'
#     FrozenInstanceError, not a hand-rolled scheme -- see repository.py's
#     own header comment for why).
#   - the security proof, against the real test database: two tenants (A
#     and B) are seeded with one site_key and one conversation each; a
#     repository scoped to tenant A must return only tenant A's rows, even
#     when handed tenant B's real, valid ids -- the hostile-caller case.
import dataclasses
import uuid
from contextlib import aclosing

import pytest
import sqlalchemy as sa

from app import db
from app.db import Base
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor
from app.tenancy.repository import TenantScopedRepository
from tests.conftest import VALID_ENV, require_test_database, set_valid_env


def test_repository_requires_a_tenant_id():
    with pytest.raises(TypeError):
        TenantScopedRepository(session=None)  # missing tenant_id


def test_repository_rejects_none_tenant_id():
    with pytest.raises(ValueError):
        TenantScopedRepository(tenant_id=None, session=None)


def test_repository_tenant_id_cannot_be_reassigned():
    # dataclasses.FrozenInstanceError (a subclass of AttributeError): the
    # class-level mechanism, not a naming convention, is what prevents this.
    repo = TenantScopedRepository(tenant_id=uuid.uuid4(), session=None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        repo.tenant_id = uuid.uuid4()


@pytest.fixture
async def _seeded_tenants(monkeypatch):
    # Same per-test cache-clear/dispose plumbing as test_db.py's
    # _fresh_engine (pytest-asyncio's function-scoped event loop means a
    # process-lifetime-singleton engine cannot be reused across tests).
    database_url = require_test_database()
    set_valid_env(monkeypatch, VALID_ENV, DATABASE_URL=database_url)
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()

    # Schema via Base.metadata directly (not the real Alembic migration):
    # 1.2.c already proves the migration produces this schema; this
    # fixture's job is only to get a real Postgres schema in place to prove
    # the repository's tenant-isolation behavior, so create_all is the
    # simpler, standard choice here (rule 11).
    sync_engine = sa.create_engine(database_url, poolclass=sa.pool.NullPool)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    ids = {
        "tenant_a": uuid.uuid4(),
        "tenant_b": uuid.uuid4(),
        "site_key_a": uuid.uuid4(),
        "site_key_b": uuid.uuid4(),
        "visitor_a": uuid.uuid4(),
        "visitor_b": uuid.uuid4(),
        "conversation_a": uuid.uuid4(),
        "conversation_b": uuid.uuid4(),
    }

    async with aclosing(db.get_db_session()) as session_gen:
        session = await anext(session_gen)
        session.add_all(
            [
                Tenant(id=ids["tenant_a"], name="Tenant A", status="active"),
                Tenant(id=ids["tenant_b"], name="Tenant B", status="active"),
            ]
        )
        await session.flush()
        session.add_all(
            [
                SiteKey(
                    id=ids["site_key_a"],
                    key="pk_live_tenant_a",
                    tenant_id=ids["tenant_a"],
                    environment="production",
                    status="draft",
                ),
                SiteKey(
                    id=ids["site_key_b"],
                    key="pk_live_tenant_b",
                    tenant_id=ids["tenant_b"],
                    environment="production",
                    status="draft",
                ),
            ]
        )
        session.add_all(
            [
                Visitor(
                    vid=ids["visitor_a"],
                    tenant_id=ids["tenant_a"],
                    site_key_id=ids["site_key_a"],
                    secret_hash="hash-a",  # noqa: S106 (test fixture value, not a real secret)
                ),
                Visitor(
                    vid=ids["visitor_b"],
                    tenant_id=ids["tenant_b"],
                    site_key_id=ids["site_key_b"],
                    secret_hash="hash-b",  # noqa: S106 (test fixture value, not a real secret)
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Conversation(
                    cid=ids["conversation_a"], tenant_id=ids["tenant_a"], vid=ids["visitor_a"]
                ),
                Conversation(
                    cid=ids["conversation_b"], tenant_id=ids["tenant_b"], vid=ids["visitor_b"]
                ),
            ]
        )
        await session.commit()

    yield ids

    await db.get_engine().dispose()
    db.get_engine.cache_clear()
    db._session_factory.cache_clear()


async def test_list_site_keys_returns_only_this_tenants_site_key(_seeded_tenants):
    ids = _seeded_tenants
    async with aclosing(db.get_db_session()) as session_gen:
        session = await anext(session_gen)
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        site_keys = await repo.list_site_keys()

    assert [site_key.id for site_key in site_keys] == [ids["site_key_a"]]


async def test_get_conversation_by_id_rejects_another_tenants_real_id(_seeded_tenants):
    # The hostile-caller case: tenant B's conversation id is a real, valid
    # row -- it must still come back None for a repository scoped to A.
    ids = _seeded_tenants
    async with aclosing(db.get_db_session()) as session_gen:
        session = await anext(session_gen)
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_conversation_by_id(ids["conversation_b"])

    assert result is None


async def test_get_conversation_by_id_returns_this_tenants_own_row(_seeded_tenants):
    # Proves the mechanism isn't just blocking everything.
    ids = _seeded_tenants
    async with aclosing(db.get_db_session()) as session_gen:
        session = await anext(session_gen)
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_conversation_by_id(ids["conversation_a"])

    assert result is not None
    assert result.cid == ids["conversation_a"]
