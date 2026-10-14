# backend/app/plans/schemas.py
# Task 4.3: the typed schema for `plans.limits` (docs/SPEC.md §5.4) --
# closes the four accumulated "plans.limits is still an unstructured
# JSONB stub, Task 4.3's own job" markers in `app/config.py` (crawl_page_
# cap, upload_max_size_bytes, db_sync_row_cap, refresh_interval_min_
# allowed/refresh_now_daily_allowance) plus adds the one new field this
# task's own quota system needs (`ai_message_quota_per_month`) and the
# one §5.4 names but no Settings field stands in for yet (`total_indexed_
# pages`). Per Felix's own Phase 4 kickoff decision (b): one typed model,
# validated on read, so a typo in a plan's JSONB blob fails loudly at
# read time instead of silently defaulting -- not read ad hoc per field
# the way every one of those Settings-stand-in fields' own comments
# currently describes.
#
# SCOPE, stated explicitly rather than silently assumed: this task
# defines the TYPE and uses it for the one field its own quota system
# needs (`ai_message_quota_per_month`, via `get_plan_limits()`, app/
# plans/service.py). It deliberately does NOT rewire the five existing
# Settings-stand-in call sites (app/config.py, app/ingest/job_handlers.py,
# app/ingest/database_adapter.py, app/ingest/repository.py) to read from
# a tenant's real plan instead of the global Settings default -- that is
# a separate, wide-blast-radius change across four already-shipped,
# already-tested ingestion files, unrelated to "usage, quotas and alert
# emails" (this step's own name, docs/SPEC.md §18) and to the specific
# ask this task was given. See PROJECT_SPEC.md's own new §9 marker
# (owner: whoever next revisits per-plan ingestion limits) for that
# still-open follow-up, re-scoped from "Task 4.3" now that this task's
# own real scope is decided.
#
# extra="forbid" (matching every other schema in this project, e.g.
# ChatRequest): a plan row with an unrecognized key is exactly the kind
# of silent typo rule 10 exists to surface loudly, not accept quietly.
# No field has a default: every plan must declare every limit explicitly
# once this type is used to read it -- the "typo fails loudly" guarantee
# would be hollow if a missing key just silently fell back to a default
# instead of raising.
from pydantic import BaseModel, ConfigDict, Field


class PlanLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The AI-message monthly quota (docs/SPEC.md §9's own "Quota: monthly
    # AI-answered messages by plan") -- this task's own real consumer,
    # via app/plans/usage.py's is_over_quota().
    ai_message_quota_per_month: int = Field(gt=0)
    # docs/SPEC.md §5.4: "total indexed web pages per tenant (crawl and
    # URL-list pages only)" -- a tenant-wide cap across every source,
    # distinct from crawl_page_cap below (a single crawl RUN's own safety
    # ceiling). No real enforcement call site reads this field yet (see
    # this module's own header comment) -- the type exists so a future
    # consumer can read it without a second schema-design pass.
    total_indexed_pages: int = Field(gt=0)
    # docs/SPEC.md §5.5: the per-crawl safety cap -- mirrors Settings.
    # crawl_page_cap's own global-default shape, per plan instead.
    crawl_page_cap: int = Field(gt=0)
    # docs/SPEC.md §5.4: "upload storage and file size... from the plan"
    # -- mirrors Settings.upload_max_size_bytes's own global-default
    # shape, per plan instead.
    upload_max_size_bytes: int = Field(gt=0)
    # docs/SPEC.md §5.4: "maximum synced database rows" -- mirrors
    # Settings.db_sync_row_cap's own global-default shape, per plan
    # instead.
    db_sync_row_cap: int = Field(gt=0)
    # docs/SPEC.md §5.4: "refresh frequency... from the plan" -- the
    # same four-value vocabulary Settings.refresh_interval_min_allowed
    # and app/ingest/models.py's REFRESH_INTERVAL_DELTAS already share
    # (rule 11: one vocabulary, not a second parallel one here).
    refresh_interval_min_allowed: str = Field(
        pattern="^(daily|weekly|monthly|quarterly)$"
    )
    # docs/SPEC.md §5.4: "refresh-now (with a cooldown and a daily
    # allowance from the plan)" -- mirrors Settings.refresh_now_daily_
    # allowance's own global-default shape, per plan instead.
    refresh_now_daily_allowance: int = Field(gt=0)
