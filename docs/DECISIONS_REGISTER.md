# SamoFlow — Decisions Register

Built 2026-10-15 because a 2026-09-28 CANDIDATE decision (shadcn/ui as the dashboard-tooling
candidate, plus the npm supply-chain setup due at Step 5.1) was missed at the Phase 5-7
kickoff: nothing surfaced candidate decisions or instructions addressed to future steps in
one place. This register is that place, going forward. See PROJECT_SPEC.md §2 rules 19-22
for the process this register is now part of (register sweep at every kickoff, cite
supersession, update in the same commit, every CANDIDATE/forward-commitment is also a §9
marker).

**Search method** (exhaustive, not sampled): the full text of PROJECT_SPEC.md §5 "Decisions
made" (every line, ~6,250 dense lines, read sequentially in ~30-70 line windows across four
parallel passes split by line range: 105-1230, 1230-3600, 3600-5400, 5400-6823) and §7
"Current task and next task" (6374-6823, containing Step 1.1-2.4's own task lists and closure
summaries), plus a direct, sequential read of the whole of §9 "Open markers" (6823-6967), §10
"Phase 4 kickoff decisions" (6969-7027) and §11 "Phase 5-7 planning and kickoff decisions"
(7028-7119), plus docs/SPEC.md in full. Supplemented with whole-file greps for the literal
phrases "due at", "owner" (case-insensitive), "deferred to", "revisit at", "must be done by",
"no task scheduled", "to be scheduled", "whoever first", "tentatively", "CANDIDATE",
"RESOLVED", "ratified" and "§9 marker" to cross-check nothing was missed and to verify
resolution status of every marker found. Every decision/commitment below carries its real
PROJECT_SPEC.md source line.

---

## 1. Decisions table

One row per distinct decision (a ratified choice, an approved deviation, or a design
committed to — not a task-list row describing work still to do). `status` is one of
APPROVED, CANDIDATE, or SUPERSEDED-by-`<id>`.

### Phase 0-1 (Preparation, Foundations)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-0 | 2026-09-19 | Baseline decisions log | ~40 baseline decisions ratifying docs/SPEC.md verbatim (tenancy, retention, sources, search, specialists, escalation, quotas, hosting, etc.) | APPROVED | all | PROJECT_SPEC.md:107-144 |
| D-1.1-deps | 2026-09-19/20 | Backend dependency pins | fastapi/uvicorn/pydantic-settings/alembic/sqlalchemy/psycopg/pytest/ruff pinned and approved (rule 8) | APPROVED | 1.1 | :145-148 |
| D-1.1-naming | 2026-09-20 | Product rename | Renamed to SamoFlow (brand only); code/package/images keep the neutral name `widgetplatform` until a rename task is scheduled | CANDIDATE (rename task unscheduled) | 1.1 | :157 |
| D-1.1-docker | 2026-09-20 | Base images & build | `python:3.12.15-slim-trixie`, `postgres:18.2-trixie`, `qdrant/qdrant:v1.19.1` pinned; multi-stage build strips pip/ensurepip from both venv and system Python | APPROVED | 1.1 | :173-177, 6855 |
| D-1.1-ci | 2026-09-20 | CI security posture | No native Postgres service container — CI reuses the Compose test-db as sole source of truth; actions SHA-pinned; safe-for-fork-PR permissions | APPROVED | 1.1 | :192-198 |
| D-1.1.o-tools | 2026-09-20 | Supply-chain tooling | uv (lockfiles) + pip-audit + Grype (anchore/scan-action) + Dependabot approved (rule 8); Trivy rejected (compromised release) | APPROVED | 1.1 | :204 |
| D-1.1.o-gaps | 2026-09-20/25 | Dependabot known gaps | No Python/pip ecosystem entry (can't regen hashed locks); no `docker` ecosystem entry for backend (ARG-FROM bug, upstream issue open); temporary `ignore: postgres` rule for an anchor/alias updater bug | APPROVED (documented gaps) | 1.1 | :206, 222-224 |
| D-1.1-hooks | 2026-09-26 | Marker tooling | Built `make hooks` (not ruff TD rules — incompatible with the ASSUMPTION/UNCERTAIN/NOTE convention) covering TODO/ASSUMPTION/UNCERTAIN/NOTE | APPROVED | 1.1c | :220 |
| D-1.2-breakdown | 2026-09-26 | Schema/tenancy design | Per-domain models; UUID PKs everywhere; `site_keys` synthetic PK + unique `key`; `ON DELETE CASCADE` tenants→children; `plans.limits` unstructured JSONB; users/memberships deferred to 6.1; pool sizing deferred to Phase 7 | APPROVED | 1.2 | :234 |
| D-1.2-repo | 2026-09-27 | Tenant isolation mechanism | `TenantScopedRepository` (frozen dataclass, no `tenant_id` param on any method) is the sole enforcement point for engineering rule §21 | APPROVED | 1.2 | :244 |
| D-1.3-breakdown | 2026-09-28 | Qdrant collection design | One shared collection + `client_id` filter (not per-tenant collections); `is_tenant` index + `source_id` index; dense size 1536 as ASSUMPTION pending 2.5 confirmation; qdrant-client REST (not gRPC) approved (rule 8) | APPROVED | 1.3 | :252 |
| D-1.3-repair | 2026-09-28 | Schema-mismatch policy | `ensure_collection()`: additive repair for an absent index only; any other mismatch (size/distance/modifier/index-params) raises, never silently replaces | APPROVED | 1.3 | :265 |
| D-1.3-hnsw | 2026-09-28 | Multitenancy HNSW tuning | `hnsw_config.m=0` + per-tenant `payload_m`, recommended by Qdrant for multi-tenant scale — NOT built now | CANDIDATE | owner Phase 7 | :271, 6879 |
| D-1.3-filter | 2026-09-28 | Hybrid isolation filter placement | Filter required in BOTH dense+sparse Prefetch (mandated); outer-query filter added as defense-in-depth (confirmed no behavior change) | APPROVED | 1.3→3.1 | :275-287 |
| D-1.3-guard | 2026-09-28 | Qdrant read-path guard | Import-confinement (who can hold a client) is primary; method-name classification only applies within that allow-list; snapshot/recreate/delete methods forbidden everywhere | APPROVED | 1.3 | :293-295 |
| D-shadcn | 2026-09-28 / **APPROVED 2026-10-15 (Felix)** | Dashboard component library | shadcn/ui on Radix, built exactly to this row's own pre-set constraints (Vite not Next.js; Radix pinned as the one primitives library; official registry only; pinned CLI; generated diffs reviewed; a live strict-CSP Playwright test, extended at this alignment task to exact-shape tolerance, not a free-text phrase) | **APPROVED, SUPERSEDES D-113-dashboard-tooling** | 6.1 | :306; commit `29b444b`; docs/progress/dashboard.md |
| D-npm-supply-chain | 2026-09-28 | npm supply chain | npm arrives with the widget bundler at 5.1, not 6.1; lockfile+`npm ci`, `npm audit`, a dependabot npm entry, SHA-pinned actions due at 5.1 | **RESOLVED** at Step 5.1 (confirmed live: `docs/progress/widget.md`) | 5.1 | :306 |
| D-design-direction | 2026-09-28 | Widget/dashboard UX | Light-theme-only v1; stop-mid-stream mechanics; 100%-quota fallback messaging; Inbox escalation-fields-only v1; recent-auth scope; WCAG 2.1→2.2 AA superseded | APPROVED | docs/SPEC §11/§12 | :307 |
| D-1.4-breakdown | 2026-09-28 | JWT/session design | PyJWT approved (rule 8); `JWT_SIGNING_KEY` required ≥32 chars + optional `_PREVIOUS` for rotation; `kid` = first-12-hex(sha256(key)); no refresh endpoint; a wrong secret behaves exactly like no secret (no oracle); byte-identical 403 across 4 failure causes; dynamic per-site-key CORS; `tenants.status` fixed to active/suspended | APPROVED | 1.4 | :308 |
| D-1.4.h-cache | 2026-09-29 | Status-cache design | `StatusCache` lives on `app.state` (not a module `lru_cache`) so each app instance gets a fresh cache; keyed by `(tenant_id, site_key_id)`; a deleted row caches as `None` | APPROVED | 1.4 | :361-370 |
| D-1.4.i-repo-dep | 2026-09-29 | Tenant scoping enforcement | `get_tenant_repository` takes no `Request` param — `tenant_id` can only come from the verified JWT identity, never a forgeable field | APPROVED | 1.4 | :379-385 |
| D-1.5-breakdown | 2026-09-29 | Rate limiting design | In-process/in-memory limiter (Redis deferred to the Phase 7 scale-out trigger); two counters on `/session`: `(IP, site_key)` 10/min + a broader per-IP 30/min; `request.client.host` as caller identity until a real proxy exists; a distinct 429 body shape from 403 | APPROVED | 1.5 | :435 |
| D-1.5.a-algo | 2026-09-29 | Rate-limiter algorithm | Sliding-window-log (not fixed-window, not token/leaky-bucket); O(K) full-sweep pruning per check, flagged for reconsideration before reuse at `/chat`'s higher call frequency | APPROVED | 1.5 | :436 |
| D-1.5.c-keyencoding | 2026-09-29 | [SECURITY] Key collision fix | `(IP, site_key)` limiter key netstring-encoded to prevent a crafted `site_key` colliding two different `(ip, site_key)` pairs | APPROVED | 1.5 | :450 |
| D-1.6-breakdown | 2026-09-30 | Phase 1 acceptance design | Clause-level splitting of 3 straddling §16 lines; a fresh, independent full-suite run as its own gate; `ensure_collection()` ownership left genuinely undecided, not settled in passing | APPROVED | 1.6 | :479 |

### Phase 2 (Ingestion)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-2.1-breakdown | 2026-09-30 | Ingestion schema + job queue | `sources`/`documents`/`db_connections`/`jobs` schema; `jobs.source_id` nullable; `db_connections` credentials pgcrypto-encrypted behind a guarded accessor; `SELECT...FOR UPDATE SKIP LOCKED` job claiming | APPROVED | 2.1 | :6654-6660 |
| D-2.1-audit-bug | 2026-10-04 | [SECURITY] Log-leak fix | `worker.py`'s per-iteration failure handler was logging the full exception via `exc_info=True` despite a comment claiming otherwise — fixed, proven with a live log-capture test | APPROVED (fixed) | 2.1 | :6678-6684 |
| D-2.2-breakdown | 2026-10-04 | Domain verification design | `verified_domains` status vocabulary pending/verified/revoked; `audit_log.actor` a plain string (no FK until 6.1's `platform_admin` exists); DNS-only verification now (file/meta-tag deferred until after 2.3's SSRF guard exists); global domain-uniqueness across tenants; `dnspython` approved (rule 8) | APPROVED | 2.2 | :740-758 |
| D-2.2-superuser | 2026-10-04 | [SECURITY] `audit_log` enforcement reversal | The DB role `widgetplatform` is a **superuser** — `REVOKE UPDATE, DELETE` on `audit_log` has **zero effect**. The AST guard (2.2.h) is the ONLY real enforcement until the Phase 7 role split lands; a permanent regression test documents this honestly | APPROVED (documented residual risk) | 2.2 → Phase 7 | :744, 6869 |
| D-2.2-endpoint-dropped | 2026-10-04 | Admin endpoint scope correction | A docs/SPEC.md-vs-PROJECT_SPEC.md conflict was resolved in favor of docs/SPEC.md, then reversed before the step closed: the admin endpoint for `revoke_domain()` was **not** built — dropped from Step 2.2 entirely, deferred whole to Step 6.1's real `platform_admin` role | SUPERSEDED-by-D-6.1-admin (owner Step 6.1) | 2.2 → 6.1 | :752, 754, 6850, 6856 |
| D-2.3-breakdown | 2026-10-04 | SSRF guard design | Fail-closed IP classifier covers IPv4+IPv6; pin-to-validated-IP via httpcore's `sni_hostname` extension; DNS rebinding closed completely by IP-literal connect; 2.3 owns all three fetch-limit numbers (5MB/15s/3-redirect), not deferred to 2.6; `httpx` promoted dev→runtime dependency | APPROVED | 2.3 | :952-966 |
| D-2.4-breakdown | 2026-10-05 | Extraction/chunking design | `tiktoken`/`bs4`+`lxml`/`pypdf` (BSD, not PyMuPDF-AGPL)/`python-docx` approved (rule 8); HTML cleanup lives in 2.4 as shared logic; `documents.status` vocabulary pending/extracted/failed; "nearly empty" = &lt;50 words; `content_hash` on normalized text, not raw bytes; 512/64 chunk params as module constants, not Settings | APPROVED | 2.4 | :1062-1082 |
| D-2.4-docfindings | 2026-10-05 | Extraction findings | `cl100k_base` tokenizer fetched over network on first use (not bundled) | CANDIDATE | owner Phase 7 | :6897 |
| D-2.5-openai-sdk | 2026-10-05 | Embedding API | `openai` SDK approved as a new runtime dependency for dense embeddings | APPROVED | 2.5 | :1274 |
| D-2.5-point-id | 2026-10-05 | Point ID scheme | `uuid5(POINT_ID_NAMESPACE, doc_id:chunk_index:embedding_version)`, deterministic, never duplicates | APPROVED | 2.5 | :1280 |
| D-2.5-fastembed | 2026-10-05 | Sparse embedding | `fastembed` BM25 approved as a new runtime dependency | APPROVED | 2.5 | :1282 |
| D-2.5-namespace | 2026-10-06 | Point ID namespace | `POINT_ID_NAMESPACE` UUID generated once, hardcoded forever | APPROVED | 2.5+ | :1338 |
| D-2.8-postgres-only | 2026-10-09 | DB connector scope | Database-sync adapter is Postgres-only; MySQL/MariaDB deferred (flagged deviation from docs/SPEC.md's "MySQL/MariaDB and PostgreSQL first") | APPROVED (flagged deviation) | 2.8 | :1962 |
| D-2.9-reconcile-algo | 2026-10-21 | Reconcile algorithm | Scrolls Qdrant points and asks whether Postgres justifies each (not the reverse, which was circular) | APPROVED | 2.9 | :2469 |
| D-2.9-suspended-ingestion | 2026-10-19 | Suspended-tenant ingestion | Three independent layers stop a suspended tenant's sources from refreshing in the background | APPROVED | 2.9 | :2335 |
| D-2.10-crawl-verification-fix | 2026-10-25 | Crawl domain-verification gap | `crawl`-type source creation now rejected for an unverified domain at creation + handler layers (was previously enforced only at per-page write time) | APPROVED (fix) | 2.10 | :2574 |
| D-2.11-tentative | 2026-10-27 | Database-adapter staleness cleanup | A new task, tentatively "2.11", to build missing-row cleanup for the `database` adapter — researched, explicitly NOT built | **SUPERSEDED-by-D-pilot-dbsync-disable** | was unscheduled; now owner 6.3 (disable) / POST-PILOT (cleanup) | :2667, 6920 |
| D-3.1-retrieval-sizing | 2026-10-29 | Retrieval sizing | `retrieval_k=8`, `retrieval_prefetch_limit=40` defaults, retunable against the golden set | APPROVED | 3.1 | :2720 |

### Phase 3 (Agent engine)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-3.2-tenant-id-config | 2026-11-02 | Tenant identity propagation | `tenant_id` travels via the LangGraph config (never state), stored as a raw `uuid.UUID` (never stringified) to avoid checkpoint-metadata persistence | APPROVED [SECURITY] | 3.2/3.3 | :2970-2972 |
| D-3.3-router-shape | 2026-11-04 | Router implementation | A plain sequential if/return chain, not a data-driven predicate list (for monkeypatch testability) | APPROVED | 3.3 | :3041, 3056 |
| D-3.3-retrievedchunk | 2026-11-03 | Graph-state chunk type | A project-owned `RetrievedChunk` TypedDict replaces the raw `qdrant_client.ScoredPoint` in `AgentState` | APPROVED (retroactive fix) | 3.3 | :3010-3012 |
| D-3.4-chat-model-pkg | 2026-11-08 | Chat-model construction | `init_chat_model` via `langchain`; primary OpenAI; secondary Anthropic only if the key is set | APPROVED | 3.4 | :3169-3171 |
| D-3.4-answer-format | 2026-11-08/11 | Answer streaming format | `.bind_tools()` + incremental JSON parsing (not `.with_structured_output()`); field order `can_answer`/`answer`/`used_chunk_ids` is load-bearing (reordered at the latency follow-up) | APPROVED | 3.4 | :3223, 6947 |
| D-3.4-anthropic-two-key-gate | 2026-11-11 | Anthropic fallback gating | Secondary wired in only if BOTH `anthropic_api_key` is set AND `chat_model_secondary_verified=true` | APPROVED | 3.4 → Phase 7 | :3272-3285 |
| D-3.4-sanitiser-redesign | 2026-11-12 | Markdown answer sanitiser | Deleted the recognise-and-hold sanitiser (found broken); replaced with a single-pass backslash-escape of 6 characters | APPROVED | 3.4.c | :3547 |
| D-3.4-gate-signal | 2026-11-13 | Retrieval gate signal | Gate on the best dense-cosine score anywhere in the tenant corpus (not the fused top hit) | APPROVED | 3.4.d | :3987 |
| D-3.4-gate-threshold | 2026-11-13 | Gate relevance threshold | `gate_relevance_threshold` default 0.20; stated ASSUMPTION, tuned for real at Step 3.7's golden set | APPROVED (provisional) | 3.4.d → 3.7 → 7.5 | :4090 |
| D-3.4-unsafe-abusive-deferred | 2026-10-13 | Unsafe/abusive guardrail | Felix: real unsafe/abusive detection deferred for the pilot (moderation-endpoint latency too costly on the critical path); replacement owned by Step 6.4 | **SUPERSEDED-by-D-postpilot-admin-overrides** (owner now POST-PILOT/6.5) | 3.4 → 6.4 → POST-PILOT/6.5 | :4696, 6964 |
| D-3.5-joining | 2026-10-13 | Follow-up rewrite | Join (concatenate) the prior visitor question instead of a model-rewrite step, for the pilot — recorded deviation from docs/SPEC.md §6.3 | APPROVED (deviation) | 3.5 | :5283 |
| D-3.6-deferred | 2026-10-13 | Tool wrapper/MCP | Step 3.6 deferred out of Phase 3, bundled with §19's "tenant-supplied tool servers" | APPROVED | 3.6 → post-v1 | :5384 |
| D-3.4g-latency-reorder | 2026-10-13 | Answer schema / latency | [SECURITY] `Answer` schema field reorder to cut time-to-first-token | APPROVED | 3.4 follow-up | :5034 |
| D-3.7a-two-corpora | 2026-10-13 | Golden set | Two frozen corpora: a messy synthetic set, and (later) Felix's real crawled site | APPROVED | 3.7 → 7.5 | :5413 |
| D-3.7a-size-thresholds | 2026-10-13 | Golden set | 15-20 items/category; structural checks at a 100% bar; hallucination gated only on a &gt;5pp regression pre-calibration | APPROVED | 3.7 | :5414-5415 |
| D-3.7a-automation-order | 2026-10-13 | Eval automation | On-demand runner first, then CI-on-prompt-change, then nightly, in that order | APPROVED | 3.7 | :5416 |
| D-3.7a2-prompt-referent | 2026-10-13 | Answer prompt | `_answer_node` now uses `prepared_query`, not the raw message, for the answer prompt | APPROVED | 3.5/3.7 | :5445-5446 |
| D-3.7a2-temperature-0 | 2026-10-13 | Chat model | `chat_model_temperature` default 0.0 | SUPERSEDED-by-D-3.7i-temperature-generic | 3.7 | :5447 |
| D-3.7b-schema | 2026-10-13 | Golden set | YAML schema with per-category `@model_validator` cross-field rules | APPROVED | 3.7 | :5470 |
| D-3.7b-passage-not-chunkid | 2026-10-13 | Golden set | `expected_sources` stores verbatim evidence passages, not chunk ids | APPROVED (supersedes 3.7.a's own sketch) | 3.7 | :5470 |
| D-3.7b-escalation-absent | 2026-10-13 | Golden set | `escalation` category deliberately absent (no mechanism existed pre-4.4) | APPROVED (forward marker, owner 4.4 then re-owned — see §9 marker 6967) | 3.7/4.4 | :5474, 6967 |
| D-3.7c-english-only-ratified | 2026-10-13 | Language scope | v1 English-only RATIFIED with a named revisit trigger | APPROVED (supersedes 3.7.b marker) | 3.7 | :5496, 6836 |
| D-3.7c-site-corpus-stays-synthetic | 2026-10-13 | Golden set | Stays synthetic-only; tuning must be re-checked on a real site before pilot | APPROVED → now owner Step 7.5 | 3.7 → 7.5 | :5497 |
| D-3.7d-injection-canaries | 2026-10-13 | Injection evals | Replaced unverifiable injection goals with 3 mechanically-detectable canary/URL/role-change techniques | APPROVED | 3.7 | :5566-5568 |
| D-3.7d-groundedness-uncalibrated | 2026-10-13 | Eval gating | Groundedness reported UNCALIBRATED, not a gate, until Felix hand-labels | APPROVED (standing) | 3.7 | :5574 |
| D-3.7f-gate-threshold-keep | 2026-10-14 | Tuning | Gate threshold stays 0.20; candidate 0.25-0.30 for a real-site retest | APPROVED, revisit trigger: real tenant site (now Step 7.5) | 3.7 → 7.5 | :5668 |
| D-3.7f-retrieval-k-keep-8 | 2026-10-14 | Tuning | `retrieval_k` stays 8 | APPROVED, revisit at 7.5 | 3.7 → 7.5 | :5684 |
| D-3.7f-reranker-stays-off | 2026-10-14 | Tuning | Reranker stays off — no evidence of poor top-result quality | APPROVED, revisit at 7.5 | 3.7 → 7.5 | :5687 |
| D-3.7f-joining-confirmed | 2026-10-14 | Tuning | Joining (not rewrite) confirmed correct on real golden-set data | APPROVED | 3.5/3.7 | :5690 |
| D-3.7f-eval-methodology-fix | 2026-10-14 | Process | Prompt/schema research must run on the full golden set including previously-100% categories, never a narrow subset | APPROVED, extends the eval rule in §3 | 3.7 | :5717 |
| D-3.7g-rel07-relabel | 2026-10-14 | Golden set | `rel-07` relabeled `answerable` (was mislabelled unanswerable); backfilled `rel-16` | APPROVED | 3.7 | :5740-5767 |
| D-3.7h-ms-items-rewritten | 2026-10-14 | Golden set | `ms-10`/`ms-13` rewritten (mislabelled); `ms-05` confirmed correctly labelled | APPROVED | 3.7 | :5837-5838 |
| D-3.7i-quota-numbers | 2026-10-14 | Pricing/quotas | Plan quotas: 10,000 msgs/mo ($20 plan), 25,000 msgs/mo ($50 plan), ~21% headroom below break-even | APPROVED, owner Step 6.2 (Plan-row creation) | 4.3 → 6.2 | :5908-5910, 6825 |
| D-3.7i-fallback-deferred-phase7 | 2026-10-14 | Chat model | Chat-model fallback mechanism explicitly deferred to Phase 7 | APPROVED | Phase 7 | :5914, 6826 |
| D-3.7i-temperature-generic | 2026-10-14 | Chat model | `chat_model_temperature` → `float\|None`; omitted generically when `None`; new `chat_model_reasoning_effort` | APPROVED, supersedes D-3.7a2-temperature-0 | 3.7 | :5931 |
| D-3.7i-model-gpt6luna | 2026-10-14 | Chat model | `gpt-6-luna` (`reasoning_effort="low"`) shipped as primary — ~22x cheaper, better quality | APPROVED, revisit at 7.5 | 3.7 → 7.5 | :5938 |
| D-3.7i-judge-gpt6luna | 2026-10-14 | Judge model | Judge switched to `gpt-6-luna` (cheaper, modest accuracy edge) — creates a same-model self-preference risk | APPROVED, standing risk marker | 3.7 | :5942, 6828 |

### Phase 4 (Streaming, metering, handoff)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-4.1-stream-mode | 2026-10-14 | Chat endpoint | Stream mode widened to `["custom","updates"]`; `"messages"` never subscribed | APPROVED | 4.1 | :6056 |
| D-4.1-error-taxonomy | 2026-10-14 | Chat endpoint | Three fixed error codes: `model_error`, `timeout`, `dependency_unavailable` | APPROVED | 4.1 | :6057 |
| D-4.1-idempotency | 2026-10-14 | Chat endpoint | `client_message_id` replay via `add_messages` id-replace + `_check_idempotency()` | APPROVED | 4.1 | :6059 |
| D-4.1fu-lease | 2026-10-14 | Chat endpoint | The advisory lock is replaced by a short lease on `conversations.lease_until`/`lease_token` | APPROVED, supersedes the transaction-scoped-advisory-lock kickoff decision | 4.1 | :6084, 6029 |
| D-4.1fu-eval-determinism | 2026-10-14 | Eval tooling | `make eval` made deterministic via exact-search HNSW + an explicit tiebreak sort; the tolerance band is removed entirely | APPROVED | 3.7 | :6096 |
| D-4.2-vid-aware-lookup | 2026-10-14 | Conversations | `get_conversation_by_id()` made vid-aware (optional kw-only param) | APPROVED | 4.2 | :6114 |
| D-4.2-retention-sweep | 2026-10-14 | Retention | `retention_sweep` job type, prunes the checkpoint thread before the row | APPROVED | 4.2 | :6116 |
| D-4.3-scope-planlimits-type-only | 2026-10-14 | Scope | `PlanLimits` type built; 5 ingestion-side call sites deliberately NOT rewired (wide blast radius, out of scope) | APPROVED, re-scopes 5 markers to "whoever next revisits per-plan ingestion limits" | 4.3 | :6142 |
| D-4.3-quota-system-design | 2026-10-14 | Quotas | In-memory per-tenant counter, synced ~1 min, 503-until-ready gate | APPROVED | 4.3 | :6148-6150 |
| D-4.3-smtp-stdlib | 2026-10-14 | Email | Real SMTP via stdlib `smtplib` + `asyncio.to_thread`, no new dependency | APPROVED | 4.3 | :6154 |
| D-4.3-email-retry-simplified | 2026-10-14 | Email | Hourly retry-until-success, NOT docs/SPEC.md §14's full 5-step exponential backoff | APPROVED simplification | 4.3 | :6156, 6962 |
| D-PA-push-gate-rule18 | 2026-10-15 | Process | §2 rule 18: never push with `make test-all` failures, or before the image-job steps pass locally | APPROVED, standing rule | — | :6191 |
| D-44-escalation-detection | 2026-10-15 | Escalation | YAML phrase/pattern + "two fallbacks in a row" structural signal both built | APPROVED | 4.4 | :6223 |
| D-44-reason-fixed-string | 2026-10-15 | Escalation | `reason` is a single fixed `"visitor_requested"` string, not category-specific | APPROVED, marker: dashboard can't distinguish categories later (owner Step 6.4) | 4.4 → 6.4 | :6225, 6253 |
| D-44-limits | 2026-10-15 | Escalation | 3/hr/visitor, an hourly per-tenant email cap, one-open-per-conversation | APPROVED | 4.4 | :6231 |
| D-45-phoenix-loopback-only | 2026-10-15 | [SECURITY] | Phoenix bound to `127.0.0.1` only, no Caddy route, SSH-tunnel access only | APPROVED | 4.5 | :6291 |
| D-45-exclude-spans-fix | 2026-10-15 | Tracing | `exclude_spans=["receive","send"]` to avoid a per-chunk span explosion (library-default bug) | APPROVED | 4.5 | :6295 |

### Phase 5-7 kickoff (2026-10-15)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-113-widget-bundler | 2026-10-15 | 5.1 | esbuild for the widget bundler | APPROVED | 5.1 | :6265 |
| D-113-dashboard-tooling | 2026-10-15 | 6.1 | Vite + React + CSS Modules, design tokens as CSS custom properties | **SUPERSEDED-by-D-shadcn (2026-10-15, Felix)** — Vite/React kept, CSS Modules replaced by shadcn/ui on Radix per D-shadcn's own pre-set constraints; §12's visual system (tokens as CSS custom properties, no hard-coded colours, light/dark from the first screen, self-hosted fonts, no third-party requests) still applies, just expressed through shadcn's own theme layer instead of hand-written CSS Modules | 6.1 | :6266; commit `29b444b` |
| D-113-dashboard-auth-pilot | 2026-10-15 | 6.1 | Single-owner login for the pilot (feature-reduced, NOT security-reduced): argon2id, rate-limited lockouts, `HttpOnly`+`Secure`+`SameSite` cookies, CSRF protection, the one owner account seeded by a CLI command | APPROVED | 6.1 | :6267 |
| D-113-email-postmark | 2026-10-15 | Email | Postmark SMTP relay behind the existing `send_email()`; a dedicated sending subdomain | APPROVED | Phase 7 | :6268 |
| D-113-hosting-provider-agnostic | 2026-10-15 | Hosting | Standing constraint: any Ubuntu LTS VPS, no provider-specific APIs/services, ever | APPROVED, standing | all future deploy steps | :6269 |
| D-113-domains-softwareske | 2026-10-15 | Domains | Root domain `softwareske.com` decided; hostnames always from Settings/Caddyfile, never hardcoded | APPROVED, standing rule | all future steps | :6270 |
| D-113-monitoring | 2026-10-15 | Monitoring | A hosted uptime checker + a cron disk/memory script via the existing `send_email()` | APPROVED | 7.3 | :6271 |
| D-113-backups | 2026-10-15 | Backups | S3-compatible storage, encrypted before upload, keys backed up separately from the data | APPROVED | 7.3 | :6272 |
| D-113-parallel-track-protocol | 2026-10-15 | Process | Two git worktrees (`track/widget`, `track/dashboard`) ratified; full protocol in §11.5 | APPROVED | 5.x/6.x | :6276 |

### Step 6.1 close-out (2026-10-15)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-6.1-auth-library | 2026-10-15 | Auth library | fastapi-users (v15.0.5) selected as the maintained auth library docs/SPEC.md §12 requires (rule 8): its own `BaseUserManager`/`DatabaseStrategy`/`CookieTransport` building blocks are reused directly, extended for SHA-256-hashed + idle-expiring session tokens (the library's own default stores tokens raw and has no idle expiry) | APPROVED | 6.1 | commit `7519325`; docs/progress/dashboard.md |
| D-6.1-admin | 2026-10-15 | Admin identity | The temporary `admin_api_key` mechanism (`app/admin/dependencies.py`) is removed outright, not extended; `platform_admin` is fastapi-users' own `is_superuser` flag on the same login system (no second, parallel admin-identity mechanism); `revoke_domain()` and every `/api/v1/admin` endpoint re-pointed at `current_platform_admin` sessions | APPROVED, RESOLVES the Task 2.2.f/2.2.g §9 markers (PROJECT_SPEC.md:6872, 6878) | 6.1 | commit `1202972` |
| D-6.1-site-key-generator | 2026-10-15 | Site key generation | `generate_site_key()` (`app/tenancy/repository.py`) — a real, cryptographically secure `pk_live_…` generator (`secrets.token_urlsafe`), closing the Task 1.4.e marker naming no generator existed | APPROVED | 6.1 | commit `1202972` |
| D-6.1-origin-domain-normalise | 2026-10-15 | Normalisation | `normalize_origin()` (`app/auth/origin.py`) / `normalize_domain()` (`app/ingest/domain_verification.py`) — `site_keys.allowed_origins` and `verified_domains.domain` normalised on save to `scheme://host[:port]`, lowercase, no path, no wildcard | APPROVED | 6.1 | commit `1202972` |
| D-6.1-pilot-plans | 2026-10-15 | Pilot plan rows | `cmd_seed_plans` (`app/dashboard/admin_cli.py`) creates the two real pilot Plan rows (Starter 10,000 msgs/mo, Growth 25,000 msgs/mo), closing the "no Plan-row creation mechanism exists" gap (D-3.7i-quota-numbers' own owner) | APPROVED, RESOLVES D-3.7i-quota-numbers' Step-6.2 ownership early (built at 6.1, not deferred) | 6.1 | commit `1202972` |
| D-6.1-cors-same-origin | 2026-10-15 | Dashboard/admin CORS | Same-origin SPA+API serving (dashboard SPA and `/api/v1/dashboard`, `/api/v1/admin` share one origin in production via Caddy) — no CORS middleware needed for either surface; `OPTIONS` continues to return 405, which is correct, not an open gap, since no cross-origin preflight for these endpoints ever occurs under this design | APPROVED, RESOLVES the CORS/OPTIONS §9 marker for the dashboard/admin half (PROJECT_SPEC.md:6882) — the widget's own dynamic per-site-key CORS (Task 1.4.g) is unrelated and untouched | 6.1 | commit `7519325` |
| D-6.1-session-cookie-design | 2026-10-15 | Session/CSRF cookies | `__Host-session` (Secure, Path=/, no Domain, HttpOnly, SameSite=Strict) + `__Host-csrf` (same prefix, not HttpOnly, double-submit) — the `__Host-` prefix is the real defense against cookie tossing from a sibling subdomain, proven live (adversarial pass, Step 6.1) | APPROVED | 6.1 | commit `7519325` |
| D-6.1-recent-auth | 2026-10-15 | Recent-auth dependency | `require_recent_auth(max_age_seconds=900.0)` (`app/dashboard/dependencies.py`) built now, consumed by no endpoint yet — later steps (delete tenant, change site keys/origins, replace DB credentials, team changes) wire it in as they're built, per docs/SPEC.md §12's own "recent authentication" requirement | APPROVED (built ahead of its first consumer, deliberately) | 6.1 | commit `7519325` |
| D-6.1-session-field-na | 2026-10-15 | `/session` field renegotiation | The pre-existing 6.1 forward commitment "confirm/renegotiate the `/session` response's own field names before building the dashboard against them" (PROJECT_SPEC.md:6887) turned out not to apply: the dashboard's own auth system (`/api/v1/dashboard/auth/*`) is wholly separate from the widget's `/session` endpoint and never calls or depends on its field names | RESOLVED, NOT APPLICABLE — the widget-track contract check (D-shadcn's own sibling marker set) already separately verified `/session`'s own fields for the widget's use | 6.1 | commit `7519325` |
| D-6.1-test-layout-placeholders | 2026-10-15 | `test_layout_placeholders.py` | Narrowed a second time (after the widget track's own two narrowings for `widget/loader`/`widget/chat`) to empty `_STILL_PLACEHOLDER_READMES`/`_PLACEHOLDER_ROOTS` — `dashboard/` now has real Vite+React+shadcn tooling too, so no folder this file tracks is still a placeholder | APPROVED, RESOLVES the Step 5.1/owner-6.1 §9 marker (PROJECT_SPEC.md:6897) for real | 6.1 | commits `a98cd09`, `195de3a`; rebase conflict resolved in `2295fa9` |
| D-6.1-recent-auth-scope | 2026-10-15 | Auth feature scope | D-113-dashboard-auth-pilot's own named security floor (argon2id; rate-limited lockouts, both per-IP and per-account; `__Host-`-prefixed Secure+HttpOnly+SameSite=Strict cookies; double-submit CSRF on every state-changing route; a CLI-seeded owner account with no shipped default password, never logged) confirmed built exactly as specified, with a full adversarial pass (credential stuffing/lockout bypass, session fixation, CSRF, cookie scope/tossing, cross-tenant access, logout/revocation, argon2 parameter downgrade, seed-CLI password leakage) converted to committed regression tests | APPROVED, CONFIRMS D-113-dashboard-auth-pilot was built, not merely planned | 6.1 | commit `7519325`; docs/progress/dashboard.md |
| D-6.1-exact-csp-tolerance | 2026-10-15 | CSP test tolerance (this alignment task) | The login/Dialog strict-CSP Playwright test's prior tolerance (a free-text regex, `/Applying inline style violates/i`) replaced with an exact structural match on the real `securitypolicyviolation` DOM event (`effectiveDirective === "style-src-elem"`, `blockedURI === "inline"`, `disposition === "enforce"`, `sourceFile` under the page's own `/assets/*.js`) — proven live with a deliberately injected, differently-shaped second violation (an external `<script src>`) that the new tolerance does NOT swallow | APPROVED | 6.1 (alignment) | commit (this task); `dashboard/e2e/csp.spec.ts` |
| D-6.1-session-invalidation-hook | 2026-10-15 | Session invalidation on credential/role change (this alignment task) | `UserManager.on_after_update()` (`app/dashboard/auth_backend.py`) invalidates every one of a user's existing `DashboardSession` rows whenever an update changes `password` or `is_superuser` — hooked at `BaseUserManager.update()`, the one real path any future password-reset or platform_admin-removal CLI command would be built on, rather than three separate call sites in three commands that do not exist yet (building those three now would itself be new feature work, which this follow-up explicitly excludes) | APPROVED. **Residual gap RESOLVED at Step 6.2**: real `reset-password`/`set-platform-admin`/`unset-platform-admin` commands now exist (`app/dashboard/admin_cli.py`), each built on `UserManager.update()`, so the hook covers every real mutation path; `seed_cli.py`'s own message now points at them | 6.1 (alignment) → 6.2 | commit (this task); `app/dashboard/auth_backend.py`, `app/dashboard/repository.py`, `backend/tests/test_dashboard_auth_backend.py`; closed by `app/dashboard/admin_cli.py` (Step 6.2) |
| D-6.1-fastapi-users-maintenance-mode | 2026-10-15 | Auth library maintenance status (this alignment task) | Checked live (PyPI + GitHub README + GitHub API, all dated 2026-10-15): fastapi-users is explicitly in **maintenance mode** per its own README — "we'll continue to provide security updates and dependency maintenance, no new features will be added"; a successor toolkit is in development with no name or date yet. Latest release v15.0.5 (2026-03-27, matches what this project pins); repo not archived, last pushed 2026-08-17; PyPI classifier `Development Status :: 5 - Production/Stable`. Not swapped in this task, per direct instruction | APPROVED (informational), re-check owner Phase 7 (§9 marker below) | 6.1 (alignment) | https://pypi.org/project/fastapi-users/ ; https://github.com/fastapi-users/fastapi-users (README) ; https://api.github.com/repos/fastapi-users/fastapi-users ; checked 2026-10-15 |

### Pilot-scope decisions (2026-10-15, this task)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-pilot-dbsync-disable | 2026-10-15 | [SECURITY] Database-sync scope | Database-sync source type disabled for the pilot — creation rejected server-side, not offered in the dashboard | APPROVED, supersedes D-2.11-tentative | owner 6.3; cleanup POST-PILOT | PROJECT_SPEC.md §5 "Felix's decisions, ratified (2026-10-15)" |
| D-pilot-dbconn-cleanup | 2026-10-15 | Database-sync credential cleanup | A deleted database source's `db_connections` credentials are cleaned up in 6.3's own delete flow | APPROVED, re-owns the Task 2.9.e/Phase-2-close-out marker | owner 6.3 | same |
| D-pilot-token-cap-agent-steps | 2026-10-15 | Abuse/cost controls | Per-message token cap and max-agent-steps limit join the Phase 7 batch; per-site burst cap stays POST-PILOT | APPROVED, partially re-scopes the Task 1.5.f/4.3 marker and the agent-steps portion of the 6945 tool-server marker (for the current flat-cap case only — see note) | owner 7.1/7.3 batch (burst cap: POST-PILOT) | same |
| D-pilot-placeholder-secrets-gate | 2026-10-15 | Go-live gate | Rejecting placeholder secrets when `APP_ENV=production` becomes a mandatory Step 7.4 go-live checklist item | APPROVED, re-owns the Task 2.2.g/1.3.a1/1.4.a/2.1.a/3.1.b marker | owner 7.4 | same |
| D-pilot-image-secret-scan | 2026-10-15 | Security scanning | Image secret scanning joins Step 7.2's full security and injection run | APPROVED, re-owns the Task 1.1.o marker | owner 7.2 | same |
| D-pilot-onboarding-step | 2026-10-15 | New step | New Step 7.5 "Pilot onboarding": the pilot tenant's own golden set, plus re-checking the model choice, the 0.20 gate threshold, `retrieval_k=8` and the reranker-off decision on real content | APPROVED, new step; consolidates the Task 3.7.a/3.7.f/3.7.g/3.7.i "whoever onboards the first real pilot tenant" markers | owner 7.5 (new) | same |
| D-postpilot-admin-overrides | 2026-10-15 | Admin screen scope | Per-tenant gate-threshold override, per-tenant model choice, and the async unsafe/abusive review are re-owned to POST-PILOT, bundled with 6.5 Platform admin | APPROVED, supersedes the "Phase 6's admin screen"/"Step 6.4" ownership on the Task 3.4.d/3.4.b-research/3.4.g markers | owner POST-PILOT / whoever onboards the second tenant (same as 6.5) | same |
| D-postpilot-admin-no-tenant-shape | 2026-10-15 (found at Step 6.1; recorded at this alignment task) | Admin account shape | A real finding, found live while testing `POST /auth/login` (`test_admin_routes.py`): there is no "platform-admin-only, with no tenant membership at all" account shape today — `/auth/login` unconditionally requires a real `Membership` row to succeed, which is correct, intentional behaviour for the pilot (every real admin account `seed_cli.py --platform-admin` creates also gets a membership, so the one hand-picked pilot admin can always log in) | APPROVED, new POST-PILOT entry, bundled with 6.5 Platform admin | owner POST-PILOT / whoever onboards the second tenant or needs a non-tenant-owning admin (same as 6.5) | docs/progress/dashboard.md's "A second real bug found live" entry |

### Phase 5 continuation (5.3-5.6, 2026-10-15, this task)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-53-history-no-citations | 2026-10-15 | History rendering | A past conversation's own stored text renders through the SAME renderer with an EMPTY citation allow-set, never a separate "history mode" — any surviving link syntax structurally cannot match, so it always renders as plain text | APPROVED | 5.3 | `docs/progress/widget.md` |
| D-54-support-details-no-new-path | 2026-10-15 | Support-details fallback | The fallback message (docs/SPEC.md §6.1) needs no dedicated UI surface — it streams through the existing `token`/`citations`/`done` sequence like any other answer, already covered by Step 5.2's own renderer | APPROVED | 5.4 | same |
| D-55-focus-trap-mobile-only | 2026-10-15 | Focus trap scope | "A focus trap in panels" (this batch's own task instruction) is built to match docs/SPEC.md §11's own, more specific design direction: a real trap + host-scroll-lock on the mobile (<480px) sheet only; the desktop panel stays non-modal (no trap) | APPROVED, a deliberate reading where the task instruction and the spec's own design direction could otherwise be read as conflicting | 5.5 | same |
| D-55-cssom-style-not-csp-restricted | 2026-10-15 | [SECURITY-adjacent] Theming mechanism | `element.style.setProperty()` is NOT subject to CSP style-src (unlike a `<style>` tag, a `style=""` attribute, or a Constructable Stylesheet's `replaceSync()`, which all are) — confirmed live with a direct, isolated probe before relying on it | APPROVED, verified live | 5.5 | same |
| D-55-no-dashboard-preview | 2026-10-15 | Scope | The dashboard live-preview mode (docs/SPEC.md §12, `postMessage`-based) is deliberately not built — a new message-passing attack surface and a cross-track coupling point, for a feature the pilot doesn't need (save-then-view-on-a-test-page works today) | APPROVED, deferred POST-PILOT | owner: whoever builds the dashboard preview after pilot | PROJECT_SPEC.md §9 |

**Note on D-shadcn vs D-113-dashboard-tooling**: this is the exact gap that prompted this
register. The 2026-10-15 Phase 5-7 kickoff (D-113-dashboard-tooling) ratified "Vite + React +
CSS Modules" for the dashboard without citing or superseding the still-open shadcn/ui
CANDIDATE recorded 2026-09-28 (D-shadcn, owner Step 6.1) — exactly the silent-divergence
failure PROJECT_SPEC.md §2 rule 20 now forbids. **RESOLVED at this alignment task (2026-10-15,
Felix): D-shadcn is APPROVED, explicitly superseding D-113-dashboard-tooling, citing this
supersession (rule 20) rather than silently diverging a second time** — built in commit
`29b444b`: Vite + React + TypeScript (kept), shadcn/ui on Radix (replaces CSS Modules), every
one of D-shadcn's own pre-set constraints honoured (official registry only, pinned CLI,
generated diffs reviewed before commit, components added one at a time, never used in the
widget, a live strict-CSP Playwright test — see docs/progress/dashboard.md's own "CSP and the
Dialog scroll-lock" entry, and this alignment task's own exact-tolerance tightening of that
same test, item 3 below).

### Step 7.3 prep (ops track, 2026-10-15)

Tool choices made under rule 8 while building the Caddyfile/prod compose override/backup and
monitoring scripts (docs/progress/ops.md has the full build log). All PREP, not final —
docs/DECISIONS_REGISTER.md §2's own 7.3 entry below records final verification as owned by
whoever runs 7.3/7.4 on the real server.

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-73-caddy-pin | 2026-10-15 | Proxy image | `caddy:2.10.2`, verified against a real manifest list (`docker manifest inspect`) — the proxy kickoff decision #5 already named (Caddy over Nginx); this is the version pin | APPROVED | 7.3 | `deploy/docker-compose.prod.yml`; docs/progress/ops.md |
| D-73-backup-encryption-age | 2026-10-15 | Backup encryption tool | `age` (asymmetric: a public recipient key encrypts, a private key — stored separately, never in the backup bucket — decrypts) over `gpg`/OpenSSL `enc`: the asymmetric split is what makes "the pgcrypto key and other secrets stored SEPARATELY from the data" (this task's own requirement) a structural property of the encrypting side needing no secret at all, not an operator discipline to maintain by hand | APPROVED | 7.3 | `ops/backup/lib.sh`; docs/progress/ops.md |
| D-73-s3-tool-awscli | 2026-10-15 | S3-compatible upload tool | The AWS CLI (`aws s3 cp`, pointed at any S3-compatible endpoint via `--endpoint-url`) over `rclone`/`s5cmd`: the smallest standard mechanism for a single bucket, no multi-cloud abstraction this project doesn't need (rule 11) | APPROVED | 7.3 | `ops/backup/Dockerfile`; docs/progress/ops.md |
| D-73-pg-client-pgdg | 2026-10-15 | `postgresql-client-18` source | Debian trixie's own base repo ships only `postgresql-client-17` (confirmed live: no `-18` candidate in `apt-cache policy`) — the official PGDG apt repo (`apt.postgresql.org`) is the standard, well-known source for a client matching `postgres:18.2-trixie`'s own major version exactly | APPROVED | 7.3 | `ops/backup/Dockerfile`; docs/progress/ops.md |
| D-73-caddy-ports-reset | 2026-10-15 | [SECURITY-adjacent] Prod port isolation | Confirmed live (`docker compose config`, not assumed): a bare `ports: []` in a Compose override does NOT clear an inherited `ports:` list (Compose concatenates list keys by default) — only the Compose Specification's `!reset []` tag genuinely does. Used for `api`/`phoenix`/`postgres`/`qdrant` in the prod override, so none of the four can ever gain a published port in production regardless of what the base `deploy/docker-compose.yml` does later | APPROVED, verified live | 7.3 | `deploy/docker-compose.prod.yml`; docs/progress/ops.md |
| D-73-phoenix-tunnel-container-ip | 2026-10-15 | Phoenix SSH-tunnel access with zero published ports | Reconciles two instructions that first looked like they conflicted ("only ports 80 and 443 published" vs. "Phoenix access over an SSH tunnel"): Linux Docker already routes a host to any container's own IP on a user-defined bridge network with zero `ports:` entries, so `ssh -L 6006:<container-ip>:6006` still works with NO published port anywhere — docs/deploy/RUNBOOK.md §9 has the exact commands | APPROVED | 7.3 | `docs/deploy/RUNBOOK.md`; docs/progress/ops.md |
| D-73-backend-changes-flagged-not-built | 2026-10-15 | Scope boundary | Two real `backend/` changes this proxy will need — trusted `X-Forwarded-For` for the rate limiter (confirmed live: every `request.client.host` read in `app/auth/routes.py`/`app/chat/routes.py`/`app/dashboard/routes.py` today becomes Caddy's own IP once it fronts the app, silently breaking per-IP rate limiting) and `Host`-header validation (confirmed live: no `TrustedHostMiddleware` or equivalent exists anywhere in `app/main.py`/`app/config.py` today) — are named here, NOT built: this track does not edit `backend/` (§11.5 point 4), and both are already the named subject of the pre-existing PROJECT_SPEC.md:6859/6888 marker this entry reinforces, not duplicates | APPROVED, flagged not built | owner: Phase 7 batch (whoever next edits `backend/`) | `deploy/Caddyfile`; docs/progress/ops.md |
| D-73-ci-fix-env-dedup | 2026-10-15 (CI fix, follow-up) | [SECURITY-adjacent: config drift] Env var dedup | `main`'s CI `test` job went red (`backend/tests/test_env_example.py`, 2 failures) because `CADDY_UPLOAD_MAX_SIZE_MB` was a needless second variable for a value the backend already names (`Settings.upload_max_size_bytes`) and because no new variable had been registered in that test's own allow-lists. Fixed: removed `CADDY_UPLOAD_MAX_SIZE_MB`; `deploy/Caddyfile`'s `request_body` limit now reads `UPLOAD_MAX_SIZE_BYTES` directly (the SAME Settings field) via Caddy's own `{$VAR:default}` placeholder syntax; `SMTP_HOST`/`SMTP_PORT`/`SMTP_USERNAME`/`SMTP_PASSWORD`/`SMTP_FROM_ADDRESS` (already correctly named, just unregistered) and `UPLOAD_MAX_SIZE_BYTES` joined `backend/tests/test_env_example.py`'s existing `EXTRA_EXAMPLE_KEYS`; every genuinely new deploy-only key (hostnames, S3/backup, monitoring) moved to a new, separately named `OPS_ONLY_EXAMPLE_KEYS` tuple in that same file. **Found live, not assumed**: Compose's `${VAR:-}` passes an EMPTY string (not omitted) when unset, which Caddy's `{$VAR:default}` placeholder treats as "set", not "unset" — confirmed by a direct `docker run -e UPLOAD_MAX_SIZE_BYTES= ... caddy validate` failure; fixed by giving the Compose-layer default the same literal number as Settings' own default | APPROVED, RESOLVES the `test_env_example.py` CI failure | 7.3 | `backend/tests/test_env_example.py` (the one authorized edit); `.env.example`; `deploy/Caddyfile`; `deploy/docker-compose.prod.yml`; docs/progress/ops.md |
| D-73-ci-fix-push-gate-timing | 2026-10-15 (CI fix, follow-up) | Process correction | Root cause of the CI failure above was NOT skipping the offline-suite gate (PROJECT_SPEC.md §2 rule 18 / §11.5 point 7) — it was running it at the wrong POINT IN TIME: the suite ran (1090 passed, 0 failed, genuinely true at that moment) BEFORE the final `.env.example` edit, then `.env.example`/RUNBOOK/register/progress-log edits followed with no re-run before push. New standing rule for this track: the full backend offline suite runs from `.venv` immediately before the push, after the LAST commit of the batch — never earlier in the batch regardless of how many times an earlier version already passed | APPROVED, new process rule | 7.3, standing for this track | docs/progress/ops.md's own "CI fix" entry |

### Production bug fix: config-path resolution (2026-10-16)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-config-path-bugfix | 2026-10-16 | [SECURITY-adjacent: production outage] Phrase-data file loading | `Path(__file__).resolve().parents[N]`-relative loading of the escalation/greeting/rewrite-trigger yaml files broke every chat turn in the real built image (resolves to `site-packages` once non-editably installed, not the backend root) — fixed by moving the three files INSIDE the `app` package (`app/agent/phrases/`, real wheel-build data) and loading them via `importlib.resources`, which is correct for an editable install, a non-editable install and the built image alike; `backend/Dockerfile`'s separate `COPY config/ ./config/` line removed | APPROVED, fixed and verified live (adversarial: reproduced the real `FileNotFoundError` against the pre-fix code, confirmed the fix and both new regression tests/smoke checks catch it) | out-of-band, between Phase 6 close-out and Phase 7 | PROJECT_SPEC.md §5 "Production bug fix: config-path resolution for phrase-data files"; `backend/app/agent/{escalation_phrases,greeting_phrases,rewrite_trigger}.py`; `backend/pyproject.toml`; `backend/Dockerfile`; `backend/tests/test_package_data.py`; `deploy/smoke-image.sh` |
| D-config-path-spec-deviation | 2026-10-16 | Deviation from docs/SPEC.md §7's literal wording | §7 names the escalation trigger-phrase file's location as "the backend config folder" — moved inside `app/agent/phrases/` instead (the functional mechanism §7 actually specifies — yaml, schema-validated at load, deploy-gated — is unchanged, only the literal path differs), justified by a real, previously-shipped production defect in that literal path | APPROVED (deviation, rule 7) | same | same |
| D-worker-healthcheck-timeout | 2026-10-16 | Worker Docker healthcheck timing | `samoflow-demo-worker-1` reported `unhealthy` after a forced restart — diagnosed live as a real timeout-margin defect (the healthcheck's own `import app.worker` cold-imports this project's heaviest dependency tree, measured 5.4-5.8s every run against a shared 5s timeout, vs. the api service's own 0.5-0.65s HTTP-only check) — fixed with a worker-specific `interval: 15s`/`timeout: 15s`/`retries: 3` in `deploy/docker-compose.yml`, not a shared-anchor change | APPROVED, fixed and verified live | out-of-band, between Phase 6 close-out and Phase 7 | PROJECT_SPEC.md §5 "Production bug fix: worker healthcheck timeout margin"; `deploy/docker-compose.yml` |

### Production bug fix: widget launcher color + stuck chat input (2026-10-16)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-launcher-color-bugfix | 2026-10-16 | Widget theming | The launcher button never picked up the tenant's brand color — root cause: `--sf-primary`/`--sf-primary-text` were set (App.js) on the chat PANEL only, a SIBLING of the launcher under the same shadow root, not a shared ancestor, so the custom properties never inherited into it; separately, the loader's own CSS hardcoded a literal color with no `var()` reference at all. Fixed by moving the `setProperty()` calls to the shared shadow HOST element (`mount.js`), which both the launcher and the panel descend from, and switching `widget.css`'s `.samoflow-launcher` to reference the same two variables. A new `color-cache.js` pair (loader/chat, mirroring the already-approved `position-cache.js` pattern) caches the resolved color so a RETURNING visitor's launcher paints correctly from first paint, not only after their next click; the loader's own reader validates a strict `#rrggbb` (or the exact literal `#ffffff`/`#000000` for the text color) before ever calling `setProperty()`, rejecting anything else (`rgb()`, `url()`, `var()`, `expression()`, overlong strings, missing fields) — adversarial tests committed. No `postMessage` anywhere: this is a same-document shadow-DOM widget (a lazily-loaded `<script>`, never an iframe), so there is no cross-document boundary for `postMessage` to cross in the first place — the real, equivalent trust boundary here is the loader's own localStorage-cache reader, treated with the same rigor the prompt asked for a `postMessage` handler. | APPROVED, fixed and verified live (real demo stack, teal `#14b8a6`: launcher/header/bubbles/Send all match; returning-visitor first-paint confirmed with a persisted browser context) | out-of-band, between Phase 6 close-out and Phase 7 | PROJECT_SPEC.md §5 "Production bug fix: widget launcher color + stuck chat input"; `widget/chat/src/{contrast,color-cache,mount,App}.js`; `widget/loader/src/{color-cache,widget}.js`; `widget/loader/src/widget.css`; `widget/chat/src/chat.css`; `widget/loader/test/color-cache.test.mjs`; `widget/chat/test/contrast.test.mjs`; `widget/test/e2e/chat.spec.mjs` |
| D-stuck-input-bugfix | 2026-10-16 | Widget chat input | After the first turn, the message input/Send stayed disabled forever — root cause: `App.js` read `controller.isStreaming` (a plain mutable property on the `ChatController` class instance) directly as the `disabled` prop, never Preact state. The LAST re-render during a turn (triggered by `onDone`/`onError` calling `setMessages`/`setBanner` from inside `_consumeStream`) happens strictly BEFORE `ChatController.send()`'s/`retry()`'s own `finally` block sets `isStreaming = false`, so that render reads it as still `true`, and nothing ever renders again when it flips back — a pure Preact-reactivity bug, reproducible with a mock backend too, not specific to a real-backend response shape. Fixed with a real `useState` (`isStreaming`) in `App.js`, set true before `controller.send()`/`.retry()` and false in a `.finally()` on that promise; a `useEffect` (keyed on `isStreaming`/`open`/`view`) refocuses the textarea, re-running on `open` too so a turn that finished while the panel was closed (hidden elements cannot receive focus) is caught the moment the panel reopens. Verified live against the real backend for the normal/409/503/timeout/dropped-connection/close-reopen-mid-stream paths; quota-exhausted is NOT a terminal state (docs/SPEC.md §9/§11: it routes through the same `done` event with a fixed fallback reply, and "the escalation form still works") — no spec-mandated terminal state exists anywhere in this flow. | APPROVED, fixed and verified live + e2e (second real message sent successfully against the real demo backend; 5 new e2e tests cover 409/503/SSE-timeout-error/dropped-connection/close-reopen-mid-stream) | out-of-band, same task as D-launcher-color-bugfix | PROJECT_SPEC.md §5 "Production bug fix: widget launcher color + stuck chat input"; `widget/chat/src/App.js`; `widget/test/e2e/chat.spec.mjs` |
| D-conversation-id-workaround-removed | 2026-10-16 | Widget `conversation_id` discovery | Removes `ChatController._discoverConversationIdIfUnknown()` (the extra `GET /api/v1/conversations` call on a session's first turn) outright, per the Step-6.2-era forward commitment (below) — `POST /chat`'s own `done` SSE event already returns `conversation_id` on EVERY turn (Step 6.2, pre-authorised), so `chat-controller.js` now reads it directly from that event. **A second, independent real defect found while removing it, not merely a latency cleanup**: that workaround's own `GET /api/v1/conversations` call FAILED with a real browser CORS error in a genuinely cross-origin deployment (confirmed live against the real demo stack: widget on `127.0.0.1:4280`, API on `127.0.0.1:8010`) — `conversation_id` was therefore NEVER actually learned that way in production, meaning every turn (not only the first) silently started a brand-new conversation, discarding context. | APPROVED, RESOLVES the Step-6.2-era widget-track-close-out forward commitment ("remove the GET /conversations workaround ... once 6.2 lands") | out-of-band, same task | PROJECT_SPEC.md §5 "Production bug fix: widget launcher color + stuck chat input"; `widget/chat/src/chat-controller.js`; `widget/chat/test/chat-controller.test.mjs` |
| D-conversations-escalate-cors-bugfix | 2026-10-16 | [SECURITY-adjacent: production defect] Missing CORS on 3 widget-facing endpoints | `GET /api/v1/conversations`, `GET /api/v1/conversations/{cid}/messages` and `POST /api/v1/conversations/{cid}/escalate` never had an `OPTIONS` preflight handler or an `Access-Control-Allow-Origin` header on any real response — unlike `/session` and `/chat`, which both had real, dynamic per-site-key CORS from day one. Confirmed live: this is exactly why the `conversation_id`-discovery workaround above failed, and the SAME gap independently breaks "Previous chats" (`ConversationList.js`) and "Talk to a person" (`EscalationPanel.js`) in any genuinely cross-origin widget deployment — neither feature's own existing, "passing" Playwright e2e test ever caught this, because `widget/test/e2e/server.mjs` serves the widget page and its fake API from the SAME origin/port, so the browser never enforces a real CORS preflight in that suite at all. Fixed: a shared `build_preflight_response()` helper (`app/auth/cors.py`, extracted now rather than copy-pasted a 3rd/4th/5th time past this project's own established duplication threshold — `/session`'s and `/chat`'s own preflight handlers refactored onto it too), a new `OPTIONS` handler for each of the 3 endpoints, and `Access-Control-Allow-Origin: identity.org` + `Vary: Origin` on EVERY real response branch of each (success, 404, 409, 429) — the 404 branches were converted from `raise HTTPException` to an explicit `JSONResponse` carrying the same headers, since FastAPI builds an exception response independently of a handler's own injected `response` object and would otherwise silently drop them. | APPROVED, fixed and verified live (real demo stack) + 2 new backend test functions (preflight + every response branch, both files) | out-of-band, same task | PROJECT_SPEC.md §5 "Production bug fix: widget launcher color + stuck chat input"; `backend/app/auth/cors.py` (new); `backend/app/auth/routes.py`; `backend/app/chat/routes.py`; `backend/app/handoff/routes.py`; `backend/tests/test_chat_routes.py`; `backend/tests/test_handoff_routes.py` |
| D-sse-contract-file | 2026-10-16 | Process: SSE wire-contract drift | The real reason the widget's own e2e suite kept passing while the real widget was broken (BUG2's own 4th question): `widget/test/e2e/server.mjs`'s mock typed its `done`/`token`/`citations`/`error` payloads completely independently of the real backend's own `_sse_event()`-built ones, with nothing to catch either side drifting from the other — the mock simply never grew the `conversation_id` field the real backend already had. Fixed at the source, not by hand-syncing the mock once: a single committed `docs/chat_sse_contract.json` lists each event type's real field names; `backend/tests/test_chat_routes.py::test_done_and_other_sse_events_match_the_committed_contract` asserts a REAL live turn's event payloads match it exactly; `widget/test/e2e/server.mjs`'s own `sseFrame()` helper (the one chokepoint every mock payload already passed through) now validates every frame against the SAME file at the moment it is built, throwing immediately if a call site's fields drift, and adds `v: 1` itself, mirroring the real backend's `_sse_event()`. | APPROVED | out-of-band, same task | `docs/chat_sse_contract.json` (new); `backend/tests/test_chat_routes.py`; `widget/test/e2e/server.mjs` |
| D-widget-stop-mid-stream-not-built | 2026-10-16 | Found, not fixed — new gap | docs/SPEC.md §11's own design direction ("the visitor can stop an answer while it is generating... a stopped message counts once toward quota... the partial answer is kept, marked as stopped") has NO implementation anywhere in the widget (`App.js` has no stop control; `chat-controller.js` has no abort path) — found while auditing every path that ends a turn for this same task's own re-enable/refocus fix, but it is a missing FEATURE, not a stuck/dead state (the turn still completes normally without it), so it is out of scope for a bugfix batch. Not silently dropped — see the matching §9 marker. | APPROVED (found, not fixed, flagged) | owner: whoever next touches the chat composer, no task scheduled yet | PROJECT_SPEC.md §9 |
| D-chat-own-error-paths-cors-gap | 2026-10-16 | Found, not fixed — new gap, [SECURITY-adjacent: availability] | `POST /api/v1/chat` ITSELF has the same missing-CORS-headers defect as D-conversations-escalate-cors-bugfix above, on every one of its own PRE-STREAM `JSONResponse` error branches (429 rate-limited, 503 not-ready, 409 busy-stream, 409 lease-busy, 409 stale-retry, 503 pool-timeout) — only the final, successful `EventSourceResponse` carries `Access-Control-Allow-Origin` today. Confirmed by reading every `JSONResponse(...)` call site in `app/chat/routes.py` directly. NOT fixed in this task: scattering the same 2-line header dict into 6 separate return statements is the wrong shape for this fix (the actual over-engineered, error-prone path, per rule 11) — the right fix is almost certainly a small piece of shared middleware or a response-building helper applied uniformly, which is a bigger, more deliberate unit of work than a bugfix batch should rush, and needs reasoning about whether it should also touch the dashboard/admin surface (which deliberately has NO CORS today, D-6.1-cors-same-origin) before it is built. A visitor who hits one of these 6 real, reachable statuses on `/chat` itself gets an unreadable, confusing browser-level network error instead of the widget's own calm, fixed message for it — a real, if infrequent (rate-limit/busy/startup conditions), production gap. | APPROVED (found, not fixed, flagged — a deliberate scope boundary, not an oversight) | owner Phase 7 (security/CORS pass) or a dedicated small task, no task scheduled yet | PROJECT_SPEC.md §9 |

### [SECURITY] Step 7-follow-up: widget public-API batch (2026-10-16)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-7follow-widget-cors-middleware | 2026-10-16 | [SECURITY] CORS mechanism, consolidated | RESOLVES `D-chat-own-error-paths-cors-gap`. A single path-scoped `WidgetCORSMiddleware` (`app/auth/cors.py`) replaces every per-endpoint `@router.options(...)` preflight handler built by the prior batch (`D-conversations-escalate-cors-bugfix`) and additionally covers `/chat`'s own 6 pre-stream `JSONResponse` error branches, which never had a header before. The handler still makes the real allow/deny decision (setting `request.state.cors_allowed_origin` the moment the real Origin is verified against the token's own `org` claim or the site key's allow-list); the middleware's only job is attaching that header to whatever response follows, success or error, so a future new error branch cannot forget it. Confirmed live, not assumed: `BaseHTTPMiddleware` does not buffer a real SSE response in this project's pinned Starlette version (proven against a real uvicorn server — `httpx.ASGITransport` gives a false pass, it buffers everything); `request.state` set in a handler is visible to this middleware even when the handler raised `HTTPException` (Starlette's `ExceptionMiddleware` sits below it in the stack). | APPROVED, fixed and verified live (`backend/tests/test_widget_cors.py`) | out-of-band, between Phase 6 close-out and Phase 7 | PROJECT_SPEC.md §5 "[SECURITY] Step 7-follow-up: widget public-API batch"; `backend/app/auth/cors.py`; `backend/app/main.py`; `backend/app/auth/routes.py`; `backend/app/chat/routes.py`; `backend/app/handoff/routes.py`; `backend/tests/test_widget_cors.py` |
| D-7follow-dual-origin-e2e | 2026-10-16 | Process: e2e CORS coverage | `widget/test/e2e/server.mjs` now runs as two genuinely separate origins (widget port + API port), not one — before this, every widget e2e `fetch()` was same-origin, so the browser never enforced a real preflight, which is exactly why the prior batch's own CORS gaps shipped undetected (see `D-conversations-escalate-cors-bugfix`'s own root-cause note). The mock's CORS logic is built FROM a new committed `docs/widget_cors_contract.json`, asserted to match the real `WIDGET_ROUTES` exactly (same shared-contract pattern as `D-sse-contract-file`). **Adversarial proof done live, not merely claimed**: one real ACAO header was removed from the mock, the e2e suite went red, the header was restored and the suite went green — repeated across 4 separate cycles. **A second, independent defect found while building this**: `page.route().fulfill()` does not enforce real browser CORS at all (confirmed by a direct probe) — several of the prior batch's own 409/503/timeout/dropped-connection tests therefore never had real CORS-testing value despite appearing to; not retrofitted (still valid for their own original purpose), new real-network-trigger tests added alongside. | APPROVED, fixed and verified live | out-of-band, same task | PROJECT_SPEC.md §5 same section; `docs/widget_cors_contract.json` (new); `backend/tests/test_widget_cors_contract.py` (new); `widget/test/e2e/server.mjs`; `widget/test/e2e/public/index.html`; `widget/test/e2e/public/chat-fixture.html`; `widget/test/e2e/chat.spec.mjs` |
| D-7follow-widget-appearance-endpoint | 2026-10-16 | [SECURITY] Pre-click branding, supersedes `D-launcher-color-bugfix`'s own color-cache/position-cache mechanism | A new, tiny, cacheable `GET /api/v1/widget-appearance?site_key=...` (no visitor session, no conversation, no LLM call) now gives the launcher its real tenant color/position before the visitor's FIRST click — most visitors never click at all, so the prior mechanism's own first-visit gap (a brand-new visitor's very first page view still showed the default) was a real branding defect for paying tenants, not a cosmetic nicety. `Cache-Control: public, max-age=120` + a real `ETag`/304 round trip (confirmed live); rate-limited per-IP and per-site-key; a deliberate, justified `Access-Control-Allow-Origin: *` (the response is genuinely public, non-sensitive, identical for every origin — the one wildcard exception in this batch). Unknown/revoked/suspended/non-active all return the identical generic 404 — no oracle. **Retires, not merely supersedes**, the prior batch's own `color-cache.js`/`position-cache.js` localStorage pair outright (deleted, with full regression-test migration) — this endpoint is strictly better (every visitor, not only returning ones, with no client-side visitor-identifying storage at all), so the old mechanism's own documented gap is moot, not kept alongside as a fallback. `pick_readable_text_color()`/`is_valid_css_color()` ported line-for-line to Python (`app/tenancy/contrast.py`), parity proven via identical test vectors on both sides. | APPROVED, fixed and verified live + adversarial (16 backend + 18 widget-loader test cases: malformed/missing fields rejected, no field beyond the 3-field allowlist, a real bounded-timeout test proving the abort wiring genuinely aborts) | out-of-band, same task | PROJECT_SPEC.md §5 same section; `backend/app/auth/routes.py`; `backend/app/auth/schemas.py`; `backend/app/tenancy/contrast.py` (new); `backend/app/config.py`; `.env.example`; `backend/tests/test_widget_appearance.py` (new); `backend/tests/test_contrast.py` (new); `widget/loader/src/appearance.js` (new); `widget/loader/src/widget.js`; `widget/chat/src/mount.js`; deletes `widget/{loader,chat}/src/color-cache.js`, `widget/{loader,chat}/src/position-cache.js`, `widget/loader/test/color-cache.test.mjs` |
| D-7follow-stop-mid-stream | 2026-10-16 | [SECURITY] RESOLVES `D-widget-stop-mid-stream-not-built` | docs/SPEC.md §9's "stop mid-stream" is now built. **Design reconciling two instructions that would otherwise conflict**: the task said "use AbortController on the widget side", but the spec also requires stop to be "distinct from a dropped connection" — a raw aborted fetch is indistinguishable from a network failure at the transport level, so AbortController is used ONLY for the widget's own immediate local responsiveness (`chat-controller.js`'s `stop()` aborts its own `/chat` reader so the input re-enables without a round trip); a NEW, explicit `POST /api/v1/chat/stop` (Bearer-authed, idempotent no-op if nothing is streaming) is the one thing that actually tells the backend to cancel generation. `_answer_node` (`app/agent/graph.py`) now takes the LangGraph `config` it previously didn't, and checks a new `stop_requested_from_config()` accessor — a zero-argument CALLABLE in config (unlike every other config value here, which is frozen at turn start), because this one must observe a signal that can change mid-turn. A stop is honoured ONLY once real answer text has already started streaming (never before — there is no partial answer to keep otherwise), which also means it can only ever fire from inside the branch that already guarantees `can_answer=True`, so the node simply `break`s into its OWN existing post-loop return code — no second return path, so "counts once toward quota" is true via the SAME code a normal completion uses, not a new one. **The real mechanism behind "the server must actually cancel generation", confirmed live, not assumed**: a bare `async for` does not call `.aclose()` on a `break` (only GC eventually does) — `_answer_node` now wraps the model's `astream()` in `contextlib.aclosing()`, proven non-vacuous by a temporary-removal adversarial check. | APPROVED, fixed and verified live + adversarial (a real HTTP-level test pauses a fake model via a real `asyncio.Event`, posts a real `/chat/stop` over a separate httpx client — a single shared client/transport was confirmed live to deadlock the two concurrent calls — and proves a genuine partial answer persists, `done.stopped=true`, the lease releases before the follow-up request, usage counts exactly once, and an immediate second `/chat` succeeds with no 409) | out-of-band, same task | PROJECT_SPEC.md §5 same section; `backend/app/agent/context.py`; `backend/app/agent/entrypoint.py`; `backend/app/agent/graph.py`; `backend/app/chat/routes.py`; `backend/app/auth/cors.py`; `docs/widget_cors_contract.json`; `docs/openapi.json`; `backend/tests/test_agent_graph_answer.py`; `backend/tests/test_chat_routes.py`; `backend/tests/test_widget_cors.py`; `widget/chat/src/chat-controller.js`; `widget/chat/src/App.js`; `widget/chat/src/chat.css`; `widget/chat/test/chat-controller.test.mjs`; `widget/test/e2e/server.mjs`; `widget/test/e2e/chat.spec.mjs` |
| D-7follow-stop-history-not-shown | 2026-10-16 | Found, not fixed — deliberate scope boundary (Part D), not an oversight | `GET /conversations/{cid}/messages` does not show a PAST turn's own stopped marker in history — `AgentState.answer_stopped` is a per-turn OVERWRITE field, the identical, already-accepted limitation `citations` already has for history (Task 3.4.e's own decision). Not a new gap; stated so a future reader does not read silence as an oversight. | APPROVED (found, not fixed, flagged) | owner: whoever next builds a dedicated persisted-messages table (same owner as the pre-existing citations-in-history marker), no task scheduled yet | PROJECT_SPEC.md §9 |

### [SECURITY] PART 0 audit of the widget public-API batch (2026-10-16) — gaps found and fixed

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-part0-chat-stop-no-rate-limit | 2026-10-16 | [SECURITY] Real gap found and fixed | `POST /api/v1/chat/stop` shipped with NO rate limiting at all — every other authenticated widget endpoint has one, this one was missed. Fixed: a new per-visitor (default 20/60s) and per-IP (default 60/60s) `RateLimiter` pair, mirroring `/chat`'s own shape with a fully disjoint key space/state (hammering stop must never consume a visitor's own chat quota). Checked after identity resolves (this handler uses `Depends(get_current_visitor)`, so identity is already resolved or the request already 401'd before the body runs; this endpoint does no expensive work, so there is no DoS-cost reason to restructure around checking rate limits first). | APPROVED, fixed and verified live (`test_stop_rate_limited_per_visitor_gets_429_with_retry_after`) | out-of-band, PART 0 of this batch | PROJECT_SPEC.md §9; `backend/app/chat/routes.py`; `backend/app/config.py`; `.env.example`; `backend/tests/test_env_example.py`; `backend/tests/test_chat_routes.py` |
| D-part0-chat-stop-isolation-untested | 2026-10-16 | [SECURITY] Coverage gap found and fixed (code was already correct) | `POST /chat/stop` takes no `conversation_id`/`lease_token` by design (only the caller's own `identity.vid`), which already makes "a different visitor's token" / "a guessed conversation_id" / "a stale lease_token" structurally unable to stop someone else's turn — but nothing adversarially PROVED this live; every existing stop test used the SAME visitor for both streaming and stopping. Fixed: a new live test pauses one visitor's turn deterministically (a real `asyncio.Event`, never a timing guess), has a SECOND, genuinely different, legitimately-authenticated visitor call `/chat/stop`, and confirms the first visitor's own turn completes normally (`answer_stopped=False`). A second new test confirms a request with no `Authorization` header at all gets the same plain 401 every other route gives. | APPROVED, fixed and verified live | out-of-band, PART 0 of this batch | PROJECT_SPEC.md §9; `backend/tests/test_chat_routes.py` |
| D-part0-cross-site-key-origin-untested | 2026-10-16 | [SECURITY] Coverage gap found and fixed (code was already correct) | "ACAO only ever echoes an origin allowed for THAT site key" was proven at the pure-function level (`backend/tests/test_origin.py`'s own exhaustive table) and by direct code reading (`app/auth/routes.py`'s `origin_is_allowed(origin, site_key.allowed_origins)` — the SPECIFIC row's own list, never a merged or cached one) — but no end-to-end `/session` test used TWO site keys with DIFFERENT allow-lists to prove cross-key confusion is rejected; every existing "disallowed origin" test used a globally-foreign origin, which cannot distinguish per-key scoping from a blanket allow-list. Fixed: a new test presents `site_key_live`'s own real, working `ALLOWED_ORIGIN` against `site_key_draft` (same tenant, a genuinely different `allowed_origins` list) and confirms it fails identically to the reference failure. | APPROVED, fixed and verified live | out-of-band, PART 0 of this batch | PROJECT_SPEC.md §9; `backend/tests/test_session.py` |
| D-part0-disallowed-origin-untested-3-routes | 2026-10-16 | [SECURITY] Coverage gap found and fixed (code was already correct) | "A disallowed origin gets no ACAO on any branch" was tested for `/session`, `/chat` and `/chat/stop`, but never for `GET /conversations`, `GET /conversations/{cid}/messages` or `POST /conversations/{cid}/escalate` — all three share the identical `get_current_visitor()` origin-vs-`org`-claim mechanism already proven for `/chat`, but this was never confirmed directly for them. Fixed: one new test exercises all three with `DISALLOWED_ORIGIN`, confirming a plain 401 with no ACAO on each. | APPROVED, fixed and verified live | out-of-band, PART 0 of this batch | PROJECT_SPEC.md §9; `backend/tests/test_widget_cors.py` |
| D-part0-vary-untested-on-2-branches | 2026-10-16 | Coverage gap found and fixed (code was already correct) | `WidgetCORSMiddleware` sets `Vary: Origin` on EVERY matched-route response unconditionally, before its ACAO decision — but the two branches that deliberately never get ACAO (`/chat`'s own 413 and pool-exhaustion-503, both checked before identity resolves) never had this specifically asserted. Fixed: added the assertion to both existing tests. | APPROVED, fixed and verified live | out-of-band, PART 0 of this batch | PROJECT_SPEC.md §9; `backend/tests/test_widget_cors.py` |
| D-part0-appearance-site-key-limiter-untested | 2026-10-16 | Coverage gap found and fixed (code was already correct) | `GET /widget-appearance` already has both a per-IP and a per-(ip, site_key) `RateLimiter`, but only the per-IP one had a dedicated test. Fixed: a new test sets the per-site-key budget low and the per-IP one deliberately high (so it could never trip first and mask the other), confirming the per-site-key limiter trips independently. | APPROVED, fixed and verified live | out-of-band, PART 0 of this batch | PROJECT_SPEC.md §9; `backend/tests/test_widget_appearance.py` |
| D-part0-appearance-timing-class | 2026-10-16 | Found, not fixed — pre-existing, documented, same shape as `/session`'s own design | `GET /widget-appearance`'s three failure reasons (unknown/suspended site key, inactive tenant) are byte-identical in body and status, but NOT in query count: the inactive-tenant branch does one more DB round trip (`get_tenant()`) than the other two. This is the IDENTICAL shape `/session`'s own `create_session()` already has for its own four-reasons-one-response design (its step (d) also costs one more query than steps (b)/(c)) — not a new gap this endpoint introduces. Not fixed: equalizing query count (or deliberately padding latency) is its own fragile piece of work needed for BOTH endpoints together, and a single extra LOCAL Postgres round trip is a weak signal against real network jitter for any remote attacker. Named in code and here rather than quietly assumed away. | APPROVED (found, not fixed, flagged — a deliberate, scaled-to-risk call, not an oversight) | owner: whoever next revisits `/session`'s own no-oracle design, no task scheduled yet | PROJECT_SPEC.md §9; `backend/app/auth/routes.py`'s own comment at `get_widget_appearance()` |
| D-part0-single-process-confirmed-and-guarded | 2026-10-16 | [SECURITY-adjacent] Multi-process safety, confirmed and guarded | Confirmed live (not assumed): production runs exactly ONE `api` process — `backend/entrypoint.sh`'s own uvicorn command has no `--workers` flag, neither compose file sets `deploy.replicas` on `api`, and `docs/deploy/RUNBOOK.md` gave no scaling instruction at all. The turn LEASE (`conversations.lease_until`/`lease_token`) is already Postgres-backed and safe across any number of processes; `chat_active_streams`/`chat_stop_requests` (and every other in-process mechanism this project already documents the same way — usage counters, rate limiters, `StatusCache`) are NOT, and remain correctly scoped to exactly this one-process precondition. Since neither the compose config nor the RUNBOOK lets an operator raise the process count today, the stop signal is NOT moved to shared state this round (the user's own stated condition for doing so is not met) — instead, a new guard (`backend/tests/test_single_api_process_guard.py`) fails the build the moment entrypoint.sh or either compose file ever tries to raise it above one process, and a new RUNBOOK paragraph warns against the one case no static guard can catch (`docker compose up --scale api=N`, an operator-issued flag, never persisted in any file). | APPROVED, confirmed live + guarded, re-check if this ever changes | out-of-band, PART 0 of this batch | PROJECT_SPEC.md §9; `backend/tests/test_single_api_process_guard.py` (new); `docs/deploy/RUNBOOK.md` |
| D-part0-commit-hygiene-standing-rule | 2026-10-16 | Process rule | New standing rule (PROJECT_SPEC.md §2 rule 23, CLAUDE.md's new "Commit hygiene" section): every commit must pass the tests on its own; if a batch's parts are too interleaved to split safely, put them in ONE commit and say so explicitly — never leave an intermediate commit red. Recorded because the prior widget public-API batch's own "shared file lands in the LAST part's commit" rule left at least one intermediate commit (`f7acdd6`) unable to pass `test_widget_cors.py`'s own new `chat_stop`-route tests if checked out alone (the route itself landed in a later commit). | APPROVED, new standing rule | standing, all future batches | PROJECT_SPEC.md §2 rule 23; CLAUDE.md |

### [SECURITY] PART A: verified_domains link allowlist (2026-10-17) — RESOLVES the widget-track close-out forward commitment above

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-partA-verified-domains-allowlist | 2026-10-17 | [SECURITY] Widget renderer link allowlist, additive | A link in answer/history text now renders live if it is https AND (its exact URL is in this turn's own citation set OR its HOST exactly matches one of the tenant's own `verified_domains` from `widget_config`) — the two rules are additive, proven separately AND together. Matching uses the real WHATWG `URL` parser throughout (never a hand-rolled host extractor), canonicalising both sides (the live link's own host, and each stored verified-domain entry) through the SAME `new URL()`-based path specifically so an IDN/homograph look-alike compares in its own distinct punycode form, never by visual similarity — confirmed live (`café.example` and a Cyrillic homograph of "verified" encode to different, non-colliding punycode strings). `verified_domains` applies to BOTH a live answer and a re-opened past conversation (a tenant-wide, session-long property, unlike citations, which are genuinely per-turn and cannot be reconstructed for history) — but never to the visitor's OWN typed message bubble, which keeps the existing empty-allow-set treatment regardless. | APPROVED, built and verified live (adversarial pass below) | RESOLVES the Step-5.1/5.2-era widget-track-close-out forward commitment ("widen the answer-text link allowlist... once `verified_domains` exists") | PROJECT_SPEC.md §5 "[SECURITY] PART A: verified_domains link allowlist"; `widget/chat/src/{render,url-safety,config-validation,App}.js`; `widget/chat/test/{markdown-render-adversarial,url-safety,config-validation}.test.mjs`; `widget/chat/test/vnode-helpers.mjs` |
| D-partA-subdomains-do-not-count | 2026-10-17 | Decision under docs/SPEC.md | A subdomain of a verified domain does NOT automatically count as verified — exact host match only. Justified by docs/SPEC.md §5's own existing ingestion rule, applied identically here for consistency rather than re-decided from scratch: "Subdomains count only if the tenant verifies them." A tenant that wants `docs.example.com` links to render live must verify `docs.example.com` itself, exactly as it would need to for crawling it. | APPROVED | same | docs/SPEC.md §5; `widget/chat/src/render.js`'s own header comment; `widget/chat/test/markdown-render-adversarial.test.mjs`'s own subdomain tests |

### [SECURITY] PART B: full-history secret scan (2026-10-17)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-partB-gitleaks-scan | 2026-10-17 | [SECURITY] Public-repo secret hygiene, new tooling | Full git history (322 commits, every ref, not just HEAD's own ancestry) scanned with gitleaks v8.21.2, pinned to an exact version AND image digest, run via its own official container (no new repo dependency -- nothing installed). `ops/security/scan-secrets.sh` (new) wraps the invocation, handling this repo's own worktree layout (a git worktree's `.git` names an ABSOLUTE host path under the real repo's `.git/worktrees/<name>`, which a container with only the worktree bind-mounted cannot resolve -- confirmed live: without mounting the common ancestor too, gitleaks silently reported "no leaks found" after actually failing to open the repository, a false-negative far worse than a loud error). 18 original findings, every one individually reviewed by reading the real historical source at its exact commit/file/line (never assumed from the rule name/entropy score alone) -- confirmed ALL 18 are test-fixture/placeholder values (`backend/scripts/generate_openapi_schema.py`'s own `"placeholder-..."` literals; `deploy/smoke-image.sh`'s own `smoke-test-...`/`change-me` throwaway values for a disposable container; `backend/tests/{test_main,test_alembic,conftest,test_tokens}.py`'s own `"test-..."`/`"harmless-..."`/`"distinctive-..."` fixtures, several already carrying their own pre-existing `# noqa: S105 (test fixture value)` comment). **No real credential found -- no rotation needed.** Each suppressed via a fingerprint-based `.gitleaksignore` (repo root, new), one line of reason per entry as required; the script's own adversarial self-test (committing then reverting a random, never-real GitHub-PAT-shaped probe token) confirmed live that it genuinely fails on a new finding, not merely that it always reports clean. | APPROVED, scanned live, no real secret found, tooling built and self-tested | PART B of this batch | PROJECT_SPEC.md §5 "[SECURITY] PART B: full-history secret scan"; `ops/security/scan-secrets.sh` (new); `.gitleaksignore` (new); `docs/deploy/RUNBOOK.md` |

### CI fix: widget.yml Playwright keyboard-focus flake (2026-10-17)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-ci-fix-appearance-fetch-blocks-mount | 2026-10-17 | Root-cause CI fix, real UX defect found | `loader/src/widget.js`'s `mount()` (since `52d4c6d`) awaited `fetchAppearance()` BEFORE creating the host/shadow-root/launcher at all -- the launcher did not exist in the DOM until that network round trip resolved, on every page load, contradicting `appearance.js`'s own stated goal ("a slow/unreachable API cannot meaningfully delay first paint"). Reproduced the Widget CI job's exit-1 failure (`test/e2e/accessibility.spec.mjs`'s keyboard-reachability test) live inside a genuine `ubuntu-24.04` container on the unfixed code (first attempt); never reproduced natively on macOS. Fixed by creating the host/shadow-root/launcher synchronously and running `fetchAppearance()` concurrently (never blocking it); the test was also brought in line with its own sibling tests' established idiom (wait for the launcher to exist before interacting with it, instead of a one-shot `page.evaluate()` snapshot). 5/5 clean `test:e2e` runs in the same reproducing container after the fix. | APPROVED, reproduced, fixed, re-verified live (5x) | CI fix on `track/ops` | PROJECT_SPEC.md §5 "CI fix: widget.yml Playwright keyboard-focus flake"; `widget/loader/src/widget.js`; `widget/test/e2e/accessibility.spec.mjs` |
| D-ci-fix-run-ci-locally-docker | 2026-10-17 | New tooling, closes a previously-documented gap | `run-ci-locally.mjs`'s own already-known limitation (Step 5.1's entry: "run on my own machine, not GitHub's `ubuntu-24.04` runner") was confirmed live as the actual reason this exact flake passed locally and failed in CI. New `widget/scripts/run-ci-locally-docker.sh` (`npm run ci:local:linux`) runs the identical `widget.yml` step list, via the existing `run-ci-locally.mjs` itself (no duplicated step list), inside a disposable stock `ubuntu:24.04` container at the pinned `.nvmrc` Node version -- no new repo dependency, same convention as `ops/security/scan-secrets.sh`. Copies the repo into the container's own filesystem rather than bind-mounting it read-write, since a Linux `npm ci` run directly against the host's `node_modules` leaves Linux-native `esbuild`/Playwright binaries behind and breaks the host's own native tooling -- hit and fixed live during this exact task. | APPROVED, built and run live end to end | same | `widget/scripts/run-ci-locally-docker.sh` (new); `widget/package.json`'s `ci:local:linux` script |

### Out-of-band maintenance batch: Claude Code guard rails, Dependabot maintenance, CI minutes, PR secret scanning, launcher reveal-once fix (2026-10-17)

| id | date | topic | decision | status | step(s) | source |
|---|---|---|---|---|---|---|
| D-maint-claude-settings-deny | 2026-10-17 | Tooling safety, not a SamoFlow spec decision | A prior out-of-band batch ran `git reset --hard` without checking status, against the harness's own "Executing actions with care" rule. New, committed `.claude/settings.json` adds a `permissions.deny` list (Bash prefix-wildcard rules) blocking `git reset --hard`, every `git clean -f`/`-fd`/`-fdx` flag order named, `git push --force`/`-f`/`--force-with-lease`, `git checkout -- .`/`git checkout .`/`git restore .`, `git branch -D`, `docker system prune`/`docker volume prune`, and the catastrophic literal `rm -rf .`/`..`/`/`/`~`/`$HOME`/`.git` forms. Deliberately NOT a blanket `rm -rf` deny: Claude Code's own deny-wins-over-allow precedence means a blanket rule would also block legitimate build/cache cleanup (`rm -rf node_modules`, `.pytest_cache`, `dist`, ...) with no way to carve an exception back in via `allow` -- the catastrophic literal forms are the ones a bash deny-list's literal-prefix matching can actually express safely. Known, stated gap: prefix-text matching is not argv-aware, so a reordered/obfuscated invocation (e.g. `git clean -d -f -x`, spaced differently than any listed entry) is not guaranteed to match -- this is a mechanical backstop for the realistic default-invocation accident this rule exists to prevent, not a sandbox. Safe alternatives recorded for each denied family: `git stash`/`git stash -u` in place of `--hard`/`clean`/`checkout -- .`/`restore .`; `git branch -d` (refuses unmerged) in place of `-D`; a human-run `git push --force...` in place of doing it unattended; targeted `docker rm`/`docker rmi`/`docker volume rm` in place of the two prune commands. `backend/tests/test_claude_settings_guard.py` (new) guards the file parses and every family stays covered. | APPROVED, built and tested live | PART A of this batch | PROJECT_SPEC.md §5 "Out-of-band maintenance batch (2026-10-17), PART A"; `.claude/settings.json` (new); `backend/tests/test_claude_settings_guard.py` (new); `CLAUDE.md` |

---

## 2. Forward commitments by step

Every instruction addressed to a future step or phase (due at / owner / deferred to / revisit
at / must be done by), grouped under the step it applies to. RESOLVED items are omitted here
(see the decisions table and PROJECT_SPEC.md §9 for the full resolved history) — this section
is deliberately forward-only, matching its purpose.

### 5.1 Loader and shadow DOM shell
- Widget bundler/build tooling decided (esbuild, D-113-widget-bundler) and the npm
  supply-chain setup — **RESOLVED**, confirmed live in `docs/progress/widget.md`.
- `test_layout_placeholders.py` update for `widget/` tooling — **RESOLVED, but corrected
  (2026-10-15): the prior line here overstated the fix's own scope.** Step 5.1's own fix
  covered `widget/loader` only; `widget/chat/` still had no real tooling at the time and
  remained guarded on purpose (5.2 hadn't started). Step 5.2 (commit `84aa984`) gave
  `widget/chat/` real tooling too, which re-triggered the identical guard for the identical
  reason; a follow-up commit (this task) narrowed the test a second time, down to
  `dashboard/` alone — **RESOLVED for real now**, confirmed live (`backend` offline suite via
  `.venv`, zero failures).

### 5.2 Chat UI core [SECURITY]
- The widget's own message renderer must independently enforce: images disabled outright,
  raw HTML disabled outright, GFM bare-URL auto-linking disabled outright, link rendering
  restricted to the turn's own citation URLs plus the tenant's verified domains — checked
  client-side regardless of what the server already escaped. Must also confirm the renderer
  does not Unicode-normalise text before markdown-parsing it (a concrete bypass: a fullwidth
  Unicode look-alike construct folds to live ASCII after NFKC normalisation). (PROJECT_SPEC.md:6959)
  **RESOLVED at commit `84aa984`** (`widget/chat/src/render.js`, `markdown.js`,
  `url-safety.js`): every one of these five properties is built and has its own committed
  adversarial regression test (`widget/chat/test/markdown-render-adversarial.test.mjs`),
  asserted directly against the rendered Preact vnode tree. **One real, named gap, not
  silently absorbed into "RESOLVED"**: `widget_config` carries no verified-domains field
  today (the Step 5.1 Part C contract check, PROJECT_SPEC.md:6991) — the renderer therefore
  restricts links to the turn's own citation URLs ONLY; it does not yet also allow the
  tenant's verified domains, because the contract does not give it that list. Widening the
  allow-list to include verified domains once `widget_config` carries them (PROJECT_SPEC.md:6991,
  owner the dashboard track, see the new entry below) needs no renderer redesign — it is a
  second, additive allow-list rule, not a rewrite of this one.

### 5.3 Conversations UI
- No forward commitment was addressed to this step in advance; built and **RESOLVED** this
  batch (commit recorded in `docs/progress/widget.md`): "Previous chats" (`GET
  /conversations`, opening one via `GET /conversations/{cid}/messages`), rendered through the
  SAME restricted renderer Step 5.2 built — history links render as plain text, never live,
  since no per-turn citation set is ever reconstructable for a past turn (see the
  "Unscheduled" section's own "past-turn citations" entry, PROJECT_SPEC.md:6932); a retry
  button that reuses the failed turn's own `client_message_id` (docs/SPEC.md §10); empty/
  loading/error states all real and tested.

### 5.4 Escalation panel and fallback UI
- No forward commitment was addressed to this step in advance; built and **RESOLVED** this
  batch: the "Talk to a person" button (shown on `offer_escalation`), a consent panel stating
  exactly what will be shared before anything is sent, client-side contact-field validation
  mirroring the server's own control-character/length/email-shape rules exactly (never the
  real enforcement boundary), `POST /conversations/{cid}/escalate` via the existing
  conversation-id workaround, and the three required security-adjacent checks (no submission
  without consent; injection through contact fields stays inert; a double-submit is
  prevented) — each a committed, live-passing Playwright test, not merely argued. The support-
  details fallback display needed no separate code path: it is a fixed server-side message
  streamed through the SAME `token`/`citations`/`done` sequence any other answer uses, so the
  existing renderer already covers it safely.

### 5.5 Theming, accessibility and CSP
- No forward commitment was addressed to this step in advance; built and **RESOLVED** this
  batch: tenant branding (colors/logo/greeting/position) through the existing validated
  config; automatic WCAG-AA text-contrast against the tenant's own primary color (real
  relative-luminance math, not a brightness guess); keyboard operability, visible focus, a
  real focus trap + host-scroll-lock on the mobile sheet only (desktop stays non-modal, per
  docs/SPEC.md §11's own design direction — a deliberate reading of "a focus trap in panels,"
  recorded here since the spec's own design direction is more specific than the task
  instruction's generic phrasing); a decoupled, visually-hidden live region that announces
  only completed turns, never per-token; `prefers-reduced-motion` respected (the panel's own
  open animation is skipped entirely under it); an automated axe scan with zero serious/
  critical findings; the 5.1-era flash-of-unstyled-launcher fixed (the button is now created
  only after its own stylesheet's `load` event). **A real, load-bearing finding along the
  way**: CSP's style-src governs anything that goes through CSS TEXT PARSING (a `<style>` tag,
  a `style=""` attribute, a Constructable Stylesheet's `replaceSync()`) but NOT a plain CSSOM
  property assignment (`element.style.setProperty()`) — confirmed live with a direct probe
  (an external, non-inline script under a real `style-src 'self'` header, zero violations) —
  which is what makes per-tenant color theming possible at all without a parameterized
  backend stylesheet endpoint. The dashboard live-preview mode (docs/SPEC.md §12) is
  deliberately NOT built — see the new POST-PILOT entry under 6.5 above.

### 5.6 Phase 5 acceptance
- No forward commitment was addressed to this step in advance; see `docs/progress/widget.md`
  for the full clause-by-clause check of docs/SPEC.md §18's own Phase 5 accept line and the
  §9/register reconciliation this step performed.

### Widget track close-out (2026-10-15, this task) — forward commitment for Phase 6
- **Consume `verified_domains` and the first-turn `conversation_id` once the dashboard track
  adds them in Step 6.2.** Both are additive contract gaps the widget track found and worked
  around (Step 5.1 Part C and Step 5.2 Part D, both above; also §9 markers,
  PROJECT_SPEC.md:7099 and the `conversation_id` marker). Once 6.2 lands: remove the
  `GET /conversations`-ordered-most-recently-active-first workaround for `conversation_id` and
  read it directly off the first turn's own response/SSE event instead; widen the answer-text
  link allowlist to also allow a link whose URL's domain is one of the tenant's own
  `verified_domains`, alongside the existing citation-URL-only rule (additive, never a
  replacement — a verified-domain match stays a SEPARATE, wider allowance next to the
  narrower, already-shipped citation rule, not a loosening of it). Owner: whichever session
  picks this up after 6.2 lands — not pre-assigned to a specific track, since by then the
  widget track itself may not still be an open worktree.
  **The `conversation_id` HALF is RESOLVED at the 2026-10-16 widget-bugfix task** (D-
  conversation-id-workaround-removed above) — `chat-controller.js` now reads it off every
  `done` event; the workaround is deleted outright. **The `verified_domains`-allowlist HALF is
  RESOLVED at the [SECURITY] batch follow-up, PART A (2026-10-17)** — see
  `D-partA-verified-domains-allowlist` below for the full build and the live adversarial
  proof.

### Backend contract gaps found by the widget track (owner: the dashboard track)
**Ownership formally taken, and scheduled for Step 6.2's own build, at this alignment task
(2026-10-15) — not built in this task itself (ALIGNMENT-ONLY, no 6.2 work).** Felix
pre-authorises (a) and (b) below as additive, non-breaking changes to widget-facing
responses — new OPTIONAL fields only, nothing renamed or removed from either `widget_config`
or the `/chat` `done` event's existing shape; the widget's own already-shipped code reading
these responses is unaffected by either addition landing. (c) needs no authorisation (a
marker deletion only, once 6.2 actually touches `app/auth/schemas.py`). See the matching
entries now also listed under "### 6.2 Tenant settings" below for discoverability from that
step's own forward-commitments list.

Three real, named gaps in the locked `backend/` contract, each found by the widget track
while building against it (Steps 5.1/5.2) and each flagged rather than fixed, per the
`backend/` edit restriction (PROJECT_SPEC.md §11.5 point 4) — the widget track cannot edit
`backend/` outside a stop condition, so all three are owned by the dashboard track, the one
track currently allowed to touch it during the parallel-track period. Each is also a §9
marker (rule 22); all three are additive fixes, needing no shape change on the widget's own
already-shipped side.
- **Add `verified_domains` to `widget_config`, additively.** `_public_widget_config()`
  (`app/auth/routes.py`) never surfaces the tenant's verified domains to the widget — the
  widget's own renderer (5.2, `84aa984`) works around this safely by restricting links to the
  turn's own citation URLs only (narrower and safer than a domain allow-list), but cannot
  widen to verified domains until this field exists. A new key alongside the existing six,
  never a replacement. (PROJECT_SPEC.md:6991)
- **Return `conversation_id` on `POST /api/v1/chat`'s `done` event for a first turn,
  additively.** Confirmed by reading every `_sse_event()` call site directly: no SSE event, no
  response header, and no line in docs/SPEC.md §10's own event table carries it today — a
  real contract gap (spec-level, not just implementation), found only while building Step
  5.2's `chat-controller.js`, not caught by the Step 5.1 Part C contract check itself (see the
  process-lesson note below). The widget's own CURRENT workaround (`ChatController
  ._discoverConversationIdIfUnknown()`, `widget/chat/src/chat-controller.js`): one extra
  `GET /api/v1/conversations` call, made only on a session's first turn (while
  `conversation_id` is still unknown; cached for every later turn), relying on that endpoint's
  own most-recently-active-first ordering (`last_message_at DESC`, touched at lease-acquire
  time, before the stream even starts) to reliably read back `conversations[0]` as the
  conversation that exact turn just created. Real, working, zero-backend-change today — but a
  real added request on every session's first turn, which the recommended fix (a new
  `conversation_id` field on the existing `done` payload) removes entirely once built.
  (PROJECT_SPEC.md:6993)
- **Delete the verified `SessionResponse` ASSUMPTION marker.** `backend/app/auth/schemas.py`'s
  own marker ("these are this task's own choice, not a spec-mandated wire contract") is now
  independently VERIFIED by the widget track, the contract's first real consumer — all four
  response fields and all six `_PUBLIC_WIDGET_CONFIG_FIELDS` keys are exactly what Step 5.2
  needs. Per PROJECT_SPEC.md §2 rule 10 ("verify then remove ASSUMPTION") the marker should be
  deleted; the widget track confirmed it but does not make the `backend/` edit itself.
  (PROJECT_SPEC.md:6992)

**Process lesson (2026-10-15), recorded so the next contract check doesn't repeat it**: the
Step 5.1 Part C contract check audited the `/session` response's SCHEMAS (field names, the
six `widget_config` keys) but not a full REQUEST/RESPONSE ROUND TRIP for every endpoint the
widget actually drives — the `conversation_id` gap above is exactly the kind of thing a
schema-only audit misses, since nothing about `ChatRequest`'s or the `done` event's own
*declared* shape looks wrong in isolation; the gap only appears when you ask "how does the
caller actually get back what it just sent/created, across a real multi-turn flow." A contract
check from here on should trace at least one real end-to-end flow (create → use → re-use)
for every endpoint it signs off on, not just confirm each message's own field names.

### 6.1 Dashboard authentication [SECURITY]
**RESOLVED in full at this alignment task (2026-10-15) — every item below is now built, not
forward-only; see the "Step 6.1 close-out" decisions-table rows above for the exact
commit/test citation for each.** Per this section's own stated convention (RESOLVED items are
omitted here, kept only in the decisions table and PROJECT_SPEC.md §9), the original seven
forward commitments are not re-listed; they map to D-shadcn, D-6.1-admin,
D-6.1-site-key-generator, D-6.1-origin-domain-normalise, D-6.1-session-field-na,
D-6.1-test-layout-placeholders and D-6.1-cors-same-origin respectively.

### 6.2 Tenant settings — RESOLVED (2026-10-16)
- **Three backend contract gaps**: **RESOLVED.** (a) `verified_domains` added to
  `widget_config`; (b) `conversation_id` returned on `POST /api/v1/chat`'s `done` event for a
  first turn; (c) the verified `SessionResponse` ASSUMPTION marker deleted. All additive-only,
  proven via `scripts/check_openapi_additive.py`.
- **Real support-details fields + per-tenant fallback template**: **RESOLVED.** Real fields
  on `tenants.config` (`app/tenancy/schemas.py`'s `SupportDetailsUpdate`); the real template
  (`render_support_details_text()`, `app/agent/graph.py`) rendered per-turn by
  `app/chat/routes.py` and threaded through the LangGraph config (never recomputed inside the
  graph), read back by all three fallback nodes, still passing through
  `_sanitize_answer_text()`. `tenants.config["alert_email"]` also added (already wired at
  Task 4.3's own usage-alert sweep). **A real [SECURITY] finding made and fixed in the same
  pass**: `support_text` is stored wrapped in `{"text": ...}`, never a bare `str` — a bare str
  would have been silently copied into the checkpoint's own persisted `metadata` column by
  `langgraph.checkpoint.base.get_checkpoint_metadata()` (confirmed live), leaking a tenant's
  real email/phone/contact-link. Proven live against the real checkpoint table
  (`test_support_text_is_never_persisted_in_checkpoint_metadata`). **Related pre-existing gap
  found, not fixed (flagged for whoever next touches it)**: `quota_exhausted` (Task 4.3, a
  bare `bool`) is NOT similarly wrapped and DOES leak into persisted checkpoint metadata
  today — non-sensitive, but the identical mechanism.
- **Real `Plan`-row creation mechanism**: **RESOLVED early, at Step 6.1** (`cmd_seed_plans`,
  `app/dashboard/admin_cli.py`) — not deferred to 6.2 after all (D-6.1-pilot-plans above).
- **Document the four optional rate-limit Settings fields in `.env.example`**: **RESOLVED.**

### 6.3 Sources screens — RESOLVED (2026-10-16)
- **Disable `database`-type source creation server-side and omit it from the dashboard UI**
  (Felix decision A, D-pilot-dbsync-disable). **RESOLVED.**
- **Clean up a deleted database source's `db_connections` credentials in the delete flow**
  (Felix decision B, D-pilot-dbconn-cleanup). **RESOLVED.**
- The url-list adapter's own missing sibling to crawl's staleness cleanup: `update_source()`
  overwrites `source.config` wholesale with no diffing, so a tenant removing one URL from
  their own list has no cleanup path — the removed URL's `Document` row and Qdrant points are
  never revisited. **RESOLVED** — `update_source()` now diffs and cleans up dropped URLs'
  documents/Qdrant points when `client`/`collection_name` are supplied.
  (PROJECT_SPEC.md:6919)
- **The two upload-orphan markers (2.7.c)** — a re-upload's previous `upload_id` bytes never
  deleted; `upload_id` generation/lookup needing tenant-scoping. **Both RESOLVED**: the real
  upload endpoint (`app/dashboard/source_routes.py`) deletes the previous entry's bytes on a
  filename-matching re-upload, and resolves `upload_id` only through the tenant's own already-
  scoped `source.config["uploads"]`, proven live against a cross-tenant adversarial test.
- **Investigated at Step 6.3 (2026-10-16), declined as not contained, per this task's own
  "wire it IF it's contained; otherwise leave the register entry with its reason" instruction.**
  Three of the five (`crawl_page_cap`/`db_sync_row_cap`/`refresh_now_daily_allowance`) are a
  genuinely small, mechanical change at their one real call site each (fetch the `Tenant` row,
  call the already-existing `get_plan_limits()`, fall back to the Settings default on `None` —
  exactly `app/chat/routes.py`'s own existing quota-check pattern). The fourth,
  `refresh_interval_min_allowed`, is NOT contained to the same degree: it is read inside a
  bare module-level helper (`_validate_refresh_interval()`, `app/ingest/repository.py`) with no
  `tenant`/`session` parameter at all, shared by `create_source()`/`update_source()` — making
  it per-plan means changing that helper's own signature and both call sites, in a function
  with real existing regression coverage for the exact behavior being touched
  (`test_update_source_enforces_the_same_refresh_interval_rules_on_change`). The fifth,
  `upload_max_size_bytes`, has a real NEW consumer in this same task (the real upload endpoint,
  `app/dashboard/source_routes.py`) — wired there directly against `PlanLimits` with a Settings
  fallback from day one (no legacy behavior to risk). **Declined to touch the other four
  existing call sites in this task**: in production today, no real tenant has `plan_id` set yet
  (no admin screen, and the pilot tenant's own plan assignment is a manual CLI step Felix has
  not yet run) — `get_plan_limits()` returns `None` for every real tenant right now, meaning
  the wiring would have ZERO observable effect until a plan is actually assigned, for a real
  signature-change risk today, in a business-logic/security-adjacent file. Re-owned, with this
  task's own investigation as the new reason: whoever next revisits per-plan ingestion limits
  (the first time a real tenant actually has a `plan_id` assigned) should wire `crawl_page_cap`/
  `db_sync_row_cap`/`refresh_now_daily_allowance` using the pattern named above, and decide
  then whether `refresh_interval_min_allowed`'s own larger shared-helper change is worth it at
  that point. (PROJECT_SPEC.md:6899, 6900, 6916, 6917)

### 6.4 Escalation inbox and usage — RESOLVED (2026-10-16)
- Apply the exact inverse of `_sanitize_answer_text()`'s escape table (markdown-unescape,
  then HTML-escape, in that order) before displaying a stored transcript in the dashboard
  inbox screen — the email-body half is already resolved. **RESOLVED**:
  `app/dashboard/escalation_routes.py`'s `get_escalation()` applies `markdown_unescape()` to
  the whole stored snapshot (one pass, not per-line like the email body — no reliable
  per-line role boundary survives in the already-joined stored string; that module's own
  docstring names this exact case as harmless); the HTML-escape half is satisfied by
  construction (`InboxScreen.tsx` renders it as a plain React text node, never
  `dangerouslySetInnerHTML`). (PROJECT_SPEC.md:6960)
- The `reason` field on an escalation is a single fixed `"visitor_requested"` string — the
  inbox cannot distinguish trigger categories (asks-for-a-person / complaint / negative
  wording / two-fallbacks) without a future schema change. **Still accurate, not fixed —
  a genuine, accepted limitation named in docs/DECISIONS_REGISTER.md's own D-44-reason-
  fixed-string entry, not this step's job to change the escalation schema.**
  (PROJECT_SPEC.md:6253)
- ~~The async unsafe/abusive-input review~~ — **re-owned to POST-PILOT/6.5** (Felix decision
  G, D-postpilot-admin-overrides); no longer this step's job.

### 6.5 Platform admin — POST-PILOT
(§11.1 already marks this whole step POST-PILOT, owner: whoever onboards the second tenant
or the first non-Felix-operated one. The following join it by Felix's decision G, this task.)
- Per-tenant gate-threshold override (platform-default half already built and real; the
  nullable `tenants.config` field, the admin-screen edit UI, and the audit-log entry remain). (PROJECT_SPEC.md:6943)
- Per-tenant chat-model/provider choice (`get_chat_model()` is a process-wide singleton with
  no per-tenant hook today). (PROJECT_SPEC.md:6946)
- The async flag-for-review path for unsafe/abusive input (Felix's own 3.4.g decision to
  defer real-time detection; this is its named replacement mechanism). (PROJECT_SPEC.md:6964)
- Self-signup, mandatory email verification, and team invites (docs/SPEC.md §12's full auth
  system) — a contained, additive follow-up once a second tenant exists. (PROJECT_SPEC.md:6274)
- **The dashboard live-preview mode (docs/SPEC.md §12: "the live preview renders the real
  widget bundle with unsaved configuration" via validated `postMessage`) is deliberately NOT
  built for the pilot — re-owned here POST-PILOT by direct instruction at Step 5.1/5.4-5.6
  (2026-10-15), the widget track's own batch.** Reason stated plainly: a `postMessage`-based
  live preview is a NEW message-passing attack surface (origin validation that must be
  airtight on both the dashboard's send side and the widget's own receive side, for a feature
  no pilot tenant needs on day one) AND a real coupling point between the two tracks (the
  widget bundle would need to expose a receive-config API the dashboard calls, binding both
  tracks' own release cadence together for a non-essential feature). The pilot's own path is
  simpler and already fully supported: save the config via the dashboard, then open the real
  widget on a real test page to see it. (docs/SPEC.md:345, 347)
- **A "platform-admin-only, no tenant membership at all" account shape** (found live at Step
  6.1, recorded at this alignment task, 2026-10-15, D-postpilot-admin-no-tenant-shape) —
  `/auth/login` today unconditionally requires a real `Membership`; correct for the pilot
  (every real admin also owns a tenant), but a future platform-admin-only operator account
  needs this built first.

### 6.6 Phase 6 acceptance
- *(no open forward commitment found addressed specifically to this step)*

### 7.1 Load tests
- Per-message token cap and max-agent-steps limit join this batch (Felix decision C,
  D-pilot-token-cap-agent-steps). (this task)
- First-turn p95 margin (73ms over the §17 budget) was measured only under light sequential
  research traffic — the k6 load test must confirm it holds under genuine concurrent
  production load. (PROJECT_SPEC.md:6827, 6038)
- docs/SPEC.md §17's own retrieval p95 budget ("under 100ms at pilot scale") has never been
  measured — no test in Step 3.1's own suite asserts a timing bound. (PROJECT_SPEC.md:6871)
- Turn-lease acquire/release latency, and `gpt-6-luna`'s real-world latency/availability,
  under genuine concurrency — both unmeasured. (PROJECT_SPEC.md:6108, 5959 — cross-referenced by fork)
- `gpt-6.1-sol` is a real, close near-miss on every ship-rule criterion except latency — worth
  a dedicated re-measurement once latency improves or the §17 budget is revised. (PROJECT_SPEC.md:5891, 6833)

### 7.2 Full security and injection run [SECURITY]
- **Image secret scanning joins this step's run** (Felix decision E, D-pilot-image-secret-scan). (this task)
- **The golden-set evals must also run against the BUILT IMAGE, not source-run evals alone**
  (D-config-path-bugfix, 2026-10-16) — the config-path bug shipped past every source-run test
  and eval precisely because none of them ever exercised the real non-editable-install/built-
  image code path; this step's own full run must close that blind spot (e.g. real HTTP calls
  to a running container's `/chat` endpoint). (PROJECT_SPEC.md §9, "Owner Step 7.2")
- A base64/zero-width/homoglyph-re-encoded injection canary is not caught by the current
  literal-substring detector — a second injection-detector pass should decide the real scope. (PROJECT_SPEC.md:6965)
- A broader, systematic fuzz pass on the groundedness judge (more phrasings, encoded
  variants, multiple judge models/temperatures) before leaning on the groundedness number for
  a real gating decision. (PROJECT_SPEC.md:6966)
- A real escalation/routing category should be added to the golden set — `Route` is
  `answer`/`gate_fallback`/`greeting` only today, so Step 4.4's own escalation-phrase matcher
  has no golden-set category measuring it. (PROJECT_SPEC.md:6967)

### 7.3 Backups, restore drill and monitoring
- The restore drill must prove the restored credentials actually decrypt the restored data —
  a real, end-to-end proof, not merely "the backup file exists." **Prep done (2026-10-15, ops
  track) — final verification on the real server at 7.3/7.4.** `ops/backup/restore-drill.sh` +
  `ops/backup/run-restore-drill.sh` built and run LIVE locally end to end (both the real-row
  decrypt path and the empty-table canary path — see docs/progress/ops.md for the full pasted
  proof), including a real Qdrant snapshot restore with a matching point count. Not yet proven
  against the real production backup bucket or the real `DB_CONNECTION_ENCRYPTION_KEY`, since
  neither exists until a real server does. (PROJECT_SPEC.md:6272)
- Per-message token cap and max-agent-steps limit join this batch (Felix decision C — see 7.1
  above; the batch is 7.1+7.3+7.4 together).
- `mem_limit`/`pids_limit` for api/worker/migrate should be tuned from the real load tests
  (the 512m/200 figures are unmeasured starting points); consider limits for Postgres/Qdrant
  themselves too. (PROJECT_SPEC.md:6870)
- JWT signing-key rotation should be documented and tested end to end (a real rotation drill),
  not just the mechanism. (PROJECT_SPEC.md:6886)
- Allowed `Host` header validation, and the identical rate-limiter forwarded-header switch,
  both wait on the real proxy this step introduces. **Prep done (2026-10-15, ops track) — the
  proxy now exists** (`deploy/Caddyfile`, every hostname from env, automatic TLS, security
  headers, strict CSP matching the dashboard's own e2e test, body limits, buffering off for
  `/api/v1/chat`) **— final verification (the actual backend wiring: trust Caddy's own
  `X-Forwarded-For`/`X-Real-IP`, validate `Host`) is a `backend/` change this track does not
  make, owned by whoever next edits `backend/` during 7.3/7.4** (see
  D-73-backend-changes-flagged-not-built above). (PROJECT_SPEC.md:6859, 6888)
- **New (2026-10-15, ops track): a production Compose override** (`deploy/docker-compose.
  prod.yml`) **adding Caddy and removing every other service's published port** (verified live
  via `docker compose config` that only `caddy` ends up with published ports — see
  D-73-caddy-ports-reset above) **— prep done, final verification (the real `docker compose up`
  on the real server) owned by 7.3/7.4.**
- **New (2026-10-15, ops track): the cron disk/memory monitor** (`ops/monitoring/check_
  resources.py`, stdlib-only, alerts via the same Postmark SMTP settings) **and the hosted
  uptime-checker setup steps** (`docs/deploy/RUNBOOK.md` §7) **— prep done (script smoke-tested
  live inside a real Linux container, both the clean-run and the forced-alert path), final
  verification (a real alert actually reaching a real inbox, the real uptime checker actually
  configured) owned by 7.3/7.4.**
- Separate migration-vs-application Postgres roles (DDL rights vs. data rights only) — this
  is also what finally makes the `audit_log` `REVOKE` (D-2.2-superuser) real enforcement for
  the first time; re-verify the existing regression test flips to FAIL→pass as expected. (PROJECT_SPEC.md:6869)
- Replace plaintext secrets in the environment with Docker secrets or an external secrets
  manager. (PROJECT_SPEC.md:6873)
- arm64 image build/scan, both manual verification and CI wiring. (PROJECT_SPEC.md:6861, 6876)
- API shutdown behavior under load (only verified at idle so far). (PROJECT_SPEC.md:6862)
- Per-tenant HNSW tuning (`hnsw_config.m=0` + `payload_m`) for `knowledge_chunks` — deferred
  with no cost, applicable later via the update-collection-parameters API. (PROJECT_SPEC.md:6879)
- The `pip install --require-hashes` gap for `[build-system] requires` packages (hatchling) —
  accepted as a residual risk; revisit if the upstream pip issue resolves, or before go-live
  if risk tolerance changes. (PROJECT_SPEC.md:6934)
- A real embedding-provider vendor-outage fallback (chat-model fallback alone leaves the turn
  dead at the retrieval step on a full OpenAI outage) — materially larger scope than a
  chat-model swap, since it implies re-indexing the tenant's corpus. (PROJECT_SPEC.md:6942, 6835)
- The CI `test`/`security.yml` job duplication (a composite action) — deferred until
  genuinely painful. (PROJECT_SPEC.md:6853)

### 7.4 Go-live checklist and spec reconciliation
- **Rejecting placeholder secrets when `APP_ENV=production` is a mandatory go-live gate**
  (Felix decision D, D-pilot-placeholder-secrets-gate) — `QDRANT_API_KEY`, `JWT_SIGNING_KEY`,
  `DB_CONNECTION_ENCRYPTION_KEY`, `QDRANT_READ_ONLY_API_KEY` all in scope. (this task)
- **Manual screen-reader pass with VoiceOver, by Felix.** Added at the widget track's own
  close-out (2026-10-15). Step 5.6 (Phase 5 acceptance) deliberately did not round this up:
  its own "screen-reader pass" evidence is an automated proxy only (a zero-serious-finding axe
  scan, plus the decoupled live region proven to announce exactly once per completed turn) —
  not a real pass with a real screen reader, which this environment has no way to run. A real
  VoiceOver pass over the widget (launcher, panel open/close, streaming answer, citations,
  conversations list, escalation panel, Esc/Tab behavior) is still owed before go-live.
- Chat-model fallback (a verified Anthropic secondary, or retry-before-first-token) — zero
  real coverage today. (PROJECT_SPEC.md:5914, 6826)
- Email deliverability must be tested against major mailbox providers (docs/SPEC.md §18's own
  Phase 7 accept line already names this).
- **Remove the dashboard's one known, tolerated strict-CSP violation** (recorded at this
  alignment task, 2026-10-15, D-6.1-exact-csp-tolerance's own named gap): react-remove-scroll-
  bar's own inline `<style>` insertion for the Dialog's scrollbar-gap CSS. Two real options,
  not yet actioned: a Caddy-issued per-response CSP nonce (needs dynamic HTML templating this
  project has not built yet — Phase 7/Caddy is the natural point); or switching to a
  scroll-lock approach that injects no `<style>` element at all (if one is found that still
  preserves the Dialog's real functional scroll-lock and focus-trap). Not a hash-pin — the
  violation's own CSS text embeds the real measured scrollbar-gap width, confirmed
  platform/browser-dependent, so a hash would be brittle across environments in exactly the
  way this project's CSP already rejects hash-pinning for. (`dashboard/e2e/csp.spec.ts`)
- **Re-check fastapi-users' maintenance status before pilot go-live** (recorded at this
  alignment task, 2026-10-15, D-6.1-fastapi-users-maintenance-mode): confirmed live (PyPI +
  GitHub README + GitHub API, checked 2026-10-15) that the library is in maintenance mode —
  security fixes and dependency maintenance continue, no new features, and a (currently
  unnamed, undated) successor toolkit is in development. Not a reason to swap libraries now
  (this project's security floor does not depend on new features), but re-check before pilot
  go-live whether a CVE has landed unpatched, whether the successor has shipped with a
  realistic migration path, or whether the project's own Python/FastAPI/SQLAlchemy versions
  have drifted past what a maintenance-mode (not actively-developed) library still supports.

### 7.5 Pilot onboarding (new step — Felix decision F, D-pilot-onboarding-step)
- Build the pilot tenant's own real golden set, replacing/extending the two stand-in corpora.
- Re-check, against real tenant content: the model choice (`gpt-6-luna`), the gate relevance
  threshold (0.20), `retrieval_k` (8), and the reranker-off decision — every one of these has
  only ever been tuned on synthetic/stand-in corpora. (PROJECT_SPEC.md:5497, 6844, 6957, 5668, 5684, 5687, 5938)
- Natural checkpoint (not yet decided to live here) for the Swahili/mixed-language-input
  revisit trigger — "before the first pilot tenant goes live, OR as soon as a non-English
  visitor message actually appears in production," whichever comes first. (PROJECT_SPEC.md:6836)

---

## 3. Unscheduled

Every marker whose owner is "no task scheduled," "to be scheduled," or "whoever first...".

- Product rename (VileSite/neutral `widgetplatform` → a real SamoFlow-branded codebase) has
  no scheduled task. (PROJECT_SPEC.md:157)
- Document the four optional rate-limit Settings fields in `.env.example` — "whichever task
  next touches `.env.example`, or Phase 6 dashboard-facing config docs." (PROJECT_SPEC.md:6889)
- A `<sitemapindex>` of nested sitemap files is not recursed into — "whoever first hits a real
  tenant site large enough to need it." (PROJECT_SPEC.md:6847, 6907, 1582)
- CSS-class-based element hiding is not detected by `_is_hidden()` — "whoever first hits a
  real page needing it." (PROJECT_SPEC.md:6896)
- The database-sync one-query guard's false-positive on a literal `;` inside a quoted string —
  "whoever first adds a `WHERE` clause or any other embedded literal value to a database-sync
  query." (PROJECT_SPEC.md:6911)
- Whoever builds a second `database`-sync engine (MySQL/MariaDB) must decide how it's
  distinguished from Postgres in the schema. (PROJECT_SPEC.md:6908)
- Whoever first wires TLS functionally for the database connector must close the
  asyncpg SNI/cert-pinning gap against `verify-full`. (PROJECT_SPEC.md:6909)
- The `.github/dependabot.yml` temporary `ignore: postgres` rule — remove once any of three
  named conditions happens first. (PROJECT_SPEC.md:6858)
- The Alembic autogenerate template (`script.py.mako`) fails this project's own lint config —
  "next migration-heavy task or a dedicated small fix." (PROJECT_SPEC.md:6867)
- Whichever future step first introduces a LangGraph subgraph must decide the `checkpoint_ns`
  question — none exists yet. (PROJECT_SPEC.md:6936)
- Whichever step first changes the embedding model or `EMBEDDING_VERSION` must re-tune the
  gate threshold from scratch (also Step 7.5 for the first real tuning). (PROJECT_SPEC.md:6944)
- Whichever step first builds real tenant-supplied tool servers (post-v1, §19) resolves the
  MCP-vs-`retrieve()` relationship; if a real agentic loop is ever built alongside it, the
  agent-steps limit must be built in the same change — **distinct from, and not satisfied by,**
  D-pilot-token-cap-agent-steps' own flat per-turn cap (built now, for the current
  one-call-per-turn design). (PROJECT_SPEC.md:6945)
- Whoever next revisits the groundedness judge, or starts Felix's own calibration labelling,
  whichever comes first — the judge/primary model match is currently incidental, not
  deliberate. (PROJECT_SPEC.md:6843)
- Whoever next changes the primary or judge model must re-check the same-model
  self-preference risk — standing, re-check whenever either changes. (PROJECT_SPEC.md:6828, 6040)
- The next can_answer research task must include a representative sample of every
  already-100% category, not just the categories implicated by the FPs/FNs. (PROJECT_SPEC.md:6840)
- Auditing the remaining `multi_source` items for the same `rel-07`/`ms-10`/`ms-13`-style
  mislabelling pattern. (PROJECT_SPEC.md:5792, 5819)
- Whoever next revisits email retry timing should decide whether the simplified hourly-retry
  is good enough long-term, or the full 5-step backoff is worth building once a real SMTP
  server exists. (PROJECT_SPEC.md:6962)
- Past-turn citations cannot be reconstructed from conversation history (a per-turn-overwrite
  state field) — no task owner. (PROJECT_SPEC.md:6932)
- Quota numbers (10,000 / 25,000) must be re-derived from real per-tenant traffic composition
  once it exists — the golden-set category mix is only a stated approximation. (PROJECT_SPEC.md:5912, 6031, 6825)
- ~~Whoever next builds a real password-reset or platform_admin-removal path...~~ —
  **RESOLVED at Step 6.2**: `app/dashboard/admin_cli.py`'s `reset-password`/
  `set-platform-admin`/`unset-platform-admin` commands, built on `UserManager.update()`.

---

## 4. Item 2 — Audit of completed steps (1.x-4.x): forward commitments not shown honored

Exhaustive review of every forward commitment addressed to a step that is itself now
complete (1.1 through 4.6). **Finding: no forward commitment addressed to a completed step
was found silently unaddressed.** This project's own process (duplication checks, step
close-outs, and the Step 4.6 Phase 4 acceptance reconciliation) catches and resolves or
re-owns essentially everything in the same pass it's found in — a real, verified property,
not assumed. The following are not failures, but scope corrections and near-misses worth
surfacing to Felix because an outside reader following only the original decision line (not
its later correction) would be misled:

1. **D-2.2-endpoint-dropped** (Step 2.2, 2026-10-04): a decision was recorded ("wired to a
   real admin endpoint now, following docs/SPEC.md over PROJECT_SPEC.md's prior practice"),
   then reversed before the same step closed, with the reversal documented in the same file.
   Reading only the earlier line in isolation would wrongly suggest an admin endpoint for
   domain revocation exists in Phase 2. It doesn't — dropped entirely, owner Step 6.1.
   (PROJECT_SPEC.md:752, 754, 6850)
2. **D-2.2-superuser** (Step 2.2, 2026-10-04): the `audit_log` append-only guarantee's
   intended enforcement mechanism (a Postgres-level `REVOKE UPDATE, DELETE`) is silently
   non-functional today because the `widgetplatform` DB role is a superuser — caught by the
   same task that built it, documented honestly with a permanent regression test, but the
   real enforcement (an AST guard only) will not match the compliance expectation most
   readers would form from "REVOKE" alone until the Phase 7 role split lands. (PROJECT_SPEC.md:744, 6869)
3. **Escalation golden-set category** (owner set to Step 4.4 at Task 3.1.b, 2026-10-09): Step
   4.4's own closure/decision-log entry (PROJECT_SPEC.md:6221-6257) makes no mention of the
   golden set at all — the re-owning to "whoever next extends the golden set" lives only in
   the §9 marker text itself, not in 4.4's own closure narrative. A minor
   documentation-location inconsistency (the §9 marker is intact and correctly worded), not
   a dropped obligation, but worth tightening: a completed step's own closure summary should
   name every marker it did NOT resolve, not rely solely on the marker's own text surviving
   elsewhere. (PROJECT_SPEC.md:6967)
4. **5 ingestion-limit Settings fields** (`crawl_page_cap`, `upload_max_size_bytes`,
   `db_sync_row_cap`, `refresh_interval_min_allowed`, `refresh_now_daily_allowance`, each
   originally "owner Task 4.3"): Task 4.3 (now Done) explicitly did NOT rewire any of them to
   the new `PlanLimits` type — re-scoped, with reasoning given, to "whoever next revisits
   per-plan ingestion limits (no task scheduled yet)." Transparent, not silent, but it means
   Task 4.3's own marker is closed on paper while the underlying gap (these caps still aren't
   plan-driven) persists with no scheduled owner — flagged in the register's Unscheduled
   section and partly addressed by assigning 6.3 as the natural next owner (§2 above), since
   it's the first step building real per-tenant limits UI. (PROJECT_SPEC.md:6142, 6170)
5. **Email retry schedule** (Task 4.3, now Done): docs/SPEC.md §14's full 5-step exponential
   backoff was not implemented; a simpler hourly-retry-until-success shipped instead, stated
   as deliberate (no real SMTP credentials existed to verify a more elaborate schedule
   against). Transparent, not silent — flagged under Unscheduled. (PROJECT_SPEC.md:6156, 6962)

No other completed-step (1.1-4.6) forward commitment was found unaddressed across the full
sweep of §5/§7/§9.

---

## 5. Item 3 — In-flight (5.1, 6.1) register entries not already relayed

Every register entry that applies to Step 5.1 or Step 6.1 and is **not** already among the
pre-relayed items (shadcn/ui + constraints; the maintained auth library; admin-key removal +
`platform_admin` + CLI admin commands; npm supply chain at 5.1; re-pointing `revoke_domain()`
and the admin endpoints; the stale `TODO(6.5)`; `test_layout_placeholders` both tracks; the
dashboard CORS marker via same-origin; `require_recent_auth`; widget JWT on dashboard
endpoints returns 401; the `pk_live_` generator; origin/domain normalisation; the Plan rows
10,000/25,000; the §12 acceptance additions; the `/session` field-name and verified-domains
contract check):

**5.1** — none found beyond the pre-relayed list. (The bundler choice and npm supply-chain
setup are both already relayed and already RESOLVED per `docs/progress/widget.md`.)

**6.1** (point-in-time snapshot as this register's own "missed decision" audit, taken before
Step 6.1 shipped — kept verbatim below for the historical record of what was still open at
that moment; **all three items are now RESOLVED, confirmed at this alignment task,
2026-10-15**):
1. **D-shadcn remains unresolved** (PROJECT_SPEC.md:306) — the 2026-10-15 kickoff ratified
   "Vite + React + CSS Modules" (D-113-dashboard-tooling) for dashboard tooling without
   citing or superseding the still-open shadcn/ui CANDIDATE. This is the exact gap this whole
   task was commissioned to fix structurally; Step 6.1 must resolve it explicitly, not treat
   the kickoff's silence on shadcn as an implicit rejection.
   **RESOLVED — see D-shadcn in the decisions table, commit `29b444b`.**
2. **The single-owner dashboard-auth-scope decision and its named security floor**
   (D-113-dashboard-auth-pilot, PROJECT_SPEC.md:6267): self-signup/email-verification/team-
   invites are dropped as a FEATURE reduction only — the real floor (argon2id, rate-limited
   lockouts, `HttpOnly`+`Secure`+`SameSite` cookies, CSRF protection, CLI-seeded owner
   account, no shipped default password) is a concrete, non-obvious scope decision for 6.1
   that is not covered by "the maintained auth library" alone (that item is about which
   library; this is about which features and what security floor).
   **RESOLVED — built and adversarially tested exactly as specified; see D-6.1-recent-auth-scope
   in the decisions table, commit `7519325`.**
3. **Dashboard/admin CORS and OPTIONS handling is still explicitly undecided**
   (PROJECT_SPEC.md:6860) beyond whatever the "via same-origin" pre-relay already told Felix —
   if same-origin serving is the real plan, Step 6.1 should record that as the actual
   decision (with a register/§9 update) rather than leave the marker's own "decide with the
   widget and admin work" wording unresolved.
   **RESOLVED — same-origin recorded as the actual decision; see D-6.1-cors-same-origin in
   the decisions table, commit `7519325`.**

---

**Senior engineer check**: built by four parallel sub-agents each reading a slice of
PROJECT_SPEC.md's ~6,250-line decision log verbatim, plus a direct sequential read of §9/§10/
§11/docs/SPEC.md by the coordinating session — chosen over sampling because the task
explicitly required exhaustiveness and the register's whole purpose is to never again miss a
candidate decision the way shadcn/ui was missed. The main trade-off: the decisions table
consolidates to roughly one row per decision-cluster rather than one row per sentence, so a
reader needing full verification detail (exact test counts, `Verified:` paragraphs) must
still follow the `source` line back into PROJECT_SPEC.md — a register that repeated every
verification sentence verbatim would be a second changelog, not a register. Highest residual
risk: the register is a snapshot as of 2026-10-15 — PROJECT_SPEC.md §2 rules 19-22 (added by
this task) are what keep it from going stale again, but only if every future step's own
kickoff actually performs the sweep rule 19 now requires.
