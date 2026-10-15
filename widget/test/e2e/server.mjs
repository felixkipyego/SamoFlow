// Task 5.1: a tiny static file server for the Playwright test, standing
// in for the tenant's own site. Sets a real strict Content-Security-Policy
// RESPONSE HEADER (not just a <meta> tag, for full fidelity to a real
// tenant deployment) with no 'unsafe-inline' and no 'unsafe-eval' anywhere
// -- the exact bar docs/SPEC.md §11 requires the widget to work under.
// Stdlib only (node:http) -- no new dependency for a 20-line test helper.
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
const distDir = path.join(widgetRoot, "dist");

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

const DIST_FILES = new Set(["/widget.js", "/widget.css"]);

function resolveTarget(urlPath) {
  if (DIST_FILES.has(urlPath)) {
    return path.join(distDir, urlPath.slice(1));
  }
  const relative = urlPath === "/" ? "index.html" : urlPath.slice(1);
  return path.join(publicDir, relative);
}

const server = http.createServer(async (req, res) => {
  const { pathname } = new URL(req.url, "http://localhost");
  const target = resolveTarget(pathname);

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

const port = Number(process.env.PORT || 4173);
server.listen(port, "127.0.0.1", () => {
  console.log(`widget e2e fixture server listening on http://127.0.0.1:${port}`);
});
