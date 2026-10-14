# backend/tests/test_plans_usage.py
# Task 4.3: tests for app/plans/usage.py -- the in-memory quota counter,
# the pending-write buffer, and per-model cost estimation.
#
# Module-level state reset: app.plans.usage._counts_by_tenant_month and
# ._pending are process-wide globals (by design -- see that module's own
# header comment on the single-API-process precondition). Every test
# below clears both before running, via the autouse fixture, so no test
# ever inherits another test's own counter state.
import uuid
from datetime import UTC, datetime

import pytest

from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.plans import usage as usage_module
from app.plans.schemas import PlanLimits
from app.plans.usage import (
    PendingUsageEvent,
    current_month_count,
    estimate_cost_usd,
    is_over_quota,
    monthly_count_from_db,
    record_usage_event,
    sync_usage_counters,
    usage_fraction,
)
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor
from tests.conftest import db_session

ORIGIN = "https://tenant.example"

_LIMITS = PlanLimits(
    ai_message_quota_per_month=3,
    total_indexed_pages=500,
    crawl_page_cap=500,
    upload_max_size_bytes=1,
    db_sync_row_cap=1,
    refresh_interval_min_allowed="daily",
    refresh_now_daily_allowance=1,
)


@pytest.fixture(autouse=True)
def _clear_usage_module_state():
    usage_module._counts_by_tenant_month.clear()
    usage_module._pending.clear()
    yield
    usage_module._counts_by_tenant_month.clear()
    usage_module._pending.clear()


def _clock(fixed: datetime):
    return lambda: fixed


def test_estimate_cost_usd_for_the_one_real_priced_model():
    # Real pricing (PROJECT_SPEC.md, Task 3.7.h): gpt-6-luna is
    # $0.10/$0.50 per 1M input/output tokens.
    cost = estimate_cost_usd("gpt-6-luna", prompt_tokens=1_000_000, completion_tokens=1_000_000)
    assert cost == pytest.approx(0.60)


def test_estimate_cost_usd_returns_none_for_an_unpriced_model(caplog):
    cost = estimate_cost_usd("some-future-model", prompt_tokens=100, completion_tokens=100)
    assert cost is None
    assert "no known per-token pricing" in caplog.text


async def test_record_usage_event_increments_the_current_month_counter_only_when_counted():
    tenant_id = uuid.uuid4()
    now = datetime(2026, 10, 14, tzinfo=UTC)

    await record_usage_event(
        PendingUsageEvent(
            tenant_id=tenant_id,
            vid=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            counted_toward_quota=True,
            model="gpt-6-luna",
            prompt_tokens=10,
            completion_tokens=5,
            estimated_cost_usd=0.001,
            created_at=now,
        )
    )
    # A free turn (guardrail/fallback/quota-exhausted) must never move
    # the counter, even though a row is still recorded.
    await record_usage_event(
        PendingUsageEvent(
            tenant_id=tenant_id,
            vid=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            counted_toward_quota=False,
            model=None,
            prompt_tokens=None,
            completion_tokens=None,
            estimated_cost_usd=None,
            created_at=now,
        )
    )

    assert current_month_count(tenant_id, clock=_clock(now)) == 1
    assert len(usage_module._pending) == 2


def test_is_over_quota_and_usage_fraction():
    tenant_id = uuid.uuid4()
    now = datetime(2026, 10, 14, tzinfo=UTC)
    key = (tenant_id, "2026-10")
    usage_module._counts_by_tenant_month[key] = 2

    assert is_over_quota(tenant_id, _LIMITS, clock=_clock(now)) is False
    assert usage_fraction(tenant_id, _LIMITS, clock=_clock(now)) == pytest.approx(2 / 3)

    usage_module._counts_by_tenant_month[key] = 3
    assert is_over_quota(tenant_id, _LIMITS, clock=_clock(now)) is True


@pytest.fixture
async def _seeded_pair(reset_test_database):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    site_key_id = uuid.uuid4()
    vid_a = uuid.uuid4()
    vid_b = uuid.uuid4()
    cid_a = uuid.uuid4()
    cid_b = uuid.uuid4()
    async with db_session() as session:
        session.add_all(
            [
                Tenant(id=tenant_a, name="A", status="active", config={}),
                Tenant(id=tenant_b, name="B", status="active", config={}),
            ]
        )
        await session.flush()
        session.add(
            SiteKey(
                id=site_key_id,
                tenant_id=tenant_a,
                key="pk_live_usage_test",
                environment="production",
                status="live",
                allowed_origins=[ORIGIN],
            )
        )
        await session.flush()
        session.add_all(
            [
                Visitor(
                    vid=vid_a,
                    tenant_id=tenant_a,
                    site_key_id=site_key_id,
                    secret_hash=hash_visitor_secret(generate_visitor_secret()),
                ),
                Visitor(
                    vid=vid_b,
                    tenant_id=tenant_b,
                    site_key_id=site_key_id,
                    secret_hash=hash_visitor_secret(generate_visitor_secret()),
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Conversation(cid=cid_a, tenant_id=tenant_a, vid=vid_a, title=None),
                Conversation(cid=cid_b, tenant_id=tenant_b, vid=vid_b, title=None),
            ]
        )
        await session.commit()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "vid_a": vid_a,
        "vid_b": vid_b,
        "cid_a": cid_a,
        "cid_b": cid_b,
    }


async def test_sync_usage_counters_flushes_pending_and_reloads_the_authoritative_count(
    _seeded_pair,
):
    now = datetime(2026, 10, 14, tzinfo=UTC)
    for _ in range(2):
        await record_usage_event(
            PendingUsageEvent(
                tenant_id=_seeded_pair["tenant_a"],
                vid=_seeded_pair["vid_a"],
                conversation_id=_seeded_pair["cid_a"],
                counted_toward_quota=True,
                model="gpt-6-luna",
                prompt_tokens=10,
                completion_tokens=5,
                estimated_cost_usd=0.001,
                created_at=now,
            )
        )
    # tenant_b gets one free (uncounted) turn -- must not move its count.
    await record_usage_event(
        PendingUsageEvent(
            tenant_id=_seeded_pair["tenant_b"],
            vid=_seeded_pair["vid_b"],
            conversation_id=_seeded_pair["cid_b"],
            counted_toward_quota=False,
            model=None,
            prompt_tokens=None,
            completion_tokens=None,
            estimated_cost_usd=None,
            created_at=now,
        )
    )

    async with db_session() as session:
        await sync_usage_counters(session, clock=_clock(now))

        assert await monthly_count_from_db(
            _seeded_pair["tenant_a"], session, clock=_clock(now)
        ) == 2
        assert await monthly_count_from_db(
            _seeded_pair["tenant_b"], session, clock=_clock(now)
        ) == 0

    assert current_month_count(_seeded_pair["tenant_a"], clock=_clock(now)) == 2
    assert current_month_count(_seeded_pair["tenant_b"], clock=_clock(now)) == 0
    assert usage_module._pending == []


async def test_sync_usage_counters_is_scoped_to_the_real_calendar_month(_seeded_pair):
    last_month = datetime(2026, 9, 30, 12, tzinfo=UTC)
    this_month = datetime(2026, 10, 1, 0, 1, tzinfo=UTC)

    await record_usage_event(
        PendingUsageEvent(
            tenant_id=_seeded_pair["tenant_a"],
            vid=_seeded_pair["vid_a"],
            conversation_id=_seeded_pair["cid_a"],
            counted_toward_quota=True,
            model="gpt-6-luna",
            prompt_tokens=1,
            completion_tokens=1,
            estimated_cost_usd=0.0001,
            created_at=last_month,
        )
    )

    async with db_session() as session:
        await sync_usage_counters(session, clock=_clock(this_month))

    # The row is real (written during the flush above), but it belongs
    # to LAST month -- this month's own count must not include it.
    assert current_month_count(_seeded_pair["tenant_a"], clock=_clock(this_month)) == 0
