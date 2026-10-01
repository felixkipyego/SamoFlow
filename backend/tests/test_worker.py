# backend/tests/test_worker.py
# Tests for Task 1.1.f's worker entrypoint stub (backend/app/worker.py).
# Task 2.1.d extends this file with the real job-processing loop's own
# tests (the noop handler, an unknown job_type, a raising handler, the
# idle-poll interval) -- run() is the same function 1.1.f's own tests
# already exercise, so one file, not a second. Task 2.1.e adds the real
# process-level graceful-shutdown proof (a SIGTERM sent while a handler
# is actively running), using the exact same _spawn_worker()/
# _worker_subprocess_env() machinery 1.1.f's own SIGTERM test already
# established.
import asyncio
import logging
import os
import signal
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.config import SettingsError, get_settings
from app.ingest.models import Job
from app.ingest.repository import IngestRepository
from app.tenancy.models import Tenant
from app.worker import JOB_HANDLERS, run
from tests.conftest import (
    ALL_SETTINGS_VARS,
    TEST_PASSWORD,
    VALID_ENV,
    _fetch_job,
    db_session,
    set_valid_env,
)

BACKEND_DIR = Path(__file__).resolve().parent.parent


async def test_run_returns_promptly_when_stop_is_already_set(monkeypatch, caplog):
    set_valid_env(monkeypatch, VALID_ENV)
    stop = asyncio.Event()
    stop.set()
    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(run(stop), timeout=2)
    assert any("app_env=development" in record.message for record in caplog.records)


async def test_startup_log_never_contains_database_url_or_password(monkeypatch, caplog):
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        DATABASE_URL=f"postgresql+psycopg://user:{TEST_PASSWORD}@localhost:5432/widgetplatform",
    )
    stop = asyncio.Event()
    stop.set()
    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(run(stop), timeout=2)
    assert TEST_PASSWORD not in caplog.text
    assert "postgresql+psycopg://" not in caplog.text


async def test_run_with_no_environment_raises_settings_error():
    with pytest.raises(SettingsError):
        await run()


async def test_run_logs_only_the_exception_type_never_the_message_on_unexpected_failure(
    monkeypatch, caplog
):
    # Duplication check after 2.1.c/d/e, item C1: a real bug, found live --
    # the per-iteration except-and-continue branch (run()'s own loop) used
    # to call logger.exception(), which logs the full exception message and
    # traceback via exc_info=True, directly contradicting that branch's own
    # comment ("log only the exception's type, never str(exc)"). Fixed to
    # logger.error() with only type(exc).__name__. No real DB is needed to
    # trigger this: claim_next_job is monkeypatched to raise before ever
    # issuing a query, so opening the session never attempts a real
    # connection (SQLAlchemy connects lazily, on first actual I/O).
    import app.worker as worker_module

    async def _raise_with_secret(session):
        raise ValueError("DISTINCTIVE-FAKE-SECRET-98765")

    monkeypatch.setattr(worker_module, "claim_next_job", _raise_with_secret)
    set_valid_env(monkeypatch, VALID_ENV)

    with caplog.at_level(logging.INFO):
        await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert "DISTINCTIVE-FAKE-SECRET-98765" not in caplog.text
    assert any(
        "unexpected failure" in record.message and "ValueError" in record.message
        for record in caplog.records
    ), "expected a log entry identifying the exception type"


def _worker_subprocess_env(**overrides):
    # Strips every Settings-related var (required or optional -- Task
    # 1.4.a) from the caller's own ambient environment, so a truly "empty
    # w.r.t. Settings" subprocess env can be built from **overrides alone.
    env = {k: v for k, v in os.environ.items() if k not in ALL_SETTINGS_VARS}
    env.update(overrides)
    return env


async def _spawn_worker(env):
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker",
        cwd=BACKEND_DIR,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


async def _wait_for_line_containing(stream, needle, timeout):
    async def _read():
        while True:
            line = await stream.readline()
            if not line:
                return None
            if needle in line.decode(errors="replace"):
                return line
        return None

    return await asyncio.wait_for(_read(), timeout=timeout)


async def test_sigterm_shuts_down_the_real_process_cleanly():
    process = await _spawn_worker(_worker_subprocess_env(**VALID_ENV))
    try:
        startup_line = await _wait_for_line_containing(
            process.stderr, "app_env=development", timeout=10
        )
        assert startup_line is not None, "worker never logged its startup line"
        process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), timeout=10)
        assert exit_code == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_worker_with_empty_environment_exits_nonzero_without_traceback():
    process = await _spawn_worker(_worker_subprocess_env())
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode != 0
    assert b"Traceback" not in stderr


# --- Task 2.1.d: the real job-processing loop -----------------------------
# All live, against the real test database -- claim_next_job()'s own
# FOR UPDATE SKIP LOCKED behavior (2.1.c) cannot be proven any other way,
# and neither can a genuine end-to-end claim -> dispatch -> mark cycle.
# max_iterations=1 is this task's own "stop for testing" mechanism (see
# app/worker.py's own run() signature): bounds the loop to exactly one
# claim attempt (whether or not a job was ready), composing with the
# pre-existing `stop` event rather than replacing it.
#
# _seeded_tenant (bare fixture parameter) and _fetch_job() both come from
# conftest.py now, not defined locally here -- duplication check after
# 2.1.c/d/e, items A1/A2: this file and test_queue.py each had their own,
# near-identical copies.


async def _enqueue(tenant_id, job_type: str) -> uuid.UUID:
    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        job = await repo.enqueue(job_type=job_type, payload={})
        await session.commit()
        return job.id


async def test_run_processes_a_noop_job_to_success(_seeded_tenant):
    job_id = await _enqueue(_seeded_tenant, "noop")

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    row = await _fetch_job(job_id)
    assert row.status == "succeeded"
    assert row.attempts == 0  # never touched mark_job_failed's own backoff path
    assert row.error is None


async def test_run_fails_an_unknown_job_type_without_crashing(_seeded_tenant):
    # A structural, non-transient error (this job_type will never suddenly
    # become registered): worker.py calls mark_job_failed(permanent=True)
    # for this specific case, skipping 2.1.c's normal backoff entirely --
    # "failed" immediately, after exactly one loop iteration, not after
    # max_attempts retries. attempts stays at 0: it counts real
    # handler-execution attempts, and none was made -- there was no
    # handler to run.
    job_id = await _enqueue(_seeded_tenant, "not-a-registered-job-type")

    # The loop itself must not raise, even though nothing handles this
    # job_type -- proven by simply awaiting run() to completion, not
    # wrapping it in pytest.raises.
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    row = await _fetch_job(job_id)
    assert row.status == "failed"
    assert row.attempts == 0
    assert "unknown job_type" in row.error
    assert "not-a-registered-job-type" in row.error


async def test_run_marks_a_raising_handler_failed_and_keeps_looping(monkeypatch, _seeded_tenant):
    async def _always_raises(payload: dict) -> None:
        raise RuntimeError("handler exploded")

    monkeypatch.setitem(JOB_HANDLERS, "always-raises-test-only", _always_raises)
    job_id = await _enqueue(_seeded_tenant, "always-raises-test-only")

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    row = await _fetch_job(job_id)
    # Feeds correctly into 2.1.c's own backoff logic, not the permanent
    # path added above for an unknown job_type: a handler that raises
    # could be a transient failure (unlike "no handler exists at all"),
    # so it keeps the standard retry treatment -- attempts incremented,
    # rescheduled pending (since max_attempts > 1), next_run_at pushed
    # into the future, error recorded. attempts == 1 (not left at 0) is
    # itself proof this took the non-permanent branch.
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.error == "handler exploded"
    assert row.next_run_at > datetime.now(UTC)

    # The loop itself completed (run() returned via max_iterations, not an
    # unhandled exception) -- that this test reached this line at all,
    # rather than pytest reporting an error from an exception escaping
    # run(), is itself part of the "keeps looping" proof.


async def test_run_sleeps_approximately_the_configured_poll_interval_when_idle(
    monkeypatch, reset_test_database
):
    # No jobs pending at all (reset_test_database alone, no _seeded_tenant
    # needed) -- claim_next_job() returns None every time, so run() must
    # sleep, not spin. A small REAL interval (not an injected fake clock):
    # the sleep itself is asyncio.wait_for(stop.wait(), timeout=...), the
    # same mechanism that makes the real worker responsive to SIGTERM
    # during an idle poll -- faking that would test a different code path
    # than production actually runs. At 0.2s the wall-clock measurement
    # below is not meaningfully flake-prone.
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "0.2")
    get_settings.cache_clear()

    started = time.monotonic()
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.2 * 0.8  # actually slept, not a tight spin
    assert elapsed < 2.0  # did not hang or wait far longer than configured


# --- Task 2.1.e: graceful shutdown -----------------------------------------
# A real, standalone `python -m app.worker` subprocess (matching 1.1.f's own
# test_sigterm_shuts_down_the_real_process_cleanly, not an in-process unit
# test of the mechanism) against the real test database: enqueue a slow job
# and a second, merely-pending job, send a genuine SIGTERM while the slow
# job's handler is actively running, and confirm (a) the in-flight job
# reaches its correct terminal state rather than being left "running"
# forever, and (b) no new job was claimed after the signal -- the second job
# is still "pending".
#
# No code change was needed in run()'s own loop for this: `while not
# stop.is_set():` is already re-evaluated fresh at the top of every
# iteration, and stop.set() (the SIGTERM callback) is a plain cooperative
# flag -- it never cancels an in-flight `await handler(...)`. So a signal
# arriving mid-handler is only ever observed at the NEXT iteration boundary,
# after the current job's handler has already run to completion and been
# marked succeeded/failed -- exactly the "finish what you started, claim
# nothing new" semantics this task asks for. Confirmed live, not assumed,
# below (and this exact property is also why 1.1.f's own SIGTERM test,
# which sends the signal between iterations rather than mid-handler, was
# never sufficient proof of this specific case on its own).
#
# A real, unrelated bug found live while first writing this proof (not a
# bug in the mechanism above): a genuine standalone `python -m app.worker`
# process had never had anything import app.tenancy.models/app.plans.models,
# so jobs.tenant_id's FK to tenants.id could not be resolved at
# mark_job_succeeded()'s own flush -- NoReferencedTableError, every time.
# 2.1.d's own tests never caught this because they called run() directly
# under pytest, where some other already-imported test module had always
# registered every domain's models first. Fixed in app/worker.py itself
# (the same import-for-side-effect fix alembic/env.py already needed at
# 2.1.b, for the identical reason).


async def _poll_until_status(job_id, expected_status: str, timeout: float) -> None:
    started = time.monotonic()
    while time.monotonic() - started < timeout:
        async with db_session() as session:
            status = (
                await session.execute(sa.select(Job.status).where(Job.id == job_id))
            ).scalar_one()
        if status == expected_status:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} never reached status {expected_status!r}")


async def test_sigterm_mid_handler_finishes_the_current_job_and_claims_no_other(
    reset_test_database,
):
    database_url = reset_test_database
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Shutdown Tenant", status="active"))
        await session.commit()
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        slow_job = await repo.enqueue(job_type="sleep", payload={"seconds": 0.5})
        other_job = await repo.enqueue(job_type="noop", payload={})
        await session.commit()
        slow_job_id, other_job_id = slow_job.id, other_job.id

    process = await _spawn_worker(
        _worker_subprocess_env(**{**VALID_ENV, "DATABASE_URL": database_url})
    )
    try:
        startup_line = await _wait_for_line_containing(
            process.stderr, "app_env=development", timeout=10
        )
        assert startup_line is not None, "worker never logged its startup line"

        # Poll the real row, not a log line: proves the handler is
        # genuinely mid-flight (claimed, status flipped) before SIGTERM.
        await _poll_until_status(slow_job_id, "running", timeout=10)
        process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), timeout=10)
        assert exit_code == 0
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()

    slow_job_row = await _fetch_job(slow_job_id)
    other_job_row = await _fetch_job(other_job_id)
    assert slow_job_row.status == "succeeded"  # handler finished normally, not left "running"
    assert other_job_row.status == "pending"  # never claimed after the signal
