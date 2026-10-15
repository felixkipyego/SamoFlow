# backend/app/handoff/models.py
# Step 4.4: the `escalations` table (docs/SPEC.md §7: "id, tenant_id,
# conversation_id, vid, reason, optional contact name/email/phone,
# transcript snapshot, status new/seen/resolved, created_at").
import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import TIMESTAMP_NOW, UUID_PK, Base


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    # No ondelete -- matches app/tenancy/models.py's own Conversation.vid
    # (no visitor-deletion mechanism exists anywhere in this codebase
    # today; tenant deletion already cascades via tenant_id above).
    vid: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("visitors.vid"), nullable=False
    )
    # ondelete="SET NULL", nullable: docs/SPEC.md §7 gives escalations
    # their OWN, LONGER retention window (default 12 months, up to 24)
    # than chat retention (default 30 days, up to 90) -- a plain
    # cascading or restrictive FK here would be WRONG either way: CASCADE
    # would silently cut every escalation's own lifetime down to however
    # long its conversation survives (almost always shorter, defeating
    # the whole point of a separate, longer setting); the Postgres
    # default (NO ACTION/RESTRICT) would make the nightly chat-retention
    # sweep's own conversation DELETE fail outright the moment ANY
    # escalation still referenced it. SET NULL is correct: the
    # conversation link is best-effort/for-reference only -- the
    # `transcript` column below is a SELF-CONTAINED snapshot taken at
    # submission time, so losing the live FK on a later conversation
    # deletion loses nothing this table itself needs to keep working.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.cid", ondelete="SET NULL"), nullable=True
    )
    # Closed vocabulary -- see this module's own header. V1 simplification,
    # stated plainly, not silently decided: every escalation today is
    # visitor-button-triggered (docs/SPEC.md §7: "The AI cannot submit
    # anything... the visitor's browser submits POST .../escalate"), so
    # there is exactly one real value in practice. Not visitor-supplied
    # freeform text -- that would be a new, unnecessary injection
    # surface on a column with no current reader beyond a future
    # dashboard display (see PROJECT_SPEC.md's own §9 marker, owner
    # Step 6.4, for a possible richer breakdown once that screen exists).
    reason: Mapped[str] = mapped_column(String, nullable=False)
    contact_name: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_email: Mapped[str | None] = mapped_column(String, nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String, nullable=True)
    # Text, not String: a full multi-turn transcript. Stored EXACTLY as
    # persisted in the LangGraph checkpoint at submission time (assistant
    # lines keep `_sanitize_answer_text()`'s own backslash-escaped
    # markdown form; visitor lines are raw) -- a faithful, untransformed
    # snapshot. docs/SPEC.md §7: "Transcripts are stored as text and
    # always escaped on output" -- escaping is therefore an OUTPUT-TIME
    # concern for whichever medium renders this column (this task's own
    # email body; Step 6.4's future dashboard screen), never baked in at
    # write time, so no renderer is stuck with an earlier renderer's own
    # escaping choice. See app/text_escaping.py's own markdown_unescape()
    # for the shared plain-text half of that output-time pipeline.
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="new")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)

    __table_args__ = (
        CheckConstraint("status IN ('new', 'seen', 'resolved')", name="ck_escalations_status"),
        # The nightly escalation-retention sweep's own query ("this
        # tenant's rows older than its own retention cutoff"), the
        # identical shape/reasoning as UsageEvent's own
        # ix_usage_events_tenant_created (app/plans/models.py).
        Index("ix_escalations_tenant_created", "tenant_id", "created_at"),
        # "One open escalation per conversation" (docs/SPEC.md §7) --
        # the real check this index serves (app/handoff/routes.py's own
        # submit_escalation()): "does an escalation with status != '
        # resolved' already exist for this conversation_id". Composite
        # over (conversation_id, status) rather than conversation_id
        # alone: the real query always filters on both.
        Index("ix_escalations_conversation_status", "conversation_id", "status"),
    )
