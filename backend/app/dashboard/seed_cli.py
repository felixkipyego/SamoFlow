# backend/app/dashboard/seed_cli.py
# Step 6.1: `python -m app.dashboard.seed_cli` -- seeds the pilot tenant's
# owner (this task's own direct instruction: "a CLI command that seeds the
# pilot tenant's owner (no default password, never logged)"), and
# optionally promotes that same account to platform_admin (Felix's own
# 2026-10-15 correction: "CLI admin commands... seed an owner" -- this is
# that command; `--platform-admin` is the one extra flag it needs so
# Felix himself can also become the first platform_admin through the
# identical, already-built mechanism, rather than a second script).
#
# The password is ALWAYS read interactively via getpass.getpass() -- never
# a --password flag, never a default, never read from an environment
# variable. This is deliberate, not an oversight: a CLI flag or env var
# would put the real password in this process's own argv (visible to any
# other process on the host via `ps`, and written to the operator's own
# shell history the moment they type the command), or require an operator
# to type a plaintext password into a shell history/env file at all.
# getpass.getpass() reads directly from the controlling terminal, is never
# echoed, and is never logged anywhere in this module -- grep this file:
# the only thing ever printed or logged about the password is the fact
# that one was collected, never its value or length.
#
# Built on the SAME fastapi-users UserManager.create() this project's own
# login path uses (app/dashboard/auth_backend.py) -- real argon2id
# hashing and the same password-length validation (UserManager.
# validate_password()), not a second, parallel user-creation path.
#
# Invoked via the `migrate` Compose service (deploy/docker-compose.yml, as
# a one-off `docker compose run` command, not part of `up`), reusing that
# service's own environment/network wiring rather than a new one -- see
# entrypoint.sh's new "seed-owner" case:
#   docker compose --env-file .env -f deploy/docker-compose.yml run --rm \
#     migrate seed-owner --email owner@example.com --tenant-name "Acme Inc"
import argparse
import asyncio
import getpass
import sys

from fastapi_users import exceptions, schemas
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy import select

# For the side effect of registering every domain's tables on Base.
# metadata (app/all_models.py's own header comment) -- without this, a
# real flush() here raises NoReferencedTableError the moment SQLAlchemy's
# ORM mapper configuration step needs to resolve tenants.plan_id's own FK
# to a `plans` table nothing in this standalone CLI's own import graph
# ever mapped (confirmed live: a real, reproduced failure, not assumed).
from app import all_models  # noqa: F401
from app.cli import configure_cli_logging
from app.dashboard.auth_backend import UserManager
from app.dashboard.models import User
from app.dashboard.passwords import password_helper
from app.dashboard.repository import create_membership
from app.db import _session_factory
from app.tenancy.models import Tenant
from app.tenancy.repository import create_tenant


class SeedOwnerError(Exception):
    """Raised for any expected failure (tenant/user state problems,
    password validation) -- message is always safe to print directly,
    never contains the password.
    """


class _UserCreate(schemas.BaseUserCreate):
    pass


async def _get_or_create_tenant(session, tenant_name: str) -> Tenant:
    result = await session.execute(select(Tenant).where(Tenant.name == tenant_name))
    tenant = result.scalar_one_or_none()
    if tenant is not None:
        return tenant
    return await create_tenant(session, name=tenant_name, status="active")


def _prompt_for_password() -> str:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise SeedOwnerError("passwords did not match")
    return password


async def seed_owner(
    email: str, tenant_name: str, password: str, *, platform_admin: bool = False
) -> User:
    async with _session_factory()() as session:
        # Constructed directly, not via app.dashboard.auth_backend's own
        # get_user_db() -- that function is an async-generator FastAPI
        # dependency (manages its own request-scoped lifecycle via
        # Depends()); outside a request, this is the same adapter with
        # none of that machinery needed.
        manager = UserManager(SQLAlchemyUserDatabase(session, User), password_helper)

        tenant = await _get_or_create_tenant(session, tenant_name)
        try:
            # safe=False: is_superuser is a real, deliberate input here --
            # this is a trusted, server-side-only operator tool, not
            # user-facing input (BaseUserManager.create()'s own docstring:
            # "safe=True... sensitive values like is_superuser... will be
            # ignored").
            user = await manager.create(
                _UserCreate(email=email, password=password, is_superuser=platform_admin),
                safe=False,
            )
        except exceptions.UserAlreadyExists as exc:
            raise SeedOwnerError(
                f"a user with email {email!r} already exists; this command does not "
                "reset an existing password or promote an existing account -- use "
                "`python -m app.dashboard.admin_cli reset-password` or "
                "`set-platform-admin`/`unset-platform-admin` for that"
            ) from exc
        except exceptions.InvalidPasswordException as exc:
            raise SeedOwnerError(str(exc.reason)) from exc

        await create_membership(session, user_id=user.id, tenant_id=tenant.id, role="owner")
        await session.commit()

    role_note = " (platform_admin)" if platform_admin else ""
    print(f"seeded owner {email!r} for tenant {tenant_name!r}{role_note}")
    return user


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True, help="the owner's login email")
    parser.add_argument("--tenant-name", required=True, help="the pilot tenant's name")
    parser.add_argument(
        "--platform-admin",
        action="store_true",
        help="also grant this account the platform_admin role (is_superuser)",
    )
    args = parser.parse_args()

    configure_cli_logging()
    password = _prompt_for_password()
    try:
        asyncio.run(
            seed_owner(
                args.email, args.tenant_name, password, platform_admin=args.platform_admin
            )
        )
    except SeedOwnerError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
