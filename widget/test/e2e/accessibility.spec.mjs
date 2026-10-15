// Task 5.5: WCAG 2.2 AA -- an automated axe scan with no serious
// findings, keyboard operability, visible focus, a real focus trap on
// the mobile sheet (desktop stays non-modal, per docs/SPEC.md §11's own
// design direction), prefers-reduced-motion, and the strict-CSP e2e
// still showing zero violations with all of 5.3/5.4/5.5 now mounted.
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function openChat(page) {
  await page.goto("/chat-fixture.html");
  await page.evaluate(() => {
    document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button").click();
  });
  return page.locator("#samoflow-widget-host").locator("css=.sf-panel");
}

test("an automated accessibility scan of the open panel finds no serious or critical violations", async ({ page }) => {
  await openChat(page);
  const results = await new AxeBuilder({ page }).include("#samoflow-widget-host").analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
});

test("the launcher and the send button are both keyboard-reachable and keyboard-operable", async ({ page }) => {
  await page.goto("/chat-fixture.html");

  // Tab from a blank page must reach the launcher (it pierces the
  // shadow boundary the same way any other focusable element does).
  // Two presses: the fixture's own host-page marker (tabindex="0",
  // added for the desktop-non-modal test below) is first in real DOM
  // order -- `document.body.appendChild()` always appends the widget
  // host LAST regardless of where its own <script> tag sits in markup.
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  const launcherFocused = await page.evaluate(
    () => document.activeElement === document.getElementById("samoflow-widget-host") ||
      document.getElementById("samoflow-widget-host").shadowRoot.activeElement?.className === "samoflow-launcher",
  );
  expect(launcherFocused).toBe(true);

  await page.keyboard.press("Enter");
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();

  await panel.locator("css=.sf-input").fill("hi");
  await panel.locator("css=.sf-input").press("Enter");
  await expect(panel).toContainText("Here is a safe answer.");
});

test("Escape closes the panel and returns focus to the launcher", async ({ page }) => {
  const panel = await openChat(page);
  await expect(panel).toBeVisible();

  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();

  const launcherIsActive = await page.evaluate(
    () => document.getElementById("samoflow-widget-host").shadowRoot.activeElement?.className === "samoflow-launcher",
  );
  expect(launcherIsActive).toBe(true);
});

test("on the mobile sheet (below 480px), Tab is trapped inside the open panel", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 700 });
  const panel = await openChat(page);
  await expect(panel).toBeVisible();

  // Typed first so the Send button is enabled -- a disabled button
  // cannot receive real focus at all (`.focus()` on it is a silent
  // no-op), which would make `.last()` below resolve to a dead end
  // rather than the panel's own real last focusable control.
  await panel.locator("css=.sf-input").fill("hi");

  // Focus the LAST focusable element in the panel, then Tab once more
  // -- a trapped panel wraps back to the FIRST one, never escaping to
  // the host page.
  const focusableSelector = "css=button:not([disabled]), a[href], input, textarea";
  const focusableCount = await panel.locator(focusableSelector).count();
  expect(focusableCount).toBeGreaterThan(0);

  await panel.locator(focusableSelector).last().focus();
  await page.keyboard.press("Tab");

  const stillInsidePanel = await page.evaluate(() => {
    const host = document.getElementById("samoflow-widget-host");
    const active = host.shadowRoot.activeElement;
    return active !== null && host.shadowRoot.contains(active);
  });
  expect(stillInsidePanel).toBe(true);
});

test("on desktop (>=480px), the panel is non-modal: Tab is free to leave it onto the host page", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  const panel = await openChat(page);
  await expect(panel).toBeVisible();

  await page.locator("#host-page-marker").focus();
  const hostMarkerFocused = await page.evaluate(() => document.activeElement?.id === "host-page-marker");
  expect(hostMarkerFocused).toBe(true);
});

test("prefers-reduced-motion disables the panel's own open animation", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  const panel = await openChat(page);
  const animationName = await panel.evaluate((el) => getComputedStyle(el).animationName);
  expect(animationName).toBe("none");
});

test("with 5.3/5.4/5.5 all mounted, the strict-CSP e2e still shows zero violations end to end", async ({ page }) => {
  const cspViolations = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /content security policy/i.test(msg.text())) {
      cspViolations.push(msg.text());
    }
  });

  const panel = await openChat(page);
  await panel.locator("css=.sf-input").fill("hi");
  await panel.locator("css=.sf-send").click();
  await expect(panel).toContainText("Here is a safe answer.");

  // Escalation BEFORE the conversations-list excursion: opening a past
  // conversation resets `offerEscalation` to false by design (a loaded
  // history has no live turn of its own offering it), so this order is
  // load-bearing, not arbitrary.
  await panel.locator("css=.sf-talk-to-human").click();
  await panel.locator('css=input[type="checkbox"]').check();
  await panel.locator("css=.sf-escalation-submit").click();
  await expect(panel).toContainText("Your request has been sent");

  await panel.locator('css=[aria-label="Previous chats"]').click();
  await panel.locator("css=.sf-conversation-item").first().click();
  // Opening a conversation returns straight to the chat view by
  // design (ConversationList.js's own onOpenConversation) -- there is
  // no separate "back" step needed here.
  await expect(panel).toContainText("What are your hours?");

  expect(cspViolations, `unexpected CSP violations: ${cspViolations.join(" | ")}`).toEqual([]);
});
