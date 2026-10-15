# backend/app/dashboard/schemas.py
# Step 6.1: request/response models for the dashboard auth endpoints.
import uuid

from pydantic import BaseModel, ConfigDict


class LoginRequest(BaseModel):
    # extra="forbid": an unknown field 422s before the handler body (and
    # therefore before any rate-limit/database work) ever runs -- matches
    # app.auth.schemas.SessionRequest's own established convention.
    model_config = ConfigDict(extra="forbid")

    email: str
    password: str


class DashboardUser(BaseModel):
    id: uuid.UUID
    email: str


class DashboardTenant(BaseModel):
    id: uuid.UUID
    name: str


class DashboardSessionResponse(BaseModel):
    # The shared shape for both POST /auth/login and GET /auth/me --
    # "who am I logged in as, and which tenant does my membership scope
    # me to" is the same question either way.
    user: DashboardUser
    tenant: DashboardTenant
