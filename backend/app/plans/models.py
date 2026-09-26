# backend/app/plans/models.py
# Task 1.2.b: ORM model for plans (docs/SPEC.md §5.4). Minimal on purpose --
# a placeholder table until Phase 2/4 features define real limit fields
# (PROJECT_SPEC.md decision: limits stays an unstructured JSONB blob for
# now). No repository/query logic here (that's 1.2.d).
import uuid

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Plan(Base):
    __tablename__ = "plans"

    # UUID PK (not a fixed string/enum id): docs/SPEC.md never names a fixed
    # set of plan identifiers, and a UUID keeps this table consistent with
    # every other table's PK style rather than introducing a second PK
    # scheme for one table (rule 11).
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    limits: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
