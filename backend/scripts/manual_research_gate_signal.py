#!/usr/bin/env python3
# backend/scripts/manual_research_gate_signal.py
# Task 3.4.d RESEARCH (no gate code built by this script -- it only
# gathers evidence for the report): a MANUAL, OPT-IN script making REAL
# OpenAI dense-embedding calls (cents of cost -- ~60 embeddings of
# text-embedding-3-small) against a REAL, DISPOSABLE test-qdrant
# instance -- NEVER run by pytest/CI (not under tests/, no test_ prefix,
# not imported by anything). Run it by hand:
#
#   cd backend
#   export TEST_QDRANT_URL=http://127.0.0.1:<port>       # from `make test-qdrant`
#   export TEST_QDRANT_API_KEY=...
#   export TEST_QDRANT_READ_ONLY_API_KEY=...
#   export OPENAI_API_KEY=sk-...                          # a REAL key
#   python scripts/manual_research_gate_signal.py
#
# Sparse/BM25 embedding (fastembed) is fully LOCAL -- no network call, no
# cost. Only the dense side touches a real paid API: ~32 corpus chunks +
# 30 queries = 62 embedding calls (batched into far fewer HTTP requests
# by embed_dense()'s own one-call-per-list design), a small fraction of
# a cent at text-embedding-3-small's published pricing.
#
# SAFETY: this script reads TEST_QDRANT_* env vars (the SAME names
# tests/conftest.py's own require_test_qdrant() uses) and points
# os.environ's plain QDRANT_URL/QDRANT_API_KEY/QDRANT_READ_ONLY_API_KEY
# at them BEFORE importing anything from `app`, so every real production
# module (Settings, retrieve(), get_qdrant_client()) transparently talks
# to the disposable test instance -- production Qdrant is never touched.
# A narrow host allow-list (localhost/127.0.0.1 only) mirrors
# require_test_qdrant()'s own safety check, inlined here rather than
# importing a test-only fixture into a script (one-directional
# dependency, matching this project's own layering).
#
# WHAT THIS SCRIPT REUSES FROM REAL PRODUCTION CODE, UNMODIFIED (per
# manual_check_chat_model_streaming.py's own established principle --
# prove against the REAL code path, not a separately maintained copy
# that could silently drift): `retrieve()` itself (the real hybrid
# RRF-fused query, `app/retrieval/service.py`), `embed_dense()`/
# `embed_sparse_query()` (`app/ingest/embedding.py`), `_retrieval_filter()`
# (the real tenant+HIDDEN_FIELD filter), `build_payload()`/`build_point()`/
# `build_point_id()`/`upsert_points()` (`app/ingest/qdrant_writer.py`),
# and `ensure_collection()` (`app/qdrant.py`). The ONLY new code here is
# the corpus/query data and two EXTRA, non-fused diagnostic queries
# (dense-only, sparse-only) that no production code path needs -- these
# exist solely to recover the two sub-scores a fused QueryResponse does
# not expose (confirmed live, by reading qdrant-client 1.19.1's own
# ScoredPoint/QueryResponse models: exactly one `score` field, no
# pre-fusion score anywhere).
import asyncio
import os
import statistics
import uuid
from urllib.parse import urlsplit

_ALLOWED_TEST_HOSTS = frozenset({"localhost", "127.0.0.1"})


def _point_test_env_at_env_vars() -> None:
    required = ("TEST_QDRANT_URL", "TEST_QDRANT_API_KEY", "TEST_QDRANT_READ_ONLY_API_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            f"Missing required env var(s): {', '.join(missing)}. Start test-qdrant first "
            "(`make test-qdrant`) and export its URL/keys -- see this script's own header."
        )
    url = os.environ["TEST_QDRANT_URL"]
    host = urlsplit(url).hostname
    if host not in _ALLOWED_TEST_HOSTS:
        raise SystemExit(
            f"TEST_QDRANT_URL host {host!r} is not in the allowed test-host list "
            f"{sorted(_ALLOWED_TEST_HOSTS)} -- refusing to run against a non-local Qdrant "
            "instance (this script seeds and queries real data, including real OpenAI "
            "embeddings, and must never point at anything but a disposable local instance)."
        )
    os.environ["QDRANT_URL"] = url
    os.environ["QDRANT_API_KEY"] = os.environ["TEST_QDRANT_API_KEY"]
    os.environ["QDRANT_READ_ONLY_API_KEY"] = os.environ["TEST_QDRANT_READ_ONLY_API_KEY"]


_point_test_env_at_env_vars()  # MUST run before any `app.*` import below

from qdrant_client.http.models import Filter, SparseVector  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.ingest.embedding import embed_dense, embed_sparse, embed_sparse_query  # noqa: E402
from app.ingest.qdrant_writer import (  # noqa: E402
    build_payload,
    build_point,
    build_point_id,
    upsert_points,
)
from app.qdrant import (  # noqa: E402
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    build_qdrant_client,
    ensure_collection,
    get_qdrant_read_client,
)
from app.retrieval.service import _retrieval_filter, retrieve  # noqa: E402

TENANT_ID = uuid.uuid4()
SOURCE_ID = uuid.uuid4()
PROBE_COLLECTION = f"gate_research_{uuid.uuid4().hex[:8]}"
EMBEDDING_VERSION = "gate-research-v1"

# --- Corpus: a small business's knowledge base, 4 clearly distinct ----------
# --- topics, 8 chunks each (32 total). -------------------------------------
CORPUS: list[tuple[str, str]] = [
    ("Pricing", "Acme Gadgets offers three subscription tiers: Basic at $9.99/month, "
     "Pro at $24.99/month, and Enterprise at $79.99/month, each billed monthly with "
     "no long-term contract."),
    ("Pricing", "The Basic plan includes access to our core gadget catalog, standard "
     "shipping rates, and email support with a 48-hour response time."),
    ("Pricing", "The Pro plan adds priority shipping discounts of 15%, live chat "
     "support, and early access to new product launches two weeks before the "
     "general public."),
    ("Pricing", "The Enterprise plan is designed for businesses ordering in bulk, "
     "and includes a dedicated account manager, net-30 invoicing, and volume "
     "discounts starting at 10% for orders over 100 units."),
    ("Pricing", "Annual billing is available on the Pro and Enterprise plans only, "
     "and saves customers two months' worth of fees compared to paying monthly."),
    ("Pricing", "All listed prices are in US dollars and exclude any applicable "
     "state or local sales tax, which is calculated at checkout based on the "
     "shipping address."),
    ("Pricing", "Customers can upgrade or downgrade their subscription tier at any "
     "time from the account settings page, with changes taking effect at the start "
     "of the next billing cycle."),
    ("Pricing", "We occasionally run seasonal promotions offering a percentage off "
     "the first three months of a new Pro or Enterprise subscription; these "
     "promotions are announced via our email newsletter."),
    ("Returns Policy", "Acme Gadgets accepts returns within 30 days of the delivery "
     "date for a full refund, provided the item is unused and in its original "
     "packaging."),
    ("Returns Policy", "To start a return, log into your account, go to Order "
     "History, select the item, and click 'Request Return' to generate a prepaid "
     "return shipping label."),
    ("Returns Policy", "Refunds are issued to the original payment method within 5 "
     "to 7 business days after we receive and inspect the returned item at our "
     "warehouse."),
    ("Returns Policy", "Items marked as 'Final Sale' at the time of purchase, "
     "including clearance gadgets and custom-engraved products, cannot be returned "
     "or exchanged."),
    ("Returns Policy", "If you receive a damaged or defective item, contact support "
     "within 7 days of delivery with photos of the damage, and we will send a "
     "replacement at no additional cost."),
    ("Returns Policy", "Exchanges for a different size or color are handled as a "
     "return followed by a new order, rather than a direct swap, to keep inventory "
     "accurate."),
    ("Returns Policy", "Return shipping within the continental United States is "
     "free using our prepaid label; customers are responsible for return shipping "
     "costs on international orders."),
    ("Returns Policy", "Gift returns without a receipt are issued as store credit "
     "at the item's current listed price, rather than a refund to a payment "
     "method."),
    ("Shipping", "Standard shipping within the continental United States takes 5 "
     "to 7 business days and costs a flat $4.99, or is free on orders over $50."),
    ("Shipping", "Expedited shipping is available at checkout for an additional "
     "$14.99 and delivers within 2 business days for orders placed before 2pm "
     "Eastern Time."),
    ("Shipping", "Acme Gadgets ships to over 30 countries internationally, with "
     "delivery times ranging from 10 to 21 business days depending on destination "
     "and customs processing."),
    ("Shipping", "International orders may be subject to customs duties and import "
     "taxes, which are the responsibility of the customer and are not included in "
     "the item price or shipping cost."),
    ("Shipping", "Once an order ships, customers receive a tracking number by "
     "email, and tracking updates can also be viewed in the Order History section "
     "of the account page."),
    ("Shipping", "We currently do not ship to P.O. boxes for orders containing "
     "items over 5 pounds, due to carrier size restrictions."),
    ("Shipping", "Orders placed on weekends or public holidays begin processing on "
     "the next business day, which may add 1 to 2 days to the stated delivery "
     "estimate."),
    ("Shipping", "Acme Gadgets does not currently offer same-day delivery or local "
     "courier pickup in any market."),
    ("Hours & Support", "Our customer support team is available by live chat and "
     "phone Monday through Friday, 8am to 6pm Eastern Time, excluding major US "
     "public holidays."),
    ("Hours & Support", "Email support is monitored seven days a week, with a "
     "typical response time of 24 to 48 hours depending on ticket volume."),
    ("Hours & Support", "Our physical flagship store in Austin, Texas is open "
     "Monday through Saturday from 10am to 7pm, and Sunday from 12pm to 5pm."),
    ("Hours & Support", "Phone support can be reached at our toll-free number, "
     "which is listed on the Contact Us page along with current estimated hold "
     "times."),
    ("Hours & Support", "During major holidays such as Thanksgiving and Christmas "
     "Day, both phone and live chat support are closed, and only email tickets are "
     "accepted."),
    ("Hours & Support", "Enterprise plan customers have access to an extended "
     "support window, Monday through Friday from 6am to 9pm Eastern Time, through "
     "their dedicated account manager."),
    ("Hours & Support", "Our support team does not provide live chat on weekends, "
     "but the help center article library is available 24 hours a day for "
     "self-service troubleshooting."),
    ("Hours & Support", "Response time commitments (48 hours for Basic, live chat "
     "for Pro, dedicated manager for Enterprise) are outlined in full in each "
     "plan's own terms of service."),
]

QUERY_GROUPS: dict[str, list[str]] = {
    "A: clearly answerable": [
        "How much does the Pro plan cost per month?",
        "What is your return window for a full refund?",
        "How long does standard shipping take within the US?",
        "What are your customer support hours on weekdays?",
        "Can I get free shipping, and what's the order threshold?",
        "Is annual billing available, and which plans support it?",
        "What happens if I receive a damaged item?",
        "Do you ship internationally, and how long does it take?",
        "What are your flagship store's opening hours?",
        "Are prices in USD, and do they include sales tax?",
    ],
    "B: related but unanswerable": [
        "Do you offer a military or veteran discount on any subscription plan?",
        "Can I return a product after 45 days if I have a good reason?",
        "Do you offer same-day delivery in New York City?",
        "Is there a student discount on the Basic plan?",
        "Can I pay for my subscription using cryptocurrency like Bitcoin?",
        "What is your policy on price matching a competitor's lower price?",
        "Do you offer gift wrapping for physical products?",
        "Can I pause my subscription for a few months instead of cancelling it?",
        "Is there a referral program that gives existing customers a discount?",
        "Do you offer carbon-neutral or offset shipping options?",
    ],
    "C: off-topic / nonsense": [
        "What is the capital city of Mongolia?",
        "How do I bake a traditional sourdough bread loaf at home?",
        "Can you explain how quantum entanglement works in simple terms?",
        "asdkjfh aslkdjf qwerty zzzxxxyyy nonsense input",
        "What is the airspeed velocity of an unladen swallow?",
        "Who won the FIFA World Cup in 1998?",
        "What's a good recipe for a vegetarian lasagna?",
        "How do photosynthesis and cellular respiration relate to each other?",
        "asdf123 !!!   ???   random gibberish text here",
        "What's the best way to train for a marathon in three months?",
    ],
}


def _topic_slug(topic: str) -> str:
    return topic.lower().replace(" & ", "-").replace(" ", "-")


async def _seed_corpus(write_client) -> None:
    await ensure_collection(write_client, name=PROBE_COLLECTION)
    texts = [text for _, text in CORPUS]
    dense_vectors = await embed_dense(texts)
    sparse_vectors = await embed_sparse(texts)
    points = [
        build_point(
            build_point_id(SOURCE_ID, i, EMBEDDING_VERSION),
            build_payload(
                tenant_id=TENANT_ID,
                source_id=SOURCE_ID,
                source_type="url",
                document_id=SOURCE_ID,
                source_url=f"https://acme-gadgets.example/{_topic_slug(topic)}",
                file_name=None,
                title=topic,
                heading_path=(topic,),
                chunk_index=i,
                content_hash=f"hash-{i}",
                embedding_model="text-embedding-3-small",
                embedding_version=EMBEDDING_VERSION,
                text=text,
            ),
            dense_vectors[i],
            sparse_vectors[i],
        )
        for i, (topic, text) in enumerate(CORPUS)
    ]
    await upsert_points(write_client, PROBE_COLLECTION, points)
    print(f"Seeded {len(points)} chunks into {PROBE_COLLECTION!r} for tenant {TENANT_ID}.\n")


async def _dense_only_score(client, dense_vector: list[float], target_id, query_filter: Filter):
    settings = get_settings()
    result = await client.query_points(
        collection_name=PROBE_COLLECTION,
        query=dense_vector,
        using=DENSE_VECTOR_NAME,
        query_filter=query_filter,
        limit=settings.retrieval_prefetch_limit,
    )
    for point in result.points:
        if point.id == target_id:
            return point.score
    return None


async def _sparse_only_score(client, sparse_vector, target_id, query_filter: Filter):
    settings = get_settings()
    result = await client.query_points(
        collection_name=PROBE_COLLECTION,
        query=SparseVector(indices=sparse_vector.indices, values=sparse_vector.values),
        using=SPARSE_VECTOR_NAME,
        query_filter=query_filter,
        limit=settings.retrieval_prefetch_limit,
    )
    for point in result.points:
        if point.id == target_id:
            return point.score
    return None


async def _run_one_query(client, query_text: str) -> dict:
    points = await retrieve(client_id=TENANT_ID, query=query_text, collection_name=PROBE_COLLECTION)
    if not points:
        return {"query": query_text, "no_hits": True}

    top = points[0]
    dense_vector = (await embed_dense([query_text]))[0]
    sparse_vector = (await embed_sparse_query([query_text]))[0]
    query_filter = _retrieval_filter(TENANT_ID)

    dense_score = await _dense_only_score(client, dense_vector, top.id, query_filter)
    sparse_score = await _sparse_only_score(client, sparse_vector, top.id, query_filter)

    return {
        "query": query_text,
        "no_hits": False,
        "top_topic": (top.payload or {}).get("title"),
        "top_text": (top.payload or {}).get("text", "")[:70],
        "rrf_score": top.score,
        "dense_score": dense_score,
        "sparse_score": sparse_score,
    }


def _fmt(value) -> str:
    return "—" if value is None else f"{value:.4f}"


def _print_group_table(group_name: str, rows: list[dict]) -> None:
    print(f"\n=== {group_name} ===")
    print(f"{'query':<55} {'topic':<18} {'rrf':>8} {'dense':>8} {'sparse':>8}")
    for row in rows:
        if row.get("no_hits"):
            print(f"{row['query'][:54]:<55} {'NO HITS':<18}")
            continue
        print(
            f"{row['query'][:54]:<55} {str(row['top_topic'])[:17]:<18} "
            f"{_fmt(row['rrf_score']):>8} {_fmt(row['dense_score']):>8} "
            f"{_fmt(row['sparse_score']):>8}"
        )

    for signal in ("rrf_score", "dense_score", "sparse_score"):
        values = [r[signal] for r in rows if not r.get("no_hits") and r.get(signal) is not None]
        if not values:
            print(f"  {signal}: no values")
            continue
        print(
            f"  {signal}: min={min(values):.4f} max={max(values):.4f} "
            f"mean={statistics.mean(values):.4f} median={statistics.median(values):.4f}"
        )


async def main() -> None:
    read_only_client = get_qdrant_read_client()
    write_client = build_qdrant_client(
        os.environ["QDRANT_URL"], os.environ["QDRANT_API_KEY"]
    )
    try:
        await _seed_corpus(write_client)

        all_rows: dict[str, list[dict]] = {}
        for group_name, queries in QUERY_GROUPS.items():
            rows = []
            for q in queries:
                rows.append(await _run_one_query(read_only_client, q))
            all_rows[group_name] = rows
            _print_group_table(group_name, rows)

        print("\n=== Raw rows (for copy/paste into the report) ===")
        for group_name, rows in all_rows.items():
            for row in rows:
                print(group_name, row)
    finally:
        if await write_client.collection_exists(PROBE_COLLECTION):
            await write_client.delete_collection(PROBE_COLLECTION)
        await write_client.close()
        await read_only_client.close()
        print(f"\nCleaned up: deleted {PROBE_COLLECTION!r}.")


if __name__ == "__main__":
    asyncio.run(main())
