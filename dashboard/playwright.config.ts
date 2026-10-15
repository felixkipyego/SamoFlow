import { defineConfig } from "@playwright/test";

// Step 6.1: the LIVE strict-CSP Playwright test (this task's own direct
// instruction) runs against the real PRODUCTION build (dist/), served
// with a real strict Content-Security-Policy header (e2e/csp-server.mjs)
// -- not the Vite dev server, which sets no CSP header at all and would
// prove nothing about the real deployed page.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${process.env.CSP_SERVER_PORT ?? 4173}`,
  },
  webServer: {
    command: "npm run build && node e2e/csp-server.mjs",
    url: `http://127.0.0.1:${process.env.CSP_SERVER_PORT ?? 4173}`,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
