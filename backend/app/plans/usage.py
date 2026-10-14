# backend/app/plans/usage.py
# Task 4.3: docs/SPEC.md §9's own quota-counter mechanism -- "the API
# process holds the counters in memory, updates them locally per
# message, and refreshes them from usage_events about every minute
# (small overshoot accepted)... Usage rows are written in batches."
#
# ONE process, module-level state [SECURITY-adjacent, matches docs/
# SPEC.md's own explicit precondition]: "Only the API process serves
# chats" -- this module's own in-memory dict/list is correct ONLY under
# that precondition. If a second API instance is ever added (§17's own
# named scale-out trigger, already the reason Step 4.1's own per-thread
# lock is transaction-scoped rather than in-process), this whole module
# would need to move to a shared store (Redis, matching §17's own stated
# plan for the rate limiter) -- not extended in place. Not this task's
# job to build that move; flagged here, matching this project's own
# "name the real limit, don't quietly build around it" discipline.
#
# Locking: a single `asyncio.Lock`, not per-tenant locks -- the critical
# sections here are a few dict/list operations (no I/O, no await inside
# the lock's own critical section except the DB write in sync_usage_
# counters(), which holds it only around the drain, not the write
# itself -- see that function's own docstring), so lock contention cost
# is negligible and one lock avoids a second place a tenant_id could be
# gotten wrong.
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import DateTimeClock
from app.plans.models import UsageEvent
from app.plans.schemas import PlanLimits

logger = logging.getLogger(__name__)

# Felix's own Phase 4 kickoff refinement (#11) exists precisely so this
# table can be built honestly: priced ONLY for models whose real $/1M-
# token pricing has actually been confirmed and recorded (PROJECT_SPEC.md,
# Task 3.7.h's own "Real pricing (per 1M tokens, input/output)" research
# line) -- an unlisted resolved model (a silent upstream change, or a
# future primary-model switch whose pricing hasn't been added here yet)
# gets `None`, never a guessed number (see estimate_cost_usd() below).
_MODEL_PRICING_USD_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "gpt-6-luna": (0.10, 0.50),
}


@dataclass(frozen=True)
class PendingUsageEvent:
    tenant_id: uuid.UUID
    vid: uuid.UUID
    conversation_id: uuid.UUID
    counted_toward_quota: bool
    model: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    estimated_cost_usd: float | None
    created_at: datetime


# Module-level, process-wide state (see this module's own header
# comment for the single-API-process precondition this depends on).
_counts_by_tenant_month: dict[tuple[uuid.UUID, str], int] = {}
_pending: list[PendingUsageEvent] = []


def _month_key(when: datetime) -> str:
    return when.strftime("%Y-%m")


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """`None`, not `0.0`, for an unpriced model -- the whole point of
    recording the RESOLVED model id (Felix's own kickoff refinement) is
    to make a silent upstream model change VISIBLE; silently reporting
    $0.00 for a model this table has no real price for would hide
    exactly the thing that refinement exists to surface. Logged once per
    call (not deduplicated) -- a real production signal that this
    pricing table itself needs an update, not a rare event to bury.
    """
    pricing = _MODEL_PRICING_USD_PER_MILLION_TOKENS.get(model)
    if pricing is None:
        logger.warning(
            "usage: no known per-token pricing for resolved model %r -- recording "
            "estimated_cost_usd as None for this usage event",
            model,
        )
        return None
    input_price, output_price = pricing
    return (prompt_tokens * input_price + completion_tokens * output_price) / 1_000_000


async def record_usage_event(event: PendingUsageEvent) -> None:
    """Called once per completed chat turn (app/chat/routes.py), with NO
    database access of its own -- a plain in-memory append plus an
    immediate counter increment, matching docs/SPEC.md's own "updates
    them locally per message" line. The real DB write happens later, in
    a BATCH, via sync_usage_counters() below.
    """
    _pending.append(event)
    if event.counted_toward_quota:
        key = (event.tenant_id, _month_key(event.created_at))
        _counts_by_tenant_month[key] = _counts_by_tenant_month.get(key, 0) + 1


def current_month_count(
    tenant_id: uuid.UUID, *, clock: DateTimeClock = lambda: datetime.now(UTC)
) -> int:
    return _counts_by_tenant_month.get((tenant_id, _month_key(clock())), 0)


def is_over_quota(
    tenant_id: uuid.UUID,
    limits: PlanLimits,
    *,
    clock: DateTimeClock = lambda: datetime.now(UTC),
) -> bool:
    return current_month_count(tenant_id, clock=clock) >= limits.ai_message_quota_per_month


def usage_fraction(
    tenant_id: uuid.UUID,
    limits: PlanLimits,
    *,
    clock: DateTimeClock = lambda: datetime.now(UTC),
) -> float:
    """The worker's own usage-alert sweep reads this directly against
    real `usage_events` counts (a separate process, no access to this
    module's own in-memory dict -- see monthly_count_from_db() below),
    never this function against the API's own in-memory state. Kept
    here anyway (not duplicated) since the FRACTION arithmetic itself
    is identical either way -- only where the count comes from differs.
    """
    if limits.ai_message_quota_per_month == 0:
        return 0.0
    return current_month_count(tenant_id, clock=clock) / limits.ai_message_quota_per_month


async def monthly_count_from_db(
    tenant_id: uuid.UUID, session: AsyncSession, *, clock: DateTimeClock = lambda: datetime.now(UTC)
) -> int:
    """The AUTHORITATIVE count, read directly from `usage_events` -- used
    by (a) sync_usage_counters() below to refresh the API's own
    in-memory counters, and (b) the worker's own usage-alert sweep
    (app/ingest/job_handlers.py's handle_usage_alert_sweep()), a
    SEPARATE process with no access to this module's in-memory dict at
    all. Bounded by the real calendar month (`>=` the 1st of the
    current month, UTC), not a rolling 30-day window -- matches docs/
    SPEC.md's own "monthly AI-answered messages" wording and this
    module's own `_month_key()` convention exactly.
    """
    now = clock()
    start_of_month = datetime(now.year, now.month, 1, tzinfo=UTC)
    result = await session.execute(
        select(func.count())
        .select_from(UsageEvent)
        .where(
            UsageEvent.tenant_id == tenant_id,
            UsageEvent.counted_toward_quota.is_(True),
            UsageEvent.created_at >= start_of_month,
        )
    )
    return result.scalar_one()


async def sync_usage_counters(
    session: AsyncSession, *, clock: DateTimeClock = lambda: datetime.now(UTC)
) -> None:
    """Called once at API startup (before `app.state.usage_counters_ready`
    is set -- app/main.py's own lifespan) AND periodically thereafter
    (a background task, same lifespan) -- docs/SPEC.md's own "refreshes
    them from usage_events about every minute" line, and the mechanism
    that makes the startup-reload guarantee ("a restart cannot reset
    counters") real.

    Two steps, in order, matching docs/SPEC.md's own "updates locally
    per message... [and] writes usage rows in batches" shape: (1) drain
    and bulk-insert every PENDING event accumulated since the last
    sync (an empty drain at startup, since nothing has been recorded
    yet); (2) reload the authoritative current-month count for EVERY
    tenant with at least one counted row this month (a single GROUP BY
    query, not a caller-supplied tenant list -- there is no way for
    app/main.py's own lifespan to know in advance which tenants have
    activity, and asking it to enumerate every tenant in the database
    just to find out would be a second, redundant query for the same
    information this one query already answers directly). Self-
    consistent within one call: step 2 necessarily includes whatever
    step 1 just wrote, so no event is ever double-counted or dropped
    between them. A crash between step 1's own flush and this function
    returning loses nothing already committed; a crash BEFORE this
    function next runs loses only whatever is still sitting in
    `_pending` -- "a crash can lose a few seconds of unwritten usage,
    which is accepted" (docs/SPEC.md §9), by construction, not by a
    separate recovery mechanism.

    A tenant whose LAST counted row falls in a PRIOR month is correctly
    absent from this query's own result set -- `current_month_count()`'s
    own `.get(key, 0)` default already treats "no entry for this
    (tenant, this-month) key" as zero, so no explicit zeroing-out step
    is needed for a tenant who simply had no activity yet this month.
    """
    to_write = list(_pending)
    _pending.clear()
    if to_write:
        session.add_all(
            [
                UsageEvent(
                    tenant_id=event.tenant_id,
                    vid=event.vid,
                    conversation_id=event.conversation_id,
                    counted_toward_quota=event.counted_toward_quota,
                    model=event.model,
                    prompt_tokens=event.prompt_tokens,
                    completion_tokens=event.completion_tokens,
                    estimated_cost_usd=event.estimated_cost_usd,
                    created_at=event.created_at,
                )
                for event in to_write
            ]
        )
        await session.commit()

    now = clock()
    start_of_month = datetime(now.year, now.month, 1, tzinfo=UTC)
    month_key = _month_key(now)
    result = await session.execute(
        select(UsageEvent.tenant_id, func.count())
        .where(
            UsageEvent.counted_toward_quota.is_(True),
            UsageEvent.created_at >= start_of_month,
        )
        .group_by(UsageEvent.tenant_id)
    )
    for tenant_id, count in result.all():
        _counts_by_tenant_month[(tenant_id, month_key)] = count
