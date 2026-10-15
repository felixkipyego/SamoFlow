# backend/app/admin/routes.py
# Step 6.1 [SECURITY]: /api/v1/admin, gated by platform_admin sessions
# (app/dashboard/auth_backend.py's own current_platform_admin -- the
# SAME login system tenant users use, docs/SPEC.md §12's own "Step 6.1
# removes [the temporary admin key] and replaces it with platform-admin
# accounts... that use the same login system as tenant users"). Felix's
# own 2026-10-15 correction: this OVERRIDES this task's own earlier
# "keep the admin key" line -- the temporary mechanism (app/admin/
# dependencies.py's former require_admin_key()/AdminKeyVerified,
# Settings.admin_api_key) is removed outright in this same task, not
# extended, matching the already-existing §9 marker's own instruction
# (recorded at Task 2.2.f) exactly, now that it is finally true.
#
# EVERY /api/v1/admin endpoint uses current_platform_admin -- there is
# no other identity mechanism left in this package to accidentally wire
# a route to.
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.dashboard.auth_backend import current_platform_admin
from app.dashboard.csrf import require_csrf
from app.dashboard.models import User
from app.db import get_db_session
from app.ingest.repository import revoke_domain

router = APIRouter(prefix="/api/v1/admin")


def list_platform_tenants():
    # TODO(6.5): the real tenant-listing/plan-management/suspend screen
    # (docs/SPEC.md §12's own "Platform admin (you): manage tenants and
    # plans, suspend a site...") is still POST-PILOT (PROJECT_SPEC.md §9:
    # "the existing [mechanism] already does this for a single hand-
    # picked pilot tenant; a UI here has no tenant-facing value yet").
    # The IDENTITY mechanism this endpoint will eventually sit behind is
    # real now, Step 6.1 (current_platform_admin, above) -- this
    # function's own earlier deferral note named the now-removed
    # temporary admin-key mechanism as the reason to wait; that reason
    # no longer applies, only the feature-scope reason (6.5 is still
    # POST-PILOT) does.
    raise NotImplementedError


@router.get("/me")
async def admin_whoami(admin: User = Depends(current_platform_admin)) -> dict:  # noqa: B008
    # The one real, minimal endpoint proving the platform_admin gate end
    # to end (Felix's own 2026-10-15 correction: "Build the platform_
    # admin role now... /api/v1/admin accepts platform_admin sessions
    # only"). Every other platform-admin-facing action the pilot needs
    # (create a tenant, assign a plan, issue/revoke a site key, revoke a
    # domain, seed an owner) is a server-side CLI command instead (app/
    # dashboard/admin_cli.py) -- 6.5's own dashboard UI is still
    # POST-PILOT, per the existing, unchanged docs/SPEC.md §18/
    # PROJECT_SPEC.md §9 decision.
    return {"id": str(admin.id), "email": admin.email}


@router.post("/domains/{domain_id}/revoke")
async def revoke_domain_route(
    domain_id: uuid.UUID,
    request: Request,
    admin: User = Depends(current_platform_admin),  # noqa: B008
    session: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict:
    # `revoke_domain()` (app/ingest/repository.py, Task 2.2.g) has
    # existed as an unwired primitive since Phase 2, waiting exactly for
    # this -- "no endpoint is built on top of it until Step 6.1... wires
    # revoke_domain() to a real endpoint under the real admin-identity
    # system directly". `actor` is the real platform admin's own email,
    # not a generic placeholder string -- the identity-less stopgap
    # (admin="admin") this function's own docstring named as temporary
    # is gone the moment a real caller exists.
    require_csrf(request)
    domain = await revoke_domain(session, domain_id, actor=admin.email)
    if domain is None:
        raise HTTPException(status_code=404, detail="domain not found")
    await session.commit()
    return {"id": str(domain.id), "status": domain.status}
