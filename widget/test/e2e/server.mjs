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
// Sets a real strict Content-Security-Policy RESPONSE HEADER (not just
// a <meta> tag, for full fidelity to a real tenant deployment) with no
// 'unsafe-inline' and no 'unsafe-eval' anywhere -- the exact bar
// docs/SPEC.md §11 requires the widget to work under. Stdlib only
// (node:http) -- no new dependency for this test helper.
import http from "node:http";
import crypto from "node:crypto";
import { readFile } from "node:fs/promises";
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

const CSP = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "connect-src 'self'",
  "img-src 'self'",
  "base-uri 'none'",
].join("; ");

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

function sseFrame(event, data) {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
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

async function handleFakeApi(req, res, pathname) {
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
        colors: { primary: "#4f46e5" },
        logo_url: null,
        privacy_notice_url: null,
      },
    });
    res.writeHead(200, { "Content-Type": "application/json", "Content-Security-Policy": CSP });
    res.end(body);
    return true;
  }

  if (pathname === "/api/v1/chat" && req.method === "POST") {
    const payload = await readJsonBody(req);
    const clientMessageId = payload.client_message_id;
    const message = payload.message || "";

    if (message === "fail once then succeed" && !_failedOnce.has(clientMessageId)) {
      _failedOnce.add(clientMessageId);
      res.writeHead(503, { "Content-Type": "application/json", "Content-Security-Policy": CSP });
      res.end(JSON.stringify({ detail: "service temporarily unavailable, please retry" }));
      return true;
    }

    res.writeHead(200, { "Content-Type": "text/event-stream", "Content-Security-Policy": CSP });
    res.write(sseFrame("token", { text: "Here is a " }));
    res.write(sseFrame("token", { text: "Here is a safe answer." }));
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
    res.write(sseFrame("done", { message_id: "fake-message-id", usage: null, offer_escalation: true }));
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
    res.writeHead(200, { "Content-Type": "application/json", "Content-Security-Policy": CSP });
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
    res.writeHead(200, { "Content-Type": "application/json", "Content-Security-Policy": CSP });
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
      res.writeHead(409, { "Content-Type": "application/json", "Content-Security-Policy": CSP });
      res.end(JSON.stringify({ detail: "an escalation is already open for this conversation" }));
      return true;
    }
    _escalatedConversations.add(cid);
    _lastEscalationContactFields = contactFields;
    res.writeHead(200, { "Content-Type": "application/json", "Content-Security-Policy": CSP });
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

const PORT = Number(process.env.PORT || 4173);

const server = http.createServer(async (req, res) => {
  const { pathname } = new URL(req.url, "http://localhost");

  if (await handleFakeApi(req, res, pathname)) {
    return;
  }

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

server.listen(PORT, "127.0.0.1", () => {
  console.log(`widget e2e fixture server listening on http://127.0.0.1:${PORT}`);
});
