# backend/app/handoff/repository.py
# Step 6.4: tenant-scoped access to `escalations` for the dashboard
# Inbox screen -- list/get/count-open/set-status/delete. Same shape as
# app/tenancy/repository.py's own TenantScopedRepository: a frozen
# dataclass holding `tenant_id`/`session`, every method scoped by
# `self.tenant_id` with no caller-supplied tenant parameter anywhere,
# and "wrong tenant looks exactly like missing" (a `None` return, never
# a different error) for any lookup by id.
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.handoff.models import Escalation

_OPEN_STATUSES = ("new", "seen")


@dataclass(frozen=True)
class EscalationRepository:
    tenant_id: uuid.UUID
    session: AsyncSession

    async def list_escalations(self) -> list[Escalation]:
        result = await self.session.execute(
            select(Escalation)
            .where(Escalation.tenant_id == self.tenant_id)
            .order_by(Escalation.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_escalation(self, escalation_id: uuid.UUID) -> Escalation | None:
        result = await self.session.execute(
            select(Escalation).where(
                Escalation.id == escalation_id, Escalation.tenant_id == self.tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def count_open(self) -> int:
        # The sidebar's own badge count -- a separate, cheap query
        # rather than making every screen fetch the full list just to
        # derive this number, since the sidebar is mounted regardless
        # of which screen is active.
        result = await self.session.execute(
            select(func.count())
            .select_from(Escalation)
            .where(
                Escalation.tenant_id == self.tenant_id,
                Escalation.status.in_(_OPEN_STATUSES),
            )
        )
        return result.scalar_one()

    async def set_status(self, escalation_id: uuid.UUID, status: str) -> Escalation | None:
        escalation = await self.get_escalation(escalation_id)
        if escalation is None:
            return None
        escalation.status = status
        await self.session.flush()
        return escalation

    async def delete_escalation(self, escalation_id: uuid.UUID) -> bool:
        escalation = await self.get_escalation(escalation_id)
        if escalation is None:
            return False
        await self.session.delete(escalation)
        await self.session.flush()
        return True
