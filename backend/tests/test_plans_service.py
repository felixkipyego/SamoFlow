# backend/tests/test_plans_service.py
# Task 4.3: tests for app/plans/service.py's real get_plan_limits()
# (replacing the Step 1.1b stub) and app/plans/schemas.py's PlanLimits.
import uuid

import pytest

from app.plans.models import Plan
from app.plans.schemas import PlanLimits
from app.plans.service import get_plan_limits
from app.tenancy.models import Tenant
from tests.conftest import db_session

_REAL_LIMITS = {
    "ai_message_quota_per_month": 10_000,
    "total_indexed_pages": 500,
    "crawl_page_cap": 500,
    "upload_max_size_bytes": 20 * 1024 * 1024,
    "db_sync_row_cap": 10_000,
    "refresh_interval_min_allowed": "daily",
    "refresh_now_daily_allowance": 5,
}


def test_plan_limits_rejects_a_missing_key():
    incomplete = {k: v for k, v in _REAL_LIMITS.items() if k != "ai_message_quota_per_month"}
    with pytest.raises(Exception):  # noqa: B017, PT011 (pydantic.ValidationError, any shape)
        PlanLimits.model_validate(incomplete)


def test_plan_limits_rejects_an_unrecognized_key():
    with pytest.raises(Exception):  # noqa: B017, PT011
        PlanLimits.model_validate({**_REAL_LIMITS, "an_unknown_field": 1})


def test_plan_limits_accepts_the_real_shape():
    limits = PlanLimits.model_validate(_REAL_LIMITS)
    assert limits.ai_message_quota_per_month == 10_000


async def test_get_plan_limits_returns_none_for_a_tenant_with_no_plan_assigned(
    reset_test_database,
):
    async with db_session() as session:
        tenant = Tenant(id=uuid.uuid4(), name="No Plan Tenant", status="active", plan_id=None)
        session.add(tenant)
        await session.commit()

        result = await get_plan_limits(tenant, session)

    assert result is None


# No test for the "plan deleted out from under an assigned tenant"
# branch in get_plan_limits() -- confirmed live (while writing this
# file) that the real FK (tenants.plan_id -> plans.id, no `ondelete`)
# rejects exactly this attempt with a real ForeignKeyViolation, so
# there is no live way to construct the state that branch handles. See
# that function's own updated comment for the full finding.


async def test_get_plan_limits_returns_the_real_validated_limits(reset_test_database):
    async with db_session() as session:
        plan = Plan(id=uuid.uuid4(), name="Starter", limits=_REAL_LIMITS)
        session.add(plan)
        await session.commit()

        tenant = Tenant(
            id=uuid.uuid4(), name="Plan-Having Tenant", status="active", plan_id=plan.id
        )
        session.add(tenant)
        await session.commit()

        result = await get_plan_limits(tenant, session)

    assert result == PlanLimits.model_validate(_REAL_LIMITS)


async def test_get_plan_limits_raises_loudly_on_a_malformed_plan_row(reset_test_database):
    # Felix's own Phase 4 kickoff decision (b): a typo in a plan's own
    # JSONB blob must fail loudly at READ time, never silently default.
    async with db_session() as session:
        malformed = {k: v for k, v in _REAL_LIMITS.items() if k != "db_sync_row_cap"}
        plan = Plan(id=uuid.uuid4(), name="Malformed", limits=malformed)
        session.add(plan)
        await session.commit()

        tenant = Tenant(
            id=uuid.uuid4(), name="Malformed Plan Tenant", status="active", plan_id=plan.id
        )
        session.add(tenant)
        await session.commit()

        with pytest.raises(Exception):  # noqa: B017, PT011
            await get_plan_limits(tenant, session)
