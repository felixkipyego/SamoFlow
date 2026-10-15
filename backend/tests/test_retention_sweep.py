# backend/tests/test_retention_sweep.py
# [SECURITY] Task 4.2 adversarial follow-up: handle_retention_sweep()
# (app/ingest/job_handlers.py) had NO dedicated functional test anywhere
# in this codebase (confirmed by grep before writing this file) --
# a real gap, given this handler deletes real tenant data permanently.
# Driven through the REAL worker.run() loop, matching handle_reconcile()'s/
# handle_usage_alert_sweep()'s own established proof shape.
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.agent import checkpointer
from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.ingest.models import Job
from app.ingest.repository import IngestRepository
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor
from app.worker import run
from tests.conftest import VALID_ENV, db_session, set_valid_env

ORIGIN = "https://tenant.example"

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")


async def _fetch_job(job_id: uuid.UUID) -> Job:
    async with db_session() as session:
        return (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()


@pytest.fixture
async def _seeded_pair(reset_test_database):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    site_key_id = uuid.uuid4()
    vid_a = uuid.uuid4()
    vid_b = uuid.uuid4()
    async with db_session() as session:
        session.add_all(
            [
                Tenant(
                    id=tenant_a,
                    name="A",
                    status="active",
                    config={"retention_days": 1},
                ),
                Tenant(
                    id=tenant_b,
                    name="B",
                    status="active",
                    config={"retention_days": 1},
                ),
            ]
        )
        await session.flush()
        session.add(
            SiteKey(
                id=site_key_id,
                tenant_id=tenant_a,
                key="pk_live_retention_test",
                environment="production",
                status="live",
                allowed_origins=[ORIGIN],
            )
        )
        await session.flush()
        session.add_all(
            [
                Visitor(
                    vid=vid_a,
                    tenant_id=tenant_a,
                    site_key_id=site_key_id,
                    secret_hash=hash_visitor_secret(generate_visitor_secret()),
                ),
                Visitor(
                    vid=vid_b,
                    tenant_id=tenant_b,
                    site_key_id=site_key_id,
                    secret_hash=hash_visitor_secret(generate_visitor_secret()),
                ),
            ]
        )
        await session.commit()
    return {"tenant_a": tenant_a, "tenant_b": tenant_b, "vid_a": vid_a, "vid_b": vid_b}


async def _enqueue_sweep(tenant_id: uuid.UUID) -> uuid.UUID:
    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        job = await repo.enqueue(job_type="retention_sweep")
        await session.commit()
        return job.id


async def test_retention_sweep_never_deletes_another_tenants_conversation(
    monkeypatch, _seeded_pair
):
    set_valid_env(monkeypatch, VALID_ENV)
    await checkpointer.open_checkpointer_pool()
    old = datetime.now(UTC) - timedelta(days=30)
    expired_a_cid = uuid.uuid4()
    fresh_b_cid = uuid.uuid4()
    async with db_session() as session:
        session.add_all(
            [
                Conversation(
                    cid=expired_a_cid,
                    tenant_id=_seeded_pair["tenant_a"],
                    vid=_seeded_pair["vid_a"],
                    last_message_at=old,
                ),
                # tenant_b's own conversation is ALSO expired by age --
                # tenant_a's own sweep job must never touch it, since
                # each self-perpetuating sweep job is scoped to exactly
                # one tenant (job.tenant_id) by construction.
                Conversation(
                    cid=fresh_b_cid,
                    tenant_id=_seeded_pair["tenant_b"],
                    vid=_seeded_pair["vid_b"],
                    last_message_at=old,
                ),
            ]
        )
        await session.commit()

    job_id = await _enqueue_sweep(_seeded_pair["tenant_a"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    async with db_session() as session:
        remaining = (
            await session.execute(sa.select(Conversation.cid))
        ).scalars().all()
    assert expired_a_cid not in remaining  # tenant_a's own expired row -- deleted
    assert fresh_b_cid in remaining  # tenant_b's own row -- UNTOUCHED by tenant_a's sweep


async def test_retention_sweep_does_not_delete_an_actively_leased_conversation(
    monkeypatch, _seeded_pair
):
    set_valid_env(monkeypatch, VALID_ENV)
    await checkpointer.open_checkpointer_pool()
    old = datetime.now(UTC) - timedelta(days=30)
    leased_cid = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Conversation(
                cid=leased_cid,
                tenant_id=_seeded_pair["tenant_a"],
                vid=_seeded_pair["vid_a"],
                last_message_at=old,  # expired by age...
                lease_until=datetime.now(UTC) + timedelta(seconds=30),  # ...mid-turn right now
                lease_token=uuid.uuid4(),
            )
        )
        await session.commit()

    job_id = await _enqueue_sweep(_seeded_pair["tenant_a"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    async with db_session() as session:
        remaining = (
            await session.execute(sa.select(Conversation.cid))
        ).scalars().all()
    assert leased_cid in remaining  # NEVER deleted while its own lease is still active
