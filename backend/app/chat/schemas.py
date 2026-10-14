# backend/app/chat/schemas.py
# Task 4.1: the request body for POST /api/v1/chat (docs/SPEC.md §10:
# "{message, conversation_id?, client_message_id}"). Pure data shape only
# -- no database or settings access here, matching app/auth/schemas.py's
# own layering.
#
# `message` deliberately has NO max_length here: docs/SPEC.md §8's own
# 2,000-character cap is enforced explicitly in app/chat/routes.py instead,
# because the spec also names a SPECIFIC status code for that one failure
# (413, docs/SPEC.md §10's own "Auth, rate-limit and quota failures return
# real HTTP codes (401, 413, 429)") -- a pydantic Field(max_length=...)
# violation would 422 instead (FastAPI/Pydantic's own default), the wrong
# code for this specific, spec-named case. min_length=1 still belongs here
# (an empty message is a plain, generic malformed-request case, not the
# named 413 case) -- rejected at the ordinary 422 schema-validation layer.
#
# `conversation_id`/`client_message_id` are both `uuid.UUID`, not `str`:
# Pydantic parses and validates the UUID shape itself at this boundary, so
# a malformed id 422s before the handler body (and therefore before any
# database access) ever runs -- the same "reject at the boundary" shape
# `extra="forbid"` already gives every other field.
import uuid

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    conversation_id: uuid.UUID | None = None
    client_message_id: uuid.UUID
