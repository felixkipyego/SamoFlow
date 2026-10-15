# backend/tests/test_telemetry_service.py
# Step 4.5 [SECURITY, per Felix's own direct instruction even though
# this step is untagged in docs/SPEC.md §18's own Phase 4 list]: real
# tests for app/telemetry/service.py -- the per-tenant trace-text
# switch (docs/SPEC.md §13), the retention auto-switch-off rule, and
# the "traces must never contain API keys, auth headers or the
# pgcrypto key" requirement, each proven against REAL OTel SDK/
# instrumentor behavior (an InMemorySpanExporter, a real FastAPI app,
# a real request with a real secret header) -- never reasoned about
# from reading the library docs alone.
import asyncio
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import context
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.config import Settings, get_settings
from app.telemetry.service import (
    _TraceTextRedactionProcessor,
    _TraceTextTaggingProcessor,
    effective_trace_text_enabled,
    init_tracing,
    instrument_fastapi_app,
    is_phoenix_configured,
    record_trace,
    shutdown_tracing,
    tag_current_span_with_identity,
    trace_text_context,
)
from tests.conftest import VALID_ENV, set_valid_env

pytestmark = pytest.mark.usefixtures("close_any_checkpointer_pool_left_open")


# --- is_phoenix_configured() -------------------------------------------


def test_is_phoenix_configured_false_when_endpoint_is_unset(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()
    assert is_phoenix_configured(Settings()) is False


def test_is_phoenix_configured_true_when_endpoint_is_set(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV, PHOENIX_COLLECTOR_ENDPOINT="http://phoenix:6006/v1/traces")
    get_settings.cache_clear()
    assert is_phoenix_configured(Settings()) is True


# --- effective_trace_text_enabled() -- the retention + switch rule -----


def test_trace_text_enabled_by_default_at_the_30_day_default_retention():
    assert effective_trace_text_enabled({}, retention_days=30) is True


def test_trace_text_auto_disabled_when_retention_is_strictly_under_30_days():
    # docs/SPEC.md §13: "Tenants whose chat retention is strictly less
    # than 30 days (1 to 29) get trace text switched off automatically".
    assert effective_trace_text_enabled({}, retention_days=29) is False
    assert effective_trace_text_enabled({}, retention_days=1) is False


def test_trace_text_stays_on_at_exactly_30_days_retention():
    # docs/SPEC.md §13: "at exactly 30 days it stays on, because traces
    # and chats expire together."
    assert effective_trace_text_enabled({}, retention_days=30) is True


def test_trace_text_stays_on_above_30_days_retention():
    assert effective_trace_text_enabled({}, retention_days=90) is True


def test_explicit_tenant_switch_off_wins_even_at_long_retention():
    assert effective_trace_text_enabled({"trace_text_enabled": False}, retention_days=90) is False


def test_explicit_tenant_switch_on_does_not_override_the_retention_rule():
    # The retention rule is a HARD auto-off, not merely a default the
    # tenant's own explicit "on" can override -- docs/SPEC.md §13 names
    # this as automatic, with no stated tenant override for it.
    assert effective_trace_text_enabled({"trace_text_enabled": True}, retention_days=5) is False


# --- the real tagging + redaction span-processor pipeline ---------------


def _build_test_provider():
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(_TraceTextTaggingProcessor())
    tp.add_span_processor(_TraceTextRedactionProcessor())
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    return tp, exporter


def test_tagging_processor_propagates_the_per_request_verdict_to_child_spans():
    tp, exporter = _build_test_provider()
    tracer = tp.get_tracer("test")

    token = context.attach(trace_text_context(False))
    try:
        with tracer.start_as_current_span("outer"):
            with tracer.start_as_current_span("inner") as inner:
                inner.set_attribute("input.value", "secret visitor text")
    finally:
        context.detach(token)

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert spans["outer"].attributes["trace_text_enabled"] == "False"
    assert spans["inner"].attributes["trace_text_enabled"] == "False"


def test_redaction_processor_redacts_text_attributes_when_switched_off():
    tp, exporter = _build_test_provider()
    tracer = tp.get_tracer("test")

    token = context.attach(trace_text_context(False))
    try:
        with tracer.start_as_current_span("llm_call") as span:
            span.set_attribute("input.value", "the visitor's real question")
            span.set_attribute("llm.output_messages.0.message.content", "the real answer")
            span.set_attribute("retrieval.documents.0.document.content", "a real chunk")
            span.set_attribute("keep.me", "not text, never redacted")
    finally:
        context.detach(token)

    (result,) = exporter.get_finished_spans()
    assert result.attributes["input.value"] == "[REDACTED: trace text disabled for this tenant]"
    assert result.attributes["llm.output_messages.0.message.content"].startswith("[REDACTED")
    assert result.attributes["retrieval.documents.0.document.content"].startswith("[REDACTED")
    assert result.attributes["keep.me"] == "not text, never redacted"  # untouched


def test_redaction_processor_leaves_text_alone_when_switched_on():
    tp, exporter = _build_test_provider()
    tracer = tp.get_tracer("test")

    token = context.attach(trace_text_context(True))
    try:
        with tracer.start_as_current_span("llm_call") as span:
            span.set_attribute("input.value", "the visitor's real question")
    finally:
        context.detach(token)

    (result,) = exporter.get_finished_spans()
    assert result.attributes["input.value"] == "the visitor's real question"


def test_redaction_processor_defaults_to_enabled_when_no_baggage_was_ever_set():
    # A span created with NO trace_text_context() ever attached (should
    # not happen in production once app/chat/routes.py wires every real
    # turn, but the defensive default matters: redaction is opt-OUT via
    # an explicit "False", never opt-in by the mere ABSENCE of a tag).
    tp, exporter = _build_test_provider()
    tracer = tp.get_tracer("test")
    with tracer.start_as_current_span("untagged") as span:
        span.set_attribute("input.value", "should survive untouched")
    (result,) = exporter.get_finished_spans()
    assert result.attributes["input.value"] == "should survive untouched"


def test_manual_retrieval_span_is_covered_by_the_same_redaction(monkeypatch):
    # record_trace() (app/retrieval/service.py's own real caller) uses
    # the GLOBAL tracer provider -- set it to our test provider for
    # this one test, restoring afterward, matching OTel's own
    # documented test pattern (set_tracer_provider is the only way to
    # make a plain get_tracer() call pick up a specific provider).
    from opentelemetry import trace as trace_api

    tp, exporter = _build_test_provider()
    monkeypatch.setattr(trace_api, "_TRACER_PROVIDER", tp)

    token = context.attach(trace_text_context(False))
    try:
        with record_trace("retrieve", {"input.value": "a real visitor query"}):
            pass
    finally:
        context.detach(token)

    (result,) = exporter.get_finished_spans()
    assert result.attributes["input.value"].startswith("[REDACTED")


def test_tag_current_span_with_identity_sets_both_attributes():
    tp, exporter = _build_test_provider()
    tracer = tp.get_tracer("test")
    with tracer.start_as_current_span("turn"):
        tag_current_span_with_identity("tenant-123", "visitor-456")
    (result,) = exporter.get_finished_spans()
    assert result.attributes["tenant.id"] == "tenant-123"
    assert result.attributes["visitor.id"] == "visitor-456"


# --- [SECURITY] no secrets ever reach a span -----------------------------


def test_no_authorization_header_or_other_secret_ever_reaches_a_span():
    # A real FastAPI app, really instrumented, really sent a request
    # carrying a real-shaped Authorization bearer token AND a custom
    # header carrying a value that looks like a pgcrypto key -- proves
    # FastAPIInstrumentor's own default (no header capture at all,
    # confirmed by reading its real signature before this step was
    # built: http_capture_headers_server_request defaults to None) by
    # actually sending the request and inspecting the real exported
    # span's own attributes, not by trusting the default unverified.
    tp, exporter = _build_test_provider()
    app = FastAPI()

    @app.get("/probe")
    def probe():
        return {"ok": True}

    instrument_fastapi_app(app, tp)
    try:
        client = TestClient(app)
        secret_token = "sk-REAL-LOOKING-SECRET-TOKEN-98765"  # noqa: S105
        pgcrypto_key = "pgcrypto-passphrase-DO-NOT-LEAK-45678"  # noqa: S105
        response = client.get(
            "/probe",
            headers={
                "Authorization": f"Bearer {secret_token}",
                "X-Db-Connection-Encryption-Key": pgcrypto_key,
            },
        )
        assert response.status_code == 200
    finally:
        FastAPIInstrumentor().uninstrument_app(app)

    spans = exporter.get_finished_spans()
    assert spans  # the request really produced at least one span
    for span in spans:
        for key, value in span.attributes.items():
            haystack = f"{key}={value!r}"
            assert secret_token not in haystack
            assert pgcrypto_key not in haystack
            assert "bearer" not in haystack.lower()


def test_health_endpoint_is_excluded_from_tracing():
    tp, exporter = _build_test_provider()
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/healthcheck-report")  # must NOT be excluded by the same rule
    def not_health():
        return {"status": "ok"}

    instrument_fastapi_app(app, tp)
    try:
        client = TestClient(app)
        client.get("/health")
        spans_after_health_alone = list(exporter.get_finished_spans())
        client.get("/api/v1/healthcheck-report")
    finally:
        FastAPIInstrumentor().uninstrument_app(app)

    # /health produced NO spans at all.
    assert spans_after_health_alone == []
    # The lookalike path ("/api/v1/healthcheck-report", containing
    # "health" as a substring but not matching the `/health$`
    # end-anchored pattern) was NOT wrongly excluded -- it produced
    # exactly the normal one-span-per-request shape (below).
    assert len(exporter.get_finished_spans()) == 1


def test_streaming_responses_produce_one_span_per_request_not_per_chunk():
    # [SECURITY-adjacent finding, Step 4.5's own pre-build research]:
    # FastAPIInstrumentor's DEFAULT behavior creates one extra "http
    # send" child span per ASGI send() call -- for a real streamed SSE
    # response (POST /chat, docs/SPEC.md §10), that is roughly one
    # extra span per individual token event, confirmed live with a
    # direct repro (20 simulated chunks -> 25 total spans) BEFORE
    # instrument_fastapi_app() was given exclude_spans=["receive",
    # "send"] to fix it. This is the real regression test for that
    # fix -- docs/SPEC.md §13's own exact wording: "one span per
    # request, not per token."
    from fastapi.responses import StreamingResponse

    tp, exporter = _build_test_provider()
    app = FastAPI()

    @app.get("/stream")
    def stream():
        def gen():
            for i in range(20):
                yield f"token-{i}\n".encode()

        return StreamingResponse(gen(), media_type="text/event-stream")

    instrument_fastapi_app(app, tp)
    try:
        client = TestClient(app)
        response = client.get("/stream")
        _ = response.text  # drain the real stream, 20 real chunks
    finally:
        FastAPIInstrumentor().uninstrument_app(app)

    assert len(exporter.get_finished_spans()) == 1


def test_tracing_adds_no_measurable_latency_to_the_request_path():
    # docs/SPEC.md §18's own Phase 4 accept line: "tracing adds no
    # measurable latency to the request path." Proven two ways, not
    # one:
    # (1) BatchSpanProcessor (used here and in the real app, never
    #     SimpleSpanProcessor) queues finished spans and exports them
    #     on its OWN background worker thread -- the request's own
    #     call stack never waits on an export call, by construction,
    #     regardless of how slow or fast the real collector is. This
    #     is an architectural property, not something a timing
    #     assertion alone could prove (a slow collector behind a
    #     SimpleSpanProcessor would block the request; a slow
    #     collector behind a BatchSpanProcessor never does).
    # (2) The in-process overhead that DOES run synchronously on the
    #     request path (span creation, the tagging/redaction
    #     processors' own on_start/on_end, FastAPIInstrumentor's ASGI
    #     wrapping) is measured directly: the same trivial endpoint,
    #     timed many times, with the full real processor stack
    #     instrumented vs. with no instrumentation at all. The bound
    #     (5ms average) is deliberately generous for test-machine
    #     noise, the same "generous but bounded" treatment this
    #     project already gives `chat_model_timeout_seconds` -- the
    #     real production request path (an LLM API round-trip plus a
    #     Qdrant query, hundreds of milliseconds at minimum) makes
    #     this overhead immeasurable in practice; this bound exists so
    #     a real regression (e.g. a future processor doing blocking
    #     I/O in on_end()) would still fail this test long before it
    #     could be noticed in production.
    iterations = 200

    def _median_seconds_per_request(app: FastAPI) -> float:
        client = TestClient(app)
        client.get("/ping")  # warm up: first request pays import/JIT cost
        samples = []
        for _ in range(iterations):
            start = time.perf_counter()
            client.get("/ping")
            samples.append(time.perf_counter() - start)
        samples.sort()
        return samples[len(samples) // 2]

    plain_app = FastAPI()

    @plain_app.get("/ping")
    def ping_plain():
        return {"ok": True}

    plain_seconds = _median_seconds_per_request(plain_app)

    traced_app = FastAPI()

    @traced_app.get("/ping")
    def ping_traced():
        return {"ok": True}

    tp = TracerProvider()
    tp.add_span_processor(_TraceTextTaggingProcessor())
    tp.add_span_processor(_TraceTextRedactionProcessor())
    tp.add_span_processor(BatchSpanProcessor(InMemorySpanExporter()))
    instrument_fastapi_app(traced_app, tp)
    try:
        traced_seconds = _median_seconds_per_request(traced_app)
    finally:
        FastAPIInstrumentor().uninstrument_app(traced_app)
        tp.shutdown()

    overhead_ms = (traced_seconds - plain_seconds) * 1000
    assert overhead_ms < 5.0, (
        f"tracing added {overhead_ms:.2f}ms median overhead per request "
        "(plain="
        f"{plain_seconds * 1000:.2f}ms, traced={traced_seconds * 1000:.2f}ms) "
        "-- exceeds the 5ms bound for docs/SPEC.md §18's Phase 4 accept "
        "line ('tracing adds no measurable latency to the request path')"
    )


# --- init_tracing() / shutdown_tracing() ---------------------------------


async def test_init_tracing_returns_none_when_not_configured(monkeypatch):
    set_valid_env(monkeypatch, VALID_ENV)
    get_settings.cache_clear()
    assert await init_tracing(Settings()) is None


async def test_init_tracing_never_raises_against_an_unreachable_collector(monkeypatch):
    # Port 1: a privileged port with nothing listening on localhost in
    # any real test environment -- an instant connection-refused, not a
    # hang, so this test stays fast while still proving the real,
    # non-fatal reachability-check path end to end.
    set_valid_env(
        monkeypatch,
        VALID_ENV,
        PHOENIX_COLLECTOR_ENDPOINT="http://127.0.0.1:1/v1/traces",
        PHOENIX_STARTUP_REACHABILITY_TIMEOUT_SECONDS="1",
    )
    get_settings.cache_clear()
    tracer_provider = await asyncio.wait_for(init_tracing(Settings()), timeout=10)
    assert tracer_provider is not None  # configured, even though unreachable
    shutdown_tracing(tracer_provider)


def test_shutdown_tracing_is_a_safe_noop_when_none():
    shutdown_tracing(None)  # must not raise


def test_shutdown_tracing_calls_shutdown_on_a_real_provider():
    calls = []

    class _FakeProvider:
        def shutdown(self):
            calls.append("shutdown")

    shutdown_tracing(_FakeProvider())
    assert calls == ["shutdown"]


def test_shutdown_tracing_logs_rather_than_raises_on_a_failing_provider():
    class _FailingProvider:
        def shutdown(self):
            raise RuntimeError("simulated shutdown failure")

    shutdown_tracing(_FailingProvider())  # must not raise
