# backend/app/worker.py
# Task 1.1.f: worker entrypoint stub, proving the process starts, reads
# settings the same way the API does, and shuts down cleanly on
# SIGTERM/SIGINT. Task 2.1.d fills in the actual job loop this file
# previously lacked entirely -- claim -> dispatch-by-job_type -> mark
# succeeded/failed, proven here with a no-op handler only; real
# crawl/upload/database-sync logic is 2.4-2.8's job (matching
# ensure_collection()'s own precedent: a real, fully-tested function built
# ahead of its eventual real callers).
import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable

from app.config import SettingsError, get_settings
from app.db import _session_factory
from app.ingest.models import Job
from app.ingest.queue import claim_next_job, mark_job_failed, mark_job_succeeded

logger = logging.getLogger(__name__)


async def _noop_handler(payload: dict) -> None:
    # The one handler this task registers -- succeeds immediately, does
    # nothing. Proves the loop's own claim -> dispatch -> mark_succeeded
    # path end to end without any real adapter logic (2.4-2.8's job).
    del payload


# A plain dict, not a decorator-based registry: this is the smallest,
# most standard shape for "one string maps to one function" (rule 11) --
# a decorator-based registration mechanism would be real abstraction for
# a registry that, as of this task, holds exactly one entry. Revisit if a
# later step (2.4+) needs registration split across multiple files.
JOB_HANDLERS: dict[str, Callable[[dict], Awaitable[None]]] = {
    "noop": _noop_handler,
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


async def _claim_and_process_one_job() -> bool:
    # Returns True if a job was claimed (whether it went on to succeed,
    # fail, or hit an unknown job_type), False if nothing was ready.
    job = await _claim_one()
    if job is None:
        return False

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
        await handler(job.payload)
    except Exception as exc:  # noqa: BLE001 -- any handler failure must not crash the loop
        # permanent=False (the default): a raising handler could be a
        # transient failure, unlike an unknown job_type above, so this
        # keeps 2.1.c's normal backoff/retry treatment, never the
        # permanent path. str(exc) is safe to store here: job.payload is
        # this project's own untrusted ingestion input (never a secret),
        # and _noop_handler (the only handler registered so far) raises
        # nothing of its own -- there is no credential or connection
        # string in this path the way IngestRepository's own pgcrypto
        # statements had (duplication check after 2.1.a/b, item C1).
        # Revisit this reasoning once a real handler (2.4+) can itself
        # touch something sensitive.
        await _mark_done(job.id, str(exc))
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

    iterations = 0
    while not stop.is_set():
        try:
            claimed = await _claim_and_process_one_job()
        except Exception:
            # A transient failure reaching the database (or claiming/
            # marking a job) must not crash the whole worker process --
            # log only the exception's type, never str(exc): unlike a
            # pgp_sym_decrypt() failure (2.1.a/b's own C1 finding), a
            # connection-level failure's own message has not been proven
            # free of the DSN/credentials, so this stays defensive rather
            # than assuming it is safe. Treated exactly like "nothing was
            # ready": sleep the poll interval, try again next iteration.
            logger.exception("worker: unexpected failure claiming/processing a job")
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
