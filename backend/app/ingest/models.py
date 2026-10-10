# backend/app/ingest/models.py
# Task 2.1.a: ORM models for the ingestion pipeline's own tables
# (docs/SPEC.md §5.2) -- sources, documents, db_connections, jobs. Schema
# only; no repository/query logic here. The credential accessor for
# db_connections lives in app/ingest/repository.py, next to these models,
# matching app/tenancy/repository.py's own placement precedent (a
# repository module living beside its domain's models).
#
# verified_domains and audit_log (Task 2.2.a) were deferred here from 2.1.a
# -- 2.2 is independently security-reviewed, and those two tables are
# meaningless without their own verification/audit logic -- see
# PROJECT_SPEC.md's Step 2.1 decision entry for the original reasoning, and
# the Step 2.2 breakdown/2.2.a's own decision entry for what's built below.
#
# UUID primary keys throughout, tenant_id FKs with ondelete="CASCADE" and
# an explicit index=True -- the exact same precedent app/tenancy/models.py
# already established (gen_random_uuid() confirmed by hand to need zero
# extensions on the pinned postgres:18.2-trixie image; Postgres never
# auto-indexes a foreign key column).
import uuid
from datetime import datetime, timedelta

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import TIMESTAMP_NOW, UUID_PK, Base

# docs/SPEC.md §5.4's own four named refresh intervals -- a closed,
# spec-fixed vocabulary, matching `sources.type`'s own CheckConstraint
# precedent in spirit, but NOT enforced as a CheckConstraint (see
# Source.refresh_interval's own comment below for why).
#
# Task 2.9.b: relocated here from app/ingest/scheduler.py (its original
# home, Task 2.9.a) -- a real, necessary consequence, not a redesign:
# app/ingest/repository.py's new create_source()/update_source() need this
# exact same vocabulary for their own hard-reject validation, but
# scheduler.py already imports IngestRepository from repository.py (its
# own bootstrap-seeding call), so repository.py importing FROM scheduler.py
# in the other direction would be a circular import. models.py is the one
# place both already depend on (both import Source from here), and this
# vocabulary is arguably more at home next to the column it describes
# anyway. Values and behavior are completely unchanged -- scheduler.py
# now imports this constant from here instead of defining it.
REFRESH_INTERVAL_DELTAS: dict[str, timedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
    "quarterly": timedelta(days=90),
}


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # docs/SPEC.md §5.1's own four adapter types -- a closed, spec-fixed
    # vocabulary (which adapter handles this source), not a tenant-editable
    # open string. Matches site_keys.status's own CheckConstraint
    # precedent (a small, spec-named, closed set) rather than
    # tenants.status's plain-string treatment.
    type: Mapped[str] = mapped_column(String, nullable=False)
    # Unstructured JSONB, matching plans.limits/tenants.config's own
    # precedent: adapter-specific shape (a crawl source's config looks
    # nothing like a database source's), not yet needed as structured
    # columns (rule 11).
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # No CheckConstraint, unlike `type` above: REFRESH_INTERVAL_DELTAS (this
    # module, above) is already the one real definition of this vocabulary
    # (consumed directly by get_due_sources(), scheduler.py) -- a
    # CheckConstraint here would be a SECOND, independently-maintained copy
    # of the identical four strings (rule 11). Task 2.9.b's
    # create_source()/update_source() (app/ingest/repository.py) validate
    # against that same dict in Python instead, at creation/update time.
    refresh_interval: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default=text("true"))
    status: Mapped[str] = mapped_column(String, nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Task 2.9.b: the downgrade-flagging primitive's own persisted state
    # (flag_source_if_exceeds_plan(), app/ingest/repository.py) -- a plain
    # boolean, not a timestamp: nothing in this task's own scope needs WHEN
    # a source started/stopped exceeding its plan, only whether it
    # currently does (the smallest correct mechanism, rule 11). Mirrors
    # `enabled` above exactly in shape/style (server_default=false, the
    # inverse of enabled's true -- a brand-new source never exceeds its own
    # plan at creation time, since create_source() hard-rejects that case
    # instead of ever persisting it).
    exceeds_plan: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    __table_args__ = (
        CheckConstraint("type IN ('urls', 'crawl', 'upload', 'database')", name="ck_sources_type"),
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # A web source (urls/crawl) has a url; an upload has a file_name; a
    # database source (Task 2.8.d) has a row_identity -- never more than
    # one meaningfully for the same document, so all three stay nullable
    # rather than forcing a fake value into whichever two a given source
    # type doesn't use.
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # Task 2.8.d: resolves the long-open "whoever first designs real
    # identity/uniqueness for `database`-source documents" marker
    # (recorded at 2.4.a / the duplication check after it) -- see this
    # class's own `__table_args__` comment below for the full mechanism,
    # and app/ingest/row_templates.py's own derive_row_identity() for how
    # the real string is built (`f"{table}:{pk_value}"`).
    row_identity: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    # content_hash and etag exist for one reason: docs/SPEC.md §5.3's
    # idempotency design ("a re-run overwrites instead of duplicating")
    # depends on detecting whether a document's content actually changed
    # since the last run. etag is nullable -- it comes from the HTTP
    # response and not every source type provides one (an upload has
    # none at all); content_hash always applies once bytes exist, is
    # nearly free to compute (the fetch already downloaded them), and
    # works identically across every source type.
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    etag: Mapped[str | None] = mapped_column(String, nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)
    # pending (fetched, not yet extracted) / extracted (text/chunks exist) /
    # failed (extraction raised) -- a closed vocabulary matching sources.
    # type's/jobs.status's own CheckConstraint precedent.
    status: Mapped[str] = mapped_column(String, nullable=False)
    # Task 2.6.c: a separate, orthogonal signal from `status` -- a nearly-
    # empty page (2.4.b's own ExtractedContent.is_nearly_empty) is NOT an
    # extraction failure; extraction succeeded fine, there's just very
    # little content. Collapsing this into `status` would conflate two
    # different concepts (pipeline state vs. content quality) that this
    # column keeps separate (rule 11: the smallest correct mechanism, not
    # a new status value or a repurposed existing column). Mirrors
    # ExtractedContent.is_nearly_empty 1:1 -- this is only the persistence
    # half; the dashboard-flagging half ("may need JavaScript") is Phase
    # 6's own job, out of scope here (2.4.c's own ASSUMPTION marker for
    # "whoever builds the real crawler adapter").
    is_nearly_empty: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))

    # Task 2.4.a: url and file_name are mutually exclusive per row -- a web
    # source (urls/crawl) sets url, an upload sets file_name -- but NEITHER
    # is set for a `database` source's own documents (docs/SPEC.md §5.1's
    # fourth adapter type: a row/table extraction has no URL and no
    # filename at all). The class comment above only named two of the
    # three real cases; a document with both columns NULL is therefore a
    # genuinely valid state, not a gap to close here, so no "at least one
    # of url/file_name must be set" CheckConstraint is added. 2.4.a's own
    # investigation stopped there, deliberately: "a database-source
    # document has zero uniqueness protection today... correctly out of
    # scope for 2.4.a itself (no database adapter or row-identity design
    # exists yet)" -- recorded as its own Open marker, now resolved below.
    #
    # Task 2.8.d: row_identity is that third case's own real identity --
    # the database-source twin of url/file_name, same mutual-exclusivity
    # convention, same partial-unique-index pattern, added the moment a
    # real adapter (and a real identity scheme, derive_row_identity() in
    # app/ingest/row_templates.py) existed to define what should
    # distinguish two such rows, exactly as that Open marker's own
    # "likely a table/row-identifier column" prediction anticipated.
    # Confirmed live, not assumed, before this column existed: two
    # indistinguishable database-source rows (both url and file_name
    # NULL) under the same source_id both inserted successfully with no
    # error at all -- the real, reproduced gap this column and its own
    # index close.
    #
    # THREE separate partial unique indexes, not one combined index: the
    # mutual exclusivity means a (source_id, url) collision, a
    # (source_id, file_name) collision and a (source_id, row_identity)
    # collision are three independent uniqueness rules with no row ever
    # needing to be compared on more than one at once.
    # postgresql_where="... IS NOT NULL" is NOT functionally required to
    # stop two NULL rows from colliding -- confirmed live (Postgres never
    # considers two NULLs equal under a plain unique index, so unmatched
    # NULL rows already coexist with no guard at all) -- it is added
    # anyway purely for explicitness, matching verified_domains's own
    # partial-index precedent of stating the intended scope rather than
    # relying on an incidental side effect of NULL semantics.
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'extracted', 'failed')", name="ck_documents_status"
        ),
        Index(
            "ix_documents_source_id_url_unique",
            "source_id",
            "url",
            unique=True,
            postgresql_where=text("url IS NOT NULL"),
        ),
        Index(
            "ix_documents_source_id_file_name_unique",
            "source_id",
            "file_name",
            unique=True,
            postgresql_where=text("file_name IS NOT NULL"),
        ),
        Index(
            "ix_documents_source_id_row_identity_unique",
            "source_id",
            "row_identity",
            unique=True,
            postgresql_where=text("row_identity IS NOT NULL"),
        ),
    )


class DbConnection(Base):
    __tablename__ = "db_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    host: Mapped[str] = mapped_column(String, nullable=False)
    # Not the sensitive part -- which tables/columns a tenant has
    # allowlisted, and the row-to-text template (docs/SPEC.md §5.6).
    # Shape decided at Task 2.8.c, not here: see app/ingest/row_templates.py's
    # own header comment for the canonical reference (both columns' real
    # JSONB shape, and why) -- matching create_db_connection()'s own
    # docstring precedent for `encrypted_credentials` below, point future
    # readers at one place, don't restate it a third time here.
    allowlisted_tables: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    row_templates: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    # The credential itself: pgcrypto-encrypted opaque bytes (see
    # PROJECT_SPEC.md's Step 2.1.a decision entry for the live comparison
    # against application-level encryption, and what was actually found
    # before choosing pgcrypto). A structured, dedicated column (BYTEA),
    # never JSONB: unlike JSONB, BYTEA cannot be partially queried or
    # introspected in SQL (no `credentials->>'password'`-style bypass of
    # app.ingest.repository's own guarded accessor) -- the ciphertext is
    # meaningless without going through get_decrypted_credentials(). The
    # credential's own internal shape (username/password/etc.) is
    # deliberately NOT fixed at the schema level here -- it is encoded
    # inside the encrypted value itself, decided once the real adapter
    # (Step 2.8) knows exactly what it needs to connect with, not invented
    # speculatively in this schema-only task.
    encrypted_credentials: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable, a deliberate decision, not an afterthought: reconcile and
    # refresh-scheduling jobs are not always tied to one source row (the
    # nightly reconcile, docs/SPEC.md §5.3, compares an entire tenant
    # against Qdrant, not one source at a time).
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    job_type: Mapped[str] = mapped_column(String, nullable=False)
    # Closed, fail-closed vocabulary (matching site_keys.status's own
    # CheckConstraint precedent): pending (eligible to be claimed once
    # next_run_at is reached) / running (claimed by a worker, in
    # progress) / succeeded (done) / failed (exhausted max_attempts --
    # terminal). A job that fails but still has attempts left goes back
    # to "pending" with next_run_at pushed forward by the backoff delay,
    # rather than a fifth "retrying" state: functionally identical to
    # "pending" from the claiming query's own perspective (2.1.c), so a
    # separate state would be purely cosmetic with no behavioral
    # difference (rule 11). No "cancelled" state either: deleting or
    # disabling a source cascades (ON DELETE CASCADE above) and removes
    # its own pending jobs outright, rather than needing a status to
    # represent that case.
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("5"))
    # Indexed: 2.1.c's claiming query filters/orders on this (the rows
    # eligible to claim right now, oldest first).
    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True, **TIMESTAMP_NOW
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)
    # No DB-level onupdate: matching this project's existing precedent
    # (e.g. visitors.last_seen_at), state-transition code sets this
    # explicitly on each write (2.1.c's own job), not a trigger.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed')", name="ck_jobs_status"
        ),
    )


class VerifiedDomain(Base):
    __tablename__ = "verified_domains"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Normalization (lowercase, no scheme, no port, no path) is
    # deliberately NOT enforced here. site_keys.allowed_origins needs the
    # exact same treatment (PROJECT_SPEC.md's existing Step-6-owned open
    # marker) -- inventing a second, independently-decided rule for this
    # column instead of extending that one marker would risk the two
    # drifting apart over two different normalization behaviors for what
    # is structurally the same problem (a tenant-typed hostname/origin).
    domain: Mapped[str] = mapped_column(String, nullable=False)
    # Task 2.3.e: `file`/`meta_tag` restored alongside `dns` -- the schema
    # side of the deferral PROJECT_SPEC.md's Step 2.2 breakdown (decision
    # (b)) recorded: both need an outbound HTTP fetch to a tenant-supplied
    # host, carrying the identical SSRF surface Step 2.3's own guard
    # exists to close, so this allow-list stayed DNS-only until that guard
    # existed to reuse. Schema only: the actual file/meta-tag verification
    # LOGIC (the fetch itself, well-known-path/meta-tag parsing) is NOT
    # built by this change -- see PROJECT_SPEC.md's Open markers for that
    # still-unowned future work.
    method: Mapped[str] = mapped_column(String, nullable=False)
    # pending (claimed, token generated, not yet checked) / verified
    # (check succeeded) / revoked (platform admin action, 2.2.g) -- a
    # closed, fail-closed vocabulary matching sources.type's/jobs.status's
    # own CheckConstraint precedent.
    status: Mapped[str] = mapped_column(String, nullable=False)
    # Generated fresh per claim attempt (2.2.c's own job), never reused
    # across attempts or tenants -- the proof a successful DNS/file/
    # meta-tag check must find, so one tenant's own old proof can never be
    # replayed to verify a domain for a different tenant or a later,
    # unrelated attempt.
    verification_token: Mapped[str] = mapped_column(String, nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)

    __table_args__ = (
        CheckConstraint(
            "method IN ('dns', 'file', 'meta_tag')", name="ck_verified_domains_method"
        ),
        CheckConstraint(
            "status IN ('pending', 'verified', 'revoked')", name="ck_verified_domains_status"
        ),
        # Global uniqueness (PROJECT_SPEC.md's Step 2.2 breakdown, decision
        # (e)): a domain can be actively claimed -- pending or verified --
        # by exactly one tenant at a time, across ALL tenants, not just
        # within one tenant's own rows (deliberately on `domain` alone,
        # never `(tenant_id, domain)`). PARTIAL, not a plain unique
        # constraint: a revoked domain must become claimable again, by any
        # tenant, and a full unconditional unique constraint could never
        # allow that without deleting the old row first -- which would
        # also destroy the audit trail of who held it before. The live
        # proof that Postgres itself actually enforces this is 2.2.b's
        # job, against a real migration; this is schema declaration only.
        Index(
            "ix_verified_domains_domain_active_unique",
            "domain",
            unique=True,
            postgresql_where=text("status != 'revoked'"),
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    # Deliberately NOT tenant-scoped (no tenant_id column at all):
    # confirmed, not defaulted. An audit entry records an ADMIN's own
    # action (e.g. revoking tenant A's domain) -- the admin is not acting
    # within tenant A's own scope the way TenantScopedRepository exists to
    # enforce, and docs/SPEC.md §15 already names future actions (threshold
    # overrides, plan changes) that don't belong to any single tenant
    # either. target_id below still names which row an action affected,
    # when one exists, without this table itself being tenant-scoped.
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), **UUID_PK)
    # Plain string, deliberately not a FK: no platform_admin role, or any
    # other real admin-identity table, exists until Step 6.1
    # (PROJECT_SPEC.md's Step 2.2 breakdown, decision (a)) -- a FK here
    # would reference something that does not exist yet.
    actor: Mapped[str] = mapped_column(String, nullable=False)
    # No CheckConstraint: future actions (threshold overrides, plan
    # changes, docs/SPEC.md §15) are not enumerable yet -- an open string,
    # not a closed vocabulary invented speculatively ahead of those
    # features actually existing (rule 11).
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_type: Mapped[str] = mapped_column(String, nullable=False)
    # Nullable: not every audited action necessarily targets one specific
    # row. No FK: target_type varies per action (a domain today, a tenant
    # or plan later), so a single FK to one table would not fit every
    # case -- the same reasoning this project already applies everywhere
    # else it deliberately omits a FK rather than forcing a wrong one.
    target_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), **TIMESTAMP_NOW)

    # IMPORTANT: this ORM model does not, and structurally cannot, enforce
    # append-only writes. A privilege grant (`REVOKE UPDATE, DELETE ON
    # audit_log FROM <app role>`) is not a schema object SQLAlchemy's
    # declarative mapping can express -- that REVOKE is Step 2.2.b's own
    # job, applied in the real migration. Until 2.2.b lands, nothing below
    # this comment stops application code from calling
    # `session.execute(update(AuditLog)...)` and succeeding; do not
    # mistake this model's own absence of update/delete helper methods for
    # real enforcement. 2.2.h's AST-based guard is a secondary,
    # defense-in-depth layer on top of that database-level REVOKE, not the
    # primary mechanism (PROJECT_SPEC.md's Step 2.2 breakdown, decision (a)).
