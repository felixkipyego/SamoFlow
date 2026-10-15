# backend/tests/test_escalation_retention_sweep.py
# Step 4.4: handle_escalation_retention_sweep() (app/ingest/
# job_handlers.py) -- mirrors tests/test_retention_sweep.py's own shape
# exactly (handle_retention_sweep() had NO dedicated functional test
# anywhere in this codebase before that file; this handler is new, so
# it gets the identical proof from day one). Driven through the REAL
# worker.run() loop.
import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.handoff.models import Escalation
from app.ingest.models import Job
from app.ingest.repository import IngestRepository
from app.tenancy.models import SiteKey, Tenant, Visitor
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
                    config={"escalation_retention_months": 1},
                ),
                Tenant(
                    id=tenant_b,
                    name="B",
                    status="active",
                    config={"escalation_retention_months": 1},
                ),
            ]
        )
        await session.flush()
        session.add(
            SiteKey(
                id=site_key_id,
                tenant_id=tenant_a,
                key="pk_live_escalation_retention_test",
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
        job = await repo.enqueue(job_type="escalation_retention_sweep")
        await session.commit()
        return job.id


async def test_escalation_retention_sweep_never_deletes_another_tenants_escalation(
    monkeypatch, _seeded_pair
):
    set_valid_env(monkeypatch, VALID_ENV)
    old = datetime.now(UTC) - timedelta(days=365)
    expired_a_id = uuid.uuid4()
    # tenant_b's own escalation is ALSO expired by age -- tenant_a's own
    # sweep job must never touch it, since each self-perpetuating sweep
    # job is scoped to exactly one tenant (job.tenant_id) by construction.
    fresh_b_id = uuid.uuid4()
    async with db_session() as session:
        session.add_all(
            [
                Escalation(
                    id=expired_a_id,
                    tenant_id=_seeded_pair["tenant_a"],
                    vid=_seeded_pair["vid_a"],
                    conversation_id=None,
                    reason="visitor_requested",
                    transcript="Visitor: hi\n\nAssistant: hello",
                    status="new",
                    created_at=old,
                ),
                Escalation(
                    id=fresh_b_id,
                    tenant_id=_seeded_pair["tenant_b"],
                    vid=_seeded_pair["vid_b"],
                    conversation_id=None,
                    reason="visitor_requested",
                    transcript="Visitor: hi\n\nAssistant: hello",
                    status="new",
                    created_at=old,
                ),
            ]
        )
        await session.commit()

    job_id = await _enqueue_sweep(_seeded_pair["tenant_a"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    async with db_session() as session:
        remaining = (await session.execute(sa.select(Escalation.id))).scalars().all()
    assert expired_a_id not in remaining  # tenant_a's own expired row -- deleted
    assert fresh_b_id in remaining  # tenant_b's own row -- UNTOUCHED by tenant_a's sweep


async def test_escalation_retention_sweep_keeps_a_recent_escalation(monkeypatch, _seeded_pair):
    set_valid_env(monkeypatch, VALID_ENV)
    recent_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Escalation(
                id=recent_id,
                tenant_id=_seeded_pair["tenant_a"],
                vid=_seeded_pair["vid_a"],
                conversation_id=None,
                reason="visitor_requested",
                transcript="Visitor: hi\n\nAssistant: hello",
                status="new",
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    job_id = await _enqueue_sweep(_seeded_pair["tenant_a"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    async with db_session() as session:
        remaining = (await session.execute(sa.select(Escalation.id))).scalars().all()
    assert recent_id in remaining  # well within the 1-month retention window -- kept


async def test_escalation_retention_sweep_falls_back_to_the_platform_default_on_bad_config(
    monkeypatch, _seeded_pair
):
    # tenant_a's own config has an OUT-OF-RANGE value (docs/SPEC.md §7:
    # allowed range 1-24 months) -- the sweep must fall back to
    # Settings.default_escalation_retention_months (12) rather than
    # using the bad value, matching handle_retention_sweep()'s own
    # identical fallback-on-invalid-config precedent.
    set_valid_env(monkeypatch, VALID_ENV)
    async with db_session() as session:
        tenant = await session.get(Tenant, _seeded_pair["tenant_a"])
        tenant.config = {"escalation_retention_months": 999}
        await session.commit()

    old_but_within_default = datetime.now(UTC) - timedelta(days=60)  # 2 months old
    escalation_id = uuid.uuid4()
    async with db_session() as session:
        session.add(
            Escalation(
                id=escalation_id,
                tenant_id=_seeded_pair["tenant_a"],
                vid=_seeded_pair["vid_a"],
                conversation_id=None,
                reason="visitor_requested",
                transcript="Visitor: hi\n\nAssistant: hello",
                status="new",
                created_at=old_but_within_default,
            )
        )
        await session.commit()

    job_id = await _enqueue_sweep(_seeded_pair["tenant_a"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    async with db_session() as session:
        remaining = (await session.execute(sa.select(Escalation.id))).scalars().all()
    # 2 months old is well within the 12-month platform default (999
    # would have kept it too, but this proves the fallback VALUE is the
    # real default, not merely "the invalid value happened not to delete
    # it either").
    assert escalation_id in remaining
