# backend/tests/test_dashboard_repository.py
# Step 6.1: app/dashboard/repository.py (Membership CRUD -- User and
# session persistence are fastapi-users' own adapters, exercised via
# test_dashboard_routes.py and test_dashboard_auth_backend.py instead,
# not re-tested here).
import uuid

import pytest
import sqlalchemy as sa
from fastapi_users.db import SQLAlchemyUserDatabase

from app.dashboard.models import Membership, User
from app.dashboard.repository import create_membership, get_membership_for_user
from app.tenancy.models import Tenant
from tests.conftest import db_session


@pytest.fixture
async def _seeded_tenant(reset_test_database) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Seeded Tenant", status="active"))
        await session.commit()
    return tenant_id


async def _create_user(session, email: str) -> User:
    user_db = SQLAlchemyUserDatabase(session, User)
    return await user_db.create({"email": email, "hashed_password": "not-a-real-hash"})


async def test_create_membership_and_get_membership_for_user(reset_test_database, _seeded_tenant):
    async with db_session() as session:
        user = await _create_user(session, "owner@example.com")

    async with db_session() as session:
        membership = await create_membership(session, user_id=user.id, tenant_id=_seeded_tenant)
        await session.commit()
        assert membership.role == "owner"

    async with db_session() as session:
        found = await get_membership_for_user(session, user.id)
        assert found is not None
        assert found.tenant_id == _seeded_tenant


async def test_get_membership_for_user_returns_none_for_a_user_with_no_membership(
    reset_test_database,
):
    async with db_session() as session:
        user = await _create_user(session, "owner@example.com")

    async with db_session() as session:
        assert await get_membership_for_user(session, user.id) is None


async def test_get_membership_for_user_returns_the_oldest_when_more_than_one_exists(
    reset_test_database,
):
    tenant_ids = [uuid.uuid4(), uuid.uuid4()]
    async with db_session() as session:
        session.add_all(
            [
                Tenant(id=tenant_ids[0], name="First Tenant", status="active"),
                Tenant(id=tenant_ids[1], name="Second Tenant", status="active"),
            ]
        )
        await session.flush()
        user = await _create_user(session, "owner@example.com")
        await create_membership(session, user_id=user.id, tenant_id=tenant_ids[0])
        await session.commit()

    async with db_session() as session:
        await create_membership(session, user_id=user.id, tenant_id=tenant_ids[1])
        await session.commit()

    async with db_session() as session:
        found = await get_membership_for_user(session, user.id)
        assert found.tenant_id == tenant_ids[0]


async def test_membership_role_check_constraint_rejects_a_non_owner_role(
    reset_test_database, _seeded_tenant
):
    async with db_session() as session:
        user = await _create_user(session, "owner@example.com")

    async with db_session() as session:
        session.add(Membership(user_id=user.id, tenant_id=_seeded_tenant, role="member"))
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()


async def test_membership_unique_constraint_rejects_a_duplicate_user_tenant_pair(
    reset_test_database, _seeded_tenant
):
    async with db_session() as session:
        user = await _create_user(session, "owner@example.com")
        await session.commit()
        await create_membership(session, user_id=user.id, tenant_id=_seeded_tenant)
        await session.commit()

    async with db_session() as session:
        session.add(Membership(user_id=user.id, tenant_id=_seeded_tenant, role="owner"))
        with pytest.raises(sa.exc.IntegrityError):
            await session.flush()


async def test_membership_cascades_on_user_delete(reset_test_database, _seeded_tenant):
    async with db_session() as session:
        user = await _create_user(session, "owner@example.com")
        await session.commit()
        await create_membership(session, user_id=user.id, tenant_id=_seeded_tenant)
        await session.commit()

    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        real_user = await user_db.get(user.id)
        await user_db.delete(real_user)

    async with db_session() as session:
        assert await get_membership_for_user(session, user.id) is None
