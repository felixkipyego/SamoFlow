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
# Task 2.6.e part 2: handle_ingest_crawl() -- the `crawl` adapter's own
# JOB_HANDLERS entry, added below. Thin by design, matching handle_
# ingest_url()'s own shape exactly: session/client acquisition and the
# sources.config read happen here; the real orchestration (robots.txt ->
# sitemap-first discovery, otherwise BFS, the per-host throttle, the
# per-page loop) lives in app.ingest.crawl.run_crawl() -- this function
# is the thinnest possible glue between worker.py's own JOB_HANDLERS
# calling convention and that real logic, not a second copy of it.
#
# Heartbeat: THIS is where it is genuinely built (the Step 2.6 decision's
# own "the handler should write progress heartbeats periodically during
# its own work" lean, deferred here from handle_ingest_url(), 2.6.d).
# run_crawl()'s own `on_page_visited` callback is wired to worker.py's
# write_heartbeat() -- confirmed live, by reading write_heartbeat()
# directly, that no worker.py refactor is needed: it was always a plain,
# side-effecting function writing to a path, never tied to run()'s own
# loop structure. Imported here, not from app.ingest.crawl (which cannot
# import worker.py itself -- worker.py imports this module, which imports
# crawl.py; crawl.py importing worker.py back would be circular) --
# exactly why run_crawl() takes a plain injected callback instead.
#
# Duplication check after 2.6.d/2.6.e/2.6.f: handle_ingest_url() and
# handle_ingest_crawl() shared a byte-identical setup preamble -- the
# source_id guard, Qdrant client acquisition, session open, and Source
# fetch -- not just a similarly-shaped one. Extracted into
# _open_source_session() below; each handler's own per-item loop (a plain
# URL list vs. run_crawl()'s own orchestration), which is genuinely
# different, stays where it was, unchanged.
#
# Task 2.7.d: handle_ingest_upload() -- the `upload` adapter's own
# JOB_HANDLERS entry, added below. A 3rd real use of _open_source_
# session() (its own extraction, above, anticipated exactly this: "both
# handlers" already meant "both existing handlers at the time," not "at
# most two ever"). Follows handle_ingest_url()'s own exact shape, not
# handle_ingest_crawl()'s: a plain per-item loop over a tenant-declared
# list read from `sources.config`, each item independent, matching that
# function's own already-documented partial-failure/job-success policy
# word for word (see this module's own header comment above) -- ingest_
# upload() (2.7.c) already returns a definitive UploadIngestResult for
# every EXPECTED outcome ("skipped"/"unknown type", "failed"/corrupt
# file), the identical shape ingest_url() already established, so no new
# per-item exception handling is needed here either.
#
# Config shape, decided here, not inherited from `urls`/`crawl`: neither
# existing shape transfers -- `urls` is a bare list of strings (one piece
# of information per item); an upload needs TWO per item (which stored
# file, and its own real filename for the `documents` row/content-
# sniffing tiebreak, 2.7.a). `sources.config["uploads"]` is therefore a
# list of `{"upload_id": "<uuid>", "original_filename": "<name>"}`
# objects -- a list, not a single object, for the identical reason
# `urls` is a list: a tenant can plausibly upload more than one file
# under one source. `upload_id` is stored as its string form (JSON has
# no native UUID type) and parsed back via `uuid.UUID(...)` here --
# matching how every other JSONB config value in this codebase already
# round-trips through its own plain-JSON-compatible representation (e.g.
# `urls`' own plain strings).
#
# `storage_dir`/`max_docx_part_size_bytes` are read from Settings ONCE
# here (this handler's own call site), then passed down to ingest_
# upload() as plain parameters -- matching handle_ingest_crawl()'s own
# `settings.crawl_page_cap`/etc. precedent exactly; ingest_upload() itself
# stays Settings-free and fully testable with any value a test wants
# (2.7.c's own design).
#
# Heartbeat: deliberately NOT built here, same reasoning as handle_
# ingest_url()'s own (see this module's header comment above) -- a
# tenant-supplied upload list is realistically short (pasting/selecting a
# handful of files, not discovering hundreds of pages the way a crawl
# does), so this stays the "narrower but not reopened" case the
# HEARTBEAT_STALE_MULTIPLIER marker's own RESOLVED note (2.6.e) already
# anticipated for handle_ingest_url() -- not a new, separate risk.
#
# Task 2.8.e [SECURITY]: handle_ingest_db() -- the `database` adapter's own
# JOB_HANDLERS entry, wiring 2.8.a/b/c/d together for the first time
# through the real worker loop. A 4th real use of _open_source_session().
#
# Config shape, decided here: `sources.config["db_connection_id"]` is a
# single UUID string (parsed via uuid.UUID(...), matching `ingest_upload`'s
# own `upload_id` round-trip precedent) pointing at the already-encrypted
# `db_connections` row (2.8.a) -- NOT a second copy of that row's own
# `allowlisted_tables`/`row_templates` (2.8.c). Those two stay on
# `db_connections` alone, the one real source of truth: `sources.config`
# is kept exactly as minimal and pointer-only as `ingest_url()`'s/`ingest_
# upload()`'s own configs already are (a bare url list; a list of
# upload_id/filename pairs) -- neither duplicates data that already lives
# somewhere else more authoritative, and this config doesn't either.
#
# Setup: `_open_source_session()` (the source/tenant guard, Qdrant client,
# session) exactly as every other handler here, then a SECOND tenant-scoped
# fetch for the full `DbConnection` row (not just its credentials --
# `host`/`allowlisted_tables`/`row_templates` are needed too), inline via
# `select(...).where(id=..., tenant_id=job.tenant_id).scalar_one()` --
# matching `_open_source_session()`'s own identical inline-fetch-and-
# tenant-check shape for `Source`, not a new repository method for a
# single real call site (rule 11). `IngestRepository.get_decrypted_
# credentials()` independently re-checks tenant_id a second time (its own
# pre-existing, unmodified behavior) -- redundant with the fetch above by
# design, not an oversight, matching this project's own established
# "re-check a cheap safety property every time, don't trust a result from
# the past" precedent (ensure_read_only()/write_heartbeat()).
#
# connect_safely() + an explicit, up-front ensure_read_only() call: the
# connection is validated read-only ONCE, immediately after connecting,
# before any table is ever touched -- a writable connection is an
# INFRASTRUCTURE-level problem for the whole job (every table would be
# equally affected), not a single table's own concern, so it propagates
# UNCAUGHT here exactly like `UnsafeDatabaseHostError`/`CredentialEncryption
# Error` do, taking the ordinary job-level backoff/retry path. This is
# intentionally redundant with fetch_readonly_rows()'s own internal re-check
# on every call (2.8.b's own documented "cheap, re-run every time" design) --
# not a wasted duplicate: it fails fast before the first table's query
# rather than mid-loop, and it is the only read-only check that would ever
# run at all for a job whose `row_templates` happens to be empty.
#
# Per-table loop: iterates `db_connection.row_templates.items()` (not
# `allowlisted_tables` directly) -- `row_templates` is what actually
# carries each table's own column list/template/primary_key, so it is the
# real per-table work list; `allowlisted_tables` is consulted PER TABLE,
# inside build_table_select_query() (database_adapter.py, 2.8.e), as the
# authoritative check. This is deliberate, not incidental: `row_templates`
# and `allowlisted_tables` are two independently-editable JSONB columns on
# the same `db_connections` row (2.1.a) with nothing tying them together
# at the schema level, so a `row_templates` entry whose own table was since
# REMOVED from `allowlisted_tables` is a real, reachable drift state, not a
# hypothetical one -- exactly the "stale config" scenario this task's own
# test (c) names. build_table_select_query() raises TableNotAllowlistedError
# for that case (and InvalidIdentifierError for a malformed identifier) --
# caught HERE, per table, logged at ERROR level (a definitive, visible
# REJECTION, never a silent skip and never a silently-synced query), then
# `continue` to the next table: one table's own stale/invalid config does
# not abort the rest of the job, matching the per-item-independence policy
# below.
#
# Row cap / statement timeout: `Settings.db_sync_row_cap`/`db_sync_
# statement_timeout_seconds` (2.8.b) read ONCE here and passed to fetch_
# readonly_rows() per table -- matching `settings.crawl_page_cap`'s own
# established "handler reads Settings once, primitive stays Settings-free"
# precedent. Max-synced-rows-per-plan, decided here: the SAME number as
# 2.8.b's own per-query row cap, not a second, independent setting --
# `db_sync_row_cap`'s own docstring (app/config.py) already cites docs/
# SPEC.md's "maximum synced database rows... from the plan" line as ITS
# OWN justification, written at 2.8.b before any job handler existed to
# apply it; adding a second field with the same real-world meaning would
# be a parameter this step does not need (rule 11). Composition, stated
# plainly: because Step 2.8 is single-table-per-sync (2.8.c's own scope
# decision, no joins, no cross-table query), there is no single aggregate
# query a combined cap could apply to -- each table gets its own
# fetch_readonly_rows() call, each independently bounded by this one
# setting, so a job syncing N allowlisted tables can synchronize up to
# N * db_sync_row_cap rows in total, not one shared ceiling across the
# whole job. A true per-PLAN aggregate (capping one tenant's total synced
# rows across every table/connection) still needs Task 4.3's own real
# `plans.limits` mechanism to express and enforce correctly -- explicitly
# NOT solved here, matching `crawl_page_cap`'s/`upload_max_size_bytes`'s own
# identical "global Settings default, plans.limits sourcing deferred"
# precedent, not newly resolved by this task.
#
# Per-row loop, per-item partial-failure policy -- matching the canonical
# contract (Step 2.7 closure summary) in SPIRIT, but via a genuinely
# different MECHANISM than handle_ingest_url()'s/handle_ingest_upload()'s
# own, confirmed by reading ingest_db_row()'s real contract before building
# this, not assumed identical. ingest_url()/ingest_upload() each already
# return a definitive Result for every EXPECTED outcome, never raising --
# this handler therefore needs no per-item try/except of its own.
# ingest_db_row() (2.8.d) is deliberately NOT built that way: its own
# header comment states a rendering/identity problem (MissingColumnError,
# MissingPrimaryKeyError, TemplateRenderError, all app/ingest/row_
# templates.py) "propagates UNCAUGHT from this function... not a per-row
# outcome this result type needs to represent" -- a decision 2.8.d
# correctly left to ITS caller (this handler, the first one to exist).
# Catching exactly those three exception types here, per row -- the
# IDENTICAL shape `ingest_upload()`'s own dispatch table already
# established for a per-item EXTRACTOR failure (`pypdf.errors.PyPdfError`,
# etc., job_handlers.py's own header comment above) -- is therefore the
# correct, consistent way to achieve the SAME per-item-independence
# contract through a lower primitive with a genuinely different shape, not
# a deviation from it. A caught row is logged at ERROR level and skipped
# (`continue`) -- no `session.commit()` for it, since ingest_db_row() raises
# BEFORE ever touching the session (derive_row_identity()/render_row_to_
# text() are both pure functions over the already-fetched row), so there is
# nothing to roll back. A successfully-ingested row is committed
# immediately, per row, not once at the end -- the identical reasoning
# handle_ingest_url()'s/handle_ingest_upload()'s own per-item commit
# comments already give (embed_and_upsert() writes to Qdrant with no
# rollback tie to this session). The JOB succeeds if every table/row
# received a recorded, definitive outcome (ingested/unchanged/rejected/
# failed-and-logged), regardless of how many were rejected or failed --
# not "every row must succeed" -- matching the canonical contract's own
# "attempted, not guaranteed" framing exactly.
#
# Heartbeat: deliberately NOT built here, same reasoning as handle_ingest_
# upload()'s own (see that function's own comment above) -- a tenant's own
# allowlisted-table list is realistically bounded by Settings.db_sync_
# row_cap per table and a small number of tables per connection, not an
# open-ended discovery process the way a crawl is.
#
# Task 2.9.a: `sources.last_run_at` (schema since 2.1.a, confirmed live --
# NOT a new column) is now written, at the END of each of the four adapter
# handlers above, right before each one's own final `return` -- on BOTH
# success and partial-failure outcomes alike, never only on a clean
# success. Reasoning, stated once here rather than four times: a source
# whose job completed with some items skipped/failed still genuinely RAN
# -- it is not "never attempted," matching every one of these handlers'
# own already-established canonical contract ("attempted, not
# guaranteed," Step 2.7 closure summary). The single write point that
# correctly captures this for free, with no new control flow: the last
# line before each handler returns normally. An uncaught, infrastructure-
# level exception (the ONLY thing that takes the job-level failure path
# for any of these four) never reaches that line at all, so last_run_at
# correctly stays unwritten/stale for an attempt that genuinely never
# completed -- exactly the "ran" vs. "never completed" distinction this
# task was asked to decide, resolved by where the write sits in the
# control flow, not by a separate try/except around it.
#
# handle_scheduler_tick() -- the scheduler's own JOB_HANDLERS entry, added
# below. A genuinely different shape from the four adapter handlers: no
# Qdrant client, no _open_source_session() (job.source_id is always None
# for this job type, the identical already-established nullable-source_id
# precedent the Job model's own comment already names for "not tied to
# one source row"). See app/ingest/scheduler.py's own header comment for
# the full per-tenant design decision (confirmed with the user directly,
# not assumed) and the bootstrap/self-healing sweep
# (ensure_scheduler_ticks_seeded(), wired into worker.py's own run() loop).
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from qdrant_client import AsyncQdrantClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from app import qdrant
from app.agent import checkpointer
from app.agent.context import build_thread_id
from app.config import get_settings
from app.db import _session_factory
from app.handoff.models import Escalation
from app.ingest.crawl import run_crawl
from app.ingest.database_adapter import (
    InvalidIdentifierError,
    TableNotAllowlistedError,
    build_table_select_query,
    connect_safely,
    ensure_read_only,
    fetch_readonly_rows,
    ingest_db_row,
)
from app.ingest.models import JOB_TYPE_FOR_SOURCE_TYPE, DbConnection, Document, Job, Source
from app.ingest.qdrant_writer import delete_points_for_document
from app.ingest.reconcile import reconcile_tenant
from app.ingest.repository import IngestRepository
from app.ingest.row_templates import MissingColumnError, MissingPrimaryKeyError, TemplateRenderError
from app.ingest.scheduler import get_due_sources, is_tenant_active
from app.ingest.upload_adapter import ingest_upload
from app.ingest.web_adapter import ingest_url, is_domain_verified
from app.notifications.service import send_email
from app.plans import usage
from app.plans.models import EmailLog
from app.plans.service import get_plan_limits
from app.tenancy.repository import TenantScopedRepository

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _open_source_session(
    job: Job, job_type: str
) -> AsyncIterator[tuple[AsyncQdrantClient, AsyncSession, Source]]:
    """Shared handler setup: guard, Qdrant client, session, Source fetch.

    Extracted from handle_ingest_url() and handle_ingest_crawl() (the
    duplication check after 2.6.d/2.6.e/2.6.f) -- both handlers need
    exactly this before diverging into their own, genuinely different,
    per-item loop. `job_type` is only used to name the job_type in the
    defensive-guard error message below.
    """
    if job.source_id is None:
        # Structurally impossible through the real enqueue() path for this
        # job_type (every source always has a real source_id), but
        # defensive rather than silently assuming a caller never passes a
        # malformed row -- a plain exception here takes the ordinary
        # non-permanent handler-raised path (worker.py's own
        # _claim_and_process_one_job()), not a bespoke permanent-failure
        # classification this one edge case does not warrant building
        # (rule 11).
        raise ValueError(f"job {job.id} has job_type={job_type!r} but no source_id")

    client: AsyncQdrantClient = qdrant.get_qdrant_client()
    async with _session_factory()() as session:
        source = (
            await session.execute(
                select(Source).where(
                    Source.id == job.source_id, Source.tenant_id == job.tenant_id
                )
            )
        ).scalar_one()
        yield client, session, source


async def _skip_if_source_disabled_or_tenant_inactive(
    session: AsyncSession, source: Source
) -> bool:
    """Task 2.9.g [SECURITY]: the adapter handlers' own defense-in-depth
    backstop against a disabled source, OR a now-suspended tenant, ever
    getting fresh, visible content written -- the handler-level layer of
    the same two-layer fix this duplication check applied to
    `request_refresh_now()` (app/ingest/repository.py's own primary
    fix: that method now rejects both cases outright, before ever
    enqueueing a job). This backstop protects against ANY other path
    that might someday enqueue a job for a disabled source or a
    suspended tenant, not just that one already-fixed caller -- matching
    this project's own established defense-in-depth discipline (e.g. the
    suspended-tenant three-layer pattern, Task 2.9.a's own follow-up).

    Originally `_skip_if_source_disabled()` (source.enabled only,
    Task 2.9.g's own Part 1 fix D.1); widened here at the same task's
    Part 2 close-out review (Finding 1) to also cover tenant suspension,
    rather than adding a second, separate check function -- the two
    conditions are both "nothing to do here, not a failure" gates
    checked at the exact same call site in all four handlers below, so
    one shared function reporting "skip or not" covers both without a
    caller needing to call two functions and OR the results itself.
    Tenant checked FIRST, before source.enabled -- the same broader-
    before-narrower order `request_refresh_now()` now uses (tenant-
    level gate before source-level gate), reusing `is_tenant_active()`
    (app/ingest/scheduler.py, already imported by this module) rather
    than a second, parallel tenant-status query.

    Returns `True` (the caller must return immediately, treating this as
    nothing to do, not a failure -- the identical "cheap check that found
    nothing to do is not a failure" philosophy `_check_active_and_
    reenqueue_self()` above already uses for a suspended tenant) if
    either condition holds. Shared by all four adapter handlers below,
    each of which already holds `session`/`source` via `_open_source_
    session()` -- a single one-line call per handler, not a repeated
    multi-line block, since the real logic (and its own log lines) lives
    here once.
    """
    if not await is_tenant_active(session, source.tenant_id):
        logger.info("tenant %s is not active, skipping this job", source.tenant_id)
        return True
    if not source.enabled:
        logger.info("source %s is disabled, skipping this job", source.id)
        return True
    return False


async def handle_ingest_url(job: Job) -> None:
    """The `urls` adapter's own `JOB_HANDLERS["ingest_url"]` entry.

    Reads `sources.config["urls"]` for `job.source_id`, then calls
    ingest_url() (2.6.c) once per URL, independently -- see this module's
    own header comment for the full partial-failure/job-success reasoning
    and the heartbeat decision.
    """
    async with _open_source_session(job, "ingest_url") as (client, session, source):
        if await _skip_if_source_disabled_or_tenant_inactive(session, source):
            return
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
        # Task 2.9.a: last_run_at set only once every URL has received a
        # definitive outcome -- i.e. the handler is about to return
        # normally, matching "ran" exactly (see this module's own header
        # comment's Task 2.9.a paragraph for the full "ran vs. never
        # completed" reasoning). An uncaught exception from inside the
        # loop above never reaches this line, so a genuinely interrupted
        # attempt correctly leaves last_run_at unchanged.
        source.last_run_at = func.now()
        await session.commit()


async def handle_ingest_crawl(job: Job) -> None:
    """The `crawl` adapter's own `JOB_HANDLERS["ingest_crawl"]` entry.

    Reads `sources.config["seed_url"]` for `job.source_id` (the Step 2.6
    planning decision's own config shape for this adapter -- a single
    seed URL, discovery finds the rest; `job.payload` is unused here too,
    same reasoning as handle_ingest_url()'s own `urls` config). Delegates
    all real orchestration to app.ingest.crawl.run_crawl() -- see that
    function's own docstring for the sitemap-vs-BFS branching, the
    per-host throttle, and the partial-failure/job-success policy (IDENTICAL
    to handle_ingest_url()'s own, see this module's header comment).

    The heartbeat write (worker.py's own write_heartbeat()) is wired as
    run_crawl()'s `on_page_visited` callback -- fired once per page
    actually visited, giving a real, busy crawl genuine mid-job progress
    checkpoints, not just one heartbeat write at job start (the gap this
    task closes, deferred from 2.6.d).

    Task 2.9.d [SECURITY]: crawl staleness cleanup, added at the end of
    this same run, AFTER `last_run_at`/`last_crawl_started_at` are set and
    committed -- deliberately CRAWL-ONLY, not `handle_ingest_url()`
    (confirmed against docs/SPEC.md §5.4's own exact wording, "Pages
    missing from two consecutive full crawls are deleted" -- crawl
    discovery is implicit, so a page vanishing from the discovered set is
    ambiguous between "removed" and "a transient fetch failure," which is
    exactly what the two-consecutive-miss debounce exists to resolve; the
    url-list adapter's own input is an explicit, tenant-supplied list --
    a URL disappearing from it is already unambiguous, with nothing to
    debounce, and what should then happen to it is a source-lifecycle
    question, Task 2.9.e's own job, not a per-crawl detection problem).
    See `_cleanup_stale_crawl_documents()`'s own docstring below for the
    full two-consecutive-miss mechanism and the failed-run exclusion.
    """
    # Imported here, not at module level: see this module's own header
    # comment for why (worker.py -> job_handlers.py -> crawl.py would be
    # circular if crawl.py imported worker.py directly; importing
    # worker.py FROM job_handlers.py, which worker.py itself already
    # imports, is not circular -- Python resolves it fine since by the
    # time this function is actually CALLED, worker.py's own module
    # object is fully initialized).
    from app.worker import HEARTBEAT_PATH, write_heartbeat

    settings = get_settings()
    run_started_at = datetime.now(UTC)
    async with _open_source_session(job, "ingest_crawl") as (client, session, source):
        if await _skip_if_source_disabled_or_tenant_inactive(session, source):
            return
        seed_url = source.config["seed_url"]
        # Task 2.10.b [SECURITY]: the handler-level backstop for create_
        # source()'s own matching check (app/ingest/repository.py) -- a
        # source verified at CREATION time can have that verification
        # REVOKED before this job ever runs (`revoke_domain()`, 2.2.g),
        # and create_source() has no way to re-check that later. Re-
        # checked here, before run_crawl()'s own discovery phase starts
        # (robots.txt/sitemap/BFS), not just trusting the row's own
        # already-stale creation-time state -- the identical reasoning
        # ingest_url() already uses for re-checking per page rather than
        # once per job. Treated exactly like the disabled/suspended skip
        # immediately above: return with no last_run_at write, since no
        # discovery happened at all, and `last_crawl_started_at`'s own
        # staleness-cleanup comparison (2.9.d) must never run against a
        # job that made zero real discovery attempts -- doing so would
        # wrongly treat every currently-indexed page as "missing" after
        # just two ticks of a revoked domain, deleting real content for
        # the wrong reason.
        hostname = httpx.URL(seed_url).host
        if not await is_domain_verified(session, job.tenant_id, hostname):
            logger.info(
                "domain %s is not verified for tenant %s, skipping this crawl job",
                hostname, job.tenant_id,
            )
            return
        await run_crawl(
            session,
            client,
            qdrant.COLLECTION_NAME,
            tenant_id=job.tenant_id,
            source_id=job.source_id,
            seed_url=seed_url,
            page_cap=settings.crawl_page_cap,
            user_agent=settings.crawl_user_agent,
            delay_seconds=settings.crawl_request_delay_seconds,
            on_page_visited=lambda: write_heartbeat(HEARTBEAT_PATH),
        )
        # Task 2.9.a: last_run_at set once run_crawl() returns normally --
        # see handle_ingest_url()'s own identical comment above for the
        # full reasoning.
        source.last_run_at = func.now()
        # Task 2.9.d: the PREVIOUS completed run's own start time is read
        # BEFORE it is overwritten below -- this is the exact threshold
        # _cleanup_stale_crawl_documents() needs. Both this read and the
        # write below are reached ONLY on run_crawl()'s own normal return,
        # the identical guarantee last_run_at already relies on -- an
        # uncaught exception from run_crawl() never reaches this line, so
        # a failed/interrupted run is invisible to this mechanism: it
        # never advances `last_crawl_started_at` and never runs the
        # cleanup below, exactly the fail-closed property this task's own
        # instructions required.
        previous_crawl_started_at = source.last_crawl_started_at
        source.last_crawl_started_at = run_started_at
        await session.commit()

        if previous_crawl_started_at is not None:
            await _cleanup_stale_crawl_documents(
                session,
                client,
                qdrant.COLLECTION_NAME,
                tenant_id=job.tenant_id,
                source_id=job.source_id,
                previous_crawl_started_at=previous_crawl_started_at,
            )


async def _cleanup_stale_crawl_documents(
    session: AsyncSession,
    client: AsyncQdrantClient,
    collection_name: str,
    *,
    tenant_id: uuid.UUID,
    source_id: uuid.UUID,
    previous_crawl_started_at: datetime,
) -> None:
    """Task 2.9.d: deletes every document for `source_id` that was missing
    from BOTH the run immediately before this one AND this run itself --
    docs/SPEC.md §5.4's own "pages missing from two consecutive full
    crawls are deleted."

    The criterion, evaluated per document, is exactly one comparison:
    `documents.last_seen_at < previous_crawl_started_at`. `last_seen_at`
    is NOT a new signal -- confirmed live before building anything (per
    this task's own "if anything looks wrong, say so" instruction): it
    already exists and is already written, unconditionally, by
    finish_ingest() (app/ingest/qdrant_writer.py, since Tasks 2.5.d/2.6.c)
    on every page this OR any other adapter actually finds and processes.
    A page this run genuinely found already has `last_seen_at` bumped to
    (at earliest) this run's own start, which is always AFTER
    `previous_crawl_started_at` by construction -- so only a page missing
    from the CURRENT run is even a candidate. Of those candidates, one
    with `last_seen_at >= previous_crawl_started_at` was still found
    during the PREVIOUS run (exactly one miss so far, not deleted); one
    with `last_seen_at < previous_crawl_started_at` was missing from the
    previous run too (two consecutive misses, deleted). A page that
    reappears on a later run has `last_seen_at` bumped forward again by
    that same finish_ingest() write, which is why reappearing always
    clears any accumulated miss count for free, with no separate counter
    to reset.

    Qdrant points deleted BEFORE the `documents` row, not after --
    deliberately the OPPOSITE order from embed_and_upsert()/finish_ingest()'s
    own "Qdrant first, Postgres commit after" precedent (handle_ingest_url()'s
    own comment), and for a mirror-image reason: an upsert's own worse
    failure mode is "a documents row exists but has no matching, findable
    point" (a chunk silently unfindable despite its own row claiming to
    exist), so Qdrant goes first there. A DELETION's own worse failure
    mode is the opposite -- content that should be gone staying findable
    to visitors (`documents` row deleted but its Qdrant points survive) --
    so here Qdrant goes first instead: if the subsequent `documents`
    delete+commit ever failed, the WORST surviving state is an orphaned
    Qdrant point with no matching row, which Task 2.9.f's own (not yet
    built) nightly reconcile job already exists to catch and remove, not
    content that stays answerable to visitors after it should not be.
    Deleted and committed per document, not batched -- matching this
    project's own established per-item independence discipline (Step 2.7
    closure summary): one stale document's own failure does not block
    cleanup of the others.

    No per-item try/except around either delete call: unlike the
    canonical per-item CONTENT contract (a fetch failure, an unverified
    domain -- each an expected, per-item outcome), a Qdrant/Postgres
    delete failing here is an infrastructure-level problem with no
    expected-failure meaning of its own, so it is left to propagate
    uncaught and take the ordinary job-level retry path, matching every
    other infrastructure-level failure in this codebase. A retry is safe
    and naturally idempotent: it re-queries for still-stale documents
    fresh, so one already deleted on a prior partial attempt simply no
    longer matches and is never touched again.
    """
    stale_documents = (
        (
            await session.execute(
                select(Document).where(
                    Document.source_id == source_id,
                    Document.last_seen_at < previous_crawl_started_at,
                )
            )
        )
        .scalars()
        .all()
    )
    for document in stale_documents:
        await delete_points_for_document(
            client, collection_name, tenant_id=tenant_id, document_id=document.id
        )
        await session.delete(document)
        await session.commit()
        logger.info(
            "_cleanup_stale_crawl_documents: source %s deleted stale document %s (url=%r)",
            source_id,
            document.id,
            document.url,
        )


async def handle_ingest_upload(job: Job) -> None:
    """The `upload` adapter's own `JOB_HANDLERS["ingest_upload"]` entry.

    Reads `sources.config["uploads"]` for `job.source_id` -- a list of
    `{"upload_id": "<uuid>", "original_filename": "<name>"}` entries (see
    this module's own header comment for why this shape, not a bare list
    like `urls`'s own). Calls ingest_upload() (2.7.c) once per entry,
    independently -- see this module's own header comment for the full
    partial-failure/job-success reasoning, identical to handle_ingest_
    url()'s own.
    """
    settings = get_settings()
    storage_dir = Path(settings.upload_storage_path)
    async with _open_source_session(job, "ingest_upload") as (client, session, source):
        if await _skip_if_source_disabled_or_tenant_inactive(session, source):
            return
        uploads = source.config.get("uploads", [])
        for entry in uploads:
            upload_id = uuid.UUID(entry["upload_id"])
            original_filename = entry["original_filename"]
            result = await ingest_upload(
                session,
                client,
                qdrant.COLLECTION_NAME,
                tenant_id=job.tenant_id,
                source_id=job.source_id,
                upload_id=upload_id,
                original_filename=original_filename,
                storage_dir=storage_dir,
                max_docx_part_size_bytes=settings.docx_max_part_size_bytes,
            )
            # Committed per upload, not once at the end -- same reasoning
            # as handle_ingest_url()'s own identical per-item commit (see
            # that function's own comment above): embed_and_upsert()
            # writes to Qdrant directly, with no rollback tie to this
            # session, so a later item's own failure must never be able
            # to roll back an earlier item's already-real Qdrant points
            # along with its own now-vanished `documents` row.
            await session.commit()
            logger.info(
                "handle_ingest_upload: job %s upload %s (%s) -> %s",
                job.id,
                upload_id,
                original_filename,
                result.status,
            )
        # Task 2.9.a: last_run_at set once every upload has received a
        # definitive outcome -- see handle_ingest_url()'s own identical
        # comment above for the full reasoning.
        source.last_run_at = func.now()
        await session.commit()


async def handle_ingest_db(job: Job) -> None:
    """The `database` adapter's own `JOB_HANDLERS["ingest_db"]` entry.

    Reads `sources.config["db_connection_id"]` for `job.source_id`, fetches
    the real, tenant-scoped `db_connections` row (host, allowlisted_tables,
    row_templates) plus its decrypted credentials, opens one real
    connection via connect_safely() (2.8.a) and confirms it read-only via
    ensure_read_only() (2.8.b) up front, then for each table named in
    row_templates: builds its own SELECT via build_table_select_query()
    (2.8.e, database_adapter.py -- checks is_table_allowlisted() first),
    runs it via fetch_readonly_rows() (2.8.b), and calls ingest_db_row()
    (2.8.d) once per returned row. See this module's own header comment
    for the full config-shape, per-table-rejection and per-row-failure
    reasoning, and why this handler's own partial-failure mechanism
    (per-row/per-table try/except) is a deliberate, necessary difference
    from handle_ingest_url()'s/handle_ingest_upload()'s own shape, not an
    inconsistency.
    """
    settings = get_settings()
    async with _open_source_session(job, "ingest_db") as (client, session, source):
        if await _skip_if_source_disabled_or_tenant_inactive(session, source):
            return
        db_connection_id = uuid.UUID(source.config["db_connection_id"])
        db_connection = (
            await session.execute(
                select(DbConnection).where(
                    DbConnection.id == db_connection_id,
                    DbConnection.tenant_id == job.tenant_id,
                )
            )
        ).scalar_one()
        credentials = await IngestRepository(
            tenant_id=job.tenant_id, session=session
        ).get_decrypted_credentials(db_connection_id)

        conn = await connect_safely(db_connection.host, credentials)
        try:
            await ensure_read_only(conn)
            for table, template_config in db_connection.row_templates.items():
                try:
                    query = build_table_select_query(
                        db_connection.allowlisted_tables, table, template_config
                    )
                except (TableNotAllowlistedError, InvalidIdentifierError) as exc:
                    logger.error(
                        "handle_ingest_db: job %s table %s rejected, not synced: %s",
                        job.id,
                        table,
                        exc,
                    )
                    continue

                rows = await fetch_readonly_rows(
                    conn,
                    query,
                    row_cap=settings.db_sync_row_cap,
                    timeout_seconds=settings.db_sync_statement_timeout_seconds,
                )
                for row in rows:
                    # The same f"{table}:{pk_value}" shape derive_row_
                    # identity() (row_templates.py) uses for the REAL
                    # identity, recomputed here defensively for LOGGING
                    # only (not by calling that function again) -- matching
                    # handle_ingest_url()'s/handle_ingest_upload()'s own
                    # convention of logging the thing that actually varies
                    # per iteration (the url; the upload_id/filename pair),
                    # so a many-row table doesn't produce identical log
                    # lines for every row. Deliberately tolerant of a
                    # missing/NULL primary key -- precisely the case
                    # MissingPrimaryKeyError itself reports below -- so the
                    # log line still shows a distinguishing value (e.g.
                    # "products:None") instead of raising a second time or
                    # silently falling back to nothing.
                    pk_column = template_config["primary_key"]
                    pk_value = row[pk_column] if pk_column in row else None
                    row_identity_for_log = f"{table}:{pk_value}"
                    try:
                        result = await ingest_db_row(
                            session,
                            client,
                            qdrant.COLLECTION_NAME,
                            tenant_id=job.tenant_id,
                            source_id=job.source_id,
                            table=table,
                            row=row,
                            template_config=template_config,
                        )
                    except (
                        MissingColumnError,
                        MissingPrimaryKeyError,
                        TemplateRenderError,
                    ) as exc:
                        logger.error(
                            "handle_ingest_db: job %s row %s failed to render: %s",
                            job.id,
                            row_identity_for_log,
                            exc,
                        )
                        continue
                    # Committed per row, not once at the end -- same
                    # reasoning as every other handler's own identical
                    # per-item commit (see this module's own header
                    # comment above).
                    await session.commit()
                    logger.info(
                        "handle_ingest_db: job %s row %s -> %s",
                        job.id,
                        row_identity_for_log,
                        result.status,
                    )
        finally:
            await conn.close()

        # Task 2.9.a: last_run_at set once every table/row has received a
        # definitive outcome -- see handle_ingest_url()'s own identical
        # comment above for the full reasoning. Deliberately after the
        # try/finally above (the connection is already closed): a crash
        # severe enough to skip conn.close() entirely would also skip this
        # line, which is correct -- that is exactly the "never completed"
        # case last_run_at must NOT reflect.
        source.last_run_at = func.now()
        await session.commit()


async def _check_active_and_reenqueue_self(
    session: AsyncSession, job: Job, *, job_type: str, interval_seconds: float
) -> bool:
    """Task 2.9.g duplication-check fix: shared by `handle_scheduler_
    tick()`/`handle_reconcile()` below -- their own suspended-tenant
    guard + self-re-enqueue preamble had become a second near-identical
    block (confirmed by reading both directly, beyond the seeding-
    function pair already extracted in app/ingest/scheduler.py).

    Returns `False` if the tenant is no longer active: the caller must
    return immediately -- no self-re-enqueue, no real per-tenant work.
    The job itself still succeeds (a cheap check that correctly found
    nothing to do is not a failure); the real effect is that this
    tenant's own self-perpetuating chain stops HERE instead of ticking
    forever for a tenant with nothing to check. The matching bootstrap
    sweep (`ensure_scheduler_ticks_seeded()`/`ensure_reconcile_ticks_
    seeded()`, app/ingest/scheduler.py) only ever looks at tenants with
    NO pending/running tick at all, so it would never touch an ALREADY-
    RUNNING chain like this one -- this check is what actually stops a
    chain suspended mid-flight.

    Returns `True` once the next occurrence has been enqueued and
    committed: the caller should proceed with its own real per-tenant
    work. Deliberately this order, not the reverse -- if that real work
    were ever to raise an unexpected, uncaught exception (an
    infrastructure-level problem), the self-re-enqueue has already
    committed by then, so this tenant's own chain survives regardless of
    what happens afterward.
    """
    if not await is_tenant_active(session, job.tenant_id):
        logger.info(
            "%s: tenant %s is not active, letting its own chain end here",
            job_type,
            job.tenant_id,
        )
        return False

    next_run_at = datetime.now(UTC) + timedelta(seconds=interval_seconds)
    await IngestRepository(tenant_id=job.tenant_id, session=session).enqueue(
        job_type=job_type, next_run_at=next_run_at
    )
    await session.commit()
    return True


async def handle_scheduler_tick(job: Job) -> None:
    """The scheduler's own `JOB_HANDLERS["scheduler_tick"]` entry -- a
    genuinely different shape from the four adapter handlers above: no
    Qdrant client, no `_open_source_session()` (this job has no
    `source_id` of its own -- `job.source_id` is always `None` here,
    matching the Job model's own existing `source_id`-nullable precedent
    for exactly this "not tied to one source row" case).

    Per-tenant (app/ingest/scheduler.py's own header comment has the full
    design reasoning for why, not a global job): re-enqueues its OWN next
    occurrence `Settings.scheduler_tick_interval_seconds` in the future
    FIRST, before finding and enqueueing this tenant's own due sources --
    deliberately this order, not the reverse: if enqueueing a due source
    were ever to raise an unexpected, uncaught exception (an
    infrastructure-level problem, not a per-source one -- see below), the
    self-re-enqueue has already committed by then, so this tenant's own
    scheduling chain survives regardless of what happens afterward.
    `ensure_scheduler_ticks_seeded()` (app/ingest/scheduler.py, wired into
    worker.py's own run() loop) is the backstop if the chain is ever
    broken anyway (e.g. this job itself eventually exhausts max_attempts).

    Per-source enqueueing is NOT wrapped in its own try/except, unlike the
    four adapter handlers' own per-item loops: `enqueue()`'s only two
    expected exceptions (UnknownJobTypeError, UnknownSourceError) cannot
    actually occur here -- `job_type` always comes from the fixed,
    already-valid JOB_TYPE_FOR_SOURCE_TYPE mapping, and `source.id` was
    just read from a real row in this same transaction. Anything else
    really would be an infrastructure-level problem, correctly left to
    propagate uncaught and take the ordinary job-level backoff/retry path.

    Suspended-tenant check FIRST, before anything else (layer 2 of the
    three described in scheduler.py's own header comment), and the self-
    re-enqueue itself -- both now shared with `handle_reconcile()` below
    via `_check_active_and_reenqueue_self()` (Task 2.9.g duplication-check
    fix; see that function's own docstring for the full reasoning).
    """
    settings = get_settings()
    async with _session_factory()() as session:
        if not await _check_active_and_reenqueue_self(
            session,
            job,
            job_type="scheduler_tick",
            interval_seconds=settings.scheduler_tick_interval_seconds,
        ):
            return

        due_sources = await get_due_sources(session, tenant_id=job.tenant_id)
        for source in due_sources:
            job_type = JOB_TYPE_FOR_SOURCE_TYPE.get(source.type)
            if job_type is None:
                # Structurally impossible through the real schema --
                # ck_sources_type already restricts source.type to exactly
                # the four keys this mapping has -- but defensive rather
                # than silently assuming, matching _open_source_session()'s
                # own identical precedent for an analogous impossible case.
                logger.error(
                    "handle_scheduler_tick: tenant %s source %s has an "
                    "unrecognized type %r, not enqueued",
                    job.tenant_id,
                    source.id,
                    source.type,
                )
                continue
            await IngestRepository(tenant_id=job.tenant_id, session=session).enqueue(
                job_type=job_type, source_id=source.id, payload={}
            )
            await session.commit()
            logger.info(
                "handle_scheduler_tick: tenant %s enqueued %s for source %s",
                job.tenant_id,
                job_type,
                source.id,
            )


async def handle_reconcile(job: Job) -> None:
    """The nightly reconcile's own `JOB_HANDLERS["reconcile"]` entry --
    [SECURITY]-tagged per the Step 2.9 breakdown's own reasoning: "a
    cross-tenant Qdrant deletion operation with no per-request guardrail
    of its own (unlike `retrieve()`), hence [SECURITY]." Mirrors
    `handle_scheduler_tick()` directly above closely, by design (approved:
    "mirroring scheduler_tick's exact shape") -- no Qdrant client is
    forwarded in (unlike the four adapter handlers above), since this job
    is not source-scoped, so a real client is acquired directly here
    (`qdrant.get_qdrant_client()`, the same factory `_open_source_session()`
    itself calls) and used only for `reconcile_tenant()`'s own real work
    below.

    Per-tenant (this task's own confirmed scope decision, matching
    `scheduler_tick`'s own identical per-tenant reasoning, app/ingest/
    scheduler.py's header comment): re-enqueues its OWN next occurrence
    `Settings.reconcile_interval_seconds` in the future FIRST, before
    running the real reconcile pass -- the IDENTICAL ordering reasoning
    `handle_scheduler_tick()` already established: if `reconcile_tenant()`
    ever raised an unexpected, uncaught exception, the self-re-enqueue has
    already committed by then, so this tenant's own reconcile chain
    survives regardless of what happens afterward.
    `ensure_reconcile_ticks_seeded()` (app/ingest/scheduler.py, wired into
    worker.py's own run() loop) is the matching backstop if the chain is
    ever broken anyway.

    Suspended-tenant check FIRST, before anything else -- mirroring
    `handle_scheduler_tick()`'s own identical layer-2 protection, applied
    here even though this task's own instructions did not name tenant
    suspension explicitly (see `ensure_reconcile_ticks_seeded()`'s own
    docstring for why mirroring the CURRENT, already-refined shape of
    that function was judged the more faithful reading of "mirror its
    exact shape" than mirroring an earlier, pre-refinement version that
    no longer exists). Now shared with `handle_scheduler_tick()` above
    via `_check_active_and_reenqueue_self()` (Task 2.9.g duplication-check
    fix) -- if the tenant is no longer active, this handler returns
    immediately, no self-re-enqueue, no reconcile pass; the job itself
    still succeeds.
    """
    settings = get_settings()
    client: AsyncQdrantClient = qdrant.get_qdrant_client()
    async with _session_factory()() as session:
        if not await _check_active_and_reenqueue_self(
            session, job, job_type="reconcile", interval_seconds=settings.reconcile_interval_seconds
        ):
            return

        deleted = await reconcile_tenant(
            session, client, qdrant.COLLECTION_NAME, tenant_id=job.tenant_id
        )
        if deleted:
            logger.info(
                "handle_reconcile: tenant %s reconcile deleted %d orphaned point(s) total",
                job.tenant_id,
                deleted,
            )


async def handle_retention_sweep(job: Job) -> None:
    """Task 4.2: the nightly chat-retention sweep's own `JOB_HANDLERS
    ["retention_sweep"]` entry -- mirrors `handle_reconcile()` directly
    above exactly (per-tenant, suspended-tenant check + self-re-enqueue
    FIRST via the shared `_check_active_and_reenqueue_self()`, so an
    unexpected failure in the real sweep below can never break this
    tenant's own chain).

    Deletes every conversation past this tenant's own retention window
    (`tenants.config["retention_days"]`, falling back to `Settings.
    default_chat_retention_days` when absent or out of the documented
    [1, 90] range -- a tenant has no dashboard to write this yet, Step
    6.2's own future job) -- and, for each one, prunes its own LangGraph
    checkpoint thread FIRST (`AsyncPostgresSaver.adelete_thread()`,
    docs/SPEC.md §4.6's own "old LangGraph checkpoints are pruned on the
    same schedule"), since a checkpoint thread has no FK tying its own
    deletion to the `conversations` row's deletion -- confirmed by
    reading app/tenancy/models.py directly, checkpoint tables are a
    wholly separate system keyed by a plain string `thread_id`, never
    cascaded by Postgres itself. Checkpoint pruned BEFORE the row delete,
    not after: if the row delete were to fail/roll back, re-pruning an
    already-pruned (or never-existent) thread on the NEXT sweep is a
    harmless no-op (confirmed live, Task 3.2.b/c/d's own finding:
    `adelete_thread()` on a thread with nothing to delete does not
    raise) -- the reverse order would risk a row deleted with its own
    checkpoint never pruned, a real orphan with no later chance to fix it
    (nothing else ever looks up a checkpoint thread for a `conversations`
    row that no longer exists).

    **Escalation retention is a SEPARATE handler, not this one**:
    docs/SPEC.md §7's own "a nightly sweep deletes expired requests" is
    about escalations specifically (its own 1-24-month window, a
    DIFFERENT setting from this function's own chat-retention window)
    -- see `handle_escalation_retention_sweep()` directly below, Step
    4.4's own job type, resolving the §9 marker this docstring used to
    name.
    """
    settings = get_settings()
    async with _session_factory()() as session:
        if not await _check_active_and_reenqueue_self(
            session,
            job,
            job_type="retention_sweep",
            interval_seconds=settings.retention_sweep_interval_seconds,
        ):
            return

        repo = TenantScopedRepository(tenant_id=job.tenant_id, session=session)
        tenant = await repo.get_tenant()
        retention_days = tenant.config.get("retention_days", settings.default_chat_retention_days)
        if not isinstance(retention_days, int) or not (1 <= retention_days <= 90):
            logger.warning(
                "handle_retention_sweep: tenant %s has an invalid retention_days %r in "
                "config, using the platform default %d instead",
                job.tenant_id,
                retention_days,
                settings.default_chat_retention_days,
            )
            retention_days = settings.default_chat_retention_days

        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        expired = await repo.list_expired_conversations(cutoff)
        if not expired:
            return

        saver = checkpointer.get_checkpointer()
        for conversation in expired:
            thread_id = build_thread_id(conversation.tenant_id, conversation.vid, conversation.cid)
            await saver.adelete_thread(thread_id)
            await repo.delete_conversation(conversation.cid)
        await session.commit()
        logger.info(
            "handle_retention_sweep: tenant %s deleted %d expired conversation(s) "
            "(retention_days=%d)",
            job.tenant_id,
            len(expired),
            retention_days,
        )


async def handle_escalation_retention_sweep(job: Job) -> None:
    """Step 4.4: the nightly escalation-retention sweep's own
    `JOB_HANDLERS["escalation_retention_sweep"]` entry -- mirrors
    `handle_retention_sweep()` directly above exactly (per-tenant,
    suspended-tenant check + self-re-enqueue FIRST via the shared
    `_check_active_and_reenqueue_self()`), applied to `escalations`
    instead of `conversations`.

    `tenants.config.get("escalation_retention_months")`, falling back
    to `Settings.default_escalation_retention_months` when absent or
    out of the documented [1, 24] range -- the IDENTICAL "platform
    default, future per-tenant override, clamp at read time" shape
    `handle_retention_sweep()`'s own `retention_days` handling already
    uses, applied to months instead of days (docs/SPEC.md §7: "separate
    setting, default 12 months (allowed range 1 to 24 months)").

    No checkpoint-thread pruning here (unlike `handle_retention_sweep()`
    above): an escalation has no LangGraph checkpoint thread of its own
    -- `transcript` is a self-contained snapshot, not a live reference
    into one (app/handoff/models.py's own `transcript` column comment).
    """
    settings = get_settings()
    async with _session_factory()() as session:
        if not await _check_active_and_reenqueue_self(
            session,
            job,
            job_type="escalation_retention_sweep",
            interval_seconds=settings.retention_sweep_interval_seconds,
        ):
            return

        repo = TenantScopedRepository(tenant_id=job.tenant_id, session=session)
        tenant = await repo.get_tenant()
        retention_months = tenant.config.get(
            "escalation_retention_months", settings.default_escalation_retention_months
        )
        if not isinstance(retention_months, int) or not (1 <= retention_months <= 24):
            logger.warning(
                "handle_escalation_retention_sweep: tenant %s has an invalid "
                "escalation_retention_months %r in config, using the platform "
                "default %d instead",
                job.tenant_id,
                retention_months,
                settings.default_escalation_retention_months,
            )
            retention_months = settings.default_escalation_retention_months

        # 30-day months, matching this project's own existing
        # retention_days-in-plain-days precedent (no calendar-month
        # arithmetic anywhere else in this codebase to stay consistent
        # with) -- a deliberate, stated approximation, not a bug: a
        # "12 month" retention window is specified as roughly a year,
        # not as a precise calendar boundary docs/SPEC.md ties any
        # other behavior to.
        cutoff = datetime.now(UTC) - timedelta(days=retention_months * 30)
        result = await session.execute(
            delete(Escalation).where(
                Escalation.tenant_id == job.tenant_id, Escalation.created_at < cutoff
            )
        )
        await session.commit()
        deleted = result.rowcount
        if deleted:
            logger.info(
                "handle_escalation_retention_sweep: tenant %s deleted %d expired "
                "escalation(s) (escalation_retention_months=%d)",
                job.tenant_id,
                deleted,
                retention_months,
            )


async def handle_usage_alert_sweep(job: Job) -> None:
    """Task 4.3: the nightly-shaped (hourly, see Settings.usage_alert_
    sweep_interval_seconds) usage-alert sweep's own `JOB_HANDLERS
    ["usage_alert_sweep"]` entry -- mirrors `handle_retention_sweep()`
    directly above exactly (per-tenant, suspended-tenant check + self-
    re-enqueue FIRST via the shared `_check_active_and_reenqueue_self()`,
    so an unexpected failure in the real sweep below can never break
    this tenant's own chain).

    Reads the AUTHORITATIVE count directly from `usage_events`
    (`app.plans.usage.monthly_count_from_db()`), never the API
    process's own in-memory counter -- this is a SEPARATE process with
    no access to that in-memory state at all (app/plans/usage.py's own
    header comment: "Only the API process serves chats" is the precise
    reason that counter is in-memory in the first place). A tenant with
    no plan assigned (`get_plan_limits()` returns `None`) has no quota
    to compare against, so nothing to alert on -- the identical "no
    plan, no enforcement" treatment app/chat/routes.py's own quota
    check already gives it.

    docs/SPEC.md §9: "the worker emails the tenant's dashboard contact
    at 80% and 100%, once per threshold per month." The dedup/retry
    mechanism IS `email_log`'s own unique constraint (tenant_id,
    threshold, month) -- see that model's own header comment for the
    full upsert reasoning: a `status="sent"` row stops here; a
    `status="failed"`/no row at all retries (or attempts for the first
    time) this tick.

    **A real, named gap, not silently worked around**: docs/SPEC.md
    never defines WHICH `tenants.config` key holds the tenant's own
    alert-recipient address -- the identical "no per-tenant support-
    details fields exist yet, no dashboard writes them" gap `app/agent/
    graph.py`'s own `_no_answer_fallback_node` already names (owner Step
    6.2). `tenants.config.get("alert_email")` is this task's own chosen
    key name, matching that same "a dashboard will write this key
    someday" convention (Step 4.2's own `retention_days` key, Step
    3.4.e's own unbuilt support-details fields) -- when absent (every
    tenant today), the outcome is recorded as `"skipped_not_configured"`,
    the identical status `app/notifications/service.py`'s own
    `send_email()` already uses for "SMTP itself isn't configured",
    since from THIS sweep's own perspective both cases are identically
    "there is genuinely nothing to send this to/with" -- never a
    guessed or hardcoded fallback address.
    """
    settings = get_settings()
    async with _session_factory()() as session:
        if not await _check_active_and_reenqueue_self(
            session,
            job,
            job_type="usage_alert_sweep",
            interval_seconds=settings.usage_alert_sweep_interval_seconds,
        ):
            return

        repo = TenantScopedRepository(tenant_id=job.tenant_id, session=session)
        tenant = await repo.get_tenant()
        limits = await get_plan_limits(tenant, session)
        if limits is None:
            return

        count = await usage.monthly_count_from_db(tenant.id, session)
        fraction = count / limits.ai_message_quota_per_month
        month = datetime.now(UTC).strftime("%Y-%m")
        alert_email = tenant.config.get("alert_email")

        for threshold_label, threshold_fraction in (("80", 0.80), ("100", 1.0)):
            if fraction < threshold_fraction:
                continue

            existing = (
                await session.execute(
                    select(EmailLog).where(
                        EmailLog.tenant_id == tenant.id,
                        EmailLog.threshold == threshold_label,
                        EmailLog.month == month,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None and existing.status == "sent":
                continue  # already sent this threshold this month -- dedup stops here

            if alert_email is None:
                status = "skipped_not_configured"
            else:
                subject = f"Usage alert: {threshold_label}% of your monthly AI message quota used"
                body = (
                    f"Your account has used {threshold_label}% of its monthly AI-answered "
                    "message quota. No action is needed immediately -- this is an "
                    "informational alert."
                )
                status = "sent" if await send_email(alert_email, subject, body) else "failed"

            if existing is None:
                session.add(
                    EmailLog(
                        tenant_id=tenant.id,
                        threshold=threshold_label,
                        month=month,
                        status=status,
                        attempts=1,
                    )
                )
            else:
                existing.status = status
                existing.attempts += 1

        await session.commit()
