# backend/tests/test_dashboard_seed_cli.py
# Step 6.1: app/dashboard/seed_cli.py's own seed_owner() -- the CLI's
# getpass()-driven password prompt is exercised only via monkeypatching
# getpass.getpass() (never a real TTY in CI); the real DB-touching half
# (seed_owner() itself) is tested directly against the live test
# database.
#
# test_seed_owner_works_as_a_real_standalone_subprocess below is not
# redundant with the in-process tests above it: a REAL bug was found
# live, the hard way, running this command for real outside pytest --
# `create_tenant()`'s own flush() raised NoReferencedTableError
# (tenants.plan_id -> plans) because this module's own import graph
# never registered app.plans.models on Base.metadata. Every in-process
# test in this file was blind to it, because tests/conftest.py itself
# already imports app.all_models at module level for every test in this
# whole suite -- the exact masking this subprocess test exists to
# remove, matching test_alembic.py's own established "a subprocess run
# with a minimal env is the only thing that actually proves this" style.
import getpass
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi_users.db import SQLAlchemyUserDatabase

from app.dashboard.auth_backend import UserManager
from app.dashboard.models import User
from app.dashboard.passwords import password_helper
from app.dashboard.repository import get_membership_for_user
from app.dashboard.seed_cli import SeedOwnerError, _prompt_for_password, seed_owner
from app.tenancy.models import Tenant
from tests.conftest import VALID_ENV, db_session, minimal_subprocess_env

BACKEND_DIR = Path(__file__).resolve().parent.parent

_PASSWORD = "correct horse battery staple 1"  # noqa: S105


async def test_seed_owner_creates_tenant_user_and_membership(reset_test_database):
    user = await seed_owner("owner@example.com", "Acme Inc", _PASSWORD)

    async with db_session() as session:
        tenant = (
            await session.execute(sa.select(Tenant).where(Tenant.name == "Acme Inc"))
        ).scalar_one()
        membership = await get_membership_for_user(session, user.id)
        assert membership is not None
        assert membership.tenant_id == tenant.id
        assert membership.role == "owner"

        user_db = SQLAlchemyUserDatabase(session, User)
        real_user = await user_db.get(user.id)
        assert real_user.email == "owner@example.com"
        assert real_user.is_superuser is False
        manager = UserManager(user_db, password_helper)
        verified, _ = password_helper.verify_and_update(_PASSWORD, real_user.hashed_password)
        assert verified is True
        del manager  # constructed only to prove UserManager() wiring works too


async def test_seed_owner_with_platform_admin_sets_is_superuser(reset_test_database):
    user = await seed_owner(
        "felix@example.com", "Acme Inc", _PASSWORD, platform_admin=True
    )

    async with db_session() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        real_user = await user_db.get(user.id)
        assert real_user.is_superuser is True


async def test_seed_owner_reuses_an_existing_tenant_with_the_same_name(reset_test_database):
    first = await seed_owner("owner-a@example.com", "Shared Tenant", _PASSWORD)
    second = await seed_owner("owner-b@example.com", "Shared Tenant", _PASSWORD)

    async with db_session() as session:
        membership_a = await get_membership_for_user(session, first.id)
        membership_b = await get_membership_for_user(session, second.id)
        assert membership_a.tenant_id == membership_b.tenant_id


async def test_seed_owner_rejects_a_duplicate_email(reset_test_database):
    await seed_owner("owner@example.com", "Acme Inc", _PASSWORD)
    with pytest.raises(SeedOwnerError):
        await seed_owner("owner@example.com", "A Different Tenant", _PASSWORD)


async def test_seed_owner_rejects_a_password_shorter_than_the_minimum(reset_test_database):
    with pytest.raises(SeedOwnerError):
        await seed_owner("owner@example.com", "Acme Inc", "short")


def test_seed_owner_works_as_a_real_standalone_subprocess(reset_test_database):
    database_url = reset_test_database
    env = {
        **VALID_ENV,
        **minimal_subprocess_env(),
        "DATABASE_URL": database_url,
    }
    result = subprocess.run(  # noqa: S603 (args are fixed test-internal strings, not user input)
        [
            sys.executable,
            "-m",
            "app.dashboard.seed_cli",
            "--email",
            "subprocess-owner@example.com",
            "--tenant-name",
            "Subprocess Tenant",
        ],
        cwd=BACKEND_DIR,
        env=env,
        input="a-real-subprocess-password\na-real-subprocess-password\n",
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "seeded owner 'subprocess-owner@example.com'" in result.stdout


def test_prompt_for_password_rejects_a_mismatch(monkeypatch):
    values = iter(["password-one-long-enough", "password-two-long-enough"])
    monkeypatch.setattr(getpass, "getpass", lambda *_args, **_kwargs: next(values))
    with pytest.raises(SeedOwnerError):
        _prompt_for_password()


def test_prompt_for_password_accepts_a_match(monkeypatch):
    monkeypatch.setattr(getpass, "getpass", lambda *_args, **_kwargs: "same password twice")
    assert _prompt_for_password() == "same password twice"


def test_prompt_for_password_never_prints_the_password(monkeypatch, capsys):
    distinctive = "distinctive-password-value-never-printed"  # noqa: S105
    monkeypatch.setattr(getpass, "getpass", lambda *_args, **_kwargs: distinctive)
    _prompt_for_password()
    captured = capsys.readouterr()
    assert distinctive not in captured.out
    assert distinctive not in captured.err
