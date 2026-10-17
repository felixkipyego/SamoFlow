// Task 5.1/5.2: a tiny static file + fake-API server for the Playwright
// test, standing in for the tenant's own site AND (Task 5.2) a minimal
// stand-in for the real backend -- the widget track never runs the
// real backend containers (track rule 2), so this is deliberately NOT
// trying to be a real implementation of docs/SPEC.md §10, only enough
// of the real wire shape (SSE event names/payloads, as read directly
// from backend/app/chat/routes.py at Step 5.1 Part C's own contract
// check) to drive the chat UI through one real, visible conversation
// end to end under a real strict CSP.
//
// [SECURITY] Step 7-follow-up, Part B: this is now genuinely TWO
// origins, not one -- a WIDGET_PORT server (the "tenant's own site":
// static HTML/CSS/JS, the host page's own CSP) and a separate API_PORT
// server (the "SamoFlow API": every /api/v1/* fake endpoint), each
// started here but listening on different ports/origins. Before this,
// both lived on ONE port, so every widget fetch() was same-origin and
// the browser never enforced a real CORS preflight at all -- this is
// the exact, confirmed-live root cause of why this whole suite kept
// passing while real cross-origin CORS gaps shipped (the widget launcher
// color/stuck-input bug fix's own register entry, D-conversations-
// escalate-cors-bugfix). Splitting the ports is what makes a REAL
// browser CORS check unavoidable in every test from here on.
//
// Sets a real strict Content-Security-Policy RESPONSE HEADER (not just
// a <meta> tag, for full fidelity to a real tenant deployment) with no
// 'unsafe-inline' and no 'unsafe-eval' anywhere -- the exact bar
// docs/SPEC.md §11 requires the widget to work under; `connect-src` now
// explicitly names the separate API origin too, matching what a real
// tenant's own CSP would have to declare to let the widget call a
// cross-origin API at all. Stdlib only (node:http) -- no new dependency
// for this test helper.
import http from "node:http";
import crypto from "node:crypto";
import { readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const widgetRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "..",
);
const publicDir = path.join(widgetRoot, "test/e2e/public");
const loaderDistDir = path.join(widgetRoot, "dist");
const chatDistDir = path.join(widgetRoot, "chat/dist");

// [ops bugfix]: the single committed source of truth for POST /api/v1/
// chat's own four real SSE event shapes (docs/chat_sse_contract.json's
// own "_comment" has the full reasoning) -- loaded once, here, so
// sseFrame() below can enforce it on every single frame this fixture
// ever builds, for the rest of this file. This is the fix for BUG2's
// own 4th question (why the real widget broke despite a passing e2e
// suite): before this, this fixture typed its own payloads completely
// independently of the real backend, with nothing to catch either side
// drifting from the other -- confirmed live, that is exactly how the
// `done` event's real `conversation_id` field went missing here for as
// long as it did.
const _sseContract = JSON.parse(
  readFileSync(path.join(widgetRoot, "..", "docs", "chat_sse_contract.json"), "utf8"),
);

// [SECURITY] Step 7-follow-up, Part B: the same contract-sharing
// pattern as the SSE one above, applied to CORS -- backend/app/auth/
// cors.py's own WIDGET_ROUTES is the real mechanism; backend/tests/
// test_widget_cors_contract.py asserts it matches this exact file; this
// fixture's own corsHeadersFor()/preflightFor() below build their
// answers FROM it, so this mock cannot silently advertise a method/
// header the real backend does not, or vice versa.
const _corsContract = JSON.parse(
  readFileSync(path.join(widgetRoot, "..", "docs", "widget_cors_contract.json"), "utf8"),
);
const _corsRoutes = Object.entries(_corsContract)
  .filter(([key]) => !key.startsWith("_"))
  .map(([name, entry]) => ({ name, pattern: new RegExp(entry.path), ...entry }));

function _matchCorsRoute(pathname) {
  return _corsRoutes.find((route) => route.pattern.test(pathname)) ?? null;
}

const WIDGET_PORT = Number(process.env.PORT || 4173);
const API_PORT = Number(process.env.API_PORT || 4174);
const WIDGET_ORIGIN = `http://127.0.0.1:${WIDGET_PORT}`;
const API_ORIGIN = `http://127.0.0.1:${API_PORT}`;

const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  // The widget's own fetch() calls now go to a genuinely different
  // origin (API_ORIGIN) -- a real tenant's own CSP would need to name
  // it explicitly too; 'self' alone would NOT let this page's own
  // script talk to a separate API origin.
  `connect-src 'self' ${API_ORIGIN}`,
  "img-src 'self'",
  "base-uri 'none'",
].join("; ");

// [SECURITY] Step 7-follow-up, Part B: the fake API's own CORS
// mechanism -- mirrors app.auth.cors.WidgetCORSMiddleware's real rules
// (backend/app/auth/cors.py), simplified for this fixture's own single
// simulated tenant/site-key/origin: ANY request whose real Origin
// header is NOT exactly WIDGET_ORIGIN is treated as disallowed (no
// per-site-key allow-list lookup needed here, since this fixture only
// ever represents one real site key, matching the real demo stack's own
// single-tenant shape). Never a wildcard, never echoes a disallowed
// origin, never sends Access-Control-Allow-Credentials (the widget
// authenticates with an in-memory Bearer token only, same reasoning as
// the real backend's own cors.py header).
function _realOrigin(req) {
  const raw = req.headers.origin;
  return typeof raw === "string" && raw.length > 0 ? raw : null;
}

function _isAllowedOrigin(origin) {
  return origin === WIDGET_ORIGIN;
}

function preflightFor(route, req, res) {
  const origin = _realOrigin(req);
  const headers = {
    "Access-Control-Allow-Methods": route.methods,
    "Access-Control-Allow-Headers": route.headers,
    Vary: "Origin",
  };
  if (origin) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  res.writeHead(200, headers);
  res.end();
}

// Applied to EVERY fake-API response below (success or error) for a
// route the contract recognises -- Vary: Origin always; Access-Control-
// Allow-Origin only when the real Origin header is the one allowed
// origin, matching "a disallowed origin gets no ACAO on any branch,
// success or error."
function corsHeadersFor(req) {
  const origin = _realOrigin(req);
  const headers = { Vary: "Origin" };
  if (origin && _isAllowedOrigin(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  return headers;
}

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

const DIST_FILES = {
  "/widget.js": path.join(loaderDistDir, "widget.js"),
  "/widget.css": path.join(loaderDistDir, "widget.css"),
  "/chat.js": path.join(chatDistDir, "chat.js"),
  "/chat.css": path.join(chatDistDir, "chat.css"),
};

// [ops bugfix]: every SSE frame this fixture ever builds passes through
// here -- the one real chokepoint that makes enforcing the shared
// contract possible without touching every individual call site. `v: 1`
// is now added here, matching the real backend's own `_sse_event()`
// (backend/app/chat/routes.py) exactly, so a call site never has to
// remember to add it itself.
function sseFrame(event, data) {
  const contractFields = _sseContract[event];
  if (!contractFields) {
    throw new Error(`sseFrame: "${event}" is not a known SSE event type in docs/chat_sse_contract.json`);
  }
  const fullData = { ...data, v: 1 };
  const realFields = Object.keys(fullData).sort();
  const expectedFields = [...contractFields].sort();
  if (JSON.stringify(realFields) !== JSON.stringify(expectedFields)) {
    throw new Error(
      `sseFrame: "${event}" event fields ${JSON.stringify(realFields)} don't match the ` +
        `committed contract ${JSON.stringify(expectedFields)} (docs/chat_sse_contract.json) -- ` +
        "if the real backend's own shape changed deliberately, update the contract file, " +
        "backend/tests/test_chat_routes.py's own matching test, AND this fixture together.",
    );
  }
  return `event: ${event}\ndata: ${JSON.stringify(fullData)}\n\n`;
}

function _bearerToken(req) {
  const header = req.headers.authorization || "";
  return header.startsWith("Bearer ") ? header.slice("Bearer ".length) : null;
}

function readJsonBody(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
    });
    req.on("end", () => {
      try {
        resolve(JSON.parse(raw));
      } catch {
        resolve({});
      }
    });
  });
}

// Task 5.3: lets the e2e test force the FIRST attempt at a given
// client_message_id to fail (503), then succeed on the retry that
// reuses the identical id -- a real, stateful proof of the retry
// button's own real behavior, not a UI-only simulation.
const _failedOnce = new Set();
const _escalatedConversations = new Set();
let _lastEscalationContactFields = null;

// [ops bugfix]: lets the "3-turn conversation, turn 2 depends on turn 1"
// e2e test prove the widget carries a real conversation_id forward
// across turns -- not the model's own reasoning (that is a backend/eval
// concern), but the one thing this mock can deterministically prove:
// turn 2's own answer differs depending on which `conversation_id` its
// /chat request actually carried, keyed off what THIS exact conversation
// was told on a previous turn. A real backend would use its own
// checkpointed graph state for this; this mock's own, much simpler
// stand-in is a plain per-conversation map.
const _lastMessageByConversation = new Map();

// Test-isolation, found and fixed live during this step's own e2e run
// (docs/progress/widget.md has the full story): Playwright's
// `fullyParallel` config runs every test, across every spec FILE,
// against this ONE shared server process -- a session_token/
// conversation_id FIXED across every test let one test's own
// escalation/retry state leak into a completely unrelated later test
// (a real 409 appearing where a fresh test expected a clean 200). Each
// POST /session now mints a genuinely fresh, random session_token AND
// conversation_id, so two tests running concurrently (or in any order)
// against this same server can never share state unless they
// deliberately reuse the same token, which no real test does.
const _sessionConversations = new Map();

// Part D (docs/SPEC.md §9's "Stop mid-stream"): mirrors the real
// backend's own `_get_stop_requests()` (backend/app/chat/routes.py) --
// a per-visitor (here, per bearer token, this fixture's own stand-in
// for a real vid) flag set by POST /api/v1/chat/stop and read by the
// "trigger stoppable stream" branch below between each delayed token
// write, so an e2e test can prove the SAME explicit-stop mechanics the
// real backend implements, not merely that the widget's own
// AbortController stops rendering locally.
const _stopRequests = new Set();

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function handleFakeApi(req, res, pathname) {
  // [SECURITY] Step 7-follow-up, Part B: every widget-facing path's own
  // OPTIONS preflight, answered centrally -- mirrors app.auth.cors.
  // WidgetCORSMiddleware's own "never reaches a real handler" shape.
  // Checked before any of the specific routes below, for every method.
  if (req.method === "OPTIONS") {
    const route = _matchCorsRoute(pathname);
    if (route) {
      preflightFor(route, req, res);
      return true;
    }
    return false;
  }

  // [SECURITY] Step 7-follow-up, Part C: GET /api/v1/widget-appearance
  // -- the real endpoint's own `teal, left` test color/position, so the
  // "a brand-new visitor sees the tenant color/position with no click"
  // e2e test actually proves something (a fixture returning the
  // loader's own default would never catch a regression here, the
  // identical reasoning server.mjs's own `/session` fixture color
  // already applies). Deliberately a plain, unconditional
  // Access-Control-Allow-Origin: "*", matching the real backend's own
  // one deliberate wildcard exception (app/auth/routes.py's own header
  // has the full reasoning) -- never gated through `corsHeadersFor()`.
  if (pathname === "/api/v1/widget-appearance" && req.method === "GET") {
    const { searchParams } = new URL(req.url, "http://localhost");
    const siteKey = searchParams.get("site_key");
    const body = JSON.stringify({ primary: "#14b8a6", primary_text: "#ffffff", position: "left" });
    const headers = {
      "Content-Type": "application/json",
      "Content-Security-Policy": CSP,
      "Cache-Control": "public, max-age=120",
      "Access-Control-Allow-Origin": "*",
    };

    // Out-of-band maintenance batch (2026-10-17), PART E: three
    // site-key-triggered response shapes, keyed off dedicated fixture
    // pages (test/e2e/public/launcher-reveal-*.html), so
    // launcher-reveal.spec.mjs can prove the reveal-once behavior
    // against each one without any OTHER test's own fixed-fast-response
    // expectations (chat.spec.mjs, widget.spec.mjs) ever changing.
    if (siteKey === "sk_test_e2e_delayed") {
      // Resolves WITHIN loader/src/widget.js's own 800ms reveal budget
      // (300ms) -- proves the reveal-once path for a real, non-trivial
      // network delay, not only the instant-response default.
      setTimeout(() => {
        res.writeHead(200, headers);
        res.end(body);
      }, 300);
      return true;
    }
    if (siteKey === "sk_test_e2e_hang") {
      // Never responds at all -- proves the 800ms timeout itself
      // reveals with defaults, not a response of any shape.
      return true;
    }
    if (siteKey === "sk_test_e2e_late") {
      // Resolves AFTER the 800ms reveal budget (1200ms) -- proves a
      // late response changes nothing once already revealed.
      setTimeout(() => {
        res.writeHead(200, headers);
        res.end(body);
      }, 1200);
      return true;
    }

    res.writeHead(200, headers);
    res.end(body);
    return true;
  }

  if (pathname === "/api/v1/session" && req.method === "POST") {
    const sessionToken = crypto.randomUUID();
    const conversationId = crypto.randomUUID();
    _sessionConversations.set(sessionToken, conversationId);

    const body = JSON.stringify({
      session_token: sessionToken,
      visitor_secret: "fake-visitor-secret",
      test_mode: true,
      widget_config: {
        title: "Test Widget",
        greeting: "Hi! How can we help?",
        // [ops bugfix]: deliberately NOT the loader CSS's own default
        // (#4f46e5) -- a fixture that happened to match the default
        // could never catch a regression where the launcher silently
        // stays on the hardcoded default instead of really applying the
        // tenant's own color (exactly BUG1, found live against the real
        // backend). This is Felix's own real test color (teal).
        colors: { primary: "#14b8a6" },
        logo_url: null,
        privacy_notice_url: null,
      },
    });
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Content-Security-Policy": CSP,
      ...corsHeadersFor(req),
    });
    res.end(body);
    return true;
  }

  if (pathname === "/api/v1/chat" && req.method === "POST") {
    const payload = await readJsonBody(req);
    const clientMessageId = payload.client_message_id;
    const message = payload.message || "";

    if (message === "fail once then succeed" && !_failedOnce.has(clientMessageId)) {
      _failedOnce.add(clientMessageId);
      res.writeHead(503, {
        "Content-Type": "application/json",
        "Content-Security-Policy": CSP,
        ...corsHeadersFor(req),
      });
      res.end(JSON.stringify({ detail: "service temporarily unavailable, please retry" }));
      return true;
    }

    // [SECURITY] Step 7-follow-up, Part B: real, named triggers for the
    // two real pre-stream HTTP error statuses the task names (409
    // busy, 429 rate-limited) -- deliberately NOT a page.route()
    // interception (confirmed live, by a direct adversarial probe, that
    // Playwright's route.fulfill() bypasses the browser's own CORS
    // enforcement entirely, so a route-mocked response proves nothing
    // about CORS either way). These go through this real server, on
    // the real API_PORT origin, so the browser's own preflight/ACAO
    // check genuinely applies.
    if (message === "trigger 409 busy") {
      res.writeHead(409, {
        "Content-Type": "application/json",
        "Content-Security-Policy": CSP,
        ...corsHeadersFor(req),
      });
      res.end(JSON.stringify({ detail: "this conversation is busy, please retry" }));
      return true;
    }
    if (message === "trigger 429 rate limited") {
      res.writeHead(429, {
        "Content-Type": "application/json",
        "Content-Security-Policy": CSP,
        "Retry-After": "7",
        ...corsHeadersFor(req),
      });
      res.end(JSON.stringify({ detail: "too many requests", retry_after: 7 }));
      return true;
    }

    // Part D (docs/SPEC.md §9's "Stop mid-stream"): a deliberately SLOW
    // stream (tokens spaced 150ms apart, up to 20 of them -- ~3s total)
    // so a real Playwright test has genuine time to click Stop while it
    // is still running, and checks `_stopRequests` (set by POST
    // /api/v1/chat/stop below) between every token write -- the live,
    // two-request proof that the widget's Stop button actually reaches
    // this fixture's own explicit stop endpoint, not merely that
    // AbortController stops the widget's local rendering.
    if (message === "trigger stoppable stream") {
      const token = _bearerToken(req);
      const stopCid =
        _sessionConversations.get(token) || "fake-conversation-id";
      _stopRequests.delete(token);
      res.writeHead(200, {
        "Content-Type": "text/event-stream",
        "Content-Security-Policy": CSP,
        ...corsHeadersFor(req),
      });
      let stopped = false;
      for (let i = 0; i < 20; i += 1) {
        res.write(sseFrame("token", { text: `slow token ${i} ` }));
        await sleep(150);
        if (_stopRequests.has(token)) {
          stopped = true;
          break;
        }
      }
      _stopRequests.delete(token);
      res.write(
        sseFrame("done", {
          message_id: "fake-message-id",
          usage: null,
          offer_escalation: false,
          conversation_id: stopCid,
          stopped,
        }),
      );
      res.end();
      return true;
    }

    // [ops bugfix]: the real backend scopes a conversation to the
    // visitor's own token (one per session, for this fixture's own
    // simplification -- see _sessionConversations's own header above);
    // reused here as the `done` event's own `conversation_id`, exactly
    // like the real backend returns it (every turn, not only the
    // first -- Step 6.2's own pre-authorised contract addition).
    const cid = _sessionConversations.get(_bearerToken(req)) || "fake-conversation-id";

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Content-Security-Policy": CSP,
      ...corsHeadersFor(req),
    });

    if (message === "what did I just tell you?") {
      // The deterministic "turn 2 depends on turn 1" proof: this only
      // ever answers correctly if THIS exact request carried the SAME
      // conversation_id turn 1 did -- a fresh/wrong id would read back
      // "(nothing yet)" instead, failing the e2e test loudly.
      const remembered = _lastMessageByConversation.get(cid) ?? "(nothing yet)";
      res.write(sseFrame("token", { text: `You told me: "${remembered}"` }));
    } else if (message === "trigger quota exhausted") {
      // docs/SPEC.md §9/§11: quota-exhausted is NEVER a distinct HTTP
      // error -- it is a normal 200 stream with a fixed fallback reply,
      // decided server-side before the model is called, through the
      // exact same `token`/`citations`/`done` sequence any other answer
      // uses. This trigger documents that explicitly, rather than
      // leaving it implied by the generic success-path test alone.
      res.write(sseFrame("token", { text: "Our team will follow up by email." }));
    } else {
      res.write(sseFrame("token", { text: "Here is a " }));
      res.write(sseFrame("token", { text: "Here is a safe answer." }));
    }
    _lastMessageByConversation.set(cid, message);

    res.write(
      // A real https URL, deliberately NOT this fake server's own
      // plain-http origin -- isHttpsUrl() (render.js) correctly rejects
      // a plain-http citation, matching production's real requirement,
      // so the fixture's citation must be a genuine https-shaped URL
      // to exercise the success path; the test never actually
      // navigates to it.
      sseFrame("citations", {
        items: [{ n: 1, title: "Example source", url: "https://example.com/source-page" }],
      }),
    );
    // Task 5.4: always offers escalation -- the fake server's own
    // simplification (which real backend condition would produce this
    // is out of scope here; only the widget's own reaction to the flag
    // is under test).
    res.write(
      sseFrame("done", {
        message_id: "fake-message-id",
        usage: null,
        offer_escalation: true,
        conversation_id: cid,
        stopped: false,
      }),
    );
    res.end();
    return true;
  }

  // Part D: mirrors the real backend's own POST /api/v1/chat/stop
  // (backend/app/chat/routes.py's `stop_chat_turn()`) -- marks this
  // bearer token's own in-flight "trigger stoppable stream" loop above
  // as stopped, then responds 204 regardless (idempotent, matching the
  // real endpoint's own no-op-if-nothing-running behaviour).
  if (pathname === "/api/v1/chat/stop" && req.method === "POST") {
    _stopRequests.add(_bearerToken(req));
    res.writeHead(204, {
      "Content-Security-Policy": CSP,
      ...corsHeadersFor(req),
    });
    res.end();
    return true;
  }

  if (pathname === "/api/v1/conversations" && req.method === "GET") {
    const conversationId = _sessionConversations.get(_bearerToken(req)) || "fake-conversation-id";
    const body = JSON.stringify({
      conversations: [
        {
          conversation_id: conversationId,
          title: null,
          created_at: new Date().toISOString(),
          last_message_at: new Date().toISOString(),
        },
      ],
    });
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Content-Security-Policy": CSP,
      ...corsHeadersFor(req),
    });
    res.end(body);
    return true;
  }

  // Task 5.3: GET /conversations/{cid}/messages -- the stored text
  // deliberately contains markdown link syntax, to prove history
  // renders it as plain text (no per-turn citation set is ever
  // reconstructable, per the register's own "Unscheduled" entry).
  const messagesMatch = /^\/api\/v1\/conversations\/([^/]+)\/messages$/.exec(pathname);
  if (messagesMatch && req.method === "GET") {
    const body = JSON.stringify({
      messages: [
        { role: "visitor", text: "What are your hours?" },
        { role: "assistant", text: "We're open 9-5. See [our hours page](https://example.com/hours) for details." },
      ],
    });
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Content-Security-Policy": CSP,
      ...corsHeadersFor(req),
    });
    res.end(body);
    return true;
  }

  // Task 5.4: POST /conversations/{cid}/escalate. Mirrors the real
  // backend's own "one open escalation per conversation" rule
  // (docs/SPEC.md §7) for real: the FIRST submission for a given cid
  // succeeds; a second submission for that SAME cid returns a real
  // 409, so the widget's own double-submit-prevention UI guard is
  // never the only thing standing between a visitor and that 409 --
  // exactly the layered posture this project's whole security culture
  // insists on (never trust one layer alone).
  const escalateMatch = /^\/api\/v1\/conversations\/([^/]+)\/escalate$/.exec(pathname);
  if (escalateMatch && req.method === "POST") {
    const cid = escalateMatch[1];
    const contactFields = await readJsonBody(req);
    if (_escalatedConversations.has(cid)) {
      res.writeHead(409, {
        "Content-Type": "application/json",
        "Content-Security-Policy": CSP,
        ...corsHeadersFor(req),
      });
      res.end(JSON.stringify({ detail: "an escalation is already open for this conversation" }));
      return true;
    }
    _escalatedConversations.add(cid);
    _lastEscalationContactFields = contactFields;
    res.writeHead(200, {
      "Content-Type": "application/json",
      "Content-Security-Policy": CSP,
      ...corsHeadersFor(req),
    });
    res.end(JSON.stringify({ escalation_id: "fake-escalation-id", status: "new" }));
    return true;
  }

  // Test-only introspection endpoint: lets a test assert on exactly
  // what contact-field payload the widget actually sent, without the
  // fixture needing its own separate persistence layer.
  if (pathname === "/__test__/last-escalation-contact-fields" && req.method === "GET") {
    res.writeHead(200, { "Content-Type": "application/json", "Content-Security-Policy": CSP });
    res.end(JSON.stringify(_lastEscalationContactFields));
    return true;
  }

  return false;
}

function resolveStaticTarget(urlPath) {
  if (DIST_FILES[urlPath]) {
    return DIST_FILES[urlPath];
  }
  const relative = urlPath === "/" ? "index.html" : urlPath.slice(1);
  return path.join(publicDir, relative);
}

// [SECURITY] Step 7-follow-up, Part B: TWO separate http.Server
// instances, TWO separate ports -- the widget page (static files only)
// and the fake API (every /api/v1/* route only) are no longer the same
// origin. A request that reaches the widget server for an /api/v1/*
// path (or vice versa) is a real routing bug in this fixture, not
// something either server falls back to serving -- confirmed structurally
// by giving each server its OWN handler, with no shared fallthrough path.
const widgetServer = http.createServer(async (req, res) => {
  const { pathname } = new URL(req.url, "http://localhost");
  const target = resolveStaticTarget(pathname);
  try {
    const body = await readFile(target);
    res.writeHead(200, {
      "Content-Type": MIME_TYPES[path.extname(target)] || "application/octet-stream",
      "Content-Security-Policy": CSP,
    });
    res.end(body);
  } catch {
    res.writeHead(404, { "Content-Security-Policy": CSP });
    res.end("not found");
  }
});

const apiServer = http.createServer(async (req, res) => {
  const { pathname } = new URL(req.url, "http://localhost");
  if (await handleFakeApi(req, res, pathname)) {
    return;
  }
  res.writeHead(404, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ detail: "not found" }));
});

widgetServer.listen(WIDGET_PORT, "127.0.0.1", () => {
  console.log(`widget e2e fixture: widget page listening on ${WIDGET_ORIGIN}`);
});
apiServer.listen(API_PORT, "127.0.0.1", () => {
  console.log(`widget e2e fixture: fake API listening on ${API_ORIGIN}`);
});
