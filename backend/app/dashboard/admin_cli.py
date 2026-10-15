# backend/app/dashboard/admin_cli.py
# Step 6.1: server-side platform-admin CLI commands (Felix's own
# 2026-10-15 correction: "Since 6.5's admin UI is post-pilot, provide
# server-side CLI commands for the admin actions the pilot needs: create
# a tenant, assign a plan, issue or revoke a site key, revoke a domain,
# seed an owner"). "Seed an owner" is app/dashboard/seed_cli.py, already
# built with a `--platform-admin` flag covering the identical need --
# this module covers the other four, plus `seed-plans` (closing the
# "no Plan-row creation exists anywhere" §9 marker the same correction
# names).
#
# Each subcommand is a thin wrapper over an already-existing repository
# primitive (create_tenant, create_site_key, set_site_key_status,
# revoke_domain) -- no new business logic, matching seed_cli.py's own
# "built on the real path, not a second one" precedent. Trusted operator
# tool, not a public endpoint: no CSRF, no rate limiting, no session --
# whoever can run `docker compose run` on the production host already
# has full database access.
import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

# For the side effect of registering every domain's tables on Base.
# metadata (app/all_models.py's own header comment, and see app/
# dashboard/seed_cli.py's own identical, live-confirmed-necessary
# import for the real failure this prevents).
from app import all_models  # noqa: F401
from app.auth.origin import normalize_origin
from app.cli import configure_cli_logging
from app.db import _session_factory
from app.ingest.repository import revoke_domain
from app.plans.models import Plan
from app.plans.schemas import PlanLimits
from app.tenancy.models import Tenant
from app.tenancy.repository import (
    TenantScopedRepository,
    create_tenant,
    generate_site_key,
)


class AdminCliError(Exception):
    """Raised for any expected failure -- message is always safe to
    print directly."""


async def cmd_create_tenant(args: argparse.Namespace) -> None:
    async with _session_factory()() as session:
        tenant = await create_tenant(session, name=args.name, status="active")
        await session.commit()
    print(f"created tenant {tenant.name!r} ({tenant.id})")


async def cmd_assign_plan(args: argparse.Namespace) -> None:
    async with _session_factory()() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.id == args.tenant_id))
        ).scalar_one_or_none()
        if tenant is None:
            raise AdminCliError(f"no tenant with id {args.tenant_id}")
        plan = (
            await session.execute(select(Plan).where(Plan.id == args.plan_id))
        ).scalar_one_or_none()
        if plan is None:
            raise AdminCliError(f"no plan with id {args.plan_id}")
        tenant.plan_id = plan.id
        await session.commit()
    print(f"assigned plan {plan.name!r} to tenant {tenant.name!r}")


async def cmd_issue_site_key(args: argparse.Namespace) -> None:
    allowed_origins = []
    for raw in args.allowed_origin:
        normalized = normalize_origin(raw)
        if normalized is None:
            raise AdminCliError(f"{raw!r} is not a valid single origin (scheme://host[:port])")
        allowed_origins.append(normalized)

    async with _session_factory()() as session:
        repo = TenantScopedRepository(tenant_id=args.tenant_id, session=session)
        site_key = await repo.create_site_key(
            key=generate_site_key(), allowed_origins=allowed_origins, environment=args.environment
        )
        if args.status != "draft":
            site_key = await repo.set_site_key_status(site_key.id, args.status)
        await session.commit()
    print(f"issued site key {site_key.key} ({site_key.status}) for tenant {args.tenant_id}")


async def cmd_revoke_site_key(args: argparse.Namespace) -> None:
    async with _session_factory()() as session:
        repo = TenantScopedRepository(tenant_id=args.tenant_id, session=session)
        site_key = await repo.set_site_key_status(args.site_key_id, "suspended")
        if site_key is None:
            raise AdminCliError(
                f"no site key {args.site_key_id} for tenant {args.tenant_id}"
            )
        await session.commit()
    print(f"suspended site key {site_key.id}")


async def cmd_revoke_domain(args: argparse.Namespace) -> None:
    async with _session_factory()() as session:
        domain = await revoke_domain(session, args.domain_id, actor=args.actor)
        if domain is None:
            raise AdminCliError(f"no verified domain with id {args.domain_id}")
        await session.commit()
    print(f"revoked domain {domain.domain!r} ({domain.status})")


# Felix's §11.3 kickoff decision #3/the Task 3.7.i §9 marker: the two
# real pilot plan quotas, ~21% headroom under the real gpt-6-luna
# break-even (12,710/31,775 msgs/month) -- see that marker's own full
# derivation, not re-stated here. The other five PlanLimits fields have
# no pilot-specific number named anywhere yet, so they reuse this
# project's own existing Settings defaults verbatim (crawl_page_cap=500,
# upload_max_size_bytes=20MB, db_sync_row_cap=10_000, refresh_interval_
# min_allowed="daily", refresh_now_daily_allowance=5) -- the identical
# "generous default, no real per-plan data yet" values those Settings
# fields already document, not new numbers invented here.
_PILOT_PLANS: tuple[tuple[str, int], ...] = (
    ("Starter", 10_000),
    ("Growth", 25_000),
)


async def cmd_seed_plans(args: argparse.Namespace) -> None:  # noqa: ARG001
    async with _session_factory()() as session:
        created = []
        for name, quota in _PILOT_PLANS:
            existing = (
                await session.execute(select(Plan).where(Plan.name == name))
            ).scalar_one_or_none()
            if existing is not None:
                continue
            limits = PlanLimits(
                ai_message_quota_per_month=quota,
                total_indexed_pages=5_000,
                crawl_page_cap=500,
                upload_max_size_bytes=20 * 1024 * 1024,
                db_sync_row_cap=10_000,
                refresh_interval_min_allowed="daily",
                refresh_now_daily_allowance=5,
            )
            plan = Plan(name=name, limits=limits.model_dump())
            session.add(plan)
            created.append(name)
        await session.commit()
    if created:
        print(f"seeded plans: {', '.join(created)}")
    else:
        print("no plans seeded -- both pilot plan names already exist")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_tenant_parser = subparsers.add_parser("create-tenant")
    create_tenant_parser.add_argument("--name", required=True)
    create_tenant_parser.set_defaults(handler=cmd_create_tenant)

    assign_plan_parser = subparsers.add_parser("assign-plan")
    assign_plan_parser.add_argument("--tenant-id", dest="tenant_id", type=uuid.UUID, required=True)
    assign_plan_parser.add_argument("--plan-id", dest="plan_id", type=uuid.UUID, required=True)
    assign_plan_parser.set_defaults(handler=cmd_assign_plan)

    issue_site_key_parser = subparsers.add_parser("issue-site-key")
    issue_site_key_parser.add_argument(
        "--tenant-id", dest="tenant_id", type=uuid.UUID, required=True
    )
    issue_site_key_parser.add_argument("--allowed-origin", action="append", required=True)
    issue_site_key_parser.add_argument("--environment", default="production")
    issue_site_key_parser.add_argument(
        "--status", choices=("draft", "live"), default="draft"
    )
    issue_site_key_parser.set_defaults(handler=cmd_issue_site_key)

    revoke_site_key_parser = subparsers.add_parser("revoke-site-key")
    revoke_site_key_parser.add_argument(
        "--tenant-id", dest="tenant_id", type=uuid.UUID, required=True
    )
    revoke_site_key_parser.add_argument(
        "--site-key-id", dest="site_key_id", type=uuid.UUID, required=True
    )
    revoke_site_key_parser.set_defaults(handler=cmd_revoke_site_key)

    revoke_domain_parser = subparsers.add_parser("revoke-domain")
    revoke_domain_parser.add_argument(
        "--domain-id", dest="domain_id", type=uuid.UUID, required=True
    )
    revoke_domain_parser.add_argument("--actor", required=True, help="the operator's own email")
    revoke_domain_parser.set_defaults(handler=cmd_revoke_domain)

    seed_plans_parser = subparsers.add_parser("seed-plans")
    seed_plans_parser.set_defaults(handler=cmd_seed_plans)

    args = parser.parse_args()
    configure_cli_logging()
    try:
        asyncio.run(args.handler(args))
    except AdminCliError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
