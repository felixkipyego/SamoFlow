// Task 5.2: the headless-browser proof that the real chat UI -- not
// just the loader -- works end to end under a real strict CSP (no
// 'unsafe-inline', no 'unsafe-eval'): session creation, a streamed
// answer, a real citation link, and zero CSP violations throughout.
import { test, expect } from "@playwright/test";

test("sends a message, streams a real answer with a safe citation link, under a strict CSP with zero violations", async ({
  page,
}) => {
  const cspViolations = [];
  page.on("console", (msg) => {
    if (msg.type() === "error" && /content security policy/i.test(msg.text())) {
      cspViolations.push(msg.text());
    }
  });

  await page.goto("/chat-fixture.html");

  await page.evaluate(() => {
    document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button").click();
  });

  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("Hi! How can we help?");

  const input = panel.locator("css=.sf-input");
  await input.fill("What are your hours?");
  await panel.locator("css=.sf-send").click();

  await expect(panel).toContainText("Here is a safe answer.");

  const citationLink = panel.locator("css=.sf-citations a");
  await expect(citationLink).toHaveText("Example source");
  await expect(citationLink).toHaveAttribute("href", "https://example.com/source-page");
  await expect(citationLink).toHaveAttribute("target", "_blank");
  await expect(citationLink).toHaveAttribute("rel", "noopener noreferrer nofollow");

  expect(cspViolations, `unexpected CSP violations: ${cspViolations.join(" | ")}`).toEqual([]);
});

test("the launcher toggles the panel open and closed after the chat bundle has loaded", async ({ page }) => {
  await page.goto("/chat-fixture.html");
  const launcherButton = () => page.evaluateHandle(() => document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button"));

  await page.evaluate(() => {
    document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button").click();
  });
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();

  await (await launcherButton()).asElement().click();
  await expect(panel).toBeHidden();

  await (await launcherButton()).asElement().click();
  await expect(panel).toBeVisible();
});
