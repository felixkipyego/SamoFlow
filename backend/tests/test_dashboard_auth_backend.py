# backend/tests/test_dashboard_auth_backend.py
# Alignment task (2026-10-15), named security follow-up: "A password
# reset, an owner re-seed, or removing platform_admin through the CLI
# must invalidate ALL of that user's existing sessions immediately."
# None of those three CLI commands exist today (see app/dashboard/
# auth_backend.py's own UserManager.on_after_update header comment for
# why building three new, unrequested CLI surfaces would itself be new
# feature work this same task rules out) -- this file proves the real,
# generic mechanism instead: fastapi_users.manager.BaseUserManager.
# update() is the one real path any future password-reset/role-change
# command would be built on (matching admin_cli.py/seed_cli.py's own
# established "built on the real path, not a second one" precedent),
# and UserManager.on_after_update hooks it directly.
#
# "Confirm logout invalidates the session server-side, not only the
# cookie" is already covered by test_dashboard_routes.py's own
# test_logout_revokes_the_session_and_subsequent_requests_are_401 (a
# replay from a FRESH client, plus a real DashboardSession row count of
# 0) -- not duplicated here.
import uuid

import sqlalchemy as sa
from fastapi_users import schemas
from fastapi_users.db import SQLAlchemyUserDatabase

from app.dashboard.auth_backend import UserManager
from app.dashboard.models import DashboardSession, User
from app.dashboard.passwords import password_helper
from app.dashboard.repository import create_membership
from app.tenancy.models import Tenant
from tests.conftest import db_session

_PASSWORD = "correct horse battery staple 1"  # noqa: S105 (test fixture value)


class _UserUpdate(schemas.BaseUserUpdate):
    pass


async def _create_user_with_sessions(session, email: str, *, session_count: int) -> User:
    user_db = SQLAlchemyUserDatabase(session, User)
    user = await user_db.create(
        {"email": email, "hashed_password": password_helper.hash(_PASSWORD)}
    )
    tenant = Tenant(id=uuid.uuid4(), name=f"Tenant for {email}", status="active")
    session.add(tenant)
    await session.flush()
    await create_membership(session, user_id=user.id, tenant_id=tenant.id)
    for _ in range(session_count):
        session.add(
            DashboardSession(
                token=uuid.uuid4().hex + uuid.uuid4().hex,
                user_id=user.id,
                last_seen_at=sa.func.now(),
            )
        )
    await session.commit()
    return user


async def _session_count(user_id: uuid.UUID) -> int:
    async with db_session() as session:
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(DashboardSession)
            .where(DashboardSession.user_id == user_id)
        )
        return result.scalar_one()


async def test_a_password_update_invalidates_every_existing_session(reset_test_database):
    async with db_session() as session:
        user = await _create_user_with_sessions(
            session, "reset-me@example.com", session_count=3
        )
        assert await _session_count(user.id) == 3

        user_db = SQLAlchemyUserDatabase(session, User)
        manager = UserManager(user_db, password_helper)
        await manager.update(
            _UserUpdate(password="a brand new password long enough"),  # noqa: S106 (test fixture value)
            user,
            safe=False,
        )
        await session.commit()

    assert await _session_count(user.id) == 0


async def test_an_is_superuser_change_invalidates_every_existing_session(reset_test_database):
    async with db_session() as session:
        user = await _create_user_with_sessions(
            session, "promote-me@example.com", session_count=2
        )
        assert await _session_count(user.id) == 2

        user_db = SQLAlchemyUserDatabase(session, User)
        manager = UserManager(user_db, password_helper)
        await manager.update(_UserUpdate(is_superuser=True), user, safe=False)
        await session.commit()

    assert await _session_count(user.id) == 0


async def test_removing_platform_admin_invalidates_every_existing_session(reset_test_database):
    # The exact case this task names by name: an existing platform_admin
    # with live sessions has the role REMOVED.
    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user = await user_db.create(
            {
                "email": "demoted-admin@example.com",
                "hashed_password": password_helper.hash(_PASSWORD),
                "is_superuser": True,
            }
        )
        tenant = Tenant(id=uuid.uuid4(), name="Tenant for demoted-admin", status="active")
        session.add(tenant)
        await session.flush()
        await create_membership(session, user_id=user.id, tenant_id=tenant.id)
        session.add(
            DashboardSession(
                token=uuid.uuid4().hex + uuid.uuid4().hex,
                user_id=user.id,
                last_seen_at=sa.func.now(),
            )
        )
        await session.commit()
        assert await _session_count(user.id) == 1

        manager = UserManager(user_db, password_helper)
        await manager.update(_UserUpdate(is_superuser=False), user, safe=False)
        await session.commit()

    assert await _session_count(user.id) == 0


async def test_an_unrelated_field_update_does_not_touch_sessions(reset_test_database):
    # Negative control: the hook must be targeted, not "invalidate on
    # every update" -- a change that is neither the password nor the
    # platform_admin flag (is_verified here) must leave existing
    # sessions alone.
    async with db_session() as session:
        user = await _create_user_with_sessions(
            session, "unrelated-change@example.com", session_count=1
        )

        user_db = SQLAlchemyUserDatabase(session, User)
        manager = UserManager(user_db, password_helper)
        await manager.update(_UserUpdate(is_verified=True), user, safe=False)
        await session.commit()

    assert await _session_count(user.id) == 1
