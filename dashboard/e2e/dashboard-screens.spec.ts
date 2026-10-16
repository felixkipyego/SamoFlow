// Step 6.6: docs/SPEC.md §12's own "Acceptance additions" -- an
// automated accessibility check (no serious findings) and a keyboard-
// only pass, for EVERY dashboard screen, not just login/logout (Step
// 6.1's own csp.spec.ts). Runs against the real production build under
// the real strict CSP (e2e/csp-server.mjs), same as csp.spec.ts.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const EMAIL = process.env.DASHBOARD_E2E_EMAIL;
const PASSWORD = process.env.DASHBOARD_E2E_PASSWORD;

// Mirrors Sidebar.tsx's own NAV_ITEMS exactly -- each screen's sidebar
// label and its own <h1> heading text are identical by construction
// (src/screens/*.tsx), confirmed by reading every screen file directly.
const SCREENS = ["Overview", "Widget", "Sources", "Inbox", "Usage", "Settings"] as const;

interface CspViolation {
  effectiveDirective: string;
  blockedURI: string;
  disposition: string;
  sourceFile: string;
}

// A second, deliberately small copy of csp.spec.ts's own exact-shape
// tolerance (that file's own header comment has the full investigation)
// -- duplicated once, not extracted, matching this project's own
// "extract at the third occurrence" convention (rule 9). This file's
// own job is per-screen a11y/keyboard coverage; CSP exact-tolerance
// itself is already proven in depth by csp.spec.ts.
function isKnownScrollLockViolation(v: CspViolation): boolean {
  let sourcePath: string;
  try {
    sourcePath = new URL(v.sourceFile).pathname;
  } catch {
    return false;
  }
  return (
    v.effectiveDirective === "style-src-elem" &&
    v.blockedURI === "inline" &&
    v.disposition === "enforce" &&
    sourcePath.startsWith("/assets/") &&
    sourcePath.endsWith(".js")
  );
}

async function installCspViolationCollector(page: Page): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __cspViolations: CspViolation[] }).__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      (window as unknown as { __cspViolations: CspViolation[] }).__cspViolations.push({
        effectiveDirective: e.effectiveDirective,
        blockedURI: e.blockedURI,
        disposition: e.disposition,
        sourceFile: e.sourceFile,
      });
    });
  });
}

async function unexpectedCspViolations(page: Page): Promise<CspViolation[]> {
  const violations = await page.evaluate(
    () => (window as unknown as { __cspViolations: CspViolation[] }).__cspViolations,
  );
  return violations.filter((v) => !isKnownScrollLockViolation(v));
}

test.describe("every dashboard screen", () => {
  test.skip(!EMAIL || !PASSWORD, "DASHBOARD_E2E_EMAIL/DASHBOARD_E2E_PASSWORD not configured");

  // ONE login for the whole suite, reused across every screen -- not
  // one login per screen. docs/SPEC.md §12's own "rate-limited login
  // with temporary lockouts" is real (app/dashboard/routes.py: 10
  // attempts per account per 15 minutes) and this is exactly the
  // account this task's own manual live-smoke-testing already logged
  // into repeatedly earlier in this session; six more rapid logins
  // tripped that real lockout, found live. A real keyboard-only user
  // navigating between screens does not re-authenticate each time
  // either, so this is also the more faithful shape, not just a
  // workaround.
  test("no serious a11y findings, keyboard-operable, CSP-clean, on every screen", async ({
    page,
  }) => {
    await installCspViolationCollector(page);

    await page.goto("/");
    await page.getByLabel("Email").fill(EMAIL!);
    await page.getByLabel("Password").fill(PASSWORD!);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();

    for (const screen of SCREENS) {
      // Not `exact: true` on the nav button: Sidebar's own "Inbox" item
      // grows an open-count badge (its own aria-label, e.g. "1 open
      // escalations") once any escalation exists, which becomes part
      // of the BUTTON's own composed accessible name too ("Inbox 1
      // open escalations") -- a real, live-found fragility, not a
      // style choice. The heading check right after still uses
      // `exact: true`: a bare <h1>{screen}</h1> has no such composed
      // name to worry about.
      await page.getByRole("button", { name: screen }).click();
      await expect(page.getByRole("heading", { name: screen, exact: true })).toBeVisible();
      // Lets each screen's own data fetch settle (loading skeleton ->
      // ready/empty) before scanning -- scanning mid-skeleton would
      // under-test the screen's own real content.
      await page.waitForTimeout(500);

      const results = await new AxeBuilder({ page }).analyze();
      const serious = results.violations.filter((v) =>
        ["serious", "critical"].includes(v.impact ?? ""),
      );
      expect(serious, `${screen}: ${JSON.stringify(serious, null, 2)}`).toEqual([]);

      // Keyboard-only pass: Tab repeatedly and confirm focus actually
      // MOVES each press -- real identity comparison of the DOM node
      // before/after (a JSHandle, not a derived text signature: two
      // distinct, unlabelled <input> fields can share the same empty
      // textContent/no aria-label and would falsely look like "the
      // same element" under a text-based signature, found live on
      // this exact screen). A short screen legitimately runs OUT of
      // tab stops before 8 presses (focus leaves the page for browser
      // chrome, reported here as activeElement === body) -- that is
      // normal end-of-page behaviour, not a trap, so the loop stops
      // there rather than failing.
      let stops = 0;
      for (let i = 0; i < 8; i++) {
        const before = await page.evaluateHandle(() => document.activeElement);
        await page.keyboard.press("Tab");
        const afterIsBody = await page.evaluate(
          () => document.activeElement === null || document.activeElement === document.body,
        );
        if (afterIsBody) {
          await before.dispose();
          break;
        }
        const focusMoved = await page.evaluate(
          (beforeEl) => document.activeElement !== beforeEl,
          before,
        );
        await before.dispose();
        expect(focusMoved, `${screen}: Tab press ${i + 1} did not move focus (a keyboard trap)`).toBe(
          true,
        );
        stops += 1;
      }
      expect(stops, `${screen} had no reachable tab stops at all`).toBeGreaterThan(0);
    }

    expect(await unexpectedCspViolations(page)).toEqual([]);
  });
});
