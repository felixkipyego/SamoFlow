# dashboard

The React dashboard SPA: tenant settings, sources and ingestion status,
escalation inbox, usage against plan limits, and platform admin
(docs/SPEC.md §12). Owned by steps 6.1 to 6.5 — see PROJECT_SPEC.md.

**Tooling** (decided at the Step 6.1 breakdown, per the 2026-09-28
decision log entry): Vite + React + TypeScript, pinned Node
(`.nvmrc`/`package.json#engines`), [shadcn/ui](https://ui.shadcn.com/)
on Radix primitives (`components.json`'s own `base: "radix"`), Tailwind
CSS v4 for the CSS-custom-property theme (light/dark from `src/index.css`'s
own `:root`/`.dark` blocks), self-hosted fonts (`@fontsource-variable/geist`
— real `.woff2` files bundled into the build, never fetched from a
third-party host at runtime).

## Commands

- `npm run dev` — Vite dev server (proxies `/api` to `http://127.0.0.1:8000`,
  matching the same-origin design Caddy provides in production).
- `npm run build` — type-check (`tsc -b`) then production build.
- `npm run lint` — ESLint.
- `npm run test` — Vitest (component tests, jsdom).
- `npm run test:e2e` — Playwright, against the real production build served
  with a real strict Content-Security-Policy (`e2e/csp-server.mjs`). The
  authenticated-shell/Dialog test needs a real backend and a seeded owner:

  ```sh
  # from the repo root
  make up && make migrate
  docker compose --env-file .env -f deploy/docker-compose.yml run --rm \
    migrate seed-owner --email owner@example.com --tenant-name "Acme Inc"
  # from dashboard/
  DASHBOARD_E2E_EMAIL=owner@example.com DASHBOARD_E2E_PASSWORD=<the password you entered> \
    npm run test:e2e
  ```

  Without those two env vars, the authenticated-shell test is skipped
  (not silently passed) — the login-screen CSP/keyboard/accessibility
  checks still run.
- `npm run audit` — `npm audit --omit=dev`: scoped to production
  dependencies only. One known high-severity finding (`braces`, a
  transitive dependency of the `shadcn` CLI's own `fast-glob`, with no
  patched release published upstream as of this task) lives entirely in
  `devDependencies` — it is build/CLI tooling, never shipped in the
  browser bundle, so this is the correct scope for what the audit
  requirement actually protects.

## Adding a component

`npx shadcn@4.21.5 add <component>` (official registry only, one
component at a time, generated diffs reviewed before commit — pin the
CLI version explicitly on every future call, matching this one).
