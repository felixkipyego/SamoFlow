# backend/tests/test_usage_alert_sweep.py
# Task 4.3: tests for handle_usage_alert_sweep() (app/ingest/
# job_handlers.py) -- the worker's own 80%/100% quota-alert sweep.
# Driven through the REAL worker.run() loop (matching handle_reconcile()'s
# own established proof shape, test_job_handlers.py's "handle_reconcile()
# self-re-enqueue" section) -- JOB_HANDLERS["usage_alert_sweep"] end to
# end, not the bare function called directly.
import asyncio
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from app.auth.secrets import generate_visitor_secret, hash_visitor_secret
from app.ingest import job_handlers as job_handlers_module
from app.ingest.models import Job
from app.ingest.repository import IngestRepository
from app.plans.models import EmailLog, Plan, UsageEvent
from app.tenancy.models import Conversation, SiteKey, Tenant, Visitor
from app.worker import run
from tests.conftest import VALID_ENV, db_session, set_valid_env

ORIGIN = "https://tenant.example"

_PLAN_LIMITS = {
    "ai_message_quota_per_month": 10,
    "total_indexed_pages": 500,
    "crawl_page_cap": 500,
    "upload_max_size_bytes": 1,
    "db_sync_row_cap": 1,
    "refresh_interval_min_allowed": "daily",
    "refresh_now_daily_allowance": 1,
}


async def _fetch_job(job_id: uuid.UUID) -> Job:
    async with db_session() as session:
        return (await session.execute(sa.select(Job).where(Job.id == job_id))).scalar_one()


@pytest.fixture
async def _seeded(reset_test_database):
    tenant_id = uuid.uuid4()
    site_key_id = uuid.uuid4()
    vid = uuid.uuid4()
    cid = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Usage Alert Tenant", status="active", config={}))
        await session.flush()
        session.add(
            SiteKey(
                id=site_key_id,
                tenant_id=tenant_id,
                key="pk_live_usage_alert_test",
                environment="production",
                status="live",
                allowed_origins=[ORIGIN],
            )
        )
        await session.flush()
        session.add(
            Visitor(
                vid=vid,
                tenant_id=tenant_id,
                site_key_id=site_key_id,
                secret_hash=hash_visitor_secret(generate_visitor_secret()),
            )
        )
        await session.flush()
        session.add(Conversation(cid=cid, tenant_id=tenant_id, vid=vid, title=None))
        await session.commit()
    return {"tenant_id": tenant_id, "vid": vid, "cid": cid}


async def _assign_plan(tenant_id: uuid.UUID, limits: dict) -> None:
    async with db_session() as session:
        plan = Plan(id=uuid.uuid4(), name="Starter", limits=limits)
        session.add(plan)
        await session.flush()
        await session.execute(
            sa.update(Tenant).where(Tenant.id == tenant_id).values(plan_id=plan.id)
        )
        await session.commit()


async def _seed_counted_usage_events(seeded: dict, count: int) -> None:
    async with db_session() as session:
        session.add_all(
            [
                UsageEvent(
                    tenant_id=seeded["tenant_id"],
                    vid=seeded["vid"],
                    conversation_id=seeded["cid"],
                    counted_toward_quota=True,
                    model="gpt-6-luna",
                    prompt_tokens=10,
                    completion_tokens=5,
                    estimated_cost_usd=0.001,
                    created_at=datetime.now(UTC),
                )
                for _ in range(count)
            ]
        )
        await session.commit()


async def _enqueue_sweep(tenant_id: uuid.UUID) -> uuid.UUID:
    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        job = await repo.enqueue(job_type="usage_alert_sweep")
        await session.commit()
        return job.id


async def _email_log_rows(tenant_id: uuid.UUID) -> list[EmailLog]:
    async with db_session() as session:
        return (
            await session.execute(sa.select(EmailLog).where(EmailLog.tenant_id == tenant_id))
        ).scalars().all()


async def test_no_plan_assigned_sends_nothing(monkeypatch, _seeded):
    set_valid_env(monkeypatch, VALID_ENV)

    async def _raise_if_called(*args, **kwargs):
        raise AssertionError("send_email must never be called for a tenant with no plan")

    monkeypatch.setattr(job_handlers_module, "send_email", _raise_if_called)

    job_id = await _enqueue_sweep(_seeded["tenant_id"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    job = await _fetch_job(job_id)
    assert job.status == "succeeded"
    assert await _email_log_rows(_seeded["tenant_id"]) == []


async def test_under_80_percent_sends_nothing(monkeypatch, _seeded):
    set_valid_env(monkeypatch, VALID_ENV)
    await _assign_plan(_seeded["tenant_id"], _PLAN_LIMITS)
    await _seed_counted_usage_events(_seeded, count=5)  # 50% of quota=10

    async def _raise_if_called(*args, **kwargs):
        raise AssertionError("send_email must never be called under 80%")

    monkeypatch.setattr(job_handlers_module, "send_email", _raise_if_called)

    job_id = await _enqueue_sweep(_seeded["tenant_id"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    assert await _email_log_rows(_seeded["tenant_id"]) == []


async def test_80_percent_with_no_alert_email_configured_records_skipped(monkeypatch, _seeded):
    set_valid_env(monkeypatch, VALID_ENV)
    await _assign_plan(_seeded["tenant_id"], _PLAN_LIMITS)
    await _seed_counted_usage_events(_seeded, count=8)  # 80% of quota=10

    async def _raise_if_called(*args, **kwargs):
        raise AssertionError("send_email must never be called with no alert_email configured")

    monkeypatch.setattr(job_handlers_module, "send_email", _raise_if_called)

    job_id = await _enqueue_sweep(_seeded["tenant_id"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    rows = await _email_log_rows(_seeded["tenant_id"])
    assert len(rows) == 1
    assert rows[0].threshold == "80"
    assert rows[0].status == "skipped_not_configured"


async def test_100_percent_sends_both_thresholds_when_configured(monkeypatch, _seeded):
    set_valid_env(monkeypatch, VALID_ENV)
    await _assign_plan(_seeded["tenant_id"], _PLAN_LIMITS)
    await _seed_counted_usage_events(_seeded, count=10)  # 100% of quota=10
    async with db_session() as session:
        await session.execute(
            sa.update(Tenant)
            .where(Tenant.id == _seeded["tenant_id"])
            .values(config={"alert_email": "owner@tenant.example"})
        )
        await session.commit()

    sent_calls = []

    async def _fake_send_email(to, subject, body):
        sent_calls.append((to, subject))
        return True

    monkeypatch.setattr(job_handlers_module, "send_email", _fake_send_email)

    job_id = await _enqueue_sweep(_seeded["tenant_id"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    rows = {row.threshold: row for row in await _email_log_rows(_seeded["tenant_id"])}
    assert set(rows.keys()) == {"80", "100"}
    assert rows["80"].status == "sent"
    assert rows["100"].status == "sent"
    assert len(sent_calls) == 2
    assert all(to == "owner@tenant.example" for to, _subject in sent_calls)


async def test_already_sent_80_is_never_resent(monkeypatch, _seeded):
    set_valid_env(monkeypatch, VALID_ENV)
    await _assign_plan(_seeded["tenant_id"], _PLAN_LIMITS)
    await _seed_counted_usage_events(_seeded, count=8)  # 80%, not 100%
    async with db_session() as session:
        await session.execute(
            sa.update(Tenant)
            .where(Tenant.id == _seeded["tenant_id"])
            .values(config={"alert_email": "owner@tenant.example"})
        )
        session.add(
            EmailLog(
                tenant_id=_seeded["tenant_id"],
                threshold="80",
                month=datetime.now(UTC).strftime("%Y-%m"),
                status="sent",
                attempts=1,
            )
        )
        await session.commit()

    async def _raise_if_called(*args, **kwargs):
        raise AssertionError("send_email must never be called once already sent this month")

    monkeypatch.setattr(job_handlers_module, "send_email", _raise_if_called)

    job_id = await _enqueue_sweep(_seeded["tenant_id"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    rows = await _email_log_rows(_seeded["tenant_id"])
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert rows[0].attempts == 1  # untouched -- dedup stopped before any send attempt


async def test_a_failed_send_is_retried_and_increments_attempts(monkeypatch, _seeded):
    set_valid_env(monkeypatch, VALID_ENV)
    await _assign_plan(_seeded["tenant_id"], _PLAN_LIMITS)
    await _seed_counted_usage_events(_seeded, count=8)
    async with db_session() as session:
        await session.execute(
            sa.update(Tenant)
            .where(Tenant.id == _seeded["tenant_id"])
            .values(config={"alert_email": "owner@tenant.example"})
        )
        session.add(
            EmailLog(
                tenant_id=_seeded["tenant_id"],
                threshold="80",
                month=datetime.now(UTC).strftime("%Y-%m"),
                status="failed",
                attempts=1,
            )
        )
        await session.commit()

    async def _fake_send_email(to, subject, body):
        return True

    monkeypatch.setattr(job_handlers_module, "send_email", _fake_send_email)

    job_id = await _enqueue_sweep(_seeded["tenant_id"])
    await asyncio.wait_for(run(asyncio.Event(), max_iterations=1), timeout=10)

    assert (await _fetch_job(job_id)).status == "succeeded"
    rows = await _email_log_rows(_seeded["tenant_id"])
    assert len(rows) == 1
    assert rows[0].status == "sent"
    assert rows[0].attempts == 2
