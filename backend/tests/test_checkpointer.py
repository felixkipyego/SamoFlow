# backend/tests/test_checkpointer.py
# Task 3.2.b [SECURITY]: live tests for the dedicated checkpointer pool +
# AsyncPostgresSaver singleton (app/agent/checkpointer.py), real against
# test-db -- the guarded entry point itself is Task 3.2.c's own job, not
# tested here.
import uuid
import warnings

import psycopg
import pytest
from langgraph.checkpoint.base import CheckpointMetadata, empty_checkpoint

from app.agent import checkpointer
from tests.conftest import reset_checkpoint_env

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")


def test_get_checkpointer_before_open_raises_a_clear_error():
    # Pure/offline: no env, no pool -- confirms the module's own starting
    # state (Task 3.2.b decision 1: fail loudly, never silently open).
    assert checkpointer._checkpointer_pool is None
    with pytest.raises(RuntimeError, match="has not been opened yet"):
        checkpointer.get_checkpointer()


async def test_open_checkpointer_pool_emits_no_runtime_warning(monkeypatch):
    # Task 3.2.a's own finding: constructing psycopg_pool's async pool with
    # the default open behavior (or open=True) emits a RuntimeWarning.
    # open_checkpointer_pool() uses open=False + an explicit await
    # pool.open() specifically to avoid this -- asserted directly here,
    # not just eyeballed from a prior manual run.
    await reset_checkpoint_env(monkeypatch)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await checkpointer.open_checkpointer_pool()

    assert caught == []


async def test_checkpointer_pool_opens_and_round_trips_a_checkpoint(monkeypatch):
    # The pool opens via the same path app/main.py's own lifespan hook
    # uses, and a real AsyncPostgresSaver (get_checkpointer()) writes and
    # reads back a real checkpoint for one real thread_id, against the
    # real test-db -- not mocked, not inferred.
    conninfo = await reset_checkpoint_env(monkeypatch)
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        await checkpointer.AsyncPostgresSaver(conn).setup()

    await checkpointer.open_checkpointer_pool()
    saver = checkpointer.get_checkpointer()

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": "hello"}
    metadata = CheckpointMetadata(source="input", step=1)

    await saver.aput(config, checkpoint, metadata, {})

    result = await saver.aget_tuple(config)

    assert result is not None
    assert result.checkpoint["id"] == checkpoint["id"]
    assert result.checkpoint["channel_values"]["messages"] == "hello"
    assert result.config["configurable"]["thread_id"] == thread_id


async def test_checkpointer_setup_run_twice_is_idempotent(monkeypatch):
    # setup()'s own migration-version bookkeeping (Task 3.2.a's own
    # finding) means a second call applies zero new migrations -- checked
    # directly against checkpoint_migrations, not just "it didn't raise".
    conninfo = await reset_checkpoint_env(monkeypatch)

    await checkpointer.run_checkpointer_setup()

    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        first_count = (
            await (await conn.execute("SELECT count(*) FROM checkpoint_migrations")).fetchone()
        )[0]

    await checkpointer.run_checkpointer_setup()

    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        second_count = (
            await (await conn.execute("SELECT count(*) FROM checkpoint_migrations")).fetchone()
        )[0]

    assert first_count > 0
    assert second_count == first_count
