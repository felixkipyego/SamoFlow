# backend/app/plans/models.py
# Task 1.2.b: ORM model for plans (docs/SPEC.md §5.4). Minimal on purpose --
# a placeholder table until Phase 2/4 features define real limit fields
# (PROJECT_SPEC.md decision: limits stays an unstructured JSONB blob for
# now). No repository/query logic here (that's 1.2.d).
#
# Task 4.3 adds UsageEvent and EmailLog below -- docs/SPEC.md §18's own
# table list groups `usage_events`/`email_log` right after `plans`, and
# both exist for the same reason this table does (per-tenant quota/
# billing data), so they live in this module rather than a new one.
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import TIMESTAMP_NOW, UUID_PK, Base


class Plan(Base):
    __tablename__ = "plans"

    # UUID PK (not a fixed string/enum id): docs/SPEC.md never names a fixed
    # set of plan identifiers, and a UUID keeps this table consistent with
    # every other table's PK style rather than introducing a second PK
    # scheme for one table (rule 11).
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    name: Mapped[str] = mapped_column(String, nullable=False)
    limits: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class UsageEvent(Base):
    __tablename__ = "usage_events"

    # Task 4.3: docs/SPEC.md §9's own quota system record -- one row per
    # chat turn (whether it counted toward quota or not, see
    # counted_toward_quota below), written in BATCHES by app/plans/
    # usage.py's periodic flush (docs/SPEC.md §9: "Usage rows are written
    # in batches"), never synchronously inside the request itself (that
    # would reintroduce a DB write mid-stream, the same connection-hold
    # risk Task 4.1's own follow-up exists to avoid). `usage_events never
    # contain message content` (docs/SPEC.md §4.6) -- confirmed by this
    # column list containing no message/answer text anywhere.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    vid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visitors.vid"), nullable=False
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.cid"), nullable=False
    )
    # docs/SPEC.md §9: "Fallback replies and escalations are free and
    # don't count [toward quota]" -- False for every turn that never made
    # a real chat-model call (guardrail, no-answer fallback, dependency
    # failure, quota-exhausted itself); True only for a turn that reached
    # `_answer_node` and got a real `can_answer=True` answer. A row is
    # still written either way (the dashboard usage view and the "per-
    # tenant token and cost view", docs/SPEC.md §9, need to see free
    # turns too, not just counted ones) -- model/token/cost columns below
    # are NULL on a free turn, since no real model call was ever made.
    counted_toward_quota: Mapped[bool] = mapped_column(nullable=False)
    # Felix's own Phase 4 kickoff refinement (#11): the RESOLVED model id
    # the real API response reported (`AIMessageChunk.response_metadata
    # ["model_name"]`, app/agent/graph.py's own `_answer_node`) -- never
    # just the configured alias (`Settings.chat_model_primary`) -- so a
    # silent upstream model change becomes visible in a cost report
    # before the alias itself is ever updated. NULL on a free turn.
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(nullable=True)
    # Numeric, not float: a per-tenant cost VIEW (docs/SPEC.md §9) will
    # sum this column across many rows -- float accumulation error is a
    # real, avoidable risk for a running total that will eventually back
    # a billing-adjacent view, even an internal one. NULL when `model` is
    # NULL (free turn) or when `model` is a real but UNPRICED resolved id
    # (app/plans/usage.py's own estimate_cost_usd() returns None rather
    # than guessing $0 for an unknown model -- see that function's own
    # docstring).
    estimated_cost_usd: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)

    __table_args__ = (
        # The one real query this table serves today (app/plans/usage.py's
        # own monthly-count reload, and the worker's own usage-alert
        # sweep): "this tenant's counted rows since the start of this
        # month". Composite, not two single-column indexes: both
        # predicates are always used together, never tenant_id alone or
        # created_at alone.
        Index("ix_usage_events_tenant_created", "tenant_id", "created_at"),
    )


class EmailLog(Base):
    __tablename__ = "email_log"

    # Task 4.3: docs/SPEC.md §18's own "send outcomes, retries and dedupe
    # for quota alerts". Exactly ONE row ever exists per (tenant_id,
    # threshold, month) -- the unique constraint below -- UPSERTED in
    # place as attempts progress, not a new row per attempt: the
    # worker's own usage-alert sweep (app/ingest/job_handlers.py's
    # handle_usage_alert_sweep()) checks for an existing row with
    # status="sent" before ever trying to send (dedup: already sent this
    # threshold this month, skip); a row with status="failed" is retried
    # on the next sweep tick, UPDATING that same row (status flipped,
    # `attempts` incremented) rather than accumulating a second row --
    # this IS the "retries" half of this table's own name, visible as
    # one row's own history, not reconstructed by scanning many rows.
    # docs/SPEC.md §14's own full 5-attempt exponential-backoff schedule
    # (~1min/5min/30min/2hr/12hr) is NOT implemented here -- a deliberate
    # scope decision, not an oversight: see PROJECT_SPEC.md's own new §9
    # marker (owner: whoever next revisits email retry timing) for the
    # simpler v1 actually shipped (retried on the sweep's own hourly
    # cadence until it succeeds, with no cap on attempts and no backoff
    # growth), and the reasoning for not building the full state machine
    # against an SMTP server this project has no real credentials for
    # yet.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # "80" or "100" -- a string, not an int: this is a label for WHICH
    # fixed threshold was crossed (docs/SPEC.md §9's own two named
    # points), never a measured percentage to do arithmetic on.
    threshold: Mapped[str] = mapped_column(String, nullable=False)
    # "YYYY-MM": matches app/plans/usage.py's own _month_key() format
    # exactly, so the two never drift into two different month-encoding
    # schemes for the same underlying concept (rule 11).
    month: Mapped[str] = mapped_column(String, nullable=False)
    # Closed vocabulary, same enforced-vocabulary treatment as tenants.
    # status/site_keys.status elsewhere in this project: "sent" (dedup
    # stops here), "failed" (retried next sweep tick), "skipped_not_
    # configured" (SMTP isn't configured at all, app/notifications/
    # service.py's own send_email() -- recorded, not silently dropped,
    # so the platform admin view can tell "nothing to send" apart from
    # "tried and failed").
    status: Mapped[str] = mapped_column(String, nullable=False)
    # No server_default: always set explicitly by the sweep's own
    # upsert (1 on first attempt, incremented on each retry) -- a bare
    # INSERT with no attempts value is a bug in the caller, not
    # something to paper over with a guessed default.
    attempts: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "threshold", "month", name="uq_email_log_tenant_threshold_month"
        ),
        CheckConstraint(
            "status IN ('sent', 'failed', 'skipped_not_configured')",
            name="ck_email_log_status",
        ),
        CheckConstraint("threshold IN ('80', '100')", name="ck_email_log_threshold"),
    )
