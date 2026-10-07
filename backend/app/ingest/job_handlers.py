# backend/app/ingest/job_handlers.py
# Task 2.6.d: the `urls` adapter -- the first real `JOB_HANDLERS`
# (app/worker.py) entry beyond `noop`/`sleep` test scaffolding, and the
# first real caller of the full ingestion pipeline through the actual
# worker loop (claim_next_job() -> this handler -> ingest_url(), 2.6.c ->
# embed_and_upsert(), 2.5.e). Named job_handlers.py, not jobs.py: this
# project already has app/ingest/queue.py (the claim/mark primitives) and
# app/ingest/models.py (the `Job` ORM row itself) -- "jobs.py" would read
# ambiguously next to both; this name says plainly "JOB_HANDLERS-
# registered functions live here," nothing else.
#
# A real, necessary signature widening found before building, not
# assumed away: worker.py's existing `JOB_HANDLERS` type is
# `Callable[[dict], Awaitable[None]]` -- `noop`/`sleep` only ever need
# their own `payload`, matching that shape exactly. A real handler needs
# more: `job.tenant_id` and `job.source_id` (first-class Job columns, NOT
# part of `payload` -- duplicating them into every job_type's own payload
# shape to keep the old signature would be worse, not simpler, rule 11).
# Resolved by widening `JOB_HANDLERS` to `Callable[[Job], Awaitable[None]]`
# -- the whole Job row, not just its payload -- and updating `_claim_and_
# process_one_job()`'s own call site plus `_noop_handler`/`_sleep_handler`
# to match (one-line, mechanical: `payload` -> `job.payload`, no behavior
# change). See worker.py's own updated header comment for the mechanical
# half of this change.
#
# Session/client acquisition: this handler opens its OWN session (`async
# with _session_factory()() as session:`) and gets its own Qdrant client
# (`qdrant.get_qdrant_client()`, the cached singleton) -- matching
# worker.py's own `_claim_one()`/`_mark_done()` precedent exactly (each a
# short, separate transaction/resource acquisition, not threaded through
# from outside), not a new pattern invented here.
#
# Config shape: `sources.config` (the Step 2.6 planning decision's own
# `{"urls": [...]}` shape for this adapter), read via `job.source_id` --
# NOT `job.payload`. `sources.config` is the tenant's own persistent
# declaration of which URLs this source re-checks (reusable by any future
# scheduled-refresh job for the same source); `job.payload` is a one-off
# job's own instructions, and this job_type currently needs none.
#
# Partial-failure / job-success policy (confirmed, not assumed, by
# reading ingest_url()'s own real source before building this): every
# EXPECTED outcome -- domain not verified, a fetch failure (SSRF
# rejection, timeout, connection error) -- is already returned as an
# IngestResult, never raised. Only an embed_and_upsert() failure
# propagates UNCAUGHT from ingest_url(), by that function's own explicit,
# already-documented design ("almost always an INFRASTRUCTURE-level
# problem... that affects every URL in the job equally"). This handler
# does NOT wrap each ingest_url() call in its own try/except: an
# IngestResult of any status lets the loop continue to the next URL
# (per-URL-specific, independent outcomes); an exception is deliberately
# left to propagate straight out of this handler and the loop, matching
# ingest_url()'s own stated intent exactly -- it would be pointless (and
# wasteful of time/API calls) to keep attempting URL 2, 3, ... after URL
# 1's embed_and_upsert() call fails for an infrastructure reason, since
# the same failure would almost certainly recur for every remaining URL.
# worker.py's existing `_claim_and_process_one_job()` already does exactly
# the right thing with a propagated exception (mark_job_failed(),
# non-permanent -- the standard backoff/retry path) with ZERO new code
# needed here for that case.
#
# The consequence: the JOB is marked SUCCEEDED if the handler returns
# normally -- i.e. if every URL in the list received a definitive,
# recorded IngestResult, REGARDLESS of how many came back "skipped" or
# "failed" -- and marked failed/retried only if an unexpected exception
# interrupted the attempt partway through. This matches the task's own
# stated lean ("a job's job is to attempt the list, not guarantee every
# URL indexes successfully"), made precise: "attempted" means "every URL
# got a recorded outcome," not "at least one succeeded" -- a job whose
# every URL is skipped (e.g. a tenant pasted URLs for since-revoked
# domains) still successfully DID what it was asked to attempt, and
# retrying it would not change that outcome, matching this project's own
# no-pointless-retry discipline (the "unknown job_type" permanent-failure
# precedent, worker.py).
#
# Heartbeat: deliberately NOT built here. The Step 2.6 breakdown's own
# decision (e) already assigns the heartbeat progress-reporting mechanism
# to 2.6.e specifically ("the mechanism is built at 2.6.e, inside the
# crawl handler's own loop, where the per-page checkpoints actually are"
# -- crawl can discover far more pages than a tenant is likely to paste
# into a `urls` list by hand). Not a silent skip: if a tenant-supplied
# `urls` list is ever long enough that this handler's own total runtime
# exceeds `HEARTBEAT_STALE_MULTIPLIER * worker_poll_interval_seconds`
# (worker.py), the heartbeat WOULD go stale while the worker is
# legitimately busy -- the exact risk worker.py's own pre-existing
# ASSUMPTION/Open marker already names. Left as that same, now-slightly-
# broadened, already-tracked risk rather than duplicating a second
# progress-heartbeat mechanism here ahead of 2.6.e's own real one; revisit
# if a real `urls` list is ever observed to run long enough in practice
# for this to matter before 2.6.e lands.
import logging

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select

from app import qdrant
from app.db import _session_factory
from app.ingest.models import Job, Source
from app.ingest.web_adapter import ingest_url

logger = logging.getLogger(__name__)


async def handle_ingest_url(job: Job) -> None:
    """The `urls` adapter's own `JOB_HANDLERS["ingest_url"]` entry.

    Reads `sources.config["urls"]` for `job.source_id`, then calls
    ingest_url() (2.6.c) once per URL, independently -- see this module's
    own header comment for the full partial-failure/job-success reasoning
    and the heartbeat decision.
    """
    if job.source_id is None:
        # Structurally impossible through the real enqueue() path for this
        # job_type (a `urls` source always has a real source_id), but
        # defensive rather than silently assuming a caller never passes a
        # malformed row -- a plain exception here takes the ordinary
        # non-permanent handler-raised path (worker.py's own
        # _claim_and_process_one_job()), not a bespoke permanent-failure
        # classification this one edge case does not warrant building
        # (rule 11).
        raise ValueError(f"job {job.id} has job_type='ingest_url' but no source_id")

    client: AsyncQdrantClient = qdrant.get_qdrant_client()
    async with _session_factory()() as session:
        source = (
            await session.execute(
                select(Source).where(
                    Source.id == job.source_id, Source.tenant_id == job.tenant_id
                )
            )
        ).scalar_one()
        urls = source.config.get("urls", [])
        for url in urls:
            result = await ingest_url(
                session,
                client,
                qdrant.COLLECTION_NAME,
                tenant_id=job.tenant_id,
                source_id=job.source_id,
                source_type="urls",
                url=url,
            )
            # Committed per URL, not once at the end: embed_and_upsert()
            # (2.5.e) writes to Qdrant directly, with no rollback tie to
            # this session -- if a LATER url's call raised and this
            # session were never committed, an EARLIER url's own
            # already-real Qdrant points would survive while its
            # `documents` row vanished, leaving the two stores out of
            # sync. Matches worker.py's own _claim_one()/_mark_done()
            # precedent: small, focused transactions, not one large one.
            await session.commit()
            logger.info(
                "handle_ingest_url: job %s url %s -> %s", job.id, url, result.status
            )
