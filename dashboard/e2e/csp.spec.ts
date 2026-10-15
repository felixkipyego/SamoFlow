// Step 6.1: the LIVE strict-CSP Playwright test this task's own direct
// instruction requires -- "covering the login screen plus at least one
// Dialog (scroll-lock libraries can inject <style> tags)". Runs against
// the real production build under a real strict CSP (no 'unsafe-inline',
// no 'unsafe-eval', no third-party host -- e2e/csp-server.mjs), with no
// CSP report-only fallback.
//
// A REAL finding, investigated live, not assumed (see docs/progress/
// dashboard.md's own "CSP and the Dialog scroll-lock" entry for the
// full investigation): opening the Dialog makes react-remove-scroll-bar
// (a dependency of Radix's own Dialog, via react-style-singleton)
// attempt to insert a real <style> element for its own scrollbar-gap-
// compensation CSS -- the one DOM operation style-src actually governs
// (confirmed live with a throwaway strict-CSP page: direct
// element.style.setProperty()/style.cssText mutations are NOT affected
// by style-src at all; only a real inserted <style> element is). That
// one specific, known violation is tolerated BY EXACT STRUCTURAL SHAPE
// below, not by a free-text phrase -- see isKnownScrollLockViolation's
// own header comment. ANY OTHER violation, of any kind, still fails
// every test in this file.
import AxeBuilder from "@axe-core/playwright";
import { expect, type Page, test } from "@playwright/test";

const EMAIL = process.env.DASHBOARD_E2E_EMAIL;
const PASSWORD = process.env.DASHBOARD_E2E_PASSWORD;

interface CspViolation {
  violatedDirective: string;
  effectiveDirective: string;
  blockedURI: string;
  sourceFile: string;
  sample: string;
  disposition: string;
}

// Alignment task (2026-10-15): the prior tolerance was a free-text
// console regex (`/Applying inline style violates/i`) -- too loose,
// since it would have silently swallowed ANY future inline-style
// violation anywhere in the app, not just this one known, investigated
// case. Replaced with the browser's own structured
// `securitypolicyviolation` DOM event (captured via `addInitScript`,
// below), which gives exact machine-readable fields instead of a
// string to parse.
//
// Deliberately NOT matched on the CSP report's own hash
// (`sha256-...`): confirmed live (`debug-csp.mjs`, throwaway, not
// committed) that the real "Applying inline style violates..." message
// does carry a hash, but that hash is computed over
// react-style-singleton's own injected CSS text, which embeds the REAL
// MEASURED scrollbar-gap width -- platform/browser dependent (this
// file's own header comment already names macOS-overlay-scrollbars-
// measure-0 vs Windows as a concrete case). A hash-based match would
// pass locally and silently fail to match in CI's own different
// Chromium/OS combination, for exactly the reason this project's own
// CSP already rejects hash-pinning as non-portable. The fields below
// are structural instead, stable across that same variation:
// `effectiveDirective`/`blockedURI`/`disposition` never depend on the
// gap width at all, and `sourceFile` is "our own bundled JS", not a
// specific byte range within it.
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
        violatedDirective: e.violatedDirective,
        effectiveDirective: e.effectiveDirective,
        blockedURI: e.blockedURI,
        sourceFile: e.sourceFile,
        sample: e.sample,
        disposition: e.disposition,
      });
    });
  });
}

async function readCspViolations(page: Page): Promise<CspViolation[]> {
  return page.evaluate(
    () => (window as unknown as { __cspViolations: CspViolation[] }).__cspViolations,
  );
}

async function unexpectedCspViolations(page: Page): Promise<CspViolation[]> {
  const violations = await readCspViolations(page);
  return violations.filter((v) => !isKnownScrollLockViolation(v));
}

test("login screen renders under a strict CSP with no violations", async ({ page }) => {
  await installCspViolationCollector(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  expect(await unexpectedCspViolations(page)).toEqual([]);
});

test("login screen is keyboard-operable (tab order, visible focus)", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").focus();
  await expect(page.getByLabel("Email")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel("Password")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeFocused();
});

test("login screen has no serious automated accessibility violations", async ({ page }) => {
  await page.goto("/");
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => ["serious", "critical"].includes(v.impact ?? ""));
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
});

// Proof, not assertion-by-assumption, that the exact-shape tolerance
// above does not silently widen into "ignore inline-style violations
// in general": a SECOND, deliberately different violation (an
// externally-sourced <script>, never same-origin, never style-src at
// all) is injected on the plain login screen -- nowhere near the real
// Dialog/scroll-lock code path -- and must still be reported as
// unexpected. If this test ever starts passing with an empty
// `unexpectedCspViolations` result, the tolerance above has regressed
// into something broader than intended.
test("the exact tolerance does not swallow a different, deliberately injected violation", async ({
  page,
}) => {
  await installCspViolationCollector(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

  await page.evaluate(() => {
    const script = document.createElement("script");
    script.src = "https://example.invalid/not-same-origin.js";
    document.head.appendChild(script);
  });
  // The injected <script> never loads (CSP blocks the request before any
  // network attempt) -- give the browser one tick to deliver the
  // resulting securitypolicyviolation event before reading it back.
  await page.waitForTimeout(100);

  const unexpected = await unexpectedCspViolations(page);
  expect(unexpected).toHaveLength(1);
  expect(unexpected[0].effectiveDirective).toBe("script-src-elem");
  expect(unexpected[0].blockedURI).toBe("https://example.invalid/not-same-origin.js");
});

test.describe("authenticated shell and its logout Dialog", () => {
  test.skip(!EMAIL || !PASSWORD, "DASHBOARD_E2E_EMAIL/DASHBOARD_E2E_PASSWORD not configured");

  test("logging in, opening the logout Dialog, and logging out all stay CSP-clean", async ({
    page,
  }) => {
    await installCspViolationCollector(page);

    await page.goto("/");
    await page.getByLabel("Email").fill(EMAIL!);
    await page.getByLabel("Password").fill(PASSWORD!);
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByRole("button", { name: "Log out" })).toBeVisible();

    await page.getByRole("button", { name: "Log out" }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    // The real functional property the known, tolerated violation above
    // must NOT have broken: the background is genuinely not scrollable
    // while the dialog is open (src/index.css's own static fallback
    // rule, independent of the blocked <style> element).
    const bodyOverflow = await page.evaluate(
      () => getComputedStyle(document.body).overflow,
    );
    expect(bodyOverflow).toBe("hidden");

    expect(await unexpectedCspViolations(page)).toEqual([]);
    // The real, known violation is still happening exactly once per
    // Dialog open -- not zero (which would mean the library stopped
    // doing this and the tolerance should be deleted, not kept around
    // unused) and not more than one (which would mean something new
    // regressed alongside it).
    const allViolations = await readCspViolations(page);
    expect(allViolations.filter(isKnownScrollLockViolation)).toHaveLength(1);

    const axeResults = await new AxeBuilder({ page }).include('[role="dialog"]').analyze();
    const serious = axeResults.violations.filter((v) =>
      ["serious", "critical"].includes(v.impact ?? ""),
    );
    expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);

    await dialog.getByRole("button", { name: "Log out" }).click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    expect(await unexpectedCspViolations(page)).toEqual([]);
  });
});
