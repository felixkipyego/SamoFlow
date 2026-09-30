# backend/tests/test_queue.py
# Task 2.1.c: job-claiming primitives (backend/app/ingest/queue.py). All
# live, against the real test database -- FOR UPDATE SKIP LOCKED's own
# concurrency-safety cannot be proven any other way (test (d) below is the
# one that actually matters, per the task's own framing: the real proof,
# not just "this pattern is theoretically correct").
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.config import get_settings
from app.ingest.models import Job
from app.ingest.queue import claim_next_job, mark_job_failed, mark_job_succeeded
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Tenant
from tests.conftest import db_session


@pytest.fixture
async def _seeded_tenant(reset_test_database):
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Queue Tenant", status="active"))
        await session.commit()
    yield tenant_id


async def _make_job(
    session, tenant_id, *, next_run_at=None, status="pending", max_attempts=5
) -> uuid.UUID:
    job = Job(
        tenant_id=tenant_id,
        job_type="crawl",
        status=status,
        max_attempts=max_attempts,
        payload={},
        **({"next_run_at": next_run_at} if next_run_at is not None else {}),
    )
    session.add(job)
    await session.flush()
    return job.id


async def test_claim_next_job_returns_the_oldest_ready_job_and_flips_it_to_running(
    _seeded_tenant,
):
    tenant_id = _seeded_tenant
    now = datetime.now(UTC)
    async with db_session() as session:
        # Seeded out of order on purpose: the newest-created row has the
        # oldest next_run_at, proving the claim orders by next_run_at, not
        # insertion order or id.
        newest_created_but_oldest_ready = await _make_job(
            session, tenant_id, next_run_at=now - timedelta(seconds=30)
        )
        await _make_job(session, tenant_id, next_run_at=now - timedelta(seconds=20))
        await _make_job(session, tenant_id, next_run_at=now - timedelta(seconds=10))
        await session.commit()

    async with db_session() as session:
        claimed = await claim_next_job(session)
        await session.commit()

    assert claimed is not None
    assert claimed.id == newest_created_but_oldest_ready
    assert claimed.status == "running"

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == claimed.id))).scalar_one()
        assert row.status == "running"


async def test_claim_next_job_returns_none_when_nothing_is_ready(_seeded_tenant):
    tenant_id = _seeded_tenant
    now = datetime.now(UTC)

    async with db_session() as session:
        assert await claim_next_job(session) is None  # no jobs at all yet

    async with db_session() as session:
        await _make_job(session, tenant_id, next_run_at=now + timedelta(hours=1))
        await session.commit()

    async with db_session() as session:
        # The only job that exists is pending but not yet ready.
        assert await claim_next_job(session) is None


async def test_claim_next_job_concurrency_proof(_seeded_tenant):
    # The proof that actually matters (per the task's own framing): real
    # overlapping transactions on separate sessions/connections via
    # asyncio.gather(), not a sequential loop simulating concurrency.
    # Every seeded job must be claimed by exactly one caller -- no double
    # claim, no job silently skipped -- and oversubscribed callers must
    # cleanly get None, not an error or a duplicate.
    tenant_id = _seeded_tenant
    now = datetime.now(UTC)
    job_count = 10
    caller_count = 13  # more callers than jobs: proves oversubscription is handled cleanly

    async with db_session() as session:
        job_ids = {
            await _make_job(session, tenant_id, next_run_at=now - timedelta(seconds=i))
            for i in range(job_count)
        }
        await session.commit()

    async def _claim_and_commit():
        async with db_session() as session:
            claimed = await claim_next_job(session)
            await session.commit()
            return claimed.id if claimed is not None else None

    results = await asyncio.gather(*(_claim_and_commit() for _ in range(caller_count)))

    claimed_ids = [r for r in results if r is not None]
    none_count = sum(1 for r in results if r is None)

    assert len(claimed_ids) == job_count, "every ready job should have been claimed by someone"
    assert len(set(claimed_ids)) == job_count, "no job should have been claimed twice"
    assert set(claimed_ids) == job_ids, "no job should have been silently skipped"
    assert none_count == caller_count - job_count, "oversubscribed callers must cleanly get None"

    async with db_session() as session:
        statuses = (
            (await session.execute(sa.select(Job.status).where(Job.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
        assert statuses == ["running"] * job_count


async def test_mark_job_succeeded_sets_the_terminal_state(_seeded_tenant):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        job_id = await _make_job(session, tenant_id, status="running")
        await session.commit()

    async with db_session() as session:
        await mark_job_succeeded(session, job_id)
        await session.commit()

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        assert row.status == "succeeded"


async def test_mark_job_failed_schedules_a_retry_with_the_correct_backoff(_seeded_tenant):
    tenant_id = _seeded_tenant
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)

    async with db_session() as session:
        job_id = await _make_job(session, tenant_id, status="running", max_attempts=5)
        await session.commit()

    async with db_session() as session:
        await mark_job_failed(session, job_id, "boom", clock=lambda: fixed_now)
        await session.commit()

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        assert row.status == "pending"
        assert row.attempts == 1
        assert row.error == "boom"
        base_seconds = get_settings().job_retry_base_seconds
        assert row.next_run_at == fixed_now + timedelta(seconds=base_seconds * (2**1))


async def test_mark_job_failed_becomes_permanently_failed_at_max_attempts(_seeded_tenant):
    tenant_id = _seeded_tenant
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)

    async with db_session() as session:
        job_id = await _make_job(session, tenant_id, status="running")
        # attempts starts at its own column default (0) via _make_job's
        # Job(...) construction not setting it -- bump it to one below
        # max_attempts directly so this test only exercises the final,
        # permanent-failure transition, not four prior calls to get there.
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        row.attempts = row.max_attempts - 1
        await session.commit()

    async with db_session() as session:
        await mark_job_failed(session, job_id, "boom again", clock=lambda: fixed_now)
        await session.commit()

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        assert row.status == "failed"
        assert row.attempts == row.max_attempts
        assert row.error == "boom again"
        # Permanently failed: next_run_at is untouched (no further reschedule).


async def test_tenant_id_survives_the_claim_to_mark_succeeded_cycle_unchanged(_seeded_tenant):
    # The isolation property this task actually owns (per the Step 2.1
    # research's own framing): not cross-tenant data leakage (2.5's
    # concern), but that a claimed job's own identity -- which tenant it
    # belongs to -- is provably unchanged through the whole
    # claim -> mark_succeeded cycle.
    tenant_id = _seeded_tenant
    async with db_session() as session:
        ready_at = datetime.now(UTC) - timedelta(seconds=5)
        job_id = await _make_job(session, tenant_id, next_run_at=ready_at)
        await session.commit()

    async with db_session() as session:
        claimed = await claim_next_job(session)
        await session.commit()
    assert claimed.id == job_id
    assert claimed.tenant_id == tenant_id

    async with db_session() as session:
        await mark_job_succeeded(session, job_id)
        await session.commit()

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        assert row.tenant_id == tenant_id


async def test_tenant_id_survives_the_claim_to_mark_failed_cycle_unchanged(_seeded_tenant):
    tenant_id = _seeded_tenant
    async with db_session() as session:
        ready_at = datetime.now(UTC) - timedelta(seconds=5)
        job_id = await _make_job(session, tenant_id, next_run_at=ready_at)
        await session.commit()

    async with db_session() as session:
        claimed = await claim_next_job(session)
        await session.commit()
    assert claimed.id == job_id
    assert claimed.tenant_id == tenant_id

    async with db_session() as session:
        await mark_job_failed(session, job_id, "transient error")
        await session.commit()

    async with db_session() as session:
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        assert row.tenant_id == tenant_id
