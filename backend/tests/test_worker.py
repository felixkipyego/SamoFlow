# backend/tests/test_worker.py
# Tests for Task 1.1.f's worker entrypoint stub (backend/app/worker.py).
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import pytest

from app.config import SettingsError
from app.worker import run
from tests.conftest import REQUIRED_VARS, TEST_PASSWORD, set_valid_env

BACKEND_DIR = Path(__file__).resolve().parent.parent

VALID_ENV = {
    "APP_ENV": "development",
    "DATABASE_URL": "postgresql+psycopg://user:pw@localhost:5432/widgetplatform",
    "QDRANT_URL": "http://localhost:6333",
    "API_HOST": "127.0.0.1",
    "API_PORT": "8000",
}


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


def _subprocess_env(**overrides):
    env = {k: v for k, v in os.environ.items() if k not in REQUIRED_VARS}
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
    process = await _spawn_worker(_subprocess_env(**VALID_ENV))
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
    process = await _spawn_worker(_subprocess_env())
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode != 0
    assert b"Traceback" not in stderr
