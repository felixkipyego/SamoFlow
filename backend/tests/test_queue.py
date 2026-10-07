# backend/tests/test_queue.py
# Task 2.1.c: job-claiming primitives (backend/app/ingest/queue.py). All
# live, against the real test database -- FOR UPDATE SKIP LOCKED's own
# concurrency-safety cannot be proven any other way (test (d) below is the
# one that actually matters, per the task's own framing: the real proof,
# not just "this pattern is theoretically correct").
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa

from app.config import get_settings
from app.ingest.models import Job
from app.ingest.queue import claim_next_job, mark_job_failed, mark_job_succeeded, reap_stuck_jobs
from tests.conftest import _fetch_job, db_session

# _seeded_tenant (used as a bare fixture parameter below) comes from
# conftest.py's own auto-discovery, like reset_test_database already does
# throughout this codebase -- never imported explicitly, matching that
# established convention (duplication check after 2.1.c/d/e, item A1).


async def _make_job(
    session,
    tenant_id,
    *,
    next_run_at=None,
    status="pending",
    max_attempts=5,
    updated_at=None,
) -> uuid.UUID:
    job = Job(
        tenant_id=tenant_id,
        job_type="crawl",
        status=status,
        max_attempts=max_attempts,
        payload={},
        **({"next_run_at": next_run_at} if next_run_at is not None else {}),
        # Task 2.6.f: lets a reaper test backdate a "running" row's own
        # updated_at to simulate a job whose worker crashed hard some
        # time ago -- TIMESTAMP_NOW's own server_default only applies
        # when the column is omitted from the INSERT, so passing a real
        # value here overrides it cleanly, matching every other optional
        # override this helper already supports.
        **({"updated_at": updated_at} if updated_at is not None else {}),
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

    row = await _fetch_job(claimed.id)
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

    row = await _fetch_job(job_id)
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

    row = await _fetch_job(job_id)
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
        # A fetch-then-mutate-then-commit within one transaction, not a
        # standalone read -- _fetch_job() (duplication check after
        # 2.1.c/d/e, item A2) is for the latter only, so this one stays
        # inline rather than a detached instance needing session.merge().
        row = (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()
        row.attempts = row.max_attempts - 1
        await session.commit()

    async with db_session() as session:
        await mark_job_failed(session, job_id, "boom again", clock=lambda: fixed_now)
        await session.commit()

    row = await _fetch_job(job_id)
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

    row = await _fetch_job(job_id)
    assert row.tenant_id == tenant_id


# --- Task 2.6.f: reap_stuck_jobs() ------------------------------------------
# The stuck-job reaper -- the last of the four 2.4-2.8-range markers,
# waiting since Task 2.1.e. "A row manually left in 'running' status with
# a stale timestamp, as if its worker had crashed" (this task's own
# wording) is exactly _make_job(status="running", updated_at=<stale>)
# below -- no real crashed worker needed to prove this correctly.


async def test_reap_stuck_jobs_reclaims_a_stale_running_job(_seeded_tenant):
    tenant_id = _seeded_tenant
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)
    stale_after_seconds = 1800

    async with db_session() as session:
        stuck_id = await _make_job(
            session,
            tenant_id,
            status="running",
            updated_at=fixed_now - timedelta(seconds=stale_after_seconds + 1),
        )
        await session.commit()

    async with db_session() as session:
        reaped = await reap_stuck_jobs(
            session, stale_after_seconds=stale_after_seconds, clock=lambda: fixed_now
        )
        await session.commit()

    assert reaped == [stuck_id]
    row = await _fetch_job(stuck_id)
    # Reclaimed via mark_job_failed()'s own normal backoff path, not a
    # bare reset -- attempts incremented, a real retry delay scheduled,
    # not "pending" again with no consequence at all.
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.error == "stuck: no progress since claim or last update"
    base_seconds = get_settings().job_retry_base_seconds
    assert row.next_run_at == fixed_now + timedelta(seconds=base_seconds * (2**1))


async def test_reap_stuck_jobs_leaves_a_genuinely_running_job_alone(_seeded_tenant):
    # No false positives: a job that IS stale by wall-clock age but
    # whose own updated_at is still within the threshold (a worker
    # genuinely, actively working it) must not be touched.
    tenant_id = _seeded_tenant
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)
    stale_after_seconds = 1800

    async with db_session() as session:
        fresh_id = await _make_job(
            session,
            tenant_id,
            status="running",
            updated_at=fixed_now - timedelta(seconds=stale_after_seconds - 1),
        )
        await session.commit()

    async with db_session() as session:
        reaped = await reap_stuck_jobs(
            session, stale_after_seconds=stale_after_seconds, clock=lambda: fixed_now
        )
        await session.commit()

    assert reaped == []
    row = await _fetch_job(fresh_id)
    assert row.status == "running"
    assert row.attempts == 0
    assert row.error is None


async def test_reap_stuck_jobs_ignores_pending_and_succeeded_jobs(_seeded_tenant):
    # Only "running" is ever a candidate -- a stale updated_at on any
    # other status means nothing (e.g. a long-succeeded job that simply
    # hasn't been touched since).
    tenant_id = _seeded_tenant
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)
    stale_after_seconds = 1800
    ancient = fixed_now - timedelta(seconds=stale_after_seconds * 10)

    async with db_session() as session:
        pending_id = await _make_job(session, tenant_id, status="pending", updated_at=ancient)
        succeeded_id = await _make_job(
            session, tenant_id, status="succeeded", updated_at=ancient
        )
        await session.commit()

    async with db_session() as session:
        reaped = await reap_stuck_jobs(
            session, stale_after_seconds=stale_after_seconds, clock=lambda: fixed_now
        )
        await session.commit()

    assert reaped == []
    assert (await _fetch_job(pending_id)).status == "pending"
    assert (await _fetch_job(succeeded_id)).status == "succeeded"


async def test_reap_stuck_jobs_reuses_mark_job_failed_max_attempts_ceiling(_seeded_tenant):
    # Live proof of real REUSE, not a second, parallel failure path: a
    # stuck job already one attempt away from its own max_attempts is
    # driven all the way to permanently "failed" by the reaper, via the
    # exact same ceiling mark_job_failed() already enforces for any
    # other failure.
    tenant_id = _seeded_tenant
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)
    stale_after_seconds = 1800

    async with db_session() as session:
        stuck_id = await _make_job(
            session,
            tenant_id,
            status="running",
            updated_at=fixed_now - timedelta(seconds=stale_after_seconds + 1),
        )
        row = (await session.execute(sa.select(Job).where(Job.id == stuck_id))).scalar_one()
        row.attempts = row.max_attempts - 1
        await session.commit()

    async with db_session() as session:
        reaped = await reap_stuck_jobs(
            session, stale_after_seconds=stale_after_seconds, clock=lambda: fixed_now
        )
        await session.commit()

    assert reaped == [stuck_id]
    row = await _fetch_job(stuck_id)
    assert row.status == "failed"
    assert row.attempts == row.max_attempts


async def test_reap_stuck_jobs_concurrency_proof(_seeded_tenant):
    # Duplication check after 2.6.d/2.6.e/2.6.f, item C2: reap_stuck_jobs()
    # had no proof of its own concurrent-call safety, unlike claim_next_
    # job()'s own test_claim_next_job_concurrency_proof above -- real
    # overlapping asyncio.gather() calls, not a sequential simulation.
    # reap_stuck_jobs() uses a structurally different pattern from claim_
    # next_job() (a SELECT ... FOR UPDATE SKIP LOCKED followed by a loop of
    # mark_job_failed() calls, rather than one atomic UPDATE ... WHERE),
    # so this is a genuinely separate thing to prove, not a copy of the
    # existing claim proof. Every stuck job must be reaped by exactly one
    # caller -- no job double-processed (mark_job_failed() applied twice),
    # no job silently skipped by every caller.
    tenant_id = _seeded_tenant
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)
    stale_after_seconds = 1800
    job_count = 10
    caller_count = 5

    async with db_session() as session:
        stuck_ids = {
            await _make_job(
                session,
                tenant_id,
                status="running",
                updated_at=fixed_now - timedelta(seconds=stale_after_seconds + 1),
            )
            for _ in range(job_count)
        }
        await session.commit()

    async def _reap_and_commit():
        async with db_session() as session:
            reaped = await reap_stuck_jobs(
                session, stale_after_seconds=stale_after_seconds, clock=lambda: fixed_now
            )
            await session.commit()
            return reaped

    results = await asyncio.gather(*(_reap_and_commit() for _ in range(caller_count)))

    reaped_ids = [job_id for result in results for job_id in result]

    assert len(reaped_ids) == job_count, "every stuck job should have been reaped by someone"
    assert len(set(reaped_ids)) == job_count, "no job should have been reaped twice"
    assert set(reaped_ids) == stuck_ids, "no job should have been silently skipped"

    async with db_session() as session:
        rows = (
            (await session.execute(sa.select(Job).where(Job.tenant_id == tenant_id)))
            .scalars()
            .all()
        )
    assert {row.status for row in rows} == {"pending"}
    # attempts == 1 on every row, not 2+ -- proves mark_job_failed()'s own
    # backoff logic was applied exactly once per job, never double-applied
    # by two callers racing on the same row.
    assert {row.attempts for row in rows} == {1}


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

    row = await _fetch_job(job_id)
    assert row.tenant_id == tenant_id
