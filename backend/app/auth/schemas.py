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
    # VERIFIED at Step 5.1 Part C (2026-10-15, docs/DECISIONS_REGISTER.md's
    # "Backend contract gaps" / D-6.1-session-field-na): the widget track,
    # the first real consumer, confirmed all four fields here and all six
    # _PUBLIC_WIDGET_CONFIG_FIELDS keys are exactly what Step 5.2 needs --
    # this is no longer an unverified assumption, per rule 10 ("verify then
    # remove ASSUMPTION").
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
