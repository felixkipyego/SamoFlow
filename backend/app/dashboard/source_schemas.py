# backend/app/dashboard/source_schemas.py
# Step 6.3: request/response models for app/dashboard/source_routes.py.
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateUrlsSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["urls"]
    urls: list[str] = Field(min_length=1, max_length=200)
    refresh_interval: str = "daily"


class CreateCrawlSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["crawl"]
    seed_url: str
    refresh_interval: str = "daily"


class UpdateUrlsSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[str] | None = Field(default=None, max_length=200)
    refresh_interval: str | None = None


class SourceResponse(BaseModel):
    id: uuid.UUID
    type: str
    # "ok" | "syncing" | "error" | "disabled" -- derived live from the
    # most recent Job row for this source, never persisted (see
    # source_routes.py's own _derive_status() for why: Source.status
    # itself is a Step-2.9-era column that nothing has ever written a
    # second value into, confirmed by reading every real writer in
    # app/ingest/ directly before deciding not to reuse it as-is).
    status: Literal["ok", "syncing", "error", "disabled"]
    enabled: bool
    refresh_interval: str
    last_run_at: datetime | None
    next_run_at: datetime | None
    exceeds_plan: bool
    document_count: int
    may_need_javascript: bool
    config: dict


class SourcesListResponse(BaseModel):
    sources: list[SourceResponse]
    total_indexed_pages: int
    total_indexed_pages_limit: int | None


class DomainResponse(BaseModel):
    id: uuid.UUID
    domain: str
    method: str
    status: str
    verification_token: str
    verified_at: datetime | None
    revoked_at: datetime | None


class ClaimDomainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    method: Literal["dns", "file", "meta_tag"] = "dns"
