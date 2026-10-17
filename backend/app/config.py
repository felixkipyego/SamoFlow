# backend/app/config.py
# Settings module (Task 1.1.c): validates the required environment
# variables at startup and exposes them through one cached Settings
# instance. Nothing here reads the environment or calls get_settings() at
# import time (module import must succeed with an empty environment).
# Task 1.3.a1 adds qdrant_api_key (required, never optional or defaulted --
# an unauthenticated Qdrant client would silently work today and silently
# stop working the moment auth is enabled on a real deployment). Task 1.4.a
# adds jwt_signing_key (required) and jwt_signing_key_previous (optional,
# for rotation via a future `kid` header -- PyJWT/the token module itself
# arrive in 1.4.b, this task only adds the secret plumbing). Task 2.1.a
# adds db_connection_encryption_key (required): the pgcrypto passphrase
# for db_connections.encrypted_credentials. Task 2.5.a adds openai_api_key
# (required): the dense-embedding primitive's own API credential. Task
# 3.4.a adds anthropic_api_key (optional, same "not configured means None"
# shape as jwt_signing_key_previous -- the secondary chat-model provider,
# app/agent/chat_model.py) and chat_model_primary/chat_model_secondary
# (plain strings, the model identifiers themselves).
#
# ASSUMPTION: pydantic and pydantic-settings are not new dependencies here.
# pydantic-settings==2.15.0 is already an approved runtime dependency
# (Step 1.1.a); pydantic arrives transitively through it and fastapi.
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.secrets_policy import SECRET_CLASS_SETTINGS, check_raw_secret_values

# SQLAlchemy 2.0 maps a plain "postgresql://" DSN to the psycopg2 driver,
# which this project does not install (Step 1.1.a installs psycopg[binary],
# i.e. psycopg 3). This scheme selects the psycopg (v3) driver instead.
# Public (no leading underscore): Task 1.1.h's test database guard imports
# this instead of duplicating the literal.
POSTGRES_SCHEME = "postgresql+psycopg"


def _require_no_whitespace_or_control_chars(raw: str, field_name: str, reason: str) -> None:
    # Shared by every secret field that needs this (duplication check after
    # 1.4.a/b; re-confirmed still accurate at the 2.2.d/e/f duplication
    # check, which deliberately rewords this away from naming call sites by
    # count/name -- that phrasing had already gone stale once by then):
    # rejects stray whitespace or control characters (e.g. from a
    # copy-paste error), each caller for its own reason -- `reason` keeps
    # the message truthful per field instead of one field borrowing
    # another's rationale. Message names only the requirement, never the
    # value under validation.
    if any(ch.isspace() or not ch.isprintable() for ch in raw):
        raise ValueError(
            f"{field_name} must not contain whitespace or control characters ({reason})."
        )


class Settings(BaseSettings):
    # frozen: instances are immutable after construction.
    # hide_input_in_errors: hides the raw offending value from str()/repr()
    # of a ValidationError, but not from its structured .errors() list
    # (verified against pydantic 2.13.5). get_settings() below is the
    # boundary that turns a ValidationError into an input-free SettingsError;
    # calling Settings() directly still risks a raw value in .errors(), so
    # startup code must always go through get_settings().
    model_config = SettingsConfigDict(frozen=True, hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"]
    database_url: SecretStr
    qdrant_url: str
    qdrant_api_key: SecretStr
    # Task 3.1.b: a SEPARATE, server-enforced read-only credential for
    # retrieve()'s own read path (app/retrieval/service.py) -- confirmed
    # live, against the real pinned qdrant/qdrant:v1.19.1 server, that
    # `service.read_only_api_key` (env `QDRANT__SERVICE__READ_ONLY_API_KEY`
    # on the Qdrant server itself) is genuine server-side RBAC, not an
    # application-level convention: a write attempt (upsert, delete
    # collection) through this key is rejected by Qdrant itself with HTTP
    # 403 "Forbidden: Global manage access is required," while every real
    # read method `retrieve()` needs (scroll, query_points) succeeds
    # normally. Required, same discipline as `qdrant_api_key` -- a missing
    # read-only key would silently fall back to no least-privilege
    # separation at all, exactly the "fail loud at startup" case this
    # project's own existing secrets already avoid.
    qdrant_read_only_api_key: SecretStr
    # min_length uses pydantic's own built-in constraint (rule 11 -- a
    # well-known mechanism, not a hand-rolled length check); it applies to
    # jwt_signing_key_previous's SecretStr arm only, never to its None arm
    # (confirmed by hand against the installed pydantic==2.13.5).
    jwt_signing_key: SecretStr = Field(min_length=32)
    jwt_signing_key_previous: SecretStr | None = Field(default=None, min_length=32)
    api_host: str = Field(min_length=1)
    api_port: int = Field(ge=1, le=65535)
    # Task 1.5.b: two independent rate-limit policies for
    # POST /api/v1/session (docs/SPEC.md §9), each a (limit, window) pair.
    # Not secrets -- ordinary configurable integers, no SecretStr, no
    # guarded accessor, no allow-list. gt=0 is pydantic's own built-in
    # constraint (rule 11, same mechanism api_port already uses above),
    # not a hand-rolled positivity check -- it rejects 0 and negative
    # values for all four fields with no custom validator needed.
    session_rate_limit_per_site_key: int = Field(default=10, gt=0)
    session_rate_limit_per_site_key_window_seconds: float = Field(default=60, gt=0)
    session_rate_limit_per_ip: int = Field(default=30, gt=0)
    session_rate_limit_per_ip_window_seconds: float = Field(default=60, gt=0)
    # [SECURITY] Step 7-follow-up, Part C: GET /api/v1/widget-appearance
    # (no visitor session, no conversation, no LLM call -- just the
    # tenant's own public primary color/text color/position) is called
    # on EVERY page view, not only on a chat open like /session is --
    # higher limits than session's own, same (limit, window) shape,
    # reasoned explicitly rather than reusing session's numbers blindly:
    # a real, popular tenant site under genuine traffic could see many
    # concurrent FIRST-time visitors each firing this once; the browser's
    # own HTTP cache (Cache-Control/ETag, see the route itself) already
    # absorbs almost all repeat load within the cache window, so this
    # only needs to bound the genuinely-uncached case.
    widget_appearance_rate_limit_per_site_key: int = Field(default=120, gt=0)
    widget_appearance_rate_limit_per_site_key_window_seconds: float = Field(default=60, gt=0)
    widget_appearance_rate_limit_per_ip: int = Field(default=60, gt=0)
    widget_appearance_rate_limit_per_ip_window_seconds: float = Field(default=60, gt=0)
    # Task 2.1.a: the pgcrypto passphrase for db_connections.
    # encrypted_credentials (docs/SPEC.md §5.6: "Credentials are encrypted
    # at rest"). Required, same discipline as qdrant_api_key/
    # jwt_signing_key -- an unencrypted-at-rest credential would silently
    # work today and silently stay wrong on a real deployment if this were
    # optional. min_length=32 matches jwt_signing_key's own established
    # floor (rule 11 -- reuse an already-justified number, not a new one
    # invented here).
    db_connection_encryption_key: SecretStr = Field(min_length=32)
    # Task 2.1.c: the job queue's own retry policy. Not secrets -- same
    # treatment as the session-rate-limit fields above (plain configurable
    # numbers, gt=0 is pydantic's own built-in constraint, no custom
    # validator). job_max_attempts is read by enqueue() (IngestRepository)
    # to set each new job row's own max_attempts column at creation time --
    # mark_job_failed() then compares against that row's own persisted
    # value, not a fresh Settings read, so changing this setting later
    # never shifts the retry ceiling of an already-created job. jobs.
    # max_attempts' DB-level server_default=5 (app/ingest/models.py, from
    # 2.1.a) stays as an inert defensive floor for any insert that
    # bypasses enqueue(); it no longer governs behavior for jobs created
    # through the sanctioned path once enqueue() always sets it
    # explicitly.
    job_retry_base_seconds: float = Field(default=60, gt=0)
    job_max_attempts: int = Field(default=5, gt=0)
    # Task 2.6.f: the stuck-job reaper's own staleness threshold (the
    # Step-2.1.e-owned marker, reassigned to Step 2.6 at 2.4.g's close-out)
    # -- how long a job may sit in "running" with no `updated_at` progress
    # before it's reclaimed. Same treatment as the two fields above (plain
    # configurable number, gt=0). 1800s (30 minutes): generous enough to
    # never false-positive on a genuinely long-running crawl at this
    # project's own current scale (the 500-page default `crawl_page_cap`,
    # 2.6.e, throttled at ~0.25s/request plus a real embedding call per
    # page realistically totals single-digit minutes, not 30), while still
    # bounding a genuinely crashed worker's own stuck row to "reclaimed
    # within half an hour" rather than forever -- retuned later with real
    # numbers, like every other estimate in this project.
    job_stuck_after_seconds: float = Field(default=1800, gt=0)
    # Task 2.1.d: how long worker.py's run() waits between poll attempts
    # when claim_next_job() finds nothing ready -- never a tight spin
    # loop. Same treatment as the fields directly above (plain
    # configurable number, gt=0 is pydantic's own built-in constraint).
    # 2 seconds: noticeably shorter than job_retry_base_seconds's own
    # 60-second floor (this is "is anything ready yet", not a retry
    # delay), but long enough that an idle worker doesn't burn CPU/DB
    # round trips -- retuned later with real load-test numbers if needed
    # (Phase 7), like every other estimate in this project.
    worker_poll_interval_seconds: float = Field(default=2, gt=0)
    # Task 2.5.a: the dense-embedding primitive's own API credential
    # (docs/SPEC.md §5.1: "an English embedding model such as
    # text-embedding-3-small"). Required, same discipline as qdrant_api_key
    # -- an unauthenticated embedding call is not a real
    # failure mode (the API simply refuses it), but a missing key would
    # silently defer the failure from startup to the first real ingestion
    # job, which this project's own "fail loud at startup" pattern exists
    # to avoid. No min_length (matching qdrant_api_key's own treatment,
    # not jwt_signing_key's): this is an opaque, already-high-entropy
    # third-party-issued key, not a self-chosen secret needing an
    # artificial entropy floor.
    openai_api_key: SecretStr
    # Task 3.4.a: the secondary chat-model provider's own API credential
    # (docs/SPEC.md §6.3's own "Models" bullet: "per-tenant config via
    # `init_chat_model`, with `.with_fallbacks()` to a secondary
    # provider"). OPTIONAL, not required -- a deliberate, justified
    # deviation from every other secret in this file, matching an
    # ALREADY-ESTABLISHED pattern in this same class
    # (`jwt_signing_key_previous` immediately above, not a novel shape
    # invented here): the secondary provider is genuinely optional at
    # runtime (Q2's own decision) -- a tenant/deployment that never
    # configures `ANTHROPIC_API_KEY` must still start up and serve
    # answers through the primary provider alone, not fail startup for a
    # feature it never opted into. `app/agent/chat_model.py` (Task 3.4.a)
    # logs a startup warning when this is unset, so a primary-only
    # fallback chain is a visible, deliberate state, not a silent one.
    # No min_length, matching `openai_api_key`'s own treatment: an
    # opaque, already-high-entropy third-party-issued key.
    anthropic_api_key: SecretStr | None = Field(default=None)
    # Task 3.4.a: the primary/secondary chat-model IDENTIFIERS themselves
    # -- plain strings, not secrets, matching `crawl_user_agent`'s own
    # "Settings-configurable, not hardcoded" treatment immediately below
    # (rule 11's own "nothing hard-coded that should be configuration").
    # `chat_model_primary` CONFIRMED live at Task 3.4.b's own research
    # task: a real `gpt-4o-mini` call (opt-in, manual, never committed as
    # a CI test) worked exactly as expected. `chat_model_secondary`
    # (Anthropic) is STILL an unconfirmed ASSUMPTION -- no real API key
    # was available during that research; see PROJECT_SPEC.md's own new
    # §9 marker (Task 3.4.b) gating `ANTHROPIC_API_KEY` in production on a
    # manual streaming check passing first.
    # RATIFIED at "gpt-4o" (was "gpt-4o-mini") by Felix at Task 3.7.g
    # (2026-10-14), a Settings-only change -- real measurement, not
    # assumed: a full-106-item golden-set run, N=3, found "gpt-4o-mini"
    # (the next tier DOWN) genuinely answers 4 false positives and
    # (at full scale, with the referent-joining bug this task also
    # found and fixed) 8 false negatives that "gpt-4o" (the next tier
    # UP, same provider/family) reduces to 1 and 5 respectively, with no
    # category's own correct-answer count dropping, injection held
    # 15/15 across every repeat, and first-token latency still well
    # inside docs/SPEC.md §17's budget (p50=708ms, p95=968ms vs. the
    # 1500/3000ms targets). Two prompt-only variants tested alongside
    # this (explicit multi-chunk-synthesis permission, with and without
    # this model change) both made the false-negative count WORSE, not
    # better -- the model upgrade, not a prompt change, is what
    # actually moved the real numbers. Real cost trade-off, named
    # plainly, not hidden: ~16.7x more expensive per 1000 turns ($0.42
    # -> $6.95, real token counts, published OpenAI pricing) -- see
    # PROJECT_SPEC.md's own decision log for the full per-variant table
    # and the 5 remaining false negatives/1 remaining false positive
    # this does NOT fix (read, not guessed, in that same entry).
    # RATIFIED at "gpt-6-luna" (was "gpt-4o") by Felix at Task 3.7.i
    # (2026-10-14), a Settings-only change -- 3.7.h's own filter
    # ("priced between gpt-4o-mini and gpt-4o") wrongly excluded
    # cheaper current models; corrected to "cheaper than gpt-4o,
    # current generation, no shutdown before May 2027". A full-106-item
    # run, N=3, at `reasoning_effort="low"` (this model's OWN lowest
    # setting): FP=2 (vs. gpt-4o's 1, within the <=2 bar), FN=2 (vs.
    # gpt-4o's 5 -- BETTER, not just within the <=6 bar), no category's
    # correct count dropping by more than 1, injection held 15/15 every
    # repeat, first-token p50=1176ms/p95=2927ms (within §17's 1500/
    # 3000ms budget), 0.0% real failures (same as gpt-4o's own 0.0%).
    # Real cost: ~22x CHEAPER than gpt-4o ($7.0100 -> $0.3147 per 1000
    # turns, real token counts, published pricing) -- the only
    # candidate of 3 real ones tested that passed every ship-rule
    # condition; see PROJECT_SPEC.md's own decision log for the full
    # per-candidate table, including the two that did not pass and why.
    chat_model_primary: str = Field(default="gpt-6-luna")
    chat_model_secondary: str = Field(default="claude-haiku-4-5")
    # Task 3.4.b (PART 0 follow-up): a TWO-KEY switch, code-enforced, not
    # just documented -- `app/agent/chat_model.py`'s own `get_chat_model()`
    # wires the secondary provider into the fallback chain only if BOTH
    # `anthropic_api_key` is set AND this flag is `True`. Default `False`:
    # setting the API key alone is not enough to enable it in production.
    # The manual check this flag attests to (`backend/scripts/manual_
    # check_chat_model_streaming.py`) confirms, against the REAL Anthropic
    # API, that `tool_call_chunks` stream incrementally and the real
    # `Answer` schema's own field order is preserved -- the same two
    # properties already confirmed live for OpenAI -- before this flag
    # should ever be set `True` in a real environment. Not a secret (no
    # SecretStr) -- a plain operational switch, matching `crawl_user_
    # agent`'s own "Settings-configurable, not hardcoded" treatment.
    chat_model_secondary_verified: bool = Field(default=False)
    # Task 3.4.b: a per-request READ timeout (httpx's own `read=` category
    # -- confirmed live, not assumed, that this bounds the GAP BETWEEN
    # consecutive reads, including time-to-first-byte, never the total
    # response duration: a local slow-streaming test server sending four
    # chunks 1.5s apart, each individually under a 3.0s read timeout,
    # completed successfully in ~6.2s total -- a total-duration timeout
    # would have failed around the 3s mark instead). This is exactly the
    # semantics the chat model needs: a long, genuinely-streaming answer
    # must keep flowing as long as new tokens keep arriving within this
    # window, even if the whole answer takes far longer in total.
    # Deliberately a `Settings` field (unlike `embed_dense()`'s own
    # hard-coded reliance on the SDK default) -- confirmed at the Step
    # 3.4.a review that the chat model is QUERY-PATH ONLY, never shared
    # with ingestion's own looser tolerance, so a construction-time
    # default here is the right place, not a per-call override.
    chat_model_timeout_seconds: float = Field(default=5.0, gt=0)
    # Task 4.1. docs/SPEC.md §9's own two named POST /api/v1/chat limits --
    # "10 messages per minute per visitor" (the number, taken verbatim from
    # the spec, not invented here) and "a per-IP cap across visitors" (no
    # number named in the spec for this one). The per-IP default below
    # reuses session_rate_limit_per_ip's own established 3x-the-per-key-
    # limit ratio (30 = 3x10, matching that field's own 30:10 shape) rather
    # than inventing a new, unjustified ratio -- rule 11, reuse an
    # already-justified number. Same `RateLimiter` class (Task 1.5.a), two
    # NEW, separate instances with disjoint key spaces (per-visitor keyed by
    # `vid`, per-IP keyed by the request's own IP) -- never the session
    # endpoint's own two limiter instances, which track a structurally
    # different pair of keys (IP, site_key).
    chat_rate_limit_per_visitor: int = Field(default=10, gt=0)
    chat_rate_limit_per_visitor_window_seconds: float = Field(default=60, gt=0)
    chat_rate_limit_per_ip: int = Field(default=30, gt=0)
    chat_rate_limit_per_ip_window_seconds: float = Field(default=60, gt=0)
    # [SECURITY] batch follow-up (2026-10-16): POST /chat/stop had NO
    # rate limiting at all -- a real, confirmed gap (every other
    # authenticated widget endpoint has one). Same shape as the chat
    # limits above, a separate, independent `RateLimiter` instance with
    # its own disjoint key space -- a visitor hammering /chat/stop must
    # never consume their own /chat quota, and vice versa. Deliberately
    # a HIGHER per-visitor limit than /chat itself (a visitor clicking
    # Stop is never a billable AI call the way a chat turn is, so there
    # is no cost-containment reason to cap it as tightly -- this bound
    # exists purely against abuse/DoS of the endpoint itself).
    chat_stop_rate_limit_per_visitor: int = Field(default=20, gt=0)
    chat_stop_rate_limit_per_visitor_window_seconds: float = Field(default=60, gt=0)
    chat_stop_rate_limit_per_ip: int = Field(default=60, gt=0)
    chat_stop_rate_limit_per_ip_window_seconds: float = Field(default=60, gt=0)
    # Step 4.4: docs/SPEC.md §7's own named limit, word for word -- "3
    # escalations per rolling hour per visitor (a hard rejection with
    # 429 and Retry-After at the limit)". The window is a real "rolling
    # hour" (3600s), not an approximation -- matches this RateLimiter's
    # own sliding-window-log design (app/ratelimit.py), which has no
    # fixed-window boundary-straddling weakness to compensate for.
    escalation_rate_limit_per_visitor: int = Field(default=3, gt=0)
    escalation_rate_limit_window_seconds: float = Field(default=3600.0, gt=0)
    # Step 4.4: docs/SPEC.md §7 names "a per-tenant hourly email cap" but
    # no number -- an internal safety ceiling, the identical "generous
    # but bounded, no corresponding spec number" treatment as
    # `conversation_list_max_results` directly above (same reasoning:
    # bounds the worst case -- many visitors on one tenant each hitting
    # their own 3-per-hour cap could otherwise still flood that
    # tenant's own escalation inbox/SMTP relay without SOME tenant-wide
    # bound). Gates the EMAIL SEND only, never escalation-row creation
    # (docs/SPEC.md §7: the record "also appears in the dashboard
    # inbox" regardless of whether the email itself could be sent --
    # the identical "skipped_not_configured, never dropped" treatment
    # `app/notifications/service.py`'s own send_email() already gives a
    # deployment with no SMTP server configured at all).
    escalation_tenant_hourly_email_cap: int = Field(default=50, gt=0)
    # Task 4.1. The OVERALL wall-clock ceiling for one /chat turn (retrieve
    # + gate + the full answer stream), enforced via asyncio.wait_for() in
    # the endpoint itself -- a DIFFERENT timeout from chat_model_timeout_
    # seconds above, which (per that field's own header comment) is an IDLE
    # timeout between streamed chunks, not a total-duration one: a long,
    # genuinely-streaming answer can legitimately run well past 5s in total
    # as long as chunks keep arriving within that window, so a total-
    # duration cap must be a separate, larger number, not a reuse of that
    # field. 30s is a deliberately generous, round estimate (Qdrant's own
    # ~5s default timeout for retrieve(), plus the chat model's own
    # `.with_fallbacks()` chain potentially retrying through a slow primary
    # before engaging a secondary, plus margin) -- named here as an
    # estimate because no docs/SPEC.md §17 budget exists yet for "total
    # answer duration" (only first-token latency is budgeted there); Phase
    # 7's load test is the real check, same discipline as every other
    # unvalidated-under-load number in this file.
    chat_request_timeout_seconds: float = Field(default=30.0, gt=0)
    # Task 4.1 follow-up [SECURITY-adjacent: concurrency/availability]: the
    # turn-lease margin added on top of chat_request_timeout_seconds above
    # -- the lease duration (app/chat/routes.py's own _LEASE_MARGIN_
    # SECONDS... no, this constant) MUST exceed the overall per-turn
    # timeout, so a genuinely still-running turn can never lose its own
    # lease to expiry while it is still legitimately in flight (the
    # direct instruction this field exists to satisfy). 15s is a
    # deliberately generous round margin -- covers the real wall-clock gap
    # between "asyncio.timeout() fires inside _stream_turn()" and "the
    # lease's own release statement actually executes and commits" (DNS/
    # TCP/connection-pool-checkout time for that one release UPDATE),
    # plus slack for GC pauses or scheduler delay under load. Named as an
    # estimate, same discipline as chat_request_timeout_seconds itself.
    chat_lease_margin_seconds: float = Field(default=15.0, gt=0)
    # Task 3.7.a follow-up [correctness]: an EXPLICIT choice replacing an
    # inherited provider default -- `ChatOpenAI`'s own default `temperature`
    # is `None` (confirmed live, not assumed: nothing is sent, so OpenAI's
    # own non-zero server-side default applies), which real measurement
    # found actively harmful on its own: the expedited-shipping follow-up
    # pair's own `can_answer` rate was 9/20 (45%) at the provider default,
    # and 0/20 at `temperature=0` with the SAME (unfixed) prompt -- pinning
    # temperature alone does not fix ambiguity, it just makes whichever
    # answer the model's own distribution favours deterministic, which was
    # the WRONG answer here. Paired with the real fix (the answer prompt
    # now receives `prepared_query`, not the bare follow-up -- see
    # `_answer_node`), `temperature=0` measured 20/20 (100%), identical to
    # the unpinned-temperature result with the SAME prompt fix (20/20) --
    # i.e. once the prompt itself is no longer ambiguous, pinning
    # temperature costs nothing and buys full determinism. Range `[0, 2]`
    # matches OpenAI's own documented bound. **This is a baseline the
    # golden set (Step 3.7) measures and may retune, not a final number.**
    # `float | None` since Task 3.7.i: confirmed live at Task 3.7.h that
    # some real candidate models (`gpt-6.1-sol`, `o3`) reject a
    # `temperature` override outright ("Only the default (1) value is
    # supported") -- `None` means `get_chat_model()` omits the kwarg
    # entirely for that model, a single declarative Settings value per
    # model choice, never a per-model-name branch in code (see that
    # function's own docstring). A model run this way loses the
    # determinism this field's own history (above) exists to buy --
    # real, accepted whenever `None` is chosen, not a silent regression;
    # the golden-set eval's own N>=3 repeated-run ranges are exactly
    # what surfaces this as a measurable, reported quantity rather than
    # an invisible one.
    # Default moved to `None` at Task 3.7.i: the shipped `gpt-6-luna`
    # (like `gpt-6.1-sol`/`o3`, researched at 3.7.h/i) rejects
    # `temperature=0.0` outright ("Only the default (1) value is
    # supported"), confirmed live before shipping. **A real, accepted
    # determinism loss, not silently absorbed**: `can_answer`'s own N=3
    # repeated-run ranges (PROJECT_SPEC.md's own Task 3.7.i decision
    # log) are measurably wider for every reasoning-effort model tested
    # than they ever were for `gpt-4o` at `temperature=0.0` -- that
    # variance is the real, visible cost of this change, not a
    # regression nobody noticed.
    chat_model_temperature: float | None = Field(default=None, ge=0, le=2)
    # Task 3.7.i: OpenAI-specific, passed to the primary only (never the
    # Anthropic secondary, a different provider with no such parameter)
    # -- `None` omits the kwarg entirely for a model with no reasoning
    # tiers at all. "low" ratified for the shipped `gpt-6-luna`: its own
    # LOWEST setting, measured (not guessed) to pass every real
    # ship-rule condition Task 3.7.i's own full-set research checked.
    chat_model_reasoning_effort: str | None = Field(default="low")
    # Task 2.6.e: identifies the crawler on every robots.txt/sitemap/page
    # request it makes, matching the Step 2.6 decision entry's own
    # "Settings-configurable, not hardcoded" call. Kickoff decision #6
    # (2026-10-15): the real domain is softwareske.com, replacing the
    # samoflow.app placeholder this field carried while the domain was
    # still undecided -- still Settings-configurable, not baked into a
    # constant, per that same decision's own "every hostname comes from
    # Settings... never hardcoded" rule. Not a secret (no SecretStr): a
    # User-Agent string is sent in plaintext on every outbound request
    # by design, nothing to protect.
    crawl_user_agent: str = Field(default="SamoFlowBot/1.0 (+https://softwareske.com/bot)")
    # Task 2.6.e part 2: docs/SPEC.md §5.5 names "a delay" between
    # requests to the same host but gives no specific number (unlike the
    # fetch-limit numbers 2.3 owns, which §5.5 states exactly) -- same
    # "Settings-configurable, no spec number given" treatment as crawl_
    # user_agent above. 0.25s (250ms) matches ordinary polite-crawling
    # convention; retuned later with real numbers, like every other
    # estimate in this project (worker_poll_interval_seconds's own
    # precedent). The "2 concurrent requests per host" half of this same
    # §5.5 line IS spec-fixed (not described as configurable anywhere),
    # so it stays a plain module constant in app/ingest/crawl.py
    # (HostThrottle's own MAX_CONCURRENT_PER_HOST), not a Settings field.
    crawl_request_delay_seconds: float = Field(default=0.25, ge=0)
    # Task 2.6.e part 2: the "per-crawl safety cap" (docs/SPEC.md §5.5) --
    # deliberately NOT read from `plans.limits` here. Investigated live
    # before deciding, not assumed: `plans.limits` is confirmed (Task
    # 1.2.b's own header comment) to be "an unstructured JSONB blob...
    # until Phase 2/4 features define real limit fields," and `app/
    # plans/service.py`'s own `get_plan_limits()` is an explicit stub
    # (`raise NotImplementedError`, `TODO(4.3)`) -- real per-tenant plan-
    # limit reading is Task 4.3's own documented job, not this one's.
    # Building a real schema for `plans.limits` and a real "total
    # currently indexed pages" counting query here would preempt that
    # task's own design work and scope-creep well beyond "crawl wiring,
    # concurrency, and the heartbeat mechanism" (this task's own stated
    # scope). This Settings field is the smallest correct mechanism for
    # NOW (matching job_max_attempts's own identical-shape precedent) --
    # a single global safety ceiling, not yet per-plan. See the matching
    # Open marker: Task 4.3 should revisit this crawl handler's own page-
    # cap sourcing once real plan-limit reading exists. 500: a generous
    # but bounded safety default (this is a CEILING against a runaway
    # crawl, not a tight business limit) -- retuned later with real
    # numbers, like every other estimate in this project.
    crawl_page_cap: int = Field(default=500, gt=0)
    # Task 2.7.a: where the upload adapter's raw file bytes live on disk
    # (docs/SPEC.md §5.6: "originals stored privately") -- a plain
    # string, not pathlib.Path (matching every other path-like setting in
    # this project so far, e.g. database_url's own string treatment);
    # app/ingest/upload_storage.py turns it into a Path at the call site.
    # Mounted as a Docker volume shared between `api` and `worker`
    # (deploy/docker-compose.yml, Step 2.7 breakdown decision (b)) at the
    # same path this default names, so no override is needed in a real
    # deployment -- only tests override it (to a pytest tmp_path).
    # CONFIRMED at Task 2.7.f (2026-10-09): "/data/uploads" is the real,
    # accepted convention -- mirrored exactly in deploy/docker-compose.yml's
    # upload-storage volume mount, so the default needs no override in a
    # real deployment. (Resolves this field's own original ASSUMPTION,
    # recorded at Task 2.7.a -- see PROJECT_SPEC.md's Step 2.7 closure
    # summary.)
    upload_storage_path: str = Field(default="/data/uploads", min_length=1)
    # Task 2.7.a: the upload adapter's own per-file size ceiling
    # (docs/SPEC.md §5.4: "upload storage and file size... from the
    # plan") -- deliberately NOT sourced from `plans.limits` yet, same
    # already-established reason as `crawl_page_cap` directly above
    # (`plans.limits` is still an unstructured JSONB stub, Task 4.3's own
    # job; see the matching Open marker). 20MB (20 * 1024 * 1024): a
    # generous ceiling for a real PDF/DOCX/TXT/MD business document
    # (including a scanned, image-heavy PDF), while still bounding a
    # single upload job's worst-case parse time/memory -- distinct from,
    # and not to be confused with, `safe_fetch.py`'s own 5MB-per-page web-
    # fetch limit (2.3), which bounds a different thing (one HTML page
    # fetched over the network) for a different reason. Retuned later
    # with real numbers, like every other estimate in this project.
    upload_max_size_bytes: int = Field(default=20 * 1024 * 1024, gt=0)
    # Task 2.7.b: the per-member decompressed-size ceiling for a `.docx`
    # upload's own internal ZIP structure (app/ingest/upload_sniff.py's
    # sniff_file_type()) -- a zip-bomb defense, investigated live before
    # building (see the decision log entry): python-docx itself (via
    # `_ZipPkgReader.blob_for()` -> `zipfile.ZipFile.read()`) has ZERO
    # protection against a tiny-on-disk `.docx` whose own internal XML
    # part decompresses to gigabytes, confirmed live by constructing one
    # (a 200MB payload compressing to ~204KB, well under
    # `upload_max_size_bytes` above and so never caught by that check).
    # NOT sourced from `plans.limits` -- this isn't a tenant-facing
    # business limit at all (no docs/SPEC.md line names it), it is a pure
    # internal safety ceiling, same treatment as `job_stuck_after_seconds`
    # above (also Settings-configurable with no corresponding spec
    # number). 100MB: generous for any real Word document's own largest
    # single internal XML part (even a very large real document's own
    # word/document.xml is realistically a few MB at most), while still
    # meaningfully bounding a single decompression call's worst-case
    # memory use. Retuned later with real numbers, like every other
    # estimate in this project.
    docx_max_part_size_bytes: int = Field(default=100 * 1024 * 1024, gt=0)
    # Task 2.8.b: the database-sync adapter's own per-query row cap
    # (docs/SPEC.md §5.4: "maximum synced database rows... from the plan")
    # -- deliberately NOT sourced from `plans.limits` yet, same
    # already-established reason as `crawl_page_cap`/`upload_max_size_bytes`
    # above (`plans.limits` is still an unstructured JSONB stub, Task 4.3's
    # own job; see the matching Open marker). 10,000: generous for a real
    # reporting-style sync query, while still bounding a single job's
    # worst-case memory/transfer cost. Read ONCE by a real future caller
    # (2.8.e's own job handler, not yet built) and passed to app/ingest/
    # database_adapter.py's fetch_readonly_rows() as a plain parameter --
    # that primitive itself stays Settings-free, matching run_crawl()'s/
    # ingest_upload()'s own established precedent.
    db_sync_row_cap: int = Field(default=10_000, gt=0)
    # Task 2.8.b: the database-sync adapter's own per-query statement
    # timeout (docs/SPEC.md §5.6: "statement timeouts... apply") --
    # enforced via Postgres's own real `statement_timeout` mechanism
    # (fetch_readonly_rows(), confirmed live), not an application-level
    # asyncio.wait_for() race. NOT sourced from `plans.limits` -- this is
    # a pure internal safety ceiling, same treatment as `docx_max_part_
    # size_bytes` above (no corresponding per-tenant number in docs/
    # SPEC.md). 30s: generous for a real synchronous reporting query
    # against a tenant's own database, while still bounding a single
    # job's worst-case time spent waiting on one query.
    db_sync_statement_timeout_seconds: float = Field(default=30.0, gt=0)
    # Task 2.9.a: how often each tenant's own self-re-enqueuing
    # `scheduler_tick` job fires (docs/SPEC.md §5.4: the scheduler decides
    # which sources are due) -- same treatment as `job_stuck_after_seconds`/
    # `worker_poll_interval_seconds` above (plain configurable number,
    # gt=0). The coarsest real refresh_interval is "daily" -- the smallest
    # unit docs/SPEC.md §5.4 names -- so this does not need anywhere near
    # `worker_poll_interval_seconds`'s own 2-second responsiveness; it only
    # needs to be short enough that a source becoming due isn't noticeably
    # delayed relative to a day-granularity schedule, and long enough not
    # to add needless job-table churn per tenant. 300s (5 minutes):
    # generous on both counts -- retuned later with real numbers, like
    # every other estimate in this project.
    scheduler_tick_interval_seconds: float = Field(default=300.0, gt=0)
    # Task 2.9.b: the refresh-interval-vs-plan limit (docs/SPEC.md §5.4:
    # "refresh frequency... from the plan") -- deliberately NOT sourced
    # from `plans.limits` yet, same already-established reason as
    # `crawl_page_cap`/`upload_max_size_bytes`/`db_sync_row_cap` above
    # (`plans.limits` is still an unstructured JSONB stub, confirmed live:
    # app/plans/service.py's own get_plan_limits() is a bare
    # `raise NotImplementedError`, Task 4.3's own job; see the matching
    # Open marker). Named as one of app/ingest/models.py's own
    # REFRESH_INTERVAL_DELTAS keys, not a raw seconds count: this value is
    # compared against a source's own `refresh_interval` by looking both
    # up in that SAME dict (app/ingest/repository.py's
    # _refresh_interval_violates_plan()), rather than inventing a second,
    # parallel unit system for the identical four-value vocabulary (rule
    # 11). "daily" (the shortest/most-frequent real interval that
    # vocabulary names): the loosest possible floor, so every one of the
    # four values is allowed by default -- matches this project's own
    # "generous default, no real plan tiers exist yet" precedent
    # (crawl_page_cap=500, upload_max_size_bytes=20MB, etc.) rather than
    # arbitrarily restricting real usage before Task 4.3 gives this a real,
    # per-plan answer.
    refresh_interval_min_allowed: str = Field(default="daily")
    # Task 2.9.c: refresh-now's own dedicated cooldown (docs/SPEC.md
    # §5.4: "refresh-now (with a cooldown and a daily allowance from the
    # plan)") -- deliberately NOT one of the plan-limit stand-in family
    # directly above: docs/SPEC.md's own `plans.limits` enumeration (§5.4)
    # names "refresh-now allowance" explicitly but never a cooldown
    # duration, so this is a pure internal safety/UX mechanism (prevents a
    # human hammering the same "refresh now" button repeatedly), same
    # treatment as `docx_max_part_size_bytes`/`db_sync_statement_timeout_
    # seconds` above (Settings-configurable, no corresponding plan-limit
    # marker). 60s: long enough to absorb a rapid double-click/retry, short
    # enough that a genuine on-demand trigger still feels immediate to a
    # human -- retuned later with real numbers, like every other estimate
    # in this project.
    refresh_now_cooldown_seconds: float = Field(default=60.0, gt=0)
    # Task 2.9.c: the fifth Settings-stand-in sibling to `crawl_page_cap`/
    # `upload_max_size_bytes`/`db_sync_row_cap`/`refresh_interval_min_
    # allowed` above -- deliberately NOT sourced from `plans.limits` yet,
    # the identical already-established reason as all four (`plans.limits`
    # is still an unstructured JSONB stub, `get_plan_limits()` is still an
    # explicit `NotImplementedError` stub, Task 4.3's own job; see the
    # matching Open marker). Unlike `refresh_now_cooldown_seconds` above,
    # this genuinely IS one of docs/SPEC.md's own named `plans.limits`
    # entries ("refresh-now allowance"). 5: generous for a real on-demand
    # trigger (meant for occasional manual use, not a replacement for the
    # real schedule), while still bounding worst-case daily job-queue churn
    # from one source being refreshed on demand -- retuned later with real
    # numbers, like every other estimate in this project.
    refresh_now_daily_allowance: int = Field(default=5, gt=0)
    # Task 2.9.f: how often each tenant's own self-re-enqueuing
    # `reconcile` job fires (docs/SPEC.md §5.3: "a nightly reconcile
    # compares Postgres with Qdrant per tenant") -- mirrors
    # `scheduler_tick_interval_seconds` directly above exactly, down to
    # the "plain configurable number, gt=0" shape: the same self-re-
    # enqueuing mechanism (Task 2.9.a), a different job_type and a much
    # coarser cadence. 86400s (24 hours, i.e. "nightly" literally, not an
    # approximation) -- retuned later with real numbers, like every other
    # estimate in this project, most plausibly once real operational data
    # shows how many orphaned points actually accumulate per day.
    reconcile_interval_seconds: float = Field(default=86400.0, gt=0)
    # Task 4.2: how often each tenant's own self-re-enqueuing
    # `retention_sweep` job fires (docs/SPEC.md §13: "a nightly sweep
    # deletes expired requests" -- the identical "age out past the
    # tenant's own retention setting" cadence applies to chats too, per
    # §3's own "Chat retention... default 30 days" decision) -- mirrors
    # `reconcile_interval_seconds` directly above exactly, same shape,
    # same reasoning: 86400s (nightly, literally), retuned later with
    # real numbers.
    retention_sweep_interval_seconds: float = Field(default=86400.0, gt=0)
    # Task 4.2: the per-tenant chat-retention window (docs/SPEC.md §3:
    # "Chat retention: per website, set in the dashboard, default 30
    # days"; §13: "Retention ranges: Chats: 1 to 90 days (default 30)").
    # Real per-tenant retention lives in `tenants.config` (a dashboard-
    # editable JSONB field, Step 6.2's own future job to add real write
    # validation for) -- this Settings field is ONLY the PLATFORM-WIDE
    # DEFAULT used when a tenant's own config has no `retention_days` key
    # set yet (every tenant today, since no dashboard writes it), mirroring
    # the EXACT same "platform default in Settings, future per-tenant
    # override in tenants.config" shape `relevance_threshold` already
    # established (docs/SPEC.md §6.3). Clamped to the documented [1, 90]
    # range at READ time (`handle_retention_sweep()`, app/ingest/
    # job_handlers.py), not validated here -- a value READ from JSONB has
    # no pydantic Field to enforce it the way this Settings default does.
    default_chat_retention_days: int = Field(default=30, ge=1, le=90)
    # Step 4.4: docs/SPEC.md §7's own escalation retention -- "separate
    # setting, default 12 months (allowed range 1 to 24 months)". The
    # IDENTICAL "platform default in Settings, future per-tenant
    # override in tenants.config" shape as `default_chat_retention_days`
    # directly above (same reasoning, not repeated) -- a tenant has no
    # dashboard to write a `tenants.config["escalation_retention_months"]`
    # override yet, Step 6.2's own future job, same as that field's own.
    default_escalation_retention_months: int = Field(default=12, ge=1, le=24)
    # Task 4.3: how often app/main.py's own lifespan background task
    # flushes pending usage events to `usage_events` and reloads the
    # authoritative per-tenant monthly count (app/plans/usage.py's own
    # sync_usage_counters()) -- docs/SPEC.md §9's own "refreshes them
    # from usage_events about every minute (small overshoot accepted)".
    # 60s matches that wording directly; not a per-tenant/per-plan
    # value, since it is a platform-wide operational cadence, not a
    # business limit.
    usage_counter_sync_interval_seconds: float = Field(default=60.0, gt=0)
    # Task 4.3: the worker's own usage-alert sweep (handle_usage_alert_
    # sweep(), app/ingest/job_handlers.py) -- how often each tenant's
    # own self-re-enqueuing sweep fires, the same shape as retention_
    # sweep_interval_seconds above. docs/SPEC.md §9 names WHEN to email
    # (80%/100%, once per threshold per month) but no sweep cadence --
    # hourly is a deliberate, stated judgment call: frequent enough that
    # a tenant crossing a threshold hears about it within the hour, far
    # less job-table churn than scheduler_tick's own 5-minute cadence
    # (a quota threshold does not need near-real-time detection the way
    # a due content refresh does).
    usage_alert_sweep_interval_seconds: float = Field(default=3600.0, gt=0)
    # Task 4.3: docs/SPEC.md §14/§18's own "our own SMTP server, behind
    # a provider-neutral interface" -- all four OPTIONAL (matching
    # Settings.anthropic_api_key's own established "a deployment that
    # never configures this opt-in feature must still start" precedent):
    # a deployment with no SMTP server configured yet still starts and
    # serves chats normally, with alert emails simply not sent (logged,
    # `email_log` status "skipped_not_configured" -- app/notifications/
    # service.py's own send_email()) rather than failing startup for a
    # feature nothing has configured yet. 587 (STARTTLS), not 25: docs/
    # SPEC.md §14 itself names port 25 as commonly blocked by hosting
    # providers.
    smtp_host: str | None = Field(default=None)
    smtp_port: int = Field(default=587, gt=0, le=65535)
    smtp_username: str | None = Field(default=None)
    smtp_password: SecretStr | None = Field(default=None)
    # The sending address, distinct from smtp_username (some providers'
    # own auth user differs from the real From: address) -- required
    # only together with smtp_host (see app/notifications/service.py's
    # own is_smtp_configured() for the real "configured" definition).
    smtp_from_address: str | None = Field(default=None)
    # Step 4.5: docs/SPEC.md §13's own "phoenix.otel.register at startup".
    # Optional, matching smtp_host's own established "deployment without
    # this real service stood up yet must still start" shape -- a
    # deployment with no Phoenix container running yet still starts and
    # serves chats normally, with tracing simply disabled (app/telemetry/
    # service.py's own is_phoenix_configured()). The full OTLP traces
    # path, not just a host:port (e.g. "http://phoenix:6006/v1/traces"),
    # confirmed live against Arize's own self-hosting docs before this
    # field's shape was decided.
    phoenix_collector_endpoint: str | None = Field(default=None)
    # The project name every span is grouped under in the Phoenix UI --
    # "samoflow", this project's own internal product name throughout
    # the codebase already (e.g. crawl_user_agent's own "SamoFlowBot"),
    # not the kickoff-decided public domain (softwareske.com) -- a
    # project name and a domain name answer different questions.
    phoenix_project_name: str = Field(default="samoflow")
    # Step 4.5: docs/SPEC.md §13's own "a startup check that the
    # collector is reachable" -- an internal safety ceiling bounding
    # that one check's own real TCP connect attempt (app/telemetry/
    # service.py's own _check_collector_reachable()), matching this
    # project's own "generous but bounded, no corresponding spec
    # number" precedent (chat_model_timeout_seconds, docx_max_part_
    # size_bytes). Never blocks startup regardless of outcome -- only
    # bounds how long ONE already-non-fatal check can take.
    phoenix_startup_reachability_timeout_seconds: float = Field(default=3.0, gt=0)
    # [SECURITY-adjacent] Task 4.2 adversarial follow-up: a REAL finding,
    # not a speculative hardening -- `GET /api/v1/conversations`
    # (`TenantScopedRepository.list_conversations()`) had NO limit at
    # all, confirmed by reading the query directly: every conversation
    # a visitor has ever had was fetched and returned in one response,
    # unbounded. No number is named anywhere in docs/SPEC.md for this
    # ("previous chats" is described, no cap given), so this is an
    # internal safety ceiling, the identical "generous but bounded, no
    # corresponding spec number" treatment as `crawl_page_cap`/
    # `docx_max_part_size_bytes` -- 100 is generous for a real "previous
    # chats" panel in a small widget (no real UI shows more at once),
    # while bounding the worst case for a visitor (or a buggy/malicious
    # client hammering conversation creation) with an unusually large
    # history.
    conversation_list_max_results: int = Field(default=100, gt=0)
    # Task 3.1.c: retrieve()'s own final result count -- the `k` in
    # `retrieve(client_id, query, k)` (docs/SPEC.md §6.3), made
    # Settings-configurable rather than hardcoded so it can be retuned
    # later against the golden set (Step 3.7) without a code change.
    #
    # RATIFIED at 8 by Felix at Task 3.7.f (2026-10-14): the 3.7.c
    # recall@k table shows recall@5 already equals recall@8 on every
    # category except `multi_source`-all-documents (0.933 at @5 vs 1.000
    # at @8 -- a real, measured gap, not noise), so dropping to 5 would
    # cost exactly that one category's own worst-case recall for no
    # measured latency/token saving (none measured this task -- no
    # evidence to justify the cut). Kept at 8. See PROJECT_SPEC.md's own
    # decision log for the full table and reasoning.
    retrieval_k: int = Field(default=8, gt=0)
    # Task 3.1.c: the per-sub-query `Prefetch(..., limit=...)` candidate
    # pool size -- deliberately SEPARATE from `retrieval_k` above, not the
    # same number reused: docs/SPEC.md §6.3's own reranker bullet names
    # "roughly 20 to 50 candidates" as the pool a reranker would work
    # over, a different, larger quantity than the final result count a
    # caller actually receives. 40: the agreed starting point inside that
    # named range, Settings-configurable for the identical retune-later
    # reason as `retrieval_k`.
    retrieval_prefetch_limit: int = Field(default=40, gt=0)
    # Task 3.1.e: the reranker hook -- a literal flag, nothing behind it
    # yet, per direct instruction. docs/SPEC.md §6.3's own reranker
    # bullet is explicit that it is "enabled only if" the golden set
    # (Step 3.7, not yet built) measures the top-result quality as poor
    # -- there is no real signal yet to justify building (or picking) a
    # real reranker model/library, so this field exists purely to let
    # retrieve() branch on it correctly (see that function's own
    # docstring/body) without a code change once that signal exists.
    # RATIFIED off by Felix at Task 3.7.f (2026-10-14): 3.7.d's own real
    # end-to-end run measured citation hit rate 1.000 on every category
    # with a well-defined target (answerable, follow_up, multi_source,
    # injection) -- the evidence this field's own docstring named as the
    # enabling condition ("golden set measures the top-result quality as
    # poor") is the opposite of what was found: no retrieval-ranking
    # problem exists for a reranker to solve. Default False: unchanged
    # behavior from Task 3.1.c/3.1.d.
    reranker_enabled: bool = False
    # Task 3.4.d [SECURITY]: the gate's own relevance floor, read against
    # retrieve()'s own best_dense_score (app/retrieval/service.py) -- the
    # BEST dense cosine similarity anywhere in the tenant's corpus for a
    # query, not the fused hybrid top hit's own dense score (confirmed
    # live these can differ). Below this floor, or zero retrieved chunks,
    # the gate routes straight to the fixed fallback, no model call.
    #
    # RATIFIED at 0.20 by Felix at Task 3.7.f (2026-10-14), replacing the
    # original provisional default this field carried (PROJECT_SPEC.md's
    # own Step 3.4.d research entry, a one-off research experiment) with
    # real golden-set evidence (3.7.c's own gate sweep, 90 synthetic
    # items): at 0.20,
    # `off_topic` passes the gate 80% of the time, yet `can_answer`
    # itself still refused 15/15 `off_topic` items in the real 3.7.d
    # end-to-end run -- a false gate-PASS costs one extra model call that
    # `can_answer` already catches. 0.30 is the sweep's own knee
    # (`off_topic` drops to 27% passing, every answerable category still
    # 1.00 recall) but `answerable`'s own minimum score (0.342) leaves
    # only a 0.04 margin above it -- too close to a real answerable
    # visitor's own worst case to adopt without real-site data. A false
    # gate-OUT (an answerable visitor told "I don't know") costs more
    # than a false pass, so the lower, safer threshold is kept.
    # **Candidate range to test on a real tenant site: 0.25-0.30.**
    #
    # Revisit trigger (synthetic-only, per the 3.7.c §9 marker): re-check
    # against a real tenant site before pilot -- every number above comes
    # from one synthetic corpus, not yet confirmed to transfer.
    #
    # Specific to the CURRENT embedding model/version: this is a raw
    # cosine-similarity floor against `EMBEDDING_MODEL` (app/ingest/
    # embedding.py, currently "text-embedding-3-small") and the content
    # currently indexed under `EMBEDDING_VERSION` (app/ingest/
    # web_adapter.py, currently "v1") -- a different embedding model has
    # a different cosine-similarity distribution entirely, and this
    # number would not transfer. Re-tune whenever either changes.
    gate_relevance_threshold: float = Field(default=0.20, ge=0.0, le=1.0)
    # Step 6.1: dashboard login (docs/SPEC.md §12). Two independent
    # RateLimiter instances (app/ratelimit.py, reused as-is), the same
    # "sliding window log, per-IP checked first" shape session creation
    # already established (app/auth/routes.py) -- not a new mechanism.
    # The per-ACCOUNT instance gates every attempt (success or failure
    # alike) for one normalized email, which is what gives "rate-limited
    # login with lockout" its real teeth: 10 attempts per 15 minutes is
    # already a strong deterrent against online password guessing, while
    # essentially never affecting a legitimate human (nobody logs in more
    # than 10 times in 15 minutes under normal use) -- the simplest
    # correct design, not a separate persisted failed-attempt counter.
    dashboard_login_rate_limit_per_ip: int = Field(default=20, gt=0)
    dashboard_login_rate_limit_per_ip_window_seconds: float = Field(default=300.0, gt=0)
    dashboard_login_rate_limit_per_account: int = Field(default=10, gt=0)
    dashboard_login_rate_limit_per_account_window_seconds: float = Field(default=900.0, gt=0)
    # Step 6.1: server-side dashboard sessions (docs/SPEC.md §12: "server-
    # side sessions... revocable, with idle and absolute expiry" -- this
    # task's own direct instruction, docs/SPEC.md itself names no
    # numbers). Idle: how long a session may sit untouched before a
    # request is refused regardless of its own absolute expiry -- 30
    # minutes, a conventional admin-dashboard idle window. Absolute: the
    # hard ceiling on a session's own lifetime regardless of activity,
    # forcing a real re-login periodically even for an actively-used
    # session -- 12 hours, long enough to cover one real working day
    # without re-login, short enough that a stolen, still-active session
    # cannot be ridden indefinitely. Both retuned later with real usage
    # data, like every other estimate in this project.
    dashboard_session_idle_seconds: float = Field(default=1800.0, gt=0)
    dashboard_session_absolute_seconds: float = Field(default=43200.0, gt=0)
    # Step 6.2: the dashboard's own install-snippet screen needs to show
    # the tenant a real `data-base-url` value (widget/loader/src/
    # config.js: "data-base-url has no built-in fallback... a missing
    # data-base-url is treated as a configuration error" -- deliberately
    # mandatory on the widget's own side, so the dashboard cannot just
    # omit it either). This is a PLATFORM-WIDE constant (wherever this
    # deployment's own API is publicly reachable), not per-tenant, and
    # no Caddy/public-hosting routing exists yet to derive it from (that
    # is Phase 7's own job) -- so it is a plain operator-set value, left
    # unset by default rather than guessed. The install-snippet endpoint
    # (app/dashboard/tenant_routes.py) returns None when unset and the
    # screen tells the tenant to ask their platform admin, rather than
    # ever showing a wrong URL a tenant might paste onto a live site.
    public_api_base_url: str | None = Field(default=None)

    @field_validator("public_api_base_url", mode="before")
    @classmethod
    def _empty_public_api_base_url_means_unset(cls, value: object) -> object:
        # Same established convention as anthropic_api_key's own before-
        # validator: an empty string (.env.example's own "not configured
        # yet" default for this field) means unset, not a validation
        # error.
        if value == "":
            return None
        return value

    @field_validator("public_api_base_url")
    @classmethod
    def _require_https_url_if_set(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname:
            raise ValueError("PUBLIC_API_BASE_URL must be an https:// URL with a hostname")
        return value

    # [SECURITY] L1 PART C (#7) (out-of-band Phase 7 batch, 2026-10-17):
    # the api service's OWN public hostname (deploy/Caddyfile's
    # {$API_HOSTNAME} -- read here from the SAME env var, not a second
    # copy, so the two can never drift apart). app/main.py's own
    # TrustedHostMiddleware (production only) rejects any request whose
    # Host header is not this value or "127.0.0.1" (the api service's
    # own healthcheck, deploy/docker-compose.yml, which curls itself
    # over loopback and therefore never carries this public hostname).
    # Unset in development/test (no Caddy, no public hostname to
    # validate against) -- required in production, see
    # _require_api_hostname_in_production below.
    api_hostname: str | None = Field(default=None)
    # [SECURITY] L1 PART C follow-up (advisor review, 2026-10-17): a real
    # production-breaking bug in the original PART C commit -- the
    # dashboard SPA's own same-origin `/api/*` calls (deploy/Caddyfile's
    # {$DASHBOARD_HOSTNAME} site, `handle /api/* { reverse_proxy
    # api:8000 }`) arrive at the api service with Host = the DASHBOARD
    # hostname, never the api one (Caddy's reverse_proxy passes the
    # original Host header through unchanged by default -- confirmed
    # against Caddy 2.10.2's own docs when PART C was first built, but
    # the conclusion was never applied to the dashboard's own site
    # block). `api_hostname` alone in TrustedHostMiddleware's allow-list
    # therefore 400'd every dashboard API call in production -- login,
    # CSRF, sources, settings, all of it. Confirmed live before this fix
    # existed (this task's own report). Same `API_HOSTNAME` env var
    # treatment as `api_hostname` above: read from the SAME value
    # deploy/Caddyfile already uses for `{$DASHBOARD_HOSTNAME}`, one
    # source of truth, required in production (see
    # _require_dashboard_hostname_in_production below).
    dashboard_hostname: str | None = Field(default=None)
    # [SECURITY] L1 PART C (#7): the ONE trust boundary for the forwarded-
    # client-IP headers app/client_ip.py reads -- a request's own stated
    # client IP is honoured only when the immediate TCP peer resolves (by DNS,
    # not a hardcoded CIDR -- deploy/docker-compose.yml assigns no
    # static subnet) to this hostname, Caddy's own service name on the
    # compose network. "caddy" is a safe default (matches deploy/
    # Caddyfile's own service name); an operator who runs without Caddy
    # in front (or a Caddy under a different service name) can repoint
    # this. An unresolvable hostname (the default "caddy" in
    # development/test, where no such service exists at all) already
    # degrades safely to "nothing is trusted, use the raw socket peer",
    # so this field needs no separate explicit disable value.
    trusted_proxy_hostname: str = Field(default="caddy")

    @field_validator("api_hostname", mode="before")
    @classmethod
    def _empty_api_hostname_means_unset(cls, value: object) -> object:
        # Same established convention as public_api_base_url's own
        # before-validator above.
        if value == "":
            return None
        return value

    @field_validator("dashboard_hostname", mode="before")
    @classmethod
    def _empty_dashboard_hostname_means_unset(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("database_url")
    @classmethod
    def _require_postgres_psycopg_scheme(cls, value: SecretStr) -> SecretStr:
        parts = urlsplit(value.get_secret_value())
        database_name = parts.path.lstrip("/")
        if parts.scheme != POSTGRES_SCHEME or not parts.hostname or not database_name:
            # Message names only the required scheme, never the value under
            # validation, so a bad DSN's password cannot leak here.
            raise ValueError(
                "DATABASE_URL must be a PostgreSQL URL using the "
                f"'{POSTGRES_SCHEME}' scheme, with a hostname and a "
                "database name (SQLAlchemy 2.0 defaults plain "
                "'postgresql://' to psycopg2, which is not installed)."
            )
        return value

    @field_validator("qdrant_url")
    @classmethod
    def _require_http_scheme(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("QDRANT_URL must be an http or https URL.")
        return value

    @field_validator("qdrant_api_key")
    @classmethod
    def _require_a_clean_header_value(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        # Message names only the requirement, never the value under
        # validation, so a bad key cannot leak here.
        if not raw:
            raise ValueError("QDRANT_API_KEY must not be empty.")
        # Duplication check after 1.4.a/b: delegates to the same helper
        # jwt_signing_key/jwt_signing_key_previous use below -- one
        # whitespace/control-char check shared by every secret field that
        # needs it, instead of a second copy of the same `any(...)` line.
        _require_no_whitespace_or_control_chars(
            raw,
            "QDRANT_API_KEY",
            "it is sent as an HTTP header value; a newline would allow header injection",
        )
        return value

    @field_validator("qdrant_read_only_api_key")
    @classmethod
    def _require_a_clean_read_only_header_value(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw:
            raise ValueError("QDRANT_READ_ONLY_API_KEY must not be empty.")
        _require_no_whitespace_or_control_chars(
            raw,
            "QDRANT_READ_ONLY_API_KEY",
            "it is sent as an HTTP header value; a newline would allow header injection",
        )
        return value

    @field_validator("jwt_signing_key")
    @classmethod
    def _require_a_clean_signing_key(cls, value: SecretStr) -> SecretStr:
        _require_no_whitespace_or_control_chars(
            value.get_secret_value(), "JWT_SIGNING_KEY", "it is used as UTF-8 bytes to sign tokens"
        )
        return value

    @field_validator("db_connection_encryption_key")
    @classmethod
    def _require_a_clean_encryption_key(cls, value: SecretStr) -> SecretStr:
        _require_no_whitespace_or_control_chars(
            value.get_secret_value(),
            "DB_CONNECTION_ENCRYPTION_KEY",
            "it is used as a pgcrypto passphrase",
        )
        return value

    @field_validator("openai_api_key")
    @classmethod
    def _require_a_clean_openai_api_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        # Message names only the requirement, never the value under
        # validation, so a bad key cannot leak here.
        if not raw:
            raise ValueError("OPENAI_API_KEY must not be empty.")
        _require_no_whitespace_or_control_chars(
            raw,
            "OPENAI_API_KEY",
            "it is sent as an HTTP Authorization header value; a newline would allow "
            "header injection",
        )
        return value

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _empty_anthropic_api_key_means_not_configured(cls, value: object) -> object:
        # Same shape as jwt_signing_key_previous's own before-validator
        # immediately below: an empty string (the ".env.example default
        # when the secondary provider isn't configured" shape) means "not
        # configured", converted to None here -- not a validation error.
        if value == "":
            return None
        return value

    @field_validator("chat_model_reasoning_effort", mode="before")
    @classmethod
    def _empty_chat_model_reasoning_effort_means_omitted(cls, value: object) -> object:
        # Same shape as anthropic_api_key's own before-validator above --
        # a third occurrence of this established convention, not a new
        # one: an empty string means "no reasoning-effort override",
        # converted to None here so `get_chat_model()` omits the kwarg.
        if value == "":
            return None
        return value

    @field_validator("anthropic_api_key")
    @classmethod
    def _require_a_clean_anthropic_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            _require_no_whitespace_or_control_chars(
                value.get_secret_value(),
                "ANTHROPIC_API_KEY",
                "it is sent as an HTTP header value; a newline would allow header injection",
            )
        return value

    @field_validator("jwt_signing_key_previous", mode="before")
    @classmethod
    def _empty_previous_signing_key_means_none(cls, value: object) -> object:
        # An empty string (the common ".env.example placeholder when not
        # rotating" shape) means "no previous key" -- converted to None
        # here, before Field(min_length=32)'s own check would otherwise
        # reject "" as too short. Runs before type coercion, so `value` is
        # whatever pydantic-settings read from the environment (always a
        # str) -- anything else is passed through untouched and left to the
        # field's own type validation to reject.
        if value == "":
            return None
        return value

    @field_validator("jwt_signing_key_previous")
    @classmethod
    def _require_a_clean_previous_signing_key(
        cls, value: SecretStr | None
    ) -> SecretStr | None:
        if value is not None:
            _require_no_whitespace_or_control_chars(
                value.get_secret_value(),
                "JWT_SIGNING_KEY_PREVIOUS",
                "it is used as UTF-8 bytes to verify tokens",
            )
        return value

    @model_validator(mode="after")
    def _require_previous_signing_key_differs_from_current(self) -> "Settings":
        # Message names only the requirement, never either value, so a
        # match cannot leak which value the operator typed twice.
        if (
            self.jwt_signing_key_previous is not None
            and self.jwt_signing_key_previous.get_secret_value()
            == self.jwt_signing_key.get_secret_value()
        ):
            raise ValueError("JWT_SIGNING_KEY_PREVIOUS must differ from JWT_SIGNING_KEY.")
        return self

    # [SECURITY] L1 (out-of-band Phase 7 batch, 2026-10-17), Decision D
    # (D-pilot-placeholder-secrets-gate): under APP_ENV=production, every
    # secret-class setting (app/secrets_policy.py's own SECRET_CLASS_
    # SETTINGS, found by introspecting this class's own SecretStr-typed
    # fields -- see backend/tests/test_secrets_policy_guard.py) must not be
    # a known placeholder/test value, and must meet its own stated minimum
    # length where one exists. "Missing or empty" is already covered,
    # unconditionally, by each required field's own existing field_
    # validator above (an empty SecretStr is rejected in every environment,
    # not just production) -- this validator's own job is the part that is
    # legitimate in development/test but must fail loudly in production:
    # a real "change-me"/"test-..."/"smoke-..."/"placeholder-..." value
    # that WOULD otherwise start up successfully. api, worker and migrate
    # all go through this identical check: every one of them constructs
    # Settings only via get_settings() (app/config.py's own test_config_
    # guard.py enforces this), so this one model_validator covers all three
    # processes with no separate wiring at each entrypoint.
    @model_validator(mode="after")
    def _reject_weak_secrets_in_production(self) -> "Settings":
        if self.app_env != "production":
            return self
        raw_values: dict[str, str | None] = {}
        for setting in SECRET_CLASS_SETTINGS:
            value = getattr(self, setting.attr)
            if value is None:
                raw_values[setting.env_var] = None
            else:
                raw_values[setting.env_var] = value.get_secret_value()
        violations = check_raw_secret_values(raw_values)
        if violations:
            # Message names only the variable(s), never a value -- see
            # check_raw_secret_values()'s own docstring.
            raise ValueError(" ".join(violations))
        return self

    # [SECURITY] L1 PART C (#7) (out-of-band Phase 7 batch, 2026-10-17):
    # api_hostname has no app-breaking default in production the way
    # trusted_proxy_hostname's "caddy" does -- an unset value there would
    # leave app/main.py's TrustedHostMiddleware with no real hostname to
    # allow, locking out every genuine request while looking, from the
    # outside, like a silent misconfiguration rather than a loud startup
    # failure. Same "fail fast at startup" discipline as every other
    # required-in-production setting on this class.
    @model_validator(mode="after")
    def _require_api_hostname_in_production(self) -> "Settings":
        if self.app_env == "production" and self.api_hostname is None:
            raise ValueError(
                "API_HOSTNAME must be set in production (deploy/Caddyfile already "
                "requires it; app/main.py's TrustedHostMiddleware needs it too)."
            )
        return self

    # [SECURITY] L1 PART C follow-up (advisor review, 2026-10-17): same
    # fail-loud discipline as api_hostname above -- an unset value here
    # would leave TrustedHostMiddleware with no way to allow the
    # dashboard's own same-origin /api/* calls, the exact production
    # outage this follow-up fixes.
    @model_validator(mode="after")
    def _require_dashboard_hostname_in_production(self) -> "Settings":
        if self.app_env == "production" and self.dashboard_hostname is None:
            raise ValueError(
                "DASHBOARD_HOSTNAME must be set in production (deploy/Caddyfile "
                "already requires it; app/main.py's TrustedHostMiddleware needs "
                "it too, since Caddy forwards the dashboard's own Host header "
                "unchanged on every /api/* call)."
            )
        return self

    def database_url_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.database_url.get_secret_value()

    def qdrant_api_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.qdrant_api_key.get_secret_value()

    def qdrant_read_only_api_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.qdrant_read_only_api_key.get_secret_value()

    def jwt_signing_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.jwt_signing_key.get_secret_value()

    def jwt_signing_key_previous_str(self) -> str | None:
        # The one explicit call that unwraps the secret. Never log this.
        if self.jwt_signing_key_previous is None:
            return None
        return self.jwt_signing_key_previous.get_secret_value()

    def db_connection_encryption_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.db_connection_encryption_key.get_secret_value()

    def openai_api_key_str(self) -> str:
        # The one explicit call that unwraps the secret. Never log this.
        return self.openai_api_key.get_secret_value()

    def anthropic_api_key_str(self) -> str | None:
        # The one explicit call that unwraps the secret. Never log this.
        # None means "not configured" -- matching jwt_signing_key_previous_
        # str()'s own identical optional shape.
        if self.anthropic_api_key is None:
            return None
        return self.anthropic_api_key.get_secret_value()

    def smtp_password_str(self) -> str | None:
        # The one explicit call that unwraps the secret. Never log this.
        # None means "not configured" -- matching anthropic_api_key_str()'s
        # own identical optional shape (an SMTP server requiring no auth
        # at all is a real, legitimate configuration too).
        if self.smtp_password is None:
            return None
        return self.smtp_password.get_secret_value()


class SettingsError(Exception):
    # Raised by get_settings() instead of pydantic's ValidationError. Its
    # message is a plain str captured from str(exc) before this exception is
    # raised, so it carries no reference to the original ValidationError
    # (no __cause__/__context__) and no raw input value.
    pass


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        message = str(exc)
    # Raised after leaving the except block on purpose: this is no longer
    # "while handling" exc, so Python does not implicitly chain __context__.
    raise SettingsError(message)
