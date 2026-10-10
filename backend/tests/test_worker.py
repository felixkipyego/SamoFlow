# backend/tests/test_worker.py
# Tests for Task 1.1.f's worker entrypoint stub (backend/app/worker.py).
# Task 2.1.d extends this file with the real job-processing loop's own
# tests (the noop handler, an unknown job_type, a raising handler, the
# idle-poll interval) -- run() is the same function 1.1.f's own tests
# already exercise, so one file, not a second. Task 2.1.e adds the real
# process-level graceful-shutdown proof (a SIGTERM sent while a handler
# is actively running), using the exact same _spawn_worker()/
# _worker_subprocess_env() machinery 1.1.f's own SIGTERM test already
# established. Scoped duplication check after 2.1.g: _spawn_worker() itself
# is now spawn_module_subprocess("worker", env) (tests/conftest.py), shared
# with test_qdrant.py's own `python -m app.qdrant` CLI tests.
import asyncio
import logging
import os
import signal
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
import sqlalchemy as sa
from openai import AuthenticationError

from app.config import SettingsError, get_settings
from app.ingest.models import Job
from app.tenancy.models import Tenant
from app.worker import JOB_HANDLERS, check_heartbeat_fresh, run
from tests.conftest import (
    ALL_SETTINGS_VARS,
    TEST_PASSWORD,
    VALID_ENV,
    _fetch_job,
    db_session,
    set_valid_env,
    spawn_module_subprocess,
)


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

    # Task 2.6.f: reap_stuck_jobs() now runs every iteration too, BEFORE
    # claim_next_job() -- stubbed to a real-DB-free no-op so this test
    # keeps exercising claim_next_job()'s own raise specifically (its own
    # stated intent), not reap_stuck_jobs()'s unrelated connection failure
    # against this test's fake DATABASE_URL.
    async def _reap_noop(*args, **kwargs):
        return []

    monkeypatch.setattr(worker_module, "_reap_stuck_jobs", _reap_noop)

    # Task 2.9.a: ensure_scheduler_ticks_seeded() now runs every iteration
    # too, for the identical reason reap_stuck_jobs() is stubbed above --
    # it would otherwise also hit this test's own fake DATABASE_URL before
    # claim_next_job() is ever reached, masking the exception this test
    # actually wants to exercise.
    async def _seed_noop(*args, **kwargs):
        return []

    monkeypatch.setattr(worker_module, "_ensure_scheduler_ticks_seeded", _seed_noop)

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


# Task 2.9.a: widened from 10s to 20s for every real worker-subprocess
# STARTUP wait below (the time from process spawn through every top-level
# import, including this task's own new app.ingest.scheduler import, to
# the first log line or process exit) -- a real, measured consequence,
# not a blind bump. Confirmed live, not assumed: `import app.worker` under
# a stripped environment already took ~8.7-9.1s BEFORE this task (already
# 87-91% of the old 10s budget), confirmed via a direct, repeated
# before/after comparison against the pre-task code. This task's own
# small, legitimate addition to the import graph (app.ingest.scheduler,
# needed by handle_scheduler_tick()) was then observed, live, to push
# real `make test`/`make test-all` runs over that already-thin margin on
# this shared machine -- once reproducing with a confirmed, completely
# unrelated heavy CPU load present (a `pip install` compiling NumPy from
# source for a different project), and once reproducing again with no
# such load identifiable, meaning the ORIGINAL 10s budget was already too
# tight for this project's own real import-graph size, with or without
# extra external load, and this task's own legitimate growth is what
# finally exposed it. NOT applied to timeouts measuring POST-startup
# runtime behavior in the same tests (shutdown speed, job-claim speed) --
# those are unaffected by import time and keep their own original 10s.
_WORKER_SUBPROCESS_STARTUP_TIMEOUT = 20


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
    async with spawn_module_subprocess("worker", _worker_subprocess_env(**VALID_ENV)) as process:
        startup_line = await _wait_for_line_containing(
            process.stderr, "app_env=development", timeout=_WORKER_SUBPROCESS_STARTUP_TIMEOUT
        )
        assert startup_line is not None, "worker never logged its startup line"
        process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), timeout=10)
        assert exit_code == 0


async def test_worker_with_empty_environment_exits_nonzero_without_traceback():
    async with spawn_module_subprocess("worker", _worker_subprocess_env()) as process:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=_WORKER_SUBPROCESS_STARTUP_TIMEOUT
        )
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
    # Task 2.6.b: constructs the Job row directly, bypassing IngestRepository.
    # enqueue() -- the 4 tests calling this helper are about WORKER dispatch/
    # claim/mark behavior given a pre-existing row, not about enqueue()'s own
    # validation, and three of them deliberately need a job_type enqueue()
    # now rejects (VALID_JOB_TYPES is real-types-only, per the Step 2.6.b
    # decision -- never polluted with test scaffolding names). max_attempts
    # relies on the column's own server_default=5 -- already documented as
    # existing for exactly this case ("an inert defensive floor for any
    # insert that bypasses enqueue()", Task 2.1.c's own decision entry).
    async with db_session() as session:
        job = Job(tenant_id=tenant_id, job_type=job_type, status="pending", payload={})
        session.add(job)
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
    # Duplication check after 2.5.a/b/c, C1: type(exc).__name__ only, never
    # str(exc) -- see worker.py's own comment on this exact line for why
    # ("handler exploded" is harmless here, but a real handler's exception
    # message is no longer provably safe for an arbitrary handler).
    assert row.error == "handler raised RuntimeError"
    assert row.next_run_at > datetime.now(UTC)


def _fake_authentication_error(message: str) -> AuthenticationError:
    # Matches test_embedding.py's own _fake_rate_limit_error() helper
    # pattern -- a real openai.AuthenticationError, not a bespoke stand-in,
    # built from the real httpx2 request/response types the SDK itself
    # uses.
    request = httpx2.Request("POST", "https://api.openai.com/v1/embeddings")
    response = httpx2.Response(401, request=request)
    return AuthenticationError(message, response=response, body=None)


async def test_run_never_leaks_a_real_api_key_through_a_handlers_own_exception_message(
    monkeypatch, _seeded_tenant
):
    # Duplication check after 2.5.a/b/c, C1: a live-reproduced security
    # bug, fixed in worker.py (str(exc) -> type(exc).__name__ only) --
    # this test proves the fix, mirroring test_config.py's own
    # test_settings_error_chain_never_carries_the_openai_api_key pattern
    # but for THIS propagation path (a real handler's own real SDK
    # exception, through _claim_and_process_one_job()'s except block, into
    # the job row's stored error column) rather than Settings validation.
    # Confirmed live (duplication check research): a real
    # openai.AuthenticationError's own str() can echo the submitted API
    # key verbatim -- e.g. "Incorrect API key provided: sk-...".
    distinctive_key = "sk-DISTINCTIVE-FAKE-LEAK-TEST-KEY-13579"

    async def _raises_with_a_leaked_looking_key(payload: dict) -> None:
        raise _fake_authentication_error(f"Incorrect API key provided: {distinctive_key}")

    monkeypatch.setitem(JOB_HANDLERS, "leaks-key-test-only", _raises_with_a_leaked_looking_key)
    job_id = await _enqueue(_seeded_tenant, "leaks-key-test-only")

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    row = await _fetch_job(job_id)
    assert row.error == "handler raised AuthenticationError"
    assert distinctive_key not in row.error

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
        # Task 2.6.b: direct Job construction, bypassing enqueue() -- the
        # identical reasoning as _enqueue()'s own updated comment above
        # ("sleep"/"noop" are worker-loop test scaffolding, never part of
        # the real VALID_JOB_TYPES vocabulary).
        slow_job = Job(
            tenant_id=tenant_id, job_type="sleep", status="pending", payload={"seconds": 0.5}
        )
        other_job = Job(tenant_id=tenant_id, job_type="noop", status="pending", payload={})
        session.add(slow_job)
        session.add(other_job)
        await session.commit()
        slow_job_id, other_job_id = slow_job.id, other_job.id

    env = _worker_subprocess_env(**{**VALID_ENV, "DATABASE_URL": database_url})
    async with spawn_module_subprocess("worker", env) as process:
        startup_line = await _wait_for_line_containing(
            process.stderr, "app_env=development", timeout=_WORKER_SUBPROCESS_STARTUP_TIMEOUT
        )
        assert startup_line is not None, "worker never logged its startup line"

        # Poll the real row, not a log line: proves the handler is
        # genuinely mid-flight (claimed, status flipped) before SIGTERM.
        await _poll_until_status(slow_job_id, "running", timeout=10)
        process.send_signal(signal.SIGTERM)
        exit_code = await asyncio.wait_for(process.wait(), timeout=10)
        assert exit_code == 0

    slow_job_row = await _fetch_job(slow_job_id)
    other_job_row = await _fetch_job(other_job_id)
    assert slow_job_row.status == "succeeded"  # handler finished normally, not left "running"
    assert other_job_row.status == "pending"  # never claimed after the signal


# --- Task 2.1.f: worker healthcheck -----------------------------------------
# The worker has no HTTP server, so its healthcheck is a file-based
# heartbeat instead (app/worker.py's own HEARTBEAT_PATH/_write_heartbeat()/
# check_heartbeat_fresh()). All three tests below are offline -- no real
# database needed: claim_next_job is monkeypatched to return None
# immediately (an idle iteration), exactly like the C1 test above, since the
# heartbeat write happens before the claim attempt either way.


async def _run_one_idle_iteration(monkeypatch, heartbeat_path, iterations=1):
    import app.worker as worker_module

    async def _claim_nothing(session):
        return None

    # Task 2.6.f: reap_stuck_jobs() now runs every iteration too, BEFORE
    # claim_next_job() -- stubbed to a real-DB-free no-op for the exact
    # same reason claim_next_job() is stubbed below (this helper's own
    # fake DATABASE_URL must never be dialed for real). Found live, not
    # assumed harmless: an earlier version without this stub passed
    # standalone but failed inside the full suite with a real
    # asyncio.TimeoutError -- a fake, unreachable "localhost:5432" can
    # hang long enough to exceed this test's own 10s timeout rather than
    # failing fast, unlike the mocked claim_next_job() raise the C1 test
    # (above) relies on.
    async def _reap_noop(*args, **kwargs):
        return []

    # Task 2.9.a: ensure_scheduler_ticks_seeded() now runs every iteration
    # too, for the identical real-DB-free reason reap_stuck_jobs() is
    # stubbed above -- found live, not assumed: an earlier version without
    # this stub hit this same helper's own fake, unreachable DATABASE_URL.
    async def _seed_noop(*args, **kwargs):
        return []

    monkeypatch.setattr(worker_module, "HEARTBEAT_PATH", heartbeat_path)
    monkeypatch.setattr(worker_module, "_reap_stuck_jobs", _reap_noop)
    monkeypatch.setattr(worker_module, "_ensure_scheduler_ticks_seeded", _seed_noop)
    monkeypatch.setattr(worker_module, "claim_next_job", _claim_nothing)
    # 0.5s, not something tighter: check_heartbeat_fresh()'s own staleness
    # threshold is HEARTBEAT_STALE_MULTIPLIER * worker_poll_interval_seconds
    # (3x), so shrinking this value for test speed also shrinks the
    # threshold the test's own later assertion is checked against -- too
    # tight (e.g. 0.05s -> a 0.15s threshold) leaves no headroom for
    # ordinary test-process overhead between the write and the assertion,
    # a real flake this test hit once before settling on 0.5s.
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "0.5")
    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=iterations), timeout=10)


async def test_heartbeat_is_written_fresh_after_one_loop_iteration(monkeypatch, tmp_path):
    heartbeat_path = tmp_path / "heartbeat"
    assert not heartbeat_path.exists()

    await _run_one_idle_iteration(monkeypatch, heartbeat_path)

    assert heartbeat_path.exists()
    assert check_heartbeat_fresh(heartbeat_path)


async def test_heartbeat_updates_on_every_idle_iteration_not_just_when_a_job_is_claimed(
    monkeypatch, tmp_path
):
    # The property that makes idle-vs-wedged distinguishable: proven here
    # by running TWO idle iterations (nothing ever claimed, via the same
    # monkeypatched claim_next_job as _run_one_idle_iteration) and
    # confirming the heartbeat's own written timestamp strictly advances
    # between them -- not merely "still fresh" (which a single stale-ish
    # write could also satisfy), a genuine second write.
    heartbeat_path = tmp_path / "heartbeat"

    await _run_one_idle_iteration(monkeypatch, heartbeat_path, iterations=1)
    first_write = float(heartbeat_path.read_text())

    await _run_one_idle_iteration(monkeypatch, heartbeat_path, iterations=1)
    second_write = float(heartbeat_path.read_text())

    assert second_write > first_write


def test_check_heartbeat_fresh_detects_a_stale_heartbeat(monkeypatch, tmp_path):
    # Testable without any Docker healthcheck machinery, as instructed:
    # this is check_heartbeat_fresh()'s own pure logic, the same function
    # deploy/docker-compose.yml's real healthcheck calls via its
    # `python3 -c "..."` one-liner (matching the API's own no-curl-in-the-
    # image convention, Task 1.4.j) -- one shared source of truth, not
    # duplicated shell-script and Python logic that could drift apart.
    # check_heartbeat_fresh() reads Settings (worker_poll_interval_seconds),
    # so a valid environment is needed even though this test touches no
    # database -- same requirement every other Settings-reading test here
    # already has to satisfy.
    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()
    heartbeat_path = tmp_path / "heartbeat"

    heartbeat_path.write_text(str(time.time()))
    assert check_heartbeat_fresh(heartbeat_path) is True

    # Simulates a wedged worker: the loop stopped iterating a long time
    # ago, so its last heartbeat write is far older than any reasonable
    # threshold -- HEARTBEAT_STALE_MULTIPLIER * worker_poll_interval_seconds
    # (3 * 2 = 6s by default) is nowhere close to 9999s.
    heartbeat_path.write_text(str(time.time() - 9999))
    assert check_heartbeat_fresh(heartbeat_path) is False

    missing_path = tmp_path / "does-not-exist"
    assert check_heartbeat_fresh(missing_path) is False


# --- Task 2.6.f: the stuck-job reaper, run through the real loop -------------
# test_queue.py's own test_reap_stuck_jobs_* prove reap_stuck_jobs() itself
# directly; this proves it is genuinely WIRED into run()'s own loop, not
# just a correct primitive nothing calls.


async def test_run_reaps_a_stuck_job_during_its_own_loop(monkeypatch, _seeded_tenant):
    tenant_id = _seeded_tenant
    stale_after_seconds = 3600
    async with db_session() as session:
        job = Job(
            tenant_id=tenant_id,
            job_type="noop",
            status="running",
            payload={},
            updated_at=datetime.now(UTC) - timedelta(seconds=stale_after_seconds + 1),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    monkeypatch.setenv("JOB_STUCK_AFTER_SECONDS", str(stale_after_seconds))

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    row = await _fetch_job(job_id)
    # Reclaimed via the normal backoff path (mark_job_failed(), not a
    # bare reset) -- same real consequence test_queue.py's own direct
    # proof already establishes, now confirmed to actually fire from
    # inside a real run() iteration.
    assert row.status == "pending"
    assert row.attempts == 1
    assert "stuck" in row.error


async def test_run_leaves_a_genuinely_running_job_alone_during_its_own_loop(
    monkeypatch, _seeded_tenant
):
    tenant_id = _seeded_tenant
    stale_after_seconds = 3600
    async with db_session() as session:
        job = Job(
            tenant_id=tenant_id,
            job_type="noop",
            status="running",
            payload={},
            # Genuinely, comfortably fresh relative to the 3600s
            # threshold -- NOT "threshold - 1 second", which real test/
            # asyncio overhead between this insert and the reap sweep
            # actually running (confirmed live: enough to exceed a
            # 1-second margin) made flake on the exact boundary.
            updated_at=datetime.now(UTC) - timedelta(seconds=10),
        )
        session.add(job)
        await session.commit()
        job_id = job.id

    monkeypatch.setenv("JOB_STUCK_AFTER_SECONDS", str(stale_after_seconds))

    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    row = await _fetch_job(job_id)
    assert row.status == "running"
    assert row.attempts == 0
    assert row.error is None
