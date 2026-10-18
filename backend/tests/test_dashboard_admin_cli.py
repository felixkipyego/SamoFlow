# backend/tests/test_dashboard_admin_cli.py
# Step 6.1: app/dashboard/admin_cli.py -- the platform-admin CLI commands
# (create-tenant, assign-plan, issue-site-key, revoke-site-key,
# revoke-domain, seed-plans), against the live test database.
#
# reset-password/set-platform-admin/unset-platform-admin (alignment task,
# 2026-10-15) are tested below too: built on UserManager.update()
# specifically, so the session-invalidation hook (on_after_update(),
# app/dashboard/auth_backend.py) fires for free -- proven directly here,
# not merely assumed from reading the hook's own already-passing tests.
import getpass
import uuid
from argparse import Namespace

import pytest
import sqlalchemy as sa
from fastapi_users.db import SQLAlchemyUserDatabase

from app.dashboard.admin_cli import (
    AdminCliError,
    cmd_assign_plan,
    cmd_cleanup_smoke_test_leftovers,
    cmd_create_tenant,
    cmd_delete_tenant,
    cmd_delete_user,
    cmd_issue_site_key,
    cmd_reset_password,
    cmd_revoke_domain,
    cmd_revoke_site_key,
    cmd_seed_plans,
    cmd_set_platform_admin,
    cmd_unset_platform_admin,
)
from app.dashboard.models import DashboardSession, User
from app.dashboard.passwords import password_helper
from app.ingest.models import VerifiedDomain
from app.plans.models import Plan
from app.plans.schemas import PlanLimits
from app.tenancy.models import SiteKey, Tenant
from tests.conftest import db_session


async def test_create_tenant_creates_a_real_active_tenant(reset_test_database):
    await cmd_create_tenant(Namespace(name="Acme Inc"))

    async with db_session() as session:
        tenant = (
            await session.execute(sa.select(Tenant).where(Tenant.name == "Acme Inc"))
        ).scalar_one()
        assert tenant.status == "active"


async def test_seed_plans_creates_the_two_pilot_plans(reset_test_database):
    await cmd_seed_plans(Namespace())

    async with db_session() as session:
        plans = (await session.execute(sa.select(Plan))).scalars().all()
        names_to_quota = {p.name: PlanLimits.model_validate(p.limits) for p in plans}
        assert names_to_quota["Starter"].ai_message_quota_per_month == 10_000
        assert names_to_quota["Growth"].ai_message_quota_per_month == 25_000


async def test_seed_plans_is_idempotent(reset_test_database):
    await cmd_seed_plans(Namespace())
    await cmd_seed_plans(Namespace())

    async with db_session() as session:
        count = (await session.execute(sa.select(sa.func.count()).select_from(Plan))).scalar_one()
        assert count == 2


async def test_assign_plan_sets_tenant_plan_id(reset_test_database):
    await cmd_seed_plans(Namespace())
    async with db_session() as session:
        tenant = Tenant(name="Acme Inc", status="active")
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
        plan = (await session.execute(sa.select(Plan).where(Plan.name == "Starter"))).scalar_one()
        await session.commit()

    await cmd_assign_plan(Namespace(tenant_id=tenant_id, plan_id=plan.id))

    async with db_session() as session:
        refreshed = (
            await session.execute(sa.select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one()
        assert refreshed.plan_id == plan.id


async def test_assign_plan_rejects_an_unknown_tenant(reset_test_database):
    await cmd_seed_plans(Namespace())
    async with db_session() as session:
        plan = (await session.execute(sa.select(Plan).where(Plan.name == "Starter"))).scalar_one()
    with pytest.raises(AdminCliError):
        await cmd_assign_plan(Namespace(tenant_id=uuid.uuid4(), plan_id=plan.id))


async def test_issue_site_key_creates_a_draft_key_with_normalized_origins(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Acme Inc", status="active")
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id

    await cmd_issue_site_key(
        Namespace(
            tenant_id=tenant_id,
            allowed_origin=["HTTPS://Widget.Example:443/"],
            environment="production",
            status="draft",
        )
    )

    async with db_session() as session:
        site_key = (
            await session.execute(sa.select(SiteKey).where(SiteKey.tenant_id == tenant_id))
        ).scalar_one()
        assert site_key.key.startswith("pk_live_")
        assert site_key.allowed_origins == ["https://widget.example"]
        assert site_key.status == "draft"


async def test_issue_site_key_can_set_status_live_directly(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Acme Inc", status="active")
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id

    await cmd_issue_site_key(
        Namespace(
            tenant_id=tenant_id,
            allowed_origin=["https://widget.example"],
            environment="production",
            status="live",
        )
    )

    async with db_session() as session:
        site_key = (
            await session.execute(sa.select(SiteKey).where(SiteKey.tenant_id == tenant_id))
        ).scalar_one()
        assert site_key.status == "live"


async def test_issue_site_key_rejects_a_wildcard_origin(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Acme Inc", status="active")
        session.add(tenant)
        await session.commit()
        tenant_id = tenant.id

    with pytest.raises(AdminCliError):
        await cmd_issue_site_key(
            Namespace(
                tenant_id=tenant_id,
                allowed_origin=["https://*.example"],
                environment="production",
                status="draft",
            )
        )


async def test_revoke_site_key_suspends_it(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Acme Inc", status="active")
        session.add(tenant)
        await session.flush()
        site_key = SiteKey(
            tenant_id=tenant.id,
            key="pk_live_test",
            allowed_origins=["https://widget.example"],
            environment="production",
            status="live",
        )
        session.add(site_key)
        await session.commit()
        tenant_id, site_key_id = tenant.id, site_key.id

    await cmd_revoke_site_key(Namespace(tenant_id=tenant_id, site_key_id=site_key_id))

    async with db_session() as session:
        refreshed = (
            await session.execute(sa.select(SiteKey).where(SiteKey.id == site_key_id))
        ).scalar_one()
        assert refreshed.status == "suspended"


async def test_revoke_site_key_rejects_another_tenants_key(reset_test_database):
    async with db_session() as session:
        tenant_a = Tenant(name="Tenant A", status="active")
        tenant_b = Tenant(name="Tenant B", status="active")
        session.add_all([tenant_a, tenant_b])
        await session.flush()
        site_key = SiteKey(
            tenant_id=tenant_a.id,
            key="pk_live_test",
            allowed_origins=[],
            environment="production",
            status="live",
        )
        session.add(site_key)
        await session.commit()
        tenant_b_id, site_key_id = tenant_b.id, site_key.id

    with pytest.raises(AdminCliError):
        await cmd_revoke_site_key(Namespace(tenant_id=tenant_b_id, site_key_id=site_key_id))


async def test_revoke_domain_revokes_a_real_domain(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Acme Inc", status="active")
        session.add(tenant)
        await session.flush()
        domain = VerifiedDomain(
            tenant_id=tenant.id,
            domain="example.com",
            status="verified",
            method="dns",
            verification_token="tok",  # noqa: S106 (test fixture value, not a real secret)
        )
        session.add(domain)
        await session.commit()
        domain_id = domain.id

    await cmd_revoke_domain(Namespace(domain_id=domain_id, actor="admin@example.com"))

    async with db_session() as session:
        refreshed = (
            await session.execute(sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_id))
        ).scalar_one()
        assert refreshed.status == "revoked"


async def test_revoke_domain_rejects_an_unknown_id(reset_test_database):
    with pytest.raises(AdminCliError):
        await cmd_revoke_domain(Namespace(domain_id=uuid.uuid4(), actor="admin@example.com"))


# --- reset-password / set-platform-admin / unset-platform-admin ------------

_OLD_PASSWORD = "correct horse battery staple 1"  # noqa: S105
_NEW_PASSWORD = "a brand new password long enough"  # noqa: S105


async def _seed_user_with_a_session(email: str, *, is_superuser: bool = False) -> uuid.UUID:
    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user = await user_db.create(
            {
                "email": email,
                "hashed_password": password_helper.hash(_OLD_PASSWORD),
                "is_superuser": is_superuser,
            }
        )
        session.add(
            DashboardSession(
                token=uuid.uuid4().hex + uuid.uuid4().hex,
                user_id=user.id,
                last_seen_at=sa.func.now(),
            )
        )
        await session.commit()
        return user.id


async def _session_count(user_id: uuid.UUID) -> int:
    async with db_session() as session:
        result = await session.execute(
            sa.select(sa.func.count())
            .select_from(DashboardSession)
            .where(DashboardSession.user_id == user_id)
        )
        return result.scalar_one()


async def test_reset_password_changes_the_hash_and_invalidates_sessions(
    reset_test_database, monkeypatch
):
    user_id = await _seed_user_with_a_session("reset-me@example.com")
    assert await _session_count(user_id) == 1

    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: _NEW_PASSWORD)
    await cmd_reset_password(Namespace(email="reset-me@example.com"))

    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        real_user = await user_db.get(user_id)
        verified, _ = password_helper.verify_and_update(_NEW_PASSWORD, real_user.hashed_password)
        assert verified is True
    assert await _session_count(user_id) == 0


async def test_reset_password_for_an_unknown_email_raises(reset_test_database, monkeypatch):
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: _NEW_PASSWORD)
    with pytest.raises(AdminCliError):
        await cmd_reset_password(Namespace(email="nobody@example.com"))


async def test_reset_password_rejects_a_too_short_new_password(reset_test_database, monkeypatch):
    user_id = await _seed_user_with_a_session("short-pw@example.com")
    monkeypatch.setattr(getpass, "getpass", lambda *_a, **_k: "short")
    with pytest.raises(AdminCliError):
        await cmd_reset_password(Namespace(email="short-pw@example.com"))
    # Rejected before any update -- the session must still be alive.
    assert await _session_count(user_id) == 1


async def test_set_platform_admin_grants_the_role_and_invalidates_sessions(reset_test_database):
    user_id = await _seed_user_with_a_session("promote-me@example.com")
    assert await _session_count(user_id) == 1

    await cmd_set_platform_admin(Namespace(email="promote-me@example.com"))

    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        real_user = await user_db.get(user_id)
        assert real_user.is_superuser is True
    assert await _session_count(user_id) == 0


async def test_unset_platform_admin_revokes_the_role_and_invalidates_sessions(
    reset_test_database,
):
    user_id = await _seed_user_with_a_session("demote-me@example.com", is_superuser=True)
    assert await _session_count(user_id) == 1

    await cmd_unset_platform_admin(Namespace(email="demote-me@example.com"))

    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        real_user = await user_db.get(user_id)
        assert real_user.is_superuser is False
    assert await _session_count(user_id) == 0


async def test_set_platform_admin_for_an_unknown_email_raises(reset_test_database):
    with pytest.raises(AdminCliError):
        await cmd_set_platform_admin(Namespace(email="nobody@example.com"))


# --- delete-tenant / delete-user (ops/deploy/smoke-prod-stack.sh cleanup
# follow-up, advisor review, 2026-10-17) -----------------------------------


async def test_delete_tenant_requires_confirm(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Confirm Me", status="active")
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
        await session.commit()

    with pytest.raises(AdminCliError):
        await cmd_delete_tenant(Namespace(tenant_id=tenant_id, confirm=False))

    # Rejected before any delete -- the tenant must still be there.
    async with db_session() as session:
        still_there = (
            await session.execute(sa.select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        assert still_there is not None


async def test_delete_tenant_cascades_to_its_site_keys(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Cascade Me", status="active")
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
        site_key = SiteKey(
            tenant_id=tenant_id,
            key="pk_live_cascade-test-key",
            allowed_origins=["https://cascade.example.com"],
            environment="production",
            status="live",
        )
        session.add(site_key)
        await session.flush()
        site_key_id = site_key.id
        await session.commit()

    await cmd_delete_tenant(Namespace(tenant_id=tenant_id, confirm=True))

    async with db_session() as session:
        tenant_gone = (
            await session.execute(sa.select(Tenant).where(Tenant.id == tenant_id))
        ).scalar_one_or_none()
        assert tenant_gone is None
        site_key_gone = (
            await session.execute(sa.select(SiteKey).where(SiteKey.id == site_key_id))
        ).scalar_one_or_none()
        assert site_key_gone is None


async def test_delete_tenant_on_an_unknown_id_is_a_noop_not_an_error(reset_test_database):
    await cmd_delete_tenant(Namespace(tenant_id=uuid.uuid4(), confirm=True))


async def test_delete_tenant_is_idempotent(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="Delete Twice", status="active")
        session.add(tenant)
        await session.flush()
        tenant_id = tenant.id
        await session.commit()

    await cmd_delete_tenant(Namespace(tenant_id=tenant_id, confirm=True))
    # Second call against the now-already-deleted id: still no raise.
    await cmd_delete_tenant(Namespace(tenant_id=tenant_id, confirm=True))


async def test_delete_user_requires_confirm(reset_test_database):
    user_id = await _seed_user_with_a_session("confirm-me@example.com")

    with pytest.raises(AdminCliError):
        await cmd_delete_user(Namespace(email="confirm-me@example.com", confirm=False))

    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        still_there = await user_db.get(user_id)
        assert still_there is not None


async def test_delete_user_deletes_the_row_and_invalidates_sessions(reset_test_database):
    user_id = await _seed_user_with_a_session("delete-me@example.com")
    assert await _session_count(user_id) == 1

    await cmd_delete_user(Namespace(email="delete-me@example.com", confirm=True))

    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        gone = await user_db.get(user_id)
        assert gone is None
    # ondelete="CASCADE" on dashboard_sessions.user_id -- not a separate
    # invalidate_all_sessions_for_user() call -- proven directly, not
    # assumed from reading the FK definition alone.
    assert await _session_count(user_id) == 0


async def test_delete_user_on_an_unknown_email_is_a_noop_not_an_error(reset_test_database):
    await cmd_delete_user(Namespace(email="nobody-to-delete@example.com", confirm=True))


async def test_delete_user_is_idempotent(reset_test_database):
    await _seed_user_with_a_session("delete-twice@example.com")

    await cmd_delete_user(Namespace(email="delete-twice@example.com", confirm=True))
    # Second call against the now-already-deleted email: still no raise.
    await cmd_delete_user(Namespace(email="delete-twice@example.com", confirm=True))


# --- cleanup-smoke-test-leftovers (ops/deploy/smoke-prod-stack.sh
# SIGKILL-recovery follow-up, advisor review, 2026-10-17) ------------------


async def test_cleanup_smoke_test_leftovers_requires_confirm(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="smoke-test-leftover-1", status="active")
        session.add(tenant)
        await session.commit()

    with pytest.raises(AdminCliError):
        await cmd_cleanup_smoke_test_leftovers(Namespace(confirm=False))

    async with db_session() as session:
        still_there = (
            await session.execute(
                sa.select(Tenant).where(Tenant.name == "smoke-test-leftover-1")
            )
        ).scalar_one_or_none()
        assert still_there is not None


async def test_cleanup_smoke_test_leftovers_removes_only_the_smoke_test_pattern(
    reset_test_database,
):
    async with db_session() as session:
        session.add_all(
            [
                Tenant(name="smoke-test-leftover-2", status="active"),
                Tenant(name="smoke-test-leftover-3", status="active"),
                Tenant(name="Real Pilot Tenant", status="active"),
            ]
        )
        await session.commit()
    await _seed_user_with_a_session("smoke-test-leftover-user@example.com")
    await _seed_user_with_a_session("real-owner@example.com")

    await cmd_cleanup_smoke_test_leftovers(Namespace(confirm=True))

    async with db_session() as session:
        remaining_tenants = (
            (await session.execute(sa.select(Tenant.name))).scalars().all()
        )
        assert set(remaining_tenants) == {"Real Pilot Tenant"}
        user_db = SQLAlchemyUserDatabase(session, User)
        assert await user_db.get_by_email("smoke-test-leftover-user@example.com") is None
        real_owner = await user_db.get_by_email("real-owner@example.com")
        assert real_owner is not None


async def test_cleanup_smoke_test_leftovers_is_idempotent(reset_test_database):
    async with db_session() as session:
        tenant = Tenant(name="smoke-test-leftover-4", status="active")
        session.add(tenant)
        await session.commit()

    await cmd_cleanup_smoke_test_leftovers(Namespace(confirm=True))
    # Second call with nothing left to clean up: still no raise.
    await cmd_cleanup_smoke_test_leftovers(Namespace(confirm=True))


async def test_cleanup_smoke_test_leftovers_on_an_empty_database_is_a_noop(
    reset_test_database,
):
    await cmd_cleanup_smoke_test_leftovers(Namespace(confirm=True))
