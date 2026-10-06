# backend/tests/test_revocation_preserves_indexed_content.py
# Task 2.5.f (Step 2.5's own close-out): the live re-proof of the
# "already-indexed content stays answerable" property (docs/SPEC.md §3/
# §5.5's own project constraint). Originally recorded at Task 2.2.g,
# reassigned from Step 2.4 to Step 2.5 at Task 2.4.g's own close-out --
# "indexing" means the Qdrant upsert, and Step 2.4 never touches Qdrant.
# Structurally proven since 2.2.g (revoke_domain() touches exactly
# verified_domains and audit_log, confirmed by reading the function) --
# this is the first time it can be proven at the DATA level, since real
# indexed content now exists at all (embed_and_upsert(), Task 2.5.e).
#
# Live against BOTH real services together -- test-db (the real tenant/
# verified_domains rows revoke_domain() operates on) AND test-qdrant (the
# real indexed points) -- the first test in this project needing both at
# once, since this is the first property that genuinely spans both
# stores. Skips cleanly if either is unavailable (reset_test_database's
# own fixture skip; live_qdrant_collection's own require_test_qdrant()
# skip, reached inside the test body).
import uuid

import sqlalchemy as sa

from app import qdrant
from app.ingest import embedding
from app.ingest.extract_html import extract_html
from app.ingest.models import VerifiedDomain
from app.ingest.qdrant_writer import embed_and_upsert
from app.ingest.repository import IngestRepository, revoke_domain
from app.tenancy.models import Tenant
from tests.conftest import db_session, live_qdrant_collection

# embed_dense() stubbed, matching 2.5.d/e's own established pattern and
# this step's own confirmed design (real OpenAI calls never in the
# committed suite). This is now the THIRD file with its own local copy of
# this exact stub (test_qdrant_writer.py has two variants) -- noted
# explicitly, strengthening the existing "candidate for a future
# duplication check" note from 2.5.e, not silently repeating it unflagged.


class _FakeEmbeddingItem:
    def __init__(self, vector: list[float]) -> None:
        self.embedding = vector


class _FakeEmbeddingResponse:
    def __init__(self, vectors: list[list[float]]) -> None:
        self.data = [_FakeEmbeddingItem(v) for v in vectors]


class _FakeEmbeddingsResource:
    async def create(self, *, model, input):  # noqa: A002 -- matches the real SDK's own param name
        return _FakeEmbeddingResponse([[0.0] * qdrant.DENSE_VECTOR_SIZE for _ in input])


class _FakeClient:
    def __init__(self) -> None:
        self.embeddings = _FakeEmbeddingsResource()


def _patch_embed_dense(monkeypatch) -> None:
    monkeypatch.setattr(embedding, "_get_client", lambda: _FakeClient())


async def test_revoking_a_domain_leaves_already_indexed_chunks_answerable(
    monkeypatch, reset_test_database
):
    _patch_embed_dense(monkeypatch)
    tenant_id = uuid.uuid4()
    async with db_session() as session:
        session.add(Tenant(id=tenant_id, name="Revocation Proof Tenant", status="active"))
        await session.commit()

    async with db_session() as session:
        repo = IngestRepository(tenant_id=tenant_id, session=session)
        domain = await repo.claim_domain("revoke-proof.example")
        await session.commit()
        domain_id = domain.id

    # "verified", not merely "pending" (point (a)'s own wording: "a
    # verified domain") -- a raw UPDATE bypassing the real DNS check,
    # matching the established precedent for this exact need
    # (test_ingest_repository.py's own identical raw-update setup): DNS
    # verification itself is not under test here.
    async with db_session() as session:
        await session.execute(
            sa.text(
                "UPDATE verified_domains SET status = 'verified', verified_at = now() "
                "WHERE id = :id"
            ),
            {"id": domain_id},
        )
        await session.commit()

    async with live_qdrant_collection("revocation_proof") as (client, name):
        await qdrant.ensure_collection(client, name)
        document_id = uuid.uuid4()
        content = extract_html(
            "<html><head><title>Revocation Proof</title></head><body>"
            "<h2>Section</h2>"
            "<p>real content that must survive domain revocation</p>"
            "</body></html>"
        )

        # (a) Real indexed content, through the real end-to-end primitive
        # (Task 2.5.e) -- not a synthetic point.
        await embed_and_upsert(
            content,
            client=client,
            collection_name=name,
            tenant_id=tenant_id,
            source_id=uuid.uuid4(),
            source_type="urls",
            document_id=document_id,
            source_url="https://revoke-proof.example/page",
            file_name=None,
            embedding_version="v1",
        )
        count_before = await client.count(collection_name=name)
        assert count_before.count == 1  # confirms real content was genuinely indexed first

        # (b) THE revocation itself -- the real revoke_domain() primitive
        # (Task 2.2.g), untouched here, called exactly as any real caller
        # would.
        async with db_session() as session:
            result = await revoke_domain(session, domain_id, actor="test-admin")
            await session.commit()
        assert result.status == "revoked"

        # Confirmed via Postgres directly, not just revoke_domain()'s own
        # return value: the domain really is revoked.
        async with db_session() as session:
            domain_row = (
                await session.execute(
                    sa.select(VerifiedDomain).where(VerifiedDomain.id == domain_id)
                )
            ).scalar_one()
        assert domain_row.status == "revoked"

        # (c) THE property under test: the already-indexed chunk remains
        # queryable/answerable via the real tenant_filter() mechanism,
        # exactly matching docs/SPEC.md §16's acceptance line -- revocation
        # stops FUTURE fetching only, never retroactively removes
        # already-indexed content. revoke_domain() never touched Qdrant at
        # all (structurally confirmed since 2.2.g); this proves the real,
        # practical consequence of that, not just the absence of code that
        # could have deleted it.
        records, _ = await client.scroll(
            collection_name=name, scroll_filter=qdrant.tenant_filter(tenant_id), limit=10
        )
        assert len(records) == 1
        assert records[0].payload["text"] == "real content that must survive domain revocation"
        assert records[0].payload["client_id"] == str(tenant_id)
        assert records[0].payload["doc_id"] == str(document_id)
