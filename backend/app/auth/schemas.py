# backend/app/auth/schemas.py
# Task 1.4.g: request/response models for POST /api/v1/session
# (docs/SPEC.md §4.2). Pure data shapes only -- no database or settings
# access here.
from pydantic import BaseModel, ConfigDict


class SessionRequest(BaseModel):
    # extra="forbid": any unknown field must 422 before the handler body
    # (and therefore before any database access) ever runs -- FastAPI/
    # Pydantic reject a malformed request body at the dependency-solving
    # stage, never calling the path operation function at all.
    model_config = ConfigDict(extra="forbid")

    site_key: str
    visitor_secret: str | None = None


class SessionResponse(BaseModel):
    # ASSUMPTION: docs/SPEC.md §4.2 names what the session response carries
    # (a JWT, the visitor secret on first visit, test_mode, and the public
    # widget config) but never fixes the JSON field names themselves --
    # these are this task's own choice, not a spec-mandated wire contract.
    # visitor_secret is None on a return visit (docs/SPEC.md §4.2: "do NOT
    # return a new secret") -- present in the JSON body as `null`, per
    # Pydantic's default serialization of an Optional field, not omitted.
    session_token: str
    visitor_secret: str | None
    test_mode: bool
    # Built ONLY from app/auth/routes.py's explicit public-field allow-list
    # (never the tenant's raw config JSONB) -- see that module's own
    # _public_widget_config() for why.
    widget_config: dict[str, object]
