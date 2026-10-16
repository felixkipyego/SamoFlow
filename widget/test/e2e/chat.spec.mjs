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

// [ops bugfix] regression test: the launcher used to stay the loader
// CSS's hardcoded default (#4f46e5) forever, even once the real tenant
// color (the fixture's own #14b8a6, deliberately NOT the default -- see
// server.mjs) had been fetched and was already correctly applied to the
// header/bubbles/Send. This asserts BOTH the header and the launcher
// -- a sibling of the panel under the shared shadow root, not a
// descendant of it -- end up on the exact same real, non-default color.
test("every brand-colored element, including the launcher, picks up the real tenant color", async ({ page }) => {
  await page.goto("/chat-fixture.html");

  const launcherBackground = async () =>
    page.evaluate(() => {
      const button = document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button");
      return getComputedStyle(button).backgroundColor;
    });

  // Before the chat bundle has loaded at all, the launcher has no
  // tenant-color information yet (no cached value on a first-ever
  // visit to this fixture) -- still the loader CSS's own default.
  expect(await launcherBackground()).toBe("rgb(79, 70, 229)");

  await page.evaluate(() => {
    document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button").click();
  });

  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();

  const header = panel.locator("css=.sf-header");
  await expect(header).toHaveCSS("background-color", "rgb(20, 184, 166)");

  // The launcher -- a SIBLING of the panel, not a descendant -- must
  // end up on the exact same real tenant color, through the shared
  // shadow-host custom property, not a separate/independent mechanism.
  expect(await launcherBackground()).toBe("rgb(20, 184, 166)");

  const sendButton = panel.locator("css=.sf-send");
  await expect(sendButton).toHaveCSS("background-color", "rgb(20, 184, 166)");
});

// [ops bugfix] the real reported bug, reproduced and fixed: after the
// first answer finished, the input/Send stayed disabled forever --
// `controller.isStreaming` (a plain mutable property, never Preact
// state) was read directly for the `disabled` prop, so Preact had no
// way to know when it flipped back to false (see App.js's own header
// comment for the exact mechanism). This sends a SECOND real message
// after the first completes -- the literal reproduction.
test("the input re-enables after a turn completes, and a second message can genuinely be sent", async ({ page }) => {
  await page.goto("/chat-fixture.html");
  await page.evaluate(() => {
    document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button").click();
  });
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  await expect(panel).toBeVisible();

  const input = panel.locator("css=.sf-input");
  const send = panel.locator("css=.sf-send");

  await input.fill("first question");
  await send.click();
  await expect(panel).toContainText("Here is a safe answer.");

  // The textarea itself is the real proof (Send is ALSO correctly
  // disabled right now for an unrelated, intentional reason: the input
  // is empty after the first send cleared it -- not the bug this test
  // guards against).
  await expect(input).toBeEnabled();
  await expect(input).toBeFocused();

  await input.fill("second question");
  await expect(send).toBeEnabled();
  await send.click();
  await expect(panel.locator("css=.sf-message")).toHaveCount(4); // visitor+assistant x2
  await expect(input).toBeEnabled();
});

// [ops bugfix] docs/SPEC.md §10's own real contract addition (Step
// 6.2): `done` now carries `conversation_id` on every turn -- this is
// what let chat-controller.js remove the old GET /conversations
// workaround entirely (that workaround had ALSO silently stopped
// working in any real cross-origin deployment -- see the register). A
// genuine 3-turn conversation where turn 2 depends on turn 1's own
// content is the one proof that the SAME conversation_id really is
// threaded through every turn, not just that no error occurred.
test("a 3-turn conversation: turn 2 answers from what turn 1 actually said, proving the same conversation_id carries forward", async ({
  page,
}) => {
  await page.goto("/chat-fixture.html");
  await page.evaluate(() => {
    document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button").click();
  });
  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  const input = panel.locator("css=.sf-input");
  const send = panel.locator("css=.sf-send");

  await input.fill("my favorite color is teal");
  await send.click();
  await expect(panel).toContainText("Here is a safe answer.");
  await expect(input).toBeEnabled();

  await input.fill("what did I just tell you?");
  await send.click();
  await expect(panel).toContainText('You told me: "my favorite color is teal"');
  await expect(input).toBeEnabled();

  await input.fill("what did I just tell you?");
  await send.click();
  await expect(panel).toContainText('You told me: "what did I just tell you?"');
  await expect(input).toBeEnabled();
});

// [ops bugfix] every named way a turn can end must re-enable AND
// refocus the input -- no terminal state exists in this widget's own
// design that should leave it dead (docs/SPEC.md §9/§11: quota-
// exhausted still routes through the SAME `done` event with a fixed
// fallback reply; no other state is named as permanently disabling
// chat). Covers 409, 503, a real SSE `error` event (model/timeout), and
// a dropped connection (stream ends with no `done` at all).
for (const [name, mockRoute] of [
  [
    "a 409 (conversation busy)",
    (route) => route.fulfill({ status: 409, contentType: "application/json", body: '{"detail":"this conversation is busy, please retry"}' }),
  ],
  [
    "a 503 (service unavailable)",
    (route) => route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"service temporarily unavailable, please retry"}' }),
  ],
  [
    "a real SSE timeout error event",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: error\ndata: {"code":"timeout","message":"ignored","v":1}\n\n',
      }),
  ],
  [
    "a dropped connection (stream ends with no done event)",
    (route) =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: token\ndata: {"text":"partial","v":1}\n\n',
      }),
  ],
]) {
  test(`the input re-enables and refocuses after ${name}`, async ({ page }) => {
    await page.route("**/api/v1/chat", mockRoute);
    await page.goto("/chat-fixture.html");
    await page.evaluate(() => {
      document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button").click();
    });
    const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
    const input = panel.locator("css=.sf-input");
    const send = panel.locator("css=.sf-send");

    await input.fill("hello");
    await send.click();

    await expect(input).toBeEnabled();
    await expect(input).toBeFocused();
    await expect(send).toBeDisabled(); // empty text again -- the OTHER disabling reason, not isStreaming
  });
}

// [ops bugfix]: closing and reopening the panel WHILE a turn is still
// in flight must not leave the input stuck once it reopens -- the turn
// keeps running in the background (the panel only toggles `hidden`; the
// Preact tree, and the controller driving it, are never unmounted), so
// a hidden-at-the-time .focus() call is a no-op by spec; this proves
// the SECOND chance (the same effect re-running on `open` too) picks it
// up correctly once the panel is visible again.
test("closing and reopening the panel mid-stream still re-enables and refocuses the input once it finishes", async ({
  page,
}) => {
  let resolveRoute;
  await page.route("**/api/v1/chat", (route) => {
    resolveRoute = () =>
      route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: 'event: done\ndata: {"message_id":"m1","usage":null,"offer_escalation":false,"conversation_id":"c1","v":1}\n\n',
      });
  });

  await page.goto("/chat-fixture.html");
  const launcherButton = () =>
    page.evaluateHandle(() => document.getElementById("samoflow-widget-host").shadowRoot.querySelector("button"));
  await (await launcherButton()).asElement().click();

  const panel = page.locator("#samoflow-widget-host").locator("css=.sf-panel");
  const input = panel.locator("css=.sf-input");
  const send = panel.locator("css=.sf-send");
  await input.fill("hello");
  await send.click();
  await expect(input).toBeDisabled();

  // Close the panel while the turn is still in flight (the route above
  // has not been asked to resolve yet).
  await (await launcherButton()).asElement().click();
  await expect(panel).toBeHidden();

  // Now let the turn actually finish while the panel is closed.
  resolveRoute();
  await page.waitForTimeout(200);

  // Reopen -- the input must be enabled and focused immediately, not
  // require a further click or reload.
  await (await launcherButton()).asElement().click();
  await expect(panel).toBeVisible();
  await expect(input).toBeEnabled();
  await expect(input).toBeFocused();
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
