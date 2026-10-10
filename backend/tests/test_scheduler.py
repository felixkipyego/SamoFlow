# backend/tests/test_scheduler.py
# Task 2.9.a: tests for app/ingest/scheduler.py's own three real
# primitives, get_due_sources(), ensure_scheduler_ticks_seeded() and
# is_tenant_active() -- all tested DIRECTLY here (matching test_database_
# adapter.py's/test_row_templates.py's own "test the primitive, not only
# through the handler" precedent), live against the real test-db service
# (all three run real SQL -- no offline/fake-session equivalent would
# prove the actual query shape).
#
# Resolving the suspended-tenant Open marker (recorded at 2.9.a, closed in
# this same task): the suspended-tenant tests below cover layer 1
# (ensure_scheduler_ticks_seeded() never seeds a NEW tick for a suspended
# tenant, and correctly resumes once un-suspended) and the innermost layer
# 3 (get_due_sources() returns [] outright for a suspended tenant, even
# with a NULL/never-run last_run_at). Layer 2 (handle_scheduler_tick()
# itself stopping its own re-enqueue once its tenant is suspended -- the
# layer that actually stops an ALREADY-RUNNING chain) is tested separately,
# through the REAL worker.run() loop, in test_job_handlers.py (matching
# every other job-handler task's own proof requirement: not called in
# isolation) -- see app/ingest/scheduler.py's own header comment for the
# full three-layer design.
#
# handle_scheduler_tick() itself -- the real JOB_HANDLERS entry wiring
# these primitives together -- is tested separately, through the REAL
# worker.run() loop, in test_job_handlers.py (matching every other
# job-handler task's own proof requirement: not called in isolation).
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.ingest.models import Job, Source
from app.ingest.scheduler import ensure_scheduler_ticks_seeded, get_due_sources, is_tenant_active
from app.tenancy.models import Tenant
from tests.conftest import db_session

# _seeded_tenant (bare fixture parameter below) comes from conftest.py's
# own auto-discovery, matching test_queue.py's/test_worker.py's own
# established convention (duplication check after 2.1.c/d/e, item A1).

_NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
# Arbitrary but real -- these tests only ever check WHICH tenants got
# seeded, never the seeded job's own exact next_run_at, so the precise
# value doesn't matter here the way it does in test_job_handlers.py's own
# scheduler_tick tests (which do assert on it).
_INTERVAL_SECONDS = 300.0


def _fixed_clock() -> datetime:
    return _NOW


async def _make_source(
    session,
    tenant_id: uuid.UUID,
    *,
    source_type: str = "urls",
    config: dict | None = None,
    refresh_interval: str = "daily",
    enabled: bool = True,
    last_run_at: datetime | None = None,
) -> uuid.UUID:
    source = Source(
        tenant_id=tenant_id,
        type=source_type,
        config=config or {},
        refresh_interval=refresh_interval,
        enabled=enabled,
        status="active",
        last_run_at=last_run_at,
    )
    session.add(source)
    await session.flush()
    return source.id


# --- get_due_sources() -----------------------------------------------------


async def test_get_due_sources_includes_a_never_run_source(_seeded_tenant):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        source_id = await _make_source(session, tenant_id, last_run_at=None)
        await session.commit()

    async with db_session() as session:
        due = await get_due_sources(session, tenant_id=tenant_id, clock=_fixed_clock)
    assert {source.id for source in due} == {source_id}


async def test_get_due_sources_includes_a_source_past_its_refresh_interval(_seeded_tenant):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        stale_id = await _make_source(
            session,
            tenant_id,
            refresh_interval="daily",
            last_run_at=_NOW - timedelta(days=2),
        )
        await session.commit()

    async with db_session() as session:
        due = await get_due_sources(session, tenant_id=tenant_id, clock=_fixed_clock)
    assert {source.id for source in due} == {stale_id}


async def test_get_due_sources_excludes_a_recently_run_source(_seeded_tenant):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        await _make_source(
            session,
            tenant_id,
            refresh_interval="daily",
            last_run_at=_NOW - timedelta(hours=1),
        )
        await session.commit()

    async with db_session() as session:
        due = await get_due_sources(session, tenant_id=tenant_id, clock=_fixed_clock)
    assert due == []


async def test_get_due_sources_excludes_a_disabled_source_even_when_never_run(_seeded_tenant):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        await _make_source(session, tenant_id, enabled=False, last_run_at=None)
        await session.commit()

    async with db_session() as session:
        due = await get_due_sources(session, tenant_id=tenant_id, clock=_fixed_clock)
    assert due == []


async def test_get_due_sources_skips_a_source_with_an_unrecognized_refresh_interval(
    _seeded_tenant,
):
    # A config-drift case (schema permits any string; no CheckConstraint
    # exists yet, Task 2.9.b's own job) -- treated as not due, never
    # raised, matching this module's own per-item-independence discipline.
    tenant_id = _seeded_tenant
    async with db_session() as session:
        await _make_source(
            session,
            tenant_id,
            refresh_interval="hourly",  # not one of the four real values
            last_run_at=_NOW - timedelta(days=365),
        )
        await session.commit()

    async with db_session() as session:
        due = await get_due_sources(session, tenant_id=tenant_id, clock=_fixed_clock)
    assert due == []


async def test_get_due_sources_only_returns_the_given_tenants_own_sources(_seeded_tenant):
    # Cross-tenant isolation: a second tenant's own due source must never
    # leak into the first tenant's own due-query result.
    tenant_a = _seeded_tenant
    tenant_b = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_b, name="Second Tenant", status="active"))
        await session.commit()

    async with db_session() as session:
        source_a = await _make_source(session, tenant_a, last_run_at=None)
        await _make_source(session, tenant_b, last_run_at=None)
        await session.commit()

    async with db_session() as session:
        due = await get_due_sources(session, tenant_id=tenant_a, clock=_fixed_clock)
    assert {source.id for source in due} == {source_a}


async def test_get_due_sources_excludes_a_suspended_tenants_source_even_when_never_run(
    _seeded_tenant,
):
    # Resolves the suspended-tenant Open marker: a suspended tenant's own
    # source must NEVER be due, regardless of last_run_at -- the innermost
    # of three independent guards (app/ingest/scheduler.py's own header
    # comment has the full three-layer design). `last_run_at=None` is
    # deliberately the "most due" shape a source can have (a never-run
    # source is always included by test_get_due_sources_includes_a_never_
    # run_source above, absent suspension) -- proving even THAT case is
    # correctly excluded once the tenant is suspended.
    tenant_id = _seeded_tenant
    async with db_session() as session:
        await _make_source(session, tenant_id, last_run_at=None)
        await session.commit()

        await session.execute(
            sa.update(Tenant).where(Tenant.id == tenant_id).values(status="suspended")
        )
        await session.commit()

    async with db_session() as session:
        assert await is_tenant_active(session, tenant_id) is False
        due = await get_due_sources(session, tenant_id=tenant_id, clock=_fixed_clock)
    assert due == []


# --- ensure_scheduler_ticks_seeded() ---------------------------------------


async def _scheduler_tick_statuses(tenant_id: uuid.UUID) -> list[str]:
    async with db_session() as session:
        rows = (
            await session.execute(
                sa.select(Job.status).where(
                    Job.tenant_id == tenant_id, Job.job_type == "scheduler_tick"
                )
            )
        ).scalars().all()
    return list(rows)


async def test_ensure_scheduler_ticks_seeded_creates_exactly_one_for_a_tenant_with_none(
    _seeded_tenant,
):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        seeded = await ensure_scheduler_ticks_seeded(session, interval_seconds=_INTERVAL_SECONDS)
    assert tenant_id in seeded
    assert await _scheduler_tick_statuses(tenant_id) == ["pending"]


async def test_ensure_scheduler_ticks_seeded_does_not_duplicate_a_pending_tick(_seeded_tenant):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        first = await ensure_scheduler_ticks_seeded(session, interval_seconds=_INTERVAL_SECONDS)
    assert tenant_id in first

    async with db_session() as session:
        second = await ensure_scheduler_ticks_seeded(session, interval_seconds=_INTERVAL_SECONDS)
    assert tenant_id not in second
    assert await _scheduler_tick_statuses(tenant_id) == ["pending"]


async def test_ensure_scheduler_ticks_seeded_does_not_duplicate_a_running_tick(_seeded_tenant):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        session.add(
            Job(
                tenant_id=tenant_id,
                job_type="scheduler_tick",
                status="running",
                max_attempts=5,
                payload={},
            )
        )
        await session.commit()

    async with db_session() as session:
        seeded = await ensure_scheduler_ticks_seeded(session, interval_seconds=_INTERVAL_SECONDS)
    assert tenant_id not in seeded
    assert await _scheduler_tick_statuses(tenant_id) == ["running"]


async def test_ensure_scheduler_ticks_seeded_reseeds_after_a_tick_reaches_a_terminal_status(
    _seeded_tenant,
):
    # The self-healing property, proven directly: a tenant whose own
    # scheduler_tick chain was broken (its own job reached a terminal
    # "failed"/"succeeded" status, with no new one pending/running) gets a
    # fresh one seeded, not left permanently un-scheduled.
    tenant_id = _seeded_tenant
    async with db_session() as session:
        session.add(
            Job(
                tenant_id=tenant_id,
                job_type="scheduler_tick",
                status="failed",
                max_attempts=5,
                payload={},
            )
        )
        await session.commit()

    async with db_session() as session:
        seeded = await ensure_scheduler_ticks_seeded(session, interval_seconds=_INTERVAL_SECONDS)
    assert tenant_id in seeded
    assert sorted(await _scheduler_tick_statuses(tenant_id)) == ["failed", "pending"]


async def test_ensure_scheduler_ticks_seeded_seeds_every_tenant_missing_one(_seeded_tenant):
    tenant_a = _seeded_tenant
    tenant_b = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_b, name="Second Tenant Needing A Tick", status="active"))
        await session.commit()

    async with db_session() as session:
        seeded = await ensure_scheduler_ticks_seeded(session, interval_seconds=_INTERVAL_SECONDS)
    assert set(seeded) == {tenant_a, tenant_b}


async def test_ensure_scheduler_ticks_seeded_does_not_seed_a_suspended_tenant(_seeded_tenant):
    # Layer 1 of the suspended-tenant design: a suspended tenant with NO
    # pending/running tick (e.g. one that never had one, or whose own
    # chain already terminated) must never get a fresh one started.
    tenant_id = _seeded_tenant
    async with db_session() as session:
        await session.execute(
            sa.update(Tenant).where(Tenant.id == tenant_id).values(status="suspended")
        )
        await session.commit()

    async with db_session() as session:
        seeded = await ensure_scheduler_ticks_seeded(session, interval_seconds=_INTERVAL_SECONDS)
    assert tenant_id not in seeded
    assert await _scheduler_tick_statuses(tenant_id) == []


async def test_ensure_scheduler_ticks_seeded_resumes_once_a_tenant_is_reactivated(_seeded_tenant):
    # The free resumption property this module's own header comment
    # claims: the SAME recurring sweep that declines to seed a suspended
    # tenant picks it back up, with no code of its own, the moment that
    # tenant becomes active again.
    tenant_id = _seeded_tenant
    async with db_session() as session:
        await session.execute(
            sa.update(Tenant).where(Tenant.id == tenant_id).values(status="suspended")
        )
        await session.commit()

    async with db_session() as session:
        seeded_while_suspended = await ensure_scheduler_ticks_seeded(
            session, interval_seconds=_INTERVAL_SECONDS
        )
    assert tenant_id not in seeded_while_suspended

    async with db_session() as session:
        await session.execute(
            sa.update(Tenant).where(Tenant.id == tenant_id).values(status="active")
        )
        await session.commit()

    async with db_session() as session:
        seeded_once_active = await ensure_scheduler_ticks_seeded(
            session, interval_seconds=_INTERVAL_SECONDS
        )
    assert tenant_id in seeded_once_active
    assert await _scheduler_tick_statuses(tenant_id) == ["pending"]
