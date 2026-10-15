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

async function handleFakeApi(req, res, pathname) {
  if (pathname === "/api/v1/session" && req.method === "POST") {
    const body = JSON.stringify({
      session_token: "fake-session-token",
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
    res.write(sseFrame("done", { message_id: "fake-message-id", usage: null, offer_escalation: false }));
    res.end();
    return true;
  }

  if (pathname === "/api/v1/conversations" && req.method === "GET") {
    const body = JSON.stringify({
      conversations: [
        {
          conversation_id: "fake-conversation-id",
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
