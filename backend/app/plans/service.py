# backend/app/plans/service.py
# Task 4.3: the real get_plan_limits(), replacing the Step 1.1b stub
# (`raise NotImplementedError`). `plans` is a small, GLOBAL lookup table
# (no tenant_id column -- app/plans/models.py), not tenant-scoped data,
# so this is a plain module-level function reading it directly, not a
# TenantScopedRepository method (that class's own frozen invariant is
# "always scoped by a real tenant_id", which this lookup structurally
# isn't -- app/tenancy/repository.py's own header comment states the
# identical reasoning for create_tenant()).
#
# Returns None, not a default PlanLimits, when `tenant.plan_id` is None:
# docs/SPEC.md §5.4's own "Plans are assigned manually in an admin
# screen" confirms a tenant can exist with no plan at all (no admin
# screen exists yet to assign one) -- a tenant with no plan has no real
# business quota to enforce, so "no limits object" is the honest return
# value, not a guessed global default. Every caller (app/chat/routes.py's
# own quota check; the future retention-sweep-shaped usage-alert job)
# must treat None as "no quota enforcement for this tenant", not as an
# error.
#
# Raises pydantic.ValidationError, uncaught, when a Plan row's own
# `limits` JSONB does not validate against PlanLimits (a missing key, an
# unrecognized key, or a wrong type) -- deliberately NOT caught and
# turned into None here: Felix's own Phase 4 kickoff decision (b) is
# explicit that a malformed plan should "fail loudly... instead of
# silently defaulting". A tenant with a genuinely malformed plan is a
# real admin-side data problem, not something a chat request should
# paper over with a fallback.
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.plans.models import Plan
from app.plans.schemas import PlanLimits
from app.tenancy.models import Tenant


async def get_plan_limits(tenant: Tenant, session: AsyncSession) -> PlanLimits | None:
    if tenant.plan_id is None:
        return None
    plan = await get_plan_by_id(tenant.plan_id, session)
    if plan is None:
        # NOT reachable today, confirmed live rather than assumed: a
        # real attempt to delete a `plans` row still referenced by a
        # `tenants.plan_id` raises a real `ForeignKeyViolation`
        # (Postgres's own default FK action, since app/tenancy/
        # models.py's FK carries no `ondelete` at all -- `NO ACTION`,
        # not a silent orphan) -- the DB itself already prevents the
        # "plan deleted out from under an assigned tenant" state this
        # branch exists for. Kept anyway, matching this project's own
        # defense-in-depth convention (e.g. app/chat/routes.py's own
        # `_resolve_conversation()` vid check, kept even after the
        # repository layer became vid-aware): one cheap `is None` check
        # that costs nothing today and stops a future FK relaxation (or
        # a raw, constraint-bypassing deletion) from turning into an
        # unhandled crash instead of "treat like no plan assigned". Not
        # covered by a live test for the identical reason it has no
        # live reproduction: the DB itself refuses to construct the
        # state needed to exercise it.
        return None
    return PlanLimits.model_validate(plan.limits)


async def get_plan_by_id(plan_id: uuid.UUID, session: AsyncSession) -> Plan | None:
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    return result.scalar_one_or_none()
