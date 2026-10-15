// Task 5.1: headless-browser test config. Pre-approved as a dev
// dependency (PROJECT_SPEC.md/task instructions) for exactly the thing a
// Node unit test cannot prove: that the loader behaves correctly in a
// real browser DOM, under a real strict CSP response header.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./test/e2e",
  timeout: 30_000,
  fullyParallel: true,
  forbidOnly: true,
  webServer: {
    command: "node test/e2e/server.mjs",
    url: "http://127.0.0.1:4173/",
    reuseExistingServer: false,
    timeout: 10_000,
  },
  use: {
    baseURL: "http://127.0.0.1:4173",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
