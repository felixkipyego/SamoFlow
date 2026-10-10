# backend/app/ingest/scheduler.py
# Task 2.9.a: the scheduler's own core -- "which sources are due" and the
# self-scheduling `scheduler_tick` job's real logic (the handler itself,
# `handle_scheduler_tick()`, lives in job_handlers.py, matching that
# module's own established "thin glue, real logic lives in its own
# module" precedent, e.g. handle_ingest_crawl() -> crawl.run_crawl()).
#
# Design decision, made by the user directly, confirmed here, not
# invented: the periodic "check which sources are due" logic is itself a
# JOB, not a worker-loop tick -- reusing the already-built/proven job
# infrastructure (claim_next_job()'s own SELECT ... FOR UPDATE SKIP
# LOCKED, backoff via mark_job_failed(), reap_stuck_jobs(), the
# heartbeat), rather than inventing a second scheduling mechanism, and
# matching this project's own established pattern of every piece of
# recurring/async work becoming a job (ingest_url/ingest_crawl/
# ingest_upload/ingest_db).
#
# PER-TENANT, not global -- a real, necessary scope decision made with the
# user directly before building, not assumed. `jobs.tenant_id` is
# `nullable=False` with a cascading FK to `tenants.id` (Task 2.1.a) --
# confirmed live by reading the real schema, not assumed -- and a single
# GLOBAL scheduler_tick job scanning every tenant's sources in one query
# has no real, single owning tenant to satisfy that constraint with.
# Resolved by making `scheduler_tick` a per-tenant job instead: one exists
# per tenant, each one only ever scanning and re-enqueuing for its OWN
# tenant_id -- no schema change, and it matches the Job model's own
# existing comment precedent for the (not-yet-built) reconcile job
# ("compares an entire tenant against Qdrant, not one source at a time").
# The real cost, accepted plainly: N tenants means N independently-
# ticking jobs (not a single one), and the bootstrap below must be a
# recurring per-tenant sweep rather than a one-time global check (see
# ensure_scheduler_ticks_seeded()'s own docstring).
#
# `sources.last_run_at` (schema since Task 2.1.a, confirmed live via
# `alembic/versions/ef16e0a4ce7c_*.py` -- NOT a new column, no new
# migration needed here, correcting this task's own original premise) is
# written at the END of each adapter's own job handler
# (handle_ingest_url()/handle_ingest_crawl()/handle_ingest_upload()/
# handle_ingest_db(), job_handlers.py), on normal completion only --
# success AND partial-failure outcomes alike, since both mean the source's
# own sync genuinely RAN (the canonical contract's own "attempted, not
# guaranteed" framing, Step 2.7 closure summary). An uncaught,
# infrastructure-level exception never reaches that line at all, so
# last_run_at correctly stays unwritten/stale for an attempt that never
# actually completed -- the exact "ran" vs. "never completed" distinction
# this task's own instructions named.
#
# Suspended-tenant background processing, resolved (the Open marker 2.9.a
# originally recorded, now closed): a suspended tenant's own sources must
# NOT keep refreshing in the background, matching docs/SPEC.md's own
# tenant-suspension intent (§16: a suspended tenant is blocked from
# answering chat at all; background ingestion continuing regardless would
# be an inconsistent, surprising exception to that). THREE independent
# layers, not one, matching this project's own established defense-in-
# depth discipline (e.g. the database adapter's five independent "cannot
# write/read outside its allowlist" layers, Step 2.8):
#   (1) `ensure_scheduler_ticks_seeded()` never seeds a NEW tick for a
#       non-"active" tenant -- stops a fresh chain from ever starting for
#       an already-suspended tenant, and (for free, no extra code) means a
#       LATER un-suspension is picked up by this same recurring sweep,
#       exactly the way a broken chain is already self-healed today.
#   (2) `handle_scheduler_tick()` (job_handlers.py) checks `is_tenant_
#       active()` FIRST, before doing anything else: if the tenant is no
#       longer active, it returns immediately WITHOUT re-enqueuing its own
#       next occurrence -- the job itself still succeeds (a defensible,
#       cheap check that found nothing to do is not a failure), but the
#       self-perpetuating chain stops here instead of ticking forever for
#       a tenant with nothing to check. This is the layer that actually
#       matters for an ALREADY-RUNNING chain: a tenant can be suspended
#       mid-chain, with a scheduler_tick already pending/running for it --
#       (1) alone would never touch that existing chain, since it only
#       ever looks at tenants with NO pending/running tick at all.
#   (3) `get_due_sources()` itself also checks `is_tenant_active()` and
#       returns `[]` outright if not -- the innermost backstop, catching
#       anything that reaches this point regardless of why (a tick already
#       claimed/running at the exact moment of suspension; any other
#       future caller of this function that forgets its own check).
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DateTimeClock
from app.ingest.models import REFRESH_INTERVAL_DELTAS, Job, Source
from app.ingest.repository import IngestRepository
from app.tenancy.models import Tenant

# REFRESH_INTERVAL_DELTAS moved to app/ingest/models.py at Task 2.9.b --
# see that module's own header comment for why (repository.py's new
# create_source()/update_source() need it too, and importing it from here
# would be circular: this module already imports IngestRepository, below).
# Re-used here unchanged: an unrecognized value (a config drift this task
# cannot prevent, only detect) is still treated as "not due" rather than
# raised -- see get_due_sources()'s own docstring.

# The job_type each adapter's own completed-task work already established
# (2.6.d/2.6.e/2.7.d/2.8.e) -- `sources.type` uses the bare adapter name,
# `jobs.job_type` uses the `ingest_` prefix (2.1.a's own decision entry:
# "the two are deliberately not required to match 1:1"), so this mapping
# is the one real place that translates between them for the scheduler's
# own use. Keyed by the exact four values `ck_sources_type` enforces --
# an unrecognized `source.type` is structurally impossible through the
# real schema (defensive-only, see handle_scheduler_tick()'s own handling
# in job_handlers.py).
JOB_TYPE_FOR_SOURCE_TYPE: dict[str, str] = {
    "urls": "ingest_url",
    "crawl": "ingest_crawl",
    "upload": "ingest_upload",
    "database": "ingest_db",
}


async def is_tenant_active(session: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """`True` only if `tenant_id` names a real row with `status ==
    "active"` -- `False` for `"suspended"` AND for a tenant_id that
    matches no row at all (defensive; structurally unreachable through a
    real `scheduler_tick` job's own `tenant_id`, which is always a real
    FK, but this function makes no assumption about who else might call
    it). One shared check, used by three independent call sites (see this
    module's own header comment for the full defense-in-depth reasoning)
    rather than three copies of the identical query.
    """
    status = (
        await session.execute(select(Tenant.status).where(Tenant.id == tenant_id))
    ).scalar_one_or_none()
    return status == "active"


async def get_due_sources(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    clock: DateTimeClock = lambda: datetime.now(UTC),
) -> list[Source]:
    """Every ENABLED source belonging to `tenant_id` that is due for a
    refresh right now: `last_run_at IS NULL` (never run) or `last_run_at`
    older than its own `refresh_interval` maps to (docs/SPEC.md §5.4).

    Returns `[]` outright if the tenant is not `is_tenant_active()` --
    the innermost of three independent suspended-tenant guards (this
    module's own header comment has the full reasoning); a suspended
    tenant's own sources are never due, regardless of `last_run_at`.

    Fetches this tenant's own enabled sources (realistically a small,
    per-tenant list, never the whole table) and filters in Python rather
    than a SQL CASE-mapped interval expression -- the smallest correct
    mechanism for a plain string -> timedelta lookup (rule 11), not a
    SQL-level reimplementation of the same dict.

    A `source.refresh_interval` value outside `REFRESH_INTERVAL_DELTAS`
    (schema permits any string -- no CheckConstraint exists yet, that is
    Task 2.9.b's own job) is a configuration-drift case this task cannot
    prevent, only detect -- logged by the caller (handle_scheduler_tick(),
    job_handlers.py) and treated as NOT due, never raised: one source's
    own stale/invalid config must not abort the whole tenant's tick,
    matching this project's own established per-item-independence
    discipline (Step 2.7 closure summary).
    """
    if not await is_tenant_active(session, tenant_id):
        return []
    now = clock()
    sources = (
        (
            await session.execute(
                select(Source).where(
                    Source.tenant_id == tenant_id, Source.enabled.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    due: list[Source] = []
    for source in sources:
        if source.last_run_at is None:
            due.append(source)
            continue
        delta = REFRESH_INTERVAL_DELTAS.get(source.refresh_interval)
        if delta is None:
            continue
        if source.last_run_at <= now - delta:
            due.append(source)
    return due


async def ensure_scheduler_ticks_seeded(
    session: AsyncSession,
    *,
    interval_seconds: float,
    clock: DateTimeClock = lambda: datetime.now(UTC),
) -> list[uuid.UUID]:
    """For every tenant with no `scheduler_tick` job currently `"pending"`
    or `"running"`, seeds one. Returns the tenant ids seeded, for
    logging/testing, matching `reap_stuck_jobs()`'s own "return ids
    reclaimed" precedent.

    Called once per `run()`-loop iteration (worker.py), the SAME cadence
    as `reap_stuck_jobs()`/`write_heartbeat()` -- deliberately not a
    one-time startup check, for two real reasons stated plainly: (1) a
    brand-new tenant created while workers are already running (no
    restart) is picked up within one poll interval, not only at the next
    cold start; (2) this is ALSO the self-healing recovery path if a
    tenant's own self-re-enqueuing chain was ever broken (its own
    scheduler_tick job reached a terminal `"failed"` status after
    exhausting `max_attempts`) -- the identical backstop role
    `reap_stuck_jobs()` already plays for a different failure mode, reused
    rather than inventing a second recovery mechanism (rule 11).

    A real, necessary consequence found live, not assumed, before settling
    on this signature: the seeded job's own `next_run_at` is `clock() +
    interval_seconds`, deliberately NOT immediately claimable the way a
    real adapter job's own enqueue() call always is. An earlier version
    without this delay made every freshly-bootstrapped tick immediately
    compete with this SAME tenant's own already-pending real work for the
    very next claim slot -- confirmed live via `make test-all`, not
    theorized: a real test (`test_the_same_upload_id_ingested_twice_
    across_two_real_jobs_stays_idempotent`) failed because its own SECOND
    `run()` call claimed a LEFTOVER tick seeded (but not claimed) during
    its FIRST call, instead of the test's own freshly-enqueued second
    upload job, which has a newer `next_run_at` than that now-stale tick.
    Delaying the very first tick by the same `interval_seconds` every
    LATER self-re-enqueue already uses closes this race structurally (a
    freshly-bootstrapped tick can never be older than genuinely pending
    tenant work), not merely in the one case this was found in -- and
    matches production intent anyway: a brand-new tenant's own schedule
    starting a few minutes late is immaterial against day-granularity
    refresh intervals, and avoids a thundering-herd-style burst of ticks
    all becoming claimable on the very same iteration a fresh deployment
    first notices many existing tenants at once.

    Only seeds a tick for a tenant whose own `status == "active"` -- a
    suspended tenant never gets a NEW chain started (this module's own
    header comment has the full three-layer reasoning; this is layer 1 of
    3). A real, useful side effect of this being a recurring sweep rather
    than a one-time check, not a separate mechanism: a tenant later
    UN-suspended is picked up by this SAME query on its very next
    iteration (no pending/running tick exists for it, and it is active
    again), exactly the way an already-broken chain is already self-healed
    today -- resumption needs no code of its own.
    """
    missing_tenant_ids = (
        (
            await session.execute(
                select(Tenant.id).where(
                    Tenant.status == "active",
                    ~sa.exists(
                        select(Job.id).where(
                            Job.tenant_id == Tenant.id,
                            Job.job_type == "scheduler_tick",
                            Job.status.in_(("pending", "running")),
                        )
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    next_run_at = clock() + timedelta(seconds=interval_seconds)
    for tenant_id in missing_tenant_ids:
        await IngestRepository(tenant_id=tenant_id, session=session).enqueue(
            job_type="scheduler_tick", next_run_at=next_run_at
        )
    if missing_tenant_ids:
        await session.commit()
    return list(missing_tenant_ids)
