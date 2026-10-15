# backend/app/telemetry/service.py
# Step 4.5: Phoenix tracing (docs/SPEC.md §13), replacing the Step 1.1b
# stub. Self-hosted Phoenix (a separate Compose service, deploy/docker-
# compose.yml) is the collector + UI; this module is everything the API
# process itself does: register a real TracerProvider with a BATCHING
# span processor (non-blocking export, docs/SPEC.md §13's own named
# requirement), instrument LangChain/LangGraph (OpenInference) and
# FastAPI (OpenTelemetry) -- confirmed live, both packages installed
# and real, not merely assumed maintained -- a manual retriever span
# (app/retrieval/service.py's own retrieve(), the one Qdrant call this
# project's own OpenInference instrumentor never sees, since it is a
# raw qdrant-client call, not a LangChain Runnable), and the per-tenant
# trace-text switch "applied per request before spans are exported".
#
# [SECURITY], even though this step is untagged in docs/SPEC.md §18's
# own Phase 4 step list (Felix's own direct instruction, Step 4.5
# kickoff): the Phoenix UI/collector are reachable only on the internal
# Docker network or localhost, never routed through Caddy (enforced in
# deploy/docker-compose.yml -- bound to 127.0.0.1 only -- and documented
# there as an SSH-tunnel-only surface, not in this module); traces must
# never contain API keys, auth headers or the pgcrypto key (tested in
# tests/test_telemetry_service.py, against this module's own real code,
# not reasoned about); the retention and per-tenant switch-off rule are
# both tested against real OTel SDK behavior, not assumed from reading
# the two libraries' own docs.
#
# [SECURITY-adjacent finding, confirmed live before this module was
# written, not assumed]: OTel's own `BoundedAttributes` (the real
# container every ReadableSpan's `.attributes` is) RAISES `TypeError`
# on any `__setitem__`/`__delitem__` once a span has ended -- the
# well-known "redact in a SpanProcessor.on_end()" pattern from older
# OTel SDK versions no longer works unmodified against the version
# this project installs. There is no PUBLIC API for post-end attribute
# mutation in this SDK version; the documented-in-practice escape hatch
# (toggling the container's own private `_immutable` flag, still going
# through the public `__setitem__`/`__delitem__` methods rather than
# writing to `_dict` directly) is what `_TraceTextRedactionProcessor`
# below uses, proven against a real `InMemorySpanExporter` before
# shipping it (tests/test_telemetry_service.py).
import asyncio
import logging
from urllib.parse import urlsplit

from opentelemetry import baggage, context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor, TracerProvider
from opentelemetry.trace import get_current_span

from app.config import Settings

logger = logging.getLogger(__name__)

# docs/SPEC.md §13: "Spans are tagged with tenant and visitor." Also the
# baggage key this module's own trace-text switch propagates through --
# see _TraceTextTaggingProcessor's own docstring for why baggage, not a
# direct span attribute set by the caller, is the real mechanism.
TENANT_ID_ATTRIBUTE = "tenant.id"
VISITOR_ID_ATTRIBUTE = "visitor.id"
_TRACE_TEXT_BAGGAGE_KEY = "trace_text_enabled"

# openinference.semconv.trace.SpanAttributes' own text-bearing keys
# (confirmed live by reading that module directly, not guessed): every
# attribute whose key STARTS WITH one of these carries visitor- or
# model-generated TEXT, never structural/numeric data, and is exactly
# what docs/SPEC.md §13's own per-tenant trace-text switch must redact
# when it is off. `llm.prompts` added defensively alongside the four
# confirmed keys -- the same semantic family (prompt/completion text),
# even though this project's own LangChain usage does not currently
# populate it (no raw-prompt LLM call pattern in app/agent/graph.py).
_TEXT_ATTRIBUTE_PREFIXES = (
    "input.value",
    "output.value",
    "llm.input_messages",
    "llm.output_messages",
    "llm.prompts",
    "retrieval.documents",
)
_REDACTED_VALUE = "[REDACTED: trace text disabled for this tenant]"


def is_phoenix_configured(settings: Settings) -> bool:
    """`False` is a normal, expected state for a fresh deployment that
    has not stood up the Phoenix service yet -- matches `is_smtp_
    configured()`'s own established "optional, opt-in feature" shape
    exactly (app/notifications/service.py).
    """
    return settings.phoenix_collector_endpoint is not None


class _TraceTextTaggingProcessor(SpanProcessor):
    """on_start ONLY: copies the CURRENT request's own trace-text
    verdict -- set as OTel baggage around the whole turn, by whichever
    caller resolved it (app/chat/routes.py) -- onto every span created
    during that request, including the ones LangChain's/FastAPI's own
    instrumentors create automatically, which this project's own code
    never touches directly. Baggage, not a plain contextvar: with a
    BATCHING span processor (required, docs/SPEC.md §13), the real
    export happens on a separate background thread/task long after the
    original request's own context is gone -- the verdict MUST be baked
    into the span's own attributes synchronously, while on_start still
    runs inside the original request's context, never deferred to
    export time.

    MUST be registered BEFORE `_TraceTextRedactionProcessor` below --
    confirmed live (not assumed) that `TracerProvider` invokes every
    registered processor's own on_start/on_end in registration order,
    so this tagging processor's on_start needs to run before anything
    else reads the tag it sets.
    """

    def on_start(self, span: Span, parent_context: context.Context | None = None) -> None:
        enabled = baggage.get_baggage(_TRACE_TEXT_BAGGAGE_KEY, context=parent_context)
        if enabled is not None:
            span.set_attribute(_TRACE_TEXT_BAGGAGE_KEY, enabled)

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class _TraceTextRedactionProcessor(SpanProcessor):
    """on_end: strips every text-bearing attribute (see
    `_TEXT_ATTRIBUTE_PREFIXES`) from a span whose own `trace_text_
    enabled` tag -- set by `_TraceTextTaggingProcessor` above, which
    MUST run first -- reads `"False"`. This is the real mechanism
    behind docs/SPEC.md §13's own "the switch is applied per request
    before spans are exported": registered BEFORE the real export
    processor in `init_tracing()` below, so a redacted span is what the
    batch exporter actually queues, never the original un-redacted one.

    A span with NO tag at all (tracing wired up with no per-request
    baggage ever set -- should not happen in production once app/chat/
    routes.py sets it on every real turn, but a defensive default
    matters here) is treated as trace-text ENABLED, never silently
    redacted -- redaction is opt-OUT (the tenant/retention rule turns
    it off), not opt-in, matching docs/SPEC.md §13's own "stored...
    unless switched off" default.
    """

    def on_start(self, span: Span, parent_context: context.Context | None = None) -> None:
        pass

    def on_end(self, span: ReadableSpan) -> None:
        attrs = getattr(span, "_attributes", None)
        if attrs is None or attrs.get(_TRACE_TEXT_BAGGAGE_KEY) != "False":
            return
        was_immutable = attrs._immutable
        attrs._immutable = False
        try:
            for key in list(attrs.keys()):
                if key.startswith(_TEXT_ATTRIBUTE_PREFIXES):
                    attrs[key] = _REDACTED_VALUE
        finally:
            attrs._immutable = was_immutable

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


def effective_trace_text_enabled(tenant_config: dict, retention_days: int) -> bool:
    """docs/SPEC.md §13: "Tenants whose chat retention is strictly less
    than 30 days (1 to 29) get trace text switched off automatically...
    at exactly 30 days it stays on" -- plus the tenant's own explicit
    `trace_text_enabled` switch (default True, docs/SPEC.md: "stored...
    the tenant can switch it off"). Both conditions must hold for text
    to be captured; either one disables it. A pure function of already-
    resolved inputs (never reads `tenants.config` or Settings itself)
    so it is trivially testable against every combination without a
    database -- the caller (app/chat/routes.py) is responsible for
    resolving `retention_days` the IDENTICAL way `handle_retention_
    sweep()` already does (tenants.config["retention_days"], falling
    back to the platform default, clamped to [1, 90]).
    """
    explicit_switch = tenant_config.get("trace_text_enabled", True)
    return bool(explicit_switch) and retention_days >= 30


def trace_text_context(enabled: bool) -> context.Context:
    """Returns a new Context with this turn's own trace-text verdict
    attached as baggage -- the caller attaches it (`context.attach()`)
    before the turn starts and detaches the returned token after,
    matching `opentelemetry.context`'s own attach/detach contract
    exactly (never a bare `with` block here: attach/detach must wrap
    the ENTIRE turn, including the LangGraph run, not just this
    function's own body).
    """
    return baggage.set_baggage(_TRACE_TEXT_BAGGAGE_KEY, str(enabled))


def tag_current_span_with_identity(tenant_id: object, vid: object) -> None:
    """docs/SPEC.md §13: "Spans are tagged with tenant and visitor." A
    plain `set_attribute` on the CURRENT span (the FastAPI-instrumented
    request span, at the point the caller has resolved identity) --
    never creates a new span, never touches baggage. `str()` on both:
    OTel attribute values must be primitives (str/bool/int/float or a
    homogeneous sequence of one of those), never a `uuid.UUID` object
    directly -- confirmed live, an unconverted UUID is silently DROPPED
    by `BoundedAttributes`'s own `_clean_attribute_value()`, not an
    error, which would have made this tagging silently do nothing.
    """
    span = get_current_span()
    span.set_attribute(TENANT_ID_ATTRIBUTE, str(tenant_id))
    span.set_attribute(VISITOR_ID_ATTRIBUTE, str(vid))


def record_trace(name: str, attributes: dict | None = None):
    """The ONE manual span this project builds by hand (docs/SPEC.md
    §13: "a manual retriever span inside retrieve()") -- every other
    span in this application comes from an automatic instrumentor
    (LangChain/LangGraph via OpenInference, FastAPI via OpenTelemetry).
    `retrieve()`'s own real Qdrant call (`client.query_batch_points()`,
    app/retrieval/service.py) is a raw qdrant-client call, not a
    LangChain Runnable, so no instrumentor ever sees it on its own.

    Returns a context manager (`tracer.start_as_current_span`, from
    whatever the GLOBAL tracer provider is at call time) -- a no-op,
    real span (the OTel API's own documented behavior with no
    TracerProvider configured, confirmed live) when tracing is not
    configured at all, so every call site can use this unconditionally,
    with no `if is_phoenix_configured(...)` branch of its own.
    """
    from opentelemetry import trace as trace_api

    tracer = trace_api.get_tracer("samoflow.retrieval")
    return tracer.start_as_current_span(name, attributes=attributes or {})


async def _check_collector_reachable(endpoint: str, timeout_seconds: float) -> bool:
    """docs/SPEC.md §13: "a startup check that the collector is
    reachable." A bounded, plain TCP connect to the endpoint's own
    host/port -- NOT an HTTP health-check GET: Phoenix's own `/v1/
    traces` path is a POST-only OTLP ingestion endpoint (confirmed by
    reading Arize's own self-hosting docs before relying on this), and
    no documented `/healthz`-shaped path was found for the self-hosted
    server -- a TCP-level check proves the real thing this function's
    own name promises ("is the collector reachable at all") without
    depending on an unconfirmed HTTP contract. Never raises: a failure
    here is logged and returned as `False`, never blocking startup
    (matches this project's own hard-won Step-4.3/Part-A lesson -- a
    startup dependency check must never crash the whole process or
    `/health` would go down with it).
    """
    parsed = urlsplit(endpoint)
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if host is None:
        logger.warning(
            "phoenix_collector_endpoint %r has no host -- cannot check reachability", endpoint
        )
        return False
    try:
        async with asyncio.timeout(timeout_seconds):
            _reader, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
        return True
    except Exception:
        logger.warning(
            "Phoenix collector at %s:%s is not reachable -- tracing will export spans "
            "(if any are produced) into a void until this is fixed; the API process "
            "itself starts normally regardless",
            host,
            port,
        )
        return False


async def init_tracing(settings: Settings) -> TracerProvider | None:
    """Returns `None` (tracing disabled) when Phoenix is not configured
    -- the identical "optional, opt-in feature, deployment must still
    start without it" shape as SMTP/Anthropic. Never raises on an
    unreachable collector (see `_check_collector_reachable`'s own
    docstring) -- only a malformed Settings value (caught nowhere here
    deliberately: a real misconfiguration should fail loudly at
    startup, the same "validate, don't silently default" discipline
    `Settings` itself already applies) propagates.
    """
    if not is_phoenix_configured(settings):
        logger.info("PHOENIX_COLLECTOR_ENDPOINT is not set -- tracing disabled")
        return None

    # Imported lazily: these three packages (arize-phoenix-otel,
    # openinference-instrumentation-langchain, opentelemetry-
    # instrumentation-fastapi) are real, pinned production dependencies
    # (backend/pyproject.toml) -- imported here, not at module level, so
    # importing app.telemetry.service itself never requires them to be
    # installed for a caller that only needs the pure helpers above
    # (effective_trace_text_enabled, trace_text_context) -- matching
    # this project's own "optional feature, lazy import" precedent
    # (app/plans/usage.py's own estimate_cost_usd(), which only needs
    # pricing data when a real call is priced).
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from phoenix.otel import BatchSpanProcessor

    resource = Resource.create({"openinference.project.name": settings.phoenix_project_name})
    tracer_provider = TracerProvider(resource=resource)
    # Registration order is the real mechanism, confirmed live: tag
    # FIRST (on_start), redact SECOND (on_end, before export), export
    # LAST (on_end, after redaction already ran) -- see both custom
    # processors' own docstrings above.
    tracer_provider.add_span_processor(_TraceTextTaggingProcessor())
    tracer_provider.add_span_processor(_TraceTextRedactionProcessor())
    tracer_provider.add_span_processor(
        BatchSpanProcessor(endpoint=settings.phoenix_collector_endpoint)
    )

    if not LangChainInstrumentor().is_instrumented_by_opentelemetry:
        LangChainInstrumentor().instrument(tracer_provider=tracer_provider)

    await _check_collector_reachable(
        settings.phoenix_collector_endpoint, settings.phoenix_startup_reachability_timeout_seconds
    )
    return tracer_provider


def instrument_fastapi_app(app, tracer_provider: TracerProvider) -> None:
    """Separate from `init_tracing()`: a FastAPI app instance does not
    exist yet at Settings-resolution time (app/main.py's own lifespan
    hook builds the app BEFORE this hook runs) -- `create_app()` calls
    this directly, after `init_tracing()` returns a real provider.

    `excluded_urls=r"/health$"`: confirmed LIVE, not assumed, that this
    library's own `excluded_urls` performs an UNANCHORED regex SEARCH
    against the full request URL (scheme+host+path), not a path-only
    match -- an anchored `^/health$` pattern therefore never matches
    anything (the URL never starts with "/health", it starts with
    "http://..."), while a bare `/health` (no end anchor) would ALSO
    exclude a hypothetical future path merely containing that substring
    (e.g. "/api/v1/healthcheck"). `/health$` is the live-confirmed
    correct middle ground: matches the real `/health` endpoint exactly,
    matches nothing else.

    `exclude_spans=["receive", "send"]`: a REAL finding, not merely a
    tidiness choice -- confirmed live, before shipping without it, that
    this instrumentor's own DEFAULT behavior creates one extra "http
    send" child span per ASGI `send()` call, which for a streamed SSE
    response (POST /chat, docs/SPEC.md §10) means roughly one extra
    span per individual token event -- 20 simulated streamed chunks
    produced 25 total spans in a direct, real repro. This directly
    contradicts docs/SPEC.md §13's own explicit requirement, word for
    word: "OpenTelemetry for FastAPI... one span per request, not per
    token." With this parameter, the same 20-chunk repro produces
    exactly 1 span (tests/test_telemetry_service.py's own `test_
    streaming_responses_produce_one_span_per_request_not_per_chunk`).
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor().instrument_app(
        app,
        tracer_provider=tracer_provider,
        excluded_urls=r"/health$",
        exclude_spans=["receive", "send"],
    )


def shutdown_tracing(tracer_provider: TracerProvider | None) -> None:
    """docs/SPEC.md §13: "a flush on shutdown." `shutdown()` (confirmed
    live, `phoenix.otel.otel.TracerProvider` exposes it, Step 4.5's own
    pre-build research) flushes every registered processor's own
    pending batch THEN closes it -- a separate `force_flush()` call
    first is redundant, not a second safety net, so this function makes
    exactly one call. A no-op, never raising, when tracing was never
    configured (`tracer_provider is None`).
    """
    if tracer_provider is None:
        return
    try:
        tracer_provider.shutdown()
    except Exception:
        logger.exception("failed to shut down the Phoenix tracer provider cleanly")
