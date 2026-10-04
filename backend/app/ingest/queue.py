# backend/app/ingest/queue.py
# Task 2.1.c: job-claiming primitives. Deliberately a new, separate module
# from app/ingest/repository.py, not IngestRepository methods: claim_next_job()/
# mark_job_succeeded()/mark_job_failed() have no tenant identity at all --
# the worker claims across every tenant's pending jobs (PROJECT_SPEC.md's
# Step 2.1 decision entry) -- so they structurally cannot fit
# IngestRepository's own invariant (construction always requires a real
# tenant_id), the exact same reasoning app/tenancy/repository.py's
# create_tenant()/get_site_key_by_key() already established for "no tenant
# identity yet", just the inverse case ("no tenant identity, ever, for this
# call"). enqueue() -- the one tenant-scoped operation here -- lives on
# IngestRepository instead, beside create_db_connection().
#
# Claiming mechanism (PROJECT_SPEC.md's Step 2.1 decision (b)): a single
# UPDATE...WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)
# RETURNING statement -- the standard, well-known Postgres queue idiom
# (rule 11), not a separate SELECT-then-UPDATE across two statements. One
# statement is inherently "the claim and the pending -> running flip in
# the same transaction": there is no window between reading which row is
# next and marking it running for a second concurrent caller to also pick
# the same row. Verified live before writing this (not assumed): the
# compiled SQL renders as `UPDATE jobs SET ... WHERE jobs.id = (SELECT
# jobs.id FROM jobs WHERE ... ORDER BY ... LIMIT 1 FOR UPDATE SKIP LOCKED)
# RETURNING ...`; session.execute(stmt).scalar_one_or_none() returns a
# real, correctly-flipped Job ORM instance; and a genuine concurrent
# proof -- 15 real overlapping asyncio.gather() callers, separate
# sessions/connections, against 14 real pending rows -- claimed all 14
# uniquely with zero double-claims and exactly 1 caller correctly
# receiving None (see test_queue.py's own concurrency test for the
# permanent, automated version of this proof).
#
# claim_next_job() does not commit internally, matching every other
# repository/service function in this codebase (app/auth/routes.py's own
# endpoint-level commit is the only place that calls session.commit() in
# application code) -- the caller (2.1.d's worker loop, or a test) decides
# the transaction boundary. The FOR UPDATE SKIP LOCKED row lock is held
# from the moment the statement executes, not from commit -- Postgres
# releases it only on commit/rollback -- so concurrent callers are already
# correctly serialized against each other before any caller commits; a
# caller that crashes before committing simply rolls back, automatically
# restoring the job to "pending" for the next claimer, a useful,
# unplanned self-healing property of using a real transaction for this
# rather than a hand-rolled "claimed_by" column.
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app.config import get_settings
from app.db import DateTimeClock
from app.ingest.models import Job


async def claim_next_job(session: AsyncSession) -> Job | None:
    subquery = (
        select(Job.id)
        .where(Job.status == "pending", Job.next_run_at <= func.now())
        .order_by(Job.next_run_at)
        .with_for_update(skip_locked=True)
        .limit(1)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == subquery)
        .values(status="running", updated_at=func.now())
        .returning(Job)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def mark_job_succeeded(session: AsyncSession, job_id: uuid.UUID) -> None:
    job = await session.get(Job, job_id)
    if job is None:
        raise ValueError(f"job {job_id} does not exist")
    job.status = "succeeded"
    job.updated_at = func.now()
    await session.flush()


async def mark_job_failed(
    session: AsyncSession,
    job_id: uuid.UUID,
    error: str,
    clock: DateTimeClock = lambda: datetime.now(UTC),
    permanent: bool = False,
) -> None:
    # Backoff formula (PROJECT_SPEC.md's Step 2.1.c decision entry):
    # next_run_at = clock() + base_seconds * 2**attempts, attempts counted
    # AFTER incrementing for this failure. With the Settings defaults
    # (job_retry_base_seconds=60, and this job's own max_attempts=5, set
    # at enqueue() time from job_max_attempts) that schedules retries at
    # 2/4/8/16 minutes before the 5th failure makes it permanently
    # "failed" -- reasonable for a background ingestion job (crawl/upload/
    # database-sync/reconcile), nothing user-facing or latency-sensitive
    # waits on it. Compared against this job's own persisted max_attempts
    # column, never a fresh get_settings().job_max_attempts read: a
    # later change to that setting must never retroactively change an
    # already-created job's own retry ceiling.
    #
    # clock is injectable (matching RateLimiter's own
    # `clock: Callable[[], float] = time.monotonic` pattern,
    # app/ratelimit.py) so a test can assert the exact resulting
    # next_run_at against a fixed, known value -- both next_run_at and
    # updated_at use the same clock() call for one consistent "now"
    # reference within this one state transition, rather than mixing a
    # Python-side clock for one field and the database's own func.now()
    # for the other.
    #
    # permanent (Task 2.1.d): added to this existing 2.1.c function
    # rather than a second, near-duplicate mark_job_failed_permanently()
    # -- one function with a branch, not two copies of the shared
    # job-lookup/error/updated_at plumbing. False (the default) is
    # 2.1.c's own original behavior, byte-for-byte unchanged: attempts
    # increments, backoff applies, "failed" only once max_attempts is
    # exhausted. permanent=True is for a structural, non-transient error
    # -- worker.py's own "no handler registered for this job_type"
    # case -- where retrying is guaranteed to reproduce the identical
    # failure, so it skips straight to "failed" instead of burning
    # max_attempts retries and their backoff delay to reach the same
    # terminal state. attempts is deliberately NOT incremented here: it
    # counts real handler-execution attempts, and none was made -- there
    # was no handler to run. Never used for a handler that raises (that
    # failure could be transient and keeps the normal retry treatment);
    # only for "no handler exists for this job_type at all".
    job = await session.get(Job, job_id)
    if job is None:
        raise ValueError(f"job {job_id} does not exist")
    now = clock()
    job.error = error
    if permanent:
        job.status = "failed"
    else:
        job.attempts += 1
        if job.attempts >= job.max_attempts:
            job.status = "failed"
        else:
            job.status = "pending"
            base_seconds = get_settings().job_retry_base_seconds
            job.next_run_at = now + timedelta(seconds=base_seconds * (2**job.attempts))
    job.updated_at = now
    await session.flush()
