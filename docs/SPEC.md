# VileSite — Project Specification: Embeddable Agentic AI Widget & Platform

**Status:** Final for implementation. Every decision in this document has been confirmed by the project owner. Section 19 lists items deferred to later phases, and section 20 lists estimates to measure.

**Audience:** the project owner and the AI coding assistant that will build the system.

---

## 1. Overview & objectives

A production-ready, multi-tenant embeddable AI widget platform. **One website is one tenant**, with its own site key(s) and its own `client_id` in Qdrant. The widget:

- answers visitors **only from that tenant's content** (crawled pages, listed URLs, uploaded files, synced database data);
- streams answers in real time;
- remembers returning visitors' earlier chats **in the same browser**;
- hands off to a human through an **escalation request** when the AI cannot help.

## 2. Core architecture & tech stack

- **Backend & API:** FastAPI (async) with SSE streaming.
- **Agentic orchestration:** LangGraph with `AsyncPostgresSaver` for multi-turn state persistence.
- **Vector database:** Qdrant (dense and sparse vectors), with `client_id` payload filtering for tenant isolation.
- **Tooling:** Model Context Protocol (MCP) for modular tool execution.
- **Observability & evaluation:** self-hosted Arize Phoenix via OpenInference.
- **Frontend:** React dashboard, plus a Preact chat UI inside a shadow DOM container, delivered by a plain-JS loader (`widget.js`).
- **Also required:** PostgreSQL (app data, LangGraph checkpoints, job queue), a worker process (same image as the API) for ingestion and background jobs, and an email sender (SMTP or a transactional email service).
- **Hosting (v1):** one Ubuntu server running Docker Compose (§14).

---

## 3. Decisions log

| Area | Decision |
|---|---|
| Widget isolation | Shadow DOM. |
| Tenant model | One website = one tenant, with its own site key(s) and `client_id`. |
| Visitors | Recognized by browser now; logged-in users in a later phase (`external_user_id` reserved). |
| Conversations | A visitor can have several conversations and sees a "previous chats" list. |
| Chat retention | Per website, set in the dashboard, default 30 days. |
| Content sources | URL list, automatic crawl, or both; file uploads; database connector. |
| Source visibility | All sources are answerable to any visitor; clear warning when a tenant adds uploads or database content; one-click disable/delete per source. |
| Domain ownership | Verified (file, meta tag or DNS record) before any crawl or URL fetch. |
| Language | English only. |
| Refresh | Daily, weekly, monthly or quarterly per source, plus refresh-now, all limited by plan. |
| Plans | Limits table built now, plans assigned manually in an admin screen, payments later. |
| Crawler | Simple crawler only; browser rendering later if needed. |
| Upload types | PDF, Word (.docx), plain text/Markdown. |
| Database | Scheduled sync first; approved live queries later. |
| Search | Hybrid (dense + sparse BM25, server-side RRF fusion); reranker hook built but off by default. |
| Follow-up questions | Rewritten by a small, fast model when there is earlier conversation; original message used if the rewrite fails or times out. |
| Specialists | Retrieval Specialist; Fallback & Guardrail Specialist; Escalation / Human Handoff Coordinator. Rule-based routing, no LLM hop. |
| Actions | The AI has no tools with side effects. Escalation is submitted by the visitor. |
| Tool sources | Tools we build and run first; tenant tool servers in a later phase. |
| Unanswerable questions | Answer only from the tenant's content; otherwise say the information can't be found and show the tenant's support details from the dashboard. |
| Sources shown | Links for web pages only; no labels for uploads or database content. |
| Human handoff | Escalation request: transcript emailed to the tenant and shown in a dashboard inbox. Escalations kept 12 months by default (separate setting). |
| Monthly limit | Counts AI-answered messages; at 100% AI answers stop and the support-details message shows; tenant emailed at 80% and 100%. |
| Abuse defense | Rate limits only for now; a challenge can be added later. |
| Dropped connection | Cancel the run, show a retry button, no partial answer saved. |
| Dashboard login | Email and password, with an owner who can invite team members. |
| Widget look | Colors, logo, title, greeting and left/right position. |
| Hosting | One Ubuntu server with Docker Compose. |
| Trace text | Phoenix stores message text, auto-deletes after 30 days, and each tenant can switch it off. |
| Retention ranges | Chats: 1 to 90 days (default 30). Escalations: 1 to 24 months (default 12). |
| Plan page cap | Total indexed web pages per tenant (crawl and URL-list pages only), plus a per-crawl safety cap. Uploads are limited by storage and file size, and database sync by rows. |
| Databases | MySQL/MariaDB and PostgreSQL first. |
| Relevance threshold | Platform default; only the platform admin can override it per tenant. |
| Domain verification | Verified once, no re-checks; the platform admin can revoke or force re-verification. |
| Email | Sent through our own SMTP server, behind a provider-neutral interface. |
| Escalation trigger phrases | A YAML configuration file in the repo; every change goes through a deploy and the golden-set tests. |
| Revoked domain | Stops fetching only. Content already indexed from that domain stays answerable until the tenant or the platform admin disables or deletes the source, or the site is suspended. |
| Going live | A site key moves from draft to live only through a server-side check (at least one contact method and one allowed origin). Draft keys work from allowed origins with a test-mode badge. |

---

## 4. Identity, visitors and threads

### 4.1 Data model

| Table | Key columns |
|---|---|
| `tenants` | `id` (= `client_id`), `name`, `status`, `plan_id`, `config` (retention_days, escalation_retention_days, support details, escalation email, theme, relevance threshold, trace_text_enabled) |
| `site_keys` | `key` (`pk_live_…`, unique), `tenant_id`, `allowed_origins[]`, `environment`, `status` (draft, live or suspended) |
| `visitors` | `vid`, `tenant_id`, `site_key_id`, `secret_hash`, `first_seen_at`, `last_seen_at`, `external_user_id` (null, reserved) |
| `conversations` | `cid`, `tenant_id`, `vid`, `title`, `created_at`, `last_message_at` |

### 4.2 Session flow

1. The widget loads with `data-site-key` and calls `POST /api/v1/session {site_key, visitor_secret?}`.
2. The server requires an `Origin` header that exactly matches the key's allowlist (a missing `Origin` is rejected), and the key and tenant must be active. CORS echoes only the matched origin, with `Vary: Origin`.
3. **First visit:** create a visitor, generate a random 256-bit `visitor_secret`, store only its SHA-256 hash, and return the secret plus a JWT. **Return visit:** verify the secret and return a fresh JWT.
4. The JWT lives 15 minutes. Claims: `iss`, `aud=widget-api`, `sub=vid`, `tid=client_id`, `org=<origin>`, `exp`, with a `kid` header.
5. The session response also carries the tenant's public widget config (title, greeting, colors, logo URL, position, privacy notice link).
6. All other public endpoints require `Authorization: Bearer <jwt>`.

### 4.3 Rules

- **`client_id` never comes from the client.** It comes from `tid` in the verified JWT. Request models forbid extra fields.
- **JWT verification:** pinned algorithm (HS256), reject `none`, check `aud`, `iss` and `exp` (30s leeway), support key rotation via `kid`.
- **Signing keys:** a 256-bit secret from the environment or a secret file (never in the repository). The current and previous keys are both accepted, so rotating via `kid` causes no downtime.
- **Origin binding:** on every request `Origin` must equal the token's `org` claim.
- **Streams outlive tokens:** the token is checked when a stream opens.
- **Separate audiences:** widget JWTs are never accepted on admin endpoints. Dashboard auth is a different credential type.
- **Kill switch:** tenant and key status come from an in-process cache with a 60s TTL, so suspension takes effect within about a minute without a DB hit per request.
- **Storage:** the visitor secret is kept in the host site's `localStorage` under `aw:{site_key}`. The JWT is held in memory only. If storage is blocked, the widget falls back to memory and history is lost on refresh.

### 4.4 Accepted limits

- Origin checks stop browsers on other sites only. Scripted abuse with a spoofed `Origin` is bounded by rate limits and quotas (§9).
- With shadow DOM, JavaScript on the host page can read the visitor secret. The impact is limited to one visitor's chats on that one site.
- History is best-effort per browser. Clearing storage, private mode, or a browser clearing site storage after long inactivity loses it. Logged-in support (later phase) is the fix.

### 4.5 Threads and conversations

- **Thread ID** is built server-side: `thread_id = f"{client_id}:{vid}:{cid}"`.
- The client may send only a `conversation_id`. The server verifies it belongs to that tenant and visitor and returns **404** otherwise. `client_id`, `vid`, `thread_id`, `checkpoint_id` and `configurable` are never accepted from the client and are ignored if sent.
- **One guarded entry point:** only one function may call `graph.astream`, `aget_state` or `Command(resume=…)`. It builds the config itself and asserts the thread prefix matches the caller.
- **Tools never receive `client_id` from the model.** It comes from server-set config and stays out of tool schemas.
- **Later:** logged-in users via a token signed by the tenant's server, mapped to `external_user_id`.

### 4.6 Retention

- **Chats:** `retention_days` per tenant (default 30; allowed range 1 to 90 days). A nightly worker job deletes conversations idle longer than that, using the checkpointer's thread delete, and removes visitors left with nothing. Shortening the setting deletes older chats at the next sweep, so the dashboard warns the tenant. Old LangGraph checkpoints are pruned on the same schedule.
- **Escalations:** separate setting, default 12 months, allowed range 1 to 24 months (§7).
- `usage_events` never contain message content.

---

## 5. Knowledge sources and ingestion

### 5.1 One pipeline

Each source type is an adapter that produces normalized documents. Chunking, embedding and Qdrant writes are shared.

| Source type | Behavior |
|---|---|
| `urls` | Fetch a tenant-supplied list of pages (verified domains only). |
| `crawl` | Sitemap first, otherwise same-domain crawl on verified domains. `urls` and `crawl` can be combined. |
| `upload` | PDF, .docx, .txt/.md. |
| `database` | Scheduled sync of tenant-selected tables/views (§5.6). |

Chunking starts at **512 tokens with 64 overlap**, keeping the heading path. Each chunk gets a **dense** embedding (an English embedding model such as `text-embedding-3-small`) and a **sparse BM25** vector. The embedding model name and version are stored on every chunk.

### 5.2 Storage

- **Postgres (source of truth):** `sources` (id, tenant_id, type, config, refresh_interval, enabled, status, last_run_at), `verified_domains` (tenant_id, domain, method, verified_at, revoked_at), `documents` (id, tenant_id, source_id, url or file name, title, content_hash, etag, last_seen_at, status), `db_connections` (encrypted credentials, host, allowlisted tables and columns, row templates), `jobs` (queue), `plans`.
- **Qdrant:** one collection with named vectors `dense` and `sparse` on the same point (the sparse config uses the `idf` modifier so BM25 scoring is correct), a tenant-aware payload index on `client_id` (Qdrant 1.11 or later), and an index on `source_id`. Payload: `client_id`, `source_id`, `source_type`, `doc_id`, `source_url` (web pages only), `file_name` (internal only), `title`, `heading_path`, `chunk_index`, `content_hash`, `embedding_model`, `embedding_version`, `text`.
- **Qdrant is derived from Postgres.**

### 5.3 Jobs and consistency

- A Postgres-backed queue and a worker process. No Redis or Celery. Jobs are idempotent, retried with backoff, and their status shows in the dashboard.
- **Deterministic point IDs** (UUIDv5 from document, chunk index and embedding version), so a re-run overwrites instead of duplicating.
- A **nightly reconcile** compares Postgres with Qdrant per tenant and deletes orphaned points.
- **Deleting or disabling a source** removes or hides its points by `source_id`, and a test asserts none remain.

### 5.4 Refresh and plans

- Refresh interval is per source: daily, weekly, monthly or quarterly, plus **refresh-now** (with a cooldown and a daily allowance from the plan).
- The scheduler skips unchanged pages (ETag / Last-Modified, then content hash). Pages missing from two consecutive full crawls are deleted.
- **`plans`** holds limits in a `limits` JSON: minimum refresh interval, total indexed web pages per tenant (crawl and URL-list pages only), a per-crawl safety cap, upload storage and file size, maximum synced database rows, database sync minimum interval, refresh-now allowance, monthly AI-answered messages. Plans are assigned manually in an admin screen; payments come later. Limits are enforced **server-side**, and a downgrade adjusts existing schedules at their next run.

### 5.5 Crawler and URL-fetch safety

- Only **verified domains** are fetched. Subdomains count only if the tenant verifies them. Verification is done once, with its method and date recorded, and is not re-checked. The platform admin can revoke a verification or force a tenant to re-verify at any time. This is an action in the platform admin screen, backed by admin API endpoints (using the temporary admin key until Phase 6). It takes effect immediately (fetches from that domain stop), the tenant sees "re-verification required", and each action is recorded in an audit table. Revocation stops fetching only: content already indexed from that domain stays searchable and answerable until the tenant disables or deletes the source, or the platform admin disables the source or suspends the site.
- http/https only. DNS is resolved and **private, loopback, link-local and cloud-metadata addresses are rejected**. The check repeats after every redirect, and the connection is pinned to the validated IP.
- Limits: the plan's total indexed web-page cap (uploads and database rows have their own limits) plus a per-crawl safety cap (when the total is reached the crawler stops adding new pages, existing pages keep refreshing, the dashboard shows "limit reached", and deleting pages frees room), 5MB per page, 15s timeout, max 3 redirects, 2 concurrent requests per host with a delay.
- Honors `robots.txt`, supports path exclusions, and identifies itself with a fixed User-Agent.
- HTML cleanup strips scripts, styles and hidden elements. Nearly empty pages are flagged in the dashboard ("may need JavaScript").

### 5.6 Uploads and database sync

- **Uploads:** content type checked by content, not just extension; size and page limits from the plan; parsed in the worker, never in the API process; originals stored privately so re-embedding needs no re-upload; a corrupt file fails its job without crashing the worker.
- **Database connector:**
  - Read-only credentials, and the connection test warns if the account can write.
  - The tenant chooses tables/views and columns and edits a row-to-text template.
  - Credentials are encrypted at rest.
  - Connections come from the server's fixed outbound IP, with the same host checks as the crawler.
  - Statement timeouts and row limits apply.
  - MySQL/MariaDB and PostgreSQL first.
  - A maximum number of synced rows per plan.
  - Approved live queries are a later phase.

---

## 6. Agents, routing, retrieval and answers

### 6.1 Specialists

| Specialist | Job |
|---|---|
| **Retrieval Specialist** | Hybrid search over pages, uploads and synced database rows, then a grounded answer with web-page links. Covers products and pricing through the same search. |
| **Fallback & Guardrail Specialist** | Fixed replies (never model-written) for: no relevant answer (with the tenant's support details), greetings and thanks, unsafe or abusive input, and quota exhausted. Offers the escalation button when a person may be needed. |
| **Escalation / Human Handoff Coordinator** | Detects when a person is needed and offers the escalation flow (§7). |

### 6.2 Routing (rules, no LLM hop)

1. **Quota exhausted →** Fallback specialist (support details, escalation offered), no AI call.
2. **Greeting, thanks or unsafe/abusive input →** Guardrail reply.
3. **Escalation intent →** Coordinator.
4. **Otherwise →** `prepare → retrieve → gate → answer`.

Injection-looking input is logged and flagged for review but still goes through the normal path and its safeguards.

### 6.3 The answering path

- **State:** `MessagesState` with `thread_id` per §4.5, persisted by `AsyncPostgresSaver` (dedicated pool, `autocommit=True`, `row_factory=dict_row`; `setup()` runs at deploy).
- **prepare:** when the conversation has earlier messages, a small, fast model rewrites the follow-up into a standalone question. If the rewrite fails or exceeds a ~2s timeout, the original message is used. First messages skip this step.
- **retrieve:** the only code path that reads Qdrant, in one `retrieve(client_id, query, k)` function. All calls are async, and the filter is built per call, never in a global retriever.
  - **Hybrid query:** use the Qdrant Query API with a dense sub-query and a sparse sub-query, fused server-side with Reciprocal Rank Fusion.
  - **The `client_id` filter is applied inside both sub-queries**, and an isolation test covers it.
  - **Reranker hook:** behind a flag, off by default. When enabled it takes roughly 20 to 50 candidates and keeps the top few.
  - The golden set measures whether the right chunk appears in the top results, and the reranker is enabled only if that number is poor.
- **gate:** if the best hit is below the relevance threshold, skip the AI call and return the fallback. The threshold is a platform default that only the platform admin can override per tenant (it is not tenant-editable), and it is tuned in Phase 3 using the golden set. It is stored as a nullable per-tenant field in `tenants.config` (the platform default lives in configuration), edited only in the platform admin screen, never exposed on tenant endpoints, and every change is logged.
- **answer:** the model returns `{can_answer, answer, used_chunk_ids}`. If `can_answer` is false, the server returns the fallback.
- **Sources shown:** the server builds citations from chunk metadata, for web pages only. No labels for uploads or database content.
- **Fallback text:** a template filled from the dashboard's support details (email, phone, contact link, hours, optional message). **Never written by the model.** A site key moves from `draft` to `live` only through a server-side check that requires at least one contact method and at least one allowed origin; the dashboard shows the same requirement, but the server enforces it. Draft keys work from allowed origins and show a test-mode badge, so tenants can try the widget on a staging site.
- **Models:** per-tenant config via `init_chat_model`, with `.with_fallbacks()` to a secondary provider.

---

## 7. Human handoff (escalation)

- **Triggers (rules, no AI call):** the visitor asks for a person or makes a complaint, two fallbacks happen in a row, or strongly negative wording appears. The phrases live in a YAML file in the backend config folder (English only, roughly 50 to 200 entries; each entry has a phrase or pattern, a category of asks-for-a-person, complaint or negative, and a reference to its golden-set test). It is loaded at startup and validated against a schema in the tests, so every change goes through a deploy and the golden-set tests (tenant-specific phrases and a database-backed list are deferred). The widget then shows a "Talk to a person" button next to the support details.
- **The AI cannot submit anything.** The visitor taps the button, a panel shows exactly what will be shared (a consent notice), the visitor may add optional contact details, and the visitor's browser submits `POST /api/v1/conversations/{cid}/escalate` as a normal authenticated request.
- **Storage:** `escalations` (id, tenant_id, conversation_id, vid, reason, optional contact name/email/phone, transcript snapshot, status new/seen/resolved, created_at). Transcripts are stored as text and always escaped on output.
- **Delivery:** an email with the transcript goes only to the tenant's configured escalation address, never to a visitor-supplied one. It also appears in the dashboard inbox, where the tenant can mark status and delete.
- **Limits:** 3 escalations per rolling hour per visitor (a hard rejection with 429 and `Retry-After` at the limit), one open escalation per conversation, and a per-tenant hourly email cap. Escalations don't count against the AI message quota.
- **Retention:** separate setting, default 12 months (allowed range 1 to 24 months); a nightly sweep deletes expired requests, and tenant deletion cascades.
- **Later:** live handoff in real time, built on the same escalation record.

---

## 8. Tools and prompt-injection defense

- **The AI has no tools with side effects.** In v1 the only tool is knowledge search. Approved live database queries and tenant tool servers are later phases.
- **One tool wrapper** applies a per-tenant allowlist, a timeout, an output size cap, and treats all output as untrusted text. Adding tenant tool servers later means adding network checks and a credential vault, not a redesign.
- **Fixed platform rules:** tenants customize name, tone and greeting, never the safety rules.
- **Data, not instructions:** retrieved text and tool output sit in delimited data blocks, and the model is told to treat them as data.
- **Links and images:** the widget strips images and allows links only to cited pages and the tenant's verified domains. This closes the image-based data-leak trick.
- **Limits:** messages capped at 2,000 characters, capped history window, output length and agent steps.
- **Detection:** suspicious inputs are logged and flagged, not blocked. No separate guard model.
- **Isolation** is enforced by the Qdrant filter and server-set config, never by the prompt.

---

## 9. Abuse and cost controls

**Default limits** (configurable, to be tuned with real data):
- `/api/v1/session`: 10 per minute per IP and site key.
- `/api/v1/chat`: 10 messages per minute per visitor, 1 concurrent stream per visitor, a per-IP cap across visitors, and a per-site burst cap from the plan.
- Hidden per-message token cap; max agent steps.

**Order of checks** (cheapest first): JWT, cached tenant/plan status, rate limit, quota, then embedding and the AI call.

**Quota:** monthly AI-answered messages by plan. Fallback replies and escalations are free and don't count. Only the API process serves chats, so it holds the counters in memory, updates them locally per message, and refreshes them from `usage_events` about every minute (small overshoot accepted). Usage rows are written in batches. On startup the API reloads the current month's usage before serving chats, so a restart cannot reset counters. Until the reload finishes, `/api/v1/chat` returns 503 with `Retry-After`, so no chat is answered over quota. Only chat is gated: the container health check stays healthy, and the session, history, escalation and admin endpoints keep working; a crash can lose a few seconds of unwritten usage, which is accepted. At 100% the widget shows the support-details message, and the worker emails the tenant's dashboard contact at 80% and 100%, once per threshold per month.

**Implementation:**
- An in-process limiter behind a `RateLimiter` interface on a single API instance. This is a temporary design: §17 defines when to move the limiter and counters to Redis (when running more than one API instance).
- 429 responses carry `Retry-After`, and the widget shows a short note.
- Admin controls: suspend a site, a global switch to turn off AI calls, and an alert on message-rate spikes. A challenge (for example Turnstile) can be added at session creation later.
- Per-tenant token and cost view for the platform owner, built from `usage_events`.

---

## 10. Streaming API

**Endpoints (public, JWT):**
- `POST /api/v1/session`
- `POST /api/v1/chat {message, conversation_id?, client_message_id}`: omitting `conversation_id` starts a new conversation.
- `GET /api/v1/conversations`: powers "previous chats".
- `GET /api/v1/conversations/{cid}/messages`: history, showing completed turns only.
- `POST /api/v1/conversations/{cid}/escalate`

**Streaming:**
- `EventSourceResponse` (`sse-starlette`) with `ping=15`; headers `Cache-Control: no-cache` and `X-Accel-Buffering: no`; the reverse proxy must not buffer `/api/v1/chat`.
- Auth, rate-limit and quota failures return real HTTP codes (401, 413, 429) **before** the stream opens.
- LangGraph `stream_mode=["messages","custom","updates"]`: `messages` becomes `token`, `custom` (via the stream writer) becomes `status`, and the final state becomes `citations`, then `done`.

| Event | Payload |
|---|---|
| `status` | `{text}` |
| `token` | `{text}` |
| `citations` | `{items: [{n, title, url}]}` |
| `done` | `{message_id, usage}` |
| `error` | `{code, message}`, no stack traces |

Every event carries an incrementing `id` and `v: 1`; the server supports the previous schema version for one release.

**Dropped connections:** the server cancels the run (no wasted tokens) and does not persist the partial answer. The widget shows a retry button, and a retry reuses the same `client_message_id`, so the message never appears twice. There is no stream resume in v1.

**Concurrency:** async end to end, no blocking calls in handlers, DB connections acquired per operation and never held while streaming.

---

## 11. Widget

**Loader (`widget.js`, plain JS, about 4KB gzipped):** reads `data-site-key`; renders the launcher in a small shadow root; loads the chat UI on first click, so there is no extra cost before that; short cache time. The chat UI's assets are content-hashed and cached as immutable.

**Chat UI (Preact, inside the shadow DOM, about 60KB gzipped JS):**
- Streams with `fetch` and a small SSE parser (`EventSource` is GET-only).
- Answers are rendered as sanitized markdown with raw HTML disabled; images are stripped; links open with `target="_blank" rel="noopener noreferrer nofollow"` and only to allowed destinations.
- The token is held in memory only, and the visitor secret is stored per §4.3.
- Includes the "previous chats" list, retry button, escalation panel with consent notice, and a privacy notice link.
- **Accessibility (WCAG 2.1 AA):** the message list is `role="log"` with `aria-live="polite"`, announcing completed messages rather than every token; keyboard support, Esc to close, visible focus, `prefers-reduced-motion`, AA contrast.
- Works on sites with a strict CSP (no inline scripts, no `eval`); English string table; full-screen sheet below 480px.

**Customization (tenant, dashboard):** colors, logo, title, greeting, and left or right position. Logos are PNG, JPEG or WebP with a size cap, served from our domain; SVG is rejected. No custom CSS in v1.

---

## 12. Dashboard and admin

React SPA served statically.

- **Login:** email and password, with argon2id hashing, mandatory email verification before the first login (links last 24 hours, resend is limited to 3 per hour, and unverified accounts and unaccepted invites are purged after 7 days; if the purged account was the only owner of a self-signup tenant with no content, the tenant is purged with it, and an admin-created tenant that has data is marked "ownerless" in the admin view so an owner can be re-invited), reset by email, rate-limited login with temporary lockouts, and dashboard sessions kept separate from widget tokens. Each website has an owner who can invite team members. Optional 2FA and Google sign-in come later. Use a maintained auth library rather than hand-rolling.
- **Screens:**
  - sources and ingestion status, with add-source warnings, domain verification, per-source disable/delete/refresh-now, and "may need JavaScript" flags;
  - support details and escalation email;
  - widget look and greeting;
  - retention settings (chats, escalations) and the trace-text switch;
  - **escalation inbox** (list, transcript, status, delete);
  - usage against plan limits.
- **Platform admin (you):** manage tenants and plans, suspend a site, override the relevance threshold per tenant, revoke or force re-verification of a domain, disable or delete any tenant's source, see email delivery failures (a global list filterable by tenant, plus each tenant's failures on its admin page), view per-tenant cost, and the global AI switch.
- **Temporary admin key:** during Phases 2 to 5 the admin endpoints (including domain revocation) are protected by a single secret from the environment. Step 6.1 removes it and replaces it with platform-admin accounts (a `platform_admin` role) that use the same login system as tenant users.
- Admin endpoints live under `/api/v1/admin`, with tenant scope derived from membership, never from request parameters.

---

## 13. Observability, evaluation and data protection

**Phoenix (self-hosted):**
- `phoenix.otel.register` at startup with a **batching** span processor (non-blocking export), a flush on shutdown, and a startup check that the collector is reachable.
- Instrumentors: OpenInference for LangChain/LangGraph and the LLM SDK, OpenTelemetry for FastAPI (exclude `/health`; one span per request, not per token), and a manual retriever span inside `retrieve()`. **Verify each instrumentor exists and is maintained before relying on it**, including any Qdrant instrumentation, and fall back to manual spans.
- Spans are tagged with tenant and visitor. Trace sampling is configurable (full in staging, reduced in production).
- **Text in traces:** stored for 30 days, then auto-deleted. The tenant can switch it off, and the switch is applied per request before spans are exported. Tenants whose chat retention is strictly less than 30 days (1 to 29) get trace text switched off automatically, so traces never outlive their chats; at exactly 30 days it stays on, because traces and chats expire together. The rule applies to new traces from the moment retention changes, and older trace text ages out within 30 days. Tenants with longer chat retention lose nothing important: traces simply expire earlier than the chats.

**Evaluation:**
- A golden set per pilot tenant covering answerable questions, unanswerable ones, follow-ups, escalation triggers, and injection attempts.
- Offline evals run nightly and whenever a prompt changes, using `phoenix.evals` for hallucination and relevance, plus a retrieval check (is the right chunk in the top results). A release is blocked if hallucination or injection-failure rates regress past a threshold.
- Judges are calibrated against a small human-labelled set before they gate anything.
- Online evals sample a small share of traces asynchronously in the worker, never in the request path.

**Data protection:**
- Retention: chats (default 30 days, range 1 to 90), escalations (default 12 months, range 1 to 24 months), traces (30 days), `usage_events` (metadata only).
- Tenant deletion cascades to sources, documents, vectors, conversations, threads, escalations and usage.
- The widget shows a configurable privacy notice, and the escalation flow shows a consent notice.
- Sub-processors (LLM, embeddings, email, hosting) are documented. The design supports deletion and access requests under Kenya's Data Protection Act and GDPR; legal review is outside this build.

---

## 14. Deployment and operations (v1)

**One Ubuntu server with Docker Compose.** Services: `proxy` (Caddy or Nginx: TLS, buffering off for `/api/v1/chat`, long idle timeouts), `api`, `worker`, `postgres`, `qdrant`, `phoenix`.

- **Starting size:** roughly 4 vCPU and 8 GB RAM (an estimate to be measured). Budgets count as not met when any §17 budget is missed in two consecutive load-test runs at the target concurrency. The response is one resize to the next size up (the host must support resizing) followed by a re-test; if the budgets are still not met, move Postgres and Qdrant to a second server before go-live.
- **Fixed outbound IP:** the server's static IP, given to tenants for their database firewalls.
- **Backups:** nightly Postgres backups and Qdrant snapshots stored off the server, provider snapshots enabled, and a **restore drill before real tenants go live**.
- **Rebuildable:** the whole setup can be recreated from the Compose file and documented secrets.
- **Monitoring:** uptime checks with alerts, disk and memory alerts, and the spike alert from §9.
- **Database connections:** separate pools (about 10 app, 10 checkpointer, 5 worker), well under Postgres's default connection limit.
- **Migrations:** Alembic, run as a separate deploy step.
- **Secrets:** environment or secret files, never in the repository.
- **Email:** sent through our own SMTP server behind a provider-neutral email interface, so a hosted service can replace it later. Requirements: SPF, DKIM and DMARC for the sending domain, reverse DNS on the sending IP, TLS, a send queue, failure monitoring, and confirmation that the hosting provider allows outbound mail (many block port 25 by default). Retry policy: 5 attempts with exponential backoff (about 1 minute, 5 minutes, 30 minutes, 2 hours, 12 hours), then the email is marked failed and shown in the platform admin view; every outcome is stored in `email_log`. v1 records synchronous SMTP results only. Reading bounces from a return-path mailbox is deferred, because the dashboard inbox is the source of truth for escalations and email is only a notification.

---

## 15. Data model (summary)

`tenants`, `users`, `memberships`, `site_keys`, `verified_domains`, `visitors`, `conversations`, `plans`, `sources`, `documents`, `db_connections`, `jobs`, `escalations`, `usage_events`, `email_log` (send outcomes, retries and dedupe for quota alerts), `audit_log` (admin actions such as domain revocations, threshold overrides and plan changes), LangGraph checkpoint tables, and the Qdrant collection described in §5.2.

---

## 16. Testing, CI and security acceptance tests

**Tooling:** pytest with real Postgres and Qdrant containers; Playwright for widget end-to-end tests; k6 for streaming load tests; golden-set evals (§13). The isolation and security suite below **must pass before any merge**.

**Identity and threads**
- A tenant A token can never return tenant B's chunks, including through the hybrid query's dense and sparse sub-queries.
- Visitor A can't read or resume visitor B's conversation, even within the same tenant.
- Another visitor's `conversation_id` returns 404, and client-sent `thread_id`, `client_id` or `configurable` is ignored.
- `/api/v1/session` from a non-allowlisted or missing `Origin` returns 403, and a token replayed with a different `Origin` returns 401.
- Expired, forged, `alg=none` and wrong-`aud` tokens return 401, and a widget token on admin endpoints returns 401.
- A model-generated tool call containing `client_id` has no effect.
- A suspended tenant is blocked within the cache TTL, and the retention sweep deletes expired chats and nothing newer.

**Ingestion and crawler**
- Crawling an unverified domain is rejected.
- URLs resolving to `127.0.0.1`, `10.x` or `169.254.169.254` are blocked, including through redirects and DNS rebinding.
- A refresh re-embeds only changed pages, and deleting a source leaves zero vectors for its `source_id`.
- Tenant A can't list or trigger tenant B's sources.
- A daily interval is rejected on a weekly-only plan, and a downgrade adjusts schedules.
- Oversized or mislabelled uploads are rejected, and a corrupt PDF fails its job without crashing the worker.
- The database connector cannot write or read outside its allowlist.
- The reconcile job removes orphaned Qdrant points.
- The plan's total indexed web-page cap stops new pages while existing pages keep refreshing, and deleting pages frees room; uploads and database rows do not count toward it.
- A revoked domain verification blocks all fetches from that domain.
- Revoking or forcing re-verification through the admin action takes effect immediately, is recorded in the audit table, and leaves content already indexed from that domain answerable.

**Abuse and cost**
- The 11th message in a minute returns 429 with `Retry-After`, and a second concurrent stream is refused.
- A session-creation flood from one IP is blocked, and many new visitors from one IP hit the IP cap.
- At 100% quota the visitor gets the fallback with no AI call, and one email is queued.
- Fallback replies and escalations don't count toward the quota, oversized messages return 413, and a rate-limited request makes no embedding or AI call.
- After an API restart, quota counters reload from `usage_events` and do not reset to zero.
- Chat requests that arrive before the quota reload finishes get 503 with `Retry-After`, and none is answered over quota. The session endpoint keeps working during that window.
- A failing SMTP send is retried on the schedule in §14, then marked failed in `email_log`.
- Trace text is switched off for a tenant at 29 days of chat retention and stays on at 30.

**Escalation**
- A visitor can't escalate another visitor's conversation (404).
- The rate limit and one-open-per-conversation rule hold.
- Emails go only to the tenant's configured address.
- HTML in messages is escaped in emails and in the dashboard inbox.
- No AI path can submit an escalation.
- Every entry in the trigger-phrase file has a golden-set test case.
- The 4th escalation within a rolling hour returns 429 with `Retry-After`.
- Inbox access is tenant-scoped.

**Streaming**
- A dropped connection cancels the run and leaves no partial answer in history.
- A retry with the same `client_message_id` doesn't duplicate the message.

**Injection**
- Direct override and system-prompt extraction attempts fail.
- Instructions planted in an ingested page or an uploaded file don't change behavior.
- Markdown image and link leak attempts are neutralised.
- Cross-tenant probing and questions about upload file names reveal nothing.
- Off-topic questions all reach the fallback.

**Dashboard**
- Login rate limiting and lockout work, and a password reset link is single-use.
- Login is refused until the email address is verified.
- Unverified accounts and unaccepted invites are purged after 7 days, and resend is limited to 3 per hour; a purged sole owner removes an empty self-signup tenant and marks an admin-created tenant with data as ownerless.
- A site key cannot move to `live` without at least one contact method and one allowed origin, even when the request is made directly to the API.

---

## 17. Performance budgets and scale-out triggers

*Starting targets; validate with k6 load tests and revise from real data.*

| Budget | Target |
|---|---|
| Session creation | p95 under 100ms (no external calls) |
| Time to first token | First turn p50 under 1.5s, p95 under 3s. Follow-ups p50 under 2s (includes the rewrite step) |
| Retrieval (hybrid) | p95 under 100ms at pilot scale |
| Concurrency | Starting goal of 200 concurrent streams on the single server |
| Widget | Loader about 4KB gzipped, chat UI about 60KB gzipped, no requests before the launcher click beyond the loader |

The budgets table above drives pre-launch sizing under the §14 rule. The scale-out triggers below drive post-launch operations. A trigger reached during Phase 7 load testing is recorded as a finding, and it forces action before go-live only if it also causes a budget miss.

| Signal | Action |
|---|---|
| API CPU above 70% sustained | Run more API instances behind the proxy and move the rate limiter and counters to Redis |
| Queue lag above 5 minutes | Add worker instances (same image) |
| Retrieval p95 above 150ms | Tune Qdrant indexing and payload filters, then review capacity |
| Postgres connections above 70% of the limit | Add PgBouncer (transaction mode; set `prepare_threshold=0` for the checkpointer) |
| Single server no longer enough, or uptime commitments require it | Move Postgres and Qdrant to managed services or separate hosts |
| Time to first token regresses | Inspect the rewrite step and the reranker flag first |

---

## 18. Phases, steps and acceptance criteria

Each step is built as a small set of tasks, one at a time. Steps tagged [SECURITY] protect tenant data or handle untrusted input and receive an independent review.

**Phase 0: Preparation.** Understand the spec and create the project context file.
*Steps:* 0.1 Understand the spec; 0.2 Create PROJECT_SPEC.md.

**Phase 1: Foundations.** Repo layout, Docker Compose, FastAPI, Postgres pools, Alembic, Qdrant collection (dense and sparse vectors, indexes), tenants, site keys, visitors, conversations and plans tables, JWT session endpoint, rate limiter, CI.
*Steps:* 1.1 Repo skeleton and Compose; 1.1b Whole-application skeleton; 1.1c Marker check; 1.2 Database, migrations and tenant-scoped access; 1.3 Qdrant collection [SECURITY]; 1.4 Session endpoint and JWT [SECURITY]; 1.5 Rate limiter and limits; 1.6 Phase 1 acceptance.
*Accept:* `docker compose up` gives a healthy stack; session flow and identity tests pass.

**Phase 2: Ingestion.** Sources, domain verification, worker and queue, adapters (URL list, crawl, upload, database sync), chunking, dense and sparse embedding, Qdrant upserts, refresh scheduler with plan gating, reconcile job, SSRF guard.
*Steps:* 2.1 Ingestion tables and job queue; 2.2 Domain verification [SECURITY]; 2.3 Safe fetcher (SSRF guard) [SECURITY]; 2.4 Extraction and chunking; 2.5 Embedding and Qdrant upserts; 2.6 URL-list and crawl adapters; 2.7 Upload adapter [SECURITY]; 2.8 Database sync adapter [SECURITY]; 2.9 Scheduler, plans and reconcile; 2.10 Phase 2 acceptance.
*Accept:* all ingestion and crawler tests pass; a recrawl re-embeds only changed pages.

**Phase 3: Agent engine.** `AsyncPostgresSaver`, guarded entry point, router and the three specialists, follow-up rewrite, hybrid `retrieve()` with the reranker hook, gate and fallback, structured output, tool wrapper with knowledge search via MCP, first golden set.
*Steps:* 3.1 Hybrid retrieval [SECURITY]; 3.2 Checkpointer and guarded entry point [SECURITY]; 3.3 Graph skeleton and router; 3.4 Answer node, citations and fallback; 3.5 Follow-up rewrite; 3.6 Tool wrapper and MCP knowledge search [SECURITY]; 3.7 Golden set and eval runner; 3.8 Phase 3 acceptance.
*Accept:* identity and injection suites pass; unanswerable questions hit the fallback without an AI call; retrieval quality is measured on the golden set.

**Phase 4: Streaming, metering and handoff.** SSE chat and conversations endpoints, escalation endpoint and email, `usage_events`, quotas and alert emails, Phoenix tracing, trace-text switch.
*Steps:* 4.1 Chat endpoint (SSE) [SECURITY]; 4.2 Conversations endpoints and retention sweep; 4.3 Usage, quotas and alert emails; 4.4 Escalation [SECURITY]; 4.5 Phoenix tracing; 4.6 Phase 4 acceptance.
*Accept:* streaming, abuse and escalation tests pass; tracing adds no measurable latency to the request path.

**Phase 5: Widget.** Loader, Preact chat UI, previous chats, retry, escalation panel, customization, accessibility.
*Steps:* 5.1 Loader and shadow DOM shell; 5.2 Chat UI core [SECURITY]; 5.3 Conversations UI; 5.4 Escalation panel and fallback UI; 5.5 Theming, accessibility and CSP; 5.6 Phase 5 acceptance.
*Accept:* budgets met; keyboard-only and screen-reader pass; the widget works on a strict-CSP test page.

**Phase 6: Dashboard.** Auth (replacing the temporary admin key), settings, sources and verification, escalation inbox, usage, platform admin.
*Steps:* 6.1 Dashboard authentication [SECURITY]; 6.2 Tenant settings; 6.3 Sources screens; 6.4 Escalation inbox and usage; 6.5 Platform admin; 6.6 Phase 6 acceptance.
*Accept:* a new tenant can onboard end to end without touching the database.

**Phase 7: Hardening and go-live.** k6 streaming load test, full security and injection suites, security review, backup and restore drill, monitoring and alerts.
*Steps:* 7.1 Load tests; 7.2 Full security and injection run [SECURITY]; 7.3 Backups, restore drill and monitoring; 7.4 Go-live checklist and spec reconciliation.
*Accept:* every target in §17 is measured and recorded (any budget not met under the §14 rule, meaning missed in two consecutive runs, triggers the resize-or-split steps before go-live), the restore drill has been run successfully, email deliverability has been tested against major mailbox providers, and there are no open high-severity findings.

---

## 19. Deferred to later phases

Logged-in visitors; live human handoff; approved live database queries; tenant-supplied tool servers; actions with human approval; JavaScript-rendered crawling; a request challenge (for example Turnstile); the reranker (enabled only if evals justify it); spreadsheet uploads; payment processing; multiple languages; custom widget CSS; 2FA and Google sign-in for the dashboard; stream resume.

---

## 20. Estimates and accepted assumptions

All open assumptions were confirmed by the project owner (database types, retention ranges, email approach). What remains are estimates to measure:

- The default limits in §9 and the budgets in §17 are starting points.
- The server size in §14 is an estimate.
- The rule that trace text is switched off automatically for tenants with chat retention under 30 days is accepted.
- API paths use the `/api/v1` prefix.

---

## 21. Repository layout and engineering rules

```
backend/    app/{auth,tenancy,plans,chat,agent,retrieval,ingest,handoff,notifications,mcp,admin,telemetry}/  tests/  alembic/
widget/     loader/  chat/
dashboard/
evals/      golden sets, runners
deploy/     docker-compose.yml  Caddyfile  backup scripts
```

**Commands:** `make up`, `make test`, `make lint`, `make migrate`, `make evals`, `make hooks` (lists TODO, ASSUMPTION, UNCERTAIN and NOTE markers in the code).

**Engineering rules (never broken):**
- Tenant identity comes only from the verified token. Never accept `client_id`, visitor id, `thread_id` or checkpoint ids from the client. Verify `conversation_id` by database lookup.
- All Qdrant reads go through `retrieve()`, and the `client_id` filter is applied inside every sub-query.
- All tenant-scoped database access goes through the tenant-scoped repository helpers.
- The model never writes contact details and has no tool with side effects. Escalation is submitted by the visitor's browser.
- Everything is async. No blocking calls in handlers. Never hold a DB connection while streaming.
- Migrations via Alembic only. Secrets never in the repository.
- Treat all ingested text, tool output and visitor messages as untrusted input.
- Nothing hard-coded that should be configuration (limits, thresholds, paths, URLs).
- Any deviation from this spec is recorded in the decisions log (section 3) in the same change.
- After each phase, run the acceptance checks and summarize deviations before starting the next.
