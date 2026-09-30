# backend/tests/test_worker.py
# Tests for Task 1.1.f's worker entrypoint stub (backend/app/worker.py).
# Task 2.1.d extends this file with the real job-processing loop's own
# tests (the noop handler, an unknown job_type, a raising handler, the
# idle-poll interval) -- run() is the same function 1.1.f's own tests
# already exercise, so one file, not a second.
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
from app.plans import models as plans_models  # noqa: F401 (registers "plans" on Base.metadata)
from app.tenancy.models import Tenant
from app.worker import JOB_HANDLERS, run
from tests.conftest import (
    ALL_SETTINGS_VARS,
    TEST_PASSWORD,
    VALID_ENV,
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


@pytest.fixture
async def _seeded_tenant(reset_test_database):
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Worker Tenant", status="active"))
        await session.commit()
    yield tenant_id


async def _enqueue(tenant_id, job_type: str) -> uuid.UUID:
    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        job = await repo.enqueue(job_type=job_type, payload={})
        await session.commit()
        return job.id


async def _fetch_job(job_id) -> Job:
    async with db_session() as session:
        return (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()


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
