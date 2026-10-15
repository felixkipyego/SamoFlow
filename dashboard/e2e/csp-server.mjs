// Step 6.1: a tiny static file server for the LIVE strict-CSP Playwright
// test (e2e/csp.spec.ts) -- serves the real production build
// (dashboard/dist/, built via `npm run build`) with the exact strict
// Content-Security-Policy header this task requires, and proxies /api
// to the real backend (identical shape to vite.config.ts's own dev
// proxy -- see that file's header comment for why this keeps the
// browser's view of same-origin intact).
//
// No new dependency: Node's own built-in http/fs modules only.
import { createReadStream, existsSync } from "node:fs";
import http from "node:http";
import { extname, join } from "node:path";

const DIST_DIR = join(import.meta.dirname, "..", "dist");
const PORT = Number(process.env.CSP_SERVER_PORT ?? 4173);
const API_TARGET = process.env.CSP_SERVER_API_TARGET ?? "http://127.0.0.1:8000";

// Strict: no 'unsafe-inline', no 'unsafe-eval', no third-party host
// anywhere -- the real policy this dashboard must ship under (docs/
// SPEC.md §12's own "runs under a strict CSP"). frame-ancestors/base-uri/
// form-action close the three classic gaps a bare default-src misses.
const CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'none'",
  "form-action 'self'",
].join("; ");

const MIME_TYPES = {
  ".html": "text/html",
  ".js": "text/javascript",
  ".css": "text/css",
  ".woff2": "font/woff2",
  ".svg": "image/svg+xml",
  ".json": "application/json",
};

function serveStatic(req, res) {
  let filePath = join(DIST_DIR, req.url === "/" ? "index.html" : req.url.split("?")[0]);
  if (!existsSync(filePath) || req.url === "/") {
    filePath = join(DIST_DIR, "index.html");
  }
  res.setHeader("Content-Security-Policy", CSP);
  res.setHeader("Content-Type", MIME_TYPES[extname(filePath)] ?? "application/octet-stream");
  createReadStream(filePath).pipe(res);
}

async function proxyApi(req, res) {
  const target = new URL(req.url, API_TARGET);
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const body = Buffer.concat(chunks);

  const upstream = await fetch(target, {
    method: req.method,
    headers: { ...req.headers, host: undefined },
    body: body.length > 0 ? body : undefined,
  });
  res.statusCode = upstream.status;
  for (const [key, value] of upstream.headers.entries()) {
    if (key.toLowerCase() === "content-security-policy") continue;
    res.setHeader(key, value);
  }
  res.setHeader("Content-Security-Policy", CSP);
  res.end(Buffer.from(await upstream.arrayBuffer()));
}

const server = http.createServer((req, res) => {
  if (req.url.startsWith("/api/")) {
    proxyApi(req, res).catch((err) => {
      res.statusCode = 502;
      res.end(String(err));
    });
    return;
  }
  serveStatic(req, res);
});

server.listen(PORT, () => {
  console.log(`csp-server listening on http://127.0.0.1:${PORT}`);
});
