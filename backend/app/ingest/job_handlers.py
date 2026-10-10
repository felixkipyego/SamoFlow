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
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from qdrant_client import AsyncQdrantClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import qdrant
from app.config import get_settings
from app.db import _session_factory
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
from app.ingest.models import DbConnection, Job, Source
from app.ingest.repository import IngestRepository
from app.ingest.row_templates import MissingColumnError, MissingPrimaryKeyError, TemplateRenderError
from app.ingest.upload_adapter import ingest_upload
from app.ingest.web_adapter import ingest_url

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


async def handle_ingest_url(job: Job) -> None:
    """The `urls` adapter's own `JOB_HANDLERS["ingest_url"]` entry.

    Reads `sources.config["urls"]` for `job.source_id`, then calls
    ingest_url() (2.6.c) once per URL, independently -- see this module's
    own header comment for the full partial-failure/job-success reasoning
    and the heartbeat decision.
    """
    async with _open_source_session(job, "ingest_url") as (client, session, source):
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
    async with _open_source_session(job, "ingest_crawl") as (client, session, source):
        seed_url = source.config["seed_url"]
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
