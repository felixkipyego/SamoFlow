# backend/tests/test_tenancy_repository.py
# Tasks 1.2.d/1.2.e: tests for the tenant-scoped repository core
# (backend/app/tenancy/repository.py).
#
# Three groups:
#   - structural tests (no database): the class cannot be constructed
#     without a tenant_id, rejects an explicit tenant_id=None, its fields
#     cannot be reassigned after construction (dataclasses'
#     FrozenInstanceError, not a hand-rolled scheme -- see repository.py's
#     own header comment for why), and create_tenant() is never a method on
#     the class (1.2.e).
#   - create_tenant()'s own test: it works standalone, with no tenant
#     scoping (there is no tenant_id yet for a brand-new tenant) (1.2.e).
#   - the security proof, against the real test database: two tenants (A
#     and B) are seeded with one site_key, one visitor and one conversation
#     each; a repository scoped to tenant A must return/create only tenant
#     A's rows, even when handed tenant B's real, valid ids (the
#     hostile-caller case) or an id that was never inserted at all (Task
#     1.2.f's duplication check: both must produce the identical outcome,
#     since distinguishing them would let a caller learn "that id is real,
#     just not yours" -- an enumeration oracle this repository exists to
#     prevent).
import dataclasses
import uuid

import pytest
import sqlalchemy as sa

from app import db
from app.db import Base
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor
from app.tenancy.repository import TenantScopedRepository, create_tenant
from tests.conftest import VALID_ENV, db_session, require_test_database, set_valid_env


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


def test_create_tenant_is_not_a_repository_method():
    # create_tenant() is a module-level function, deliberately never a
    # method on TenantScopedRepository (see repository.py's own header
    # comment for why: a brand-new tenant has no tenant_id yet to scope by).
    assert not hasattr(TenantScopedRepository, "create_tenant")


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

    async with db_session() as session:
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
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        site_keys = await repo.list_site_keys()

    assert [site_key.id for site_key in site_keys] == [ids["site_key_a"]]


async def test_get_conversation_by_id_rejects_another_tenants_real_id(_seeded_tenants):
    # The hostile-caller case: tenant B's conversation id is a real, valid
    # row -- it must still come back None for a repository scoped to A.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_conversation_by_id(ids["conversation_b"])

    assert result is None


async def test_get_conversation_by_id_returns_none_for_a_nonexistent_id(_seeded_tenants):
    # Must produce the same outcome as the foreign-tenant case above -- not a
    # different error, not a crash -- otherwise the two are distinguishable
    # and a caller could learn "that id is real, just not yours".
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_conversation_by_id(uuid.uuid4())

    assert result is None


async def test_get_conversation_by_id_returns_this_tenants_own_row(_seeded_tenants):
    # Proves the mechanism isn't just blocking everything.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_conversation_by_id(ids["conversation_a"])

    assert result is not None
    assert result.cid == ids["conversation_a"]


async def test_create_tenant_creates_a_real_row(_seeded_tenants):
    # No tenant scoping applies (there's no tenant_id to scope by yet): a
    # plain function taking a session directly, not a repository.
    async with db_session() as session:
        tenant = await create_tenant(session, name="Brand New Tenant", status="active")
        await session.commit()
        tenant_id = tenant.id

    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=tenant_id, session=session)
        result = await repo.get_tenant()

    assert result.id == tenant_id
    assert result.name == "Brand New Tenant"


async def test_get_tenant_returns_the_repositorys_own_tenant(_seeded_tenants):
    # Confirms this in a two-tenant database: a repository scoped to A
    # returns A, never B, even though B is a real row right next to it.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_tenant()

    assert result.id == ids["tenant_a"]
    assert result.name == "Tenant A"


async def test_create_site_key_sets_tenant_id_automatically(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        site_key = await repo.create_site_key(
            key="pk_live_new_key", allowed_origins=["https://a.example"], environment="production"
        )

    assert site_key.tenant_id == ids["tenant_a"]
    assert site_key.status == "draft"


async def test_create_visitor_sets_tenant_id_automatically(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        visitor = await repo.create_visitor(
            site_key_id=ids["site_key_a"], secret_hash="new-hash"  # noqa: S106
        )

    assert visitor.tenant_id == ids["tenant_a"]
    assert visitor.site_key_id == ids["site_key_a"]


async def test_create_visitor_rejects_a_foreign_tenants_site_key_id(_seeded_tenants):
    # Hostile-caller case: tenant B's site_key_id is a real, valid row, but
    # does not belong to tenant A -- must be rejected, not silently accepted.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError, match="does not belong to tenant"):
            await repo.create_visitor(
                site_key_id=ids["site_key_b"], secret_hash="new-hash"  # noqa: S106
            )


async def test_create_visitor_rejects_a_nonexistent_site_key_id(_seeded_tenants):
    # Must raise the identical error as the foreign-tenant case above.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError, match="does not belong to tenant"):
            await repo.create_visitor(
                site_key_id=uuid.uuid4(), secret_hash="new-hash"  # noqa: S106
            )


async def test_get_visitor_by_id_rejects_another_tenants_real_id(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_id(ids["visitor_b"])

    assert result is None


async def test_get_visitor_by_id_returns_none_for_a_nonexistent_id(_seeded_tenants):
    # Must produce the same outcome as the foreign-tenant case above.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_id(uuid.uuid4())

    assert result is None


async def test_get_visitor_by_id_returns_this_tenants_own_row(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        result = await repo.get_visitor_by_id(ids["visitor_a"])

    assert result is not None
    assert result.vid == ids["visitor_a"]


async def test_create_conversation_sets_tenant_id_automatically(_seeded_tenants):
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        conversation = await repo.create_conversation(vid=ids["visitor_a"], title="hello")

    assert conversation.tenant_id == ids["tenant_a"]
    assert conversation.vid == ids["visitor_a"]


async def test_create_conversation_rejects_a_foreign_tenants_vid(_seeded_tenants):
    # Hostile-caller case: tenant B's vid is a real, valid row, but does not
    # belong to tenant A -- must be rejected, not silently accepted.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError, match="does not belong to tenant"):
            await repo.create_conversation(vid=ids["visitor_b"])


async def test_create_conversation_rejects_a_nonexistent_vid(_seeded_tenants):
    # Must raise the identical error as the foreign-tenant case above.
    ids = _seeded_tenants
    async with db_session() as session:
        repo = TenantScopedRepository(tenant_id=ids["tenant_a"], session=session)
        with pytest.raises(ValueError, match="does not belong to tenant"):
            await repo.create_conversation(vid=uuid.uuid4())
