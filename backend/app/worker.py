# backend/app/worker.py
# Task 1.1.f: worker entrypoint stub, proving the process starts, reads
# settings the same way the API does, and shuts down cleanly on
# SIGTERM/SIGINT. Task 2.1.d fills in the actual job loop this file
# previously lacked entirely -- claim -> dispatch-by-job_type -> mark
# succeeded/failed, proven here with a no-op handler only; real
# crawl/upload/database-sync logic is 2.4-2.8's job (matching
# ensure_collection()'s own precedent: a real, fully-tested function built
# ahead of its eventual real callers).
#
# Task 2.6.d: `JOB_HANDLERS` registers its first REAL handler
# (`handle_ingest_url`, app/ingest/job_handlers.py) -- see that module's
# own header comment for the full design (the JOB_HANDLERS signature
# widening this required, the partial-failure/job-success policy, the
# heartbeat decision).
#
# Task 2.6.e part 2: `handle_ingest_crawl` registered too. It imports
# write_heartbeat/HEARTBEAT_PATH from this module -- but does so INSIDE
# its own function body (app/ingest/job_handlers.py), not at that
# module's top level, since this module already imports FROM job_
# handlers.py at ITS OWN top level (the line directly below); a
# module-level import back would be a genuine circular import, resolved
# by deferring it to call time instead (both modules are fully
# initialized by then).
#
# Task 2.7.d: `handle_ingest_upload` registered too -- no heartbeat
# import needed (that handler deliberately does not write one, matching
# handle_ingest_url()'s own precedent; see job_handlers.py's own header
# comment).
#
# Task 2.8.e [SECURITY]: `handle_ingest_db` registered too -- also no
# heartbeat import needed, identical reasoning to `handle_ingest_upload`'s
# own (see job_handlers.py's own header comment).
import asyncio
import logging
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from app import all_models  # noqa: F401
from app.config import SettingsError, get_settings
from app.db import _session_factory
from app.ingest.job_handlers import (
    handle_ingest_crawl,
    handle_ingest_db,
    handle_ingest_upload,
    handle_ingest_url,
)
from app.ingest.models import Job
from app.ingest.queue import claim_next_job, mark_job_failed, mark_job_succeeded, reap_stuck_jobs

# all_models above is imported for the side effect of registering every
# domain's tables on Base.metadata -- without it, jobs.tenant_id's FK to
# tenants.id cannot be resolved at flush time, since SQLAlchemy needs both
# tables' mappers registered to sort them. Found live at Task 2.1.e: a
# genuine standalone `python -m app.worker` subprocess, with nothing else
# in-process to have already imported app.tenancy.models, raised
# NoReferencedTableError on its very first mark_job_succeeded() flush.
# 2.1.d's own tests never caught this because they called run() directly
# under pytest, where some other test module had always already imported
# every domain's models first. Duplication check after 2.1.c/d/e: this
# used to be worker.py's own separate plans/tenancy import block, now
# shared with alembic/env.py via app/all_models.py instead.

logger = logging.getLogger(__name__)

# Task 2.1.f: the worker has no HTTP server (unlike the API's own /ready,
# Task 1.4.j), so its healthcheck needs a different mechanism -- a
# file-based heartbeat. /tmp is the only writable path available: the
# container runs with read_only: true + a tmpfs /tmp (deploy/
# docker-compose.yml's x-app-hardening, applied to worker too), so this is
# not an arbitrary choice, it's close to the only one. The file's content
# is the write time itself (time.time(), as plain text) rather than relying
# on the filesystem's own mtime: a human or script can `cat` it and
# understand it immediately, and it avoids any mtime-semantics subtlety
# across tmpfs/exec-into-container edge cases.
HEARTBEAT_PATH = Path("/tmp/worker-heartbeat")  # noqa: S108 (not a shared/predictable-path risk: container-local tmpfs, no other process reads or writes it)

# A multiple of worker_poll_interval_seconds, not a fixed number of seconds:
# the heartbeat is written once per loop iteration (see run() below), and an
# idle iteration's own wait is bounded by that same setting, so "how stale
# is too stale" must scale with it. 3x covers one full normal idle-wait
# between writes plus headroom for an occasional slow iteration (a DB
# round-trip hiccup, a GC pause) without needing to approach
# x-healthcheck-timing's own retries*interval=25s detection floor (deploy/
# docker-compose.yml) -- this threshold, not Docker's own probe schedule,
# is what actually decides "stale" here.
HEARTBEAT_STALE_MULTIPLIER = 3

# RESOLVED for handle_ingest_crawl() at Task 2.6.e part 2: this handler
# calls write_heartbeat() itself, per discovered page, during its own
# work (not just once per run()-loop iteration at job start) -- the
# mechanism this ASSUMPTION originally called for, now built. Still an
# open ASSUMPTION for any FUTURE long-running handler (2.7/2.8) that
# does not yet exist: handle_ingest_url() (2.6.d) processes a tenant-
# supplied, realistically-short URL list and was judged not yet the
# trigger case at that task's own build time; this remains true for it
# unless real-world usage proves otherwise.


def write_heartbeat(path: Path = HEARTBEAT_PATH) -> None:
    # Task 2.6.e part 2: renamed from _write_heartbeat() (no behavior
    # change) -- a real handler (handle_ingest_crawl(), app/ingest/
    # job_handlers.py) now calls this directly, mid-execution, per
    # discovered page, per the Step 2.6 decision's own lean ("the handler
    # should write progress heartbeats periodically during its own
    # work"). No worker.py refactor needed for this: the heartbeat was
    # always a plain, side-effecting function writing to a path, not
    # something tied to run()'s own loop structure -- confirmed by
    # reading this function directly before assuming a refactor was
    # required.
    path.write_text(str(time.time()))


def check_heartbeat_fresh(path: Path = HEARTBEAT_PATH) -> bool:
    # The one function both deploy/docker-compose.yml's real healthcheck
    # (invoked as a plain `python3 -c "..."` one-liner, matching the API's
    # own no-curl-in-the-image convention, Task 1.4.j) and this file's own
    # tests call -- one source of truth for "is this fresh", not duplicated
    # shell-script logic and Python logic that could silently drift apart.
    try:
        written_at = float(path.read_text())
    except (OSError, ValueError):
        return False
    threshold = HEARTBEAT_STALE_MULTIPLIER * get_settings().worker_poll_interval_seconds
    return (time.time() - written_at) < threshold


async def _noop_handler(job: Job) -> None:
    # The one handler this task registers -- succeeds immediately, does
    # nothing. Proves the loop's own claim -> dispatch -> mark_succeeded
    # path end to end without any real adapter logic (2.4-2.8's job).
    del job


async def _sleep_handler(job: Job) -> None:
    # Task 2.1.e: exists solely so a test can reliably send a real SIGTERM
    # while a handler is actively running -- no other registered handler
    # (noop returns instantly) gives graceful-shutdown's own "finish the
    # in-flight job, don't claim a new one" behavior a window to prove
    # itself against. payload["seconds"] (default 0) keeps this inert by
    # default, same spirit as noop, just controllably slow when asked.
    await asyncio.sleep(job.payload.get("seconds", 0))


# Task 2.6.d: widened from Callable[[dict], Awaitable[None]] to
# Callable[[Job], Awaitable[None]] -- a real, necessary signature change
# found before building, not assumed away. `noop`/`sleep` only ever
# needed their own `payload`, matching the old shape exactly, but the
# first REAL handler (handle_ingest_url(), app/ingest/job_handlers.py)
# needs `job.tenant_id`/`job.source_id` too -- first-class Job columns,
# not payload fields; duplicating them into every job_type's own payload
# shape just to keep the old signature would be worse, not simpler (rule
# 11). `noop`/`sleep` updated mechanically above (`payload` -> `job`,
# `job.payload` where the body actually used it) -- no behavior change,
# confirmed by the full existing test_worker.py suite passing unmodified.
#
# Still a plain dict, not a decorator-based registry: the smallest, most
# standard shape for "one string maps to one function" (rule 11) --
# still true now that it holds 3 entries, not a signal to add a registry
# mechanism nothing here actually needs yet.
JOB_HANDLERS: dict[str, Callable[[Job], Awaitable[None]]] = {
    "noop": _noop_handler,
    "sleep": _sleep_handler,
    "ingest_url": handle_ingest_url,
    "ingest_crawl": handle_ingest_crawl,
    "ingest_upload": handle_ingest_upload,
    "ingest_db": handle_ingest_db,
}


async def _claim_one() -> Job | None:
    # Its own short transaction, separate from running the handler below
    # -- claim_next_job()'s own FOR UPDATE-derived lock is only held for
    # this claim, not for the handler's entire (potentially long-running,
    # from 2.4+ onward) execution. Matches the engineering rule's own
    # "never hold a DB connection while streaming" spirit: don't hold one
    # across unrelated, possibly-slow work either.
    async with _session_factory()() as session:
        job = await claim_next_job(session)
        await session.commit()
    return job


async def _mark_done(job_id, error: str | None, *, permanent: bool = False) -> None:
    # Its own separate transaction -- see _claim_one()'s comment above.
    async with _session_factory()() as session:
        if error is None:
            await mark_job_succeeded(session, job_id)
        else:
            await mark_job_failed(session, job_id, error, permanent=permanent)
        await session.commit()


async def _reap_stuck_jobs() -> list:
    # Task 2.6.f: its own separate transaction, same reasoning as
    # _claim_one()/_mark_done() above. Run once per run() iteration,
    # same cadence as the heartbeat write below -- reusing that already-
    # established "cheap, safe to run redundantly often" precedent
    # rather than inventing a second, separately-timed scheduling
    # mechanism (a real scheduler with differently-timed background
    # tasks is Step 2.9's own job, not built speculatively here). A
    # single indexed-enough WHERE clause against this project's own
    # current `jobs` table scale; revisit if that scale ever makes this
    # genuinely expensive (not assumed a problem ahead of evidence).
    async with _session_factory()() as session:
        reaped = await reap_stuck_jobs(
            session, stale_after_seconds=get_settings().job_stuck_after_seconds
        )
        await session.commit()
    return reaped


async def _claim_and_process_one_job() -> bool:
    # Returns True if a job was claimed (whether it went on to succeed,
    # fail, or hit an unknown job_type), False if nothing was ready.
    job = await _claim_one()
    if job is None:
        return False
    logger.info("worker: claimed job %s (job_type=%s)", job.id, job.job_type)

    handler = JOB_HANDLERS.get(job.job_type)
    if handler is None:
        # A structural, non-transient error (Task 2.1.d decision,
        # extending 2.1.c's own mark_job_failed()): this job_type will
        # never suddenly become registered, so retrying it is guaranteed
        # to reproduce the identical failure -- permanent=True skips
        # straight to "failed" rather than burning max_attempts retries
        # and their backoff delay to reach the same terminal state.
        # Never crash on a job_type this worker doesn't know: fail that
        # one job clearly and move on -- a bad enqueue() call elsewhere
        # must not take the whole worker down.
        await _mark_done(job.id, f"unknown job_type: {job.job_type!r}", permanent=True)
        return True

    try:
        await handler(job)
    except Exception as exc:  # noqa: BLE001 -- any handler failure must not crash the loop
        # permanent=False (the default): a raising handler could be a
        # transient failure, unlike an unknown job_type above, so this
        # keeps 2.1.c's normal backoff/retry treatment, never the
        # permanent path. type(exc).__name__ only, never str(exc) --
        # matching the identical, already-correct pattern this same file
        # uses a few lines below for the claim/process loop's own
        # exception handler. This used to store str(exc) directly, reasoned
        # safe only because "_noop_handler (the only handler registered so
        # far) raises nothing of its own" -- that reasoning's own comment
        # explicitly said to revisit it "once a real handler (2.4+) can
        # itself touch something sensitive". embed_dense() (Task 2.5.a) is
        # exactly such a handler: a real OpenAI AuthenticationError's own
        # str() can echo the submitted API key verbatim (confirmed live
        # against the installed SDK during the duplication check after
        # 2.5.a/b/c), and Step 2.6 will wire embed_dense() into a real job
        # handler here. str(exc) is no longer provably safe for an
        # arbitrary handler, so this stays defensive like the sibling
        # handler below, not merely convenient for debugging.
        await _mark_done(job.id, f"handler raised {type(exc).__name__}")
        return True

    await _mark_done(job.id, None)
    return True


async def run(stop: asyncio.Event | None = None, max_iterations: int | None = None) -> None:
    # Never Settings() directly: only app/config.py may do that (guard test).
    settings = get_settings()
    if stop is None:
        stop = asyncio.Event()
    # Only app_env, never database_url/qdrant_url: those must not reach logs.
    logger.info("worker starting: app_env=%s", settings.app_env)

    # Graceful shutdown (Task 2.1.e): stop.is_set() is re-evaluated fresh
    # here at the top of every iteration, and stop.set() (the SIGTERM
    # signal handler, main()'s own add_signal_handler callback) is a plain
    # cooperative flag -- it never cancels an in-flight
    # `await _claim_and_process_one_job()`. So a signal arriving while a
    # job's handler is actively running is only ever observed at the NEXT
    # iteration boundary, after that job has already been claimed, run to
    # completion, and marked succeeded/failed -- "finish what you started,
    # claim nothing new" -- with no extra code needed beyond this check
    # already being here. Confirmed live, not assumed: see
    # test_worker.py's own test_sigterm_mid_handler_finishes_the_current_
    # job_and_claims_no_other, a real SIGTERM sent to a real subprocess
    # while a deliberately slow handler is mid-sleep.
    iterations = 0
    while not stop.is_set():
        try:
            # Written first, every iteration -- including an idle one with
            # nothing to claim (Task 2.1.f): idle-but-alive must be
            # distinguishable from wedged, which an "only on a claimed job"
            # heartbeat could not do (a worker with no pending jobs for a
            # while would look identical to one that stopped iterating
            # entirely). Inside this same try/except as the claim/process
            # call below, not a separate one: a write failure (e.g. a full
            # tmpfs) is exactly the kind of unexpected per-iteration
            # failure that block already exists to catch, and a worker
            # that cannot write its own heartbeat is arguably unhealthy in
            # a real sense too, not just logging-wise. HEARTBEAT_PATH is
            # passed explicitly (not left to write_heartbeat()'s own
            # default parameter) so a test can monkeypatch the module-level
            # name and have it actually take effect here -- a default
            # argument's value is bound once, at function-definition time,
            # so monkeypatching the module attribute alone would not
            # otherwise reach this call.
            write_heartbeat(HEARTBEAT_PATH)
            # Task 2.6.f: the stuck-job reaper, run once per iteration,
            # same cadence as the heartbeat write directly above (see
            # _reap_stuck_jobs()'s own comment for why no separate
            # scheduling mechanism was built for this). Inside this same
            # try/except, matching write_heartbeat()'s own reasoning
            # exactly: a transient failure reaping jobs must not crash
            # the worker either.
            reaped = await _reap_stuck_jobs()
            if reaped:
                logger.info("worker: reaped %d stuck job(s): %s", len(reaped), reaped)
            claimed = await _claim_and_process_one_job()
        except Exception as exc:
            # A transient failure reaching the database (or claiming/
            # marking a job) must not crash the whole worker process --
            # log only the exception's type, never str(exc): unlike a
            # pgp_sym_decrypt() failure (2.1.a/b's own C1 finding), a
            # connection-level failure's own message has not been proven
            # free of the DSN/credentials, so this stays defensive rather
            # than assuming it is safe. Treated exactly like "nothing was
            # ready": sleep the poll interval, try again next iteration.
            # NOTE: logger.exception() (and logger.error(..., exc_info=True))
            # must never be used here -- both log the full exception message
            # and traceback despite this comment's own intent, exactly the
            # leak this is trying to avoid (duplication check after
            # 2.1.c/d/e, item C1 -- a real bug, found live: the previous
            # logger.exception() call here contradicted this very comment).
            logger.error(
                "worker: unexpected failure claiming/processing a job (%s)",
                type(exc).__name__,
            )
            claimed = False

        if not claimed:
            try:
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.worker_poll_interval_seconds
                )
            except TimeoutError:
                pass

        iterations += 1
        if max_iterations is not None and iterations >= max_iterations:
            return


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    try:
        loop.run_until_complete(run(stop))
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    finally:
        loop.close()


if __name__ == "__main__":
    main()
