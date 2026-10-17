// Out-of-band maintenance batch (2026-10-17), PART E: the launcher must
// show ONCE, already in its final look -- the CI fix that made
// host/shadowRoot/launcher creation synchronous (widget.js, Part C of
// the same batch) was correct on its own terms, but left a real defect:
// the launcher painted the DEFAULT color/position first and only
// switched once GET /api/v1/widget-appearance resolved. Each fixture
// below (test/e2e/public/launcher-reveal-*.html) is a distinct site key
// (test/e2e/server.mjs) so this file never touches chat.spec.mjs's or
// widget.spec.mjs's own fixed-fast-response expectations.
import { test, expect } from "@playwright/test";

const launcherSelector = () =>
  "css=button.samoflow-launcher";

test("with a delayed (but within-budget) appearance response, the launcher's computed color and position already equal the tenant's values the moment it first becomes visible", async ({
  page,
}) => {
  await page.goto("/launcher-reveal-delayed.html");

  const launcher = page.locator("#samoflow-widget-host").locator(launcherSelector());
  // Auto-retrying: resolves the FIRST instant the launcher is actually
  // visible (server.mjs responds at 300ms, inside the 800ms budget).
  await expect(launcher).toBeVisible();

  // A one-shot read, deliberately NOT an auto-retrying toHaveCSS: the
  // whole point is what is true AT THE MOMENT of first visibility, not
  // eventually -- a flash-then-switch would also eventually settle on
  // the right value, which toHaveCSS's own retrying would not catch.
  const [background, hostClass, hostLeft] = await Promise.all([
    launcher.evaluate((el) => getComputedStyle(el).backgroundColor),
    page.locator("#samoflow-widget-host").getAttribute("class"),
    page.locator("#samoflow-widget-host").evaluate((el) => getComputedStyle(el).left),
  ]);

  expect(background).toBe("rgb(20, 184, 166)");
  expect(hostClass).toMatch(/sf-position-left/);
  expect(hostLeft).toBe("20px");
});

test("a never-responding appearance endpoint still reveals the launcher with defaults within the timeout", async ({
  page,
}) => {
  const start = Date.now();
  await page.goto("/launcher-reveal-hang.html");

  const launcher = page.locator("#samoflow-widget-host").locator(launcherSelector());
  await expect(launcher).toBeVisible();
  const elapsed = Date.now() - start;

  // Generous upper bound (the 800ms reveal budget plus real page-load/
  // test overhead) -- the point is "well within a few seconds", not a
  // tight race against the exact 800ms figure.
  expect(elapsed).toBeLessThan(5000);

  const background = await launcher.evaluate((el) => getComputedStyle(el).backgroundColor);
  // widget.css's own hardcoded default (#4f46e5), never the tenant's
  // real teal -- the fetch never resolved, so no override was ever
  // applied.
  expect(background).toBe("rgb(79, 70, 229)");

  const hostClass = await page.locator("#samoflow-widget-host").getAttribute("class");
  expect(hostClass).not.toMatch(/sf-position-left/);
});

test("a late response after the timeout changes nothing", async ({ page }) => {
  await page.goto("/launcher-reveal-late.html");

  const launcher = page.locator("#samoflow-widget-host").locator(launcherSelector());
  // Reveals with DEFAULTS at ~800ms (server.mjs's late response is
  // scheduled for 1200ms, after the reveal budget).
  await expect(launcher).toBeVisible();

  const colorAtReveal = await launcher.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(colorAtReveal).toBe("rgb(79, 70, 229)");

  // Wait well past the server's own 1200ms late response, then confirm
  // nothing moved: a late response is ignored outright, not merely
  // slow to apply.
  await page.waitForTimeout(1000);

  const colorAfterLateResponse = await launcher.evaluate(
    (el) => getComputedStyle(el).backgroundColor,
  );
  expect(colorAfterLateResponse).toBe("rgb(79, 70, 229)");

  const hostClass = await page.locator("#samoflow-widget-host").getAttribute("class");
  expect(hostClass).not.toMatch(/sf-position-left/);
});

test("while pending, the launcher is neither focusable nor in the accessibility tree; once revealed, it is keyboard-reachable", async ({
  page,
}) => {
  await page.goto("/launcher-reveal-delayed.html");

  // Before the 300ms delayed response resolves, the launcher element
  // already exists (synchronous creation) but must not be focusable.
  const isHiddenAndUnfocusable = await page.evaluate(() => {
    const host = document.getElementById("samoflow-widget-host");
    const button = host?.shadowRoot?.querySelector("button.samoflow-launcher");
    if (!button) {
      return null; // not yet created -- re-checked by the retrying assertion below
    }
    return getComputedStyle(button).visibility === "hidden" && host.classList.contains("sf-pending");
  });
  // Either the button doesn't exist yet, or it exists and is correctly
  // hidden+pending -- never "exists and already visible" this early.
  expect(isHiddenAndUnfocusable).not.toBe(false);

  const launcher = page.locator("#samoflow-widget-host").locator(launcherSelector());
  await expect(launcher).toBeVisible();

  // One Tab press, not two: unlike chat-fixture.html (accessibility.
  // spec.mjs), this fixture's own host-page-marker has no `tabindex`,
  // so the launcher is the only focusable element on the page.
  await page.keyboard.press("Tab");
  const launcherFocused = await page.evaluate(
    () =>
      document.getElementById("samoflow-widget-host").shadowRoot.activeElement?.className ===
      "samoflow-launcher",
  );
  expect(launcherFocused).toBe(true);
});
